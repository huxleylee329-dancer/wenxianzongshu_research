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

from gpt_researcher.workflows.academic_writing.adapters import AcademicWritingAdapter
from gpt_researcher.workflows.academic_writing.graph import (
    resume_academic_workflow,
    start_academic_workflow,
)
from gpt_researcher.workflows.academic_writing.state import (
    AcademicWorkflowIdentity,
    AcademicWorkflowRequest,
    AdapterFailure,
    ExecutionError,
    InvariantError,
    ThreadProtocolError,
    WorkflowEvidenceSource,
    WorkflowOutline,
    WorkflowOutlineSection,
    WorkflowResearchEvidence,
    WorkflowTopicPlan,
    restore_workflow_state,
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
    section = WorkflowOutlineSection(
        section_id="section:000001",
        order=1,
        title="Section",
        brief="Brief",
    )
    data: dict[str, Any] = {
        "outline_id": "outline:000001",
        "evidence_id": "evidence:000001",
        "attempt": 1,
        "title": "Outline",
        "sections": (section,),
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


def _event_projection(state: object) -> list[tuple[str, str | None]]:
    return [(event.event_type, event.node_id) for event in state.events]  # type: ignore[attr-defined]


def _assert_traceback_surface_has_no_sentinel(
    error: BaseException, sentinel: str
) -> list[str]:
    seen: set[int] = set()

    def visit(value: object) -> None:
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
    visit(error)
    current = error.__traceback__
    while current is not None:
        frame_names.append(current.tb_frame.f_code.co_name)
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

    return _build_graph(adapter, saver)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failed_node", "expected_phase", "event_count", "expected_next"),
    [
        ("topic_planner", "initialized", 0, ("topic_planner",)),
        ("research_evidence", "topic_planned", 2, ("research_evidence",)),
        ("outline_writer", "evidence_collected", 4, ("outline_writer",)),
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
    _assert_traceback_surface_has_no_sentinel(error, SECRET)

    graph = _compiled_for_test(crashing, saver)
    snapshot = await graph.aget_state({"configurable": {"thread_id": "thread-1"}})
    checkpoint_state = restore_workflow_state(snapshot.values)
    assert checkpoint_state.phase == expected_phase
    assert checkpoint_state.status == "running"
    assert len(checkpoint_state.events) == event_count
    assert all(event.node_id != failed_node for event in checkpoint_state.events)
    assert snapshot.next == expected_next
    assert SECRET not in repr(snapshot.tasks)
    assert SECRET not in repr(snapshot.metadata)

    recovered = await resume_academic_workflow(
        _identity(), FakeAdapter(), checkpointer=saver
    )
    assert _event_projection(recovered) == SUCCESS_EVENTS
    clean = await start_academic_workflow(
        _request(), FakeAdapter(), checkpointer=InMemorySaver()
    )
    assert recovered == clean


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_result", [object(), {"unexpected": "mapping"}])
async def test_invalid_adapter_response_is_fixed_invariant_and_resumable(
    bad_result: object,
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
async def test_start_and_resume_thread_guards_use_fixed_priority() -> None:
    saver = InMemorySaver()
    await start_academic_workflow(_request(), FakeAdapter(), checkpointer=saver)

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
    with pytest.raises(
        ThreadProtocolError,
        match="^academic outline approval decision is required$",
    ):
        await resume_academic_workflow(
            _identity(), FakeAdapter(), checkpointer=saver
        )

    with pytest.raises(
        ThreadProtocolError,
        match="^academic workflow checkpoint does not exist$",
    ):
        await resume_academic_workflow(
            _identity(workflow_id="unused", thread_id="unused", run_id="unused"),
            FakeAdapter(),
            checkpointer=saver,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("pending", "error_type", "message"),
    [
        ((), ThreadProtocolError, "academic workflow thread is not resumable"),
        (("outline_writer",), InvariantError, "academic workflow invariant violation"),
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
        lambda adapter, checkpointer: fake_graph,
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
        assert "GPTResearcher" not in source
        assert "ResearchConductor" not in source
