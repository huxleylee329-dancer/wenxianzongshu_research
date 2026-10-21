"""Offline evidence reading, durable library, API and workflow integration."""

import asyncio
import io
import json
from collections import Counter
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gpt_researcher.academic_library.http import Download, PublicHTTP
from gpt_researcher.academic_library.models import LibraryError, Paper, Passage, Reading
from gpt_researcher.academic_library.reading import PaperReader, parse_document, select_passages
from gpt_researcher.academic_library.research import AcademicPaperResearcher, PaperTools, validate_selection
from gpt_researcher.academic_library.store import KnowledgeLibrary


@pytest.fixture(autouse=True)
def no_external_calls(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("unexpected external call")
    monkeypatch.setattr(PublicHTTP, "get", forbidden)
    monkeypatch.setattr("socket.create_connection", forbidden)
    monkeypatch.delenv("ACADEMIC_LIBRARY_TOKEN", raising=False)
    monkeypatch.delenv("ACADEMIC_KB_EMBEDDING_PROVIDER", raising=False)
    monkeypatch.delenv("ACADEMIC_KB_EMBEDDING_MODEL", raising=False)


def test_reading_original_pdf_xml_and_explicit_abstract_fallback():
    import fitz
    doc = fitz.open()
    page = doc.new_page(); page.insert_text((60, 80), "Original battery evidence.")
    data = doc.tobytes(); doc.close()
    passages = parse_document(data, "pdf")
    assert passages[0].locator.startswith("page 1;")
    assert "Original battery" in passages[0].text
    xml = ("<article><body><sec><p>" + "Original evidence. " * 30 + "</p></sec></body></article>").encode()
    class Transport:
        def get(self, *a, **k):
            return Download(xml, "application/xml", "https://example.org/fulltext")
    paper = Paper("europe_pmc", "PMC1", "Paper", "https://example.org/1", "Abstract", fulltext_urls=("https://example.org/fulltext",))
    reading = PaperReader(Transport()).read(paper)
    assert reading.status == "full_text" and reading.content_sha256 and reading.passages[0].locator.startswith("paragraph 1;")
    class Failing:
        def get(self, *a, **k):
            raise LibraryError("http_failed")
    fallback = PaperReader(Failing()).read(paper)
    assert fallback.status == "abstract" and fallback.failure_code == "http_failed"
    assert PaperReader(Failing()).read(replace(paper, abstract="")).status == "unavailable"
    with pytest.raises(LibraryError, match="unsafe_xml"):
        parse_document(b'<!DOCTYPE a [<!ENTITY secret SYSTEM "file:///etc/passwd">]><article/>', "xml")
    with pytest.raises(LibraryError, match="no_fulltext_body"):
        parse_document(b"<html><body>Sign in to read</body></html>", "html")


def test_library_persistence_duplicate_update_delete_and_chinese(tmp_path):
    library = KnowledgeLibrary(tmp_path)
    data = ("电池 界面稳定性\nSilicon anode battery evidence.\n" * 60).encode()
    result = library.import_document("电池.md", data)
    document_id = result["document_id"]
    assert result["chunk_count"] > 1
    assert library.import_document("duplicate.md", data) == {"document_id": document_id, "duplicate": True}
    reopened = KnowledgeLibrary(tmp_path)
    for query in ("battery", "界面稳定"):
        hits = reopened.search(query, document_ids=(document_id,))
        assert hits and {h["document_id"] for h in hits} == {document_id}
    assert reopened.original(document_id)[0].read_bytes() == data
    reopened.import_document("revised.md", b"Completely different catalyst research.", replace_id=document_id)
    assert not reopened.search("battery")
    assert reopened.search("catalyst") and len(list((tmp_path / "originals").glob("*.bin"))) == 1
    reopened.delete(document_id)
    assert reopened.list_documents() == [] and reopened.search("catalyst") == []
    assert not list((tmp_path / "originals").glob("*.bin"))


def test_vector_fusion_never_returns_stale_or_unselected_content(tmp_path):
    class Vectors:
        def __init__(self):
            self.ids = []
        def put(self, ids, texts):
            self.ids.extend(ids)
        def delete(self, ids):
            pass  # deliberately stale backend; SQLite must enforce visibility
        def search(self, query, limit):
            return self.ids
    vectors = Vectors()
    library = KnowledgeLibrary(tmp_path, vectors=vectors)
    first = library.import_document("a.md", b"Battery first evidence.")["document_id"]
    second = library.import_document("b.md", b"Battery second evidence.")["document_id"]
    assert {h["document_id"] for h in library.search("battery", document_ids=(second,))} == {second}
    library.delete(first)
    assert {h["document_id"] for h in library.search("unrelated")} == {second}
    library.import_document("b.md", b"Updated battery evidence.", replace_id=second)
    assert all("Updated" in h["text"] for h in library.search("unrelated"))
    library.rebuild_vectors()
    assert library.vector_warning is None


@pytest.mark.parametrize("filename,data,code", [
    ("../escape.txt", b"text", "invalid_filename"),
    ("bad.txt", b"", "invalid_document_size"),
    ("bad.exe", b"binary", "unsupported_document_type"),
    ("bad.txt", b"\xff", "document_parse_failed"),
])
def test_library_invalid_imports_never_produce_documents(tmp_path, filename, data, code):
    library = KnowledgeLibrary(tmp_path)
    with pytest.raises(LibraryError, match=code):
        library.import_document(filename, data)
    assert library.list_documents() == []
    assert not list((tmp_path / "originals").iterdir())


def test_docx_retains_paragraph_locations():
    from docx import Document
    doc = Document(); doc.add_paragraph("Experimental measurement.")
    buffer = io.BytesIO(); doc.save(buffer)
    passages = parse_document(buffer.getvalue(), "docx")
    assert passages[0].text == "Experimental measurement." and "paragraph 1" in passages[0].locator


@pytest.mark.parametrize("mode", ["web", "local", "hybrid"])
@pytest.mark.asyncio
async def test_real_evidence_adapter_accepts_three_modes_without_llm(tmp_path, monkeypatch, mode):
    from gpt_researcher.workflows.academic_writing.research_evidence import GPTResearcherResearchEvidenceAdapter, _create_production_researcher
    from gpt_researcher.workflows.academic_writing.state import AcademicWorkflowRequest, WorkflowTopicPlan
    from gpt_researcher.utils.enum import Tone
    from gpt_researcher.academic_library import research
    library = KnowledgeLibrary(tmp_path)
    doc_id = library.import_document("local.md", b"Battery interface measured evidence.")["document_id"]
    tools = PaperTools(library=library)
    calls = []
    paper = Paper("hal", "1", "Online battery", "https://example.org/paper", "Battery evidence.")
    def search(query, providers, per_source):
        calls.append(query)
        return [paper]
    tools.search_papers = search
    tools.read_paper = lambda p: Reading(p, "abstract", (Passage(p.abstract, "abstract"),))
    request = AcademicWorkflowRequest(workflow_mode="academic_langgraph", workflow_id="w", thread_id="t", run_id="r", query="battery", report_type="research_report", report_source=mode, tone=Tone.Formal.value, language="zh-CN", max_search_results=4, source_urls=(), document_urls=("library:" + doc_id,) if mode != "web" else (), query_domains=())
    plan = WorkflowTopicPlan(topic_plan_id="topic-plan:000001", workflow_id="w", run_id="r", attempt=1, research_topic="battery", research_questions=("battery interface",))
    monkeypatch.setenv("ACADEMIC_PAPER_PROVIDERS", "hal")
    monkeypatch.setattr(research, "PaperTools", lambda: tools)
    def forbidden(*args, **kwargs):
        pytest.fail("library research must not construct the legacy GPTResearcher")
    monkeypatch.setattr("gpt_researcher.agent.GPTResearcher", forbidden)
    researcher = (AcademicPaperResearcher(request, plan, tools=tools, providers=("hal",)) if mode == "web" else _create_production_researcher(request, plan))
    adapter = GPTResearcherResearchEvidenceAdapter(object(), researcher_factory=lambda *args: researcher)
    evidence = await adapter.collect_research_evidence(request, plan)
    assert len(evidence.sources) == (2 if mode == "hybrid" else 1)
    assert len(evidence.provenance) == len(evidence.sources)
    assert all(p.evidence_blocks for p in evidence.provenance)
    assert bool(calls) == (mode != "local")
    assert all(s["content_status"] in ("abstract", "full_text") for s in researcher.summaries)
    if mode == "local":
        assert researcher.providers == ()
    if mode != "web":
        bad = request.model_copy(update={"document_urls": ("file:///private.txt", "library:" + doc_id)})
        with pytest.raises(LibraryError, match="invalid_document_selection"):
            _create_production_researcher(bad, plan)


def test_library_http_endpoints_and_access_boundary(tmp_path, monkeypatch):
    from backend.server import academic_library_api as api
    library = KnowledgeLibrary(tmp_path)
    monkeypatch.setattr(api, "get_library", lambda: library)
    app = FastAPI(); app.include_router(api.router)
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        # TestClient is not loopback; prove access check, then override it only
        # for endpoint behavior. Never weaken the production dependency.
        assert client.get("/api/academic-library/documents").status_code == 403
        app.dependency_overrides[api.require_library_access] = lambda: None
        result = client.post("/api/academic-library/documents", files={"file": ("paper.txt", b"Battery evidence.", "text/plain")})
        assert result.status_code == 200
        doc_id = result.json()["document_id"]
        assert client.get("/api/academic-library/documents").json()["documents"][0]["id"] == doc_id
        assert client.post("/api/academic-library/search", json={"query": "battery"}).json()["results"]
        response = client.get(f"/api/academic-library/documents/{doc_id}/original")
        assert response.content == b"Battery evidence." and response.headers["x-content-type-options"] == "nosniff"
        assert client.get(f"/api/academic-library/documents/{doc_id}/passages").json()["passages"]
        assert client.delete(f"/api/academic-library/documents/{doc_id}").json()["deleted"]
        assert client.get(f"/api/academic-library/documents/{doc_id}/original").status_code == 400
    from starlette.requests import Request
    def request(host="localhost:8000", origin="http://localhost:8000", peer="127.0.0.1"):
        return Request({"type": "http", "method": "GET", "scheme": "http", "path": "/", "query_string": b"", "server": ("localhost", 8000), "client": (peer, 123), "headers": [(b"host", host.encode()), (b"origin", origin.encode())]})
    api.require_library_access(request())
    from fastapi import HTTPException
    for bad in (request(host="evil.example", origin="http://evil.example"), request(origin="http://evil.example"), request(peer="10.0.0.2")):
        with pytest.raises(HTTPException):
            api.require_library_access(bad)


@pytest.mark.parametrize("mode,providers,ids", [("hybrid", ["hal"], []), ("local", [], []), ("web", ["made_up"], []), ("web", ["hal"], ["123"]), ("web", ["hal", "hal"], [])])
def test_invalid_source_selection_is_preflight_only(mode, providers, ids):
    with pytest.raises(LibraryError):
        validate_selection(mode, providers, ids)


@pytest.mark.parametrize("mode,ids", [("local", []), ("hybrid", ["../outside"]), ("local", ["f" * 32])])
@pytest.mark.asyncio
async def test_invalid_library_documents_reject_before_planning(tmp_path, monkeypatch, mode, ids):
    from backend.server import academic_workflow_service as module
    from gpt_researcher.academic_library import store
    library = KnowledgeLibrary(tmp_path / "library")
    monkeypatch.setattr(store, "get_library", lambda: library)
    def forbidden(*args, **kwargs):
        pytest.fail("invalid document selection must fail before any workflow/adapter call")
    monkeypatch.setattr(module, "start_academic_workflow", forbidden)
    service = module.AcademicWorkflowService(output_root=tmp_path / "outputs", adapter_factory=forbidden)
    with pytest.raises(module.AcademicWorkflowServiceError, match="invalid_request"):
        await service.start({"query": "Battery", "report_mode": "stem_literature_review", "source_mode": mode, "document_ids": ids}, forbidden)
    assert service._sessions == {}


@pytest.mark.parametrize("mode", ["web", "local", "hybrid"])
@pytest.mark.parametrize("verdict", ["supported", "uncertain"])
@pytest.mark.asyncio
async def test_web_service_real_pipeline_all_source_modes(tmp_path, monkeypatch, mode, verdict):
    from backend.server import academic_workflow_service as module
    from gpt_researcher.academic_library import store
    from gpt_researcher.workflows.academic_writing import topic_planner, outline_writer, section_writer, citation_reviewer
    from gpt_researcher.workflows.academic_writing.section_writer_sequence import GPTResearcherSectionWriterSequence
    from gpt_researcher.workflows.academic_writing.academic_draft_composer import GPTResearcherAcademicDraftComposer

    library = KnowledgeLibrary(tmp_path / "library")
    doc_id = library.import_document("battery.md", b"Battery interface measured evidence.")["document_id"]
    library.import_document("unselected.md", b"Battery private unselected evidence.")
    monkeypatch.setattr(store, "get_library", lambda: library)
    monkeypatch.setattr("gpt_researcher.academic_library.research.get_library", lambda: library)
    paper = Paper("hal", "1", "Paper", "https://example.org/paper", "Battery interface evidence.")
    search_calls, messages_to_llm, calls = [], [], Counter()
    def search(*args, **kwargs):
        assert mode != "local", "local mode must never enter online discovery"
        search_calls.append(args)
        return [paper]
    monkeypatch.setattr(PaperTools, "search_papers", search)
    # Only external completions/discovery are fake: planning, outline parsing,
    # Writer/Sequence, Gate, Reviewer, Composer, merge/render and graph are real.
    class Client:
        def __init__(self, stage):
            self.stage = stage
        async def complete(self, *, system_message, user_message):
            calls[self.stage] += 1
            messages_to_llm.append(user_message)
            data = json.loads(user_message)
            if self.stage == "plan":
                assert data["report_source"] == mode
                return json.dumps({"research_questions": ["Battery interface"]})
            if self.stage == "outline":
                return json.dumps({"title": "Battery", "sections": [{"role": item["role"], "brief": "Discuss available evidence"} for item in data["section_profile"]]})
            if self.stage == "writer":
                return json.dumps({"content": "Measured evidence " + " ".join(item["citation_marker"] for item in data["evidence_sources"])})
            return json.dumps({"section_id": data["section_id"], "cited_source_ids": data["cited_source_ids"], "verdict": verdict, "issues": [] if verdict == "supported" else ["insufficient_evidence"], "rationale": "Offline fixture opinion."})
    monkeypatch.setattr(topic_planner, "_create_production_planner_client", lambda: Client("plan"))
    monkeypatch.setattr(outline_writer, "_create_production_outline_writer_client", lambda: Client("outline"))
    writer = section_writer.GPTResearcherSectionWriterAdapter(section_writer_client_factory=lambda: Client("writer"))
    reviewer = citation_reviewer.GPTResearcherCitationReviewerAdapter(citation_reviewer_client_factory=lambda: Client("review"))
    composer = GPTResearcherAcademicDraftComposer(section_writer_sequence=GPTResearcherSectionWriterSequence(section_writer=writer), citation_reviewer=reviewer)
    service = module.AcademicWorkflowService(output_root=tmp_path / "outputs", composer_factory=lambda: composer)
    messages = []
    async def emit(message):
        messages.append(message)
    await service.start({"query": "Battery", "report_mode": "stem_literature_review", "source_mode": mode, "paper_providers": ["hal"] if mode != "local" else [], "document_ids": [doc_id] if mode != "web" else []}, emit)
    session_id = messages[-1]["session_id"]
    evidence_message = next(m for m in messages if m["type"] == "academic_evidence")
    assert len(evidence_message["sources"]) == (2 if mode == "hybrid" else 1)
    assert "raw_content" not in str(evidence_message) and "passages" not in str(evidence_message)
    assert bool(search_calls) == (mode != "local")
    assert {s["provider"] for s in evidence_message["sources"]} == ({"hal"} if mode == "web" else {"local"} if mode == "local" else {"local", "hal"})
    assert calls == {"plan": 1, "outline": 1}
    await service.decision({"session_id": session_id, "decision": "approve"}, emit)
    assert messages[-1]["type"] == ("academic_ready" if verdict == "supported" else "academic_review_required")
    session = service._sessions[session_id]
    assert session.state.request.report_source == mode
    assert session.state.phase == ("draft_ready" if verdict == "supported" else "review_required")
    assert session.state.status == "completed"
    assert session.state.events[-1].event_type == "workflow_completed"
    assert calls == {"plan": 1, "outline": 1, "writer": 8, "review": 8}
    assert all("private unselected" not in message for message in messages_to_llm)
    with pytest.raises(module.AcademicWorkflowServiceError):
        await service.decision({"session_id": session_id, "decision": "approve"}, emit)
    assert calls == {"plan": 1, "outline": 1, "writer": 8, "review": 8}


def test_optional_vector_configuration_never_selects_cloud(monkeypatch, tmp_path):
    from gpt_researcher.academic_library.store import LocalVectors
    for provider in ("openai", "custom", ""):
        with pytest.raises(LibraryError, match="local_embedding_configuration_required"):
            LocalVectors(tmp_path, provider, "model")
