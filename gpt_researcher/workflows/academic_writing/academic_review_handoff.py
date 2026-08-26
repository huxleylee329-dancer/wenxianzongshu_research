"""Deterministic off-graph packaging of non-ready academic review artifacts."""

from __future__ import annotations

import json as _json

from pydantic import BaseModel as _BaseModel
from pydantic import ConfigDict as _ConfigDict
from pydantic import ValidationInfo as _ValidationInfo
from pydantic import model_validator as _model_validator

from .academic_draft_composer import (
    WorkflowAcademicDraftComposition as _WorkflowAcademicDraftComposition,
)
from .citation_evidence_gate import (
    WorkflowCitationEvidenceGateResult as _WorkflowCitationEvidenceGateResult,
    gate_citation_evidence as _gate_citation_evidence,
)
from .citation_review_disposition import (
    WorkflowCitationReviewDisposition as _WorkflowCitationReviewDisposition,
    gate_citation_review_disposition as _gate_citation_review_disposition,
)
from .citation_reviewer import (
    WorkflowSectionCitationReview as _WorkflowSectionCitationReview,
)
from .state import (
    AcademicWorkflowState as _AcademicWorkflowState,
    WorkflowEvidenceProvenance as _WorkflowEvidenceProvenance,
    WorkflowEvidenceSource as _WorkflowEvidenceSource,
    WorkflowOutline as _WorkflowOutline,
    WorkflowOutlineDecisionRecord as _WorkflowOutlineDecisionRecord,
    WorkflowOutlineSection as _WorkflowOutlineSection,
    WorkflowResearchEvidence as _WorkflowResearchEvidence,
    WorkflowSectionDraft as _WorkflowSectionDraft,
)


__all__ = (
    "WorkflowAcademicReviewHandoff",
    "build_academic_review_handoff",
)


_ERROR_TEXT = "academic review handoff failed"
_NON_READY_COMPOSITION_MAX_BYTES = 1526996
_HANDOFF_FIELDS = ("composition", "cited_sources", "cited_provenance")
_COMPOSITION_FIELDS = (
    "drafts",
    "gate_result",
    "reviews",
    "disposition",
    "merged_draft",
    "referenced_draft",
)
_DRAFT_FIELDS = ("outline_id", "section_id", "attempt", "content")
_GATE_FIELDS = (
    "outline_id",
    "section_ids",
    "cited_source_ids_by_section",
    "attempt",
)
_REVIEW_FIELDS = (
    "outline_id",
    "section_id",
    "cited_source_ids",
    "verdict",
    "issues",
    "rationale",
    "attempt",
)
_DISPOSITION_FIELDS = (
    "outline_id",
    "section_ids",
    "section_dispositions",
    "disposition",
    "attempt",
)
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
_ISSUES = (
    "insufficient_evidence",
    "possible_contradiction",
    "citation_placement_unclear",
)


class _AcademicReviewHandoffError(RuntimeError):
    pass


class _Marker:
    __slots__ = ()


_FAILURE = _Marker()


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
        index = 0
        while index < tuple.__len__(keys):
            if type(tuple.__getitem__(keys, index)) is not str:
                return _FAILURE
            index += 1
        index = 0
        while index < tuple.__len__(members):
            if type(tuple.__getitem__(members, index)) is not str:
                return _FAILURE
            index += 1
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


def _string_tuple(
    value: object,
    *,
    minimum: int = 0,
    maximum: int | None = None,
) -> tuple[str, ...] | _Marker:
    if type(value) is not tuple:
        return _FAILURE
    size = tuple.__len__(value)
    if size < minimum or (maximum is not None and size > maximum):
        return _FAILURE
    index = 0
    while index < size:
        if type(tuple.__getitem__(value, index)) is not str:
            return _FAILURE
        index += 1
    return value


def _source_id_is_valid(value: str) -> bool:
    prefix = "evidence-source:"
    suffix = value[len(prefix) :]
    return (
        value.startswith(prefix)
        and len(suffix) == 6
        and suffix.isascii()
        and suffix.isdigit()
        and 1 <= int(suffix) <= 200
        and value == f"{prefix}{int(suffix):06d}"
    )


def _section_id_is_valid(value: str, index: int) -> bool:
    return value == f"section:{index + 1:06d}"


def _source_parts(
    value: object,
) -> tuple[str, int, str, str, str | None] | _Marker:
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
        or (candidate_id is not None and type(candidate_id) is not str)
    ):
        return _FAILURE
    if (
        not 1 <= order <= 200
        or source_id != f"evidence-source:{order:06d}"
        or not _source_id_is_valid(source_id)
        or not title.strip()
        or len(title) > 512
        or not url.strip()
        or len(url) > 4096
        or (
            candidate_id is not None
            and (not candidate_id.strip() or len(candidate_id) > 256)
        )
    ):
        return _FAILURE
    try:
        trusted = _WorkflowEvidenceSource(
            source_id=source_id,
            order=order,
            title=title,
            url=url,
            candidate_id=candidate_id,
        )
        trusted_namespace = object.__getattribute__(trusted, "__dict__")
        if (
            dict.__getitem__(trusted_namespace, "source_id") != source_id
            or dict.__getitem__(trusted_namespace, "order") != order
            or dict.__getitem__(trusted_namespace, "title") != title
            or dict.__getitem__(trusted_namespace, "url") != url
            or dict.__getitem__(trusted_namespace, "candidate_id") != candidate_id
        ):
            del trusted
            return _FAILURE
        del trusted
    except Exception:
        return _FAILURE
    return source_id, order, title, url, candidate_id


def _provenance_parts(
    value: object,
) -> tuple[str, tuple[str, ...]] | _Marker:
    surface = _surface(value, _WorkflowEvidenceProvenance, _PROVENANCE_FIELDS)
    if type(surface) is not tuple:
        return _FAILURE
    namespace, _ = surface
    source_id = dict.__getitem__(namespace, "source_id")
    blocks = _string_tuple(
        dict.__getitem__(namespace, "evidence_blocks"),
        minimum=1,
        maximum=64,
    )
    if type(source_id) is not str or type(blocks) is not tuple:
        return _FAILURE
    if not _source_id_is_valid(source_id):
        return _FAILURE
    index = 0
    while index < tuple.__len__(blocks):
        block = tuple.__getitem__(blocks, index)
        if not block.strip() or len(block) > 16384:
            return _FAILURE
        index += 1
    try:
        trusted = _WorkflowEvidenceProvenance(
            source_id=source_id,
            evidence_blocks=blocks,
        )
        trusted_namespace = object.__getattribute__(trusted, "__dict__")
        if (
            dict.__getitem__(trusted_namespace, "source_id") != source_id
            or dict.__getitem__(trusted_namespace, "evidence_blocks") != blocks
        ):
            del trusted
            return _FAILURE
        del trusted
    except Exception:
        return _FAILURE
    return source_id, blocks


def _gate_projection(
    value: object,
) -> tuple[str, tuple[str, ...], tuple[tuple[str, ...], ...], int] | _Marker:
    surface = _surface(
        value,
        _WorkflowCitationEvidenceGateResult,
        _GATE_FIELDS,
    )
    if type(surface) is not tuple:
        return _FAILURE
    namespace, _ = surface
    outline_id = dict.__getitem__(namespace, "outline_id")
    section_ids = _string_tuple(
        dict.__getitem__(namespace, "section_ids"),
        minimum=1,
        maximum=12,
    )
    citation_values = dict.__getitem__(namespace, "cited_source_ids_by_section")
    attempt = dict.__getitem__(namespace, "attempt")
    if (
        type(outline_id) is not str
        or type(section_ids) is not tuple
        or type(citation_values) is not tuple
        or type(attempt) is not int
        or attempt != 1
        or tuple.__len__(citation_values) != tuple.__len__(section_ids)
    ):
        return _FAILURE
    citations: list[tuple[str, ...]] = []
    aggregate = 0
    index = 0
    while index < tuple.__len__(section_ids):
        section_id = tuple.__getitem__(section_ids, index)
        values = _string_tuple(
            tuple.__getitem__(citation_values, index),
            minimum=1,
            maximum=64,
        )
        if not _section_id_is_valid(section_id, index) or type(values) is not tuple:
            citations.clear()
            return _FAILURE
        seen: set[str] = set()
        source_index = 0
        while source_index < tuple.__len__(values):
            source_id = tuple.__getitem__(values, source_index)
            if not _source_id_is_valid(source_id) or source_id in seen:
                citations.clear()
                seen.clear()
                return _FAILURE
            seen.add(source_id)
            source_index += 1
        aggregate += tuple.__len__(values)
        if aggregate > 768:
            citations.clear()
            seen.clear()
            return _FAILURE
        citations.append(values)
        seen.clear()
        index += 1
    return outline_id, section_ids, tuple(citations), attempt


def _review_projection(
    value: object,
) -> tuple[str, str, tuple[str, ...], str, tuple[str, ...], str, int] | _Marker:
    surface = _surface(value, _WorkflowSectionCitationReview, _REVIEW_FIELDS)
    if type(surface) is not tuple:
        return _FAILURE
    namespace, _ = surface
    outline_id = dict.__getitem__(namespace, "outline_id")
    section_id = dict.__getitem__(namespace, "section_id")
    cited = _string_tuple(
        dict.__getitem__(namespace, "cited_source_ids"),
        minimum=1,
        maximum=64,
    )
    verdict = dict.__getitem__(namespace, "verdict")
    issues = _string_tuple(dict.__getitem__(namespace, "issues"), maximum=3)
    rationale = dict.__getitem__(namespace, "rationale")
    attempt = dict.__getitem__(namespace, "attempt")
    if (
        type(outline_id) is not str
        or type(section_id) is not str
        or type(cited) is not tuple
        or type(verdict) is not str
        or type(issues) is not tuple
        or type(rationale) is not str
        or type(attempt) is not int
        or attempt != 1
        or verdict not in ("supported", "unsupported", "uncertain")
        or not rationale.strip()
        or len(rationale) > 2048
    ):
        return _FAILURE
    expected_issues = tuple(issue for issue in _ISSUES if issue in issues)
    if issues != expected_issues:
        return _FAILURE
    if verdict == "supported" and issues:
        return _FAILURE
    if verdict == "unsupported" and not any(
        issue in ("insufficient_evidence", "possible_contradiction")
        for issue in issues
    ):
        return _FAILURE
    if verdict == "uncertain" and not issues:
        return _FAILURE
    return outline_id, section_id, cited, verdict, issues, rationale, attempt


def _disposition_projection(
    value: object,
) -> tuple[str, tuple[str, ...], tuple[str, ...], str, int] | _Marker:
    surface = _surface(
        value,
        _WorkflowCitationReviewDisposition,
        _DISPOSITION_FIELDS,
    )
    if type(surface) is not tuple:
        return _FAILURE
    namespace, _ = surface
    outline_id = dict.__getitem__(namespace, "outline_id")
    section_ids = _string_tuple(
        dict.__getitem__(namespace, "section_ids"),
        minimum=1,
        maximum=12,
    )
    section_dispositions = _string_tuple(
        dict.__getitem__(namespace, "section_dispositions"),
        minimum=1,
        maximum=12,
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
        or tuple.__len__(section_ids) != tuple.__len__(section_dispositions)
        or disposition not in ("ready", "needs_human_review", "blocked")
    ):
        return _FAILURE
    index = 0
    aggregate = "ready"
    while index < tuple.__len__(section_dispositions):
        value_at_index = tuple.__getitem__(section_dispositions, index)
        if value_at_index not in ("ready", "needs_human_review", "blocked"):
            return _FAILURE
        if value_at_index == "blocked":
            aggregate = "blocked"
        elif value_at_index == "needs_human_review" and aggregate == "ready":
            aggregate = "needs_human_review"
        index += 1
    if disposition != aggregate:
        return _FAILURE
    return outline_id, section_ids, section_dispositions, disposition, attempt


def _draft_projection(value: object) -> tuple[str, str, int, str] | _Marker:
    surface = _surface(value, _WorkflowSectionDraft, _DRAFT_FIELDS)
    if type(surface) is not tuple:
        return _FAILURE
    namespace, _ = surface
    outline_id = dict.__getitem__(namespace, "outline_id")
    section_id = dict.__getitem__(namespace, "section_id")
    attempt = dict.__getitem__(namespace, "attempt")
    content = dict.__getitem__(namespace, "content")
    if (
        type(outline_id) is not str
        or type(section_id) is not str
        or type(attempt) is not int
        or attempt != 1
        or type(content) is not str
        or not content.strip()
        or len(content) > 24576
    ):
        return _FAILURE
    try:
        trusted = _WorkflowSectionDraft(
            outline_id=outline_id,
            section_id=section_id,
            attempt=attempt,
            content=content,
        )
        trusted_namespace = object.__getattribute__(trusted, "__dict__")
        if (
            dict.__getitem__(trusted_namespace, "outline_id") != outline_id
            or dict.__getitem__(trusted_namespace, "section_id") != section_id
            or dict.__getitem__(trusted_namespace, "attempt") != attempt
            or dict.__getitem__(trusted_namespace, "content") != content
        ):
            del trusted
            return _FAILURE
        del trusted
    except Exception:
        return _FAILURE
    return outline_id, section_id, attempt, content


def _route(verdict: str) -> str:
    if verdict == "supported":
        return "ready"
    if verdict == "uncertain":
        return "needs_human_review"
    return "blocked"


def _global_ids(
    citations: tuple[tuple[str, ...], ...],
) -> tuple[str, ...] | _Marker:
    ordered: list[str] = []
    seen: set[str] = set()
    section_index = 0
    while section_index < tuple.__len__(citations):
        values = tuple.__getitem__(citations, section_index)
        source_index = 0
        while source_index < tuple.__len__(values):
            source_id = tuple.__getitem__(values, source_index)
            if source_id not in seen:
                seen.add(source_id)
                ordered.append(source_id)
                if len(ordered) > 64:
                    ordered.clear()
                    seen.clear()
                    return _FAILURE
            source_index += 1
        section_index += 1
    seen.clear()
    if not ordered:
        return _FAILURE
    return tuple(ordered)


def _composition_projection(
    value: object,
) -> tuple[
    tuple[_WorkflowSectionDraft, ...],
    tuple[str, tuple[str, ...], tuple[tuple[str, ...], ...], int],
    tuple[_WorkflowSectionCitationReview, ...],
    tuple[str, tuple[str, ...], tuple[str, ...], str, int],
    tuple[str, ...],
] | _Marker:
    surface = _surface(
        value,
        _WorkflowAcademicDraftComposition,
        _COMPOSITION_FIELDS,
    )
    if type(surface) is not tuple:
        return _FAILURE
    namespace, _ = surface
    drafts = dict.__getitem__(namespace, "drafts")
    gate_value = dict.__getitem__(namespace, "gate_result")
    reviews = dict.__getitem__(namespace, "reviews")
    disposition_value = dict.__getitem__(namespace, "disposition")
    merged = dict.__getitem__(namespace, "merged_draft")
    referenced = dict.__getitem__(namespace, "referenced_draft")
    if (
        type(drafts) is not tuple
        or type(reviews) is not tuple
        or not 1 <= tuple.__len__(drafts) <= 12
        or tuple.__len__(reviews) != tuple.__len__(drafts)
        or merged is not None
        or referenced is not None
    ):
        return _FAILURE
    gate = _gate_projection(gate_value)
    disposition = _disposition_projection(disposition_value)
    if type(gate) is not tuple or type(disposition) is not tuple:
        return _FAILURE
    outline_id = tuple.__getitem__(gate, 0)
    section_ids = tuple.__getitem__(gate, 1)
    citations = tuple.__getitem__(gate, 2)
    if (
        tuple.__len__(section_ids) != tuple.__len__(drafts)
        or tuple.__getitem__(disposition, 0) != outline_id
        or tuple.__getitem__(disposition, 1) != section_ids
        or tuple.__getitem__(disposition, 3)
        not in ("needs_human_review", "blocked")
    ):
        return _FAILURE
    routed: list[str] = []
    trusted_drafts: list[_WorkflowSectionDraft] = []
    trusted_reviews: list[_WorkflowSectionCitationReview] = []
    index = 0
    while index < tuple.__len__(drafts):
        draft_value = tuple.__getitem__(drafts, index)
        review_value = tuple.__getitem__(reviews, index)
        if (
            type(draft_value) is not _WorkflowSectionDraft
            or type(review_value) is not _WorkflowSectionCitationReview
        ):
            routed.clear()
            return _FAILURE
        draft = _draft_projection(draft_value)
        review = _review_projection(review_value)
        expected_section = tuple.__getitem__(section_ids, index)
        expected_citations = tuple.__getitem__(citations, index)
        if (
            type(draft) is not tuple
            or type(review) is not tuple
            or tuple.__getitem__(draft, 0) != outline_id
            or tuple.__getitem__(draft, 1) != expected_section
            or tuple.__getitem__(review, 0) != outline_id
            or tuple.__getitem__(review, 1) != expected_section
            or tuple.__getitem__(review, 2) != expected_citations
        ):
            routed.clear()
            trusted_drafts.clear()
            trusted_reviews.clear()
            return _FAILURE
        try:
            trusted_draft = _WorkflowSectionDraft(
                outline_id=tuple.__getitem__(draft, 0),
                section_id=tuple.__getitem__(draft, 1),
                attempt=tuple.__getitem__(draft, 2),
                content=tuple.__getitem__(draft, 3),
            )
            trusted_review = _WorkflowSectionCitationReview(
                outline_id=tuple.__getitem__(review, 0),
                section_id=tuple.__getitem__(review, 1),
                cited_source_ids=tuple.__getitem__(review, 2),
                verdict=tuple.__getitem__(review, 3),
                issues=tuple.__getitem__(review, 4),
                rationale=tuple.__getitem__(review, 5),
                attempt=tuple.__getitem__(review, 6),
            )
        except Exception:
            routed.clear()
            trusted_drafts.clear()
            trusted_reviews.clear()
            return _FAILURE
        trusted_drafts.append(trusted_draft)
        trusted_reviews.append(trusted_review)
        del trusted_draft
        del trusted_review
        routed.append(_route(tuple.__getitem__(review, 3)))
        index += 1
    routed_values = tuple(routed)
    routed.clear()
    if tuple.__getitem__(disposition, 2) != routed_values:
        trusted_drafts.clear()
        trusted_reviews.clear()
        return _FAILURE
    global_ids = _global_ids(citations)
    if type(global_ids) is not tuple:
        trusted_drafts.clear()
        trusted_reviews.clear()
        return _FAILURE
    try:
        trusted_gate = _WorkflowCitationEvidenceGateResult(
            outline_id=tuple.__getitem__(gate, 0),
            section_ids=tuple.__getitem__(gate, 1),
            cited_source_ids_by_section=tuple.__getitem__(gate, 2),
            attempt=tuple.__getitem__(gate, 3),
        )
        trusted_disposition = _WorkflowCitationReviewDisposition(
            outline_id=tuple.__getitem__(disposition, 0),
            section_ids=tuple.__getitem__(disposition, 1),
            section_dispositions=tuple.__getitem__(disposition, 2),
            disposition=tuple.__getitem__(disposition, 3),
            attempt=tuple.__getitem__(disposition, 4),
        )
        trusted_draft_values = tuple(trusted_drafts)
        trusted_review_values = tuple(trusted_reviews)
        trusted_composition = _WorkflowAcademicDraftComposition(
            drafts=trusted_draft_values,
            gate_result=trusted_gate,
            reviews=trusted_review_values,
            disposition=trusted_disposition,
            merged_draft=None,
            referenced_draft=None,
        )
        dumped = _BaseModel.model_dump(trusted_composition, mode="json")
        encoded = _json.dumps(
            dumped,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > _NON_READY_COMPOSITION_MAX_BYTES:
            del trusted_composition
            trusted_drafts.clear()
            trusted_reviews.clear()
            return _FAILURE
        del dumped
        del encoded
        del trusted_composition
        del trusted_gate
        del trusted_disposition
    except Exception:
        trusted_drafts.clear()
        trusted_reviews.clear()
        return _FAILURE
    trusted_drafts.clear()
    trusted_reviews.clear()
    return trusted_draft_values, gate, trusted_review_values, disposition, global_ids


def _state_projection(
    value: object,
) -> tuple[
    tuple[_WorkflowEvidenceSource, ...],
    tuple[_WorkflowEvidenceProvenance, ...],
    str,
    tuple[str, ...],
] | _Marker:
    surface = _surface(value, _AcademicWorkflowState, _STATE_FIELDS)
    if type(surface) is not tuple:
        return _FAILURE
    namespace, _ = surface
    schema_version = dict.__getitem__(namespace, "schema_version")
    phase = dict.__getitem__(namespace, "phase")
    status = dict.__getitem__(namespace, "status")
    evidence_value = dict.__getitem__(namespace, "research_evidence")
    outline_value = dict.__getitem__(namespace, "outline")
    decision_value = dict.__getitem__(namespace, "outline_decision")
    if (
        type(schema_version) is not str
        or schema_version != "1"
        or type(phase) is not str
        or phase != "outline_approved"
        or type(status) is not str
        or status != "completed"
    ):
        return _FAILURE
    full = frozenset(_EVIDENCE_FIELDS)
    missing_provenance = frozenset(_EVIDENCE_FIELDS[:-1])
    evidence_surface = _surface(
        evidence_value,
        _WorkflowResearchEvidence,
        _EVIDENCE_FIELDS,
        (full, missing_provenance),
    )
    outline_surface = _surface(outline_value, _WorkflowOutline, _OUTLINE_FIELDS)
    decision_surface = _surface(
        decision_value,
        _WorkflowOutlineDecisionRecord,
        _DECISION_FIELDS,
    )
    if (
        type(evidence_surface) is not tuple
        or type(outline_surface) is not tuple
        or type(decision_surface) is not tuple
    ):
        return _FAILURE
    evidence_namespace, evidence_fields_set = evidence_surface
    outline_namespace, _ = outline_surface
    decision_namespace, _ = decision_surface
    evidence_id = dict.__getitem__(evidence_namespace, "evidence_id")
    evidence_attempt = dict.__getitem__(evidence_namespace, "attempt")
    sources = dict.__getitem__(evidence_namespace, "sources")
    provenance = dict.__getitem__(evidence_namespace, "provenance")
    outline_id = dict.__getitem__(outline_namespace, "outline_id")
    outline_evidence_id = dict.__getitem__(outline_namespace, "evidence_id")
    outline_attempt = dict.__getitem__(outline_namespace, "attempt")
    sections = dict.__getitem__(outline_namespace, "sections")
    decision_outline = dict.__getitem__(decision_namespace, "outline_id")
    decision = dict.__getitem__(decision_namespace, "decision")
    decision_attempt = dict.__getitem__(decision_namespace, "attempt")
    if (
        type(evidence_id) is not str
        or type(evidence_attempt) is not int
        or evidence_attempt != 1
        or type(sources) is not tuple
        or type(provenance) is not tuple
        or type(outline_id) is not str
        or type(outline_evidence_id) is not str
        or outline_evidence_id != evidence_id
        or type(outline_attempt) is not int
        or outline_attempt != 1
        or type(sections) is not tuple
        or not 1 <= tuple.__len__(sections) <= 12
        or type(decision_outline) is not str
        or decision_outline != outline_id
        or type(decision) is not str
        or decision != "approve"
        or type(decision_attempt) is not int
        or decision_attempt != 1
    ):
        return _FAILURE
    if "provenance" not in evidence_fields_set and tuple.__len__(provenance) != 0:
        return _FAILURE
    section_ids: list[str] = []
    section_index = 0
    while section_index < tuple.__len__(sections):
        section = tuple.__getitem__(sections, section_index)
        section_surface = _surface(section, _WorkflowOutlineSection, _SECTION_FIELDS)
        if type(section_surface) is not tuple:
            section_ids.clear()
            return _FAILURE
        section_namespace, _ = section_surface
        section_id = dict.__getitem__(section_namespace, "section_id")
        order = dict.__getitem__(section_namespace, "order")
        title = dict.__getitem__(section_namespace, "title")
        brief = dict.__getitem__(section_namespace, "brief")
        if (
            type(section_id) is not str
            or type(order) is not int
            or order != section_index + 1
            or not _section_id_is_valid(section_id, section_index)
            or type(title) is not str
            or not title.strip()
            or type(brief) is not str
            or not brief.strip()
        ):
            section_ids.clear()
            return _FAILURE
        section_ids.append(section_id)
        section_index += 1
    if tuple.__len__(sources) > 200 or tuple.__len__(provenance) > 64:
        section_ids.clear()
        return _FAILURE
    source_ids: set[str] = set()
    urls: set[str] = set()
    candidate_ids: set[str] = set()
    source_index = 0
    while source_index < tuple.__len__(sources):
        source = tuple.__getitem__(sources, source_index)
        if type(source) is not _WorkflowEvidenceSource:
            section_ids.clear()
            source_ids.clear()
            urls.clear()
            candidate_ids.clear()
            return _FAILURE
        parts = _source_parts(source)
        if type(parts) is not tuple:
            section_ids.clear()
            source_ids.clear()
            urls.clear()
            candidate_ids.clear()
            return _FAILURE
        source_id, order, _, url, candidate_id = parts
        if (
            order != source_index + 1
            or source_id in source_ids
            or url in urls
            or (candidate_id is not None and candidate_id in candidate_ids)
        ):
            section_ids.clear()
            source_ids.clear()
            urls.clear()
            candidate_ids.clear()
            return _FAILURE
        source_ids.add(source_id)
        urls.add(url)
        if candidate_id is not None:
            candidate_ids.add(candidate_id)
        source_index += 1
    provenance_ids: set[str] = set()
    block_count = 0
    character_count = 0
    last_source_position = -1
    provenance_index = 0
    source_positions = {
        tuple.__getitem__(_source_parts(tuple.__getitem__(sources, index)), 0): index
        for index in range(tuple.__len__(sources))
    }
    while provenance_index < tuple.__len__(provenance):
        item = tuple.__getitem__(provenance, provenance_index)
        if type(item) is not _WorkflowEvidenceProvenance:
            return _FAILURE
        parts = _provenance_parts(item)
        if type(parts) is not tuple:
            return _FAILURE
        source_id, blocks = parts
        if source_id in provenance_ids or source_id not in source_positions:
            return _FAILURE
        position = source_positions[source_id]
        if position <= last_source_position:
            return _FAILURE
        last_source_position = position
        provenance_ids.add(source_id)
        block_count += tuple.__len__(blocks)
        block_index = 0
        while block_index < tuple.__len__(blocks):
            character_count += len(tuple.__getitem__(blocks, block_index))
            block_index += 1
        if block_count > 64 or character_count > 262144:
            return _FAILURE
        provenance_index += 1
    source_ids.clear()
    urls.clear()
    candidate_ids.clear()
    provenance_ids.clear()
    source_positions.clear()
    return sources, provenance, outline_id, tuple(section_ids)


def _project_outputs(
    sources: tuple[_WorkflowEvidenceSource, ...],
    provenance: tuple[_WorkflowEvidenceProvenance, ...],
    ordered_ids: tuple[str, ...],
) -> tuple[
    tuple[_WorkflowEvidenceSource, ...],
    tuple[_WorkflowEvidenceProvenance, ...],
] | _Marker:
    source_map: dict[str, _WorkflowEvidenceSource] = {}
    provenance_map: dict[str, _WorkflowEvidenceProvenance] = {}
    index = 0
    while index < tuple.__len__(sources):
        source = tuple.__getitem__(sources, index)
        parts = _source_parts(source)
        if type(parts) is not tuple:
            source_map.clear()
            return _FAILURE
        source_map[tuple.__getitem__(parts, 0)] = source
        index += 1
    index = 0
    while index < tuple.__len__(provenance):
        item = tuple.__getitem__(provenance, index)
        parts = _provenance_parts(item)
        if type(parts) is not tuple:
            source_map.clear()
            provenance_map.clear()
            return _FAILURE
        provenance_map[tuple.__getitem__(parts, 0)] = item
        index += 1
    projected_sources: list[_WorkflowEvidenceSource] = []
    projected_provenance: list[_WorkflowEvidenceProvenance] = []
    index = 0
    while index < tuple.__len__(ordered_ids):
        source_id = tuple.__getitem__(ordered_ids, index)
        if source_id not in source_map or source_id not in provenance_map:
            projected_sources.clear()
            projected_provenance.clear()
            source_map.clear()
            provenance_map.clear()
            return _FAILURE
        projected_sources.append(source_map[source_id])
        projected_provenance.append(provenance_map[source_id])
        index += 1
    source_map.clear()
    provenance_map.clear()
    return tuple(projected_sources), tuple(projected_provenance)


class WorkflowAcademicReviewHandoff(_BaseModel):
    """Mechanical non-ready review package; it makes no factual claim."""

    model_config = _ConfigDict(frozen=True, extra="forbid", strict=True)

    composition: _WorkflowAcademicDraftComposition
    cited_sources: tuple[_WorkflowEvidenceSource, ...]
    cited_provenance: tuple[_WorkflowEvidenceProvenance, ...]

    @_model_validator(mode="before")
    @classmethod
    def _require_exact_input(cls, value: object, info: _ValidationInfo) -> object:
        if type(value) is cls:
            return value
        if type(value) is not dict:
            raise TypeError("academic review handoff must be an exact mapping")
        keys = tuple(dict.keys(value))
        index = 0
        while index < tuple.__len__(keys):
            if type(tuple.__getitem__(keys, index)) is not str:
                raise TypeError("academic review handoff fields must be exact")
            index += 1
        if set(keys) != set(_HANDOFF_FIELDS):
            raise TypeError("academic review handoff fields must be exact")
        composition = dict.__getitem__(value, "composition")
        sources = dict.__getitem__(value, "cited_sources")
        provenance = dict.__getitem__(value, "cited_provenance")
        expected_container = list if info.mode == "json" else tuple
        if type(sources) is not expected_container or type(provenance) is not expected_container:
            raise TypeError("academic review handoff containers must be exact")
        if info.mode == "json":
            return {
                "composition": composition,
                "cited_sources": tuple(sources),
                "cited_provenance": tuple(provenance),
            }
        if type(composition) is not _WorkflowAcademicDraftComposition:
            raise TypeError("academic review handoff composition must be exact")
        index = 0
        while index < tuple.__len__(sources):
            if type(tuple.__getitem__(sources, index)) is not _WorkflowEvidenceSource:
                raise TypeError("academic review handoff sources must be exact")
            index += 1
        index = 0
        while index < tuple.__len__(provenance):
            if type(tuple.__getitem__(provenance, index)) is not _WorkflowEvidenceProvenance:
                raise TypeError("academic review handoff provenance must be exact")
            index += 1
        return value

    @_model_validator(mode="after")
    def _validate_binding(self) -> WorkflowAcademicReviewHandoff:
        namespace = object.__getattribute__(self, "__dict__")
        composition = dict.__getitem__(namespace, "composition")
        sources = dict.__getitem__(namespace, "cited_sources")
        provenance = dict.__getitem__(namespace, "cited_provenance")
        composition_plan = _composition_projection(composition)
        if (
            type(composition_plan) is not tuple
            or type(sources) is not tuple
            or type(provenance) is not tuple
            or not 1 <= tuple.__len__(sources) <= 64
            or tuple.__len__(sources) != tuple.__len__(provenance)
        ):
            raise ValueError("academic review handoff binding is invalid")
        expected_ids = tuple.__getitem__(composition_plan, 4)
        actual_ids: list[str] = []
        index = 0
        while index < tuple.__len__(sources):
            source = tuple.__getitem__(sources, index)
            provenance_item = tuple.__getitem__(provenance, index)
            if (
                type(source) is not _WorkflowEvidenceSource
                or type(provenance_item) is not _WorkflowEvidenceProvenance
            ):
                actual_ids.clear()
                raise ValueError("academic review handoff members are invalid")
            source_parts = _source_parts(source)
            provenance_parts = _provenance_parts(provenance_item)
            if type(source_parts) is not tuple or type(provenance_parts) is not tuple:
                actual_ids.clear()
                raise ValueError("academic review handoff members are invalid")
            source_id = tuple.__getitem__(source_parts, 0)
            if source_id != tuple.__getitem__(provenance_parts, 0):
                actual_ids.clear()
                raise ValueError("academic review handoff member binding is invalid")
            actual_ids.append(source_id)
            index += 1
        actual = tuple(actual_ids)
        actual_ids.clear()
        if actual != expected_ids or len(set(actual)) != len(actual):
            raise ValueError("academic review handoff citation order is invalid")
        return self


def _execute_builder(
    state: object,
    composition: object,
) -> WorkflowAcademicReviewHandoff | _Marker:
    try:
        state_plan = _state_projection(state)
        composition_plan = _composition_projection(composition)
        if type(state_plan) is not tuple or type(composition_plan) is not tuple:
            return _FAILURE
        sources = tuple.__getitem__(state_plan, 0)
        provenance = tuple.__getitem__(state_plan, 1)
        state_outline = tuple.__getitem__(state_plan, 2)
        state_sections = tuple.__getitem__(state_plan, 3)
        drafts = tuple.__getitem__(composition_plan, 0)
        embedded_gate = tuple.__getitem__(composition_plan, 1)
        reviews = tuple.__getitem__(composition_plan, 2)
        embedded_disposition = tuple.__getitem__(composition_plan, 3)
        if (
            state_outline != tuple.__getitem__(embedded_gate, 0)
            or state_sections != tuple.__getitem__(embedded_gate, 1)
        ):
            return _FAILURE
        recomputed_gate_value = _gate_citation_evidence(state, drafts)
        recomputed_gate = _gate_projection(recomputed_gate_value)
        if type(recomputed_gate) is not tuple or recomputed_gate != embedded_gate:
            return _FAILURE
        recomputed_disposition_value = _gate_citation_review_disposition(
            recomputed_gate_value,
            reviews,
        )
        recomputed_disposition = _disposition_projection(
            recomputed_disposition_value
        )
        if (
            type(recomputed_disposition) is not tuple
            or recomputed_disposition != embedded_disposition
            or tuple.__getitem__(recomputed_disposition, 3)
            not in ("needs_human_review", "blocked")
        ):
            return _FAILURE
        ordered_ids = _global_ids(tuple.__getitem__(recomputed_gate, 2))
        if type(ordered_ids) is not tuple:
            return _FAILURE
        projected = _project_outputs(sources, provenance, ordered_ids)
        if type(projected) is not tuple:
            return _FAILURE
        result = WorkflowAcademicReviewHandoff(
            composition=composition,
            cited_sources=tuple.__getitem__(projected, 0),
            cited_provenance=tuple.__getitem__(projected, 1),
        )
        if type(result) is not WorkflowAcademicReviewHandoff:
            return _FAILURE
        result_namespace = object.__getattribute__(result, "__dict__")
        if dict.__getitem__(result_namespace, "composition") is not composition:
            return _FAILURE
        return result
    except Exception:
        return _FAILURE


def _raise_failure() -> None:
    raise _AcademicReviewHandoffError(_ERROR_TEXT)


def build_academic_review_handoff(
    state: _AcademicWorkflowState,
    composition: _WorkflowAcademicDraftComposition,
) -> WorkflowAcademicReviewHandoff:
    result = _execute_builder(state, composition)
    del state
    del composition
    if type(result) is not WorkflowAcademicReviewHandoff:
        del result
        _raise_failure()
    return result
