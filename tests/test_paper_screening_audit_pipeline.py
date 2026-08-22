import asyncio
import socket
from types import SimpleNamespace

import pytest

from gpt_researcher.agent import GPTResearcher
from gpt_researcher.screening.audit import (
    AUDIT_UNAVAILABLE_MESSAGE,
    AuditCollectorState,
    PaperScreeningAuditCollector,
    PaperScreeningRequestMetadata,
    build_paper_screening_web_pass_audit,
)
from gpt_researcher.screening.collection import CollectorState, PaperCandidateCollector
from gpt_researcher.screening.decisions import ScreeningPolicy
from gpt_researcher.screening.rules import screen_paper_occurrences
from gpt_researcher.screening.workspace import ScreeningWorkspace, WorkspaceState
from gpt_researcher.skills.researcher import PreparedEvidenceRequest, ResearchConductor
from gpt_researcher.utils.enum import ReportSource, ReportType


@pytest.fixture(autouse=True)
def _no_external_access(monkeypatch):
    def blocked(*_args, **_kwargs):
        raise AssertionError("real external access is forbidden")

    monkeypatch.setattr(socket, "create_connection", blocked)


def _bare_researcher():
    researcher = GPTResearcher.__new__(GPTResearcher)
    researcher._paper_candidate_collector = None
    researcher._paper_candidate_collector_owner = False
    researcher._paper_candidate_collector_borrower = False
    researcher._paper_candidate_run_active = False
    researcher._paper_screening_audit_collector = None
    researcher._paper_screening_audit_run_ordinal = 0
    researcher.report_source = ReportSource.Web.value
    researcher.report_type = ReportType.ResearchReport.value
    return researcher


def test_getter_unavailable_message_and_finalized_identity():
    researcher = _bare_researcher()
    with pytest.raises(RuntimeError, match=AUDIT_UNAVAILABLE_MESSAGE):
        researcher.get_paper_screening_audit()

    collector = PaperScreeningAuditCollector(
        run_ordinal=1, policy=ScreeningPolicy(), topic_relevance_enabled=False
    )
    researcher._paper_screening_audit_collector = collector
    with pytest.raises(RuntimeError, match=AUDIT_UNAVAILABLE_MESSAGE):
        researcher.get_paper_screening_audit()


@pytest.mark.asyncio
async def test_conduct_clears_old_audit_after_guard_before_binding_failure():
    researcher = _bare_researcher()
    old = object()
    researcher._paper_screening_audit_collector = old
    observed = []

    class Conductor:
        def _bind_paper_screening_for_run(self):
            observed.append(researcher._paper_screening_audit_collector)
            raise ValueError("binding failed")

    researcher.research_conductor = Conductor()
    researcher._conduct_research_impl = lambda *_args: None
    with pytest.raises(ValueError, match="binding failed"):
        await researcher.conduct_research()
    assert observed == [None]
    assert researcher._paper_candidate_run_active is False
    assert researcher._paper_candidate_collector.state is CollectorState.ABORTED
    with pytest.raises(RuntimeError, match=AUDIT_UNAVAILABLE_MESSAGE):
        researcher.get_paper_screening_audit()


@pytest.mark.asyncio
async def test_quick_search_does_not_clear_existing_audit(monkeypatch):
    researcher = _bare_researcher()
    old = object()
    researcher._paper_screening_audit_collector = old
    researcher._paper_screening_audit_run_ordinal = 7

    async def quick(*_args, **_kwargs):
        return "quick"

    researcher._quick_search_impl = quick
    assert await researcher.quick_search("query") == "quick"
    assert researcher._paper_screening_audit_collector is old
    assert researcher._paper_screening_audit_run_ordinal == 7
    assert researcher._paper_candidate_collector.state is CollectorState.FINALIZED


@pytest.mark.asyncio
async def test_borrower_never_clears_or_creates_audit():
    researcher = _bare_researcher()
    researcher._paper_candidate_collector_borrower = True
    existing = object()
    researcher._paper_screening_audit_collector = existing
    researcher.research_conductor = SimpleNamespace(
        _bind_paper_screening_for_run=lambda: None
    )

    async def conduct(*_args):
        return "borrowed"

    researcher._conduct_research_impl = conduct
    assert await researcher.conduct_research() == "borrowed"
    assert researcher._paper_screening_audit_collector is existing


@pytest.mark.asyncio
async def test_overlap_fails_before_audit_is_replaced():
    researcher = _bare_researcher()
    old = object()
    researcher._paper_screening_audit_collector = old
    researcher._paper_candidate_run_active = True
    with pytest.raises(RuntimeError, match="already active"):
        await researcher.conduct_research()
    assert researcher._paper_screening_audit_collector is old


def _eligible_conductor(researcher):
    class Conductor:
        _paper_screening_policy = ScreeningPolicy()
        _paper_topic_relevance_enabled = False

        def _bind_paper_screening_for_run(self):
            return None

        def _should_use_paper_screening(self):
            return True

    researcher.research_conductor = Conductor()


def _add_empty_pass(researcher):
    collector = researcher._paper_screening_audit_collector
    pass_ref = collector.allocate_web_pass()
    collector.add_pass(
        build_paper_screening_web_pass_audit(
            web_pass_order=pass_ref.web_pass_order,
            web_pass_id=pass_ref.web_pass_id,
            policy=ScreeningPolicy(),
            topic_relevance_enabled=False,
            deterministic_result=screen_paper_occurrences((), ScreeningPolicy()),
            topic_result=None,
            request_metadata=(),
        )
    )


async def _run_context_conversion_failure(monkeypatch, audit_collector):
    created_workspaces = []

    class TrackingWorkspace(ScreeningWorkspace):
        def __init__(self, policy):
            super().__init__(policy)
            created_workspaces.append(self)

    researcher = SimpleNamespace(
        _paper_screening_audit_collector=audit_collector,
        cfg=SimpleNamespace(),
        add_costs=lambda *_args, **_kwargs: None,
    )
    conductor = ResearchConductor(researcher)
    conductor._paper_screening_policy = ScreeningPolicy()
    conductor._paper_topic_relevance_enabled = False

    async def no_mcp(_query):
        return None

    async def plan(_query, _workspace, _domains):
        return []

    async def prepare(request_id, query, _workspace, *_args):
        return PreparedEvidenceRequest(request_id, query, (), (), (), ())

    async def consume(*_args, **_kwargs):
        return object()

    monkeypatch.setattr(
        "gpt_researcher.skills.researcher.ScreeningWorkspace",
        TrackingWorkspace,
    )
    monkeypatch.setattr(conductor, "_prepare_screening_mcp_cache", no_mcp)
    monkeypatch.setattr(conductor, "_plan_screened_research", plan)
    monkeypatch.setattr(conductor, "_prepare_evidence_request", prepare)
    monkeypatch.setattr(conductor, "_consume_prepared_evidence", consume)

    with pytest.raises(TypeError) as raised:
        await conductor._get_context_by_screened_web_search("query", [], [])
    return raised.value, created_workspaces[0]


@pytest.mark.asyncio
async def test_without_audit_context_conversion_failure_keeps_legacy_finalize_order(
    monkeypatch,
):
    _error, workspace = await _run_context_conversion_failure(monkeypatch, None)
    assert workspace.state is WorkspaceState.FINALIZED


@pytest.mark.asyncio
async def test_with_audit_context_conversion_failure_aborts_before_pass_publication(
    monkeypatch,
):
    collector = PaperScreeningAuditCollector(
        run_ordinal=1,
        policy=ScreeningPolicy(),
        topic_relevance_enabled=False,
    )
    _error, workspace = await _run_context_conversion_failure(monkeypatch, collector)
    assert workspace.state is WorkspaceState.ABORTED
    assert collector.state is AuditCollectorState.OPEN
    with pytest.raises(RuntimeError, match=AUDIT_UNAVAILABLE_MESSAGE):
        collector.snapshot()


def _install_screened_pass_impl(
    monkeypatch,
    researcher,
    *,
    workspace_type=ScreeningWorkspace,
):
    created_workspaces = []

    class TrackingWorkspace(workspace_type):
        def __init__(self, policy):
            super().__init__(policy)
            created_workspaces.append(self)

    pipeline = ResearchConductor(researcher)
    pipeline._paper_screening_policy = ScreeningPolicy()
    pipeline._paper_topic_relevance_enabled = False

    async def no_mcp(_query):
        return None

    async def plan(_query, _workspace, _domains):
        return []

    async def prepare(request_id, query, _workspace, *_args):
        return PreparedEvidenceRequest(request_id, query, (), (), (), ())

    async def consume(*_args, **_kwargs):
        return "context"

    monkeypatch.setattr(
        "gpt_researcher.skills.researcher.ScreeningWorkspace",
        TrackingWorkspace,
    )
    monkeypatch.setattr(pipeline, "_prepare_screening_mcp_cache", no_mcp)
    monkeypatch.setattr(pipeline, "_plan_screened_research", plan)
    monkeypatch.setattr(pipeline, "_prepare_evidence_request", prepare)
    monkeypatch.setattr(pipeline, "_consume_prepared_evidence", consume)

    async def conduct(*_args):
        return await pipeline._get_context_by_screened_web_search("query", [], [])

    researcher._conduct_research_impl = conduct
    _eligible_conductor(researcher)
    return created_workspaces


@pytest.mark.asyncio
async def test_workspace_finalize_failure_aborts_owner_without_partial_snapshots(
    monkeypatch,
):
    failure = RuntimeError("workspace finalize sentinel")
    add_calls = []
    original_add = PaperScreeningAuditCollector.add_pass

    class FailingFinalizeWorkspace(ScreeningWorkspace):
        def finalize(self):
            raise failure

    def record_add(self, pass_audit):
        add_calls.append(pass_audit)
        return original_add(self, pass_audit)

    monkeypatch.setattr(PaperScreeningAuditCollector, "add_pass", record_add)
    researcher = _bare_researcher()
    workspaces = _install_screened_pass_impl(
        monkeypatch,
        researcher,
        workspace_type=FailingFinalizeWorkspace,
    )
    with pytest.raises(RuntimeError) as raised:
        await researcher.conduct_research()
    assert raised.value is failure
    assert len(add_calls) == 1
    assert workspaces[0].state is WorkspaceState.ABORTED
    assert researcher._paper_candidate_collector.state is CollectorState.ABORTED
    assert researcher._paper_screening_audit_collector.state is AuditCollectorState.ABORTED
    assert researcher._paper_candidate_run_active is False
    with pytest.raises(RuntimeError, match=AUDIT_UNAVAILABLE_MESSAGE):
        researcher.get_paper_screening_audit()
    with pytest.raises(RuntimeError, match="only after finalization"):
        researcher.get_paper_candidates()


@pytest.mark.asyncio
async def test_pass_builder_failure_skips_add_and_aborts_workspace_and_owner(
    monkeypatch,
):
    failure = RuntimeError("pass builder sentinel")
    add_calls = []

    def fail_builder(**_kwargs):
        raise failure

    def record_add(self, pass_audit):
        add_calls.append((self, pass_audit))

    monkeypatch.setattr(
        "gpt_researcher.skills.researcher.build_paper_screening_web_pass_audit",
        fail_builder,
    )
    monkeypatch.setattr(PaperScreeningAuditCollector, "add_pass", record_add)
    researcher = _bare_researcher()
    workspaces = _install_screened_pass_impl(monkeypatch, researcher)
    with pytest.raises(RuntimeError) as raised:
        await researcher.conduct_research()
    assert raised.value is failure
    assert add_calls == []
    assert workspaces[0].state is WorkspaceState.ABORTED
    assert researcher._paper_candidate_collector.state is CollectorState.ABORTED
    assert researcher._paper_screening_audit_collector.state is AuditCollectorState.ABORTED
    assert researcher._paper_candidate_run_active is False


@pytest.mark.asyncio
async def test_add_pass_failure_aborts_before_workspace_finalize_or_publication(
    monkeypatch,
):
    failure = RuntimeError("add pass sentinel")
    prepare_calls = []
    commit_calls = []

    def fail_add(_self, _pass_audit):
        raise failure

    def record_prepare(_self):
        prepare_calls.append(True)

    def record_commit(_self):
        commit_calls.append(True)

    monkeypatch.setattr(PaperScreeningAuditCollector, "add_pass", fail_add)
    monkeypatch.setattr(PaperScreeningAuditCollector, "prepare", record_prepare)
    monkeypatch.setattr(PaperScreeningAuditCollector, "commit", record_commit)
    researcher = _bare_researcher()
    workspaces = _install_screened_pass_impl(monkeypatch, researcher)
    with pytest.raises(RuntimeError) as raised:
        await researcher.conduct_research()
    assert raised.value is failure
    assert workspaces[0].state is WorkspaceState.ABORTED
    assert prepare_calls == []
    assert commit_calls == []
    assert researcher._paper_screening_audit_collector.state is AuditCollectorState.ABORTED


@pytest.mark.asyncio
async def test_audit_prepare_failure_aborts_both_before_candidate_finalize(
    monkeypatch,
):
    failure = RuntimeError("audit prepare sentinel")
    candidate_finalize_calls = []

    def fail_prepare(_self):
        raise failure

    def record_candidate_finalize(_self):
        candidate_finalize_calls.append(True)

    monkeypatch.setattr(PaperScreeningAuditCollector, "prepare", fail_prepare)
    monkeypatch.setattr(PaperCandidateCollector, "finalize", record_candidate_finalize)
    researcher = _bare_researcher()
    _eligible_conductor(researcher)

    async def conduct(*_args):
        _add_empty_pass(researcher)
        return "context"

    researcher._conduct_research_impl = conduct
    with pytest.raises(RuntimeError) as raised:
        await researcher.conduct_research()
    assert raised.value is failure
    assert candidate_finalize_calls == []
    assert researcher._paper_candidate_collector.state is CollectorState.ABORTED
    assert researcher._paper_screening_audit_collector.state is AuditCollectorState.ABORTED
    assert researcher._paper_candidate_run_active is False
    with pytest.raises(RuntimeError, match="only after finalization"):
        researcher.get_paper_candidates()
    with pytest.raises(RuntimeError, match=AUDIT_UNAVAILABLE_MESSAGE):
        researcher.get_paper_screening_audit()


@pytest.mark.asyncio
async def test_dual_cleanup_failure_preserves_original_and_attempts_both(
    monkeypatch,
    caplog,
):
    original = RuntimeError("PRIMARY_BUSINESS_SENTINEL")
    cleanup_secret = "SYNTHETIC_CLEANUP_SECRET"
    abort_calls = []

    def fail_audit_abort(_self):
        abort_calls.append("audit")
        raise RuntimeError(cleanup_secret)

    def fail_candidate_abort(_self):
        abort_calls.append("candidate")
        raise RuntimeError(cleanup_secret)

    monkeypatch.setattr(PaperScreeningAuditCollector, "abort", fail_audit_abort)
    monkeypatch.setattr(PaperCandidateCollector, "abort", fail_candidate_abort)
    researcher = _bare_researcher()
    _eligible_conductor(researcher)

    async def conduct(*_args):
        raise original

    researcher._conduct_research_impl = conduct
    with pytest.raises(RuntimeError) as raised:
        await researcher.conduct_research()
    assert raised.value is original
    assert abort_calls == ["audit", "candidate"]
    assert researcher._paper_candidate_run_active is False
    assert cleanup_secret not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("gate", ("disabled", "capability"))
async def test_owner_without_active_screening_hides_old_audit_without_replacement(
    monkeypatch,
    gate,
):
    researcher = _bare_researcher()
    old = PaperScreeningAuditCollector(
        run_ordinal=4,
        policy=ScreeningPolicy(),
        topic_relevance_enabled=False,
    )
    old.prepare()
    old.commit()
    researcher._paper_screening_audit_collector = old
    researcher._paper_screening_audit_run_ordinal = 4
    builder_calls = []

    class InactiveConductor:
        def _bind_paper_screening_for_run(self):
            return None

        def _should_use_paper_screening(self):
            builder_calls.append(gate)
            return False

    researcher.research_conductor = InactiveConductor()

    async def conduct(*_args):
        return "legacy"

    researcher._conduct_research_impl = conduct
    assert await researcher.conduct_research() == "legacy"
    assert builder_calls == [gate]
    assert researcher._paper_screening_audit_collector is None
    assert researcher._paper_screening_audit_run_ordinal == 4
    assert researcher._paper_candidate_collector.state is CollectorState.FINALIZED
    with pytest.raises(RuntimeError, match=AUDIT_UNAVAILABLE_MESSAGE):
        researcher.get_paper_screening_audit()


@pytest.mark.asyncio
async def test_detailed_style_web_research_borrower_never_receives_owner_audit():
    owner = _bare_researcher()
    _eligible_conductor(owner)

    async def owner_conduct(*_args):
        _add_empty_pass(owner)
        return "owner"

    owner._conduct_research_impl = owner_conduct
    assert await owner.conduct_research() == "owner"
    owner_snapshot = owner.get_paper_screening_audit()

    borrower = _bare_researcher()
    borrower._bind_paper_candidate_collector(
        owner._paper_candidate_collector,
        owner=False,
    )
    borrower.research_conductor = SimpleNamespace(
        _bind_paper_screening_for_run=lambda: None
    )

    async def borrower_conduct(*_args):
        return "borrower"

    borrower._conduct_research_impl = borrower_conduct
    assert await borrower.conduct_research() == "borrower"
    assert owner.get_paper_screening_audit() is owner_snapshot
    assert borrower._paper_screening_audit_collector is None
    with pytest.raises(RuntimeError, match=AUDIT_UNAVAILABLE_MESSAGE):
        borrower.get_paper_screening_audit()
    borrower._paper_candidate_run_active = True
    with pytest.raises(RuntimeError, match="borrower"):
        borrower._finalize_paper_candidate_run()
    with pytest.raises(RuntimeError, match="borrower"):
        borrower._abort_paper_candidate_run()
    borrower._paper_candidate_run_active = False


@pytest.mark.asyncio
async def test_concurrent_researchers_keep_audit_state_isolated():
    successful = _bare_researcher()
    failing = _bare_researcher()
    _eligible_conductor(successful)
    _eligible_conductor(failing)
    failure = RuntimeError("isolated failure")

    async def succeed(*_args):
        await asyncio.sleep(0)
        _add_empty_pass(successful)
        return "success"

    async def fail(*_args):
        _add_empty_pass(failing)
        await asyncio.sleep(0)
        raise failure

    successful._conduct_research_impl = succeed
    failing._conduct_research_impl = fail
    results = await asyncio.gather(
        successful.conduct_research(),
        failing.conduct_research(),
        return_exceptions=True,
    )
    assert results == ["success", failure]
    assert successful._paper_screening_audit_collector is not failing._paper_screening_audit_collector
    assert successful.get_paper_screening_audit().run_ordinal == 1
    assert failing._paper_screening_audit_collector.state is AuditCollectorState.ABORTED
    with pytest.raises(RuntimeError, match=AUDIT_UNAVAILABLE_MESSAGE):
        failing.get_paper_screening_audit()


@pytest.mark.asyncio
async def test_successful_owner_publishes_candidate_and_audit_without_await_gap():
    researcher = _bare_researcher()
    _eligible_conductor(researcher)

    async def conduct(*_args):
        _add_empty_pass(researcher)
        return "context"

    researcher._conduct_research_impl = conduct
    assert await researcher.conduct_research() == "context"
    snapshot = researcher.get_paper_screening_audit()
    assert snapshot.run_ordinal == 1
    assert snapshot.web_passes[0].web_pass_id == "web-pass:000001"
    assert researcher._paper_candidate_collector.state is CollectorState.FINALIZED
    assert researcher._paper_screening_audit_collector.state is AuditCollectorState.FINALIZED
    assert researcher._paper_candidate_run_active is False


@pytest.mark.asyncio
async def test_candidate_finalize_failure_aborts_prepared_audit_and_allows_next_run(
    monkeypatch,
):
    researcher = _bare_researcher()
    _eligible_conductor(researcher)
    failure = RuntimeError("candidate finalize failed")
    fail_once = True
    commit_calls = []
    original_commit = PaperScreeningAuditCollector.commit

    def record_commit(collector):
        commit_calls.append(collector)
        return original_commit(collector)

    monkeypatch.setattr(PaperScreeningAuditCollector, "commit", record_commit)

    async def conduct(*_args):
        nonlocal fail_once
        _add_empty_pass(researcher)
        if fail_once:
            fail_once = False

            def fail_finalize():
                raise failure

            researcher._paper_candidate_collector.finalize = fail_finalize
        return "context"

    researcher._conduct_research_impl = conduct
    with pytest.raises(RuntimeError) as raised:
        await researcher.conduct_research()
    assert raised.value is failure
    assert commit_calls == []
    assert researcher._paper_screening_audit_collector.state is AuditCollectorState.ABORTED
    assert researcher._paper_candidate_run_active is False
    with pytest.raises(RuntimeError, match=AUDIT_UNAVAILABLE_MESSAGE):
        researcher.get_paper_screening_audit()

    assert await researcher.conduct_research() == "context"
    assert len(commit_calls) == 1
    assert researcher.get_paper_screening_audit().run_ordinal == 2


@pytest.mark.asyncio
async def test_cancelled_owner_aborts_both_collectors_and_preserves_object():
    researcher = _bare_researcher()
    _eligible_conductor(researcher)
    cancelled = asyncio.CancelledError()

    async def conduct(*_args):
        _add_empty_pass(researcher)
        raise cancelled

    researcher._conduct_research_impl = conduct
    with pytest.raises(asyncio.CancelledError) as raised:
        await researcher.conduct_research()
    assert raised.value is cancelled
    assert researcher._paper_candidate_collector.state is CollectorState.ABORTED
    assert researcher._paper_screening_audit_collector.state is AuditCollectorState.ABORTED
    assert researcher._paper_candidate_run_active is False
