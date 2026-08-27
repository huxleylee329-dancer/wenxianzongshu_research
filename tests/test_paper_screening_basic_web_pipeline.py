import asyncio
import logging
import socket
import threading
from types import SimpleNamespace

import pytest

from gpt_researcher.agent import GPTResearcher
from gpt_researcher.actions.query_processing import (
    RetrieverBatchError,
    _execute_retriever_batch,
    execute_retriever_search,
)
from gpt_researcher.screening.collection import CollectorState, PaperCandidateCollector
from gpt_researcher.screening.decisions import (
    PaperType,
    ScreeningPolicy,
    UnknownValuePolicy,
)
from gpt_researcher.screening.models import PaperCandidate
from gpt_researcher.screening.workspace import ScreeningWorkspace, WorkspaceState
from gpt_researcher.skills.researcher import PreparedEvidenceRequest, ResearchConductor
from gpt_researcher.utils.enum import ReportSource, ReportType


@pytest.fixture(autouse=True)
def _isolated_environment_and_no_network(monkeypatch):
    for name in (
        "PAPER_SCREENING_ENABLED",
        "PAPER_SCREENING_MIN_YEAR",
        "PAPER_SCREENING_MAX_YEAR",
        "PAPER_SCREENING_UNKNOWN_YEAR",
        "PAPER_SCREENING_ALLOWED_TYPES",
        "PAPER_SCREENING_UNKNOWN_TYPE",
        "SEMANTIC_SCHOLAR_API_KEY",
        "SEMANTIC_SCHOLAR_JOURNALS",
    ):
        monkeypatch.delenv(name, raising=False)

    def blocked(*_args, **_kwargs):
        raise AssertionError("real external access is forbidden")

    monkeypatch.setattr(socket, "create_connection", blocked)


def _candidate(candidate_id="arxiv:one", body="  academic body  "):
    return PaperCandidate(
        candidate_id=candidate_id,
        source="arxiv",
        source_record_id="one",
        retrieval_query="query",
        source_rank=1,
        title="A sufficiently long paper title",
        href=f"https://academic.test/{candidate_id}",
        body=body,
        abstract="abstract",
        authors=(),
        published_year=2024,
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


def _researcher(*, source=ReportSource.Web.value, report_type=ReportType.ResearchReport.value, retrievers=()):
    return SimpleNamespace(
        report_source=source,
        report_type=report_type,
        retrievers=list(retrievers),
        cfg=SimpleNamespace(
            paper_screening_enabled="false",
            paper_screening_min_year="",
            paper_screening_max_year="",
            paper_screening_unknown_year="include",
            paper_screening_allowed_types="",
            paper_screening_unknown_type="include",
            max_search_results_per_query=5,
        ),
    )


def _bare_agent(*, cfg, source=ReportSource.Web.value, report_type=ReportType.ResearchReport.value, retrievers=()):
    researcher = object.__new__(GPTResearcher)
    researcher.report_source = source
    researcher.report_type = report_type
    researcher.cfg = cfg
    researcher.retrievers = list(retrievers)
    researcher._paper_candidate_collector = None
    researcher._paper_candidate_collector_owner = False
    researcher._paper_candidate_collector_borrower = False
    researcher._paper_candidate_run_active = False
    researcher.research_conductor = ResearchConductor(researcher)
    return researcher


def test_constructor_does_not_read_screening_config_or_check_capability():
    class ExplodingConfig:
        def __getattr__(self, name):
            if name.startswith("paper_screening"):
                raise AssertionError("constructor must not read screening config")
            raise AttributeError(name)

    class ExplodingCapability(type):
        def __getattribute__(cls, name):
            if name == "search_candidates":
                raise AssertionError("constructor must not inspect capabilities")
            return super().__getattribute__(name)

    class Retriever(metaclass=ExplodingCapability):
        pass

    researcher = _researcher(retrievers=(Retriever,))
    researcher.cfg = ExplodingConfig()
    conductor = ResearchConductor(researcher)
    assert conductor._paper_screening_entry_eligible is False
    assert conductor._paper_screening_enabled is False
    assert conductor._paper_screening_policy is None
    assert conductor._paper_screening_candidate_capable is False


@pytest.mark.parametrize(
    "source,report_type",
    [
        (ReportSource.Hybrid.value, ReportType.ResearchReport.value),
        (ReportSource.Local.value, ReportType.ResearchReport.value),
        (ReportSource.Web.value, ReportType.DeepResearch.value),
        (ReportSource.Web.value, ReportType.DetailedReport.value),
        (ReportSource.Web.value, ReportType.SubtopicReport.value),
    ],
)
def test_out_of_scope_modes_do_not_read_invalid_screening_config(source, report_type):
    class ExplodingConfig:
        max_search_results_per_query = 5

        def __getattr__(self, name):
            if name.startswith("paper_screening"):
                raise AssertionError("screening config must not be read")
            raise AttributeError(name)

    researcher = _researcher(source=source, report_type=report_type)
    researcher.cfg = ExplodingConfig()
    conductor = ResearchConductor(researcher)
    assert conductor._paper_screening_entry_eligible is False


def test_eligible_invalid_enabled_fails_before_capability_gate():
    class Retriever:
        @classmethod
        def __getattribute__(cls, name):
            if name == "search_candidates":
                raise AssertionError("capability gate ran too early")
            return super().__getattribute__(name)

    researcher = _researcher(retrievers=(Retriever,))
    researcher.cfg.paper_screening_enabled = "invalid"
    conductor = ResearchConductor(researcher)
    with pytest.raises(ValueError, match="PAPER_SCREENING_ENABLED"):
        conductor._bind_paper_screening_for_run()


def test_enabled_policy_is_strict_and_no_academic_uses_old_path():
    class OrdinaryRetriever:
        def search(self):
            return []

    researcher = _researcher(retrievers=(OrdinaryRetriever,))
    researcher.cfg.paper_screening_enabled = " TRUE "
    researcher.cfg.paper_screening_min_year = "2020"
    researcher.cfg.paper_screening_max_year = "2025"
    researcher.cfg.paper_screening_unknown_year = "exclude"
    researcher.cfg.paper_screening_allowed_types = "journal, preprint"
    researcher.cfg.paper_screening_unknown_type = "include"
    conductor = ResearchConductor(researcher)
    conductor._bind_paper_screening_for_run()
    assert conductor._paper_screening_enabled is True
    assert conductor._paper_screening_candidate_capable is False
    assert conductor._paper_screening_policy == ScreeningPolicy(
        min_year=2020,
        max_year=2025,
        unknown_year=UnknownValuePolicy.EXCLUDE,
        allowed_paper_types=(PaperType.JOURNAL, PaperType.PREPRINT),
        unknown_paper_type=UnknownValuePolicy.INCLUDE,
    )
    assert conductor._should_use_paper_screening() is False


@pytest.mark.asyncio
async def test_quick_search_ignores_invalid_screening_config_and_never_binds(
    monkeypatch,
):
    class ExplodingConfig:
        def __getattr__(self, name):
            if name.startswith("paper_screening"):
                raise AssertionError("Quick Search must not read screening config")
            raise AttributeError(name)

    researcher = _bare_agent(cfg=ExplodingConfig())
    calls = []

    def forbidden_binding():
        raise AssertionError("Quick Search must not bind screening")

    async def quick_impl(*_args, **_kwargs):
        calls.append("quick")
        return [{"title": "t", "href": "https://example.test", "body": "b"}]

    researcher.research_conductor._bind_paper_screening_for_run = forbidden_binding
    researcher._quick_search_impl = quick_impl
    monkeypatch.setattr(
        "gpt_researcher.skills.researcher.ScreeningWorkspace",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Quick Search must not create a Workspace")
        ),
    )
    monkeypatch.setattr(
        "gpt_researcher.screening.workspace.screen_paper_occurrences",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Quick Search must not call the screening engine")
        ),
    )

    result = await researcher.quick_search("query")

    assert calls == ["quick"]
    assert result == [{"title": "t", "href": "https://example.test", "body": "b"}]
    assert researcher._paper_candidate_collector.state is CollectorState.FINALIZED
    assert researcher._paper_candidate_run_active is False


@pytest.mark.asyncio
async def test_conduct_research_invalid_binding_aborts_owner_before_external_call():
    cfg = _researcher().cfg
    cfg.paper_screening_enabled = "invalid"
    researcher = _bare_agent(cfg=cfg, retrievers=(type("Academic", (), {"search_candidates": lambda self: ()}),))
    external_calls = []

    async def conduct_impl(*_args, **_kwargs):
        external_calls.append("external")
        return "unexpected"

    researcher._conduct_research_impl = conduct_impl

    with pytest.raises(ValueError, match="PAPER_SCREENING_ENABLED"):
        await researcher.conduct_research()

    assert external_calls == []
    assert researcher._paper_candidate_collector.state is CollectorState.ABORTED
    assert researcher._paper_candidate_run_active is False
    with pytest.raises(RuntimeError):
        researcher.get_paper_candidates()


@pytest.mark.asyncio
async def test_out_of_scope_conduct_research_does_not_read_invalid_config():
    class ExplodingConfig:
        def __getattr__(self, name):
            if name.startswith("paper_screening"):
                raise AssertionError("out-of-scope run must not read screening config")
            raise AttributeError(name)

    researcher = _bare_agent(
        cfg=ExplodingConfig(),
        source=ReportSource.Local.value,
        report_type=ReportType.ResearchReport.value,
    )
    calls = []

    async def conduct_impl(*_args, **_kwargs):
        calls.append("conduct")
        return "local result"

    researcher._conduct_research_impl = conduct_impl

    assert await researcher.conduct_research() == "local result"
    assert calls == ["conduct"]
    assert researcher.research_conductor._paper_screening_entry_eligible is False


@pytest.mark.asyncio
async def test_consecutive_conduct_runs_rebind_fresh_state():
    cfg = _researcher().cfg
    cfg.paper_screening_enabled = "true"
    cfg.paper_screening_min_year = "2020"

    class Academic:
        def search_candidates(self):
            return ()

    researcher = _bare_agent(cfg=cfg, retrievers=(Academic,))
    bindings = []
    collectors = []

    async def conduct_impl(*_args, **_kwargs):
        bindings.append(researcher.research_conductor._paper_screening_policy)
        collectors.append(researcher._paper_candidate_collector)
        return "ok"

    researcher._conduct_research_impl = conduct_impl

    assert await researcher.conduct_research() == "ok"
    cfg.paper_screening_min_year = "2021"
    assert await researcher.conduct_research() == "ok"

    assert [policy.min_year for policy in bindings] == [2020, 2021]
    assert bindings[0] is not bindings[1]
    assert collectors[0] is not collectors[1]


@pytest.mark.asyncio
async def test_overlap_guard_runs_before_screening_state_is_replaced():
    cfg = _researcher().cfg
    researcher = _bare_agent(cfg=cfg)
    sentinel_policy = object()
    conductor = researcher.research_conductor
    conductor._paper_screening_entry_eligible = True
    conductor._paper_screening_enabled = True
    conductor._paper_screening_policy = sentinel_policy
    conductor._paper_screening_candidate_capable = True
    researcher._paper_candidate_run_active = True

    with pytest.raises(RuntimeError, match="already active"):
        await researcher.conduct_research()

    assert conductor._paper_screening_entry_eligible is True
    assert conductor._paper_screening_enabled is True
    assert conductor._paper_screening_policy is sentinel_policy
    assert conductor._paper_screening_candidate_capable is True


@pytest.mark.asyncio
async def test_cancelled_binding_aborts_owner_and_propagates_same_exception():
    researcher = _bare_agent(cfg=_researcher().cfg)
    cancellation = asyncio.CancelledError("binding cancelled")

    def cancel_binding():
        raise cancellation

    researcher.research_conductor._bind_paper_screening_for_run = cancel_binding
    researcher._conduct_research_impl = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("research implementation must not run")
    )

    with pytest.raises(asyncio.CancelledError) as raised:
        await researcher.conduct_research()

    assert raised.value is cancellation
    assert researcher._paper_candidate_collector.state is CollectorState.ABORTED
    assert researcher._paper_candidate_run_active is False


@pytest.mark.asyncio
async def test_candidate_generator_materialized_once_and_existing_helper_unchanged():
    candidate = _candidate()
    iterations = 0
    worker_thread = None

    class Retriever:
        def search_candidates(self, max_results=5):
            nonlocal worker_thread
            worker_thread = threading.get_ident()

            def values():
                nonlocal iterations
                iterations += 1
                yield candidate

            return values()

        def search(self, max_results=5):
            raise AssertionError("search must not be called")

    batch = await _execute_retriever_batch(Retriever(), max_results=3)
    assert batch.candidates == (candidate,)
    assert batch.projected_results == (candidate.to_retriever_result(),)
    assert iterations == 1
    assert worker_thread != threading.get_ident()

    collector = PaperCandidateCollector()
    outward = await execute_retriever_search(
        Retriever(), max_results=3, collector=collector
    )
    assert outward == [candidate.to_retriever_result()]
    assert set(outward[0]) == {"title", "href", "body"}


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_kind", ["call", "materialization", "contract"])
async def test_provider_boundary_classifies_call_materialization_and_contract(failure_kind):
    sentinel = "SYNTHETIC_SECRET_SENTINEL"

    class Retriever:
        def search_candidates(self):
            if failure_kind == "call":
                raise RuntimeError(sentinel)
            if failure_kind == "materialization":
                def values():
                    raise RuntimeError(sentinel)
                    yield
                return values()
            return (object(),)

    with pytest.raises(RetrieverBatchError) as raised:
        await _execute_retriever_batch(Retriever())
    assert raised.value.category == failure_kind
    assert sentinel not in str(raised.value)
    assert sentinel not in repr(raised.value)


@pytest.mark.asyncio
async def test_ordinary_generator_is_materialized_and_validated_in_worker_once():
    iterations = 0
    iteration_thread = None

    class Retriever:
        def search(self):
            def values():
                nonlocal iterations, iteration_thread
                iterations += 1
                iteration_thread = threading.get_ident()
                yield {"title": "ordinary", "href": "https://ordinary.test", "body": "snippet"}

            return values()

    batch = await _execute_retriever_batch(Retriever())

    assert batch.candidate_capable is False
    assert batch.projected_results == (
        {"title": "ordinary", "href": "https://ordinary.test", "body": "snippet"},
    )
    assert iterations == 1
    assert iteration_thread != threading.get_ident()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload,category",
    [
        ((item for item in (object(),)), "contract"),
        (None, "materialization"),
    ],
)
async def test_ordinary_batch_failure_is_safely_classified(payload, category):
    class Retriever:
        def search(self):
            return payload

    with pytest.raises(RetrieverBatchError) as raised:
        await _execute_retriever_batch(Retriever())

    assert raised.value.category == category


@pytest.mark.asyncio
async def test_provider_cancelled_error_is_not_wrapped():
    class Retriever:
        def search_candidates(self):
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await _execute_retriever_batch(Retriever())


def test_prepared_evidence_request_is_private_data_outside_workspace():
    prepared = PreparedEvidenceRequest(
        retrieval_request_id="evidence:000001",
        query="query",
        ordinary_urls=("https://ordinary.test",),
        ordinary_prefetched_content=(
            {"url": "https://prefetched.test", "raw_content": "x" * 101},
        ),
        academic_occurrence_ids=("occurrence:sha256:" + "0" * 64,),
        mcp_context=({"content": "mcp"},),
    )
    workspace = ScreeningWorkspace(ScreeningPolicy())
    assert prepared.ordinary_urls == ("https://ordinary.test",)
    assert not any("ordinary" in name or "mcp" in name for name in vars(workspace))


@pytest.mark.asyncio
async def test_screened_pipeline_preserves_preallocated_gather_order(monkeypatch):
    researcher = _researcher(retrievers=(type("Academic", (), {"search_candidates": lambda self: ()}),))
    researcher.cfg.paper_screening_enabled = "true"
    researcher.query = "main"
    researcher.query_domains = []
    researcher.verbose = False
    researcher.websocket = None
    researcher.report_type = ReportType.ResearchReport.value
    conductor = ResearchConductor(researcher)
    conductor._bind_paper_screening_for_run()

    monkeypatch.setattr(
        conductor, "_prepare_screening_mcp_cache", lambda _query: asyncio.sleep(0)
    )
    monkeypatch.setattr(
        conductor,
        "_plan_screened_research",
        lambda *_args, **_kwargs: asyncio.sleep(0, result=["slow", "fast"]),
    )
    completion = []

    async def prepare(request_id, query, *_args, **_kwargs):
        if query == "slow":
            await asyncio.sleep(0.02)
        completion.append(request_id)
        return PreparedEvidenceRequest(request_id, query, (), (), (), ())

    consumed = []

    async def consume(prepared, _workspace):
        consumed.append(prepared.retrieval_request_id)
        return prepared.query

    monkeypatch.setattr(conductor, "_prepare_evidence_request", prepare)
    monkeypatch.setattr(conductor, "_consume_prepared_evidence", consume)

    result = await conductor._get_context_by_screened_web_search("main", [], [])
    assert completion[0] == "evidence:000002"
    assert consumed == [
        "evidence:000001",
        "evidence:000002",
        "evidence:000003",
    ]
    assert result == "slow fast main"


@pytest.mark.asyncio
async def test_real_two_phase_path_routes_academic_body_without_scraping_landing_page(
    monkeypatch,
):
    events = []
    browser_inputs = []
    compressor_inputs = []
    research_sources = []
    academic = _candidate(body="  byte-for-byte academic body  ")

    class AcademicRetriever:
        def __init__(self, query, query_domains=()):
            self.query = query

        def search_candidates(self, max_results=5):
            events.append(("provider", self.query))
            return (academic,)

    class OrdinaryRetriever:
        def __init__(self, query, query_domains=()):
            self.query = query

        def search(self, max_results=5):
            events.append(("ordinary", self.query))
            return [
                {
                    "title": "ordinary",
                    "href": "https://ordinary.test/page",
                    "body": "search snippet only",
                },
                {
                    "title": "prefetched",
                    "href": "https://ordinary.test/prefetched",
                    "body": "ignored snippet",
                    "raw_content": "p" * 101,
                },
            ]

    class Scraper:
        async def browse_urls(self, urls):
            events.append(("scraper", tuple(urls)))
            browser_inputs.append(tuple(urls))
            return [
                {"url": url, "raw_content": "scraped ordinary page"}
                for url in urls
            ]

    class ContextManager:
        async def get_similar_content_by_query(self, query, content):
            events.append(("compressor", query))
            compressor_inputs.append(tuple(dict(item) for item in content))
            return f"context:{query}"

    researcher = _researcher(retrievers=(AcademicRetriever, OrdinaryRetriever))
    researcher.cfg.paper_screening_enabled = "true"
    researcher.cfg.mcp_strategy = "disabled"
    researcher.query = "main"
    researcher.query_domains = []
    researcher.verbose = False
    researcher.websocket = None
    researcher.role = "role"
    researcher.parent_query = None
    researcher.kwargs = {}
    researcher.add_costs = lambda *_args, **_kwargs: None
    researcher._paper_candidate_collector = None
    researcher.scraper_manager = Scraper()
    researcher.context_manager = ContextManager()
    researcher.vector_store = None
    researcher.visited_urls = set()
    researcher.add_research_sources = lambda values: research_sources.extend(values)

    async def planner(**_kwargs):
        events.append(("planner", "main"))
        return []

    monkeypatch.setattr(
        "gpt_researcher.skills.researcher.plan_research_outline", planner
    )

    conductor = ResearchConductor(researcher)
    conductor._bind_paper_screening_for_run()
    context = await conductor._get_context_by_screened_web_search("main", [], [])

    assert context == "context:main", events
    assert browser_inputs == [("https://ordinary.test/page",)]
    assert academic.href not in browser_inputs[0]
    assert compressor_inputs == [
        (
            {"url": "https://ordinary.test/page", "raw_content": "scraped ordinary page"},
            {"url": "https://ordinary.test/prefetched", "raw_content": "p" * 101},
            {"url": academic.href, "raw_content": academic.body},
        )
    ]
    assert research_sources == [
        {"url": "https://ordinary.test/prefetched"},
        {"url": academic.href},
    ]
    evidence_provider = events.index(("provider", "main"), 1)
    scraper = next(index for index, event in enumerate(events) if event[0] == "scraper")
    compressor = next(
        index for index, event in enumerate(events) if event[0] == "compressor"
    )
    assert evidence_provider < scraper < compressor


def test_safe_provider_log_omits_synthetic_secret(caplog):
    sentinel = "SYNTHETIC_SECRET_SENTINEL"
    conductor = ResearchConductor(_researcher())
    with caplog.at_level(logging.WARNING):
        conductor._log_provider_batch_failure("FakeAcademic", "call")
    text = caplog.text
    assert "FakeAcademic" in text
    assert "call" in text
    assert sentinel not in text
