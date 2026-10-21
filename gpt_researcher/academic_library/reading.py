"""Read original bytes into addressable passages, never an LLM summary."""

from __future__ import annotations

import re
from collections.abc import Iterable

from .http import PublicHTTP
from .models import LibraryError, Paper, Passage, Reading, content_id


MAX_FILE_BYTES = 12_000_000
MAX_TEXT_CHARS = 2_000_000
PARSER_VERSION = "academic-reader-v1"


def _passages(parts: Iterable[tuple[str, str]]) -> tuple[Passage, ...]:
    result = []
    total = 0
    for locator, value in parts:
        value = value.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n").strip()
        if not value:
            continue
        if total + len(value) > MAX_TEXT_CHARS:
            raise LibraryError("document_text_too_large")
        total += len(value)
        # Preserve text exactly within each page/section; chunks keep offsets.
        for offset in range(0, len(value), 1800):
            result.append(Passage(value[offset:offset + 1800], f"{locator}; chars {offset}:{min(offset + 1800, len(value))}"))
    if not result:
        raise LibraryError("no_extractable_text")
    return tuple(result)


def parse_document(data: bytes, kind: str) -> tuple[Passage, ...]:
    if type(data) is not bytes or not data or len(data) > MAX_FILE_BYTES:
        raise LibraryError("invalid_document_size")
    try:
        if kind == "pdf":
            import fitz

            with fitz.open(stream=data, filetype="pdf") as doc:
                if doc.needs_pass or doc.page_count > 600:
                    raise LibraryError("unsupported_pdf")
                return _passages((f"page {i + 1}", page.get_text()) for i, page in enumerate(doc))
        if kind in ("xml", "html", "htm"):
            from lxml import etree
            from bs4 import BeautifulSoup

            if kind == "xml":
                if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
                    raise LibraryError("unsafe_xml")
                root = etree.fromstring(data, parser=etree.XMLParser(resolve_entities=False, no_network=True))
                body = root.find(".//body")
                if body is None:
                    raise LibraryError("no_fulltext_body")
                parts = [(f"paragraph {i + 1}", "".join(p.itertext())) for i, p in enumerate(body.iter("p"))]
            else:
                soup = BeautifulSoup(data, "html.parser")
                for tag in soup(["script", "style", "nav", "header", "footer", "form"]):
                    tag.decompose()
                body = soup.find("article") or soup.find("main")
                if body is None:
                    raise LibraryError("no_fulltext_body")
                parts = [(f"paragraph {i + 1}", p.get_text(" ", strip=True)) for i, p in enumerate(body.find_all("p"))]
            if sum(len(p[1]) for p in parts) < 300:
                raise LibraryError("no_fulltext_body")
            return _passages(parts)
        if kind in ("txt", "md", "csv"):
            return _passages([("text", data.decode("utf-8-sig"))])
        if kind in ("docx", "pptx", "xlsx"):
            import io
            import zipfile

            # Bound decompressed OOXML before asking a parser to read it.
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                if len(archive.infolist()) > 5000 or sum(i.file_size for i in archive.infolist()) > 50_000_000:
                    raise LibraryError("document_too_complex")
            if kind == "docx":
                from docx import Document
                doc = Document(io.BytesIO(data))
                parts = [(f"paragraph {i + 1}", p.text) for i, p in enumerate(doc.paragraphs)]
                parts += [(f"table {i + 1}", "\n".join(" | ".join(c.text for c in row.cells) for row in table.rows)) for i, table in enumerate(doc.tables)]
            elif kind == "pptx":
                from pptx import Presentation
                parts = [(f"slide {i + 1}", "\n".join(s.text for s in slide.shapes if s.has_text_frame)) for i, slide in enumerate(Presentation(io.BytesIO(data)).slides)]
            else:
                import openpyxl
                book = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
                try:
                    parts = []
                    for sheet in book:
                        for index, row in enumerate(sheet.iter_rows(values_only=True)):
                            if len(parts) >= 100_000:
                                raise LibraryError("document_too_complex")
                            parts.append((f"sheet {sheet.title}; row {index + 1}", " | ".join(str(v) if v is not None else "" for v in row)))
                finally:
                    book.close()
            return _passages(parts)
        raise LibraryError("unsupported_document_type")
    except LibraryError:
        raise
    except Exception:
        raise LibraryError("document_parse_failed") from None


class PaperReader:
    def __init__(self, http=None):
        self.http = PublicHTTP() if http is None else http

    def read(self, paper: Paper) -> Reading:
        failure = None
        for url in paper.fulltext_urls[:2]:
            try:
                result = self.http.get(url, max_bytes=MAX_FILE_BYTES)
                kind = "pdf" if result.body.startswith(b"%PDF-") else "xml" if "xml" in result.content_type or result.body.lstrip().startswith(b"<?xml") else "html"
                passages = parse_document(result.body, kind)
                return Reading(paper, "full_text", passages, content_id(result.body), PARSER_VERSION, result.url)
            except LibraryError as exc:
                failure = exc.args[0]
        # Abstract fallback is explicit and is not described as full-text reading.
        if paper.abstract:
            return Reading(paper, "abstract", (Passage(paper.abstract, "abstract"),), failure_code=failure)
        return Reading(paper, "unavailable" if failure else "metadata_only", (), failure_code=failure)


def select_passages(reading: Reading, query: str, limit: int = 3) -> tuple[Passage, ...]:
    """Cheap deterministic selection; emit chosen excerpts in original order."""
    words = set(re.findall(r"[a-zA-Z0-9]{2,}|[\u4e00-\u9fff]", query.lower()))
    ranked = sorted(enumerate(reading.passages), key=lambda item: (-sum(word in item[1].text.lower() for word in words), item[0]))
    selected = {i for i, _ in ranked[:limit]}
    return tuple(p for i, p in enumerate(reading.passages) if i in selected)
