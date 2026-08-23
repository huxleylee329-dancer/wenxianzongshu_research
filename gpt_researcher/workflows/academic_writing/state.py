"""Strict serializable contracts for the academic-writing workflow."""

from __future__ import annotations

from typing import Annotated, Literal, TypeAlias, TypedDict

import json
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictInt,
    field_validator,
    model_validator,
)


JsonValue: TypeAlias = (
    None | bool | int | str | list["JsonValue"] | dict[str, "JsonValue"]
)
NodeId: TypeAlias = Literal[
    "topic_planner", "research_evidence", "outline_writer"
]
FailureCode: TypeAlias = Literal[
    "topic_planning_failed",
    "research_evidence_failed",
    "outline_writing_failed",
]
PositiveStrictInt: TypeAlias = Annotated[StrictInt, Field(gt=0)]


def _validate_fixed_one(value: object) -> Literal[1]:
    if type(value) is not int or value != 1:
        raise ValueError("value must be the strict integer one")
    return 1


FixedOne: TypeAlias = Annotated[Literal[1], BeforeValidator(_validate_fixed_one)]


class AcademicWorkflowGraphState(TypedDict):
    """The workflow's sole LangGraph channel."""

    workflow: dict[str, JsonValue]


class _StrictWorkflowModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


def _strip_nonblank(value: str, label: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{label} must not be blank")
    return normalized


def _normalize_unique_strings(values: tuple[str, ...], label: str) -> tuple[str, ...]:
    normalized = tuple(_strip_nonblank(value, label) for value in values)
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{label} values must be unique")
    return normalized


class AcademicWorkflowIdentity(_StrictWorkflowModel):
    workflow_id: str
    thread_id: str
    run_id: str

    @field_validator("workflow_id", "thread_id", "run_id")
    @classmethod
    def _normalize_identity(cls, value: str) -> str:
        return _strip_nonblank(value, "workflow identity")


class AcademicWorkflowRequest(_StrictWorkflowModel):
    workflow_mode: Literal["academic_langgraph"]
    workflow_id: str
    thread_id: str
    run_id: str
    query: str
    report_type: str
    report_source: str
    tone: str
    language: str
    source_urls: tuple[str, ...]
    document_urls: tuple[str, ...]
    query_domains: tuple[str, ...]
    max_search_results: PositiveStrictInt | None

    @field_validator(
        "workflow_id",
        "thread_id",
        "run_id",
        "query",
        "report_type",
        "report_source",
        "tone",
        "language",
    )
    @classmethod
    def _normalize_required_text(cls, value: str) -> str:
        return _strip_nonblank(value, "request value")

    @field_validator("source_urls", "document_urls", "query_domains")
    @classmethod
    def _normalize_ordered_values(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _normalize_unique_strings(values, "request collection")


class WorkflowTopicPlan(_StrictWorkflowModel):
    topic_plan_id: Literal["topic-plan:000001"]
    workflow_id: str
    run_id: str
    attempt: FixedOne
    research_topic: str
    research_questions: tuple[str, ...]

    @field_validator("workflow_id", "run_id", "research_topic")
    @classmethod
    def _normalize_text(cls, value: str) -> str:
        return _strip_nonblank(value, "topic-plan value")

    @field_validator("research_questions")
    @classmethod
    def _normalize_questions(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if not values:
            raise ValueError("research questions must not be empty")
        return _normalize_unique_strings(values, "research question")


class WorkflowEvidenceSource(_StrictWorkflowModel):
    source_id: str
    order: PositiveStrictInt
    title: str
    url: str
    candidate_id: str | None

    @field_validator("source_id")
    @classmethod
    def _normalize_source_id(cls, value: str) -> str:
        return _strip_nonblank(value, "evidence source id")

    @field_validator("title")
    @classmethod
    def _normalize_title(cls, value: str) -> str:
        normalized = _strip_nonblank(value, "evidence source title")
        if len(normalized) > 512:
            raise ValueError("evidence source title is too long")
        return normalized

    @field_validator("url")
    @classmethod
    def _normalize_url(cls, value: str) -> str:
        normalized = _strip_nonblank(value, "evidence source URL")
        if len(normalized) > 4096:
            raise ValueError("evidence source URL is too long")
        return normalized

    @field_validator("candidate_id")
    @classmethod
    def _normalize_candidate_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = _strip_nonblank(value, "candidate id")
        if len(normalized) > 256:
            raise ValueError("candidate id is too long")
        return normalized

    @model_validator(mode="after")
    def _validate_derived_id(self) -> "WorkflowEvidenceSource":
        if self.source_id != f"evidence-source:{self.order:06d}":
            raise ValueError("evidence source id must match order")
        return self


class WorkflowResearchEvidence(_StrictWorkflowModel):
    evidence_id: Literal["evidence:000001"]
    topic_plan_id: Literal["topic-plan:000001"]
    attempt: FixedOne
    context_blocks: tuple[str, ...]
    sources: tuple[WorkflowEvidenceSource, ...]

    @field_validator("context_blocks")
    @classmethod
    def _validate_context_blocks(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if not 1 <= len(values) <= 64:
            raise ValueError("evidence context block count is invalid")
        normalized: list[str] = []
        for value in values:
            block = _strip_nonblank(value, "evidence context block")
            if len(block) > 16384:
                raise ValueError("evidence context block is too long")
            normalized.append(block)
        if sum(len(block) for block in normalized) > 262144:
            raise ValueError("evidence context aggregate is too long")
        return tuple(normalized)

    @field_validator("sources")
    @classmethod
    def _validate_sources(
        cls, values: tuple[WorkflowEvidenceSource, ...]
    ) -> tuple[WorkflowEvidenceSource, ...]:
        if len(values) > 200:
            raise ValueError("evidence source count is too large")
        expected_orders = tuple(range(1, len(values) + 1))
        if tuple(source.order for source in values) != expected_orders:
            raise ValueError("evidence source order must be contiguous")
        urls = tuple(source.url for source in values)
        if len(set(urls)) != len(urls):
            raise ValueError("evidence source URLs must be unique")
        candidate_ids = tuple(
            source.candidate_id for source in values if source.candidate_id is not None
        )
        if len(set(candidate_ids)) != len(candidate_ids):
            raise ValueError("candidate ids must be unique")
        return values


class WorkflowOutlineSection(_StrictWorkflowModel):
    section_id: str
    order: PositiveStrictInt
    title: str
    brief: str

    @field_validator("section_id", "title", "brief")
    @classmethod
    def _normalize_text(cls, value: str) -> str:
        return _strip_nonblank(value, "outline section value")

    @model_validator(mode="after")
    def _validate_derived_id(self) -> "WorkflowOutlineSection":
        if self.section_id != f"section:{self.order:06d}":
            raise ValueError("section id must match order")
        return self


class WorkflowOutline(_StrictWorkflowModel):
    outline_id: Literal["outline:000001"]
    evidence_id: Literal["evidence:000001"]
    attempt: FixedOne
    title: str
    sections: tuple[WorkflowOutlineSection, ...]

    @field_validator("title")
    @classmethod
    def _normalize_title(cls, value: str) -> str:
        return _strip_nonblank(value, "outline title")

    @field_validator("sections")
    @classmethod
    def _validate_sections(
        cls, values: tuple[WorkflowOutlineSection, ...]
    ) -> tuple[WorkflowOutlineSection, ...]:
        if not values:
            raise ValueError("outline sections must not be empty")
        if tuple(section.order for section in values) != tuple(
            range(1, len(values) + 1)
        ):
            raise ValueError("outline section order must be contiguous")
        ids = tuple(section.section_id for section in values)
        if len(set(ids)) != len(ids):
            raise ValueError("outline section ids must be unique")
        titles = tuple(section.title for section in values)
        if len(set(titles)) != len(titles):
            raise ValueError("outline section titles must be unique")
        return values


class AdapterFailure(_StrictWorkflowModel):
    code: FailureCode


_FAILURE_NODE_BY_CODE: dict[FailureCode, NodeId] = {
    "topic_planning_failed": "topic_planner",
    "research_evidence_failed": "research_evidence",
    "outline_writing_failed": "outline_writer",
}


class WorkflowError(_StrictWorkflowModel):
    error_id: Literal["error:000001"]
    order: FixedOne
    failed_node_id: NodeId
    attempt: FixedOne
    code: FailureCode

    @model_validator(mode="after")
    def _validate_failure_mapping(self) -> "WorkflowError":
        if _FAILURE_NODE_BY_CODE[self.code] != self.failed_node_id:
            raise ValueError("workflow error code does not match failed node")
        return self


class WorkflowEvent(_StrictWorkflowModel):
    event_id: str
    order: PositiveStrictInt
    event_type: Literal[
        "node_started", "node_completed", "workflow_completed", "workflow_failed"
    ]
    node_id: NodeId | None
    attempt: FixedOne

    @field_validator("event_id")
    @classmethod
    def _normalize_event_id(cls, value: str) -> str:
        return _strip_nonblank(value, "event id")

    @model_validator(mode="after")
    def _validate_event(self) -> "WorkflowEvent":
        if self.event_id != f"event:{self.order:06d}":
            raise ValueError("event id must match order")
        if self.event_type == "workflow_completed":
            if self.node_id is not None:
                raise ValueError("workflow_completed must not name a node")
        elif self.node_id is None:
            raise ValueError("node and failure events must name a node")
        return self


def _event_projection(
    events: tuple[WorkflowEvent, ...]
) -> tuple[tuple[str, NodeId | None], ...]:
    return tuple((event.event_type, event.node_id) for event in events)


_TOPIC_SUCCESS = (
    ("node_started", "topic_planner"),
    ("node_completed", "topic_planner"),
)
_EVIDENCE_SUCCESS = _TOPIC_SUCCESS + (
    ("node_started", "research_evidence"),
    ("node_completed", "research_evidence"),
)
_OUTLINE_SUCCESS = _EVIDENCE_SUCCESS + (
    ("node_started", "outline_writer"),
    ("node_completed", "outline_writer"),
    ("workflow_completed", None),
)


class AcademicWorkflowState(_StrictWorkflowModel):
    schema_version: Literal["1"]
    workflow_id: str
    thread_id: str
    run_id: str
    phase: Literal[
        "initialized", "topic_planned", "evidence_collected", "outline_ready"
    ]
    status: Literal["running", "completed", "failed"]
    request: AcademicWorkflowRequest
    topic_plan: WorkflowTopicPlan | None
    research_evidence: WorkflowResearchEvidence | None
    outline: WorkflowOutline | None
    errors: tuple[WorkflowError, ...]
    events: tuple[WorkflowEvent, ...]

    @field_validator("workflow_id", "thread_id", "run_id")
    @classmethod
    def _normalize_identity(cls, value: str) -> str:
        return _strip_nonblank(value, "workflow state identity")

    @model_validator(mode="after")
    def _validate_reachable_shape(self) -> "AcademicWorkflowState":
        if (
            self.workflow_id,
            self.thread_id,
            self.run_id,
        ) != (
            self.request.workflow_id,
            self.request.thread_id,
            self.request.run_id,
        ):
            raise ValueError("state and request identity must match")

        if self.topic_plan is not None and (
            self.topic_plan.workflow_id != self.workflow_id
            or self.topic_plan.run_id != self.run_id
        ):
            raise ValueError("topic plan identity must match state")
        if self.research_evidence is not None and (
            self.topic_plan is None
            or self.research_evidence.topic_plan_id != self.topic_plan.topic_plan_id
        ):
            raise ValueError("evidence must reference the topic plan")
        if self.outline is not None and (
            self.research_evidence is None
            or self.outline.evidence_id != self.research_evidence.evidence_id
        ):
            raise ValueError("outline must reference evidence")

        shape = (self.phase, self.status)
        expected_artifacts: tuple[bool, bool, bool]
        expected_events: tuple[tuple[str, NodeId | None], ...]
        expected_failure_node: NodeId | None = None
        if shape == ("initialized", "running"):
            expected_artifacts = (False, False, False)
            expected_events = ()
        elif shape == ("topic_planned", "running"):
            expected_artifacts = (True, False, False)
            expected_events = _TOPIC_SUCCESS
        elif shape == ("evidence_collected", "running"):
            expected_artifacts = (True, True, False)
            expected_events = _EVIDENCE_SUCCESS
        elif shape == ("outline_ready", "completed"):
            expected_artifacts = (True, True, True)
            expected_events = _OUTLINE_SUCCESS
        elif shape == ("initialized", "failed"):
            expected_artifacts = (False, False, False)
            expected_failure_node = "topic_planner"
            expected_events = (
                ("node_started", "topic_planner"),
                ("workflow_failed", "topic_planner"),
            )
        elif shape == ("topic_planned", "failed"):
            expected_artifacts = (True, False, False)
            expected_failure_node = "research_evidence"
            expected_events = _TOPIC_SUCCESS + (
                ("node_started", "research_evidence"),
                ("workflow_failed", "research_evidence"),
            )
        elif shape == ("evidence_collected", "failed"):
            expected_artifacts = (True, True, False)
            expected_failure_node = "outline_writer"
            expected_events = _EVIDENCE_SUCCESS + (
                ("node_started", "outline_writer"),
                ("workflow_failed", "outline_writer"),
            )
        else:
            raise ValueError("unreachable workflow phase/status")

        artifacts = (
            self.topic_plan is not None,
            self.research_evidence is not None,
            self.outline is not None,
        )
        if artifacts != expected_artifacts:
            raise ValueError("workflow artifacts do not match phase/status")
        if _event_projection(self.events) != expected_events:
            raise ValueError("workflow events do not match phase/status")
        if tuple(event.order for event in self.events) != tuple(
            range(1, len(self.events) + 1)
        ):
            raise ValueError("workflow event order must be contiguous")

        if expected_failure_node is None:
            if self.errors:
                raise ValueError("running/completed workflow cannot contain errors")
        elif (
            len(self.errors) != 1
            or self.errors[0].failed_node_id != expected_failure_node
        ):
            raise ValueError("failed workflow must contain its exact error")
        return self


class ExecutionError(RuntimeError):
    """A fixed safe wrapper for unknown adapter exceptions."""

    def __init__(self) -> None:
        super().__init__("academic workflow execution failed")


class InvariantError(RuntimeError):
    """A fixed safe wrapper for contract and node invariant failures."""

    def __init__(self) -> None:
        super().__init__("academic workflow invariant violation")


_THREAD_MESSAGES = {
    "missing": "academic workflow checkpoint does not exist",
    "identity": "academic workflow identity does not match checkpoint",
    "exists": "academic workflow thread already exists",
    "not_resumable": "academic workflow thread is not resumable",
}


class ThreadProtocolError(RuntimeError):
    """A fixed safe thread-protocol failure."""

    def __init__(
        self, kind: Literal["missing", "identity", "exists", "not_resumable"]
    ) -> None:
        super().__init__(_THREAD_MESSAGES[kind])


def validate_json_value(value: object) -> None:
    """Reject every value outside the strict application JSON domain."""

    value_type = type(value)
    if value is None or value_type in (bool, int, str):
        return
    if value_type is list:
        for item in value:  # type: ignore[union-attr]
            validate_json_value(item)
        return
    if value_type is dict:
        for key, item in value.items():  # type: ignore[union-attr]
            if type(key) is not str:
                raise TypeError("JSON object keys must be exact strings")
            validate_json_value(item)
        return
    raise TypeError("value is outside the strict JSON domain")


def workflow_to_graph_state(
    state: AcademicWorkflowState,
) -> AcademicWorkflowGraphState:
    """Dump a strict DTO into the graph's single JSON-compatible channel."""

    if type(state) is not AcademicWorkflowState:
        raise TypeError("state must be an AcademicWorkflowState")
    workflow = state.model_dump(mode="json")
    validate_json_value(workflow)
    return {"workflow": workflow}


def canonical_workflow_bytes(graph_state: AcademicWorkflowGraphState) -> bytes:
    """Encode the graph payload with the one frozen canonical JSON form."""

    if type(graph_state) is not dict or set(graph_state) != {"workflow"}:
        raise TypeError("graph state must contain exactly the workflow channel")
    workflow = graph_state["workflow"]
    if type(workflow) is not dict:
        raise TypeError("workflow channel must be a JSON object")
    validate_json_value(workflow)
    return json.dumps(
        workflow,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def restore_workflow_state(
    graph_state: AcademicWorkflowGraphState,
) -> AcademicWorkflowState:
    """Restore and strictly revalidate a checkpoint payload through JSON."""

    return AcademicWorkflowState.model_validate_json(
        canonical_workflow_bytes(graph_state)
    )
