"""Private LangGraph factory and guarded workflow facades."""

from __future__ import annotations

import json

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, Interrupt, PregelTask, StateSnapshot

from .adapters import AcademicWritingAdapter
from .report_profiles import _FIXED_REPORT_MODES
from .nodes import (
    _OutlineApproveCommitError,
    _OutlineRejectCommitError,
    _approval_interrupt_payload,
    _make_nodes,
    _route_after_node,
    _validated_dto,
)
from .state import (
    AcademicOutlineDecisionCommand,
    AcademicWorkflowGraphState,
    AcademicWorkflowIdentity,
    AcademicWorkflowRequest,
    AcademicWorkflowState,
    InvariantError,
    OutlineDecisionProtocolError,
    ThreadProtocolError,
    _outline_digest,
    _outline_digest_equal,
    restore_workflow_state,
    validate_json_value,
    workflow_to_graph_state,
)


_APPROVE_ERROR_MARKER = (
    "_OutlineApproveCommitError('academic outline decision commit failed')"
)
_REJECT_ERROR_MARKER = (
    "_OutlineRejectCommitError('academic outline decision commit failed')"
)
_DECISION_INVALID = object()
_DECISION_MISSING = object()
_DECISION_IDENTITY = object()
_DECISION_COMMITTED = object()
_DECISION_UNAVAILABLE = object()
_DECISION_OUTLINE = object()
_DECISION_DIGEST = object()
_DECISION_RETRY = object()
_DECISION_INVARIANT = object()
_DECISION_APPROVE_COMMIT = object()
_DECISION_REJECT_COMMIT = object()

_REQUEST_FIELDS = (
    "workflow_mode",
    "workflow_id",
    "thread_id",
    "run_id",
    "query",
    "report_type",
    "report_source",
    "tone",
    "language",
    "source_urls",
    "document_urls",
    "query_domains",
    "max_search_results",
    "report_mode",
    "report_locale",
)
_REQUEST_OLD_FIELDS = frozenset(_REQUEST_FIELDS[:-2])
_REQUEST_ALLOWED_FIELD_SETS = (
    frozenset(_REQUEST_FIELDS),
    _REQUEST_OLD_FIELDS | {"report_mode"},
    _REQUEST_OLD_FIELDS | {"report_locale"},
    _REQUEST_OLD_FIELDS,
)


def _build_graph(
    adapter: AcademicWritingAdapter,
    checkpointer: BaseCheckpointSaver,
):
    topic_planner, research_evidence, outline_writer, outline_approval = _make_nodes(
        adapter
    )
    builder = StateGraph(AcademicWorkflowGraphState)
    builder.add_node("topic_planner", topic_planner)
    builder.add_node("research_evidence", research_evidence)
    builder.add_node("outline_writer", outline_writer)
    builder.add_node("outline_approval", outline_approval)
    builder.add_edge(START, "topic_planner")
    builder.add_conditional_edges(
        "topic_planner",
        _route_after_node,
        {"continue": "research_evidence", "end": END},
    )
    builder.add_conditional_edges(
        "research_evidence",
        _route_after_node,
        {"continue": "outline_writer", "end": END},
    )
    builder.add_conditional_edges(
        "outline_writer",
        _route_after_node,
        {"continue": "outline_approval", "end": END},
    )
    builder.add_edge("outline_approval", END)
    return builder.compile(checkpointer=checkpointer)


def _safe_input(value: object, expected_type: type):
    failed = False
    validated = None
    try:
        validated = _validated_dto(value, expected_type)
    except Exception:
        failed = True
    if failed or validated is None:
        raise InvariantError()
    return validated


def _inspect_start_profile(value: object) -> tuple[bool, bool, str, str | None]:
    failed = False
    result: tuple[bool, bool, str, str | None] | None = None
    try:
        if type(value) is not AcademicWorkflowRequest:
            raise TypeError("request type is invalid")
        namespace = object.__getattribute__(value, "__dict__")
        fields_set = object.__getattribute__(value, "__pydantic_fields_set__")
        extra = object.__getattribute__(value, "__pydantic_extra__")
        private = object.__getattribute__(value, "__pydantic_private__")
        if type(namespace) is not dict or type(fields_set) is not set:
            raise TypeError("request surface is invalid")
        if extra is not None or private is not None:
            raise TypeError("request state is invalid")
        keys = tuple(dict.keys(namespace))
        members = tuple(set.__iter__(fields_set))
        if any(type(key) is not str for key in keys) or any(
            type(member) is not str for member in members
        ):
            raise TypeError("request field names are invalid")
        if frozenset(keys) != frozenset(_REQUEST_FIELDS) or not any(
            fields_set == allowed for allowed in _REQUEST_ALLOWED_FIELD_SETS
        ):
            raise TypeError("request fields are invalid")
        mode = dict.__getitem__(namespace, "report_mode")
        locale = dict.__getitem__(namespace, "report_locale")
        if type(mode) is not str or (locale is not None and type(locale) is not str):
            raise TypeError("request profile values are invalid")
        result = (
            "report_mode" in fields_set,
            "report_locale" in fields_set,
            mode,
            locale,
        )
    except Exception:
        failed = True
    if failed or result is None:
        raise InvariantError() from None
    return result


def _safe_restore(graph_state: object) -> AcademicWorkflowState:
    failed = False
    state: AcademicWorkflowState | None = None
    try:
        if type(graph_state) is not dict:
            raise TypeError("graph result must be a mapping")
        state = restore_workflow_state(graph_state)
    except Exception:
        failed = True
    if failed or state is None:
        raise InvariantError()
    return state


def _identity_matches(
    state: AcademicWorkflowState, identity: AcademicWorkflowIdentity
) -> bool:
    return (
        state.workflow_id,
        state.thread_id,
        state.run_id,
    ) == (
        identity.workflow_id,
        identity.thread_id,
        identity.run_id,
    )


def _canonical_mapping(value: object) -> bytes | None:
    try:
        if type(value) is not dict:
            return None
        validate_json_value(value)
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except Exception:
        return None


def _approval_shape(
    snapshot: object, state: AcademicWorkflowState
) -> str | None:
    if (
        state.phase != "outline_ready"
        or state.status != "running"
        or state.outline is None
        or state.outline_decision is not None
        or state.errors
        or len(state.events) != 6
    ):
        return None
    if type(snapshot) is not StateSnapshot:
        return None
    next_nodes = snapshot.next
    tasks = snapshot.tasks
    if (
        type(next_nodes) is not tuple
        or any(type(node) is not str for node in next_nodes)
        or type(tasks) is not tuple
        or len(tasks) != 1
    ):
        return None
    task = tasks[0]
    if type(task) is not PregelTask:
        return None
    task_id = task.id
    task_name = task.name
    task_path = task.path
    task_error = task.error
    task_interrupts = task.interrupts
    task_result = task.result
    task_state = task.state
    if (
        type(task_id) is not str
        or not task_id
        or type(task_name) is not str
        or type(task_path) is not tuple
        or any(type(part) is not str for part in task_path)
        or type(task_interrupts) is not tuple
        or len(task_interrupts) != 1
    ):
        return None
    if (
        task_name != "outline_approval"
        or type(task_path) is not tuple
        or task_path != ("__pregel_pull", "outline_approval")
        or task_state is not None
    ):
        return None
    task_interrupt = task_interrupts[0]
    if type(task_interrupt) is not Interrupt:
        return None
    interrupt_value = task_interrupt.value
    expected_interrupt = _approval_interrupt_payload(state)
    if _canonical_mapping(interrupt_value) != _canonical_mapping(expected_interrupt):
        return None
    if next_nodes == ("outline_approval",):
        if task_error is None and task_result is None:
            return "pause"
        return None
    if next_nodes != () or type(task_result) is not dict or task_result != {}:
        return None
    if type(task_error) is not str:
        return None
    if task_error == _APPROVE_ERROR_MARKER:
        return "approve"
    if task_error == _REJECT_ERROR_MARKER:
        return "reject"
    return None


def _safe_decision_command(
    value: object,
) -> AcademicOutlineDecisionCommand | None:
    command: AcademicOutlineDecisionCommand | None = None
    try:
        command = _validated_dto(value, AcademicOutlineDecisionCommand)
    except Exception:
        return None
    return command


def _raise_decision_facade_failure(failure: object) -> None:
    if failure is _DECISION_INVALID:
        raise OutlineDecisionProtocolError("invalid") from None
    if failure is _DECISION_MISSING:
        raise OutlineDecisionProtocolError("missing") from None
    if failure is _DECISION_IDENTITY:
        raise OutlineDecisionProtocolError("identity") from None
    if failure is _DECISION_COMMITTED:
        raise OutlineDecisionProtocolError("committed") from None
    if failure is _DECISION_UNAVAILABLE:
        raise OutlineDecisionProtocolError("unavailable") from None
    if failure is _DECISION_OUTLINE:
        raise OutlineDecisionProtocolError("outline") from None
    if failure is _DECISION_DIGEST:
        raise OutlineDecisionProtocolError("digest") from None
    if failure is _DECISION_RETRY:
        raise OutlineDecisionProtocolError("retry") from None
    if failure is _DECISION_APPROVE_COMMIT:
        raise _OutlineApproveCommitError() from None
    if failure is _DECISION_REJECT_COMMIT:
        raise _OutlineRejectCommitError() from None
    raise InvariantError() from None


async def start_academic_workflow(
    request: AcademicWorkflowRequest,
    adapter: AcademicWritingAdapter,
    *,
    checkpointer: BaseCheckpointSaver,
) -> AcademicWorkflowState:
    """Start a new guarded workflow thread."""

    mode_explicit, locale_explicit, selected_mode, selected_locale = (
        _inspect_start_profile(request)
    )
    validated_request = _safe_input(request, AcademicWorkflowRequest)
    identity = AcademicWorkflowIdentity(
        workflow_id=validated_request.workflow_id,
        thread_id=validated_request.thread_id,
        run_id=validated_request.run_id,
    )
    graph = _build_graph(adapter, checkpointer)
    config: RunnableConfig = {
        "configurable": {"thread_id": identity.thread_id}
    }
    snapshot = await graph.aget_state(config)
    if snapshot.created_at is None:
        if (
            not mode_explicit
            or not locale_explicit
            or selected_mode not in _FIXED_REPORT_MODES
            or selected_locale != "zh-CN"
        ):
            raise InvariantError() from None
        initial_state = AcademicWorkflowState(
            schema_version="1",
            workflow_id=identity.workflow_id,
            thread_id=identity.thread_id,
            run_id=identity.run_id,
            phase="initialized",
            status="running",
            request=validated_request,
            topic_plan=None,
            research_evidence=None,
            outline=None,
            outline_decision=None,
            errors=(),
            events=(),
        )
        await graph.ainvoke(
            workflow_to_graph_state(initial_state), config=config
        )
        completed_snapshot = await graph.aget_state(config)
        completed_state = _safe_restore(completed_snapshot.values)
        if completed_state.status == "failed":
            return completed_state
        if _approval_shape(completed_snapshot, completed_state) != "pause":
            raise InvariantError() from None
        return completed_state

    checkpoint_state = _safe_restore(snapshot.values)
    if not _identity_matches(checkpoint_state, identity):
        raise ThreadProtocolError("identity")
    raise ThreadProtocolError("exists")


async def resume_academic_workflow(
    identity: AcademicWorkflowIdentity,
    adapter: AcademicWritingAdapter,
    *,
    checkpointer: BaseCheckpointSaver,
) -> AcademicWorkflowState:
    """Resume the single pending node of an existing workflow thread."""

    validated_identity = _safe_input(identity, AcademicWorkflowIdentity)
    graph = _build_graph(adapter, checkpointer)
    config: RunnableConfig = {
        "configurable": {"thread_id": validated_identity.thread_id}
    }
    snapshot = await graph.aget_state(config)
    if snapshot.created_at is None:
        raise ThreadProtocolError("missing")

    checkpoint_state = _safe_restore(snapshot.values)
    if not _identity_matches(checkpoint_state, validated_identity):
        raise ThreadProtocolError("identity")
    if checkpoint_state.status in ("completed", "failed"):
        raise ThreadProtocolError("not_resumable")
    approval_shape = _approval_shape(snapshot, checkpoint_state)
    if approval_shape is not None:
        raise ThreadProtocolError("approval_required")
    if checkpoint_state.phase == "outline_ready":
        raise InvariantError() from None
    if snapshot.next == ():
        raise ThreadProtocolError("not_resumable")

    expected_nodes = {
        "initialized": ("topic_planner",),
        "topic_planned": ("research_evidence",),
        "evidence_collected": ("outline_writer",),
    }
    expected = expected_nodes.get(checkpoint_state.phase)
    if expected is None or snapshot.next != expected:
        raise InvariantError()

    await graph.ainvoke(None, config=config)
    resumed_snapshot = await graph.aget_state(config)
    resumed_state = _safe_restore(resumed_snapshot.values)
    if resumed_state.status == "failed":
        return resumed_state
    if _approval_shape(resumed_snapshot, resumed_state) != "pause":
        raise InvariantError() from None
    return resumed_state


async def submit_academic_outline_decision(
    command: AcademicOutlineDecisionCommand,
    adapter: AcademicWritingAdapter,
    *,
    checkpointer: BaseCheckpointSaver,
) -> AcademicWorkflowState:
    """Submit one strictly bound decision to an available outline checkpoint."""

    async def _sensitive_decision_attempt() -> AcademicWorkflowState | object:
        validated_command = _safe_decision_command(command)
        if validated_command is None:
            return _DECISION_INVALID
        graph = _build_graph(adapter, checkpointer)
        config: RunnableConfig = {
            "configurable": {"thread_id": validated_command.thread_id}
        }
        snapshot = await graph.aget_state(config)
        if snapshot.created_at is None:
            return _DECISION_MISSING

        try:
            checkpoint_state = _safe_restore(snapshot.values)
        except InvariantError:
            return _DECISION_INVARIANT
        if (
            checkpoint_state.workflow_id,
            checkpoint_state.thread_id,
            checkpoint_state.run_id,
        ) != (
            validated_command.workflow_id,
            validated_command.thread_id,
            validated_command.run_id,
        ):
            return _DECISION_IDENTITY
        if (
            checkpoint_state.outline_decision is not None
            or checkpoint_state.phase in ("outline_approved", "outline_rejected")
            or checkpoint_state.status in ("completed", "failed")
        ):
            return _DECISION_COMMITTED
        if (
            checkpoint_state.phase != "outline_ready"
            or checkpoint_state.status != "running"
            or checkpoint_state.outline is None
        ):
            return _DECISION_UNAVAILABLE
        if checkpoint_state.outline.outline_id != validated_command.outline_id:
            return _DECISION_OUTLINE
        expected_digest = _outline_digest(checkpoint_state.outline)
        if not _outline_digest_equal(
            expected_digest, validated_command.outline_digest
        ):
            return _DECISION_DIGEST

        shape = _approval_shape(snapshot, checkpoint_state)
        if shape is None:
            return _DECISION_INVARIANT
        if shape in ("approve", "reject") and shape != validated_command.decision:
            return _DECISION_RETRY

        resume_mapping = validated_command.model_dump(mode="json")
        try:
            await graph.ainvoke(Command(resume=resume_mapping), config=config)
        except _OutlineApproveCommitError:
            return _DECISION_APPROVE_COMMIT
        except _OutlineRejectCommitError:
            return _DECISION_REJECT_COMMIT
        terminal_snapshot = await graph.aget_state(config)
        try:
            terminal_state = _safe_restore(terminal_snapshot.values)
        except InvariantError:
            return _DECISION_INVARIANT
        expected_phase = (
            "outline_approved"
            if validated_command.decision == "approve"
            else "outline_rejected"
        )
        if (
            terminal_state.phase != expected_phase
            or terminal_state.status != "completed"
            or terminal_state.outline_decision is None
            or terminal_state.outline_decision.decision != validated_command.decision
            or terminal_snapshot.next != ()
        ):
            return _DECISION_INVARIANT
        return terminal_state

    outcome = await _sensitive_decision_attempt()
    if type(outcome) is AcademicWorkflowState:
        return outcome
    failure = outcome
    del command, adapter, checkpointer, outcome, _sensitive_decision_attempt
    _raise_decision_facade_failure(failure)
    raise InvariantError()
