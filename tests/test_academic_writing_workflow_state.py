"""Contract tests for the academic-writing workflow DTO and JSON boundary."""

from __future__ import annotations

import asyncio
import io
import json
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from gpt_researcher.workflows.academic_writing.state import (
    AcademicWorkflowIdentity,
    AcademicWorkflowRequest,
    AcademicWorkflowState,
    AdapterFailure,
    WorkflowOutlineDecisionRecord,
    WorkflowError,
    WorkflowEvent,
    WorkflowEvidenceSource,
    WorkflowOutline,
    WorkflowOutlineSection,
    WorkflowResearchEvidence,
    WorkflowSectionDraft,
    WorkflowTopicPlan,
    canonical_workflow_bytes,
    restore_workflow_state,
    validate_json_value,
    workflow_to_graph_state,
)


def _request_data(**changes: Any) -> dict[str, Any]:
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
        "source_urls": ("https://example.test/source",),
        "document_urls": (),
        "query_domains": ("example.test",),
        "max_search_results": 5,
    }
    data.update(changes)
    return data


def _request(**changes: Any) -> AcademicWorkflowRequest:
    return AcademicWorkflowRequest(**_request_data(**changes))


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


def _source(order: int = 1, **changes: Any) -> WorkflowEvidenceSource:
    data: dict[str, Any] = {
        "source_id": f"evidence-source:{order:06d}",
        "order": order,
        "title": f"Source {order}",
        "url": f"https://example.test/paper/{order}",
        "candidate_id": f"candidate-{order}",
    }
    data.update(changes)
    return WorkflowEvidenceSource(**data)


def _evidence(**changes: Any) -> WorkflowResearchEvidence:
    data: dict[str, Any] = {
        "evidence_id": "evidence:000001",
        "topic_plan_id": "topic-plan:000001",
        "attempt": 1,
        "context_blocks": ("Bounded evidence.",),
        "sources": (_source(),),
    }
    data.update(changes)
    return WorkflowResearchEvidence(**data)


def _section(order: int = 1, **changes: Any) -> WorkflowOutlineSection:
    data: dict[str, Any] = {
        "section_id": f"section:{order:06d}",
        "order": order,
        "title": f"Section {order}",
        "brief": f"Brief {order}",
    }
    data.update(changes)
    return WorkflowOutlineSection(**data)


def _outline(**changes: Any) -> WorkflowOutline:
    data: dict[str, Any] = {
        "outline_id": "outline:000001",
        "evidence_id": "evidence:000001",
        "attempt": 1,
        "title": "Deterministic outline",
        "sections": (_section(),),
    }
    data.update(changes)
    return WorkflowOutline(**data)


def _event(order: int, event_type: str, node_id: str | None) -> WorkflowEvent:
    return WorkflowEvent(
        event_id=f"event:{order:06d}",
        order=order,
        event_type=event_type,
        node_id=node_id,
        attempt=1,
    )


TOPIC_EVENTS = (
    _event(1, "node_started", "topic_planner"),
    _event(2, "node_completed", "topic_planner"),
)
EVIDENCE_EVENTS = TOPIC_EVENTS + (
    _event(3, "node_started", "research_evidence"),
    _event(4, "node_completed", "research_evidence"),
)
PAUSE_EVENTS = EVIDENCE_EVENTS + (
    _event(5, "node_started", "outline_writer"),
    _event(6, "node_completed", "outline_writer"),
)
APPROVE_EVENTS = PAUSE_EVENTS + (
    _event(7, "node_started", "outline_approval"),
    _event(8, "node_completed", "outline_approval"),
    _event(9, "workflow_completed", None),
)
REJECT_EVENTS = PAUSE_EVENTS + (
    _event(7, "node_started", "outline_approval"),
    _event(8, "node_completed", "outline_approval"),
    _event(9, "workflow_rejected", None),
)


def _state(**changes: Any) -> AcademicWorkflowState:
    data: dict[str, Any] = {
        "schema_version": "1",
        "workflow_id": "workflow-1",
        "thread_id": "thread-1",
        "run_id": "run-1",
        "phase": "initialized",
        "status": "running",
        "request": _request(),
        "topic_plan": None,
        "research_evidence": None,
        "outline": None,
        "outline_decision": None,
        "errors": (),
        "events": (),
    }
    data.update(changes)
    return AcademicWorkflowState(**data)


def _decision(
    decision: str, outline: WorkflowOutline
) -> WorkflowOutlineDecisionRecord:
    return WorkflowOutlineDecisionRecord(
        decision_id="outline-decision:000001",
        schema_version="1",
        workflow_id="workflow-1",
        thread_id="thread-1",
        run_id="run-1",
        outline_id="outline:000001",
        outline_digest=(
            "f770a9a8afdcd0e06031956653848d588"
            "8d9071451a80a68d85d97f1dd0bc1e1"
        ),
        decision=decision,
        actor_assertion="actor-A",
        attempt=1,
    )


def _assert_recursive_type_equality(left: Any, right: Any) -> None:
    assert type(left) is type(right)
    if isinstance(left, list):
        assert len(left) == len(right)
        for left_item, right_item in zip(left, right, strict=True):
            _assert_recursive_type_equality(left_item, right_item)
    elif isinstance(left, dict):
        assert left.keys() == right.keys()
        for key in left:
            _assert_recursive_type_equality(left[key], right[key])


def test_identity_and_request_are_strict_frozen_and_normalized() -> None:
    identity = AcademicWorkflowIdentity(
        workflow_id=" workflow-1 ", thread_id=" thread-1 ", run_id=" run-1 "
    )
    assert identity.workflow_id == "workflow-1"
    assert identity.thread_id == "thread-1"
    assert identity.run_id == "run-1"

    request = _request(query="  Deterministic research  ")
    assert request.query == "Deterministic research"
    with pytest.raises(ValidationError):
        request.query = "changed"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        AcademicWorkflowIdentity(
            workflow_id="workflow-1",
            thread_id="thread-1",
            run_id="run-1",
            extra="forbidden",
        )

    invalid_changes = (
        {"workflow_id": " "},
        {"max_search_results": True},
        {"max_search_results": 0},
        {"source_urls": ["https://example.test/source"]},
        {"source_urls": ("https://same.test", "https://same.test")},
        {"query_domains": (" ",)},
    )
    for changes in invalid_changes:
        with pytest.raises(ValidationError):
            _request(**changes)


def test_json_array_restores_tuple_but_python_list_is_rejected() -> None:
    python_data = _request_data(source_urls=["https://example.test/source"])
    with pytest.raises(ValidationError):
        AcademicWorkflowRequest(**python_data)

    json_request = AcademicWorkflowRequest.model_validate_json(
        json.dumps(python_data, separators=(",", ":"), sort_keys=True)
    )
    assert json_request.source_urls == ("https://example.test/source",)
    assert type(json_request.source_urls) is tuple


def test_topic_plan_outline_and_derived_orders_are_strict() -> None:
    plan = _plan(research_questions=(" Question one ", "Question two"))
    assert plan.research_questions == ("Question one", "Question two")
    for questions in ((), ("same", "same"), (" ",)):
        with pytest.raises(ValidationError):
            _plan(research_questions=questions)

    with pytest.raises(ValidationError):
        _section(order=2, section_id="section:000001")
    with pytest.raises(ValidationError):
        _section(order=True)
    with pytest.raises(ValidationError):
        _outline(sections=())
    with pytest.raises(ValidationError):
        _outline(
            sections=(
                _section(1, title="Duplicate"),
                _section(2, title=" Duplicate "),
            )
        )
    with pytest.raises(ValidationError):
        _outline(sections=(_section(2), _section(1)))


def _fixed_one_model(case: str, value: object) -> object:
    if case == "topic_plan_attempt":
        return _plan(attempt=value)
    if case == "research_evidence_attempt":
        return _evidence(attempt=value)
    if case == "outline_attempt":
        return _outline(attempt=value)
    if case == "workflow_error_order":
        return WorkflowError(
            error_id="error:000001",
            order=value,
            failed_node_id="topic_planner",
            attempt=1,
            code="topic_planning_failed",
        )
    if case == "workflow_error_attempt":
        return WorkflowError(
            error_id="error:000001",
            order=1,
            failed_node_id="topic_planner",
            attempt=value,
            code="topic_planning_failed",
        )
    if case == "workflow_event_attempt":
        return WorkflowEvent(
            event_id="event:000001",
            order=1,
            event_type="node_started",
            node_id="topic_planner",
            attempt=value,
        )
    raise AssertionError(f"unknown fixed-one case: {case}")


@pytest.mark.parametrize(
    ("case", "field"),
    [
        ("topic_plan_attempt", "attempt"),
        ("research_evidence_attempt", "attempt"),
        ("outline_attempt", "attempt"),
        ("workflow_error_order", "order"),
        ("workflow_error_attempt", "attempt"),
        ("workflow_event_attempt", "attempt"),
    ],
)
def test_every_fixed_one_field_rejects_bool_and_coercion(
    case: str, field: str
) -> None:
    python_valid = _fixed_one_model(case, 1)
    assert getattr(python_valid, field) == 1
    assert type(getattr(python_valid, field)) is int
    for invalid in (True, False, "1", 1.0, 0, 2):
        with pytest.raises(ValidationError):
            _fixed_one_model(case, invalid)

    model_type = type(python_valid)
    valid_payload = python_valid.model_dump(mode="json")
    json_valid = model_type.model_validate_json(
        json.dumps(valid_payload, ensure_ascii=False, allow_nan=False)
    )
    assert getattr(json_valid, field) == 1
    assert type(getattr(json_valid, field)) is int
    for invalid in (True, False, "1", 1.0, 0, 2):
        invalid_payload = dict(valid_payload)
        invalid_payload[field] = invalid
        with pytest.raises(ValidationError):
            model_type.model_validate_json(
                json.dumps(invalid_payload, ensure_ascii=False, allow_nan=False)
            )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("title", "x" * 513),
        ("url", "x" * 4097),
        ("candidate_id", "x" * 257),
        ("title", " "),
        ("url", " "),
        ("candidate_id", " "),
    ],
)
def test_evidence_source_field_limits(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        _source(**{field: value})


def test_evidence_collection_item_and_aggregate_limits() -> None:
    sources = tuple(_source(index) for index in range(1, 201))
    evidence = _evidence(sources=sources, context_blocks=("x" * 16384,) * 16)
    assert len(evidence.sources) == 200
    assert sum(map(len, evidence.context_blocks)) == 262144

    invalid_values = (
        {"context_blocks": ()},
        {"context_blocks": (" ",)},
        {"context_blocks": ("x" * 16385,)},
        {"context_blocks": ("x",) * 65},
        {"context_blocks": ("x" * 16384,) * 17},
        {"sources": tuple(_source(index) for index in range(1, 202))},
        {"sources": (_source(1), _source(2, url=_source(1).url))},
        {"sources": (_source(1), _source(2, candidate_id="candidate-1"))},
        {"sources": (_source(2), _source(1))},
    )
    for changes in invalid_values:
        with pytest.raises(ValidationError):
            _evidence(**changes)


def test_failure_error_and_event_contracts_are_closed() -> None:
    failure = AdapterFailure(code="topic_planning_failed")
    assert failure.model_dump() == {"code": "topic_planning_failed"}
    assert not isinstance(failure, BaseException)

    with pytest.raises(ValidationError):
        AdapterFailure(code="unknown")
    with pytest.raises(ValidationError):
        WorkflowError(
            error_id="error:000001",
            order=1,
            failed_node_id="topic_planner",
            attempt=1,
            code="research_evidence_failed",
        )
    with pytest.raises(ValidationError):
        WorkflowEvent(
            event_id="event:000001",
            order=2,
            event_type="node_started",
            node_id="topic_planner",
            attempt=1,
        )
    with pytest.raises(ValidationError):
        _event(1, "workflow_completed", "topic_planner")
    with pytest.raises(ValidationError):
        _event(1, "node_completed", None)


def test_all_reachable_phase_status_shapes_validate() -> None:
    plan = _plan()
    evidence = _evidence()
    outline = _outline()
    valid_states = (
        _state(),
        _state(phase="topic_planned", topic_plan=plan, events=TOPIC_EVENTS),
        _state(
            phase="evidence_collected",
            topic_plan=plan,
            research_evidence=evidence,
            events=EVIDENCE_EVENTS,
        ),
        _state(
            phase="outline_ready",
            topic_plan=plan,
            research_evidence=evidence,
            outline=outline,
            events=PAUSE_EVENTS,
        ),
        _state(
            phase="outline_approved",
            status="completed",
            topic_plan=plan,
            research_evidence=evidence,
            outline=outline,
            outline_decision=_decision("approve", outline),
            events=APPROVE_EVENTS,
        ),
        _state(
            phase="outline_rejected",
            status="completed",
            topic_plan=plan,
            research_evidence=evidence,
            outline=outline,
            outline_decision=_decision("reject", outline),
            events=REJECT_EVENTS,
        ),
        _state(
            status="failed",
            errors=(
                WorkflowError(
                    error_id="error:000001",
                    order=1,
                    failed_node_id="topic_planner",
                    attempt=1,
                    code="topic_planning_failed",
                ),
            ),
            events=(
                _event(1, "node_started", "topic_planner"),
                _event(2, "workflow_failed", "topic_planner"),
            ),
        ),
        _state(
            phase="topic_planned",
            status="failed",
            topic_plan=plan,
            errors=(
                WorkflowError(
                    error_id="error:000001",
                    order=1,
                    failed_node_id="research_evidence",
                    attempt=1,
                    code="research_evidence_failed",
                ),
            ),
            events=TOPIC_EVENTS
            + (
                _event(3, "node_started", "research_evidence"),
                _event(4, "workflow_failed", "research_evidence"),
            ),
        ),
        _state(
            phase="evidence_collected",
            status="failed",
            topic_plan=plan,
            research_evidence=evidence,
            errors=(
                WorkflowError(
                    error_id="error:000001",
                    order=1,
                    failed_node_id="outline_writer",
                    attempt=1,
                    code="outline_writing_failed",
                ),
            ),
            events=EVIDENCE_EVENTS
            + (
                _event(5, "node_started", "outline_writer"),
                _event(6, "workflow_failed", "outline_writer"),
            ),
        ),
    )
    assert [state.phase for state in valid_states] == [
        "initialized",
        "topic_planned",
        "evidence_collected",
        "outline_ready",
        "outline_approved",
        "outline_rejected",
        "initialized",
        "topic_planned",
        "evidence_collected",
    ]


def test_unreachable_shapes_and_cross_references_are_rejected() -> None:
    with pytest.raises(ValidationError):
        _state(status="completed")
    with pytest.raises(ValidationError):
        _state(phase="topic_planned", topic_plan=_plan(), events=())
    with pytest.raises(ValidationError):
        _state(workflow_id="other")
    with pytest.raises(ValidationError):
        _state(
            phase="topic_planned",
            topic_plan=_plan(run_id="other"),
            events=TOPIC_EVENTS,
        )
    with pytest.raises(ValidationError):
        _state(
            phase="evidence_collected",
            topic_plan=_plan(),
            research_evidence=_evidence(topic_plan_id="topic-plan:wrong"),
            events=EVIDENCE_EVENTS,
        )
    with pytest.raises(ValidationError):
        _state(
            phase="outline_ready",
            status="completed",
            topic_plan=_plan(),
            research_evidence=_evidence(),
            outline=_outline(evidence_id="evidence:wrong"),
            events=PAUSE_EVENTS,
        )


def test_application_json_round_trip_preserves_values_types_model_and_bytes() -> None:
    state = _state(
        phase="outline_ready",
        request=_request(query="确定性研究", language="zh"),
        topic_plan=_plan(),
        research_evidence=_evidence(),
        outline=_outline(),
        events=PAUSE_EVENTS,
    )
    graph_state = workflow_to_graph_state(state)
    assert set(graph_state) == {"workflow"}
    assert isinstance(graph_state["workflow"], dict)
    assert isinstance(graph_state["workflow"]["events"], list)
    validate_json_value(graph_state["workflow"])

    before = graph_state["workflow"]
    before_bytes = canonical_workflow_bytes(graph_state)
    restored = restore_workflow_state(graph_state)
    after_state = workflow_to_graph_state(restored)
    after = after_state["workflow"]
    assert before == after
    _assert_recursive_type_equality(before, after)
    assert restored == state
    assert canonical_workflow_bytes(after_state) == before_bytes
    assert before_bytes == json.dumps(
        before,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    assert "确定性研究".encode("utf-8") in before_bytes
    assert b"\\u786e" not in before_bytes


class _ForbiddenEnum(Enum):
    VALUE = "value"


@pytest.mark.parametrize(
    "value",
    [
        1.0,
        float("nan"),
        float("inf"),
        ("tuple",),
        {"set"},
        b"bytes",
        Path("forbidden"),
        datetime(2025, 1, 1),
        _ForbiddenEnum.VALUE,
        RuntimeError("forbidden"),
        asyncio.Lock(),
        asyncio.Queue(),
        io.StringIO("forbidden"),
        object(),
        int,
        lambda: None,
        {1: "non-string-key"},
        {"nested": ["ok", {"bad": 1.0}]},
    ],
)
def test_recursive_json_validator_rejects_forbidden_values(value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        validate_json_value(value)


def test_recursive_json_validator_accepts_only_exact_json_types() -> None:
    value = {
        "none": None,
        "bool": True,
        "int": 1,
        "str": "value",
        "list": [None, False, 2, "nested", {"key": []}],
    }
    validate_json_value(value)


def test_section_draft_is_strict_frozen_canonical_and_does_not_extend_state() -> None:
    draft = WorkflowSectionDraft(
        outline_id="outline:000001",
        section_id="  existing/custom-section  ",
        attempt=1,
        content="  章节正文  ",
    )
    expected = {
        "outline_id": "outline:000001",
        "section_id": "  existing/custom-section  ",
        "attempt": 1,
        "content": "章节正文",
    }
    assert draft.model_dump(mode="json") == expected
    encoded = json.dumps(
        expected,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    restored = WorkflowSectionDraft.model_validate_json(encoded)
    assert restored == draft
    assert json.dumps(
        restored.model_dump(mode="json"),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8") == encoded
    with pytest.raises(ValidationError):
        draft.content = "changed"  # type: ignore[misc]
    assert "section_draft" not in AcademicWorkflowState.model_fields
    assert "section_draft" not in _state().model_dump(mode="json")


@pytest.mark.parametrize(
    "changes",
    [
        {"outline_id": "outline:other"},
        {"section_id": "   "},
        {"attempt": True},
        {"attempt": False},
        {"attempt": 2},
        {"attempt": 1.0},
        {"attempt": "1"},
        {"content": "\t\r\n"},
        {"content": "x" * 24577},
        {"extra": "forbidden"},
    ],
)
def test_section_draft_rejects_invalid_python_shapes(changes: dict[str, object]) -> None:
    data: dict[str, object] = {
        "outline_id": "outline:000001",
        "section_id": "section:any-existing-id",
        "attempt": 1,
        "content": "x" * 24576,
    }
    data.update(changes)
    with pytest.raises((TypeError, ValidationError)):
        WorkflowSectionDraft.model_validate(data)


def test_section_draft_rejects_mapping_and_string_subclasses_and_json_attempts() -> None:
    class MappingSubclass(dict[str, object]):
        pass

    class StringSubclass(str):
        pass

    valid: dict[str, object] = {
        "outline_id": "outline:000001",
        "section_id": "section:any-existing-id",
        "attempt": 1,
        "content": "body",
    }
    with pytest.raises((TypeError, ValidationError)):
        WorkflowSectionDraft.model_validate(MappingSubclass(valid))
    for field in ("outline_id", "section_id", "content"):
        changed = dict(valid)
        changed[field] = StringSubclass(str(changed[field]))
        with pytest.raises((TypeError, ValidationError)):
            WorkflowSectionDraft.model_validate(changed)
    for attempt in (True, False, 0, 2, 1.0, "1", None):
        changed = dict(valid, attempt=attempt)
        with pytest.raises((TypeError, ValidationError)):
            WorkflowSectionDraft.model_validate_json(json.dumps(changed))
