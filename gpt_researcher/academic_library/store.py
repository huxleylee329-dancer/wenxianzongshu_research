"""Private SQLite/FTS library; optional on-disk Chroma with explicit embeddings.

SQLite is authoritative. Vector results are always resolved back through the
active SQLite chunk table, so stale/deleted vector entries cannot leak content.
No cloud embedding or default Chroma embedding function is ever selected.
"""

from __future__ import annotations

import os
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path

from .models import LibraryError, Paper, Passage, Reading, content_id
from .reading import MAX_FILE_BYTES, PARSER_VERSION, parse_document


_ID = re.compile(r"[0-9a-f]{32}")


class LocalVectors:
    def __init__(self, path: Path, provider: str, model: str):
        if provider != "ollama" or not model:
            raise LibraryError("local_embedding_configuration_required")
        # User installs/downloads the local model explicitly; no auto-download.
        try:
            import chromadb
            from gpt_researcher.memory.embeddings import Memory
            self.embedder = Memory(provider, model).get_embeddings()
            self.client = chromadb.PersistentClient(path=str(path))
            name = "papers-" + content_id((provider + ":" + model + ":" + PARSER_VERSION).encode())[:24]
            self.collection = self.client.get_or_create_collection(name, embedding_function=None)
        except Exception:
            raise LibraryError("local_vector_backend_unavailable") from None

    def put(self, ids, texts):
        if ids:
            self.collection.upsert(ids=ids, embeddings=self.embedder.embed_documents(texts))

    def delete(self, ids):
        if ids:
            self.collection.delete(ids=ids)

    def search(self, query, limit):
        count = self.collection.count()
        if not count:
            return []
        result = self.collection.query(query_embeddings=[self.embedder.embed_query(query)], n_results=min(count, limit))
        return result["ids"][0]


class KnowledgeLibrary:
    def __init__(self, root: Path, *, vectors=None):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.originals = self.root / "originals"
        self.originals.mkdir(exist_ok=True)
        self._lock = threading.RLock()
        self.vectors = vectors
        self.vector_warning = None
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY, hash TEXT NOT NULL, filename TEXT NOT NULL,
                    title TEXT NOT NULL, kind TEXT NOT NULL, parser TEXT NOT NULL,
                    generation TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS document_hash ON documents(hash);
                CREATE TABLE IF NOT EXISTS chunks (
                    id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    ordinal INTEGER NOT NULL, locator TEXT NOT NULL, text TEXT NOT NULL
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(
                    chunk_id UNINDEXED, terms, tokenize='unicode61'
                );
            """)

    @contextmanager
    def _db(self):
        with self._lock:
            db = sqlite3.connect(self.root / "library.sqlite3", timeout=10)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            try:
                with db:
                    yield db
            finally:
                db.close()

    @staticmethod
    def _terms(value):
        # Default unicode61 does not segment Chinese; explicitly index CJK
        # characters and adjacent bigrams, plus normal Latin/numeric tokens.
        tokens = re.findall(r"[a-zA-Z0-9_]+|[\u4e00-\u9fff]", value.lower())
        chinese = re.findall(r"[\u4e00-\u9fff]+", value)
        return tokens + [word[i:i + 2] for word in chinese for i in range(len(word) - 1)]

    def _doc(self, db, document_id):
        if type(document_id) is not str or not _ID.fullmatch(document_id):
            raise LibraryError("invalid_document_id")
        row = db.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
        if row is None:
            raise LibraryError("document_not_found")
        return row

    def list_documents(self):
        with self._db() as db:
            return [dict(row) for row in db.execute("SELECT d.*, count(c.id) AS chunk_count FROM documents d LEFT JOIN chunks c ON c.document_id=d.id GROUP BY d.id ORDER BY d.created_at,d.id")]

    def import_document(self, filename: str, data: bytes, *, replace_id: str | None = None):
        if type(filename) is not str or not filename or len(filename) > 240 or Path(filename).name != filename or "/" in filename or "\\" in filename:
            raise LibraryError("invalid_filename")
        kind = Path(filename).suffix.lower().lstrip(".")
        if kind not in ("pdf", "xml", "html", "htm", "txt", "md", "csv", "docx", "pptx", "xlsx"):
            raise LibraryError("unsupported_document_type")
        if type(data) is not bytes or not data or len(data) > MAX_FILE_BYTES:
            raise LibraryError("invalid_document_size")
        digest = content_id(data)
        with self._db() as db:
            if replace_id:
                self._doc(db, replace_id)
            existing = db.execute("SELECT id FROM documents WHERE hash=?", (digest,)).fetchone()
            if existing and (not replace_id or existing["id"] == replace_id):
                return {"document_id": existing["id"], "duplicate": True}
        passages = parse_document(data, kind)
        document_id = replace_id or uuid.uuid4().hex
        generation = uuid.uuid4().hex
        chunk_ids = [generation + ":" + str(i) for i in range(len(passages))]
        target = self.originals / (digest + ".bin")
        temp = self.originals / (uuid.uuid4().hex + ".tmp")
        old_chunk_ids = []
        with self._lock:
            # Parsing may run concurrently, but duplicate detection and commit
            # share the lock so two identical uploads cannot create two records.
            with self._db() as db:
                existing = db.execute("SELECT id FROM documents WHERE hash=?", (digest,)).fetchone()
                if existing and (not replace_id or existing["id"] == replace_id):
                    return {"document_id": existing["id"], "duplicate": True}
            try:
                temp.write_bytes(data)
                os.replace(temp, target)
                with self._db() as db:
                    if replace_id:
                        self._doc(db, replace_id)
                        old_chunk_ids = [row[0] for row in db.execute("SELECT id FROM chunks WHERE document_id=?", (document_id,))]
                        for chunk_id in old_chunk_ids:
                            db.execute("DELETE FROM chunk_fts WHERE chunk_id=?", (chunk_id,))
                        db.execute("DELETE FROM chunks WHERE document_id=?", (document_id,))
                        db.execute("UPDATE documents SET hash=?,filename=?,title=?,kind=?,parser=?,generation=? WHERE id=?", (digest, filename, Path(filename).stem, kind, PARSER_VERSION, generation, document_id))
                    else:
                        db.execute("INSERT INTO documents(id,hash,filename,title,kind,parser,generation) VALUES(?,?,?,?,?,?,?)", (document_id, digest, filename, Path(filename).stem, kind, PARSER_VERSION, generation))
                    for index, passage in enumerate(passages):
                        db.execute("INSERT INTO chunks VALUES(?,?,?,?,?)", (chunk_ids[index], document_id, index, passage.locator, passage.text))
                        db.execute("INSERT INTO chunk_fts VALUES(?,?)", (chunk_ids[index], " ".join(self._terms(passage.text))))
                self._index_vectors(old_chunk_ids, chunk_ids, passages)
                self._remove_unreferenced_originals()
            finally:
                temp.unlink(missing_ok=True)
        return {"document_id": document_id, "duplicate": False, "chunk_count": len(passages)}

    def _index_vectors(self, old_ids, ids, passages):
        if self.vectors is not None:
            try:
                self.vectors.delete(old_ids)
                self.vectors.put(ids, [p.text for p in passages])
            except Exception:
                self.vector_warning = "vector_index_incomplete"

    def rebuild_vectors(self):
        if self.vectors is None:
            raise LibraryError("vector_backend_not_configured")
        with self._db() as db:
            rows = db.execute("SELECT id,text FROM chunks ORDER BY document_id,ordinal").fetchall()
        for offset in range(0, len(rows), 64):
            batch = rows[offset:offset + 64]
            self.vectors.put([row["id"] for row in batch], [row["text"] for row in batch])
        self.vector_warning = None

    def passages(self, document_id):
        with self._db() as db:
            row = self._doc(db, document_id)
            parts = tuple(Passage(p["text"], p["locator"]) for p in db.execute("SELECT text,locator FROM chunks WHERE document_id=? ORDER BY ordinal", (document_id,)))
        return Reading(Paper("local", document_id, row["title"], "/api/academic-library/documents/" + document_id + "/original"), "full_text", parts, row["hash"], row["parser"])

    def original(self, document_id):
        with self._db() as db:
            row = self._doc(db, document_id)
            path = (self.originals / (row["hash"] + ".bin")).resolve()
            if path.parent != self.originals.resolve() or not path.is_file():
                raise LibraryError("document_not_found")
            return path, row["filename"]

    def _remove_unreferenced_originals(self):
        with self._db() as db:
            hashes = {row[0] for row in db.execute("SELECT hash FROM documents")}
        for path in self.originals.glob("*.bin"):
            if re.fullmatch(r"[0-9a-f]{64}", path.stem) and path.stem not in hashes:
                path.unlink()

    def delete(self, document_id):
        with self._lock:
            with self._db() as db:
                self._doc(db, document_id)
                ids = [r[0] for r in db.execute("SELECT id FROM chunks WHERE document_id=?", (document_id,))]
                for chunk_id in ids:
                    db.execute("DELETE FROM chunk_fts WHERE chunk_id=?", (chunk_id,))
                db.execute("DELETE FROM documents WHERE id=?", (document_id,))
            self._index_vectors(ids, [], ())
            self._remove_unreferenced_originals()

    def search(self, query: str, *, document_ids: tuple[str, ...] = (), limit: int = 12):
        if type(query) is not str or not query.strip() or len(query) > 4096 or type(limit) is not int or not 1 <= limit <= 64:
            raise LibraryError("invalid_query")
        if type(document_ids) is not tuple or len(document_ids) > 64:
            raise LibraryError("invalid_document_selection")
        tokens = list(dict.fromkeys(self._terms(query)))[:64]
        if not tokens:
            return []
        match = " OR ".join('"' + token + '"' for token in tokens)
        with self._db() as db:
            for document_id in document_ids:
                self._doc(db, document_id)
            clause = " AND c.document_id IN (" + ",".join("?" for _ in document_ids) + ")" if document_ids else ""
            rows = db.execute("SELECT f.chunk_id FROM chunk_fts f JOIN chunks c ON c.id=f.chunk_id WHERE chunk_fts MATCH ?" + clause + " ORDER BY bm25(chunk_fts), f.chunk_id LIMIT ?", (match, *document_ids, limit * 4)).fetchall()
            ranks = {r[0]: 1 / (60 + i) for i, r in enumerate(rows, 1)}
            if self.vectors is not None:
                try:
                    for i, chunk_id in enumerate(self.vectors.search(query, limit * 4), 1):
                        ranks[chunk_id] = ranks.get(chunk_id, 0) + 1 / (60 + i)
                except Exception:
                    self.vector_warning = "vector_search_unavailable"
            results = []
            for chunk_id in sorted(ranks, key=lambda key: (-ranks[key], key)):
                row = db.execute("SELECT c.*,d.title,d.hash FROM chunks c JOIN documents d ON d.id=c.document_id WHERE c.id=?" + clause, (chunk_id, *document_ids)).fetchone()
                if row:
                    results.append(dict(row))
                if len(results) == limit:
                    break
            return results


_library = None
_library_lock = threading.Lock()


def get_library() -> KnowledgeLibrary:
    global _library
    with _library_lock:
        if _library is None:
            root = Path(os.getenv("ACADEMIC_LIBRARY_PATH", "data/academic-library"))
            vectors, warning = None, None
            provider = os.getenv("ACADEMIC_KB_EMBEDDING_PROVIDER", "")
            model = os.getenv("ACADEMIC_KB_EMBEDDING_MODEL", "")
            if provider or model:
                try:
                    vectors = LocalVectors(root / "vectors", provider, model)
                except LibraryError as exc:
                    warning = exc.args[0]
            _library = KnowledgeLibrary(root, vectors=vectors)
            _library.vector_warning = warning
        return _library
