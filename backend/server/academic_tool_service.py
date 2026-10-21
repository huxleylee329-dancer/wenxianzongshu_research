"""Single-step policy proposals with explicit approval and server-owned arguments."""

from __future__ import annotations

import hashlib
import os
import re
import threading
import time
import uuid
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field

from gpt_researcher.academic_library.models import LibraryError, Paper, Reading
from gpt_researcher.academic_library.research import PaperTools, validate_selection
from gpt_researcher.academic_library.store import get_library


MAX_SESSIONS = 32
SESSION_TTL_SECONDS = 30 * 60
MAX_EXECUTIONS = 16
MAX_CANDIDATES = 16
MAX_PASSAGES = 12
MAX_PASSAGE_CHARS = 2000
MAX_RESULT_CHARS = 16000
_ID = re.compile(r"[0-9a-f]{32}")


@dataclass
class _Session:
    session_id: str
    mode: str
    providers: tuple[str, ...]
    document_ids: tuple[str, ...]
    touched: float
    lock: threading.Lock = field(default_factory=threading.Lock)
    proposal_id: str | None = None
    proposal: dict | None = None
    executions: int = 0
    papers: dict[str, Paper] = field(default_factory=dict)
    results: list[dict] = field(default_factory=list)
    documents: list[dict] = field(default_factory=list)


class AcademicToolService:
    """In-memory sessions owned by the library router's authenticated workbench."""

    def __init__(self, *, policy=None, tools_factory=None, library_factory=None,
                 clock=time.monotonic):
        self._policy = policy
        self._tools_factory = tools_factory or PaperTools
        self._library_factory = library_factory or get_library
        self._clock = clock
        self._sessions: dict[str, _Session] = {}
        self._lock = threading.Lock()
        self._policy_lock = threading.Lock()

    def _get_policy(self):
        # Importing the API does not load a model, library, or embeddings.
        with self._policy_lock:
            if self._policy is None:
                from gpt_researcher.academic_library.tool_policy import ToolPolicy
                self._policy = ToolPolicy(os.getenv("ACADEMIC_TOOL_POLICY_URL", "http://127.0.0.1:8091"))
            return self._policy

    def status(self):
        try:
            result = self._get_policy().get_status()
            if type(result) is not dict:
                raise ValueError()
            available = result.get("available") is True
            return {"available": available, "status": "ready" if available else "unavailable",
                    "supervised": True, "max_executions": MAX_EXECUTIONS}
        except Exception:
            return {"available": False, "status": "unavailable", "supervised": True,
                    "max_executions": MAX_EXECUTIONS}

    def _validate_documents(self, document_ids):
        if not document_ids:
            return []
        try:
            documents = self._library_factory().list_documents()
            owned = {document["id"]: document for document in documents}
        except Exception:
            raise LibraryError("tool_library_unavailable") from None
        if not set(document_ids).issubset(owned):
            raise LibraryError("document_not_found")
        selected = []
        for document_id in document_ids:
            document = owned[document_id]
            # Match the library selector's filename label; never include paths,
            # hashes, chunk contents, or metadata from unselected documents.
            title = next((document.get(key) for key in ("filename", "title")
                          if type(document.get(key)) is str and document[key].strip()), None)
            if title is None:
                raise LibraryError("tool_library_unavailable")
            selected.append({"document_id": document_id, "title": title[:512]})
        return selected

    @contextmanager
    def _session(self, session_id, scope=None):
        created = session_id is None
        if not created and (type(session_id) is not str or not _ID.fullmatch(session_id)):
            raise LibraryError("tool_session_not_found")
        with self._lock:
            now = self._clock()
            for key, old in tuple(self._sessions.items()):
                if now - old.touched >= SESSION_TTL_SECONDS and old.lock.acquire(blocking=False):
                    del self._sessions[key]
                    old.lock.release()
            if created:
                if scope is None:
                    raise LibraryError("tool_session_not_found")
                if len(self._sessions) >= MAX_SESSIONS:
                    raise LibraryError("tool_session_limit")
                session_id = uuid.uuid4().hex
                session = _Session(session_id, *scope, touched=now)
                self._sessions[session_id] = session
            else:
                session = self._sessions.get(session_id)
                if session is None:
                    raise LibraryError("tool_session_not_found")
            if not session.lock.acquire(blocking=False):
                raise LibraryError("tool_session_busy")
            session.touched = now
        try:
            if scope is not None and scope != (session.mode, session.providers, session.document_ids):
                raise LibraryError("tool_scope_changed")
            yield session
        except Exception:
            if created:
                with self._lock:
                    self._sessions.pop(session_id, None)
            raise
        finally:
            session.touched = self._clock()
            session.lock.release()

    @staticmethod
    def _metadata(paper_id, paper):
        return {"paper_id": paper_id, "title": paper.title[:512],
                "provider": paper.provider[:64],
                "year": paper.year if type(paper.year) is int and 1000 <= paper.year <= 2200 else None,
                "doi": (paper.doi or "")[:512], "url": paper.url[:1024]}

    def _candidates(self, session):
        return [self._metadata(key, paper) for key, paper in session.papers.items()]

    def _context(self, session):
        return {"mode": session.mode, "allowed_providers": list(session.providers),
                "allowed_document_ids": list(session.document_ids),
                "documents": deepcopy(session.documents),
                "candidates": self._candidates(session), "existing_results": deepcopy(session.results[-8:])}

    @staticmethod
    def _authorize(action, context):
        from gpt_researcher.academic_library.tool_policy import ToolPolicyError, authorize
        try:
            return authorize(action, context)
        except ToolPolicyError as exc:
            raise LibraryError(str(exc)) from None
        except Exception:
            raise LibraryError("tool_policy_invalid_decision") from None

    def plan(self, *, task, mode, providers, document_ids, session_id=None):
        if type(task) is not str or not task.strip() or len(task) > 4096:
            raise LibraryError("invalid_task")
        scope = validate_selection(mode, providers, document_ids)
        with self._session(session_id, scope) as session:
            # A new plan retires its predecessor even when inference fails.
            session.proposal_id = session.proposal = None
            if session.executions >= MAX_EXECUTIONS:
                raise LibraryError("tool_execution_limit")
            session.documents = self._validate_documents(session.document_ids)
            from gpt_researcher.academic_library.tool_policy import ToolPolicyError
            try:
                decision = self._get_policy().decide(task, self._context(session))
            except ToolPolicyError as exc:
                raise LibraryError(str(exc)) from None
            except Exception:
                raise LibraryError("tool_policy_unavailable") from None
            decision = self._authorize(decision, self._context(session))
            if decision["action"] == "call":
                session.proposal_id = uuid.uuid4().hex
                session.proposal = deepcopy(decision)
            return {"session_id": session.session_id, "proposal_id": session.proposal_id,
                    "decision": deepcopy(decision), "candidates": self._candidates(session),
                    "status": "awaiting_approval" if session.proposal else decision["action"]}

    def _register(self, session, paper):
        if not isinstance(paper, Paper):
            raise LibraryError("tool_invalid_result")
        if paper.provider == "local":
            if session.mode == "web" or paper.record_id not in session.document_ids:
                raise LibraryError("tool_invalid_result")
        elif session.mode == "local" or (paper.provider not in session.providers
                and not (paper.provider == "europe_pmc" and "pubmed_central" in session.providers)):
            raise LibraryError("tool_invalid_result")
        paper_id = hashlib.sha256((session.session_id + ":" + paper.key).encode()).hexdigest()[:32]
        session.papers[paper_id] = paper
        while len(session.papers) > MAX_CANDIDATES:
            del session.papers[next(iter(session.papers))]
        return self._metadata(paper_id, paper)

    def _reading(self, session, reading, remaining):
        if not isinstance(reading, Reading):
            raise LibraryError("tool_invalid_result")
        metadata = self._register(session, reading.paper)
        status = reading.status if reading.status in ("full_text", "abstract", "metadata_only", "unavailable") else "unavailable"
        passages = []
        for passage in reading.passages[:MAX_PASSAGES]:
            text = passage.text[:min(MAX_PASSAGE_CHARS, remaining)]
            if not text:
                break
            passages.append({"locator": passage.locator[:256], "text": text})
            remaining -= len(text)
        session.results.append({**metadata, "status": status,
                                "text": "\n".join(p["text"] for p in passages)[:512]})
        session.results[:] = session.results[-8:]
        return {**metadata, "status": status, "passages": passages,
                "truncated": bool(reading.truncated or len(passages) < len(reading.passages)
                                  or any(len(p.text) > len(public["text"])
                                         for p, public in zip(reading.passages, passages)))}, remaining

    def _dispatch(self, session, action):
        name, arguments = action["tool"], action["arguments"]
        # No tools are instantiated until authorization and explicit approval.
        tools = self._tools_factory()
        if name == "search_papers":
            papers = tools.search_papers(arguments["query"], tuple(arguments["providers"]),
                                         per_source=arguments.get("per_source", 6))
            metadata = [self._register(session, paper) for paper in papers[:MAX_CANDIDATES]]
            session.results.extend({**paper, "status": "metadata_only"} for paper in metadata)
            session.results[:] = session.results[-8:]
            failures = getattr(tools, "failures", [])
            return {"tool": name, "papers": metadata,
                    "warning": "provider_search_incomplete" if failures else None}
        if name == "resolve_paper":
            metadata = self._register(session, tools.resolve_paper(arguments["identifier"]))
            session.results.append({**metadata, "status": "metadata_only"})
            session.results[:] = session.results[-8:]
            return {"tool": name, "papers": [metadata]}
        if name == "read_paper":
            paper = session.papers.get(arguments["paper_id"])
            if paper is None:
                raise LibraryError("tool_unknown_paper")
            if paper.provider == "local":
                if paper.record_id not in session.document_ids:
                    raise LibraryError("document_not_authorized")
                source = self._library_factory().passages(paper.record_id)
            else:
                source = tools.read_paper(paper)
            reading, _ = self._reading(session, source, MAX_RESULT_CHARS)
            return {"tool": name, "readings": [reading]}
        if name == "search_library":
            readings = tools.search_library(arguments["query"], tuple(arguments["document_ids"]),
                                             limit=arguments.get("limit", 12))
            public, remaining = [], MAX_RESULT_CHARS
            for reading in readings[:MAX_CANDIDATES]:
                item, remaining = self._reading(session, reading, remaining)
                public.append(item)
            return {"tool": name, "readings": public}
        raise LibraryError("tool_policy_invalid_decision")

    def execute(self, *, session_id, proposal_id, approve):
        if type(approve) is not bool:
            raise LibraryError("tool_approval_required")
        with self._session(session_id) as session:
            if (type(proposal_id) is not str or not _ID.fullmatch(proposal_id)
                    or session.proposal_id != proposal_id or session.proposal is None):
                raise LibraryError("tool_proposal_not_found")
            action = session.proposal
            # Consume before any tools, so errors and cancellations cannot replay.
            session.proposal_id = session.proposal = None
            response = {"session_id": session.session_id, "proposal_id": proposal_id}
            if not approve:
                return {**response, "status": "cancelled", "result": None,
                        "candidates": self._candidates(session)}
            if session.executions >= MAX_EXECUTIONS:
                raise LibraryError("tool_execution_limit")
            session.documents = self._validate_documents(session.document_ids)
            action = self._authorize(action, self._context(session))
            if action["action"] != "call":
                raise LibraryError("tool_policy_invalid_decision")
            session.executions += 1
            try:
                result = self._dispatch(session, action)
            except Exception:
                raise LibraryError("tool_execution_failed") from None
            return {**response, "status": "executed", "result": result,
                    "candidates": self._candidates(session)}


_service = None
_service_lock = threading.Lock()


def get_tool_service():
    global _service
    with _service_lock:
        if _service is None:
            _service = AcademicToolService()
        return _service
