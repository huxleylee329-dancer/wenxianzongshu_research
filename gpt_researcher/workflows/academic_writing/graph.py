"""Private LangGraph factory and guarded workflow facades."""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from .adapters import AcademicWritingAdapter
from .nodes import _make_nodes, _route_after_node, _validated_dto
from .state import (
    AcademicWorkflowGraphState,
    AcademicWorkflowIdentity,
    AcademicWorkflowRequest,
    AcademicWorkflowState,
    InvariantError,
    ThreadProtocolError,
    restore_workflow_state,
    workflow_to_graph_state,
)


def _build_graph(
    adapter: AcademicWritingAdapter,
    checkpointer: BaseCheckpointSaver,
):
    topic_planner, research_evidence, outline_writer = _make_nodes(adapter)
    builder = StateGraph(AcademicWorkflowGraphState)
    builder.add_node("topic_planner", topic_planner)
    builder.add_node("research_evidence", research_evidence)
    builder.add_node("outline_writer", outline_writer)
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
        {"continue": END, "end": END},
    )
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


async def start_academic_workflow(
    request: AcademicWorkflowRequest,
    adapter: AcademicWritingAdapter,
    *,
    checkpointer: BaseCheckpointSaver,
) -> AcademicWorkflowState:
    """Start a new guarded workflow thread."""

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
            errors=(),
            events=(),
        )
        result = await graph.ainvoke(
            workflow_to_graph_state(initial_state), config=config
        )
        return _safe_restore(result)

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

    result = await graph.ainvoke(None, config=config)
    return _safe_restore(result)
