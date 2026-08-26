"""Strict deterministic references renderer for approved academic drafts."""

from __future__ import annotations

import json as _json
from typing import Literal as _Literal

from pydantic import BaseModel as _BaseModel
from pydantic import ConfigDict as _ConfigDict
from pydantic import ValidationInfo as _ValidationInfo
from pydantic import field_validator as _field_validator
from pydantic import model_validator as _model_validator

from gpt_researcher.workflows.academic_writing.citation_evidence_gate import (
    WorkflowCitationEvidenceGateResult,
)
from gpt_researcher.workflows.academic_writing.citation_review_disposition import (
    WorkflowCitationReviewDisposition,
)
from gpt_researcher.workflows.academic_writing.section_merger import (
    WorkflowMergedDraft,
)
from gpt_researcher.workflows.academic_writing.state import (
    AcademicWorkflowRequest as _AcademicWorkflowRequest,
    AcademicWorkflowState,
    WorkflowEvidenceProvenance as _WorkflowEvidenceProvenance,
    WorkflowEvidenceSource as _WorkflowEvidenceSource,
    WorkflowEvent as _WorkflowEvent,
    WorkflowOutline as _WorkflowOutline,
    WorkflowOutlineDecisionRecord as _WorkflowOutlineDecisionRecord,
    WorkflowOutlineSection as _WorkflowOutlineSection,
    WorkflowResearchEvidence as _WorkflowResearchEvidence,
    WorkflowTopicPlan as _WorkflowTopicPlan,
)


__all__ = (
    "WorkflowReferencedDraft",
    "render_references",
)


_ERROR_TEXT = "references renderer failed"
_SECTION_MAX_COUNT = 12
_SOURCE_MAX_COUNT = 200
_CITATION_MAX_COUNT = 64
_GATE_MAX_BYTES = 19519
_DISPOSITION_MAX_BYTES = 575
_MERGED_MAX_CHARS = 359538
_CONTENT_MAX_CHARS = 5901246
_RESULT_MAX_BYTES = 8596240
_REFERENCES_PREFIX = "\n\n## References\n\n"
_CITATION_PREFIX = "[[cite:"
_CITATION_SUFFIX = "]]"

_STATE_FIELDS = (
    "schema_version",
    "workflow_id",
    "thread_id",
    "run_id",
    "phase",
    "status",
    "request",
    "topic_plan",
    "research_evidence",
    "outline",
    "outline_decision",
    "errors",
    "events",
)
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
)
_TOPIC_FIELDS = (
    "topic_plan_id",
    "workflow_id",
    "run_id",
    "attempt",
    "research_topic",
    "research_questions",
)
_EVIDENCE_FIELDS = (
    "evidence_id",
    "topic_plan_id",
    "attempt",
    "context_blocks",
    "sources",
    "provenance",
)
_SOURCE_FIELDS = ("source_id", "order", "title", "url", "candidate_id")
_PROVENANCE_FIELDS = ("source_id", "evidence_blocks")
_OUTLINE_FIELDS = ("outline_id", "evidence_id", "attempt", "title", "sections")
_SECTION_FIELDS = ("section_id", "order", "title", "brief")
_DECISION_FIELDS = (
    "decision_id",
    "schema_version",
    "workflow_id",
    "thread_id",
    "run_id",
    "outline_id",
    "outline_digest",
    "decision",
    "actor_assertion",
    "attempt",
)
_EVENT_FIELDS = ("event_id", "order", "event_type", "node_id", "attempt")
_MERGED_FIELDS = ("outline_id", "section_ids", "attempt", "content")
_GATE_FIELDS = (
    "outline_id",
    "section_ids",
    "cited_source_ids_by_section",
    "attempt",
)
_DISPOSITION_FIELDS = (
    "outline_id",
    "section_ids",
    "section_dispositions",
    "disposition",
    "attempt",
)
_RESULT_FIELDS = (
    "outline_id",
    "section_ids",
    "reference_source_ids",
    "attempt",
    "content",
)


class _ReferencesRendererError(RuntimeError):
    pass


class _Marker:
    __slots__ = ()


_FAILURE = _Marker()


def _copy_container_strings(
    value: object,
    expected_type: type[list[object]] | type[tuple[object, ...]],
) -> tuple[str, ...] | _Marker:
    if type(value) is not expected_type:
        return _FAILURE
    copied: list[str] = []
    length = list.__len__(value) if expected_type is list else tuple.__len__(value)
    index = 0
    while index < length:
        item = (
            list.__getitem__(value, index)
            if expected_type is list
            else tuple.__getitem__(value, index)
        )
        if type(item) is not str:
            return _FAILURE
        copied.append(item)
        index += 1
    return tuple(copied)


def _valid_source_id(value: object) -> bool:
    if type(value) is not str:
        return False
    prefix = "evidence-source:"
    suffix = value[len(prefix) :]
    if (
        not value.startswith(prefix)
        or len(suffix) != 6
        or not suffix.isascii()
        or not suffix.isdigit()
    ):
        return False
    order = int(suffix)
    return 1 <= order <= _SOURCE_MAX_COUNT and value == f"{prefix}{order:06d}"


class WorkflowReferencedDraft(_BaseModel):
    """Strict immutable draft with deterministic references appended."""

    model_config = _ConfigDict(frozen=True, extra="forbid", strict=True)

    outline_id: _Literal["outline:000001"]
    section_ids: tuple[str, ...]
    reference_source_ids: tuple[str, ...]
    attempt: _Literal[1]
    content: str

    @_model_validator(mode="before")
    @classmethod
    def _require_exact_input(cls, value: object, info: _ValidationInfo) -> object:
        if type(value) is cls:
            return value
        if type(value) is not dict:
            raise TypeError("referenced draft must be an exact mapping")
        keys = tuple(dict.keys(value))
        if any(type(key) is not str for key in keys) or set(keys) != set(
            _RESULT_FIELDS
        ):
            raise TypeError("referenced draft fields must be exact")
        outline_id = dict.__getitem__(value, "outline_id")
        section_ids_value = dict.__getitem__(value, "section_ids")
        reference_ids_value = dict.__getitem__(value, "reference_source_ids")
        attempt = dict.__getitem__(value, "attempt")
        content = dict.__getitem__(value, "content")
        expected_type = list if info.mode == "json" else tuple
        section_ids = _copy_container_strings(section_ids_value, expected_type)
        reference_ids = _copy_container_strings(reference_ids_value, expected_type)
        if (
            type(outline_id) is not str
            or type(section_ids) is not tuple
            or type(reference_ids) is not tuple
            or type(attempt) is not int
            or type(content) is not str
        ):
            raise TypeError("referenced draft values must use exact types")
        if info.mode == "json":
            return {
                "outline_id": outline_id,
                "section_ids": section_ids,
                "reference_source_ids": reference_ids,
                "attempt": attempt,
                "content": content,
            }
        return value

    @_field_validator("section_ids")
    @classmethod
    def _validate_section_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if not 1 <= tuple.__len__(values) <= _SECTION_MAX_COUNT:
            raise ValueError("referenced draft section count is invalid")
        index = 0
        while index < tuple.__len__(values):
            if tuple.__getitem__(values, index) != f"section:{index + 1:06d}":
                raise ValueError("referenced draft section ID is invalid")
            index += 1
        return values

    @_field_validator("reference_source_ids")
    @classmethod
    def _validate_reference_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if not 1 <= tuple.__len__(values) <= _SOURCE_MAX_COUNT:
            raise ValueError("referenced draft reference count is invalid")
        seen: set[str] = set()
        index = 0
        while index < tuple.__len__(values):
            value = tuple.__getitem__(values, index)
            if not _valid_source_id(value) or value in seen:
                raise ValueError("referenced draft reference ID is invalid")
            seen.add(value)
            index += 1
        return values

    @_model_validator(mode="after")
    def _validate_attempt_content_and_bytes(self) -> WorkflowReferencedDraft:
        if type(self.attempt) is not int or self.attempt != 1:
            raise ValueError("referenced draft attempt is invalid")
        if not self.content.strip() or len(self.content) > _CONTENT_MAX_CHARS:
            raise ValueError("referenced draft content is invalid")
        payload: dict[str, object] = {
            "outline_id": self.outline_id,
            "section_ids": list(self.section_ids),
            "reference_source_ids": list(self.reference_source_ids),
            "attempt": self.attempt,
            "content": self.content,
        }
        encoded = _canonical_bytes(payload)
        if type(encoded) is not bytes or len(encoded) > _RESULT_MAX_BYTES:
            raise ValueError("referenced draft canonical payload is too large")
        return self


def _surface(
    value: object,
    expected_type: type[object],
    fields: tuple[str, ...],
    allowed_field_sets: tuple[frozenset[str], ...] | None = None,
) -> tuple[dict[str, object], set[str]] | _Marker:
    if type(value) is not expected_type:
        return _FAILURE
    try:
        namespace = object.__getattribute__(value, "__dict__")
        fields_set = object.__getattribute__(value, "__pydantic_fields_set__")
        extra = object.__getattribute__(value, "__pydantic_extra__")
        private = object.__getattribute__(value, "__pydantic_private__")
        if type(namespace) is not dict or type(fields_set) is not set:
            return _FAILURE
        if extra is not None or private is not None:
            return _FAILURE
        keys = tuple(dict.keys(namespace))
        members = tuple(set.__iter__(fields_set))
        if any(type(key) is not str for key in keys):
            return _FAILURE
        if any(type(member) is not str for member in members):
            return _FAILURE
        if keys != fields:
            return _FAILURE
        allowed = (
            (frozenset(fields),)
            if allowed_field_sets is None
            else allowed_field_sets
        )
        if not any(fields_set == expected for expected in allowed):
            return _FAILURE
        return namespace, fields_set
    except Exception:
        return _FAILURE


def _copy_string_tuple(value: object) -> list[str] | _Marker:
    copied = _copy_container_strings(value, tuple)
    if type(copied) is not tuple:
        return _FAILURE
    return list(copied)


def _copy_request(value: object) -> dict[str, object] | _Marker:
    surface = _surface(value, _AcademicWorkflowRequest, _REQUEST_FIELDS)
    if type(surface) is not tuple:
        return _FAILURE
    namespace, _ = surface
    copied: dict[str, object] = {}
    for name in _REQUEST_FIELDS[:9]:
        item = dict.__getitem__(namespace, name)
        if type(item) is not str:
            return _FAILURE
        copied[name] = item
    for name in ("source_urls", "document_urls", "query_domains"):
        items = _copy_string_tuple(dict.__getitem__(namespace, name))
        if type(items) is not list:
            return _FAILURE
        copied[name] = items
    maximum = dict.__getitem__(namespace, "max_search_results")
    if maximum is not None and type(maximum) is not int:
        return _FAILURE
    copied["max_search_results"] = maximum
    return copied


def _copy_topic(value: object) -> dict[str, object] | _Marker:
    surface = _surface(value, _WorkflowTopicPlan, _TOPIC_FIELDS)
    if type(surface) is not tuple:
        return _FAILURE
    namespace, _ = surface
    copied: dict[str, object] = {}
    for name in ("topic_plan_id", "workflow_id", "run_id"):
        item = dict.__getitem__(namespace, name)
        if type(item) is not str:
            return _FAILURE
        copied[name] = item
    attempt = dict.__getitem__(namespace, "attempt")
    research_topic = dict.__getitem__(namespace, "research_topic")
    questions = _copy_string_tuple(dict.__getitem__(namespace, "research_questions"))
    if (
        type(attempt) is not int
        or type(research_topic) is not str
        or type(questions) is not list
    ):
        return _FAILURE
    copied["attempt"] = attempt
    copied["research_topic"] = research_topic
    copied["research_questions"] = questions
    return copied


def _copy_source(value: object) -> dict[str, object] | _Marker:
    surface = _surface(value, _WorkflowEvidenceSource, _SOURCE_FIELDS)
    if type(surface) is not tuple:
        return _FAILURE
    namespace, _ = surface
    source_id = dict.__getitem__(namespace, "source_id")
    order = dict.__getitem__(namespace, "order")
    title = dict.__getitem__(namespace, "title")
    url = dict.__getitem__(namespace, "url")
    candidate_id = dict.__getitem__(namespace, "candidate_id")
    if (
        type(source_id) is not str
        or type(order) is not int
        or type(title) is not str
        or type(url) is not str
    ):
        return _FAILURE
    if candidate_id is not None and type(candidate_id) is not str:
        return _FAILURE
    return {
        "source_id": source_id,
        "order": order,
        "title": title,
        "url": url,
        "candidate_id": candidate_id,
    }


def _copy_provenance(value: object) -> dict[str, object] | _Marker:
    surface = _surface(value, _WorkflowEvidenceProvenance, _PROVENANCE_FIELDS)
    if type(surface) is not tuple:
        return _FAILURE
    namespace, _ = surface
    source_id = dict.__getitem__(namespace, "source_id")
    blocks = _copy_string_tuple(dict.__getitem__(namespace, "evidence_blocks"))
    if type(source_id) is not str or type(blocks) is not list:
        return _FAILURE
    return {"source_id": source_id, "evidence_blocks": blocks}


def _copy_evidence(value: object) -> dict[str, object] | _Marker:
    full = frozenset(_EVIDENCE_FIELDS)
    without_provenance = frozenset(_EVIDENCE_FIELDS[:-1])
    surface = _surface(
        value,
        _WorkflowResearchEvidence,
        _EVIDENCE_FIELDS,
        (full, without_provenance),
    )
    if type(surface) is not tuple:
        return _FAILURE
    namespace, fields_set = surface
    provenance = dict.__getitem__(namespace, "provenance")
    if type(provenance) is not tuple:
        return _FAILURE
    if "provenance" not in fields_set and tuple.__len__(provenance) != 0:
        return _FAILURE
    copied: dict[str, object] = {}
    for name in ("evidence_id", "topic_plan_id"):
        item = dict.__getitem__(namespace, name)
        if type(item) is not str:
            return _FAILURE
        copied[name] = item
    attempt = dict.__getitem__(namespace, "attempt")
    contexts = _copy_string_tuple(dict.__getitem__(namespace, "context_blocks"))
    sources = dict.__getitem__(namespace, "sources")
    if type(attempt) is not int or type(contexts) is not list:
        return _FAILURE
    if type(sources) is not tuple:
        return _FAILURE
    copied_sources: list[dict[str, object]] = []
    index = 0
    while index < tuple.__len__(sources):
        source = _copy_source(tuple.__getitem__(sources, index))
        if type(source) is not dict:
            return _FAILURE
        copied_sources.append(source)
        index += 1
    copied_provenance: list[dict[str, object]] = []
    index = 0
    while index < tuple.__len__(provenance):
        entry = _copy_provenance(tuple.__getitem__(provenance, index))
        if type(entry) is not dict:
            return _FAILURE
        copied_provenance.append(entry)
        index += 1
    copied["attempt"] = attempt
    copied["context_blocks"] = contexts
    copied["sources"] = copied_sources
    copied["provenance"] = copied_provenance
    return copied


def _copy_outline_section(value: object) -> dict[str, object] | _Marker:
    surface = _surface(value, _WorkflowOutlineSection, _SECTION_FIELDS)
    if type(surface) is not tuple:
        return _FAILURE
    namespace, _ = surface
    section_id = dict.__getitem__(namespace, "section_id")
    order = dict.__getitem__(namespace, "order")
    title = dict.__getitem__(namespace, "title")
    brief = dict.__getitem__(namespace, "brief")
    if (
        type(section_id) is not str
        or type(order) is not int
        or type(title) is not str
        or type(brief) is not str
    ):
        return _FAILURE
    return {
        "section_id": section_id,
        "order": order,
        "title": title,
        "brief": brief,
    }


def _copy_outline(value: object) -> dict[str, object] | _Marker:
    surface = _surface(value, _WorkflowOutline, _OUTLINE_FIELDS)
    if type(surface) is not tuple:
        return _FAILURE
    namespace, _ = surface
    outline_id = dict.__getitem__(namespace, "outline_id")
    evidence_id = dict.__getitem__(namespace, "evidence_id")
    attempt = dict.__getitem__(namespace, "attempt")
    title = dict.__getitem__(namespace, "title")
    sections = dict.__getitem__(namespace, "sections")
    if (
        type(outline_id) is not str
        or type(evidence_id) is not str
        or type(attempt) is not int
        or type(title) is not str
        or type(sections) is not tuple
    ):
        return _FAILURE
    copied_sections: list[dict[str, object]] = []
    index = 0
    while index < tuple.__len__(sections):
        section = _copy_outline_section(tuple.__getitem__(sections, index))
        if type(section) is not dict:
            return _FAILURE
        copied_sections.append(section)
        index += 1
    return {
        "outline_id": outline_id,
        "evidence_id": evidence_id,
        "attempt": attempt,
        "title": title,
        "sections": copied_sections,
    }


def _copy_decision(value: object) -> dict[str, object] | _Marker:
    surface = _surface(value, _WorkflowOutlineDecisionRecord, _DECISION_FIELDS)
    if type(surface) is not tuple:
        return _FAILURE
    namespace, _ = surface
    copied: dict[str, object] = {}
    for name in _DECISION_FIELDS[:-1]:
        item = dict.__getitem__(namespace, name)
        if type(item) is not str:
            return _FAILURE
        copied[name] = item
    attempt = dict.__getitem__(namespace, "attempt")
    if type(attempt) is not int:
        return _FAILURE
    copied["attempt"] = attempt
    return copied


def _copy_event(value: object) -> dict[str, object] | _Marker:
    surface = _surface(value, _WorkflowEvent, _EVENT_FIELDS)
    if type(surface) is not tuple:
        return _FAILURE
    namespace, _ = surface
    event_id = dict.__getitem__(namespace, "event_id")
    order = dict.__getitem__(namespace, "order")
    event_type = dict.__getitem__(namespace, "event_type")
    node_id = dict.__getitem__(namespace, "node_id")
    attempt = dict.__getitem__(namespace, "attempt")
    if (
        type(event_id) is not str
        or type(order) is not int
        or type(event_type) is not str
        or type(attempt) is not int
    ):
        return _FAILURE
    if node_id is not None and type(node_id) is not str:
        return _FAILURE
    return {
        "event_id": event_id,
        "order": order,
        "event_type": event_type,
        "node_id": node_id,
        "attempt": attempt,
    }


def _copy_state_payload(value: object) -> dict[str, object] | _Marker:
    try:
        surface = _surface(value, AcademicWorkflowState, _STATE_FIELDS)
        if type(surface) is not tuple:
            return _FAILURE
        namespace, _ = surface
        copied: dict[str, object] = {}
        for name in (
            "schema_version",
            "workflow_id",
            "thread_id",
            "run_id",
            "phase",
            "status",
        ):
            item = dict.__getitem__(namespace, name)
            if type(item) is not str:
                return _FAILURE
            copied[name] = item
        request = _copy_request(dict.__getitem__(namespace, "request"))
        topic = _copy_topic(dict.__getitem__(namespace, "topic_plan"))
        evidence = _copy_evidence(dict.__getitem__(namespace, "research_evidence"))
        outline = _copy_outline(dict.__getitem__(namespace, "outline"))
        decision = _copy_decision(dict.__getitem__(namespace, "outline_decision"))
        if (
            type(request) is not dict
            or type(topic) is not dict
            or type(evidence) is not dict
            or type(outline) is not dict
            or type(decision) is not dict
        ):
            return _FAILURE
        errors = dict.__getitem__(namespace, "errors")
        events = dict.__getitem__(namespace, "events")
        if type(errors) is not tuple or tuple.__len__(errors) != 0:
            return _FAILURE
        if type(events) is not tuple:
            return _FAILURE
        copied_events: list[dict[str, object]] = []
        index = 0
        while index < tuple.__len__(events):
            event = _copy_event(tuple.__getitem__(events, index))
            if type(event) is not dict:
                return _FAILURE
            copied_events.append(event)
            index += 1
        copied["request"] = request
        copied["topic_plan"] = topic
        copied["research_evidence"] = evidence
        copied["outline"] = outline
        copied["outline_decision"] = decision
        copied["errors"] = []
        copied["events"] = copied_events
        return copied
    except Exception:
        return _FAILURE


def _validate_json_value(value: object) -> bool:
    value_type = type(value)
    if value is None or value_type in (bool, int, str):
        return True
    if value_type is list:
        index = 0
        while index < list.__len__(value):
            if not _validate_json_value(list.__getitem__(value, index)):
                return False
            index += 1
        return True
    if value_type is dict:
        for key in dict.keys(value):
            if type(key) is not str:
                return False
            if not _validate_json_value(dict.__getitem__(value, key)):
                return False
        return True
    return False


def _same_json_shape(left: object, right: object) -> bool:
    if type(left) is not type(right):
        return False
    if type(left) is dict:
        left_keys = tuple(dict.keys(left))
        right_keys = tuple(dict.keys(right))
        if left_keys != right_keys:
            return False
        index = 0
        while index < tuple.__len__(left_keys):
            key = tuple.__getitem__(left_keys, index)
            if not _same_json_shape(
                dict.__getitem__(left, key),
                dict.__getitem__(right, key),
            ):
                return False
            index += 1
        return True
    if type(left) is list:
        if list.__len__(left) != list.__len__(right):
            return False
        index = 0
        while index < list.__len__(left):
            if not _same_json_shape(
                list.__getitem__(left, index),
                list.__getitem__(right, index),
            ):
                return False
            index += 1
        return True
    return left == right


def _canonical_bytes(value: object) -> bytes | _Marker:
    try:
        if not _validate_json_value(value):
            return _FAILURE
        return _json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except Exception:
        return _FAILURE


def _restore_state(payload: object) -> AcademicWorkflowState | _Marker:
    try:
        if type(payload) is not dict:
            return _FAILURE
        encoded = _canonical_bytes(payload)
        if type(encoded) is not bytes:
            return _FAILURE
        restored = AcademicWorkflowState.model_validate_json(encoded)
        if type(restored) is not AcademicWorkflowState:
            return _FAILURE
        dumped = restored.model_dump(mode="json")
        if type(dumped) is not dict or not _same_json_shape(payload, dumped):
            return _FAILURE
        restored_bytes = _canonical_bytes(dumped)
        if type(restored_bytes) is not bytes or restored_bytes != encoded:
            return _FAILURE
        rerestored = AcademicWorkflowState.model_validate_json(restored_bytes)
        if type(rerestored) is not AcademicWorkflowState or rerestored != restored:
            return _FAILURE
        return rerestored
    except Exception:
        return _FAILURE


def _trusted_state_plan(
    state: AcademicWorkflowState,
) -> tuple[str, tuple[str, ...], int, tuple[tuple[str, str, str], ...]] | _Marker:
    try:
        if state.phase != "outline_approved" or state.status != "completed":
            return _FAILURE
        outline = state.outline
        decision = state.outline_decision
        evidence = state.research_evidence
        if (
            type(outline) is not _WorkflowOutline
            or type(decision) is not _WorkflowOutlineDecisionRecord
            or type(evidence) is not _WorkflowResearchEvidence
            or decision.decision != "approve"
        ):
            return _FAILURE
        outline_id = outline.outline_id
        outline_attempt = outline.attempt
        sections = outline.sections
        sources = evidence.sources
        if (
            type(outline_id) is not str
            or type(outline_attempt) is not int
            or outline_attempt != 1
            or type(sections) is not tuple
            or type(sources) is not tuple
        ):
            return _FAILURE
        section_ids: list[str] = []
        index = 0
        while index < tuple.__len__(sections):
            section = tuple.__getitem__(sections, index)
            if type(section) is not _WorkflowOutlineSection:
                return _FAILURE
            section_id = section.section_id
            if type(section_id) is not str:
                return _FAILURE
            section_ids.append(section_id)
            index += 1
        source_entries: list[tuple[str, str, str]] = []
        index = 0
        while index < tuple.__len__(sources):
            source = tuple.__getitem__(sources, index)
            if type(source) is not _WorkflowEvidenceSource:
                return _FAILURE
            source_id = source.source_id
            title = source.title
            url = source.url
            if (
                type(source_id) is not str
                or type(title) is not str
                or type(url) is not str
            ):
                return _FAILURE
            source_entries.append((source_id, title, url))
            index += 1
        return outline_id, tuple(section_ids), outline_attempt, tuple(source_entries)
    except Exception:
        return _FAILURE


def _restore_merged_mapping(mapping: dict[str, object]) -> bool:
    try:
        encoded = _canonical_bytes(mapping)
        if type(encoded) is not bytes:
            return False
        restored = WorkflowMergedDraft.model_validate_json(encoded)
        if type(restored) is not WorkflowMergedDraft:
            return False
        dumped = restored.model_dump(mode="json")
        restored_encoded = _canonical_bytes(dumped)
        if (
            type(restored_encoded) is not bytes
            or not _same_json_shape(mapping, dumped)
            or restored_encoded != encoded
        ):
            return False
        rerestored = WorkflowMergedDraft.model_validate_json(restored_encoded)
        return type(rerestored) is WorkflowMergedDraft and rerestored == restored
    except Exception:
        return False


def _extract_merged(
    value: object,
) -> tuple[str, tuple[str, ...], int, str] | _Marker:
    surface = _surface(value, WorkflowMergedDraft, _MERGED_FIELDS)
    if type(surface) is not tuple:
        return _FAILURE
    namespace, _ = surface
    try:
        outline_id = dict.__getitem__(namespace, "outline_id")
        section_ids = _copy_container_strings(
            dict.__getitem__(namespace, "section_ids"), tuple
        )
        attempt = dict.__getitem__(namespace, "attempt")
        content = dict.__getitem__(namespace, "content")
        if (
            type(outline_id) is not str
            or type(section_ids) is not tuple
            or type(attempt) is not int
            or attempt != 1
            or type(content) is not str
            or not str.strip(content)
            or len(content) > _MERGED_MAX_CHARS
        ):
            return _FAILURE
        mapping: dict[str, object] = {
            "outline_id": outline_id,
            "section_ids": list(section_ids),
            "attempt": attempt,
            "content": content,
        }
        if not _restore_merged_mapping(mapping):
            return _FAILURE
        return outline_id, section_ids, attempt, content
    except Exception:
        return _FAILURE


def _restore_gate_mapping(mapping: dict[str, object]) -> bool:
    try:
        encoded = _canonical_bytes(mapping)
        if type(encoded) is not bytes or len(encoded) > _GATE_MAX_BYTES:
            return False
        restored = WorkflowCitationEvidenceGateResult.model_validate_json(encoded)
        if type(restored) is not WorkflowCitationEvidenceGateResult:
            return False
        dumped = restored.model_dump(mode="json")
        restored_encoded = _canonical_bytes(dumped)
        if (
            type(restored_encoded) is not bytes
            or not _same_json_shape(mapping, dumped)
            or restored_encoded != encoded
        ):
            return False
        rerestored = WorkflowCitationEvidenceGateResult.model_validate_json(
            restored_encoded
        )
        return (
            type(rerestored) is WorkflowCitationEvidenceGateResult
            and rerestored == restored
        )
    except Exception:
        return False


def _extract_gate(
    value: object,
) -> tuple[str, tuple[str, ...], tuple[tuple[str, ...], ...], int] | _Marker:
    surface = _surface(value, WorkflowCitationEvidenceGateResult, _GATE_FIELDS)
    if type(surface) is not tuple:
        return _FAILURE
    namespace, _ = surface
    try:
        outline_id = dict.__getitem__(namespace, "outline_id")
        section_ids = _copy_container_strings(
            dict.__getitem__(namespace, "section_ids"), tuple
        )
        citations_value = dict.__getitem__(namespace, "cited_source_ids_by_section")
        attempt = dict.__getitem__(namespace, "attempt")
        if (
            type(outline_id) is not str
            or type(section_ids) is not tuple
            or type(citations_value) is not tuple
            or type(attempt) is not int
            or attempt != 1
            or not 1 <= tuple.__len__(section_ids) <= _SECTION_MAX_COUNT
            or tuple.__len__(citations_value) != tuple.__len__(section_ids)
        ):
            return _FAILURE
        citations: list[tuple[str, ...]] = []
        aggregate = 0
        index = 0
        while index < tuple.__len__(citations_value):
            inner = _copy_container_strings(
                tuple.__getitem__(citations_value, index), tuple
            )
            if (
                type(inner) is not tuple
                or not 1 <= tuple.__len__(inner) <= _CITATION_MAX_COUNT
            ):
                return _FAILURE
            seen: set[str] = set()
            source_index = 0
            while source_index < tuple.__len__(inner):
                source_id = tuple.__getitem__(inner, source_index)
                if not _valid_source_id(source_id) or source_id in seen:
                    return _FAILURE
                seen.add(source_id)
                source_index += 1
            aggregate += tuple.__len__(inner)
            citations.append(inner)
            index += 1
        if aggregate > _SECTION_MAX_COUNT * _CITATION_MAX_COUNT:
            return _FAILURE
        copied_citations = tuple(citations)
        mapping: dict[str, object] = {
            "outline_id": outline_id,
            "section_ids": list(section_ids),
            "cited_source_ids_by_section": [
                list(tuple.__getitem__(copied_citations, position))
                for position in range(tuple.__len__(copied_citations))
            ],
            "attempt": attempt,
        }
        if not _restore_gate_mapping(mapping):
            return _FAILURE
        return outline_id, section_ids, copied_citations, attempt
    except Exception:
        return _FAILURE


def _restore_disposition_mapping(mapping: dict[str, object]) -> bool:
    try:
        encoded = _canonical_bytes(mapping)
        if type(encoded) is not bytes or len(encoded) > _DISPOSITION_MAX_BYTES:
            return False
        restored = WorkflowCitationReviewDisposition.model_validate_json(encoded)
        if type(restored) is not WorkflowCitationReviewDisposition:
            return False
        dumped = restored.model_dump(mode="json")
        restored_encoded = _canonical_bytes(dumped)
        if (
            type(restored_encoded) is not bytes
            or not _same_json_shape(mapping, dumped)
            or restored_encoded != encoded
        ):
            return False
        rerestored = WorkflowCitationReviewDisposition.model_validate_json(
            restored_encoded
        )
        return (
            type(rerestored) is WorkflowCitationReviewDisposition
            and rerestored == restored
        )
    except Exception:
        return False


def _extract_disposition(
    value: object,
) -> tuple[str, tuple[str, ...], tuple[str, ...], str, int] | _Marker:
    surface = _surface(
        value,
        WorkflowCitationReviewDisposition,
        _DISPOSITION_FIELDS,
    )
    if type(surface) is not tuple:
        return _FAILURE
    namespace, _ = surface
    try:
        outline_id = dict.__getitem__(namespace, "outline_id")
        section_ids = _copy_container_strings(
            dict.__getitem__(namespace, "section_ids"), tuple
        )
        section_dispositions = _copy_container_strings(
            dict.__getitem__(namespace, "section_dispositions"), tuple
        )
        disposition = dict.__getitem__(namespace, "disposition")
        attempt = dict.__getitem__(namespace, "attempt")
        if (
            type(outline_id) is not str
            or type(section_ids) is not tuple
            or type(section_dispositions) is not tuple
            or type(disposition) is not str
            or type(attempt) is not int
            or attempt != 1
        ):
            return _FAILURE
        mapping: dict[str, object] = {
            "outline_id": outline_id,
            "section_ids": list(section_ids),
            "section_dispositions": list(section_dispositions),
            "disposition": disposition,
            "attempt": attempt,
        }
        if not _restore_disposition_mapping(mapping):
            return _FAILURE
        return outline_id, section_ids, section_dispositions, disposition, attempt
    except Exception:
        return _FAILURE


def _flatten_citations(
    citations: tuple[tuple[str, ...], ...],
    source_ids: set[str],
) -> tuple[str, ...] | _Marker:
    seen: set[str] = set()
    flattened: list[str] = []
    section_index = 0
    while section_index < tuple.__len__(citations):
        inner = tuple.__getitem__(citations, section_index)
        source_index = 0
        while source_index < tuple.__len__(inner):
            source_id = tuple.__getitem__(inner, source_index)
            if source_id not in source_ids:
                return _FAILURE
            if source_id not in seen:
                seen.add(source_id)
                flattened.append(source_id)
            source_index += 1
        section_index += 1
    if not 1 <= len(flattened) <= _SOURCE_MAX_COUNT:
        return _FAILURE
    return tuple(flattened)


def _scan_markers(
    content: str,
    source_ids: set[str],
) -> tuple[str, ...] | _Marker:
    seen: set[str] = set()
    ordered: list[str] = []
    cursor = 0
    while True:
        start = str.find(content, _CITATION_PREFIX, cursor)
        if start < 0:
            break
        value_start = start + len(_CITATION_PREFIX)
        end = str.find(content, _CITATION_SUFFIX, value_start)
        if end < 0:
            return _FAILURE
        source_id = content[value_start:end]
        token_end = end + len(_CITATION_SUFFIX)
        if (
            not source_id
            or "[" in source_id
            or "]" in source_id
            or not _valid_source_id(source_id)
            or source_id not in source_ids
            or (
                token_end < len(content)
                and content[token_end] in "[]"
            )
        ):
            return _FAILURE
        if source_id not in seen:
            seen.add(source_id)
            ordered.append(source_id)
        cursor = token_end
    return tuple(ordered)


def _prepare_render_plan(
    state: object,
    merged_draft: object,
    gate_result: object,
    disposition: object,
) -> tuple[
    str,
    tuple[str, ...],
    tuple[str, ...],
    str,
    tuple[tuple[str, str, str], ...],
] | _Marker:
    try:
        state_payload = _copy_state_payload(state)
        del state
        if type(state_payload) is not dict:
            del merged_draft
            del gate_result
            del disposition
            return _FAILURE
        trusted_state = _restore_state(state_payload)
        del state_payload
        if type(trusted_state) is not AcademicWorkflowState:
            del merged_draft
            del gate_result
            del disposition
            return _FAILURE
        state_plan = _trusted_state_plan(trusted_state)
        del trusted_state
        if type(state_plan) is not tuple:
            del merged_draft
            del gate_result
            del disposition
            return _FAILURE
        merged = _extract_merged(merged_draft)
        del merged_draft
        if type(merged) is not tuple:
            del state_plan
            del gate_result
            del disposition
            return _FAILURE
        gate = _extract_gate(gate_result)
        del gate_result
        if type(gate) is not tuple:
            del state_plan
            del merged
            del disposition
            return _FAILURE
        routed = _extract_disposition(disposition)
        del disposition
        if type(routed) is not tuple:
            del state_plan
            del merged
            del gate
            return _FAILURE

        state_outline = tuple.__getitem__(state_plan, 0)
        state_sections = tuple.__getitem__(state_plan, 1)
        state_attempt = tuple.__getitem__(state_plan, 2)
        sources = tuple.__getitem__(state_plan, 3)
        merged_outline = tuple.__getitem__(merged, 0)
        merged_sections = tuple.__getitem__(merged, 1)
        merged_attempt = tuple.__getitem__(merged, 2)
        content = tuple.__getitem__(merged, 3)
        gate_outline = tuple.__getitem__(gate, 0)
        gate_sections = tuple.__getitem__(gate, 1)
        citations = tuple.__getitem__(gate, 2)
        gate_attempt = tuple.__getitem__(gate, 3)
        routed_outline = tuple.__getitem__(routed, 0)
        routed_sections = tuple.__getitem__(routed, 1)
        section_dispositions = tuple.__getitem__(routed, 2)
        overall = tuple.__getitem__(routed, 3)
        routed_attempt = tuple.__getitem__(routed, 4)
        if (
            type(state_outline) is not str
            or type(state_sections) is not tuple
            or type(state_attempt) is not int
            or type(sources) is not tuple
            or type(merged_outline) is not str
            or type(merged_sections) is not tuple
            or type(merged_attempt) is not int
            or type(content) is not str
            or type(gate_outline) is not str
            or type(gate_sections) is not tuple
            or type(citations) is not tuple
            or type(gate_attempt) is not int
            or type(routed_outline) is not str
            or type(routed_sections) is not tuple
            or type(section_dispositions) is not tuple
            or type(overall) is not str
            or type(routed_attempt) is not int
            or state_outline != merged_outline
            or state_outline != gate_outline
            or state_outline != routed_outline
            or state_sections != merged_sections
            or state_sections != gate_sections
            or state_sections != routed_sections
            or state_attempt != 1
            or merged_attempt != 1
            or gate_attempt != 1
            or routed_attempt != 1
            or overall != "ready"
            or tuple.__len__(section_dispositions) != tuple.__len__(state_sections)
        ):
            return _FAILURE
        index = 0
        while index < tuple.__len__(section_dispositions):
            if tuple.__getitem__(section_dispositions, index) != "ready":
                return _FAILURE
            index += 1

        source_ids: set[str] = set()
        metadata_by_id: dict[str, tuple[str, str]] = {}
        index = 0
        while index < tuple.__len__(sources):
            source = tuple.__getitem__(sources, index)
            if type(source) is not tuple or tuple.__len__(source) != 3:
                return _FAILURE
            source_id = tuple.__getitem__(source, 0)
            title = tuple.__getitem__(source, 1)
            url = tuple.__getitem__(source, 2)
            if (
                type(source_id) is not str
                or type(title) is not str
                or type(url) is not str
                or source_id in source_ids
            ):
                return _FAILURE
            source_ids.add(source_id)
            metadata_by_id[source_id] = (title, url)
            index += 1
        reference_ids = _flatten_citations(citations, source_ids)
        if type(reference_ids) is not tuple:
            return _FAILURE
        marker_ids = _scan_markers(content, source_ids)
        if type(marker_ids) is not tuple or marker_ids != reference_ids:
            return _FAILURE
        metadata: list[tuple[str, str, str]] = []
        index = 0
        while index < tuple.__len__(reference_ids):
            source_id = tuple.__getitem__(reference_ids, index)
            title, url = dict.__getitem__(metadata_by_id, source_id)
            metadata.append((source_id, title, url))
            index += 1
        result_metadata = tuple(metadata)
        return state_outline, state_sections, reference_ids, content, result_metadata
    except Exception:
        return _FAILURE


def _build_result_bytes(
    plan: tuple[
        str,
        tuple[str, ...],
        tuple[str, ...],
        str,
        tuple[tuple[str, str, str], ...],
    ],
) -> bytes | _Marker:
    try:
        outline_id = tuple.__getitem__(plan, 0)
        section_ids = tuple.__getitem__(plan, 1)
        reference_ids = tuple.__getitem__(plan, 2)
        merged_content = tuple.__getitem__(plan, 3)
        metadata = tuple.__getitem__(plan, 4)
        lines: list[str] = []
        index = 0
        while index < tuple.__len__(metadata):
            source_id, title, url = tuple.__getitem__(metadata, index)
            source_json = _json.dumps(source_id, ensure_ascii=False)
            title_json = _json.dumps(title, ensure_ascii=False)
            url_json = _json.dumps(url, ensure_ascii=False)
            line = (
                f"    [{index + 1}] source_id={source_json} "
                f"title={title_json} url={url_json}"
            )
            lines.append(line)
            index += 1
        bibliography = _REFERENCES_PREFIX + "\n".join(lines)
        if len(bibliography) > 5541708:
            return _FAILURE
        content = merged_content + bibliography
        if len(content) > _CONTENT_MAX_CHARS:
            return _FAILURE
        result = WorkflowReferencedDraft(
            outline_id=outline_id,
            section_ids=section_ids,
            reference_source_ids=reference_ids,
            attempt=1,
            content=content,
        )
        dumped = result.model_dump(mode="json")
        encoded = _canonical_bytes(dumped)
        if type(encoded) is not bytes or len(encoded) > _RESULT_MAX_BYTES:
            return _FAILURE
        restored = WorkflowReferencedDraft.model_validate_json(encoded)
        if type(restored) is not WorkflowReferencedDraft:
            return _FAILURE
        restored_dumped = restored.model_dump(mode="json")
        restored_encoded = _canonical_bytes(restored_dumped)
        if (
            type(restored_encoded) is not bytes
            or not _same_json_shape(dumped, restored_dumped)
            or restored != result
            or restored_encoded != encoded
        ):
            return _FAILURE
        return encoded
    except Exception:
        return _FAILURE


def _restore_result(value: object) -> WorkflowReferencedDraft | _Marker:
    try:
        if type(value) is not bytes or len(value) > _RESULT_MAX_BYTES:
            return _FAILURE
        restored = WorkflowReferencedDraft.model_validate_json(value)
        if type(restored) is not WorkflowReferencedDraft:
            return _FAILURE
        dumped = restored.model_dump(mode="json")
        encoded = _canonical_bytes(dumped)
        if type(encoded) is not bytes or encoded != value:
            return _FAILURE
        return restored
    except Exception:
        return _FAILURE


def _raise_failure() -> None:
    raise _ReferencesRendererError(_ERROR_TEXT)


def render_references(
    state: AcademicWorkflowState,
    merged_draft: WorkflowMergedDraft,
    gate_result: WorkflowCitationEvidenceGateResult,
    disposition: WorkflowCitationReviewDisposition,
) -> WorkflowReferencedDraft:
    plan = _prepare_render_plan(state, merged_draft, gate_result, disposition)
    del state
    del merged_draft
    del gate_result
    del disposition
    if type(plan) is not tuple:
        del plan
        _raise_failure()
    encoded = _build_result_bytes(plan)
    del plan
    if type(encoded) is not bytes:
        del encoded
        _raise_failure()
    result = _restore_result(encoded)
    del encoded
    if type(result) is not WorkflowReferencedDraft:
        del result
        _raise_failure()
    return result
