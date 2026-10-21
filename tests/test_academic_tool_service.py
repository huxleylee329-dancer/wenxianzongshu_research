"""Offline tests for explicit approval, immutable grants, and bounded sessions."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from threading import Event

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.server.academic_tool_service import AcademicToolService, MAX_EXECUTIONS, MAX_SESSIONS, SESSION_TTL_SECONDS
from gpt_researcher.academic_library.models import LibraryError, Paper, Passage, Reading
from gpt_researcher.academic_library.tool_policy import ToolPolicyError


DOC = "a" * 32
OTHER_DOC = "b" * 32
PAPER = Paper("hal", "server-record", "Battery paper", "https://example.org/paper", "Abstract", year=2024,
              fulltext_urls=("https://example.org/original.pdf",), origins=("private-provenance",))
SEARCH = {"action": "call", "tool": "search_papers", "arguments": {"query": "battery", "providers": ["hal"]}}
LOCAL = {"action": "call", "tool": "search_library", "arguments": {"query": "battery", "document_ids": [DOC]}}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("unexpected external call")
    monkeypatch.setattr("requests.sessions.Session.request", forbidden)
    monkeypatch.setattr("socket.create_connection", forbidden)
    monkeypatch.setattr("gpt_researcher.academic_library.http.PublicHTTP.get", forbidden)
    monkeypatch.delenv("ACADEMIC_LIBRARY_TOKEN", raising=False)


class Policy:
    def __init__(self, action=SEARCH):
        self.action = deepcopy(action)
        self.calls = []

    def get_status(self):
        return {"available": True, "status": "ready", "internal_path": "never-public"}

    def decide(self, task, context):
        self.calls.append((task, deepcopy(context)))
        return deepcopy(self.action)


class Library:
    def __init__(self):
        self.ids = [DOC]

    def list_documents(self):
        return [{"id": key, "filename": "Selected.pdf", "title": "Selected"} for key in self.ids]


class Tools:
    def __init__(self):
        self.calls = []
        self.failures = []

    def search_papers(self, query, providers, *, per_source):
        self.calls.append(("search_papers", query, providers, per_source))
        return [PAPER]

    def resolve_paper(self, identifier):
        self.calls.append(("resolve_paper", identifier))
        return replace(PAPER, provider="crossref", doi=identifier)

    def read_paper(self, paper):
        self.calls.append(("read_paper", paper))
        return Reading(paper, "full_text", tuple(Passage("evidence " * 1000, f"page {i}") for i in range(20)),
                       parser="private-parser", retrieved_url="file:///secret", content_sha256="private-hash")

    def search_library(self, query, document_ids, *, limit):
        self.calls.append(("search_library", query, document_ids, limit))
        return [Reading(Paper("local", DOC, "Selected", "/api/academic-library/documents/" + DOC + "/original"),
                        "full_text", (Passage("Selected evidence", "paragraph 1"),))]


def setup_service(action=SEARCH, **kwargs):
    policy, tools, library = Policy(action), Tools(), Library()
    service = AcademicToolService(policy=policy, tools_factory=lambda: tools, library_factory=lambda: library, **kwargs)
    return service, policy, tools, library


def plan(service, **kwargs):
    return service.plan(**{"task": "Find battery papers", "mode": "web", "providers": ["hal"], "document_ids": [], **kwargs})


def execute(service, proposal, approve=True):
    return service.execute(session_id=proposal["session_id"], proposal_id=proposal["proposal_id"], approve=approve)


def test_plan_requires_explicit_approval_and_executes_once():
    service, policy, tools, _ = setup_service()
    proposal = plan(service)
    assert proposal["status"] == "awaiting_approval"
    assert len(proposal["session_id"]) == len(proposal["proposal_id"]) == 32
    assert tools.calls == []
    assert policy.calls[0][1] == {"mode": "web", "allowed_providers": ["hal"], "allowed_document_ids": [],
                                   "documents": [], "candidates": [], "existing_results": []}
    result = execute(service, proposal)
    assert result["status"] == "executed"
    assert tools.calls == [("search_papers", "battery", ("hal",), 6)]
    candidate = result["candidates"][0]
    assert candidate["paper_id"] != PAPER.record_id and len(candidate["paper_id"]) == 32
    assert "private-provenance" not in str(result) and "fulltext_urls" not in str(result)
    with pytest.raises(LibraryError, match="tool_proposal_not_found"):
        execute(service, proposal)
    assert len(tools.calls) == len(policy.calls) == 1


def test_cancel_and_non_call_decisions_perform_no_tools():
    service, policy, tools, _ = setup_service()
    proposal = plan(service)
    assert execute(service, proposal, False)["status"] == "cancelled"
    with pytest.raises(LibraryError, match="tool_proposal_not_found"):
        execute(service, proposal)
    for action, reason in (("clarify", "missing_paper"), ("finish", "no_tool_needed")):
        policy.action = {"action": action, "reason": reason}
        response = plan(service, session_id=proposal["session_id"])
        assert response["proposal_id"] is None and response["status"] == action
    assert tools.calls == []


def test_read_uses_actual_server_paper_and_caps_public_evidence():
    service, policy, tools, _ = setup_service()
    proposal = plan(service)
    result = execute(service, proposal)
    paper_id = result["candidates"][0]["paper_id"]
    policy.action = {"action": "call", "tool": "read_paper", "arguments": {"paper_id": paper_id}}
    read_proposal = plan(service, session_id=proposal["session_id"], task="Read " + paper_id)
    read_result = execute(service, read_proposal)
    assert tools.calls[-1] == ("read_paper", PAPER)
    assert tools.calls[-1][1] is PAPER
    reading = read_result["result"]["readings"][0]
    assert reading["truncated"] is True
    assert len(reading["passages"]) <= 12
    assert sum(len(p["text"]) for p in reading["passages"]) <= 16000
    assert all(len(p["text"]) <= 2000 for p in reading["passages"])
    assert all(s not in str(read_result) for s in ("private-parser", "file:///secret", "private-hash", "private-provenance"))
    policy.action = {"action": "finish", "reason": "already_satisfied"}
    plan(service, session_id=proposal["session_id"])
    assert len(policy.calls[-1][1]["existing_results"][-1]["text"]) <= 512


@pytest.mark.parametrize("action,mode,providers,documents,error", [
    (SEARCH, "local", [], [DOC], "network_not_authorized"),
    (LOCAL, "web", ["hal"], [], "local_not_authorized"),
    ({"action": "call", "tool": "resolve_paper", "arguments": {"identifier": "10.1234/test"}}, "web", ["hal"], [], "provider_not_authorized"),
    ({"action": "call", "tool": "read_paper", "arguments": {"paper_id": "unknown"}}, "web", ["hal"], [], "paper_not_authorized"),
    ({"action": "call", "tool": "search_papers", "arguments": {"query": "q", "providers": ["crossref"]}}, "web", ["hal"], [], "provider_not_authorized"),
    ({"action": "call", "tool": "search_library", "arguments": {"query": "q", "document_ids": [OTHER_DOC]}}, "local", [], [DOC], "document_not_authorized"),
    ({"action": "call", "tool": "search_library", "arguments": {"query": "q", "document_ids": []}}, "local", [], [DOC], "invalid_array"),
    ({"action": "call", "tool": "read_paper", "arguments": {"paper_id": "unknown", "url": "http://127.0.0.1/secret"}}, "web", ["hal"], [], "invalid_fields"),
    ({"action": "call", "tool": "shell", "arguments": {"command": "rm"}}, "web", ["hal"], [], "unknown_tool"),
])
def test_invalid_model_proposals_never_call_tools(action, mode, providers, documents, error):
    service, _, tools, _ = setup_service(action)
    with pytest.raises(LibraryError, match=error):
        plan(service, mode=mode, providers=providers, document_ids=documents)
    assert tools.calls == [] and service._sessions == {}


@pytest.mark.parametrize("values,error", [
    ({"mode": "auto"}, "invalid_source_mode"),
    ({"providers": []}, "provider_selection_required"),
    ({"mode": "local", "providers": [], "document_ids": []}, "document_selection_required"),
    ({"mode": "hybrid", "document_ids": [OTHER_DOC]}, "document_not_found"),
    ({"document_ids": [DOC]}, "invalid_document_selection"),
])
def test_user_scope_validation_precedes_policy(values, error):
    service, policy, tools, _ = setup_service()
    with pytest.raises(LibraryError, match=error):
        plan(service, **values)
    assert policy.calls == tools.calls == []


def test_local_search_keeps_selected_ids_and_checks_deletion_before_execute():
    service, _, tools, library = setup_service(LOCAL)
    values = {"mode": "local", "providers": [], "document_ids": [DOC]}
    proposal = plan(service, **values)
    result = execute(service, proposal)
    assert tools.calls == [("search_library", "battery", (DOC,), 12)]
    assert result["result"]["readings"][0]["passages"][0]["text"] == "Selected evidence"
    proposal = plan(service, **values, session_id=proposal["session_id"])
    library.ids = []
    with pytest.raises(LibraryError, match="document_not_found"):
        execute(service, proposal)
    with pytest.raises(LibraryError, match="tool_proposal_not_found"):
        execute(service, proposal)
    assert len(tools.calls) == 1


def test_policy_sees_current_selected_document_names_but_no_unselected_metadata():
    service, policy, tools, library = setup_service(LOCAL)
    records = [
        {"id": DOC, "filename": "Selected battery review.pdf", "title": "Selected battery review", "hash": "private-hash"},
        {"id": OTHER_DOC, "filename": "Unselected confidential.pdf", "title": "Unselected confidential"},
    ]
    library.list_documents = lambda: records
    values = {"mode": "local", "providers": [], "document_ids": [DOC]}
    proposal = plan(service, **values)
    context = policy.calls[-1][1]
    assert context["documents"] == [{"document_id": DOC, "title": "Selected battery review.pdf"}]
    assert context["allowed_document_ids"] == [DOC]
    assert "Unselected" not in str(context) and "private-hash" not in str(context)
    records[0]["filename"] = "Revised battery review.pdf"
    plan(service, **values, session_id=proposal["session_id"])
    assert policy.calls[-1][1]["documents"] == [{"document_id": DOC, "title": "Revised battery review.pdf"}]
    assert tools.calls == []


@pytest.mark.parametrize("mode,providers", [("hybrid", ["hal"]), ("local", [])])
def test_read_local_candidate_uses_library_without_changing_local_protocol(monkeypatch, mode, providers):
    service, policy, tools, library = setup_service(LOCAL)
    values = {"mode": mode, "providers": providers, "document_ids": [DOC]}
    proposal = plan(service, **values)
    result = execute(service, proposal)
    paper_id = result["candidates"][0]["paper_id"]
    library_reads = []

    def read_document(document_id):
        library_reads.append(document_id)
        assert document_id == DOC and document_id in library.ids
        return Reading(Paper("local", DOC, "Selected", "/api/academic-library/documents/" + DOC + "/original"),
                       "full_text", (Passage("Stored full document evidence", "paragraph 2"),))

    def forbidden_reader(_):
        pytest.fail("Local candidates must never enter the network paper reader")

    monkeypatch.setattr(library, "passages", read_document, raising=False)
    monkeypatch.setattr(tools, "read_paper", forbidden_reader)
    policy.action = {"action": "call", "tool": "read_paper", "arguments": {"paper_id": paper_id}}
    if mode == "local":
        with pytest.raises(LibraryError, match="network_not_authorized"):
            plan(service, **values, session_id=proposal["session_id"])
        assert library_reads == []
        return

    read_proposal = plan(service, **values, session_id=proposal["session_id"])
    assert library_reads == []
    read_result = execute(service, read_proposal)
    reading = read_result["result"]["readings"][0]
    assert library_reads == [DOC]
    assert reading["paper_id"] == paper_id and reading["status"] == "full_text"
    assert reading["passages"] == [{"locator": "paragraph 2", "text": "Stored full document evidence"}]
    assert tools.calls == [("search_library", "battery", (DOC,), 12)]


def test_crossref_requires_explicit_selected_provider():
    service, _, tools, _ = setup_service({"action": "call", "tool": "resolve_paper", "arguments": {"identifier": "doi:10.1234/TEST"}})
    proposal = plan(service, providers=["crossref"])
    result = execute(service, proposal)
    assert tools.calls == [("resolve_paper", "10.1234/test")]
    assert result["result"]["papers"][0]["provider"] == "crossref"


def test_pubmed_central_accepts_actual_europe_pmc_provider():
    service, _, tools, _ = setup_service({"action": "call", "tool": "search_papers", "arguments": {"query": "battery", "providers": ["pubmed_central"]}})
    tools.search_papers = lambda *args, **kwargs: [replace(PAPER, provider="europe_pmc")]
    result = execute(service, plan(service, providers=["pubmed_central"]))
    assert result["candidates"][0]["provider"] == "europe_pmc"


def test_unknown_cross_session_and_changed_scope_are_rejected():
    service, _, tools, _ = setup_service()
    first, second = plan(service), plan(service)
    with pytest.raises(LibraryError, match="tool_session_not_found"):
        plan(service, session_id="0" * 32)
    with pytest.raises(LibraryError, match="tool_proposal_not_found"):
        service.execute(session_id=second["session_id"], proposal_id=first["proposal_id"], approve=True)
    with pytest.raises(LibraryError, match="tool_scope_changed"):
        plan(service, session_id=first["session_id"], providers=["crossref"])
    replacement = plan(service, session_id=first["session_id"])
    with pytest.raises(LibraryError, match="tool_proposal_not_found"):
        execute(service, first)
    execute(service, replacement)
    assert len(tools.calls) == 1


def test_model_context_mutation_cannot_expand_scope_and_execution_reauthorizes():
    service, policy, tools, _ = setup_service()
    def tamper(task, context):
        context["allowed_providers"].append("crossref")
        return {"action": "call", "tool": "resolve_paper", "arguments": {"identifier": "10.1234/test"}}
    policy.decide = tamper
    with pytest.raises(LibraryError, match="provider_not_authorized"):
        plan(service)
    policy.decide = lambda *args: deepcopy(SEARCH)
    proposal = plan(service)
    service._sessions[proposal["session_id"]].proposal["arguments"]["providers"] = ["crossref"]
    with pytest.raises(LibraryError, match="provider_not_authorized"):
        execute(service, proposal)
    assert tools.calls == []


def test_failed_tool_and_policy_return_safe_codes_and_do_not_replay():
    service, policy, tools, _ = setup_service()
    def fail(*args, **kwargs):
        raise RuntimeError("private /secret token")
    tools.search_papers = fail
    proposal = plan(service)
    with pytest.raises(LibraryError, match="^tool_execution_failed$"):
        execute(service, proposal)
    with pytest.raises(LibraryError, match="tool_proposal_not_found"):
        execute(service, proposal)
    policy.decide = fail
    with pytest.raises(LibraryError, match="^tool_policy_unavailable$"):
        plan(service)
    policy.get_status = fail
    assert service.status() == {"available": False, "status": "unavailable", "supervised": True, "max_executions": 16}


def test_parallel_execute_and_plan_are_busy_and_do_not_duplicate():
    service, _, tools, _ = setup_service()
    proposal = plan(service)
    entered, release = Event(), Event()
    original = tools.search_papers
    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)
    tools.search_papers = blocked
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(execute, service, proposal)
        try:
            assert entered.wait(5)
            with pytest.raises(LibraryError, match="tool_session_busy"):
                execute(service, proposal)
            with pytest.raises(LibraryError, match="tool_session_busy"):
                plan(service, session_id=proposal["session_id"])
        finally:
            release.set()
        assert future.result()["status"] == "executed"
    assert len(tools.calls) == 1


def test_session_capacity_expiry_and_execution_budget():
    now = [0.0]
    service, _, tools, _ = setup_service(clock=lambda: now[0])
    proposals = [plan(service) for _ in range(MAX_SESSIONS)]
    with pytest.raises(LibraryError, match="tool_session_limit"):
        plan(service)
    now[0] += SESSION_TTL_SECONDS
    with pytest.raises(LibraryError, match="tool_session_not_found"):
        execute(service, proposals[0])
    proposal = plan(service)
    for _ in range(MAX_EXECUTIONS):
        execute(service, proposal)
        if len(tools.calls) < MAX_EXECUTIONS:
            proposal = plan(service, session_id=proposal["session_id"])
    with pytest.raises(LibraryError, match="tool_execution_limit"):
        plan(service, session_id=proposal["session_id"])
    assert len(tools.calls) == MAX_EXECUTIONS


def test_api_inherits_access_guard_and_rejects_forged_action(monkeypatch):
    from backend.server import academic_library_api as api, academic_tool_service as module
    service, _, tools, _ = setup_service()
    monkeypatch.setattr(module, "get_tool_service", lambda: service)
    app = FastAPI()
    app.include_router(api.router)
    prefix = "/api/academic-library/tool-policy"
    payload = {"task": "battery", "mode": "web", "providers": ["hal"], "document_ids": []}
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        assert client.get(prefix + "/status").status_code == 403
        assert client.post(prefix + "/plan", json=payload).status_code == 403
        assert client.post(prefix + "/execute", json={"session_id": "a" * 32, "proposal_id": "b" * 32, "approve": True}).status_code == 403
        app.dependency_overrides[api.require_library_access] = lambda: None
        assert client.get(prefix + "/status").json() == {"available": True, "status": "ready", "supervised": True, "max_executions": 16}
        assert client.post(prefix + "/plan", json={**payload, "candidates": []}).status_code == 422
        response = client.post(prefix + "/plan", json=payload)
        assert response.status_code == 200
        proposal = response.json()
        approval = {"session_id": proposal["session_id"], "proposal_id": proposal["proposal_id"], "approve": True}
        assert client.post(prefix + "/execute", json={**approval, "action": SEARCH}).status_code == 422
        assert client.post(prefix + "/execute", json={**approval, "approve": "true"}).status_code == 422
        assert tools.calls == []
        assert client.post(prefix + "/execute", json=approval).status_code == 200
        replay = client.post(prefix + "/execute", json=approval)
        assert replay.status_code == 400 and replay.json() == {"detail": "tool_proposal_not_found"}
