import asyncio
import builtins
import socket
from types import SimpleNamespace

import pytest

from gpt_researcher.screening.decisions import ScreeningPolicy
from gpt_researcher.screening.models import PaperCandidate
from gpt_researcher.screening.workspace import ScreeningWorkspace, WorkspaceState
from gpt_researcher.skills.researcher import PreparedEvidenceRequest, ResearchConductor
from gpt_researcher.utils.enum import ReportSource, ReportType


@pytest.fixture(autouse=True)
def _isolated_environment_and_no_network(monkeypatch):
    for name in (
        "PAPER_SCREENING_ENABLED",
        "PAPER_SCREENING_TOPIC_RELEVANCE_ENABLED",
        "OPENAI_API_KEY",
        "SEMANTIC_SCHOLAR_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    def blocked(*_args, **_kwargs):
        raise AssertionError("real external access is forbidden")

    monkeypatch.setattr(socket, "create_connection", blocked)


class AcademicRetriever:
    def search_candidates(self):
        return ()


def _candidate() -> PaperCandidate:
    return PaperCandidate(
        candidate_id="arxiv:paper",
        source="arxiv",
        source_record_id="paper",
        retrieval_query="query",
        source_rank=1,
        title="A sufficiently descriptive paper title",
        href="https://academic.invalid/paper",
        body="academic body",
        abstract="academic abstract",
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


def _cfg(topic_value="false", screening_value="true"):
    return SimpleNamespace(
        paper_screening_enabled=screening_value,
        paper_screening_min_year="",
        paper_screening_max_year="",
        paper_screening_unknown_year="include",
        paper_screening_allowed_types="",
        paper_screening_unknown_type="include",
        paper_screening_topic_relevance_enabled=topic_value,
        max_search_results_per_query=5,
        smart_llm_model="model",
        smart_llm_provider="provider",
        smart_token_limit=1000,
        temperature=0.2,
        llm_kwargs={},
        reasoning_effort="medium",
        mcp_strategy="disabled",
    )


def _researcher(*, cfg=None, source=ReportSource.Web.value, report_type=ReportType.ResearchReport.value):
    return SimpleNamespace(
        report_source=source,
        report_type=report_type,
        retrievers=[AcademicRetriever],
        cfg=cfg or _cfg(),
        query="root topic",
        query_domains=[],
        verbose=False,
        websocket=None,
        role="role",
        parent_query=None,
        kwargs={},
        _paper_candidate_collector=None,
        add_costs=lambda _cost: None,
        vector_store=None,
        visited_urls=set(),
        add_research_sources=lambda _sources: None,
    )


def test_topic_config_is_not_read_before_first_four_gates():
    class Config:
        paper_screening_enabled = "false"

        @property
        def paper_screening_topic_relevance_enabled(self):
            raise AssertionError("topic config accessed before deterministic gates")

    conductor = ResearchConductor(_researcher(cfg=Config()))
    conductor._bind_paper_screening_for_run()
    assert conductor._paper_topic_relevance_enabled is False

    class OutOfScopeConfig:
        @property
        def paper_screening_enabled(self):
            raise AssertionError("screening config accessed out of scope")

        @property
        def paper_screening_topic_relevance_enabled(self):
            raise AssertionError("topic config accessed out of scope")

    conductor = ResearchConductor(
        _researcher(cfg=OutOfScopeConfig(), source=ReportSource.Local.value)
    )
    conductor._bind_paper_screening_for_run()
    assert conductor._paper_topic_relevance_enabled is False


def test_no_candidate_capability_does_not_read_topic_config():
    class Config:
        paper_screening_enabled = "true"
        paper_screening_min_year = ""
        paper_screening_max_year = ""
        paper_screening_unknown_year = "include"
        paper_screening_allowed_types = ""
        paper_screening_unknown_type = "include"

        @property
        def paper_screening_topic_relevance_enabled(self):
            raise AssertionError("topic config accessed before capability passed")

    researcher = _researcher(cfg=Config())
    researcher.retrievers = [type("Ordinary", (), {"search": lambda self: []})]
    conductor = ResearchConductor(researcher)
    conductor._bind_paper_screening_for_run()
    assert conductor._paper_topic_relevance_enabled is False


def test_topic_value_is_read_once_after_capability_and_parsed_strictly():
    reads = 0

    class Config:
        paper_screening_enabled = "true"
        paper_screening_min_year = ""
        paper_screening_max_year = ""
        paper_screening_unknown_year = "include"
        paper_screening_allowed_types = ""
        paper_screening_unknown_type = "include"

        @property
        def paper_screening_topic_relevance_enabled(self):
            nonlocal reads
            reads += 1
            return " TRUE "

    conductor = ResearchConductor(_researcher(cfg=Config()))
    conductor._bind_paper_screening_for_run()
    assert reads == 1
    assert conductor._paper_topic_relevance_enabled is True

    invalid = _researcher(cfg=_cfg(topic_value="invalid"))
    with pytest.raises(ValueError, match="PAPER_SCREENING_TOPIC_RELEVANCE_ENABLED"):
        ResearchConductor(invalid)._bind_paper_screening_for_run()


def test_disabled_topic_path_does_not_runtime_import_topic_modules(monkeypatch):
    imported = []
    original_import = builtins.__import__

    def tracking_import(name, *args, **kwargs):
        if name in {
            "gpt_researcher.actions.paper_relevance",
            "gpt_researcher.screening.relevance",
        } or "paper_relevance" in name or name.endswith("screening.relevance"):
            imported.append(name)
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", tracking_import)
    conductor = ResearchConductor(_researcher(cfg=_cfg(topic_value="false")))
    conductor._bind_paper_screening_for_run()
    assert imported == []
    assert conductor._paper_topic_relevance_enabled is False


@pytest.mark.asyncio
async def test_topic_barrier_runs_after_screen_and_before_phase_b(monkeypatch):
    researcher = _researcher(cfg=_cfg(topic_value="true"))
    conductor = ResearchConductor(researcher)
    conductor._bind_paper_screening_for_run()
    events = []
    candidate = _candidate()

    async def no_mcp(_query):
        return None

    async def plan(_query, workspace, _domains):
        workspace.add_request("planning:000001", planning_only=True)
        events.append("planning")
        return []

    async def prepare(request_id, query, workspace, *_args):
        workspace.add_candidates(request_id, False, 1, (candidate,))
        events.append("phase_a")
        return PreparedEvidenceRequest(
            request_id,
            query,
            ("https://ordinary.invalid/page",),
            ({"url": "https://prefetched.invalid", "raw_content": "ordinary prefetched"},),
            (),
            ({"content": "mcp"},),
        )

    async def topic(_topic, deterministic_result, _cfg, _callback):
        from gpt_researcher.screening.relevance import (
            TopicRelevanceDecision,
            TopicRelevanceReasonCode,
            TopicRelevanceVerdict,
            TopicScreeningResult,
        )
        from gpt_researcher.screening.decisions import RetrievalRequestRoute

        events.append("topic")
        group = deterministic_result.duplicate_groups[0]
        occurrence = next(
            item
            for item in deterministic_result.occurrences
            if item.occurrence_id == group.canonical_occurrence_id
        )
        decision = TopicRelevanceDecision(
            decision_order=1,
            duplicate_group_id=group.group_id,
            canonical_occurrence_id=group.canonical_occurrence_id,
            canonical_candidate_id=occurrence.candidate.candidate_id,
            verdict=TopicRelevanceVerdict.IRRELEVANT,
            reason_code=TopicRelevanceReasonCode.OUT_OF_SCOPE,
            rationale="Outside scope.",
            confidence=90,
        )
        routes = tuple(
            RetrievalRequestRoute(
                retrieval_request_id=route.retrieval_request_id,
                canonical_occurrence_ids=(),
            )
            for route in deterministic_result.routes
        )
        return TopicScreeningResult(
            deterministic_result=deterministic_result,
            relevance_decisions=(decision,),
            effective_routes=routes,
        )

    consumed = []

    async def consume(prepared, workspace, *, canonical_occurrence_ids=None):
        assert workspace.state is WorkspaceState.SCREENED
        events.append("phase_b")
        consumed.append((prepared, canonical_occurrence_ids))
        return "ordinary context"

    monkeypatch.setattr(conductor, "_prepare_screening_mcp_cache", no_mcp)
    monkeypatch.setattr(conductor, "_plan_screened_research", plan)
    monkeypatch.setattr(conductor, "_prepare_evidence_request", prepare)
    monkeypatch.setattr(conductor, "_consume_prepared_evidence", consume)
    monkeypatch.setattr(
        "gpt_researcher.actions.paper_relevance.assess_topic_relevance", topic
    )

    assert await conductor._get_context_by_screened_web_search("root topic", [], []) == "ordinary context"
    assert events == ["planning", "phase_a", "topic", "phase_b"]
    prepared, route_ids = consumed[0]
    assert route_ids == ()
    assert prepared.ordinary_urls == ("https://ordinary.invalid/page",)
    assert prepared.ordinary_prefetched_content
    assert prepared.mcp_context == ({"content": "mcp"},)


@pytest.mark.asyncio
async def test_empty_effective_route_still_consumes_ordinary_prefetched_and_mcp(monkeypatch):
    researcher = _researcher(cfg=_cfg(topic_value="false"))
    browser_calls = []
    context_inputs = []

    class Scraper:
        async def browse_urls(self, urls):
            browser_calls.append(tuple(urls))
            return [{"url": url, "raw_content": "ordinary scraped"} for url in urls]

    class ContextManager:
        async def get_similar_content_by_query(self, query, content):
            context_inputs.append((query, tuple(content)))
            return "web context"

    researcher.scraper_manager = Scraper()
    researcher.context_manager = ContextManager()
    conductor = ResearchConductor(researcher)
    workspace = ScreeningWorkspace(ScreeningPolicy())
    workspace.add_request("evidence:000001", planning_only=False)
    workspace.add_candidates("evidence:000001", False, 1, (_candidate(),))
    workspace.screen()
    prepared = PreparedEvidenceRequest(
        "evidence:000001",
        "query",
        ("https://ordinary.invalid",),
        ({"url": "https://prefetched.invalid", "raw_content": "prefetched"},),
        (),
        ({"content": "mcp"},),
    )
    monkeypatch.setattr(conductor, "_get_new_urls", lambda urls: asyncio.sleep(0, result=urls))
    monkeypatch.setattr(
        conductor,
        "_combine_mcp_and_web_context",
        lambda mcp, web, _query: f"{web}|{mcp[0]['content']}",
    )
    result = await conductor._consume_prepared_evidence(
        prepared, workspace, canonical_occurrence_ids=()
    )
    assert result == "web context|mcp"
    assert browser_calls == [("https://ordinary.invalid",)]
    assert context_inputs[0][1] == (
        {"url": "https://ordinary.invalid", "raw_content": "ordinary scraped"},
        {"url": "https://prefetched.invalid", "raw_content": "prefetched"},
    )
    assert _candidate().href not in browser_calls[0]


@pytest.mark.asyncio
async def test_topic_cancellation_aborts_workspace_and_skips_phase_b(monkeypatch):
    researcher = _researcher(cfg=_cfg(topic_value="true"))
    conductor = ResearchConductor(researcher)
    conductor._bind_paper_screening_for_run()
    created = []
    real_workspace = ScreeningWorkspace

    def workspace_factory(policy):
        value = real_workspace(policy)
        created.append(value)
        return value

    async def no_mcp(_query):
        return None

    async def plan(_query, workspace, _domains):
        workspace.add_request("planning:000001", planning_only=True)
        return []

    async def prepare(request_id, query, workspace, *_args):
        workspace.add_candidates(request_id, False, 1, (_candidate(),))
        return PreparedEvidenceRequest(request_id, query, (), (), (), ())

    cancellation = asyncio.CancelledError("topic cancelled")

    async def cancel(*_args):
        raise cancellation

    phase_b_calls = []

    async def forbidden_phase_b(*_args, **_kwargs):
        phase_b_calls.append(True)
        return ""

    monkeypatch.setattr("gpt_researcher.skills.researcher.ScreeningWorkspace", workspace_factory)
    monkeypatch.setattr(conductor, "_prepare_screening_mcp_cache", no_mcp)
    monkeypatch.setattr(conductor, "_plan_screened_research", plan)
    monkeypatch.setattr(conductor, "_prepare_evidence_request", prepare)
    monkeypatch.setattr(conductor, "_consume_prepared_evidence", forbidden_phase_b)
    monkeypatch.setattr("gpt_researcher.actions.paper_relevance.assess_topic_relevance", cancel)

    with pytest.raises(asyncio.CancelledError) as raised:
        await conductor._get_context_by_screened_web_search("root topic", [], [])
    assert raised.value is cancellation
    assert created[0].state is WorkspaceState.ABORTED
    assert phase_b_calls == []


def test_consecutive_and_concurrent_binding_state_is_isolated():
    first = ResearchConductor(_researcher(cfg=_cfg(topic_value="true")))
    second = ResearchConductor(_researcher(cfg=_cfg(topic_value="false")))
    first._bind_paper_screening_for_run()
    second._bind_paper_screening_for_run()
    assert first._paper_topic_relevance_enabled is True
    assert second._paper_topic_relevance_enabled is False

    first.researcher.cfg.paper_screening_topic_relevance_enabled = "false"
    first._bind_paper_screening_for_run()
    assert first._paper_topic_relevance_enabled is False
    assert second._paper_topic_relevance_enabled is False


@pytest.mark.asyncio
async def test_topic_pipeline_consumes_request_without_deterministic_route(monkeypatch):
    researcher = _researcher(cfg=_cfg(topic_value="true"))
    browser_calls = []
    context_inputs = []
    mcp_inputs = []
    created = []
    captured_topic_results = []

    class Scraper:
        async def browse_urls(self, urls):
            browser_calls.append(tuple(urls))
            return [
                {"url": url, "raw_content": "ordinary scraped"}
                for url in urls
            ]

    class ContextManager:
        async def get_similar_content_by_query(self, query, content):
            context_inputs.append((query, tuple(content)))
            return "ordinary context"

    researcher.scraper_manager = Scraper()
    researcher.context_manager = ContextManager()
    conductor = ResearchConductor(researcher)
    conductor._bind_paper_screening_for_run()
    real_workspace = ScreeningWorkspace

    def workspace_factory(policy):
        workspace = real_workspace(policy)
        created.append(workspace)
        return workspace

    async def no_mcp(_query):
        return None

    async def plan(_query, workspace, _domains):
        workspace.add_request("planning:000001", planning_only=True)
        return []

    async def prepare(request_id, query, _workspace, *_args):
        return PreparedEvidenceRequest(
            request_id,
            query,
            ("https://ordinary.invalid/no-academic",),
            (
                {
                    "url": "https://prefetched.invalid/no-academic",
                    "raw_content": "ordinary prefetched",
                },
            ),
            (),
            ({"content": "mcp without academic"},),
        )

    async def forbidden_llm(*_args, **_kwargs):
        raise AssertionError("a request without a canonical must not call the LLM")

    from gpt_researcher.actions import paper_relevance

    real_assess = paper_relevance.assess_topic_relevance

    async def capture_topic_result(*args, **kwargs):
        result = await real_assess(*args, **kwargs)
        captured_topic_results.append(result)
        return result

    def combine(mcp, web, query):
        mcp_inputs.append((query, tuple(mcp)))
        return f"{web}|{mcp[0]['content']}"

    monkeypatch.setattr(
        "gpt_researcher.skills.researcher.ScreeningWorkspace", workspace_factory
    )
    monkeypatch.setattr(conductor, "_prepare_screening_mcp_cache", no_mcp)
    monkeypatch.setattr(conductor, "_plan_screened_research", plan)
    monkeypatch.setattr(conductor, "_prepare_evidence_request", prepare)
    monkeypatch.setattr(conductor, "_combine_mcp_and_web_context", combine)
    monkeypatch.setattr(
        paper_relevance, "create_chat_completion", forbidden_llm
    )
    monkeypatch.setattr(paper_relevance, "assess_topic_relevance", capture_topic_result)

    result = await conductor._get_context_by_screened_web_search(
        "root topic", [], []
    )

    assert result == "ordinary context|mcp without academic"
    assert captured_topic_results[0].deterministic_result.routes == ()
    assert captured_topic_results[0].effective_routes == ()
    assert created[0].state is WorkspaceState.FINALIZED
    assert browser_calls == [("https://ordinary.invalid/no-academic",)]
    assert context_inputs[0][1] == (
        {
            "url": "https://ordinary.invalid/no-academic",
            "raw_content": "ordinary scraped",
        },
        {
            "url": "https://prefetched.invalid/no-academic",
            "raw_content": "ordinary prefetched",
        },
    )
    assert mcp_inputs == [
        ("root topic", ({"content": "mcp without academic"},))
    ]


@pytest.mark.asyncio
async def test_topic_pipeline_keeps_filtered_and_missing_routes_distinct(monkeypatch):
    class RecoverableAcademicRetriever:
        def __init__(self, query, query_domains=None):
            self.query = query

        def search_candidates(self, max_results=None):
            if self.query == "provider failure":
                raise RuntimeError("synthetic recoverable provider failure")
            return (_candidate(),)

    class OrdinaryRetriever:
        def __init__(self, query, query_domains=None):
            self.query = query

        def search(self, max_results=None):
            slug = self.query.replace(" ", "-")
            return (
                {
                    "title": "ordinary",
                    "href": f"https://ordinary.invalid/{slug}",
                    "body": "summary",
                },
                {
                    "title": "prefetched",
                    "href": f"https://prefetched.invalid/{slug}",
                    "raw_content": "p" * 101,
                },
            )

    class FakeMCPRetriever:
        pass

    cfg = _cfg(topic_value="true")
    cfg.mcp_strategy = "fast"
    researcher = _researcher(cfg=cfg)
    researcher.retrievers = [
        RecoverableAcademicRetriever,
        OrdinaryRetriever,
        FakeMCPRetriever,
    ]
    browser_calls = []
    context_inputs = []
    mcp_inputs = []
    captured_topic_results = []
    created = []
    llm_calls = []

    class Scraper:
        async def browse_urls(self, urls):
            browser_calls.append(tuple(urls))
            return [
                {"url": url, "raw_content": f"scraped:{url}"}
                for url in urls
            ]

    class ContextManager:
        async def get_similar_content_by_query(self, query, content):
            context_inputs.append((query, tuple(content)))
            return f"context:{query}"

    researcher.scraper_manager = Scraper()
    researcher.context_manager = ContextManager()
    conductor = ResearchConductor(researcher)
    conductor._bind_paper_screening_for_run()
    conductor._mcp_results_cache = [{"content": "shared mcp"}]
    real_workspace = ScreeningWorkspace

    def workspace_factory(policy):
        workspace = real_workspace(policy)
        created.append(workspace)
        return workspace

    async def plan(_query, workspace, _domains):
        workspace.add_request("planning:000001", planning_only=True)
        return ["candidate request"]

    async def irrelevant_llm(*_args, **_kwargs):
        llm_calls.append(True)
        return (
            '{"verdict":"irrelevant","reason_code":"out_of_scope",'
            '"rationale":"Outside the root topic.","confidence":90}'
        )

    from gpt_researcher.actions import paper_relevance

    real_assess = paper_relevance.assess_topic_relevance

    async def capture_topic_result(*args, **kwargs):
        result = await real_assess(*args, **kwargs)
        captured_topic_results.append(result)
        return result

    def combine(mcp, web, query):
        mcp_inputs.append((query, tuple(mcp)))
        return f"{web}|{mcp[0]['content']}"

    monkeypatch.setattr(
        "gpt_researcher.skills.researcher.ScreeningWorkspace", workspace_factory
    )
    monkeypatch.setattr(conductor, "_plan_screened_research", plan)
    monkeypatch.setattr(conductor, "_combine_mcp_and_web_context", combine)
    monkeypatch.setattr(paper_relevance, "create_chat_completion", irrelevant_llm)
    monkeypatch.setattr(paper_relevance, "assess_topic_relevance", capture_topic_result)

    result = await conductor._get_context_by_screened_web_search(
        "provider failure", [], []
    )

    assert result == (
        "context:candidate request|shared mcp "
        "context:provider failure|shared mcp"
    )
    topic_result = captured_topic_results[0]
    assert tuple(
        route.retrieval_request_id for route in topic_result.effective_routes
    ) == ("evidence:000001",)
    assert topic_result.effective_routes[0].canonical_occurrence_ids == ()
    assert all(
        route.retrieval_request_id != "evidence:000002"
        for route in topic_result.effective_routes
    )
    assert llm_calls == [True]
    assert created[0].state is WorkspaceState.FINALIZED
    assert tuple(query for query, _content in context_inputs) == (
        "candidate request",
        "provider failure",
    )
    assert browser_calls == [
        ("https://ordinary.invalid/candidate-request",),
        ("https://ordinary.invalid/provider-failure",),
    ]
    assert tuple(query for query, _mcp in mcp_inputs) == (
        "candidate request",
        "provider failure",
    )
    assert all(
        any(item["url"].startswith("https://prefetched.invalid/") for item in content)
        for _query, content in context_inputs
    )
    assert all(
        _candidate().href not in urls
        for urls in browser_calls
    )
