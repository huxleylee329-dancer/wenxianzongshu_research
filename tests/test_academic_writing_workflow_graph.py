"""Offline graph, checkpoint, recovery, and security tests."""

from __future__ import annotations

import asyncio
import ast
import builtins
from contextlib import contextmanager
import gc
import importlib
import logging
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import traceback
import types
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from langgraph.checkpoint.memory import InMemorySaver

from gpt_researcher.workflows.academic_writing import graph as graph_module
from gpt_researcher.workflows.academic_writing import nodes as nodes_module
from gpt_researcher.workflows.academic_writing import state as state_module
from gpt_researcher.workflows.academic_writing.adapters import AcademicWritingAdapter
from gpt_researcher.workflows.academic_writing.academic_draft_composer import (
    WorkflowAcademicDraftComposition,
)
from gpt_researcher.workflows.academic_writing.citation_evidence_gate import (
    WorkflowCitationEvidenceGateResult,
)
from gpt_researcher.workflows.academic_writing.citation_review_disposition import (
    WorkflowCitationReviewDisposition,
)
from gpt_researcher.workflows.academic_writing.citation_reviewer import (
    WorkflowSectionCitationReview,
)
from gpt_researcher.workflows.academic_writing.graph import (
    resume_academic_workflow,
    start_academic_workflow,
    submit_academic_outline_decision,
)
from gpt_researcher.workflows.academic_writing.state import (
    AcademicOutlineDecisionCommand,
    AcademicWorkflowIdentity,
    AcademicWorkflowRequest,
    AdapterFailure,
    ExecutionError,
    InvariantError,
    OutlineDecisionProtocolError,
    ThreadProtocolError,
    WorkflowEvidenceSource,
    WorkflowOutline,
    WorkflowOutlineSection,
    WorkflowResearchEvidence,
    WorkflowSectionDraft,
    WorkflowTopicPlan,
    restore_workflow_state,
)
from gpt_researcher.workflows.academic_writing.report_profiles import (
    _get_report_profile,
)
from gpt_researcher.workflows.academic_writing.references_renderer import (
    WorkflowReferencedDraft,
)
from gpt_researcher.workflows.academic_writing.section_merger import (
    WorkflowMergedDraft,
)


SECRET = "RAW-ADAPTER-SECRET-SENTINEL"
INVARIANT_INPUT_SECRET = "INVARIANT-INPUT-SECRET-SENTINEL"

FAIL_FAST_EXTERNAL_TARGETS = (
    ("gpt_researcher", "GPTResearcher"),
    ("gpt_researcher.skills.researcher", "ResearchConductor"),
    ("gpt_researcher.utils.llm", "create_chat_completion"),
    ("gpt_researcher.actions.web_scraping", "scrape_urls"),
    ("gpt_researcher.llm_provider.generic.base", "GenericLLMProvider"),
    ("gpt_researcher.context.retriever", "SearchAPIRetriever"),
    ("gpt_researcher.context.retriever", "SectionRetriever"),
    ("gpt_researcher.context.compression", "VectorstoreCompressor"),
    ("gpt_researcher.context.compression", "WrittenContentCompressor"),
    ("gpt_researcher.context.compression", "ContextualCompressionRetriever"),
    ("gpt_researcher.context.compression", "DocumentCompressorPipeline"),
    ("gpt_researcher.context.compression", "ContextCompressor"),
    (
        "gpt_researcher.llm_provider.image.image_generator",
        "ImageGeneratorProvider",
    ),
    (
        "gpt_researcher.llm_provider.image.modelslab_image_generator",
        "ModelsLabImageGeneratorProvider",
    ),
    ("gpt_researcher.scraper.arxiv.arxiv", "ArxivScraper"),
    (
        "gpt_researcher.scraper.beautiful_soup.beautiful_soup",
        "BeautifulSoupScraper",
    ),
    ("gpt_researcher.scraper.browser.browser", "BrowserScraper"),
    ("gpt_researcher.scraper.browser.nodriver_scraper", "NoDriverScraper"),
    ("gpt_researcher.scraper.pymupdf.pymupdf", "PyMuPDFScraper"),
    (
        "gpt_researcher.scraper.web_base_loader.web_base_loader",
        "WebBaseLoaderScraper",
    ),
    ("gpt_researcher.scraper.scraper", "Scraper"),
    (
        "gpt_researcher.scraper.browser.processing.scrape_skills",
        "ArxivRetriever",
    ),
)


def _resolve_external_targets() -> dict[tuple[str, str], object]:
    resolved: dict[tuple[str, str], object] = {}
    for module_name, entry_name in FAIL_FAST_EXTERNAL_TARGETS:
        module = sys.modules.get(module_name)
        if module is None:
            raise AssertionError(f"expected loaded module: {module_name}")
        try:
            resolved[(module_name, entry_name)] = getattr(module, entry_name)
        except AttributeError as error:
            raise AssertionError(
                f"expected external entry: {module_name}.{entry_name}"
            ) from error
    return resolved


FAIL_FAST_EXTERNAL_ORIGINALS = _resolve_external_targets()


@contextmanager
def _patched_external_targets(replacement: object):
    originals = _resolve_external_targets()
    try:
        for (module_name, entry_name) in FAIL_FAST_EXTERNAL_TARGETS:
            setattr(sys.modules[module_name], entry_name, replacement)
        yield
    finally:
        for (module_name, entry_name), original in originals.items():
            setattr(sys.modules[module_name], entry_name, original)


@pytest_asyncio.fixture(autouse=True)
async def _fail_fast_external_io(monkeypatch: pytest.MonkeyPatch):
    def blocked(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("external I/O is forbidden in workflow tests")

    blocked._academic_workflow_fail_fast = True  # type: ignore[attr-defined]
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(asyncio.BaseEventLoop, "create_connection", blocked)
    monkeypatch.setattr(subprocess, "Popen", blocked)
    monkeypatch.setattr(subprocess, "run", blocked)
    monkeypatch.setattr(subprocess, "call", blocked)
    monkeypatch.setattr(subprocess, "check_call", blocked)
    monkeypatch.setattr(subprocess, "check_output", blocked)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", blocked)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", blocked)

    real_open = builtins.open
    real_os_open = os.open

    def guarded_open(file: object, mode: str = "r", *args: object, **kwargs: object):
        if any(marker in mode for marker in ("w", "a", "x", "+")):
            blocked(file, mode)
        return real_open(file, mode, *args, **kwargs)

    def guarded_os_open(path: object, flags: int, *args: object, **kwargs: object):
        write_flags = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC
        if flags & write_flags:
            blocked(path, flags)
        return real_os_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded_open)
    monkeypatch.setattr(os, "open", guarded_os_open)
    monkeypatch.setattr(Path, "write_text", blocked)
    monkeypatch.setattr(Path, "write_bytes", blocked)

    requests_module = sys.modules.get("requests.sessions")
    if requests_module is not None:
        monkeypatch.setattr(requests_module.Session, "request", blocked)
    httpx_module = sys.modules.get("httpx")
    if httpx_module is not None:
        monkeypatch.setattr(httpx_module.Client, "request", blocked)
        monkeypatch.setattr(httpx_module.AsyncClient, "request", blocked)
    aiohttp_module = sys.modules.get("aiohttp.client")
    if aiohttp_module is not None:
        monkeypatch.setattr(aiohttp_module.ClientSession, "_request", blocked)

    assert _resolve_external_targets() == FAIL_FAST_EXTERNAL_ORIGINALS
    with _patched_external_targets(blocked):
        yield
    assert _resolve_external_targets() == FAIL_FAST_EXTERNAL_ORIGINALS


def _request(**changes: Any) -> AcademicWorkflowRequest:
    data: dict[str, Any] = {
        "workflow_mode": "academic_langgraph",
        "workflow_id": "workflow-1",
        "thread_id": "thread-1",
        "run_id": "run-1",
        "query": "Deterministic research",
        "report_type": "research_report",
        "report_source": "web",
        "tone": "objective",
        "language": "en",
        "source_urls": (),
        "document_urls": (),
        "query_domains": (),
        "max_search_results": None,
        "report_mode": "stem_literature_review",
        "report_locale": "zh-CN",
    }
    data.update(changes)
    return AcademicWorkflowRequest(**data)


def _identity(**changes: Any) -> AcademicWorkflowIdentity:
    data = {
        "workflow_id": "workflow-1",
        "thread_id": "thread-1",
        "run_id": "run-1",
    }
    data.update(changes)
    return AcademicWorkflowIdentity(**data)


def _plan(**changes: Any) -> WorkflowTopicPlan:
    data: dict[str, Any] = {
        "topic_plan_id": "topic-plan:000001",
        "workflow_id": "workflow-1",
        "run_id": "run-1",
        "attempt": 1,
        "research_topic": "Deterministic research",
        "research_questions": ("What is deterministic?",),
    }
    data.update(changes)
    return WorkflowTopicPlan(**data)


def _evidence(**changes: Any) -> WorkflowResearchEvidence:
    source = WorkflowEvidenceSource(
        source_id="evidence-source:000001",
        order=1,
        title="Source",
        url="https://example.test/source",
        candidate_id="candidate-1",
    )
    data: dict[str, Any] = {
        "evidence_id": "evidence:000001",
        "topic_plan_id": "topic-plan:000001",
        "attempt": 1,
        "context_blocks": ("Bounded evidence.",),
        "sources": (source,),
    }
    data.update(changes)
    return WorkflowResearchEvidence(**data)


def _outline(**changes: Any) -> WorkflowOutline:
    profile = _get_report_profile("stem_literature_review")
    assert profile is not None
    sections = tuple(
        WorkflowOutlineSection(
            section_id=f"section:{order:06d}",
            order=order,
            title=title,
            brief=f"Brief {order}",
            section_role=role,
        )
        for order, (role, title) in enumerate(profile, start=1)
    )
    data: dict[str, Any] = {
        "outline_id": "outline:000001",
        "evidence_id": "evidence:000001",
        "attempt": 1,
        "title": "Outline",
        "sections": sections,
        "report_mode": "stem_literature_review",
        "report_locale": "zh-CN",
    }
    data.update(changes)
    return WorkflowOutline(**data)


class FakeAdapter(AcademicWritingAdapter):
    def __init__(
        self, *, workflow_id: str = "workflow-1", run_id: str = "run-1"
    ) -> None:
        self.plan_result: object = _plan(workflow_id=workflow_id, run_id=run_id)
        self.evidence_result: object = _evidence()
        self.outline_result: object = _outline()
        self.raise_at: str | None = None
        self.manual_cancel_at: str | None = None
        self.block_at: str | None = None
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.calls: list[str] = []

    async def _before_return(self, name: str) -> None:
        self.calls.append(name)
        if self.raise_at == name:
            raise RuntimeError(SECRET)
        if self.manual_cancel_at == name:
            raise asyncio.CancelledError(SECRET)
        if self.block_at == name:
            self.entered.set()
            await self.release.wait()

    async def plan_topic(
        self, request: AcademicWorkflowRequest
    ) -> WorkflowTopicPlan | AdapterFailure:
        await self._before_return("topic_planner")
        return self.plan_result  # type: ignore[return-value]

    async def collect_research_evidence(
        self,
        request: AcademicWorkflowRequest,
        topic_plan: WorkflowTopicPlan,
    ) -> WorkflowResearchEvidence | AdapterFailure:
        await self._before_return("research_evidence")
        return self.evidence_result  # type: ignore[return-value]

    async def write_outline(
        self,
        request: AcademicWorkflowRequest,
        topic_plan: WorkflowTopicPlan,
        evidence: WorkflowResearchEvidence,
    ) -> WorkflowOutline | AdapterFailure:
        await self._before_return("outline_writer")
        return self.outline_result  # type: ignore[return-value]


def _composition(verdict: str = "supported") -> WorkflowAcademicDraftComposition:
    outline = _outline()
    section_ids = tuple(section.section_id for section in outline.sections)
    citations = tuple(("evidence-source:000001",) for _ in section_ids)
    drafts = tuple(
        WorkflowSectionDraft(
            outline_id=outline.outline_id,
            section_id=section_id,
            attempt=1,
            content="Claim [[cite:evidence-source:000001]]",
        )
        for section_id in section_ids
    )
    gate = WorkflowCitationEvidenceGateResult(
        outline_id=outline.outline_id,
        section_ids=section_ids,
        cited_source_ids_by_section=citations,
        attempt=1,
    )
    if verdict == "supported":
        section_disposition = "ready"
        issues: tuple[str, ...] = ()
    elif verdict == "uncertain":
        section_disposition = "needs_human_review"
        issues = ("insufficient_evidence",)
    else:
        section_disposition = "blocked"
        issues = ("possible_contradiction",)
    reviews = tuple(
        WorkflowSectionCitationReview(
            outline_id=outline.outline_id,
            section_id=section_id,
            cited_source_ids=cited,
            verdict=verdict,
            issues=issues,
            rationale="Bounded model opinion.",
            attempt=1,
        )
        for section_id, cited in zip(section_ids, citations, strict=True)
    )
    disposition = WorkflowCitationReviewDisposition(
        outline_id=outline.outline_id,
        section_ids=section_ids,
        section_dispositions=tuple(section_disposition for _ in section_ids),
        disposition=section_disposition,
        attempt=1,
    )
    merged = None
    referenced = None
    if verdict == "supported":
        merged = WorkflowMergedDraft(
            outline_id=outline.outline_id,
            section_ids=section_ids,
            attempt=1,
            content="Merged",
        )
        referenced = WorkflowReferencedDraft(
            outline_id=outline.outline_id,
            section_ids=section_ids,
            reference_source_ids=("evidence-source:000001",),
            attempt=1,
            content="Merged\n\n## References\n\nopaque",
        )
    return WorkflowAcademicDraftComposition(
        drafts=drafts,
        gate_result=gate,
        reviews=reviews,
        disposition=disposition,
        merged_draft=merged,
        referenced_draft=referenced,
    )


class FakeComposer:
    def __init__(
        self,
        result: object | None = None,
        *,
        error: BaseException | None = None,
        costs: list[str] | None = None,
    ) -> None:
        self.result = _composition() if result is None else result
        self.error = error
        self.calls = 0
        self.states: list[object] = []
        self.costs = costs

    async def compose(self, state: object) -> object:
        self.calls += 1
        self.states.append(state)
        if self.costs is not None:
            self.costs.extend(("writer:section-1", "reviewer:section-1"))
        if self.error is not None:
            raise self.error
        return self.result


def _event_projection(state: object) -> list[tuple[str, str | None]]:
    return [(event.event_type, event.node_id) for event in state.events]  # type: ignore[attr-defined]


def _assert_traceback_surface_has_no_sentinel(
    error: BaseException,
    sentinel: str,
    *,
    forbidden_identity: object | None = None,
) -> list[str]:
    seen: set[int] = set()

    def visit(value: object) -> None:
        if forbidden_identity is not None:
            assert value is not forbidden_identity
        if id(value) in seen:
            return
        seen.add(id(value))

        value_type = type(value)
        if value_type is str:
            assert sentinel not in value
        elif value_type is bytes:
            assert sentinel.encode("utf-8") not in value
        elif value_type is dict:
            for key, item in value.items():
                visit(key)
                visit(item)
        elif value_type in (list, tuple, set, frozenset):
            for item in value:
                visit(item)
        elif value_type is types.FunctionType:
            for cell in value.__closure__ or ():
                for item in gc.get_referents(cell):
                    visit(item)
        elif isinstance(
            value,
            (
                types.ModuleType,
                type,
                types.CodeType,
                types.FrameType,
                types.TracebackType,
            ),
        ):
            return
        else:
            # Inspect the actual referent graph without invoking the object's
            # repr, attribute lookup, descriptors, or properties.
            for item in gc.get_referents(value):
                visit(item)

    frame_names: list[str] = []
    if forbidden_identity is None:
        visit(error)
    current = error.__traceback__
    while current is not None:
        frame_names.append(current.tb_frame.f_code.co_name)
        if (
            forbidden_identity is None
            or current.tb_frame.f_globals.get("__name__", "").startswith(
                "gpt_researcher.workflows.academic_writing"
            )
        ):
            for value in current.tb_frame.f_locals.values():
                visit(value)
        if current.tb_frame.f_code.co_name in {
            "_topic_planner",
            "_research_evidence",
            "_outline_writer",
        }:
            assert "result" not in current.tb_frame.f_locals
        current = current.tb_next
    assert "_prepare_transition" not in frame_names
    return frame_names


def test_traceback_walker_does_not_execute_dynamic_object_access() -> None:
    class HostileSurface:
        def __init__(self) -> None:
            object.__setattr__(self, "nested", {"safe": ["value"]})

        def __repr__(self) -> str:
            raise AssertionError("repr must not execute")

        def __getattribute__(self, name: str) -> object:
            if name == "__dict__":
                raise AssertionError("dynamic attribute access must not execute")
            return object.__getattribute__(self, name)

        @property
        def explosive_property(self) -> object:
            raise AssertionError("property must not execute")

    def raise_fixed_error() -> None:
        hostile = HostileSurface()
        assert type(hostile) is HostileSurface
        raise ExecutionError()

    with pytest.raises(ExecutionError) as caught:
        raise_fixed_error()

    _assert_traceback_surface_has_no_sentinel(caught.value, SECRET)


SUCCESS_EVENTS = [
    ("node_started", "topic_planner"),
    ("node_completed", "topic_planner"),
    ("node_started", "research_evidence"),
    ("node_completed", "research_evidence"),
    ("node_started", "outline_writer"),
    ("node_completed", "outline_writer"),
]


@pytest.mark.asyncio
async def test_success_is_deterministic_and_threads_are_independent() -> None:
    saver = InMemorySaver()
    first = await start_academic_workflow(
        _request(), FakeAdapter(), checkpointer=saver
    )
    second = await start_academic_workflow(
        _request(workflow_id="workflow-2", thread_id="thread-2", run_id="run-2"),
        FakeAdapter(workflow_id="workflow-2", run_id="run-2"),
        checkpointer=saver,
    )

    assert (first.phase, first.status) == ("outline_ready", "running")
    assert _event_projection(first) == SUCCESS_EVENTS
    assert [event.order for event in first.events] == list(range(1, 7))
    assert [event.event_id for event in first.events] == [
        f"event:{order:06d}" for order in range(1, 7)
    ]
    assert second.workflow_id == "workflow-2"
    assert second.events == first.events


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failed_node", "code", "expected_phase", "expected_calls", "event_count"),
    [
        ("topic_planner", "topic_planning_failed", "initialized", 1, 2),
        ("research_evidence", "research_evidence_failed", "topic_planned", 2, 4),
        ("outline_writer", "outline_writing_failed", "evidence_collected", 3, 6),
    ],
)
async def test_adapter_failure_is_a_safe_terminal_return(
    failed_node: str,
    code: str,
    expected_phase: str,
    expected_calls: int,
    event_count: int,
) -> None:
    saver = InMemorySaver()
    adapter = FakeAdapter()
    setattr(adapter, {
        "topic_planner": "plan_result",
        "research_evidence": "evidence_result",
        "outline_writer": "outline_result",
    }[failed_node], AdapterFailure(code=code))

    state = await start_academic_workflow(
        _request(), adapter, checkpointer=saver
    )
    config = {"configurable": {"thread_id": "thread-1"}}
    snapshot = await (
        # A fresh facade graph must read the same injected saver thread.
        _compiled_for_test(adapter, saver).aget_state(config)
    )

    assert (state.phase, state.status) == (expected_phase, "failed")
    assert len(adapter.calls) == expected_calls
    assert len(state.errors) == 1
    assert state.errors[0].failed_node_id == failed_node
    assert state.errors[0].code == code
    assert len(state.events) == event_count
    assert state.events[-2].event_type == "node_started"
    assert state.events[-1].event_type == "workflow_failed"
    assert state.events[-1].node_id == failed_node
    assert snapshot.next == ()
    with pytest.raises(ThreadProtocolError, match="^academic workflow thread is not resumable$"):
        await resume_academic_workflow(
            _identity(), FakeAdapter(), checkpointer=saver
        )


def _compiled_for_test(adapter: AcademicWritingAdapter, saver: InMemorySaver):
    # Importing the private builder explicitly is test-only; no public function
    # returns the compiled graph.
    from gpt_researcher.workflows.academic_writing.graph import _build_graph

    return _build_graph(
        adapter,
        saver,
        nodes_module._composer_slot(FakeComposer()),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failed_node", "expected_phase", "event_count", "expected_next"),
    [
        ("topic_planner", "initialized", 0, ("topic_planner",)),
        ("research_evidence", "topic_planned", 2, ("research_evidence",)),
        ("outline_writer", "evidence_collected", 4, ("outline_writer",)),
        (
            "academic_draft_composer",
            "outline_approved",
            8,
            ("academic_draft_composer",),
        ),
    ],
)
async def test_raw_exception_is_fixed_safe_and_natively_resumable(
    failed_node: str,
    expected_phase: str,
    event_count: int,
    expected_next: tuple[str, ...],
) -> None:
    saver = InMemorySaver()
    crashing = FakeAdapter()
    costs: list[str] = []
    composer_target: object | None = None

    if failed_node == "academic_draft_composer":
        paused = await start_academic_workflow(
            _request(), crashing, checkpointer=saver
        )
        class ClosureFailingComposer(FakeComposer):
            pass

        failing_composer = ClosureFailingComposer(
            error=RuntimeError(SECRET), costs=costs
        )
        composer_target = failing_composer

        async def closure_failure(self: object, state: object) -> object:
            assert self is failing_composer
            failing_composer.calls += 1
            failing_composer.states.append(state)
            if failing_composer.costs is not None:
                failing_composer.costs.extend(
                    ("writer:section-1", "reviewer:section-1")
                )
            raise RuntimeError(SECRET)

        ClosureFailingComposer.compose = closure_failure  # type: ignore[method-assign]
        with pytest.raises(ExecutionError) as caught:
            await submit_academic_outline_decision(
                AcademicOutlineDecisionCommand(
                    schema_version="1",
                    workflow_id=paused.workflow_id,
                    thread_id=paused.thread_id,
                    run_id=paused.run_id,
                    outline_id=paused.outline.outline_id,
                    outline_digest=state_module._outline_digest(paused.outline),
                    decision="approve",
                    actor_assertion="human approval",
                ),
                crashing,
                checkpointer=saver,
                composer=failing_composer,
            )
        assert failing_composer.calls == 1
    else:
        crashing.raise_at = failed_node
        with pytest.raises(ExecutionError) as caught:
            await start_academic_workflow(_request(), crashing, checkpointer=saver)

    error = caught.value
    assert str(error) == "academic workflow execution failed"
    assert error.__cause__ is None
    assert error.__context__ is None
    assert error.__suppress_context__ is False
    rendered = "".join(traceback.format_exception(error))
    assert SECRET not in str(error)
    assert SECRET not in repr(error)
    assert SECRET not in rendered
    _assert_traceback_surface_has_no_sentinel(
        error,
        SECRET,
        forbidden_identity=composer_target,
    )

    graph = _compiled_for_test(crashing, saver)
    snapshot = await graph.aget_state({"configurable": {"thread_id": "thread-1"}})
    checkpoint_state = (
        graph_module._safe_persistent_restore(snapshot.values)
        if failed_node == "academic_draft_composer"
        else restore_workflow_state(snapshot.values)
    )
    assert checkpoint_state.phase == expected_phase
    assert checkpoint_state.status == "running"
    assert len(checkpoint_state.events) == event_count
    assert all(event.node_id != failed_node for event in checkpoint_state.events)
    assert snapshot.next == expected_next
    assert SECRET not in repr(snapshot.tasks)
    assert SECRET not in repr(snapshot.metadata)

    retry_composer = FakeComposer(costs=costs)
    recovered = await resume_academic_workflow(
        _identity(),
        FakeAdapter(),
        checkpointer=saver,
        composer=retry_composer if failed_node == "academic_draft_composer" else None,
    )
    if failed_node == "academic_draft_composer":
        assert (recovered.phase, recovered.status) == ("draft_ready", "completed")
        assert retry_composer.calls == 1
        assert costs == [
            "writer:section-1",
            "reviewer:section-1",
            "writer:section-1",
            "reviewer:section-1",
        ]
        return
    assert _event_projection(recovered) == SUCCESS_EVENTS
    clean = await start_academic_workflow(
        _request(), FakeAdapter(), checkpointer=InMemorySaver()
    )
    assert recovered == clean


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_result", [object(), {"unexpected": "mapping"}])
async def test_invalid_adapter_response_is_fixed_invariant_and_resumable(
    bad_result: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saver = InMemorySaver()
    adapter = FakeAdapter()
    adapter.evidence_result = bad_result

    with pytest.raises(InvariantError) as caught:
        await start_academic_workflow(_request(), adapter, checkpointer=saver)
    assert str(caught.value) == "academic workflow invariant violation"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert caught.value.__suppress_context__ is False

    recovered = await resume_academic_workflow(
        _identity(), FakeAdapter(), checkpointer=saver
    )
    assert _event_projection(recovered) == SUCCESS_EVENTS

    class BlockingComposer(FakeComposer):
        def __init__(self) -> None:
            super().__init__()
            self.entered = asyncio.Event()

    blocking = BlockingComposer()

    async def closure_compose(self: object, state: object) -> object:
        assert self is blocking
        blocking.calls += 1
        blocking.states.append(state)
        blocking.entered.set()
        await asyncio.Event().wait()

    BlockingComposer.compose = closure_compose  # type: ignore[method-assign]
    real_composer_slot = graph_module._composer_slot
    recorded_slots: list[object] = []

    def recording_slot(choice: object) -> object:
        slot = real_composer_slot(choice)
        if choice is not None:
            recorded_slots.append(slot)
        return slot

    monkeypatch.setattr(graph_module, "_composer_slot", recording_slot)
    command = AcademicOutlineDecisionCommand(
        schema_version="1",
        workflow_id=recovered.workflow_id,
        thread_id=recovered.thread_id,
        run_id=recovered.run_id,
        outline_id=recovered.outline.outline_id,
        outline_digest=state_module._outline_digest(recovered.outline),
        decision="approve",
        actor_assertion="human approval",
    )
    composer_task = asyncio.create_task(
        submit_academic_outline_decision(
            command,
            FakeAdapter(),
            checkpointer=saver,
            composer=blocking,
        )
    )
    await blocking.entered.wait()
    composer_task.cancel("COMPOSER-CANCELLED")
    with pytest.raises(asyncio.CancelledError) as composer_cancel:
        await composer_task
    assert composer_cancel.value.args == ("COMPOSER-CANCELLED",)
    assert len(recorded_slots) == 1
    assert recorded_slots[0].is_empty()
    _assert_traceback_surface_has_no_sentinel(
        composer_cancel.value,
        "FORBIDDEN-COMPOSER-SENSITIVE-TEXT",
        forbidden_identity=blocking,
    )
    pending = await _compiled_for_test(FakeAdapter(), saver).aget_state(
        {"configurable": {"thread_id": "thread-1"}}
    )
    pending_state = graph_module._safe_persistent_restore(pending.values)
    assert (pending_state.phase, pending_state.status, pending_state.outcome) == (
        "outline_approved",
        "running",
        None,
    )
    assert pending.next == ("academic_draft_composer",)

    if type(bad_result) is object:
        valid_composition = _composition()
        assert valid_composition.referenced_draft is not None
        referenced = valid_composition.referenced_draft
        outcome_sentinel = "TERMINAL-OUTCOME-REACHABILITY-SENTINEL"
        sentinel_referenced = WorkflowReferencedDraft(
            outline_id=referenced.outline_id,
            section_ids=referenced.section_ids,
            reference_source_ids=referenced.reference_source_ids,
            attempt=referenced.attempt,
            content=referenced.content + outcome_sentinel,
        )
        transition_composition = WorkflowAcademicDraftComposition(
            drafts=valid_composition.drafts,
            gate_result=valid_composition.gate_result,
            reviews=valid_composition.reviews,
            disposition=valid_composition.disposition,
            merged_draft=valid_composition.merged_draft,
            referenced_draft=sentinel_referenced,
        )
        terminal_targets: list[object] = []

        def fail_after_terminal(terminal: object) -> object:
            terminal_targets.append(terminal)
            raise RuntimeError("transition-validation-failure")

        transition_composer = FakeComposer(result=transition_composition)
        with monkeypatch.context() as scoped:
            scoped.setattr(
                nodes_module,
                "_validated_persistent_transition",
                fail_after_terminal,
            )
            with pytest.raises(ExecutionError) as transition_error:
                await resume_academic_workflow(
                    _identity(),
                    FakeAdapter(),
                    checkpointer=saver,
                    composer=transition_composer,
                )
        assert len(terminal_targets) == 1
        assert transition_composer.calls == 1
        assert recorded_slots[-1].is_empty()
        assert str(transition_error.value) == "academic workflow execution failed"
        assert transition_error.value.__cause__ is None
        assert transition_error.value.__context__ is None
        _assert_traceback_surface_has_no_sentinel(
            transition_error.value,
            outcome_sentinel,
            forbidden_identity=terminal_targets[0],
        )
    invalid_composer = FakeComposer(result=bad_result)
    with pytest.raises(InvariantError) as composer_error:
        await resume_academic_workflow(
            _identity(),
            FakeAdapter(),
            checkpointer=saver,
            composer=invalid_composer,
        )
    assert str(composer_error.value) == "academic workflow invariant violation"
    assert invalid_composer.calls == 1
    assert len(recorded_slots) == (
        3 if type(bad_result) is object else 2
    )
    assert all(slot.is_empty() for slot in recorded_slots)
    pending = await _compiled_for_test(FakeAdapter(), saver).aget_state(
        {"configurable": {"thread_id": "thread-1"}}
    )
    pending_state = graph_module._safe_persistent_restore(pending.values)
    assert (pending_state.phase, pending_state.status, pending_state.outcome) == (
        "outline_approved",
        "running",
        None,
    )
    assert pending.next == ("academic_draft_composer",)


@pytest.mark.asyncio
async def test_invariant_error_traceback_does_not_retain_invalid_response() -> None:
    saver = InMemorySaver()

    class InvalidResponseAdapter(FakeAdapter):
        async def plan_topic(
            self, request: AcademicWorkflowRequest
        ) -> WorkflowTopicPlan | AdapterFailure:
            await self._before_return("topic_planner")
            return {  # type: ignore[return-value]
                "invalid": {"nested": [INVARIANT_INPUT_SECRET]}
            }

    adapter = InvalidResponseAdapter()

    with pytest.raises(InvariantError) as caught:
        await start_academic_workflow(_request(), adapter, checkpointer=saver)

    error = caught.value
    _assert_traceback_surface_has_no_sentinel(error, INVARIANT_INPUT_SECRET)
    rendered_with_locals = "".join(
        traceback.TracebackException.from_exception(
            error, capture_locals=True
        ).format()
    )
    assert INVARIANT_INPUT_SECRET not in rendered_with_locals

    snapshot = await _compiled_for_test(adapter, saver).aget_state(
        {"configurable": {"thread_id": "thread-1"}}
    )
    assert INVARIANT_INPUT_SECRET not in repr(snapshot.tasks)
    assert INVARIANT_INPUT_SECRET not in repr(snapshot.metadata)
    assert snapshot.next == ("topic_planner",)


@pytest.mark.asyncio
async def test_wrong_adapter_failure_code_is_invariant_not_business_failure() -> None:
    saver = InMemorySaver()
    adapter = FakeAdapter()
    adapter.plan_result = AdapterFailure(code="research_evidence_failed")
    with pytest.raises(InvariantError) as caught:
        await start_academic_workflow(_request(), adapter, checkpointer=saver)
    assert str(caught.value) == "academic workflow invariant violation"

    snapshot = await _compiled_for_test(adapter, saver).aget_state(
        {"configurable": {"thread_id": "thread-1"}}
    )
    state = restore_workflow_state(snapshot.values)
    assert state.status == "running"
    assert state.errors == ()
    assert state.events == ()
    assert snapshot.next == ("topic_planner",)


@pytest.mark.asyncio
async def test_external_task_cancellation_preserves_checkpoint_and_recovers() -> None:
    saver = InMemorySaver()
    adapter = FakeAdapter()
    adapter.block_at = "research_evidence"
    task = asyncio.create_task(
        start_academic_workflow(_request(), adapter, checkpointer=saver)
    )
    await adapter.entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    snapshot = await _compiled_for_test(adapter, saver).aget_state(
        {"configurable": {"thread_id": "thread-1"}}
    )
    state = restore_workflow_state(snapshot.values)
    assert state.phase == "topic_planned"
    assert state.status == "running"
    assert len(state.events) == 2
    assert snapshot.next == ("research_evidence",)

    recovered = await resume_academic_workflow(
        _identity(), FakeAdapter(), checkpointer=saver
    )
    assert _event_projection(recovered) == SUCCESS_EVENTS


@pytest.mark.asyncio
async def test_adapter_constructed_cancelled_error_is_an_invariant() -> None:
    saver = InMemorySaver()
    adapter = FakeAdapter()
    adapter.manual_cancel_at = "topic_planner"
    with pytest.raises(InvariantError) as caught:
        await start_academic_workflow(_request(), adapter, checkpointer=saver)
    assert str(caught.value) == "academic workflow invariant violation"

    snapshot = await _compiled_for_test(adapter, saver).aget_state(
        {"configurable": {"thread_id": "thread-1"}}
    )
    state = restore_workflow_state(snapshot.values)
    assert state.events == ()
    assert snapshot.next == ("topic_planner",)


@pytest.mark.asyncio
async def test_start_and_resume_thread_guards_use_fixed_priority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_outline_digest = state_module._outline_digest
    digest_calls: list[tuple[str, str]] = []
    stage = "approval_create"

    def counting_outline_digest(outline: WorkflowOutline) -> str:
        assert state_module._outline_digest is counting_outline_digest
        assert nodes_module._outline_digest is counting_outline_digest
        assert graph_module._outline_digest is counting_outline_digest
        digest = real_outline_digest(outline)
        digest_calls.append((stage, digest))
        return digest

    monkeypatch.setattr(state_module, "_outline_digest", counting_outline_digest)
    monkeypatch.setattr(nodes_module, "_outline_digest", counting_outline_digest)
    monkeypatch.setattr(graph_module, "_outline_digest", counting_outline_digest)

    saver = InMemorySaver()
    paused = await start_academic_workflow(
        _request(), FakeAdapter(), checkpointer=saver
    )
    assert [name for name, _digest in digest_calls] == [
        "approval_create",
        "approval_create",
    ]

    with pytest.raises(ThreadProtocolError, match="^academic workflow thread already exists$"):
        await start_academic_workflow(_request(), FakeAdapter(), checkpointer=saver)
    with pytest.raises(
        ThreadProtocolError,
        match="^academic workflow identity does not match checkpoint$",
    ):
        await start_academic_workflow(
            _request(workflow_id="different", run_id="different"),
            FakeAdapter(),
            checkpointer=saver,
        )
    with pytest.raises(
        ThreadProtocolError,
        match="^academic workflow identity does not match checkpoint$",
    ):
        await resume_academic_workflow(
            _identity(workflow_id="different", run_id="different"),
            FakeAdapter(),
            checkpointer=saver,
        )
    stage = "checkpoint_restore"
    with pytest.raises(
        ThreadProtocolError,
        match="^academic outline approval decision is required$",
    ):
        await resume_academic_workflow(
            _identity(), FakeAdapter(), checkpointer=saver
        )
    assert [name for name, _digest in digest_calls].count("checkpoint_restore") == 1

    with pytest.raises(
        ThreadProtocolError,
        match="^academic workflow checkpoint does not exist$",
    ):
        await resume_academic_workflow(
            _identity(workflow_id="unused", thread_id="unused", run_id="unused"),
            FakeAdapter(),
            checkpointer=saver,
        )

    command = AcademicOutlineDecisionCommand(
        schema_version="1",
        workflow_id=paused.workflow_id,
        thread_id=paused.thread_id,
        run_id=paused.run_id,
        outline_id="outline:000001",
        outline_digest=digest_calls[0][1],
        decision="approve",
        actor_assertion="human approval",
    )
    stage = "approval_resume"
    composer = FakeComposer()
    approved = await submit_academic_outline_decision(
        command,
        FakeAdapter(),
        checkpointer=saver,
        composer=composer,
    )
    assert (approved.phase, approved.status) == ("draft_ready", "completed")
    assert composer.calls == 1
    assert [name for name, _digest in digest_calls] == [
        "approval_create",
        "approval_create",
        "checkpoint_restore",
    ] + ["approval_resume"] * 16

    stage = "checkpoint_restore"
    terminal_snapshot = await _compiled_for_test(
        FakeAdapter(), saver
    ).aget_state({"configurable": {"thread_id": "thread-1"}})
    restored = graph_module._safe_persistent_restore(terminal_snapshot.values)
    assert restored == approved
    assert [name for name, _digest in digest_calls].count("checkpoint_restore") == 2

    stage = "retry"
    with pytest.raises(
        OutlineDecisionProtocolError,
        match="^academic outline decision has already been committed$",
    ):
        await submit_academic_outline_decision(
            command,
            FakeAdapter(),
            checkpointer=saver,
        )
    assert [name for name, _digest in digest_calls].count("retry") == 1
    assert len({digest for _name, digest in digest_calls}) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("pending", "error_type", "message"),
    [
        ((), ThreadProtocolError, "academic workflow thread is not resumable"),
        (("outline_writer",), InvariantError, "academic workflow invariant violation"),
        (
            ("research_evidence", "outline_writer"),
            InvariantError,
            "academic workflow invariant violation",
        ),
        (
            ("outline_writer", "research_evidence"),
            InvariantError,
            "academic workflow invariant violation",
        ),
    ],
)
async def test_resume_rejects_empty_and_phase_mismatched_next(
    monkeypatch: pytest.MonkeyPatch,
    pending: tuple[str, ...],
    error_type: type[Exception],
    message: str,
) -> None:
    import gpt_researcher.workflows.academic_writing.graph as graph_module

    saver = InMemorySaver()
    crashing = FakeAdapter()
    crashing.raise_at = "research_evidence"
    with pytest.raises(ExecutionError):
        await start_academic_workflow(_request(), crashing, checkpointer=saver)
    real_snapshot = await _compiled_for_test(crashing, saver).aget_state(
        {"configurable": {"thread_id": "thread-1"}}
    )

    class FakeCompiledGraph:
        invoked = False

        async def aget_state(self, config: object) -> object:
            return SimpleNamespace(
                created_at=real_snapshot.created_at,
                values=real_snapshot.values,
                next=pending,
            )

        async def ainvoke(self, value: object, *, config: object) -> object:
            self.invoked = True
            raise AssertionError("guarded resume must not invoke the graph")

    fake_graph = FakeCompiledGraph()
    monkeypatch.setattr(
        graph_module,
        "_build_graph",
        lambda adapter, checkpointer, composer_slot: fake_graph,
    )
    with pytest.raises(error_type) as caught:
        await resume_academic_workflow(
            _identity(), FakeAdapter(), checkpointer=saver
        )
    assert str(caught.value) == message
    assert fake_graph.invoked is False


def test_fail_fast_fixture_blocks_external_io_without_real_side_effects() -> None:
    with pytest.raises(AssertionError, match="external I/O is forbidden"):
        socket.socket().connect(("127.0.0.1", 9))
    with pytest.raises(AssertionError, match="external I/O is forbidden"):
        subprocess.run(["forbidden"])
    with pytest.raises(AssertionError, match="external I/O is forbidden"):
        Path("forbidden-output").write_text("forbidden", encoding="utf-8")


def test_fail_fast_fixture_patches_real_loaded_external_entry_points() -> None:
    for target, original in FAIL_FAST_EXTERNAL_ORIGINALS.items():
        module_name, entry_name = target
        entry = getattr(sys.modules[module_name], entry_name)
        assert entry is not original
        assert getattr(entry, "_academic_workflow_fail_fast", False) is True
        with pytest.raises(AssertionError, match="external I/O is forbidden"):
            entry()


def _assert_no_environment_access(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(
            alias.name == "os" for alias in node.names
        ):
            raise AssertionError(f"environment-capable os import in {path}")
        if isinstance(node, ast.ImportFrom) and node.module == "os":
            raise AssertionError(f"environment-capable os import in {path}")


@pytest.mark.parametrize("fail_after_exec", [False, True])
def test_new_module_imports_have_no_differential_side_effects(
    monkeypatch: pytest.MonkeyPatch,
    fail_after_exec: bool,
) -> None:
    from langgraph.graph import StateGraph

    observed: list[str] = []

    def side_effect(name: str):
        def blocked(*_args: object, **_kwargs: object) -> None:
            observed.append(name)
            raise AssertionError(f"import side effect is forbidden: {name}")

        return blocked

    monkeypatch.setattr(StateGraph, "compile", side_effect("graph compile"))
    monkeypatch.setattr(InMemorySaver, "__init__", side_effect("saver creation"))
    monkeypatch.setattr(asyncio, "create_task", side_effect("task creation"))
    monkeypatch.setattr(threading.Thread, "start", side_effect("thread start"))
    def logger_snapshot() -> dict[str, tuple[logging.Handler, ...]]:
        snapshot = {"root": tuple(logging.getLogger().handlers)}
        for name, logger in logging.Logger.manager.loggerDict.items():
            if name.startswith("gpt_researcher") and isinstance(logger, logging.Logger):
                snapshot[name] = tuple(logger.handlers)
        return snapshot

    loggers_before = logger_snapshot()
    environment_before = dict(os.environ)

    root = Path(__file__).parents[1]
    module_paths = (
        (
            "gpt_researcher.workflows",
            root / "gpt_researcher" / "workflows" / "__init__.py",
        ),
        (
            "gpt_researcher.workflows.academic_writing",
            root
            / "gpt_researcher"
            / "workflows"
            / "academic_writing"
            / "__init__.py",
        ),
        (
            "gpt_researcher.workflows.academic_writing.state",
            root / "gpt_researcher" / "workflows" / "academic_writing" / "state.py",
        ),
        (
            "gpt_researcher.workflows.academic_writing.adapters",
            root
            / "gpt_researcher"
            / "workflows"
            / "academic_writing"
            / "adapters.py",
        ),
        (
            "gpt_researcher.workflows.academic_writing.nodes",
            root / "gpt_researcher" / "workflows" / "academic_writing" / "nodes.py",
        ),
        (
            "gpt_researcher.workflows.academic_writing.graph",
            root / "gpt_researcher" / "workflows" / "academic_writing" / "graph.py",
        ),
    )
    module_names = tuple(name for name, _path in module_paths)
    for _name, path in module_paths:
        _assert_no_environment_access(path)

    original_modules = {name: sys.modules[name] for name in module_names}
    missing = object()
    parent_bindings = (
        ("gpt_researcher", "workflows"),
        ("gpt_researcher.workflows", "academic_writing"),
        ("gpt_researcher.workflows.academic_writing", "state"),
        ("gpt_researcher.workflows.academic_writing", "adapters"),
        ("gpt_researcher.workflows.academic_writing", "nodes"),
        ("gpt_researcher.workflows.academic_writing", "graph"),
    )
    original_bindings = {
        (parent_name, attribute): getattr(
            sys.modules[parent_name], attribute, missing
        )
        for parent_name, attribute in parent_bindings
    }

    class _LoaderAfterExecFailure(RuntimeError):
        pass

    reloaded: dict[str, object] = {}
    try:
        try:
            for name in reversed(module_names):
                assert sys.modules.pop(name) is original_modules[name]
            for name in module_names:
                reloaded[name] = importlib.import_module(name)
                assert reloaded[name] is sys.modules[name]
                assert reloaded[name] is not original_modules[name]
                assert reloaded[name].__name__ == name  # type: ignore[attr-defined]
            assert observed == []
            assert logger_snapshot() == loggers_before
            assert dict(os.environ) == environment_before
            if fail_after_exec:
                raise _LoaderAfterExecFailure(
                    "fixed synthetic loader-after-exec failure"
                )
        except _LoaderAfterExecFailure:
            assert fail_after_exec
    finally:
        for name in reversed(module_names):
            sys.modules.pop(name, None)
        sys.modules.update(original_modules)
        for (parent_name, attribute), original in original_bindings.items():
            parent = sys.modules[parent_name]
            if original is missing:
                if hasattr(parent, attribute):
                    delattr(parent, attribute)
            else:
                setattr(parent, attribute, original)

    for name, original in original_modules.items():
        assert sys.modules[name] is original
    for (parent_name, attribute), original in original_bindings.items():
        if original is missing:
            assert not hasattr(sys.modules[parent_name], attribute)
        else:
            assert getattr(sys.modules[parent_name], attribute) is original
    assert logger_snapshot() == loggers_before
    assert dict(os.environ) == environment_before


def test_new_modules_do_not_import_legacy_or_external_components() -> None:
    assert "multi_agents.agent" not in sys.modules

    root = Path(__file__).parents[1]
    for relative in (
        "gpt_researcher/workflows/academic_writing/state.py",
        "gpt_researcher/workflows/academic_writing/adapters.py",
        "gpt_researcher/workflows/academic_writing/nodes.py",
        "gpt_researcher/workflows/academic_writing/graph.py",
    ):
        source = (root / relative).read_text(encoding="utf-8")
        assert "multi_agents" not in source
        if relative.endswith(("state.py", "adapters.py")):
            assert "GPTResearcher" not in source
        assert "ResearchConductor" not in source


@pytest.mark.parametrize(
    "case",
    (
        "missing_mode",
        "missing_locale",
        "freeform",
        "none_locale",
        "unknown",
        "case",
        "whitespace",
        "subclass",
    ),
)
@pytest.mark.asyncio
async def test_new_start_requires_explicit_fixed_profile_and_exact_locale(
    case: str,
) -> None:
    saver = InMemorySaver()
    adapter = FakeAdapter()
    request = _request(thread_id=f"profile-{case}")
    hostile_calls = {"eq": 0, "repr": 0, "strip": 0}
    namespace = object.__getattribute__(request, "__dict__")
    fields_set = object.__getattribute__(request, "__pydantic_fields_set__")
    if case == "missing_mode":
        set.remove(fields_set, "report_mode")
    elif case == "missing_locale":
        set.remove(fields_set, "report_locale")
    elif case == "freeform":
        dict.__setitem__(namespace, "report_mode", "freeform")
        dict.__setitem__(namespace, "report_locale", None)
    elif case == "none_locale":
        dict.__setitem__(namespace, "report_locale", None)
    elif case == "unknown":
        dict.__setitem__(namespace, "report_mode", "unknown")
    elif case == "case":
        dict.__setitem__(namespace, "report_locale", "ZH-CN")
    elif case == "whitespace":
        dict.__setitem__(namespace, "report_locale", " zh-CN ")
    else:
        class StringSubclass(str):
            def __eq__(self, other: object) -> bool:
                hostile_calls["eq"] += 1
                raise AssertionError("string subclass equality executed")

            def __repr__(self) -> str:
                hostile_calls["repr"] += 1
                raise AssertionError("string subclass repr executed")

            def strip(self, *args: object, **kwargs: object) -> str:
                hostile_calls["strip"] += 1
                raise AssertionError("string subclass strip executed")

        dict.__setitem__(namespace, "report_locale", StringSubclass("zh-CN"))
    with pytest.raises(InvariantError, match="academic workflow invariant violation"):
        await start_academic_workflow(request, adapter, checkpointer=saver)
    assert adapter.calls == []
    assert hostile_calls == {"eq": 0, "repr": 0, "strip": 0}
