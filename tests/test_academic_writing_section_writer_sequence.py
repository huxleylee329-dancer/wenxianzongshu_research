"""Focused behavior tests for the off-graph sequential section writer."""

from __future__ import annotations

import asyncio
import builtins
import hashlib
import importlib
import inspect
import json
from collections import deque
from collections.abc import Callable
from typing import get_type_hints

import pytest

from gpt_researcher.workflows.academic_writing import (
    section_writer_sequence as module,
)
from gpt_researcher.workflows.academic_writing.section_writer_sequence import (
    GPTResearcherSectionWriterSequence,
)
from gpt_researcher.workflows.academic_writing.state import (
    AcademicWorkflowRequest,
    AcademicWorkflowState,
    WorkflowEvent,
    WorkflowOutline,
    WorkflowOutlineDecisionRecord,
    WorkflowOutlineSection,
    WorkflowResearchEvidence,
    WorkflowSectionDraft,
    WorkflowTopicPlan,
)


def _event(order: int, event_type: str, node_id: str | None) -> WorkflowEvent:
    return WorkflowEvent(
        event_id=f"event:{order:06d}",
        order=order,
        event_type=event_type,
        node_id=node_id,
        attempt=1,
    )


_APPROVE_EVENTS = (
    _event(1, "node_started", "topic_planner"),
    _event(2, "node_completed", "topic_planner"),
    _event(3, "node_started", "research_evidence"),
    _event(4, "node_completed", "research_evidence"),
    _event(5, "node_started", "outline_writer"),
    _event(6, "node_completed", "outline_writer"),
    _event(7, "node_started", "outline_approval"),
    _event(8, "node_completed", "outline_approval"),
    _event(9, "workflow_completed", None),
)
_REJECT_EVENTS = _APPROVE_EVENTS[:-3] + (
    _event(7, "node_started", "outline_approval"),
    _event(8, "node_completed", "outline_approval"),
    _event(9, "workflow_rejected", None),
)


def _section(order: int, *, brief_size: int | None = None) -> WorkflowOutlineSection:
    return WorkflowOutlineSection(
        section_id=f"section:{order:06d}",
        order=order,
        title=f"Section {order}",
        brief=f"Brief {order}" if brief_size is None else "B" * brief_size,
    )


def _approved_state(
    *,
    section_count: int = 3,
    approved: bool = True,
    report_type: str = "research_report",
    report_source: str = "web",
    query: str = "Deterministic research",
    research_topic: str | None = None,
    language: str = "en",
    questions: tuple[str, ...] = ("What is deterministic?",),
    brief_size: int | None = None,
) -> AcademicWorkflowState:
    request = AcademicWorkflowRequest(
        workflow_mode="academic_langgraph",
        workflow_id="workflow-1",
        thread_id="thread-1",
        run_id="run-1",
        query=query,
        report_type=report_type,
        report_source=report_source,
        tone="objective",
        language=language,
        source_urls=(),
        document_urls=(),
        query_domains=(),
        max_search_results=5,
    )
    plan = WorkflowTopicPlan(
        topic_plan_id="topic-plan:000001",
        workflow_id="workflow-1",
        run_id="run-1",
        attempt=1,
        research_topic=request.query if research_topic is None else research_topic,
        research_questions=questions,
    )
    evidence = WorkflowResearchEvidence(
        evidence_id="evidence:000001",
        topic_plan_id="topic-plan:000001",
        attempt=1,
        context_blocks=("Bounded evidence.",),
        sources=(),
    )
    outline = WorkflowOutline(
        outline_id="outline:000001",
        evidence_id="evidence:000001",
        attempt=1,
        title="Approved outline",
        sections=tuple(
            _section(index, brief_size=brief_size)
            for index in range(1, section_count + 1)
        ),
    )
    outline_bytes = json.dumps(
        outline.model_dump(mode="json"),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    decision = WorkflowOutlineDecisionRecord(
        decision_id="outline-decision:000001",
        schema_version="1",
        workflow_id="workflow-1",
        thread_id="thread-1",
        run_id="run-1",
        outline_id="outline:000001",
        outline_digest=hashlib.sha256(outline_bytes).hexdigest(),
        decision="approve" if approved else "reject",
        actor_assertion="actor-A",
        attempt=1,
    )
    return AcademicWorkflowState(
        schema_version="1",
        workflow_id="workflow-1",
        thread_id="thread-1",
        run_id="run-1",
        phase="outline_approved" if approved else "outline_rejected",
        status="completed",
        request=request,
        topic_plan=plan,
        research_evidence=evidence,
        outline=outline,
        outline_decision=decision,
        errors=(),
        events=_APPROVE_EVENTS if approved else _REJECT_EVENTS,
    )


class _Writer:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def write_section(
        self,
        state: AcademicWorkflowState,
        section_id: str,
    ) -> WorkflowSectionDraft:
        self.calls.append(section_id)
        return WorkflowSectionDraft(
            outline_id=state.outline.outline_id,  # type: ignore[union-attr]
            section_id=section_id,
            attempt=1,
            content=f"Body for {section_id}",
        )


class _ScriptedWriter(_Writer):
    def __init__(
        self,
        *,
        result_factory: Callable[[AcademicWorkflowState, str, int], object] | None = None,
        failure_at: int | None = None,
        cancellation: asyncio.CancelledError | None = None,
    ) -> None:
        super().__init__()
        self.result_factory = result_factory
        self.failure_at = failure_at
        self.cancellation = cancellation

    async def write_section(
        self,
        state: AcademicWorkflowState,
        section_id: str,
    ) -> WorkflowSectionDraft:
        self.calls.append(section_id)
        call_number = len(self.calls)
        if call_number == self.failure_at:
            if self.cancellation is not None:
                raise self.cancellation
            raise RuntimeError("sensitive writer failure")
        if self.result_factory is not None:
            return self.result_factory(state, section_id, call_number)  # type: ignore[return-value]
        return WorkflowSectionDraft(
            outline_id=state.outline.outline_id,  # type: ignore[union-attr]
            section_id=section_id,
            attempt=1,
            content=f"Body for {section_id}",
        )


class _DraftSubclass(WorkflowSectionDraft):
    pass


class _Hostile:
    calls = 0

    def _called(self) -> bool:
        type(self).calls += 1
        raise AssertionError("hostile code executed")

    __eq__ = lambda self, other: self._called()
    __repr__ = lambda self: self._called()
    __str__ = lambda self: self._called()
    __iter__ = lambda self: self._called()


class _StringSubclass(str):
    pass


def _valid_draft(section_id: str = "section:000001") -> WorkflowSectionDraft:
    return WorkflowSectionDraft(
        outline_id="outline:000001",
        section_id=section_id,
        attempt=1,
        content="Body",
    )


def _corrupt_draft(case: str) -> object:
    if case == "none":
        return None
    if case == "subclass":
        return _DraftSubclass(
            outline_id="outline:000001",
            section_id="section:000001",
            attempt=1,
            content="Body",
        )
    draft = _valid_draft()
    namespace = object.__getattribute__(draft, "__dict__")
    if case == "wrong_outline":
        namespace["outline_id"] = "outline:wrong"
    elif case == "wrong_section":
        namespace["section_id"] = "section:000002"
    elif case == "bool_attempt":
        namespace["attempt"] = True
    elif case == "wrong_attempt":
        namespace["attempt"] = 2
    elif case == "blank_content":
        namespace["content"] = " "
    elif case == "shadowed_method":
        namespace["model_dump"] = _Hostile()
    elif case == "extra":
        object.__setattr__(draft, "__pydantic_extra__", {"hostile": _Hostile()})
    elif case == "hostile_field":
        namespace["content"] = _Hostile()
    elif case == "string_subclass":
        namespace["content"] = _StringSubclass("Body")
    elif case == "private":
        object.__setattr__(draft, "__pydantic_private__", {"hostile": _Hostile()})
    else:  # pragma: no cover - test construction guard
        raise AssertionError(case)
    return draft


def _exception_reaches(error: BaseException, targets: tuple[object, ...]) -> bool:
    pending: deque[object] = deque([error])
    seen: set[int] = set()
    while pending:
        value = pending.popleft()
        identity = id(value)
        if identity in seen:
            continue
        seen.add(identity)
        if any(value is target for target in targets):
            return True
        if isinstance(value, BaseException):
            pending.extend(value.args)
            if value.__cause__ is not None:
                pending.append(value.__cause__)
            if value.__context__ is not None:
                pending.append(value.__context__)
            if value.__traceback__ is not None:
                pending.append(value.__traceback__)
        elif inspect.istraceback(value):
            pending.append(value.tb_frame)
            if value.tb_next is not None:
                pending.append(value.tb_next)
        elif inspect.isframe(value):
            if value.f_globals.get("__name__") == module.__name__:
                pending.extend(value.f_locals.values())
        elif inspect.isfunction(value):
            closure = object.__getattribute__(value, "__closure__")
            if type(closure) is tuple:
                for cell in closure:
                    try:
                        pending.append(cell.cell_contents)
                    except ValueError:
                        pass
        elif type(value) in (tuple, list, set, frozenset, deque):
            pending.extend(value)  # type: ignore[arg-type]
        elif type(value) is dict:
            pending.extend(value.keys())
            pending.extend(value.values())
    return False


@pytest.mark.parametrize("section_count", [1, 12])
@pytest.mark.asyncio
async def test_sequence_writes_each_section_once_in_outline_order(
    section_count: int,
) -> None:
    state = _approved_state(section_count=section_count)
    before = state.model_dump(mode="json")
    writer = _Writer()

    result = await GPTResearcherSectionWriterSequence(
        section_writer=writer
    ).write_sections(state)

    expected_ids = tuple(section.section_id for section in state.outline.sections)  # type: ignore[union-attr]
    assert type(result) is tuple
    assert all(type(draft) is WorkflowSectionDraft for draft in result)
    assert tuple(draft.section_id for draft in result) == expected_ids
    assert writer.calls == list(expected_ids)
    assert state.model_dump(mode="json") == before


def test_public_surface_and_verified_pydantic_shape() -> None:
    assert module.__all__ == ("GPTResearcherSectionWriterSequence",)
    assert str(inspect.signature(module._create_production_section_writer)) == (
        "() -> '_SectionWriter'"
    )
    assert str(inspect.signature(module._SectionWriter.write_section)) == (
        "(self, state: 'AcademicWorkflowState', section_id: 'str') -> "
        "'WorkflowSectionDraft'"
    )
    assert str(inspect.signature(GPTResearcherSectionWriterSequence.__init__)) == (
        "(self, *, section_writer: '_SectionWriter | None' = None) -> 'None'"
    )
    assert str(inspect.signature(GPTResearcherSectionWriterSequence.write_sections)) == (
        "(self, state: 'AcademicWorkflowState') -> "
        "'tuple[WorkflowSectionDraft, ...]'"
    )
    hints = get_type_hints(module._SectionWriter.write_section)
    assert hints == {
        "state": AcademicWorkflowState,
        "section_id": str,
        "return": WorkflowSectionDraft,
    }
    draft = _valid_draft()
    assert tuple(type(draft).__mro__[1].__slots__) == (
        "__dict__",
        "__pydantic_fields_set__",
        "__pydantic_extra__",
        "__pydantic_private__",
    )
    assert type(object.__getattribute__(draft, "__dict__")) is dict
    assert type(object.__getattribute__(draft, "__pydantic_fields_set__")) is set
    assert object.__getattribute__(draft, "__pydantic_extra__") is None
    assert object.__getattribute__(draft, "__pydantic_private__") is None


def _preflight_case(case: str) -> tuple[object, type[Exception], str]:
    if case == "wrong_type":
        return object(), TypeError, module._STATE_TYPE_ERROR
    if case == "rejected":
        return _approved_state(approved=False), ValueError, module._STATE_SHAPE_ERROR
    if case == "thirteen":
        state = _approved_state(section_count=13)
        encoded = json.dumps(
            state.model_dump(mode="json"),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        assert AcademicWorkflowState.model_validate_json(encoded) == state
        return state, ValueError, module._SECTION_COUNT_ERROR
    if case == "report_type":
        state = _approved_state(report_type="other")
    elif case == "report_source":
        state = _approved_state(report_source="other")
    elif case == "topic":
        state = _approved_state(research_topic="Different")
    elif case == "query":
        state = _approved_state(query="Q" * 4097)
    elif case == "language":
        state = _approved_state(language="L" * 129)
    elif case == "question_count":
        state = _approved_state(questions=("1", "2", "3", "4"))
    elif case == "question_item":
        state = _approved_state(questions=("Q" * 513,))
    elif case == "question_total":
        state = _approved_state(questions=("A" * 400, "B" * 400, "C" * 225))
    elif case == "prompt":
        state = _approved_state(brief_size=65536)
    elif case == "empty_outline":
        state = _approved_state()
        outline = state.outline
        assert outline is not None
        corrupted_outline = WorkflowOutline.model_construct(
            outline_id=outline.outline_id,
            evidence_id=outline.evidence_id,
            attempt=outline.attempt,
            title=outline.title,
            sections=(),
        )
        state = state.model_copy(update={"outline": corrupted_outline})
        return state, module._SectionWriterSequenceError, module._SEQUENCE_ERROR
    else:  # pragma: no cover - matrix construction guard
        raise AssertionError(case)
    return state, ValueError, module._INPUT_ERROR


@pytest.mark.parametrize(
    "case",
    [
        "wrong_type",
        "rejected",
        "thirteen",
        "report_type",
        "report_source",
        "topic",
        "query",
        "language",
        "question_count",
        "question_item",
        "question_total",
        "prompt",
        "empty_outline",
    ],
)
@pytest.mark.asyncio
async def test_preflight_rejects_before_writer_selection(case: str) -> None:
    state, error_type, message = _preflight_case(case)
    writer = _Writer()
    sequence = GPTResearcherSectionWriterSequence(section_writer=writer)

    with pytest.raises(error_type, match=f"^{message}$") as caught:
        await sequence.write_sections(state)  # type: ignore[arg-type]

    assert writer.calls == []
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert caught.value.__suppress_context__ is False
    if case == "empty_outline":
        assert not _exception_reaches(caught.value, (sequence, writer, state))


@pytest.mark.asyncio
async def test_production_factory_is_lazy_and_injected_path_isolated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    production = _Writer()
    factory_calls = 0

    def factory() -> _Writer:
        nonlocal factory_calls
        factory_calls += 1
        return production

    monkeypatch.setattr(module, "_create_production_section_writer", factory)
    with pytest.raises(ValueError, match="between 1 and 12"):
        await GPTResearcherSectionWriterSequence().write_sections(
            _approved_state(section_count=13)
        )
    assert factory_calls == 0

    injected = _Writer()
    await GPTResearcherSectionWriterSequence(section_writer=injected).write_sections(
        _approved_state(section_count=1)
    )
    assert factory_calls == 0

    await GPTResearcherSectionWriterSequence().write_sections(
        _approved_state(section_count=1)
    )
    assert factory_calls == 1
    assert production.calls == ["section:000001"]

    def failed_factory() -> _Writer:
        raise RuntimeError("sensitive construction failure")

    monkeypatch.setattr(module, "_create_production_section_writer", failed_factory)
    with pytest.raises(
        module._SectionWriterSequenceError,
        match=f"^{module._SEQUENCE_ERROR}$",
    ) as caught:
        await GPTResearcherSectionWriterSequence().write_sections(
            _approved_state(section_count=1)
        )
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


@pytest.mark.parametrize(
    "case",
    [
        "none",
        "subclass",
        "wrong_outline",
        "wrong_section",
        "bool_attempt",
        "wrong_attempt",
        "blank_content",
        "shadowed_method",
        "extra",
        "hostile_field",
        "string_subclass",
        "private",
        "nonawaitable",
    ],
)
@pytest.mark.asyncio
async def test_invalid_writer_results_are_static_and_stop_sequence(case: str) -> None:
    _Hostile.calls = 0
    if case == "nonawaitable":
        class NonAwaitableWriter(_Writer):
            def write_section(  # type: ignore[override]
                self,
                state: AcademicWorkflowState,
                section_id: str,
            ) -> object:
                del state
                self.calls.append(section_id)
                return object()

        writer: _Writer = NonAwaitableWriter()
    else:
        writer = _ScriptedWriter(
            result_factory=lambda state, section_id, call: _corrupt_draft(case)
        )

    with pytest.raises(
        module._SectionWriterSequenceError,
        match=f"^{module._SEQUENCE_ERROR}$",
    ) as caught:
        await GPTResearcherSectionWriterSequence(section_writer=writer).write_sections(
            _approved_state()
        )

    assert writer.calls == ["section:000001"]
    assert _Hostile.calls == 0
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


@pytest.mark.parametrize("failure_at", [1, 2, 3])
@pytest.mark.asyncio
async def test_ordinary_writer_failures_are_fixed_and_have_no_partial_result(
    failure_at: int,
) -> None:
    state = _approved_state()
    writer = _ScriptedWriter(failure_at=failure_at)
    sequence = GPTResearcherSectionWriterSequence(section_writer=writer)

    with pytest.raises(
        module._SectionWriterSequenceError,
        match=f"^{module._SEQUENCE_ERROR}$",
    ) as caught:
        await sequence.write_sections(state)

    assert len(writer.calls) == failure_at
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert not _exception_reaches(caught.value, (sequence, writer, state, state.outline))


@pytest.mark.parametrize(
    ("stage", "failure_at"),
    [
        ("production_import", None),
        ("production_construction", None),
        ("injected_selection", None),
        ("writer", 1),
        ("writer", 2),
        ("writer", 3),
    ],
)
@pytest.mark.asyncio
async def test_cancellation_is_bare_and_stops_later_calls(
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
    failure_at: int | None,
) -> None:
    cancellation = asyncio.CancelledError(stage, 17)
    writer = _ScriptedWriter(failure_at=failure_at, cancellation=cancellation)
    import_calls = 0
    construction_calls = 0
    if stage in ("production_import", "production_construction"):
        production_module = importlib.import_module(
            "gpt_researcher.workflows.academic_writing.section_writer"
        )
        real_import = builtins.__import__

        def cancelled_constructor() -> _Writer:
            nonlocal construction_calls
            construction_calls += 1
            raise cancellation

        if stage == "production_import":
            def forbidden_constructor() -> _Writer:
                nonlocal construction_calls
                construction_calls += 1
                raise AssertionError("construction must not run")

            def cancelled_import(
                name: str,
                globals: dict[str, object] | None = None,
                locals: dict[str, object] | None = None,
                fromlist: tuple[str, ...] = (),
                level: int = 0,
            ) -> object:
                nonlocal import_calls
                if (
                    name == "section_writer"
                    and level == 1
                    and globals is not None
                    and globals.get("__name__") == module.__name__
                ):
                    import_calls += 1
                    raise cancellation
                return real_import(name, globals, locals, fromlist, level)

            monkeypatch.setattr(
                production_module,
                "GPTResearcherSectionWriterAdapter",
                forbidden_constructor,
            )
            monkeypatch.setattr(builtins, "__import__", cancelled_import)
        else:
            monkeypatch.setattr(
                production_module,
                "GPTResearcherSectionWriterAdapter",
                cancelled_constructor,
            )
        sequence = GPTResearcherSectionWriterSequence()
    elif stage == "injected_selection":
        def cancelled_selection(choice: object) -> object:
            del choice
            raise cancellation

        monkeypatch.setattr(module, "_select_writer", cancelled_selection)
        sequence = GPTResearcherSectionWriterSequence(section_writer=writer)
    else:
        sequence = GPTResearcherSectionWriterSequence(section_writer=writer)

    state = _approved_state()
    with pytest.raises(asyncio.CancelledError) as caught:
        await sequence.write_sections(state)

    assert caught.value is cancellation
    assert caught.value.args == (stage, 17)
    assert len(writer.calls) == (failure_at or 0)
    if stage == "production_import":
        assert import_calls == 1
        assert construction_calls == 0
    elif stage == "production_construction":
        assert import_calls == 0
        assert construction_calls == 1
    assert not _exception_reaches(
        caught.value,
        (sequence, writer, state, state.outline, module._PRODUCTION_WRITER),
    )


@pytest.mark.asyncio
async def test_retry_is_a_complete_new_sequence() -> None:
    writer = _Writer()
    sequence = GPTResearcherSectionWriterSequence(section_writer=writer)
    state = _approved_state()

    first = await sequence.write_sections(state)
    second = await sequence.write_sections(state)

    expected = ["section:000001", "section:000002", "section:000003"]
    assert writer.calls == expected + expected
    assert first == second


@pytest.mark.asyncio
async def test_sequence_frames_do_not_retain_live_state_or_draft_collections() -> None:
    original = _approved_state(section_count=2)
    forbidden = (original, original.outline, *original.outline.sections)  # type: ignore[union-attr]
    observations: list[bool] = []
    frame_counts: list[int] = []
    entered = (asyncio.Event(), asyncio.Event())
    release = (asyncio.Event(), asyncio.Event())

    class FrameWriter(_Writer):
        async def write_section(
            self,
            state: AcademicWorkflowState,
            section_id: str,
        ) -> WorkflowSectionDraft:
            self.calls.append(section_id)
            index = len(self.calls) - 1
            entered[index].set()
            await release[index].wait()
            return WorkflowSectionDraft(
                outline_id=state.outline.outline_id,  # type: ignore[union-attr]
                section_id=section_id,
                attempt=1,
                content="Body",
            )

    writer = FrameWriter()
    task = asyncio.create_task(
        GPTResearcherSectionWriterSequence(section_writer=writer).write_sections(original)
    )
    for index in range(2):
        await entered[index].wait()
        awaitable: object | None = task.get_coro()
        frames: list[object] = []
        while inspect.iscoroutine(awaitable):
            frame = awaitable.cr_frame
            if frame is not None:
                frames.append(frame)
            awaitable = awaitable.cr_await
        sequence_frames = [
            frame
            for frame in frames
            if frame.f_globals.get("__name__") == module.__name__
        ]
        frame_counts.append(len(sequence_frames))
        values = tuple(
            value
            for frame in sequence_frames
            for value in frame.f_locals.values()
        )
        observations.append(
            any(value is target for value in values for target in forbidden)
            or any(
                type(value) is list
                and any(type(item) is WorkflowSectionDraft for item in value)
                for value in values
            )
        )
        release[index].set()
    await task
    assert all(count >= 3 for count in frame_counts)
    assert observations == [False, False]
