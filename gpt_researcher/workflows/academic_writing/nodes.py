"""Thin private node closures for the academic-writing workflow."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Protocol, TypeVar

from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt
from pydantic import BaseModel

from .adapters import AcademicWritingAdapter
from .academic_draft_composer import (
    GPTResearcherAcademicDraftComposer,
    WorkflowAcademicDraftComposition,
)
from .state import (
    AcademicWorkflowGraphState,
    AcademicOutlineDecisionCommand,
    AcademicWorkflowState,
    AdapterFailure,
    ExecutionError,
    InvariantError,
    NodeId,
    WorkflowError,
    WorkflowEvent,
    WorkflowOutline,
    WorkflowOutlineDecisionRecord,
    WorkflowResearchEvidence,
    WorkflowTopicPlan,
    _outline_digest,
    _outline_digest_equal,
    restore_workflow_state,
    validate_json_value,
    workflow_to_graph_state,
)
from .workflow_outcome import (
    AcademicWorkflowPersistentState,
    WorkflowDraftReadyOutcome,
    WorkflowReviewRequiredOutcome,
    _persistent_workflow_to_graph_state,
    _restore_persistent_workflow_state,
)


_Dto = TypeVar("_Dto", bound=BaseModel)
_Node = Callable[
    [AcademicWorkflowGraphState, RunnableConfig],
    Awaitable[AcademicWorkflowGraphState],
]
_TransitionBuilder = Callable[[object], AcademicWorkflowState]
_EXECUTION_FAILURE = object()
_INVARIANT_FAILURE = object()
_APPROVE_COMMIT_FAILURE = object()
_REJECT_COMMIT_FAILURE = object()
_PRODUCTION_COMPOSER = object()
_EMPTY_SLOT = object()


class _AcademicDraftComposer(Protocol):
    async def compose(
        self,
        state: AcademicWorkflowState,
    ) -> WorkflowAcademicDraftComposition: ...


class _ComposerDependencySlot:
    __slots__ = ("_value", "_consumed")

    def __init__(self, value: object) -> None:
        self._value = value
        self._consumed = False

    def consume(self) -> object:
        if self._consumed or self._value is _EMPTY_SLOT:
            raise InvariantError() from None
        value = self._value
        self._value = _EMPTY_SLOT
        self._consumed = True
        return value

    def clear(self) -> None:
        self._value = _EMPTY_SLOT

    def is_empty(self) -> bool:
        return self._value is _EMPTY_SLOT


def _composer_slot(composer: _AcademicDraftComposer | None) -> _ComposerDependencySlot:
    choice = _PRODUCTION_COMPOSER if composer is None else composer
    slot = _ComposerDependencySlot(choice)
    del composer, choice
    return slot


class _OutlineApproveCommitError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("academic outline decision commit failed")


class _OutlineRejectCommitError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("academic outline decision commit failed")


def _validated_dto(value: object, expected_type: type[_Dto]) -> _Dto:
    if type(value) is not expected_type:
        raise TypeError("adapter returned an unexpected DTO type")
    dumped = value.model_dump(mode="json")  # type: ignore[union-attr]
    validate_json_value(dumped)
    payload = json.dumps(
        dumped,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return expected_type.model_validate_json(payload)


def _event(
    events: tuple[WorkflowEvent, ...], event_type: str, node_id: NodeId | None
) -> WorkflowEvent:
    order = len(events) + 1
    return WorkflowEvent(
        event_id=f"event:{order:06d}",
        order=order,
        event_type=event_type,
        node_id=node_id,
        attempt=1,
    )


def _restored_predecessor(
    graph_state: AcademicWorkflowGraphState,
    config: RunnableConfig,
    *,
    phase: str,
) -> AcademicWorkflowState:
    failed = False
    restored: AcademicWorkflowState | None = None
    try:
        restored = restore_workflow_state(graph_state)
        configurable = config.get("configurable")
        if type(configurable) is not dict:
            raise TypeError("configurable must be a mapping")
        thread_id = configurable.get("thread_id")
        if type(thread_id) is not str or thread_id != restored.thread_id:
            raise ValueError("runtime thread identity differs")
        if restored.phase != phase or restored.status != "running":
            raise ValueError("node predecessor is invalid")
    except Exception:
        failed = True
    if failed or restored is None:
        raise InvariantError()
    return restored


def _validated_transition(
    build: Callable[[], AcademicWorkflowState],
) -> AcademicWorkflowGraphState:
    failed = False
    state: AcademicWorkflowState | None = None
    graph_state: AcademicWorkflowGraphState | None = None
    try:
        state = build()
        graph_state = workflow_to_graph_state(state)
        state = restore_workflow_state(graph_state)
        graph_state = workflow_to_graph_state(state)
    except Exception:
        failed = True
    if failed or state is None or graph_state is None:
        raise InvariantError()
    return graph_state


def _validated_persistent_transition(
    state: AcademicWorkflowPersistentState,
) -> AcademicWorkflowGraphState:
    failed = False
    graph_state: AcademicWorkflowGraphState | None = None
    try:
        graph_state = _persistent_workflow_to_graph_state(state)
        restored = _restore_persistent_workflow_state(graph_state)
        graph_state = _persistent_workflow_to_graph_state(restored)
        del restored
    except Exception:
        failed = True
    del state
    if failed or graph_state is None:
        raise InvariantError() from None
    return graph_state


async def _prepare_transition(
    call: Callable[[], Awaitable[object]],
    build: _TransitionBuilder,
) -> object:
    raw_failure = False
    invalid_cancellation = False
    result: object | None = None
    try:
        result = await call()
    except asyncio.CancelledError:
        task = asyncio.current_task()
        if task is not None and task.cancelling() > 0:
            raise
        invalid_cancellation = True
    except Exception:
        raw_failure = True
    if raw_failure:
        return _EXECUTION_FAILURE
    if invalid_cancellation:
        return _INVARIANT_FAILURE

    invalid_transition = False
    graph_state: AcademicWorkflowGraphState | None = None
    try:
        graph_state = _validated_transition(lambda: build(result))
    except Exception:
        invalid_transition = True
    if invalid_transition or graph_state is None:
        return _INVARIANT_FAILURE
    return graph_state


def _resolve_transition(outcome: object) -> AcademicWorkflowGraphState:
    if outcome is _EXECUTION_FAILURE:
        raise ExecutionError()
    if outcome is _INVARIANT_FAILURE or type(outcome) is not dict:
        raise InvariantError()
    return outcome  # type: ignore[return-value]


def _failure_state(
    state: AcademicWorkflowState,
    node_id: NodeId,
    failure: AdapterFailure,
) -> AcademicWorkflowState:
    expected_codes = {
        "topic_planner": "topic_planning_failed",
        "research_evidence": "research_evidence_failed",
        "outline_writer": "outline_writing_failed",
    }
    if failure.code != expected_codes[node_id]:
        raise ValueError("failure code does not match node")
    started = _event(state.events, "node_started", node_id)
    failed = _event(state.events + (started,), "workflow_failed", node_id)
    error = WorkflowError(
        error_id="error:000001",
        order=1,
        failed_node_id=node_id,
        attempt=1,
        code=failure.code,
    )
    return AcademicWorkflowState(
        schema_version="1",
        workflow_id=state.workflow_id,
        thread_id=state.thread_id,
        run_id=state.run_id,
        phase=state.phase,
        status="failed",
        request=state.request,
        topic_plan=state.topic_plan,
        research_evidence=state.research_evidence,
        outline=state.outline,
        outline_decision=None,
        errors=(error,),
        events=state.events + (started, failed),
    )


def _make_topic_planner_node(adapter: AcademicWritingAdapter) -> _Node:
    async def _topic_planner(
        graph_state: AcademicWorkflowGraphState,
        config: RunnableConfig,
    ) -> AcademicWorkflowGraphState:
        state = _restored_predecessor(graph_state, config, phase="initialized")

        def build(result: object) -> AcademicWorkflowState:
            if type(result) is AdapterFailure:
                failure = _validated_dto(result, AdapterFailure)
                return _failure_state(state, "topic_planner", failure)
            plan = _validated_dto(result, WorkflowTopicPlan)
            started = _event(state.events, "node_started", "topic_planner")
            completed = _event(
                state.events + (started,), "node_completed", "topic_planner"
            )
            return AcademicWorkflowState(
                schema_version="1",
                workflow_id=state.workflow_id,
                thread_id=state.thread_id,
                run_id=state.run_id,
                phase="topic_planned",
                status="running",
                request=state.request,
                topic_plan=plan,
                research_evidence=None,
                outline=None,
                outline_decision=None,
                errors=(),
                events=state.events + (started, completed),
            )

        outcome = await _prepare_transition(
            lambda: adapter.plan_topic(state.request), build
        )
        return _resolve_transition(outcome)

    return _topic_planner


def _make_research_evidence_node(adapter: AcademicWritingAdapter) -> _Node:
    async def _research_evidence(
        graph_state: AcademicWorkflowGraphState,
        config: RunnableConfig,
    ) -> AcademicWorkflowGraphState:
        state = _restored_predecessor(graph_state, config, phase="topic_planned")
        if state.topic_plan is None:
            raise InvariantError()

        def build(result: object) -> AcademicWorkflowState:
            if type(result) is AdapterFailure:
                failure = _validated_dto(result, AdapterFailure)
                return _failure_state(state, "research_evidence", failure)
            evidence = _validated_dto(result, WorkflowResearchEvidence)
            started = _event(state.events, "node_started", "research_evidence")
            completed = _event(
                state.events + (started,), "node_completed", "research_evidence"
            )
            return AcademicWorkflowState(
                schema_version="1",
                workflow_id=state.workflow_id,
                thread_id=state.thread_id,
                run_id=state.run_id,
                phase="evidence_collected",
                status="running",
                request=state.request,
                topic_plan=state.topic_plan,
                research_evidence=evidence,
                outline=None,
                outline_decision=None,
                errors=(),
                events=state.events + (started, completed),
            )

        outcome = await _prepare_transition(
            lambda: adapter.collect_research_evidence(state.request, state.topic_plan),
            build,
        )
        return _resolve_transition(outcome)

    return _research_evidence


def _make_outline_writer_node(adapter: AcademicWritingAdapter) -> _Node:
    async def _outline_writer(
        graph_state: AcademicWorkflowGraphState,
        config: RunnableConfig,
    ) -> AcademicWorkflowGraphState:
        state = _restored_predecessor(
            graph_state, config, phase="evidence_collected"
        )
        if state.topic_plan is None or state.research_evidence is None:
            raise InvariantError()

        def build(result: object) -> AcademicWorkflowState:
            if type(result) is AdapterFailure:
                failure = _validated_dto(result, AdapterFailure)
                return _failure_state(state, "outline_writer", failure)
            outline = _validated_dto(result, WorkflowOutline)
            started = _event(state.events, "node_started", "outline_writer")
            completed = _event(
                state.events + (started,), "node_completed", "outline_writer"
            )
            return AcademicWorkflowState(
                schema_version="1",
                workflow_id=state.workflow_id,
                thread_id=state.thread_id,
                run_id=state.run_id,
                phase="outline_ready",
                status="running",
                request=state.request,
                topic_plan=state.topic_plan,
                research_evidence=state.research_evidence,
                outline=outline,
                outline_decision=None,
                errors=(),
                events=state.events + (started, completed),
            )

        outcome = await _prepare_transition(
            lambda: adapter.write_outline(
                state.request, state.topic_plan, state.research_evidence
            ),
            build,
        )
        return _resolve_transition(outcome)

    return _outline_writer


def _approval_interrupt_payload(state: AcademicWorkflowState) -> dict[str, object]:
    if state.outline is None:
        raise InvariantError()
    payload: dict[str, object] = {
        "allowed_decisions": ["approve", "reject"],
        "outline_digest": _outline_digest(state.outline),
        "outline_id": state.outline.outline_id,
        "run_id": state.run_id,
        "schema_version": "1",
        "thread_id": state.thread_id,
        "workflow_id": state.workflow_id,
    }
    validate_json_value(payload)
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    if len(canonical) > 2048:
        raise InvariantError()
    return payload


def _restore_approval_command(
    raw_resume: object, state: AcademicWorkflowState
) -> AcademicOutlineDecisionCommand:
    if type(raw_resume) is not dict:
        raise TypeError("approval resume must be an exact mapping")
    validate_json_value(raw_resume)
    canonical = json.dumps(
        raw_resume,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    if len(canonical) > 2048:
        raise ValueError("approval resume is too long")
    command = AcademicOutlineDecisionCommand.model_validate_json(canonical)
    if command.model_dump(mode="json") != raw_resume:
        raise ValueError("approval resume changed during validation")
    if state.outline is None:
        raise ValueError("approval predecessor has no outline")
    if (
        command.workflow_id,
        command.thread_id,
        command.run_id,
        command.outline_id,
    ) != (
        state.workflow_id,
        state.thread_id,
        state.run_id,
        state.outline.outline_id,
    ):
        raise ValueError("approval identity differs")
    if not _outline_digest_equal(
        command.outline_digest, _outline_digest(state.outline)
    ):
        raise ValueError("approval outline digest differs")
    return command


def _build_approval_terminal_state(
    state: AcademicWorkflowState,
    command: AcademicOutlineDecisionCommand,
) -> AcademicWorkflowPersistentState:
    if state.outline is None:
        raise ValueError("approval predecessor has no outline")
    record = WorkflowOutlineDecisionRecord(
        decision_id="outline-decision:000001",
        schema_version="1",
        workflow_id=command.workflow_id,
        thread_id=command.thread_id,
        run_id=command.run_id,
        outline_id=command.outline_id,
        outline_digest=command.outline_digest,
        decision=command.decision,
        actor_assertion=command.actor_assertion,
        attempt=1,
    )
    started = _event(state.events, "node_started", "outline_approval")
    completed = _event(
        state.events + (started,), "node_completed", "outline_approval"
    )
    approve = command.decision == "approve"
    terminal = (
        None
        if approve
        else _event(
            state.events + (started, completed), "workflow_rejected", None
        )
    )
    events = (
        state.events + (started, completed)
        if terminal is None
        else state.events + (started, completed, terminal)
    )
    return AcademicWorkflowPersistentState(
        schema_version="1",
        workflow_id=state.workflow_id,
        thread_id=state.thread_id,
        run_id=state.run_id,
        phase="outline_approved" if approve else "outline_rejected",
        status="running" if approve else "completed",
        request=state.request,
        topic_plan=state.topic_plan,
        research_evidence=state.research_evidence,
        outline=state.outline,
        outline_decision=record,
        errors=(),
        events=events,
        outcome=None,
    )


def _prepare_approval_transition(
    state: AcademicWorkflowState, raw_resume: object
) -> object:
    command: AcademicOutlineDecisionCommand | None = None
    try:
        command = _restore_approval_command(raw_resume, state)
    except Exception:
        return _INVARIANT_FAILURE

    try:
        return _validated_persistent_transition(
            _build_approval_terminal_state(state, command)
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        return (
            _APPROVE_COMMIT_FAILURE
            if command.decision == "approve"
            else _REJECT_COMMIT_FAILURE
        )


def _run_approval_interrupt(state: AcademicWorkflowState) -> object:
    raw_resume = interrupt(_approval_interrupt_payload(state))
    return _prepare_approval_transition(state, raw_resume)


def _raise_approval_outcome_failure(failure: object) -> None:
    if failure is _APPROVE_COMMIT_FAILURE:
        raise _OutlineApproveCommitError() from None
    if failure is _REJECT_COMMIT_FAILURE:
        raise _OutlineRejectCommitError() from None
    raise InvariantError() from None


def _make_outline_approval_node() -> _Node:
    async def _outline_approval(
        graph_state: AcademicWorkflowGraphState,
        config: RunnableConfig,
    ) -> AcademicWorkflowGraphState:
        state = _restored_predecessor(
            graph_state, config, phase="outline_ready"
        )
        outcome = _run_approval_interrupt(state)
        if (
            outcome is _APPROVE_COMMIT_FAILURE
            or outcome is _REJECT_COMMIT_FAILURE
            or outcome is _INVARIANT_FAILURE
            or type(outcome) is not dict
        ):
            failure = outcome
            del graph_state, config, state, outcome
            _raise_approval_outcome_failure(failure)
        return outcome

    return _outline_approval


def _execution_view(state: AcademicWorkflowPersistentState) -> AcademicWorkflowState:
    terminal = _event(state.events, "workflow_completed", None)
    return AcademicWorkflowState(
        schema_version="1",
        workflow_id=state.workflow_id,
        thread_id=state.thread_id,
        run_id=state.run_id,
        phase="outline_approved",
        status="completed",
        request=state.request,
        topic_plan=state.topic_plan,
        research_evidence=state.research_evidence,
        outline=state.outline,
        outline_decision=state.outline_decision,
        errors=(),
        events=state.events + (terminal,),
    )


def _trusted_composition(value: object) -> WorkflowAcademicDraftComposition:
    if type(value) is not WorkflowAcademicDraftComposition:
        raise InvariantError() from None
    try:
        dumped = BaseModel.model_dump(value, mode="json")
        validate_json_value(dumped)
        encoded = json.dumps(
            dumped,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        trusted = WorkflowAcademicDraftComposition.model_validate_json(encoded)
    except Exception:
        raise InvariantError() from None
    if type(trusted) is not WorkflowAcademicDraftComposition:
        raise InvariantError() from None
    return trusted


def _terminal_state(
    state: AcademicWorkflowPersistentState,
    composition: WorkflowAcademicDraftComposition,
) -> AcademicWorkflowPersistentState:
    disposition = composition.disposition.disposition
    if disposition == "ready":
        referenced = composition.referenced_draft
        if referenced is None:
            raise InvariantError() from None
        outcome = WorkflowDraftReadyOutcome(
            outcome_type="draft_ready",
            referenced_draft=referenced,
        )
        phase = "draft_ready"
    elif disposition in ("blocked", "needs_human_review"):
        outcome = WorkflowReviewRequiredOutcome(
            outcome_type="review_required",
            drafts=composition.drafts,
            gate_result=composition.gate_result,
            reviews=composition.reviews,
        )
        phase = "review_required"
    else:
        raise InvariantError() from None
    started = _event(state.events, "node_started", "academic_draft_composer")
    completed = _event(
        state.events + (started,), "node_completed", "academic_draft_composer"
    )
    terminal = _event(
        state.events + (started, completed), "workflow_completed", None
    )
    return AcademicWorkflowPersistentState(
        schema_version="1",
        workflow_id=state.workflow_id,
        thread_id=state.thread_id,
        run_id=state.run_id,
        phase=phase,
        status="completed",
        request=state.request,
        topic_plan=state.topic_plan,
        research_evidence=state.research_evidence,
        outline=state.outline,
        outline_decision=state.outline_decision,
        errors=(),
        events=state.events + (started, completed, terminal),
        outcome=outcome,
    )


def _make_academic_draft_composer_node(
    slot: _ComposerDependencySlot,
) -> _Node:
    async def _academic_draft_composer(
        graph_state: AcademicWorkflowGraphState,
        config: RunnableConfig,
    ) -> AcademicWorkflowGraphState:
        state: AcademicWorkflowPersistentState | None = None
        execution_view: AcademicWorkflowState | None = None
        composer: object = _EMPTY_SLOT
        result: object | None = None
        trusted: WorkflowAcademicDraftComposition | None = None
        compose_method: object = _EMPTY_SLOT
        terminal: object = _EMPTY_SLOT
        transition: object = _EMPTY_SLOT
        failure: object | None = None
        try:
            state = _restore_persistent_workflow_state(graph_state)
            configurable = config.get("configurable")
            if type(configurable) is not dict:
                raise InvariantError() from None
            thread_id = configurable.get("thread_id")
            if (
                type(thread_id) is not str
                or thread_id != state.thread_id
                or state.phase != "outline_approved"
                or state.status != "running"
                or state.outcome is not None
            ):
                raise InvariantError() from None
            execution_view = _execution_view(state)
            choice = slot.consume()
            if choice is _PRODUCTION_COMPOSER:
                composer = GPTResearcherAcademicDraftComposer()
            else:
                composer = choice
            del choice
            compose_method = getattr(type(composer), "compose", None)
            if compose_method is None:
                compose_method = _EMPTY_SLOT
                raise InvariantError() from None
            try:
                result = await compose_method(composer, execution_view)
            finally:
                del compose_method
                compose_method = _EMPTY_SLOT
            trusted = _trusted_composition(result)
            try:
                terminal = _terminal_state(state, trusted)
            except Exception:
                raise InvariantError() from None
            try:
                transition = _validated_persistent_transition(terminal)
            finally:
                del terminal
                terminal = _EMPTY_SLOT
            graph_result = transition
            transition = _EMPTY_SLOT
            del (
                result,
                trusted,
                execution_view,
                composer,
                state,
                compose_method,
                terminal,
                transition,
                failure,
                graph_state,
                config,
            )
            return graph_result
        except asyncio.CancelledError:
            slot.clear()
            del (
                graph_state,
                config,
                state,
                execution_view,
                composer,
                result,
                trusted,
                compose_method,
                terminal,
                transition,
                failure,
            )
            raise
        except InvariantError:
            slot.clear()
            del (
                graph_state,
                config,
                state,
                execution_view,
                composer,
                result,
                trusted,
                compose_method,
                terminal,
                transition,
            )
            failure = _INVARIANT_FAILURE
        except Exception:
            slot.clear()
            del (
                graph_state,
                config,
                state,
                execution_view,
                composer,
                result,
                trusted,
                compose_method,
                terminal,
                transition,
            )
            failure = _EXECUTION_FAILURE
        if failure is _INVARIANT_FAILURE:
            del failure
            raise InvariantError()
        del failure
        raise ExecutionError()

    return _academic_draft_composer


def _make_nodes(
    adapter: AcademicWritingAdapter,
    composer_slot: _ComposerDependencySlot,
) -> tuple[_Node, _Node, _Node, _Node, _Node]:
    return (
        _make_topic_planner_node(adapter),
        _make_research_evidence_node(adapter),
        _make_outline_writer_node(adapter),
        _make_outline_approval_node(),
        _make_academic_draft_composer_node(composer_slot),
    )


def _route_after_node(graph_state: AcademicWorkflowGraphState) -> str:
    failed = False
    state: AcademicWorkflowState | None = None
    try:
        state = restore_workflow_state(graph_state)
    except Exception:
        failed = True
    if failed or state is None:
        raise InvariantError()
    return "end" if state.status == "failed" else "continue"


def _route_after_approval(graph_state: AcademicWorkflowGraphState) -> str:
    try:
        state = _restore_persistent_workflow_state(graph_state)
    except Exception:
        raise InvariantError() from None
    if (
        state.phase == "outline_rejected"
        and state.status == "completed"
        and state.outcome is None
    ):
        return "end"
    if (
        state.phase == "outline_approved"
        and state.status == "running"
        and state.outcome is None
    ):
        return "compose"
    raise InvariantError() from None
