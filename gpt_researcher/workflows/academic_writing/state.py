"""Strict serializable contracts for the academic-writing workflow."""

from __future__ import annotations

from typing import Annotated, Literal, TypeAlias, TypedDict

import hashlib
import hmac
import json
import unicodedata
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictInt,
    ValidationInfo,
    field_validator,
    model_validator,
)

from .report_profiles import ReportMode, _get_report_profile, _is_known_section_role


JsonValue: TypeAlias = (
    None | bool | int | str | list["JsonValue"] | dict[str, "JsonValue"]
)
NodeId: TypeAlias = Literal[
    "topic_planner",
    "research_evidence",
    "outline_writer",
    "outline_approval",
    "academic_draft_composer",
]
OutlineDecision: TypeAlias = Literal["approve", "reject"]
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


def _bounded_identity(value: str, label: str) -> str:
    normalized = _strip_nonblank(value, label)
    if len(normalized) > 256:
        raise ValueError(f"{label} is too long")
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
        return _bounded_identity(value, "workflow identity")


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
    report_mode: ReportMode = "freeform"
    report_locale: Literal["zh-CN"] | None = None

    @model_validator(mode="before")
    @classmethod
    def _validate_legacy_field_shape(
        cls, value: object, info: ValidationInfo
    ) -> object:
        if type(value) is cls:
            return value
        if type(value) is not dict:
            raise TypeError("workflow request must be an exact mapping")
        keys = tuple(dict.keys(value))
        if any(type(key) is not str for key in keys):
            raise TypeError("workflow request keys must be exact strings")
        old = {
            "workflow_mode", "workflow_id", "thread_id", "run_id", "query",
            "report_type", "report_source", "tone", "language", "source_urls",
            "document_urls", "query_domains", "max_search_results",
        }
        supplied = set(keys)
        allowed = (
            old | {"report_mode", "report_locale"},
            old | {"report_locale"},
            old | {"report_mode"},
            old,
        )
        if not any(supplied == shape for shape in allowed):
            raise TypeError("workflow request fields are invalid")
        if info.mode == "json":
            copied = dict(value)
            for name in ("source_urls", "document_urls", "query_domains"):
                item = dict.__getitem__(copied, name)
                if type(item) is list:
                    copied[name] = tuple(item)
            return copied
        return value

    @field_validator("report_mode", mode="before")
    @classmethod
    def _validate_report_mode_type(cls, value: object) -> object:
        if type(value) is not str:
            raise TypeError("report mode must be an exact string")
        return value

    @field_validator("report_locale", mode="before")
    @classmethod
    def _validate_report_locale_type(cls, value: object) -> object:
        if value is not None and type(value) is not str:
            raise TypeError("report locale must be an exact string or null")
        return value

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

    @field_validator("workflow_id", "thread_id", "run_id")
    @classmethod
    def _bound_identity(cls, value: str) -> str:
        return _bounded_identity(value, "request identity")

    @field_validator("source_urls", "document_urls", "query_domains")
    @classmethod
    def _normalize_ordered_values(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _normalize_unique_strings(values, "request collection")

    @model_validator(mode="after")
    def _validate_report_selection(self) -> "AcademicWorkflowRequest":
        if self.report_mode == "freeform":
            if self.report_locale is not None:
                raise ValueError("freeform report locale must be null")
        elif self.report_locale != "zh-CN":
            raise ValueError("fixed report locale must be zh-CN")
        return self


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

    @field_validator("workflow_id", "run_id")
    @classmethod
    def _bound_identity(cls, value: str) -> str:
        return _bounded_identity(value, "topic-plan identity")

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


class WorkflowEvidenceProvenance(_StrictWorkflowModel):
    source_id: str
    evidence_blocks: tuple[str, ...]

    @model_validator(mode="before")
    @classmethod
    def _require_exact_python_input(
        cls, value: object, info: ValidationInfo
    ) -> object:
        if type(value) is cls:
            return value
        if type(value) is not dict:
            raise TypeError("evidence provenance must be an exact mapping")
        keys = tuple(value.keys())  # type: ignore[union-attr]
        if any(type(key) is not str for key in keys) or set(keys) != {
            "source_id",
            "evidence_blocks",
        }:
            raise TypeError("evidence provenance fields must be exact")
        source_id = value["source_id"]  # type: ignore[index]
        blocks = value["evidence_blocks"]  # type: ignore[index]
        if type(source_id) is not str:
            raise TypeError("evidence provenance source id must be exact")
        expected_container = list if info.mode == "json" else tuple
        if type(blocks) is not expected_container:
            raise TypeError("evidence provenance blocks container must be exact")
        if any(type(block) is not str for block in blocks):
            raise TypeError("evidence provenance blocks must be exact strings")
        if info.mode == "json":
            copied = dict(value)
            copied["evidence_blocks"] = tuple(blocks)
            return copied
        return value

    @field_validator("source_id")
    @classmethod
    def _validate_source_id(cls, value: str) -> str:
        prefix = "evidence-source:"
        suffix = value[len(prefix) :]
        if (
            not value.startswith(prefix)
            or len(suffix) != 6
            or not suffix.isascii()
            or not suffix.isdigit()
        ):
            raise ValueError("evidence provenance source id is invalid")
        order = int(suffix)
        if not 1 <= order <= 200 or value != f"{prefix}{order:06d}":
            raise ValueError("evidence provenance source id is invalid")
        return value

    @field_validator("evidence_blocks")
    @classmethod
    def _validate_evidence_blocks(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if not 1 <= len(values) <= 64:
            raise ValueError("evidence provenance block count is invalid")
        for block in values:
            if not block.strip():
                raise ValueError("evidence provenance block must not be blank")
            if len(block) > 16384:
                raise ValueError("evidence provenance block is too long")
        return values


class WorkflowResearchEvidence(_StrictWorkflowModel):
    evidence_id: Literal["evidence:000001"]
    topic_plan_id: Literal["topic-plan:000001"]
    attempt: FixedOne
    context_blocks: tuple[str, ...]
    sources: tuple[WorkflowEvidenceSource, ...]
    provenance: tuple[WorkflowEvidenceProvenance, ...] = ()

    @field_validator("provenance", mode="before")
    @classmethod
    def _require_exact_provenance_container(
        cls, value: object, info: ValidationInfo
    ) -> object:
        expected_container = list if info.mode == "json" else tuple
        if type(value) is not expected_container:
            raise TypeError("evidence provenance container must be exact")
        if info.mode != "json" and any(
            type(item) is not WorkflowEvidenceProvenance for item in value
        ):
            raise TypeError("evidence provenance members must be exact")
        if info.mode == "json":
            return tuple(value)
        return value

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

    @model_validator(mode="after")
    def _validate_provenance_binding(self) -> "WorkflowResearchEvidence":
        if len(self.provenance) > 64:
            raise ValueError("evidence provenance count is too large")
        block_count = sum(
            len(entry.evidence_blocks) for entry in self.provenance
        )
        if block_count > 64:
            raise ValueError("evidence provenance block aggregate is too large")
        character_count = sum(
            len(block)
            for entry in self.provenance
            for block in entry.evidence_blocks
        )
        if character_count > 262144:
            raise ValueError("evidence provenance character aggregate is too large")
        source_positions = {
            source.source_id: index for index, source in enumerate(self.sources)
        }
        positions: list[int] = []
        seen_ids: set[str] = set()
        for entry in self.provenance:
            if entry.source_id in seen_ids or entry.source_id not in source_positions:
                raise ValueError("evidence provenance source binding is invalid")
            seen_ids.add(entry.source_id)
            positions.append(source_positions[entry.source_id])
        if positions != sorted(positions):
            raise ValueError("evidence provenance source order is invalid")
        return self


class WorkflowOutlineSection(_StrictWorkflowModel):
    section_id: str
    order: PositiveStrictInt
    title: str
    brief: str
    section_role: str = "freeform"

    @model_validator(mode="before")
    @classmethod
    def _validate_legacy_field_shape(
        cls, value: object, info: ValidationInfo
    ) -> object:
        if type(value) is cls:
            return value
        if type(value) is not dict:
            raise TypeError("outline section must be an exact mapping")
        keys = tuple(dict.keys(value))
        if any(type(key) is not str for key in keys):
            raise TypeError("outline section keys must be exact strings")
        old = {"section_id", "order", "title", "brief"}
        supplied = set(keys)
        if supplied not in (old, old | {"section_role"}):
            raise TypeError("outline section fields are invalid")
        return value

    @field_validator("section_role", mode="before")
    @classmethod
    def _validate_section_role_type(cls, value: object) -> object:
        if type(value) is not str:
            raise TypeError("section role must be an exact string")
        return value

    @field_validator("section_id", "title", "brief")
    @classmethod
    def _normalize_text(cls, value: str) -> str:
        return _strip_nonblank(value, "outline section value")

    @model_validator(mode="after")
    def _validate_derived_id(self) -> "WorkflowOutlineSection":
        if self.section_id != f"section:{self.order:06d}":
            raise ValueError("section id must match order")
        role = self.section_role
        if not 1 <= len(role) <= 48:
            raise ValueError("section role length is invalid")
        parts = role.split("_")
        if any(
            not part
            or not part.isascii()
            or not part[0].islower()
            or not part[0].isalpha()
            or any(not (character.islower() or character.isdigit()) for character in part)
            for part in parts
        ):
            raise ValueError("section role is invalid")
        if not _is_known_section_role(role):
            raise ValueError("section role is unknown")
        return self


class WorkflowOutline(_StrictWorkflowModel):
    outline_id: Literal["outline:000001"]
    evidence_id: Literal["evidence:000001"]
    attempt: FixedOne
    title: str
    sections: tuple[WorkflowOutlineSection, ...]
    report_mode: ReportMode = "freeform"
    report_locale: Literal["zh-CN"] | None = None

    @model_validator(mode="before")
    @classmethod
    def _validate_legacy_field_shape(
        cls, value: object, info: ValidationInfo
    ) -> object:
        if type(value) is cls:
            return value
        if type(value) is not dict:
            raise TypeError("outline must be an exact mapping")
        keys = tuple(dict.keys(value))
        if any(type(key) is not str for key in keys):
            raise TypeError("outline keys must be exact strings")
        old = {"outline_id", "evidence_id", "attempt", "title", "sections"}
        supplied = set(keys)
        allowed = (
            old | {"report_mode", "report_locale"},
            old | {"report_locale"},
            old | {"report_mode"},
            old,
        )
        if not any(supplied == shape for shape in allowed):
            raise TypeError("outline fields are invalid")
        if info.mode == "json":
            copied = dict(value)
            sections = dict.__getitem__(copied, "sections")
            if type(sections) is list:
                copied["sections"] = tuple(sections)
            return copied
        return value

    @field_validator("report_mode", mode="before")
    @classmethod
    def _validate_report_mode_type(cls, value: object) -> object:
        if type(value) is not str:
            raise TypeError("report mode must be an exact string")
        return value

    @field_validator("report_locale", mode="before")
    @classmethod
    def _validate_report_locale_type(cls, value: object) -> object:
        if value is not None and type(value) is not str:
            raise TypeError("report locale must be an exact string or null")
        return value

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

    @model_validator(mode="after")
    def _validate_profile(self) -> "WorkflowOutline":
        if self.report_mode == "freeform":
            if self.report_locale is not None or any(
                section.section_role != "freeform" for section in self.sections
            ):
                raise ValueError("freeform outline profile is invalid")
            return self
        profile = _get_report_profile(self.report_mode)
        if self.report_locale != "zh-CN" or profile is None:
            raise ValueError("fixed outline profile is invalid")
        actual = tuple((section.section_role, section.title) for section in self.sections)
        if actual != profile:
            raise ValueError("fixed outline sections do not match profile")
        return self


class WorkflowSectionDraft(_StrictWorkflowModel):
    outline_id: Literal["outline:000001"]
    section_id: str
    attempt: FixedOne
    content: str

    @model_validator(mode="before")
    @classmethod
    def _require_exact_python_input(cls, value: object) -> object:
        if type(value) is cls:
            return value
        if type(value) is not dict:
            raise TypeError("section draft must be an exact mapping")
        for field_name in ("outline_id", "section_id", "content"):
            if field_name in value and type(value[field_name]) is not str:
                raise TypeError("section draft strings must be exact")
        if "attempt" in value and type(value["attempt"]) is not int:
            raise TypeError("section draft attempt must be exact")
        return value

    @field_validator("section_id")
    @classmethod
    def _validate_section_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("section draft section id must not be blank")
        return value

    @field_validator("content")
    @classmethod
    def _normalize_content(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("section draft content must not be blank")
        if len(normalized) > 24576:
            raise ValueError("section draft content is too long")
        return normalized


def _validate_outline_digest(value: str) -> str:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError("outline digest must be lowercase SHA-256 hexadecimal")
    return value


def _validate_actor_assertion(value: str) -> str:
    normalized = _strip_nonblank(value, "actor assertion")
    if len(normalized) > 256:
        raise ValueError("actor assertion is too long")
    if any(
        character in "\r\n\x00" or unicodedata.category(character) == "Cc"
        for character in normalized
    ):
        raise ValueError("actor assertion contains a control character")
    return normalized


class AcademicOutlineDecisionCommand(_StrictWorkflowModel):
    schema_version: Literal["1"]
    workflow_id: str
    thread_id: str
    run_id: str
    outline_id: Literal["outline:000001"]
    outline_digest: str
    decision: OutlineDecision
    actor_assertion: str

    @model_validator(mode="before")
    @classmethod
    def _require_exact_python_input(cls, value: object) -> object:
        if type(value) is cls:
            return value
        if type(value) is not dict:
            raise TypeError("outline decision command must be an exact mapping")
        for field_name in (
            "schema_version",
            "workflow_id",
            "thread_id",
            "run_id",
            "outline_id",
            "outline_digest",
            "decision",
            "actor_assertion",
        ):
            if field_name in value and type(value[field_name]) is not str:
                raise TypeError("outline decision command strings must be exact")
        return value

    @field_validator("workflow_id", "thread_id", "run_id")
    @classmethod
    def _normalize_identity(cls, value: str) -> str:
        return _bounded_identity(value, "outline decision identity")

    @field_validator("outline_digest")
    @classmethod
    def _normalize_digest(cls, value: str) -> str:
        return _validate_outline_digest(value)

    @field_validator("actor_assertion")
    @classmethod
    def _normalize_actor(cls, value: str) -> str:
        return _validate_actor_assertion(value)

    @model_validator(mode="after")
    def _validate_canonical_length(self) -> "AcademicOutlineDecisionCommand":
        payload = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if len(payload) > 2048:
            raise ValueError("outline decision command is too long")
        return self


class WorkflowOutlineDecisionRecord(_StrictWorkflowModel):
    decision_id: Literal["outline-decision:000001"]
    schema_version: Literal["1"]
    workflow_id: str
    thread_id: str
    run_id: str
    outline_id: Literal["outline:000001"]
    outline_digest: str
    decision: OutlineDecision
    actor_assertion: str
    attempt: FixedOne

    @model_validator(mode="before")
    @classmethod
    def _require_exact_python_input(cls, value: object) -> object:
        if type(value) is cls:
            return value
        if type(value) is not dict:
            raise TypeError("outline decision record must be an exact mapping")
        for field_name in (
            "decision_id",
            "schema_version",
            "workflow_id",
            "thread_id",
            "run_id",
            "outline_id",
            "outline_digest",
            "decision",
            "actor_assertion",
        ):
            if field_name in value and type(value[field_name]) is not str:
                raise TypeError("outline decision record strings must be exact")
        if "attempt" in value and type(value["attempt"]) is not int:
            raise TypeError("outline decision record attempt must be exact")
        return value

    @field_validator("workflow_id", "thread_id", "run_id")
    @classmethod
    def _normalize_identity(cls, value: str) -> str:
        return _bounded_identity(value, "outline decision record identity")

    @field_validator("outline_digest")
    @classmethod
    def _normalize_digest(cls, value: str) -> str:
        return _validate_outline_digest(value)

    @field_validator("actor_assertion")
    @classmethod
    def _normalize_actor(cls, value: str) -> str:
        return _validate_actor_assertion(value)


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
        "node_started",
        "node_completed",
        "workflow_completed",
        "workflow_rejected",
        "workflow_failed",
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
        if self.event_type in ("workflow_completed", "workflow_rejected"):
            if self.node_id is not None:
                raise ValueError("terminal workflow events must not name a node")
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
_OUTLINE_PAUSE = _EVIDENCE_SUCCESS + (
    ("node_started", "outline_writer"),
    ("node_completed", "outline_writer"),
)
_OUTLINE_APPROVAL_SUCCESS = _OUTLINE_PAUSE + (
    ("node_started", "outline_approval"),
    ("node_completed", "outline_approval"),
)
_OUTLINE_APPROVED = _OUTLINE_APPROVAL_SUCCESS + (
    ("workflow_completed", None),
)
_DRAFT_READY = _OUTLINE_APPROVAL_SUCCESS + (
    ("node_started", "academic_draft_composer"),
    ("node_completed", "academic_draft_composer"),
    ("workflow_completed", None),
)
_OUTLINE_REJECTED = _OUTLINE_PAUSE + (
    ("node_started", "outline_approval"),
    ("node_completed", "outline_approval"),
    ("workflow_rejected", None),
)


class AcademicWorkflowState(_StrictWorkflowModel):
    schema_version: Literal["1"]
    workflow_id: str
    thread_id: str
    run_id: str
    phase: Literal[
        "initialized",
        "topic_planned",
        "evidence_collected",
        "outline_ready",
        "outline_approved",
        "outline_rejected",
        "draft_ready",
        "review_required",
    ]
    status: Literal["running", "completed", "failed"]
    request: AcademicWorkflowRequest
    topic_plan: WorkflowTopicPlan | None
    research_evidence: WorkflowResearchEvidence | None
    outline: WorkflowOutline | None
    outline_decision: WorkflowOutlineDecisionRecord | None
    errors: tuple[WorkflowError, ...]
    events: tuple[WorkflowEvent, ...]

    @field_validator("workflow_id", "thread_id", "run_id")
    @classmethod
    def _normalize_identity(cls, value: str) -> str:
        return _bounded_identity(value, "workflow state identity")

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
        if self.outline is not None:
            if (
                self.request.report_mode != self.outline.report_mode
                or self.request.report_locale != self.outline.report_locale
            ):
                raise ValueError("request and outline report profile must match")
            if self.outline.report_mode == "freeform":
                if any(
                    section.section_role != "freeform"
                    for section in self.outline.sections
                ):
                    raise ValueError("freeform outline profile is invalid")
            else:
                profile = _get_report_profile(self.outline.report_mode)
                actual = tuple(
                    (section.section_role, section.title)
                    for section in self.outline.sections
                )
                if profile is None or actual != profile:
                    raise ValueError("fixed outline profile is invalid")

        shape = (self.phase, self.status)
        expected_artifacts: tuple[bool, bool, bool]
        expected_events: tuple[tuple[str, NodeId | None], ...]
        expected_decision: OutlineDecision | None = None
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
        elif shape == ("outline_ready", "running"):
            expected_artifacts = (True, True, True)
            expected_events = _OUTLINE_PAUSE
        elif shape == ("outline_approved", "completed"):
            expected_artifacts = (True, True, True)
            expected_events = _OUTLINE_APPROVED
            expected_decision = "approve"
        elif shape == ("outline_approved", "running"):
            expected_artifacts = (True, True, True)
            expected_events = _OUTLINE_APPROVAL_SUCCESS
            expected_decision = "approve"
        elif shape in (
            ("draft_ready", "completed"),
            ("review_required", "completed"),
        ):
            expected_artifacts = (True, True, True)
            expected_events = _DRAFT_READY
            expected_decision = "approve"
        elif shape == ("outline_rejected", "completed"):
            expected_artifacts = (True, True, True)
            expected_events = _OUTLINE_REJECTED
            expected_decision = "reject"
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

        if expected_decision is None:
            if self.outline_decision is not None:
                raise ValueError("nonterminal workflow cannot contain a decision")
        else:
            decision = self.outline_decision
            if decision is None or self.outline is None:
                raise ValueError("terminal outline state requires a decision")
            if decision.decision != expected_decision:
                raise ValueError("outline decision does not match phase")
            if (
                decision.workflow_id,
                decision.thread_id,
                decision.run_id,
                decision.outline_id,
            ) != (
                self.workflow_id,
                self.thread_id,
                self.run_id,
                self.outline.outline_id,
            ):
                raise ValueError("outline decision identity must match state")
            if decision.outline_digest != _outline_digest(self.outline):
                raise ValueError("outline decision digest must match outline")
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
    "approval_required": "academic outline approval decision is required",
}


class ThreadProtocolError(RuntimeError):
    """A fixed safe thread-protocol failure."""

    def __init__(
        self,
        kind: Literal[
            "missing", "identity", "exists", "not_resumable", "approval_required"
        ],
    ) -> None:
        super().__init__(_THREAD_MESSAGES[kind])


_OUTLINE_DECISION_MESSAGES = {
    "invalid": "academic outline decision command is invalid",
    "missing": "academic workflow checkpoint does not exist",
    "identity": "academic workflow identity does not match checkpoint",
    "committed": "academic outline decision has already been committed",
    "unavailable": "academic outline decision is not available",
    "outline": "academic outline identity does not match checkpoint",
    "digest": "academic outline revision does not match checkpoint",
    "retry": "academic outline retry decision does not match failed attempt",
}


class OutlineDecisionProtocolError(RuntimeError):
    """A fixed safe outline-decision facade failure."""

    def __init__(
        self,
        kind: Literal[
            "invalid",
            "missing",
            "identity",
            "committed",
            "unavailable",
            "outline",
            "digest",
            "retry",
        ],
    ) -> None:
        super().__init__(_OUTLINE_DECISION_MESSAGES[kind])


def _canonical_outline_bytes(outline: WorkflowOutline) -> bytes:
    if type(outline) is not WorkflowOutline:
        raise TypeError("outline must be an exact WorkflowOutline")
    if outline.report_mode == "freeform":
        payload = {
            "outline_id": outline.outline_id,
            "evidence_id": outline.evidence_id,
            "attempt": outline.attempt,
            "title": outline.title,
            "sections": [
                {
                    "section_id": section.section_id,
                    "order": section.order,
                    "title": section.title,
                    "brief": section.brief,
                }
                for section in outline.sections
            ],
        }
    else:
        payload = outline.model_dump(mode="json")
    validate_json_value(payload)
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    restored = WorkflowOutline.model_validate_json(encoded)
    if type(restored) is not WorkflowOutline or restored != outline:
        raise ValueError("outline canonical round trip changed the model")
    restored_payload = (
        {
            "outline_id": restored.outline_id,
            "evidence_id": restored.evidence_id,
            "attempt": restored.attempt,
            "title": restored.title,
            "sections": [
                {
                    "section_id": section.section_id,
                    "order": section.order,
                    "title": section.title,
                    "brief": section.brief,
                }
                for section in restored.sections
            ],
        }
        if restored.report_mode == "freeform"
        else restored.model_dump(mode="json")
    )
    restored_encoded = json.dumps(
        restored_payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    if restored_payload != payload or restored_encoded != encoded:
        raise ValueError("outline canonical round trip changed bytes")
    return encoded


def _outline_digest(outline: WorkflowOutline) -> str:
    canonical = _canonical_outline_bytes(outline)
    if outline.report_mode == "freeform":
        return hashlib.sha256(canonical).hexdigest()
    return hashlib.sha256(b"academic-fixed-profile-v1\0" + canonical).hexdigest()


def _outline_digest_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left, right)


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
