"""Strict deterministic citation-to-evidence gate for academic drafts."""

from __future__ import annotations

import json as _json
from typing import Literal as _Literal

from pydantic import BaseModel as _BaseModel
from pydantic import ConfigDict as _ConfigDict
from pydantic import ValidationInfo as _ValidationInfo
from pydantic import field_validator as _field_validator
from pydantic import model_validator as _model_validator

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
    WorkflowSectionDraft,
    WorkflowTopicPlan as _WorkflowTopicPlan,
)


__all__ = (
    "WorkflowCitationEvidenceGateResult",
    "gate_citation_evidence",
)


_ERROR_TEXT = "citation evidence gate failed"
_CONTENT_MAX_CHARS = 24576
_SECTION_MAX_COUNT = 12
_PROVENANCE_MAX_COUNT = 64
_PROVENANCE_BLOCK_MAX_CHARS = 16384
_RESULT_MAX_BYTES = 19519
_CITATION_PREFIX = "[[cite:"
_CITATION_SUFFIX = "]]"


class _CitationEvidenceGateError(RuntimeError):
    pass


class _Marker:
    pass


_FAILURE = _Marker()
_EMPTY_PROVENANCE = _Marker()


class WorkflowCitationEvidenceGateResult(_BaseModel):
    model_config = _ConfigDict(frozen=True, extra="forbid", strict=True)

    outline_id: _Literal["outline:000001"]
    section_ids: tuple[str, ...]
    cited_source_ids_by_section: tuple[tuple[str, ...], ...]
    attempt: _Literal[1]

    @_model_validator(mode="before")
    @classmethod
    def _require_exact_input(cls, value: object, info: _ValidationInfo) -> object:
        if type(value) is cls:
            return value
        if type(value) is not dict:
            raise TypeError("citation gate result must be an exact mapping")
        keys = tuple(dict.keys(value))
        if any(type(key) is not str for key in keys) or set(keys) != {
            "outline_id",
            "section_ids",
            "cited_source_ids_by_section",
            "attempt",
        }:
            raise TypeError("citation gate result fields must be exact")
        outline_id = dict.__getitem__(value, "outline_id")
        section_ids = dict.__getitem__(value, "section_ids")
        citations = dict.__getitem__(value, "cited_source_ids_by_section")
        attempt = dict.__getitem__(value, "attempt")
        if type(outline_id) is not str or type(attempt) is not int:
            raise TypeError("citation gate result scalar types must be exact")
        expected_container = list if info.mode == "json" else tuple
        if type(section_ids) is not expected_container:
            raise TypeError("citation gate result section container must be exact")
        if type(citations) is not expected_container:
            raise TypeError("citation gate result citation container must be exact")
        if any(type(section_id) is not str for section_id in section_ids):
            raise TypeError("citation gate result section IDs must be exact strings")
        for inner in citations:
            if type(inner) is not expected_container:
                raise TypeError("citation gate result nested container must be exact")
            if any(type(source_id) is not str for source_id in inner):
                raise TypeError("citation gate result source IDs must be exact strings")
        if info.mode == "json":
            copied = dict(value)
            copied["section_ids"] = tuple(section_ids)
            copied["cited_source_ids_by_section"] = tuple(
                tuple(inner) for inner in citations
            )
            return copied
        return value

    @_field_validator("section_ids")
    @classmethod
    def _validate_section_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if not 1 <= len(values) <= _SECTION_MAX_COUNT:
            raise ValueError("citation gate result section count is invalid")
        for index, value in enumerate(values, start=1):
            if value != f"section:{index:06d}":
                raise ValueError("citation gate result section ID is invalid")
        return values

    @_field_validator("cited_source_ids_by_section")
    @classmethod
    def _validate_citations(
        cls, values: tuple[tuple[str, ...], ...]
    ) -> tuple[tuple[str, ...], ...]:
        for inner in values:
            if not 1 <= len(inner) <= _PROVENANCE_MAX_COUNT:
                raise ValueError("citation gate result citation count is invalid")
            if len(set(inner)) != len(inner):
                raise ValueError("citation gate result citations must be unique")
            for value in inner:
                prefix = "evidence-source:"
                suffix = value[len(prefix) :]
                if (
                    not value.startswith(prefix)
                    or len(suffix) != 6
                    or not suffix.isascii()
                    or not suffix.isdigit()
                ):
                    raise ValueError("citation gate result source ID is invalid")
                order = int(suffix)
                if not 1 <= order <= 200 or value != f"{prefix}{order:06d}":
                    raise ValueError("citation gate result source ID is invalid")
        return values

    @_model_validator(mode="after")
    def _validate_positional_binding(self) -> WorkflowCitationEvidenceGateResult:
        if len(self.section_ids) != len(self.cited_source_ids_by_section):
            raise ValueError("citation gate result positional binding is invalid")
        if type(self.attempt) is not int or self.attempt != 1:
            raise ValueError("citation gate result attempt is invalid")
        return self


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
    "report_mode",
    "report_locale",
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
_OUTLINE_FIELDS = (
    "outline_id", "evidence_id", "attempt", "title", "sections",
    "report_mode", "report_locale",
)
_SECTION_FIELDS = ("section_id", "order", "title", "brief", "section_role")
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
_DRAFT_FIELDS = ("outline_id", "section_id", "attempt", "content")


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
    if type(value) is not tuple:
        return _FAILURE
    copied: list[str] = []
    for index in range(tuple.__len__(value)):
        item = tuple.__getitem__(value, index)
        if type(item) is not str:
            return _FAILURE
        copied.append(item)
    return copied


def _copy_request(value: object) -> dict[str, object] | _Marker:
    full = frozenset(_REQUEST_FIELDS)
    old = frozenset(_REQUEST_FIELDS[:-2])
    surface = _surface(
        value,
        _AcademicWorkflowRequest,
        _REQUEST_FIELDS,
        (full, old | {"report_mode"}, old | {"report_locale"}, old),
    )
    if type(surface) is not tuple:
        return _FAILURE
    namespace, _ = surface
    scalar_names = _REQUEST_FIELDS[:9]
    copied: dict[str, object] = {}
    for name in scalar_names:
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
    mode = dict.__getitem__(namespace, "report_mode")
    locale = dict.__getitem__(namespace, "report_locale")
    if type(mode) is not str or (locale is not None and type(locale) is not str):
        return _FAILURE
    copied["report_mode"] = mode
    copied["report_locale"] = locale
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
    for index in range(tuple.__len__(sources)):
        source = _copy_source(tuple.__getitem__(sources, index))
        if type(source) is not dict:
            return _FAILURE
        copied_sources.append(source)
    copied_provenance: list[dict[str, object]] = []
    for index in range(tuple.__len__(provenance)):
        entry = _copy_provenance(tuple.__getitem__(provenance, index))
        if type(entry) is not dict:
            return _FAILURE
        copied_provenance.append(entry)
    copied["attempt"] = attempt
    copied["context_blocks"] = contexts
    copied["sources"] = copied_sources
    copied["provenance"] = copied_provenance
    return copied


def _copy_outline_section(value: object) -> dict[str, object] | _Marker:
    full = frozenset(_SECTION_FIELDS)
    surface = _surface(
        value,
        _WorkflowOutlineSection,
        _SECTION_FIELDS,
        (full, frozenset(_SECTION_FIELDS[:-1])),
    )
    if type(surface) is not tuple:
        return _FAILURE
    namespace, _ = surface
    section_id = dict.__getitem__(namespace, "section_id")
    order = dict.__getitem__(namespace, "order")
    title = dict.__getitem__(namespace, "title")
    brief = dict.__getitem__(namespace, "brief")
    role = dict.__getitem__(namespace, "section_role")
    if (
        type(section_id) is not str
        or type(order) is not int
        or type(title) is not str
        or type(brief) is not str
        or type(role) is not str
    ):
        return _FAILURE
    return {
        "section_id": section_id,
        "order": order,
        "title": title,
        "brief": brief,
        "section_role": role,
    }


def _copy_outline(value: object) -> dict[str, object] | _Marker:
    full = frozenset(_OUTLINE_FIELDS)
    old = frozenset(_OUTLINE_FIELDS[:-2])
    surface = _surface(
        value,
        _WorkflowOutline,
        _OUTLINE_FIELDS,
        (full, old | {"report_mode"}, old | {"report_locale"}, old),
    )
    if type(surface) is not tuple:
        return _FAILURE
    namespace, _ = surface
    outline_id = dict.__getitem__(namespace, "outline_id")
    evidence_id = dict.__getitem__(namespace, "evidence_id")
    attempt = dict.__getitem__(namespace, "attempt")
    title = dict.__getitem__(namespace, "title")
    sections = dict.__getitem__(namespace, "sections")
    mode = dict.__getitem__(namespace, "report_mode")
    locale = dict.__getitem__(namespace, "report_locale")
    if (
        type(outline_id) is not str
        or type(evidence_id) is not str
        or type(attempt) is not int
        or type(title) is not str
        or type(sections) is not tuple
        or type(mode) is not str
        or (locale is not None and type(locale) is not str)
    ):
        return _FAILURE
    copied_sections: list[dict[str, object]] = []
    for index in range(tuple.__len__(sections)):
        section = _copy_outline_section(tuple.__getitem__(sections, index))
        if type(section) is not dict:
            return _FAILURE
        copied_sections.append(section)
    return {
        "outline_id": outline_id,
        "evidence_id": evidence_id,
        "attempt": attempt,
        "title": title,
        "sections": copied_sections,
        "report_mode": mode,
        "report_locale": locale,
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
    attempt = dict.__getitem__(namespace, "attempt")
    node_id = dict.__getitem__(namespace, "node_id")
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
        for name in ("schema_version", "workflow_id", "thread_id", "run_id", "phase", "status"):
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
        for index in range(tuple.__len__(events)):
            event = _copy_event(tuple.__getitem__(events, index))
            if type(event) is not dict:
                return _FAILURE
            copied_events.append(event)
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
        for index in range(list.__len__(value)):
            if not _validate_json_value(list.__getitem__(value, index)):
                return False
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
        for key in left_keys:
            if not _same_json_shape(
                dict.__getitem__(left, key),
                dict.__getitem__(right, key),
            ):
                return False
        return True
    if type(left) is list:
        if list.__len__(left) != list.__len__(right):
            return False
        for index in range(list.__len__(left)):
            if not _same_json_shape(
                list.__getitem__(left, index),
                list.__getitem__(right, index),
            ):
                return False
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


def _approved_metadata(
    state: AcademicWorkflowState,
) -> tuple[str, tuple[str, ...]] | _Marker:
    try:
        if state.phase != "outline_approved" or state.status != "completed":
            return _FAILURE
        outline = state.outline
        if type(outline) is not _WorkflowOutline:
            return _FAILURE
        outline_id = outline.outline_id
        sections = outline.sections
        if type(outline_id) is not str or type(sections) is not tuple:
            return _FAILURE
        section_ids: list[str] = []
        for index in range(tuple.__len__(sections)):
            section = tuple.__getitem__(sections, index)
            if type(section) is not _WorkflowOutlineSection:
                return _FAILURE
            section_id = section.section_id
            if type(section_id) is not str:
                return _FAILURE
            section_ids.append(section_id)
        return outline_id, tuple(section_ids)
    except Exception:
        return _FAILURE


def _draft_metadata_plan(
    drafts: object,
    approved_outline_id: str,
    approved_section_ids: tuple[str, ...],
) -> tuple[str, tuple[str, ...]] | _Marker:
    try:
        if type(drafts) is not tuple:
            return _FAILURE
        count = tuple.__len__(drafts)
        if (
            not 1 <= count <= _SECTION_MAX_COUNT
            or count != tuple.__len__(approved_section_ids)
        ):
            return _FAILURE
        projected: list[str] = []
        for index in range(count):
            member = tuple.__getitem__(drafts, index)
            surface = _surface(member, WorkflowSectionDraft, _DRAFT_FIELDS)
            if type(surface) is not tuple:
                return _FAILURE
            namespace, _ = surface
            if not dict.__contains__(namespace, "content"):
                return _FAILURE
            outline_id = dict.__getitem__(namespace, "outline_id")
            section_id = dict.__getitem__(namespace, "section_id")
            attempt = dict.__getitem__(namespace, "attempt")
            if (
                type(outline_id) is not str
                or type(section_id) is not str
                or type(attempt) is not int
                or attempt != 1
            ):
                return _FAILURE
            if outline_id != approved_outline_id:
                return _FAILURE
            if section_id != tuple.__getitem__(approved_section_ids, index):
                return _FAILURE
            projected.append(section_id)
            del member
        if len(set(projected)) != len(projected):
            return _FAILURE
        return approved_outline_id, tuple(projected)
    except Exception:
        return _FAILURE


def _project_sources_and_provenance(
    state: AcademicWorkflowState,
) -> tuple[tuple[str, ...], tuple[str, ...]] | _Marker:
    try:
        state_surface = _surface(state, AcademicWorkflowState, _STATE_FIELDS)
        if type(state_surface) is not tuple:
            return _FAILURE
        state_namespace, _ = state_surface
        evidence = dict.__getitem__(state_namespace, "research_evidence")
        evidence_surface = _surface(
            evidence,
            _WorkflowResearchEvidence,
            _EVIDENCE_FIELDS,
        )
        if type(evidence_surface) is not tuple:
            return _FAILURE
        evidence_namespace, _ = evidence_surface
        sources = dict.__getitem__(evidence_namespace, "sources")
        provenance = dict.__getitem__(evidence_namespace, "provenance")
        if type(sources) is not tuple or type(provenance) is not tuple:
            return _FAILURE
        if tuple.__len__(provenance) == 0:
            return _EMPTY_PROVENANCE
        if tuple.__len__(provenance) > _PROVENANCE_MAX_COUNT:
            return _FAILURE
        source_ids: list[str] = []
        for index in range(tuple.__len__(sources)):
            source = tuple.__getitem__(sources, index)
            surface = _surface(source, _WorkflowEvidenceSource, _SOURCE_FIELDS)
            if type(surface) is not tuple:
                return _FAILURE
            namespace, _ = surface
            source_id = dict.__getitem__(namespace, "source_id")
            if type(source_id) is not str:
                return _FAILURE
            source_ids.append(source_id)
        if len(set(source_ids)) != len(source_ids):
            return _FAILURE
        positions = {source_id: index for index, source_id in enumerate(source_ids)}
        provenance_ids: list[str] = []
        provenance_positions: list[int] = []
        for index in range(tuple.__len__(provenance)):
            entry = tuple.__getitem__(provenance, index)
            surface = _surface(entry, _WorkflowEvidenceProvenance, _PROVENANCE_FIELDS)
            if type(surface) is not tuple:
                return _FAILURE
            namespace, _ = surface
            source_id = dict.__getitem__(namespace, "source_id")
            blocks = dict.__getitem__(namespace, "evidence_blocks")
            if type(source_id) is not str or type(blocks) is not tuple:
                return _FAILURE
            if not 1 <= tuple.__len__(blocks) <= _PROVENANCE_MAX_COUNT:
                return _FAILURE
            for block_index in range(tuple.__len__(blocks)):
                block = tuple.__getitem__(blocks, block_index)
                if (
                    type(block) is not str
                    or not str.strip(block)
                    or len(block) > _PROVENANCE_BLOCK_MAX_CHARS
                ):
                    return _FAILURE
            if source_id not in positions or source_id in provenance_ids:
                return _FAILURE
            provenance_ids.append(source_id)
            provenance_positions.append(positions[source_id])
        if provenance_positions != sorted(provenance_positions):
            return _FAILURE
        return tuple(source_ids), tuple(provenance_ids)
    except Exception:
        return _FAILURE


def _copy_draft_contents(
    drafts: object,
    metadata: tuple[str, tuple[str, ...]],
) -> tuple[str, ...] | _Marker:
    try:
        if type(drafts) is not tuple or type(metadata) is not tuple:
            return _FAILURE
        approved_outline_id, approved_section_ids = metadata
        if type(approved_outline_id) is not str or type(approved_section_ids) is not tuple:
            return _FAILURE
        if tuple.__len__(drafts) != tuple.__len__(approved_section_ids):
            return _FAILURE
        copied_contents: list[str] = []
        for index in range(tuple.__len__(drafts)):
            member = tuple.__getitem__(drafts, index)
            surface = _surface(member, WorkflowSectionDraft, _DRAFT_FIELDS)
            if type(surface) is not tuple:
                return _FAILURE
            namespace, _ = surface
            outline_id = dict.__getitem__(namespace, "outline_id")
            section_id = dict.__getitem__(namespace, "section_id")
            attempt = dict.__getitem__(namespace, "attempt")
            if (
                type(outline_id) is not str
                or type(section_id) is not str
                or type(attempt) is not int
                or attempt != 1
                or outline_id != approved_outline_id
                or section_id != tuple.__getitem__(approved_section_ids, index)
            ):
                return _FAILURE
            content = dict.__getitem__(namespace, "content")
            if type(content) is not str:
                return _FAILURE
            encoded = _json.dumps(
                content,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            )
            copied = _json.loads(encoded)
            if (
                type(copied) is not str
                or not str.strip(copied)
                or len(copied) > _CONTENT_MAX_CHARS
                or str.strip(copied) != copied
            ):
                return _FAILURE
            copied_contents.append(copied)
            del content
            del member
        return tuple(copied_contents)
    except Exception:
        return _FAILURE


def _scan_citations(
    contents: tuple[str, ...],
    source_ids: tuple[str, ...],
    provenance_ids: tuple[str, ...],
) -> tuple[tuple[str, ...], ...] | _Marker:
    try:
        if (
            type(contents) is not tuple
            or type(source_ids) is not tuple
            or type(provenance_ids) is not tuple
        ):
            return _FAILURE
        source_set = set(source_ids)
        provenance_set = set(provenance_ids)
        projected: list[tuple[str, ...]] = []
        for content in contents:
            if type(content) is not str:
                return _FAILURE
            position = 0
            ordered: list[str] = []
            seen: set[str] = set()
            while True:
                start = content.find(_CITATION_PREFIX, position)
                if start < 0:
                    remainder = content[position:]
                    if "[" in remainder or "]" in remainder:
                        return _FAILURE
                    break
                before = content[position:start]
                if "[" in before or "]" in before:
                    return _FAILURE
                value_start = start + len(_CITATION_PREFIX)
                end = content.find(_CITATION_SUFFIX, value_start)
                if end < 0:
                    return _FAILURE
                source_id = content[value_start:end]
                if source_id == "" or "[" in source_id or "]" in source_id:
                    return _FAILURE
                if source_id not in source_set or source_id not in provenance_set:
                    return _FAILURE
                if source_id not in seen:
                    seen.add(source_id)
                    ordered.append(source_id)
                position = end + len(_CITATION_SUFFIX)
            if "://" in content or not ordered:
                return _FAILURE
            projected.append(tuple(ordered))
        return tuple(projected)
    except Exception:
        return _FAILURE


def _build_result_bytes(
    metadata: tuple[str, tuple[str, ...]],
    citations: tuple[tuple[str, ...], ...],
) -> bytes | _Marker:
    try:
        outline_id, section_ids = metadata
        result = WorkflowCitationEvidenceGateResult(
            outline_id=outline_id,
            section_ids=section_ids,
            cited_source_ids_by_section=citations,
            attempt=1,
        )
        dumped = result.model_dump(mode="json")
        encoded = _canonical_bytes(dumped)
        if type(encoded) is not bytes or len(encoded) > _RESULT_MAX_BYTES:
            return _FAILURE
        restored = WorkflowCitationEvidenceGateResult.model_validate_json(encoded)
        if type(restored) is not WorkflowCitationEvidenceGateResult:
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
        return restored_encoded
    except Exception:
        return _FAILURE


def _execute_gate(state: object, drafts: object) -> bytes | _Marker:
    try:
        payload = _copy_state_payload(state)
        del state
        if type(payload) is not dict:
            del drafts
            return _FAILURE
        restored = _restore_state(payload)
        del payload
        if type(restored) is not AcademicWorkflowState:
            del drafts
            return _FAILURE
        metadata = _approved_metadata(restored)
        if type(metadata) is not tuple:
            del restored
            del drafts
            return _FAILURE
        stage_a = _draft_metadata_plan(
            drafts,
            tuple.__getitem__(metadata, 0),
            tuple.__getitem__(metadata, 1),
        )
        del metadata
        if type(stage_a) is not tuple:
            del restored
            del drafts
            return _FAILURE
        source_plan = _project_sources_and_provenance(restored)
        if source_plan is _EMPTY_PROVENANCE:
            del restored
            del drafts
            del stage_a
            del source_plan
            return _FAILURE
        if type(source_plan) is not tuple:
            del restored
            del drafts
            del stage_a
            return _FAILURE
        del restored
        contents = _copy_draft_contents(drafts, stage_a)
        del drafts
        if type(contents) is not tuple:
            del stage_a
            del source_plan
            return _FAILURE
        citations = _scan_citations(
            contents,
            tuple.__getitem__(source_plan, 0),
            tuple.__getitem__(source_plan, 1),
        )
        del contents
        del source_plan
        if type(citations) is not tuple:
            del stage_a
            return _FAILURE
        encoded = _build_result_bytes(stage_a, citations)
        del stage_a
        del citations
        return encoded
    except Exception:
        return _FAILURE


def _restore_result(value: object) -> WorkflowCitationEvidenceGateResult | _Marker:
    try:
        if type(value) is not bytes:
            return _FAILURE
        result = WorkflowCitationEvidenceGateResult.model_validate_json(value)
        if type(result) is not WorkflowCitationEvidenceGateResult:
            return _FAILURE
        dumped = result.model_dump(mode="json")
        encoded = _canonical_bytes(dumped)
        if type(encoded) is not bytes or encoded != value:
            return _FAILURE
        return result
    except Exception:
        return _FAILURE


def _raise_failure() -> None:
    raise _CitationEvidenceGateError(_ERROR_TEXT)


def gate_citation_evidence(
    state: AcademicWorkflowState,
    drafts: tuple[WorkflowSectionDraft, ...],
) -> WorkflowCitationEvidenceGateResult:
    encoded = _execute_gate(state, drafts)
    del state
    del drafts
    result = _restore_result(encoded)
    del encoded
    if type(result) is WorkflowCitationEvidenceGateResult:
        return result
    del result
    _raise_failure()
