import asyncio
import socket
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import gpt_researcher.actions.query_processing as query_processing
from gpt_researcher.agent import GPTResearcher
from gpt_researcher.screening.models import PaperCandidate


execute_retriever_search = getattr(
    query_processing, "execute_retriever_search", None
)
requires_execution = pytest.mark.skipif(
    execute_retriever_search is None,
    reason="Milestone 2.1 unified Retriever execution is not implemented yet",
)


@pytest.fixture(autouse=True)
def _isolated_environment_and_no_network(monkeypatch):
    for name in (
        "ARXIV_API_KEY",
        "SEMANTIC_SCHOLAR_API_KEY",
        "SEMANTIC_SCHOLAR_JOURNALS",
        "RETRIEVER",
        "RETRIEVERS",
        "OPENAI_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    def blocked_network(*_args, **_kwargs):
        raise AssertionError("real network access is forbidden in Milestone 2.1 tests")

    monkeypatch.setattr(socket, "create_connection", blocked_network)


def _candidate(
    *,
    candidate_id="arxiv:2401.00001",
    source="arxiv",
    retrieval_query="query",
    source_rank=1,
    title="Paper",
    href="https://example.test/paper",
    body="  frozen academic body  ",
):
    return PaperCandidate(
        candidate_id=candidate_id,
        source=source,
        source_record_id="record",
        retrieval_query=retrieval_query,
        source_rank=source_rank,
        title=title,
        href=href,
        body=body,
        abstract="Abstract",
        authors=(),
        published_year=None,
        published_at=None,
        updated_at=None,
        venue=None,
        publication_venue_id=None,
        publication_venue_name=None,
        publication_venue_type=None,
        publication_venue_alternate_names=(),
        doi=None,
        external_ids=(),
        citation_count=None,
        publication_types=(),
        categories=(),
        journal_reference=None,
    )


def test_milestone_2_1_unified_execution_is_exported():
    assert execute_retriever_search is not None


@requires_execution
@pytest.mark.asyncio
async def test_candidate_generator_is_consumed_once_in_worker_and_projected():
    from gpt_researcher.screening.collection import PaperCandidateCollector

    candidate = _candidate()
    iterations = 0
    worker_threads = []

    class CandidateRetriever:
        def search_candidates(self, max_results=5):
            nonlocal iterations
            worker_threads.append(threading.get_ident())

            def values():
                nonlocal iterations
                iterations += 1
                yield candidate

            return values()

        def search(self, max_results=5):
            raise AssertionError("search() must not be called")

    collector = PaperCandidateCollector()
    event_loop_thread = threading.get_ident()
    results = await execute_retriever_search(
        CandidateRetriever(), max_results=7, collector=collector
    )
    collector.finalize()

    assert iterations == 1
    assert worker_threads and worker_threads[0] != event_loop_thread
    assert collector.snapshot() == (candidate,)
    assert results == [candidate.to_retriever_result()]
    assert set(results[0]) == {"title", "href", "body"}


@requires_execution
@pytest.mark.asyncio
async def test_candidate_iterable_validation_is_atomic():
    from gpt_researcher.screening.collection import PaperCandidateCollector

    class BadCandidateRetriever:
        def search_candidates(self):
            yield _candidate()
            yield object()

    collector = PaperCandidateCollector()
    with pytest.raises(TypeError):
        await execute_retriever_search(BadCandidateRetriever(), collector=collector)

    collector.finalize()
    assert collector.snapshot() == ()


@requires_execution
@pytest.mark.asyncio
async def test_explicit_and_omitted_max_results_semantics():
    calls = []

    class OrdinaryRetriever:
        def search(self, *args, **kwargs):
            calls.append((args, kwargs))
            return [{"title": "Web", "href": "https://web.test", "body": "snippet"}]

    first = await execute_retriever_search(OrdinaryRetriever())
    second = await execute_retriever_search(OrdinaryRetriever(), max_results=3)

    assert calls == [((), {}), ((), {"max_results": 3})]
    assert first == second


@requires_execution
@pytest.mark.asyncio
async def test_mcp_style_object_graph_does_not_call_collector_in_worker():
    from gpt_researcher.screening.collection import PaperCandidateCollector

    collector = PaperCandidateCollector()
    event_thread = threading.get_ident()
    calls = []
    worker_threads = []
    original_add = collector.add_batch
    original_finalize = collector.finalize

    def recording_add(batch):
        calls.append(("add_batch", threading.get_ident()))
        return original_add(batch)

    def recording_finalize():
        calls.append(("finalize", threading.get_ident()))
        return original_finalize()

    collector.add_batch = recording_add
    collector.finalize = recording_finalize

    class MCPStyleCandidateRetriever:
        def __init__(self):
            self.researcher = SimpleNamespace(_paper_candidate_collector=collector)

        def search_candidates(self):
            worker_threads.append(threading.get_ident())
            return (_candidate(),)

    retriever = MCPStyleCandidateRetriever()
    assert retriever.researcher._paper_candidate_collector is collector
    await execute_retriever_search(retriever, collector=collector)
    collector.finalize()

    aborted = PaperCandidateCollector()
    original_abort = aborted.abort

    def recording_abort():
        calls.append(("abort", threading.get_ident()))
        return original_abort()

    aborted.abort = recording_abort
    aborted.abort()

    assert worker_threads and worker_threads[0] != event_thread
    assert calls == [
        ("add_batch", event_thread),
        ("finalize", event_thread),
        ("abort", event_thread),
    ]


@requires_execution
@pytest.mark.asyncio
async def test_cancelled_worker_cannot_late_write():
    from gpt_researcher.screening.collection import PaperCandidateCollector

    collector = PaperCandidateCollector()
    started = threading.Event()
    release = threading.Event()

    class SlowRetriever:
        def search_candidates(self):
            started.set()
            release.wait(timeout=5)
            return (_candidate(),)

    task = asyncio.create_task(
        execute_retriever_search(SlowRetriever(), collector=collector)
    )
    await asyncio.to_thread(started.wait, 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    collector.abort()
    release.set()
    await asyncio.sleep(0.05)

    with pytest.raises(RuntimeError):
        collector.snapshot()


@requires_execution
@pytest.mark.asyncio
async def test_get_search_results_collects_without_second_provider_request():
    from gpt_researcher.screening.collection import PaperCandidateCollector

    candidate = _candidate()
    calls = []

    class AcademicRetriever:
        def __init__(self, query, query_domains=None):
            self.query = query

        def search_candidates(self, max_results=5):
            calls.append(max_results)
            return (candidate,)

        def search(self, max_results=5):
            raise AssertionError("search() would cause a second request")

    collector = PaperCandidateCollector()
    researcher = SimpleNamespace(_paper_candidate_collector=collector)
    results = await query_processing.get_search_results(
        "query", AcademicRetriever, researcher=researcher, max_results=4
    )

    assert calls == [4]
    assert results == [candidate.to_retriever_result()]
    collector.finalize()
    assert collector.snapshot() == (candidate,)


@requires_execution
@pytest.mark.asyncio
async def test_existing_academic_provider_types_use_generic_candidate_capability(
    monkeypatch,
):
    from gpt_researcher.retrievers.arxiv.arxiv import ArxivSearch
    from gpt_researcher.retrievers.semantic_scholar.semantic_scholar import (
        SemanticScholarSearch,
    )
    from gpt_researcher.screening.collection import PaperCandidateCollector

    providers = (
        (
            ArxivSearch,
            _candidate(candidate_id="arxiv:provider", source="arxiv"),
        ),
        (
            SemanticScholarSearch,
            _candidate(
                candidate_id="semantic_scholar:provider",
                source="semantic_scholar",
            ),
        ),
    )

    for retriever_type, candidate in providers:
        retriever = retriever_type.__new__(retriever_type)
        monkeypatch.setattr(
            retriever,
            "search_candidates",
            lambda *, max_results=5, item=candidate: (item,),
        )
        monkeypatch.setattr(
            retriever,
            "search",
            lambda **_kwargs: (_ for _ in ()).throw(
                AssertionError("search() must not be called")
            ),
        )
        collector = PaperCandidateCollector()
        assert await execute_retriever_search(
            retriever,
            max_results=1,
            collector=collector,
        ) == [candidate.to_retriever_result()]
        collector.finalize()
        assert collector.snapshot() == (candidate,)


@requires_execution
@pytest.mark.asyncio
async def test_quick_search_owner_finalizes_and_preserves_internal_duplicates(monkeypatch):
    candidate = _candidate()
    projected = candidate.to_retriever_result()
    researcher = GPTResearcher.__new__(GPTResearcher)
    researcher.retrievers = [type("R1", (), {}), type("R2", (), {})]
    researcher.cfg = SimpleNamespace(
        smart_llm_model="model",
        smart_llm_provider="provider",
        smart_token_limit=100,
        llm_kwargs={},
    )
    researcher.prompt_family = SimpleNamespace()
    researcher.add_costs = MagicMock()
    researcher._paper_candidate_collector = None
    researcher._paper_candidate_collector_owner = False
    researcher._paper_candidate_run_active = False

    async def fake_get_results(_query, _retriever, **_kwargs):
        researcher._paper_candidate_collector.add_batch((candidate,))
        return [dict(projected)]

    monkeypatch.setattr("gpt_researcher.agent.get_search_results", fake_get_results)
    results = await researcher.quick_search("query", all_retrievers=True)

    assert results == [projected]
    assert researcher.get_paper_candidates() == (candidate, candidate)


@requires_execution
@pytest.mark.asyncio
async def test_quick_search_failure_aborts_and_overlap_fails_fast(monkeypatch):
    researcher = GPTResearcher.__new__(GPTResearcher)
    researcher.retrievers = [type("Retriever", (), {})]
    researcher._paper_candidate_collector = None
    researcher._paper_candidate_collector_owner = False
    researcher._paper_candidate_run_active = False
    entered = asyncio.Event()
    release = asyncio.Event()

    async def blocked(*_args, **_kwargs):
        entered.set()
        await release.wait()
        raise RuntimeError("expected failure")

    monkeypatch.setattr("gpt_researcher.agent.get_search_results", blocked)
    first = asyncio.create_task(researcher.quick_search("query"))
    await entered.wait()
    with pytest.raises(RuntimeError, match="already active"):
        await researcher.quick_search("overlap")
    release.set()
    with pytest.raises(RuntimeError, match="expected failure"):
        await first
    with pytest.raises(RuntimeError):
        researcher.get_paper_candidates()


@requires_execution
@pytest.mark.asyncio
async def test_conduct_research_owner_isolates_consecutive_runs_and_aborts_cancel():
    researcher = GPTResearcher.__new__(GPTResearcher)
    researcher._paper_candidate_collector = None
    researcher._paper_candidate_collector_owner = False
    researcher._paper_candidate_collector_borrower = False
    researcher._paper_candidate_run_active = False
    run_number = 0

    async def successful_run(_on_progress):
        nonlocal run_number
        run_number += 1
        researcher._paper_candidate_collector.add_batch(
            (
                _candidate(
                    candidate_id=f"arxiv:run-{run_number}",
                    source_rank=run_number,
                ),
            )
        )
        return [f"context-{run_number}"]

    researcher._conduct_research_impl = successful_run
    assert await researcher.conduct_research() == ["context-1"]
    assert tuple(item.candidate_id for item in researcher.get_paper_candidates()) == (
        "arxiv:run-1",
    )

    assert await researcher.conduct_research() == ["context-2"]
    assert tuple(item.candidate_id for item in researcher.get_paper_candidates()) == (
        "arxiv:run-2",
    )

    async def cancelled_run(_on_progress):
        researcher._paper_candidate_collector.add_batch((_candidate(),))
        raise asyncio.CancelledError

    researcher._conduct_research_impl = cancelled_run
    with pytest.raises(asyncio.CancelledError):
        await researcher.conduct_research()
    assert researcher._paper_candidate_run_active is False
    with pytest.raises(RuntimeError):
        researcher.get_paper_candidates()


@requires_execution
@pytest.mark.asyncio
@pytest.mark.parametrize("error_type", [RuntimeError, asyncio.CancelledError])
async def test_conduct_research_finalize_failure_aborts_and_allows_next_run(
    monkeypatch,
    error_type,
):
    from gpt_researcher.screening.collection import (
        CollectorState,
        PaperCandidateCollector,
    )

    researcher = GPTResearcher.__new__(GPTResearcher)
    researcher._paper_candidate_collector = None
    researcher._paper_candidate_collector_owner = False
    researcher._paper_candidate_collector_borrower = False
    researcher._paper_candidate_run_active = False

    async def successful_impl(_on_progress):
        researcher._paper_candidate_collector.add_batch((_candidate(),))
        return ["context"]

    researcher._conduct_research_impl = successful_impl
    original_finalize = PaperCandidateCollector.finalize
    original_error = error_type("stable candidate serialization failed")

    def failing_finalize(_collector):
        raise original_error

    monkeypatch.setattr(PaperCandidateCollector, "finalize", failing_finalize)
    with pytest.raises(error_type) as raised:
        await researcher.conduct_research()

    assert raised.value is original_error
    assert researcher._paper_candidate_collector.state is CollectorState.ABORTED
    assert researcher._paper_candidate_run_active is False
    with pytest.raises(RuntimeError):
        researcher.get_paper_candidates()

    monkeypatch.setattr(PaperCandidateCollector, "finalize", original_finalize)
    assert await researcher.conduct_research() == ["context"]
    assert researcher.get_paper_candidates() == (_candidate(),)


@requires_execution
@pytest.mark.asyncio
async def test_independent_top_level_researchers_do_not_share_candidates():
    first = GPTResearcher.__new__(GPTResearcher)
    second = GPTResearcher.__new__(GPTResearcher)
    for researcher in (first, second):
        researcher._paper_candidate_collector = None
        researcher._paper_candidate_collector_owner = False
        researcher._paper_candidate_collector_borrower = False
        researcher._paper_candidate_run_active = False

    async def first_run(_on_progress):
        first._paper_candidate_collector.add_batch(
            (_candidate(candidate_id="arxiv:first"),)
        )
        await asyncio.sleep(0)
        return []

    async def second_run(_on_progress):
        second._paper_candidate_collector.add_batch(
            (_candidate(candidate_id="arxiv:second"),)
        )
        await asyncio.sleep(0)
        return []

    first._conduct_research_impl = first_run
    second._conduct_research_impl = second_run
    await asyncio.gather(first.conduct_research(), second.conduct_research())

    assert tuple(item.candidate_id for item in first.get_paper_candidates()) == (
        "arxiv:first",
    )
    assert tuple(item.candidate_id for item in second.get_paper_candidates()) == (
        "arxiv:second",
    )


@requires_execution
@pytest.mark.asyncio
async def test_quick_search_single_mode_collects_and_recoverable_peer_failure_survives(
    monkeypatch,
):
    candidate = _candidate(candidate_id="arxiv:quick-single")
    projected = candidate.to_retriever_result()
    successful = type("SuccessfulRetriever", (), {})
    failing = type("FailingRetriever", (), {})
    researcher = GPTResearcher.__new__(GPTResearcher)
    researcher.retrievers = [successful]
    researcher._paper_candidate_collector = None
    researcher._paper_candidate_collector_owner = False
    researcher._paper_candidate_collector_borrower = False
    researcher._paper_candidate_run_active = False

    async def fake_get_results(_query, retriever, **_kwargs):
        if retriever is failing:
            raise RuntimeError("recoverable provider failure")
        researcher._paper_candidate_collector.add_batch((candidate,))
        return [dict(projected)]

    monkeypatch.setattr("gpt_researcher.agent.get_search_results", fake_get_results)
    assert await researcher.quick_search("query") == [projected]
    assert researcher.get_paper_candidates() == (candidate,)

    researcher.retrievers = [failing, successful]
    assert await researcher.quick_search("query", all_retrievers=True) == [projected]
    assert researcher.get_paper_candidates() == (candidate,)


@requires_execution
@pytest.mark.asyncio
@pytest.mark.parametrize("error_type", [RuntimeError, asyncio.CancelledError])
async def test_quick_search_finalize_failure_aborts_and_allows_next_run(
    monkeypatch,
    error_type,
):
    from gpt_researcher.screening.collection import (
        CollectorState,
        PaperCandidateCollector,
    )

    candidate = _candidate(candidate_id="arxiv:quick-finalize")
    projected = candidate.to_retriever_result()
    researcher = GPTResearcher.__new__(GPTResearcher)
    researcher._paper_candidate_collector = None
    researcher._paper_candidate_collector_owner = False
    researcher._paper_candidate_collector_borrower = False
    researcher._paper_candidate_run_active = False

    async def successful_impl(*_args, **_kwargs):
        researcher._paper_candidate_collector.add_batch((candidate,))
        return [projected]

    researcher._quick_search_impl = successful_impl
    original_finalize = PaperCandidateCollector.finalize
    original_error = error_type("stable candidate serialization failed")

    def failing_finalize(_collector):
        raise original_error

    monkeypatch.setattr(PaperCandidateCollector, "finalize", failing_finalize)
    with pytest.raises(error_type) as raised:
        await researcher.quick_search("query")

    assert raised.value is original_error
    assert researcher._paper_candidate_collector.state is CollectorState.ABORTED
    assert researcher._paper_candidate_run_active is False
    with pytest.raises(RuntimeError):
        researcher.get_paper_candidates()

    monkeypatch.setattr(PaperCandidateCollector, "finalize", original_finalize)
    assert await researcher.quick_search("query") == [projected]
    assert researcher.get_paper_candidates() == (candidate,)


@requires_execution
@pytest.mark.asyncio
async def test_planning_stage_academic_request_is_collected(monkeypatch):
    from gpt_researcher.screening.collection import PaperCandidateCollector
    from gpt_researcher.skills.researcher import ResearchConductor

    candidate = _candidate(
        candidate_id="semantic_scholar:planning",
        source="semantic_scholar",
        retrieval_query="planning query",
    )

    class CompatibleAcademicRetriever:
        def __init__(self, query, query_domains=None):
            assert query == "planning query"

        def search_candidates(self, max_results=5):
            return (candidate,)

        def search(self, max_results=5):
            raise AssertionError("planning must use the candidate path")

    collector = PaperCandidateCollector()
    fake = SimpleNamespace(
        websocket=None,
        retrievers=[CompatibleAcademicRetriever],
        cfg=SimpleNamespace(max_search_results_per_query=5),
        role="role",
        parent_query="",
        report_type="research_report",
        add_costs=MagicMock(),
        kwargs={},
        _paper_candidate_collector=collector,
    )
    conductor = ResearchConductor.__new__(ResearchConductor)
    conductor.researcher = fake
    conductor.logger = MagicMock()
    monkeypatch.setattr(
        "gpt_researcher.skills.researcher.stream_output",
        AsyncMock(),
    )
    outline = AsyncMock(return_value=["sub-query"])
    monkeypatch.setattr(
        "gpt_researcher.skills.researcher.plan_research_outline",
        outline,
    )

    assert await conductor.plan_research("planning query") == ["sub-query"]
    collector.finalize()
    assert collector.snapshot() == (candidate,)
    assert set(outline.await_args.kwargs["search_results"][0]) == {
        "title",
        "href",
        "body",
    }


@requires_execution
@pytest.mark.asyncio
async def test_hybrid_passes_retain_duplicate_academic_candidates(monkeypatch):
    from gpt_researcher.screening.collection import PaperCandidateCollector
    from gpt_researcher.skills.researcher import ResearchConductor
    from gpt_researcher.utils.enum import ReportSource

    candidate = _candidate(candidate_id="arxiv:hybrid")
    collector = PaperCandidateCollector()

    class CompatibleAcademicRetriever:
        def search_candidates(self):
            return (candidate,)

    class FakeDocumentLoader:
        def __init__(self, _path):
            pass

        async def load(self):
            return []

    fake = SimpleNamespace(
        query="hybrid query",
        retrievers=[CompatibleAcademicRetriever],
        verbose=False,
        agent="agent",
        role="role",
        source_urls=[],
        complement_source_urls=False,
        report_source=ReportSource.Hybrid.value,
        document_urls=[],
        cfg=SimpleNamespace(doc_path="unused", curate_sources=False),
        vector_store=None,
        query_domains=None,
        prompt_family=SimpleNamespace(
            join_local_web_documents=lambda docs, web: [docs, web]
        ),
        source_curator=SimpleNamespace(),
        context=None,
        _paper_candidate_collector=collector,
    )
    conductor = ResearchConductor.__new__(ResearchConductor)
    conductor.researcher = fake
    conductor.logger = MagicMock()
    conductor.json_handler = None

    async def fake_context(*_args, **_kwargs):
        return await execute_retriever_search(
            CompatibleAcademicRetriever(),
            collector=collector,
        )

    conductor._get_context_by_web_search = fake_context
    monkeypatch.setattr(
        "gpt_researcher.skills.researcher.DocumentLoader",
        FakeDocumentLoader,
    )

    await conductor.conduct_research()
    collector.finalize()
    assert collector.snapshot() == (candidate, candidate)


@requires_execution
@pytest.mark.asyncio
async def test_research_conductor_uses_unified_execution_and_keeps_prefetched_body(monkeypatch):
    from gpt_researcher.screening.collection import PaperCandidateCollector
    from gpt_researcher.skills.researcher import ResearchConductor

    candidate = _candidate(body="  exact body  ")

    class AcademicRetriever:
        BODY_IS_PREFETCHED_CONTENT = True

        def __init__(self, query, query_domains=None):
            pass

        def search_candidates(self, max_results=5):
            return (candidate,)

        def search(self, max_results=5):
            raise AssertionError("direct search() must not be called")

    collector = PaperCandidateCollector()
    fake = SimpleNamespace(
        retrievers=[AcademicRetriever],
        cfg=SimpleNamespace(max_search_results_per_query=5),
        _paper_candidate_collector=collector,
        add_research_sources=MagicMock(),
        visited_urls=set(),
    )
    conductor = ResearchConductor.__new__(ResearchConductor)
    conductor.researcher = fake
    conductor.logger = MagicMock()
    conductor._get_new_urls = AsyncMock(side_effect=lambda urls: urls)

    urls, prefetched = await conductor._search_relevant_source_urls("query")

    assert urls == []
    assert prefetched == [{"url": candidate.href, "raw_content": candidate.body}]
    collector.finalize()
    assert collector.snapshot() == (candidate,)


@requires_execution
def test_private_binding_distinguishes_owner_and_borrower():
    from gpt_researcher.screening.collection import PaperCandidateCollector

    collector = PaperCandidateCollector()
    borrower = GPTResearcher.__new__(GPTResearcher)
    borrower._paper_candidate_collector = None
    borrower._paper_candidate_collector_owner = False
    borrower._paper_candidate_run_active = False
    borrower._bind_paper_candidate_collector(collector, owner=False)

    assert borrower._paper_candidate_collector is collector
    assert borrower._paper_candidate_collector_owner is False
    with pytest.raises(RuntimeError):
        borrower._begin_paper_candidate_run()


@requires_execution
@pytest.mark.asyncio
async def test_borrowed_researcher_adds_without_owner_state_transitions():
    from gpt_researcher.screening.collection import (
        CollectorState,
        PaperCandidateCollector,
    )

    candidate = _candidate(candidate_id="arxiv:borrowed")
    collector = PaperCandidateCollector()
    borrower = GPTResearcher.__new__(GPTResearcher)
    borrower._paper_candidate_collector = None
    borrower._paper_candidate_collector_owner = False
    borrower._paper_candidate_collector_borrower = False
    borrower._paper_candidate_run_active = False
    borrower._bind_paper_candidate_collector(collector, owner=False)

    async def borrowed_run(_on_progress):
        borrower._paper_candidate_collector.add_batch((candidate,))
        return ["context"]

    borrower._conduct_research_impl = borrowed_run
    assert await borrower.conduct_research() == ["context"]
    assert collector.state is CollectorState.OPEN
    with pytest.raises(RuntimeError):
        borrower.get_paper_candidates()

    collector.finalize()
    assert borrower.get_paper_candidates() == (candidate,)


@requires_execution
@pytest.mark.asyncio
async def test_deep_research_injects_same_borrowed_collector(monkeypatch):
    from gpt_researcher.screening.collection import PaperCandidateCollector
    from gpt_researcher.skills.deep_research import DeepResearchSkill

    collector = PaperCandidateCollector()
    parent = SimpleNamespace(
        cfg=SimpleNamespace(
            deep_research_breadth=1,
            deep_research_depth=1,
            deep_research_concurrency=1,
            config_path=None,
        ),
        websocket=None,
        tone=None,
        headers={},
        visited_urls=set(),
        mcp_configs=None,
        mcp_strategy="fast",
        _paper_candidate_collector=collector,
    )
    skill = DeepResearchSkill(parent)
    skill.generate_search_queries = AsyncMock(
        return_value=[{"query": "child", "researchGoal": "goal"}]
    )
    skill.process_research_results = AsyncMock(
        return_value={
            "learnings": [],
            "followUpQuestions": [],
            "citations": {},
        }
    )
    created = []

    class NestedResearcher:
        def __init__(self, **kwargs):
            created.append(kwargs)
            self.visited_urls = set()
            self.research_sources = []

        async def conduct_research(self):
            return []

    monkeypatch.setattr("gpt_researcher.GPTResearcher", NestedResearcher)
    await skill.deep_research("topic", breadth=1, depth=1)

    assert created[0]["_paper_candidate_collector"] is collector
    assert created[0]["_paper_candidate_collector_owner"] is False


@requires_execution
@pytest.mark.asyncio
async def test_detailed_report_subtopic_researcher_borrows_outer_collector(
    monkeypatch,
):
    import backend.report_type.detailed_report.detailed_report as detailed_module
    from backend.report_type.detailed_report.detailed_report import DetailedReport
    from gpt_researcher.screening.collection import PaperCandidateCollector

    collector = PaperCandidateCollector()
    candidate = _candidate(candidate_id="arxiv:detailed-subtopic")
    report = DetailedReport.__new__(DetailedReport)
    report._paper_candidate_collector = collector
    report.query_domains = []
    report.report_source = "web"
    report.websocket = None
    report.headers = {}
    report.query = "outer query"
    report.subtopics = []
    report.global_urls = set()
    report.tone = "objective"
    report.complement_source_urls = False
    report.source_urls = []
    report.max_search_results = None
    report.global_context = []
    report.global_written_sections = []
    report.existing_headers = []
    report.gpt_researcher = SimpleNamespace(
        agent="agent",
        role="role",
        mcp_configs=None,
        mcp_strategy="fast",
        extract_headers=lambda _value: [],
        extract_sections=lambda _value: [],
    )
    created = []

    class SubtopicResearcher:
        def __init__(self, **kwargs):
            created.append(kwargs)
            self._collector = kwargs["_paper_candidate_collector"]
            self.cfg = SimpleNamespace(max_search_results_per_query=5)
            self.context = []
            self.visited_urls = set()

        async def conduct_research(self):
            self._collector.add_batch((candidate,))

        async def get_draft_section_titles(self, _task):
            return ""

        async def get_similar_written_contents_by_draft_section_titles(
            self,
            _task,
            _titles,
            _written,
        ):
            return []

        async def write_report(self, **_kwargs):
            return "subtopic report"

    monkeypatch.setattr(detailed_module, "GPTResearcher", SubtopicResearcher)
    result = await report._get_subtopic_report({"task": "subtopic"})

    assert result["report"] == "subtopic report"
    assert created[0]["_paper_candidate_collector"] is collector
    assert created[0]["_paper_candidate_collector_owner"] is False
    collector.finalize()
    assert collector.snapshot() == (candidate,)


@requires_execution
@pytest.mark.asyncio
async def test_detailed_report_owner_rebinds_finalizes_and_isolates_runs(monkeypatch):
    from backend.report_type.detailed_report.detailed_report import DetailedReport
    from gpt_researcher.screening.collection import PaperCandidateCollector

    report = DetailedReport.__new__(DetailedReport)
    report._paper_candidate_collector = None
    report._paper_candidate_run_active = False
    report.global_urls = set()
    report._get_all_subtopics = AsyncMock(return_value=[])
    async def generate_subtopic_reports(_subtopics):
        report._paper_candidate_collector.add_batch(
            (_candidate(candidate_id="arxiv:subtopic", source_rank=2),)
        )
        return [], "body"

    report._generate_subtopic_reports = AsyncMock(
        side_effect=generate_subtopic_reports
    )
    report._construct_detailed_report = AsyncMock(return_value="report")

    class InitialResearcher:
        def __init__(self):
            self._paper_candidate_collector = None
            self.visited_urls = set()

        def _bind_paper_candidate_collector(self, collector, *, owner):
            assert owner is False
            self._paper_candidate_collector = collector

        async def conduct_research(self):
            self._paper_candidate_collector.add_batch((_candidate(),))

        async def get_subtopics(self):
            return SimpleNamespace(subtopics=[])

        async def write_introduction(self):
            return "intro"

        def get_paper_candidates(self):
            return self._paper_candidate_collector.snapshot()

    report.gpt_researcher = InitialResearcher()
    report._initial_research = AsyncMock(
        side_effect=report.gpt_researcher.conduct_research
    )

    assert await report.run() == "report"
    first = report.get_paper_candidates()
    assert first == report.gpt_researcher.get_paper_candidates()
    assert tuple(item.candidate_id for item in first) == (
        "arxiv:2401.00001",
        "arxiv:subtopic",
    )

    report._initial_research.reset_mock(side_effect=True)
    report._initial_research = AsyncMock(
        side_effect=report.gpt_researcher.conduct_research
    )
    assert await report.run() == "report"
    assert len(report.get_paper_candidates()) == 2
    assert report._paper_candidate_collector is not None
    assert isinstance(report._paper_candidate_collector, PaperCandidateCollector)


@requires_execution
@pytest.mark.asyncio
async def test_detailed_report_overlap_and_failure_abort(monkeypatch):
    from backend.report_type.detailed_report.detailed_report import DetailedReport

    report = DetailedReport.__new__(DetailedReport)
    report._paper_candidate_collector = None
    report._paper_candidate_run_active = False
    report.global_urls = set()
    entered = asyncio.Event()
    release = asyncio.Event()

    class InitialResearcher:
        def __init__(self):
            self._paper_candidate_collector = None
            self.visited_urls = set()

        def _bind_paper_candidate_collector(self, collector, *, owner):
            self._paper_candidate_collector = collector

        def get_paper_candidates(self):
            return self._paper_candidate_collector.snapshot()

    report.gpt_researcher = InitialResearcher()

    async def fail_initial():
        entered.set()
        await release.wait()
        raise RuntimeError("detailed failure")

    report._initial_research = fail_initial
    first = asyncio.create_task(report.run())
    await entered.wait()
    with pytest.raises(RuntimeError, match="already active"):
        await report.run()
    release.set()
    with pytest.raises(RuntimeError, match="detailed failure"):
        await first
    assert report._paper_candidate_run_active is False
    with pytest.raises(RuntimeError):
        report.get_paper_candidates()


@requires_execution
@pytest.mark.asyncio
async def test_detailed_report_creation_failure_cannot_expose_old_snapshot(monkeypatch):
    import backend.report_type.detailed_report.detailed_report as detailed_module
    from backend.report_type.detailed_report.detailed_report import DetailedReport
    from gpt_researcher.screening.collection import PaperCandidateCollector

    previous = PaperCandidateCollector()
    previous.add_batch((_candidate(candidate_id="arxiv:previous"),))
    previous.finalize()

    report = DetailedReport.__new__(DetailedReport)
    report._paper_candidate_collector = previous
    report._paper_candidate_run_active = False

    class InitialResearcher:
        def __init__(self):
            self._paper_candidate_collector = previous

        def _bind_paper_candidate_collector(self, collector, *, owner):
            assert owner is False
            self._paper_candidate_collector = collector

        def get_paper_candidates(self):
            if self._paper_candidate_collector is None:
                raise RuntimeError("no candidate run")
            return self._paper_candidate_collector.snapshot()

    report.gpt_researcher = InitialResearcher()
    monkeypatch.setattr(
        detailed_module,
        "PaperCandidateCollector",
        MagicMock(side_effect=RuntimeError("collector creation failed")),
    )

    with pytest.raises(RuntimeError, match="collector creation failed"):
        await report.run()
    assert report._paper_candidate_run_active is False
    with pytest.raises(RuntimeError):
        report.get_paper_candidates()
    with pytest.raises(RuntimeError):
        report.gpt_researcher.get_paper_candidates()


@requires_execution
@pytest.mark.asyncio
async def test_detailed_report_cancellation_aborts_and_re_raises():
    from backend.report_type.detailed_report.detailed_report import DetailedReport

    report = DetailedReport.__new__(DetailedReport)
    report._paper_candidate_collector = None
    report._paper_candidate_run_active = False

    class InitialResearcher:
        def __init__(self):
            self._paper_candidate_collector = None

        def _bind_paper_candidate_collector(self, collector, *, owner):
            assert owner is False
            self._paper_candidate_collector = collector

    report.gpt_researcher = InitialResearcher()

    async def cancel_initial():
        report._paper_candidate_collector.add_batch((_candidate(),))
        raise asyncio.CancelledError

    report._initial_research = cancel_initial
    with pytest.raises(asyncio.CancelledError):
        await report.run()
    assert report._paper_candidate_run_active is False
    with pytest.raises(RuntimeError):
        report.get_paper_candidates()
