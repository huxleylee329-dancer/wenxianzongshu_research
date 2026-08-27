"""Strict off-graph composition of the frozen academic-writing stages."""

from __future__ import annotations

import asyncio as _asyncio
import json as _json
from typing import Protocol as _Protocol

from pydantic import BaseModel as _BaseModel
from pydantic import ConfigDict as _ConfigDict
from pydantic import ValidationInfo as _ValidationInfo
from pydantic import model_validator as _model_validator

from .citation_evidence_gate import (
    WorkflowCitationEvidenceGateResult,
    gate_citation_evidence as _gate_citation_evidence,
)
from .citation_review_disposition import (
    WorkflowCitationReviewDisposition,
    gate_citation_review_disposition as _gate_citation_review_disposition,
)
from .citation_reviewer import (
    WorkflowSectionCitationReview,
)
from .references_renderer import (
    WorkflowReferencedDraft,
    render_references as _render_references,
)
from .section_merger import WorkflowMergedDraft, merge_sections as _merge_sections
from .state import AcademicWorkflowState, WorkflowSectionDraft


__all__ = (
    "WorkflowAcademicDraftComposition",
    "GPTResearcherAcademicDraftComposer",
)


_ERROR_TEXT = "academic draft composer failed"
_NON_READY_MAX_BYTES = 1526996
_READY_MAX_BYTES = 4791994
_GLOBAL_SOURCE_MAX_COUNT = 64
_REFERENCES_PREFIX = "\n\n## References\n\n"

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
_MERGED_FIELDS = ("outline_id", "section_ids", "attempt", "content")
_REFERENCED_FIELDS = (
    "outline_id",
    "section_ids",
    "reference_source_ids",
    "attempt",
    "content",
)


class _SectionWriterSequence(_Protocol):
    async def write_sections(
        self,
        state: AcademicWorkflowState,
    ) -> tuple[WorkflowSectionDraft, ...]: ...


class _CitationReviewer(_Protocol):
    async def review_citations(
        self,
        state: AcademicWorkflowState,
        drafts: tuple[WorkflowSectionDraft, ...],
        gate_result: WorkflowCitationEvidenceGateResult,
    ) -> tuple[WorkflowSectionCitationReview, ...]: ...


class _AcademicDraftComposerError(RuntimeError):
    pass


class _Marker:
    __slots__ = ()


_FAILURE = _Marker()
_PRODUCTION_SEQUENCE = _Marker()
_PRODUCTION_REVIEWER = _Marker()


def _canonical_bytes(value: object) -> bytes | _Marker:
    try:
        return _json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
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
        keys = tuple(dict.keys(value))
        index = 0
        while index < tuple.__len__(keys):
            key = tuple.__getitem__(keys, index)
            if type(key) is not str or not _validate_json_value(
                dict.__getitem__(value, key)
            ):
                return False
            index += 1
        return True
    return False


def _same_json_shape(left: object, right: object) -> bool:
    if type(left) is not type(right):
        return False
    if type(left) is dict:
        left_keys = tuple(dict.keys(left))
        right_keys = tuple(dict.keys(right))
        if any(type(key) is not str for key in left_keys) or any(
            type(key) is not str for key in right_keys
        ):
            return False
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


def _surface(
    value: object,
    expected_type: type[object],
    fields: tuple[str, ...],
) -> dict[str, object] | _Marker:
    if type(value) is not expected_type:
        return _FAILURE
    try:
        namespace = object.__getattribute__(value, "__dict__")
        fields_set = object.__getattribute__(value, "__pydantic_fields_set__")
        extra = object.__getattribute__(value, "__pydantic_extra__")
        private = object.__getattribute__(value, "__pydantic_private__")
        if type(namespace) is not dict or type(fields_set) is not set:
            return _FAILURE
        keys = tuple(dict.keys(namespace))
        members = tuple(set.__iter__(fields_set))
        if (
            any(type(key) is not str for key in keys)
            or any(type(member) is not str for member in members)
            or extra is not None
            or private is not None
        ):
            return _FAILURE
        if keys != fields or fields_set != set(fields):
            return _FAILURE
        return namespace
    except Exception:
        return _FAILURE


def _copy_string_tuple(value: object) -> tuple[str, ...] | _Marker:
    if type(value) is not tuple:
        return _FAILURE
    copied: list[str] = []
    index = 0
    while index < tuple.__len__(value):
        item = tuple.__getitem__(value, index)
        if type(item) is not str:
            return _FAILURE
        copied.append(item)
        index += 1
    return tuple(copied)


def _restore_public_model(
    value: _BaseModel,
    expected_type: type[_BaseModel],
) -> _BaseModel | _Marker:
    try:
        dumped = _BaseModel.model_dump(value, mode="json")
        if type(dumped) is not dict or not _validate_json_value(dumped):
            return _FAILURE
        encoded = _canonical_bytes(dumped)
        if type(encoded) is not bytes:
            return _FAILURE
        restored = expected_type.model_validate_json(encoded)
        if type(restored) is not expected_type:
            return _FAILURE
        restored_dumped = _BaseModel.model_dump(restored, mode="json")
        restored_encoded = _canonical_bytes(restored_dumped)
        if (
            type(restored_dumped) is not dict
            or type(restored_encoded) is not bytes
            or not _same_json_shape(dumped, restored_dumped)
            or restored_encoded != encoded
        ):
            return _FAILURE
        return restored
    except Exception:
        return _FAILURE


def _trusted_draft(value: object) -> WorkflowSectionDraft | _Marker:
    namespace = _surface(value, WorkflowSectionDraft, _DRAFT_FIELDS)
    if type(namespace) is not dict:
        return _FAILURE
    try:
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
        ):
            return _FAILURE
        trusted = WorkflowSectionDraft(
            outline_id=outline_id,
            section_id=section_id,
            attempt=attempt,
            content=content,
        )
        restored = _restore_public_model(trusted, WorkflowSectionDraft)
        return restored if type(restored) is WorkflowSectionDraft else _FAILURE
    except Exception:
        return _FAILURE


def _trusted_gate(value: object) -> WorkflowCitationEvidenceGateResult | _Marker:
    namespace = _surface(value, WorkflowCitationEvidenceGateResult, _GATE_FIELDS)
    if type(namespace) is not dict:
        return _FAILURE
    try:
        outline_id = dict.__getitem__(namespace, "outline_id")
        section_ids = _copy_string_tuple(dict.__getitem__(namespace, "section_ids"))
        citations_value = dict.__getitem__(
            namespace, "cited_source_ids_by_section"
        )
        attempt = dict.__getitem__(namespace, "attempt")
        if (
            type(outline_id) is not str
            or type(section_ids) is not tuple
            or type(citations_value) is not tuple
            or type(attempt) is not int
            or attempt != 1
        ):
            return _FAILURE
        citations: list[tuple[str, ...]] = []
        index = 0
        while index < tuple.__len__(citations_value):
            inner = _copy_string_tuple(tuple.__getitem__(citations_value, index))
            if type(inner) is not tuple:
                return _FAILURE
            citations.append(inner)
            index += 1
        trusted = WorkflowCitationEvidenceGateResult(
            outline_id=outline_id,
            section_ids=section_ids,
            cited_source_ids_by_section=tuple(citations),
            attempt=attempt,
        )
        restored = _restore_public_model(
            trusted, WorkflowCitationEvidenceGateResult
        )
        return (
            restored
            if type(restored) is WorkflowCitationEvidenceGateResult
            else _FAILURE
        )
    except Exception:
        return _FAILURE


def _trusted_review(value: object) -> WorkflowSectionCitationReview | _Marker:
    namespace = _surface(value, WorkflowSectionCitationReview, _REVIEW_FIELDS)
    if type(namespace) is not dict:
        return _FAILURE
    try:
        outline_id = dict.__getitem__(namespace, "outline_id")
        section_id = dict.__getitem__(namespace, "section_id")
        cited_ids = _copy_string_tuple(
            dict.__getitem__(namespace, "cited_source_ids")
        )
        verdict = dict.__getitem__(namespace, "verdict")
        issues = _copy_string_tuple(dict.__getitem__(namespace, "issues"))
        rationale = dict.__getitem__(namespace, "rationale")
        attempt = dict.__getitem__(namespace, "attempt")
        if (
            type(outline_id) is not str
            or type(section_id) is not str
            or type(cited_ids) is not tuple
            or type(verdict) is not str
            or type(issues) is not tuple
            or type(rationale) is not str
            or type(attempt) is not int
            or attempt != 1
        ):
            return _FAILURE
        trusted = WorkflowSectionCitationReview(
            outline_id=outline_id,
            section_id=section_id,
            cited_source_ids=cited_ids,
            verdict=verdict,
            issues=issues,
            rationale=rationale,
            attempt=attempt,
        )
        restored = _restore_public_model(trusted, WorkflowSectionCitationReview)
        return (
            restored
            if type(restored) is WorkflowSectionCitationReview
            else _FAILURE
        )
    except Exception:
        return _FAILURE


def _trusted_disposition(
    value: object,
) -> WorkflowCitationReviewDisposition | _Marker:
    namespace = _surface(
        value, WorkflowCitationReviewDisposition, _DISPOSITION_FIELDS
    )
    if type(namespace) is not dict:
        return _FAILURE
    try:
        outline_id = dict.__getitem__(namespace, "outline_id")
        section_ids = _copy_string_tuple(dict.__getitem__(namespace, "section_ids"))
        section_dispositions = _copy_string_tuple(
            dict.__getitem__(namespace, "section_dispositions")
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
        trusted = WorkflowCitationReviewDisposition(
            outline_id=outline_id,
            section_ids=section_ids,
            section_dispositions=section_dispositions,
            disposition=disposition,
            attempt=attempt,
        )
        restored = _restore_public_model(
            trusted, WorkflowCitationReviewDisposition
        )
        return (
            restored
            if type(restored) is WorkflowCitationReviewDisposition
            else _FAILURE
        )
    except Exception:
        return _FAILURE


def _trusted_merged(value: object) -> WorkflowMergedDraft | _Marker:
    namespace = _surface(value, WorkflowMergedDraft, _MERGED_FIELDS)
    if type(namespace) is not dict:
        return _FAILURE
    try:
        outline_id = dict.__getitem__(namespace, "outline_id")
        section_ids = _copy_string_tuple(dict.__getitem__(namespace, "section_ids"))
        attempt = dict.__getitem__(namespace, "attempt")
        content = dict.__getitem__(namespace, "content")
        if (
            type(outline_id) is not str
            or type(section_ids) is not tuple
            or type(attempt) is not int
            or attempt != 1
            or type(content) is not str
        ):
            return _FAILURE
        trusted = WorkflowMergedDraft(
            outline_id=outline_id,
            section_ids=section_ids,
            attempt=attempt,
            content=content,
        )
        restored = _restore_public_model(trusted, WorkflowMergedDraft)
        return restored if type(restored) is WorkflowMergedDraft else _FAILURE
    except Exception:
        return _FAILURE


def _trusted_referenced(value: object) -> WorkflowReferencedDraft | _Marker:
    namespace = _surface(value, WorkflowReferencedDraft, _REFERENCED_FIELDS)
    if type(namespace) is not dict:
        return _FAILURE
    try:
        outline_id = dict.__getitem__(namespace, "outline_id")
        section_ids = _copy_string_tuple(dict.__getitem__(namespace, "section_ids"))
        reference_ids = _copy_string_tuple(
            dict.__getitem__(namespace, "reference_source_ids")
        )
        attempt = dict.__getitem__(namespace, "attempt")
        content = dict.__getitem__(namespace, "content")
        if (
            type(outline_id) is not str
            or type(section_ids) is not tuple
            or type(reference_ids) is not tuple
            or type(attempt) is not int
            or attempt != 1
            or type(content) is not str
        ):
            return _FAILURE
        trusted = WorkflowReferencedDraft(
            outline_id=outline_id,
            section_ids=section_ids,
            reference_source_ids=reference_ids,
            attempt=attempt,
            content=content,
        )
        restored = _restore_public_model(trusted, WorkflowReferencedDraft)
        return restored if type(restored) is WorkflowReferencedDraft else _FAILURE
    except Exception:
        return _FAILURE


def _trusted_drafts(value: object) -> tuple[WorkflowSectionDraft, ...] | _Marker:
    if type(value) is not tuple or not 1 <= tuple.__len__(value) <= 12:
        return _FAILURE
    trusted: list[WorkflowSectionDraft] = []
    index = 0
    while index < tuple.__len__(value):
        member = tuple.__getitem__(value, index)
        draft = _trusted_draft(member)
        del member
        if type(draft) is not WorkflowSectionDraft:
            trusted.clear()
            return _FAILURE
        trusted.append(draft)
        del draft
        index += 1
    return tuple(trusted)


def _trusted_reviews(
    value: object,
) -> tuple[WorkflowSectionCitationReview, ...] | _Marker:
    if type(value) is not tuple or not 1 <= tuple.__len__(value) <= 12:
        return _FAILURE
    trusted: list[WorkflowSectionCitationReview] = []
    index = 0
    while index < tuple.__len__(value):
        member = tuple.__getitem__(value, index)
        review = _trusted_review(member)
        del member
        if type(review) is not WorkflowSectionCitationReview:
            trusted.clear()
            return _FAILURE
        trusted.append(review)
        del review
        index += 1
    return tuple(trusted)


def _stable_global_ids(
    values: tuple[tuple[str, ...], ...],
) -> tuple[str, ...] | _Marker:
    seen: set[str] = set()
    ordered: list[str] = []
    section_index = 0
    while section_index < tuple.__len__(values):
        inner = tuple.__getitem__(values, section_index)
        if type(inner) is not tuple:
            return _FAILURE
        source_index = 0
        while source_index < tuple.__len__(inner):
            source_id = tuple.__getitem__(inner, source_index)
            if type(source_id) is not str:
                return _FAILURE
            if source_id not in seen:
                seen.add(source_id)
                ordered.append(source_id)
                if len(ordered) > _GLOBAL_SOURCE_MAX_COUNT:
                    return _FAILURE
            source_index += 1
        section_index += 1
    if not ordered:
        return _FAILURE
    return tuple(ordered)


def _route(verdict: str) -> str:
    if verdict == "supported":
        return "ready"
    if verdict == "uncertain":
        return "needs_human_review"
    return "blocked"


def _overall(values: tuple[str, ...]) -> str:
    result = "ready"
    index = 0
    while index < tuple.__len__(values):
        value = tuple.__getitem__(values, index)
        if value == "blocked":
            return "blocked"
        if value == "needs_human_review":
            result = "needs_human_review"
        index += 1
    return result


class WorkflowAcademicDraftComposition(_BaseModel):
    """Bound composer artifacts; routing labels are not factual approval."""

    model_config = _ConfigDict(frozen=True, extra="forbid", strict=True)

    drafts: tuple[WorkflowSectionDraft, ...]
    gate_result: WorkflowCitationEvidenceGateResult
    reviews: tuple[WorkflowSectionCitationReview, ...]
    disposition: WorkflowCitationReviewDisposition
    merged_draft: WorkflowMergedDraft | None
    referenced_draft: WorkflowReferencedDraft | None

    @_model_validator(mode="before")
    @classmethod
    def _require_exact_input(cls, value: object, info: _ValidationInfo) -> object:
        if type(value) is cls:
            return value
        if type(value) is not dict:
            raise TypeError("academic draft composition must be an exact mapping")
        keys = tuple(dict.keys(value))
        if (
            any(type(key) is not str for key in keys)
            or set(keys) != set(_COMPOSITION_FIELDS)
        ):
            raise TypeError("academic draft composition fields must be exact")
        drafts_value = dict.__getitem__(value, "drafts")
        gate_value = dict.__getitem__(value, "gate_result")
        reviews_value = dict.__getitem__(value, "reviews")
        disposition_value = dict.__getitem__(value, "disposition")
        merged_value = dict.__getitem__(value, "merged_draft")
        referenced_value = dict.__getitem__(value, "referenced_draft")
        if info.mode == "json":
            if type(drafts_value) is not list or type(reviews_value) is not list:
                raise TypeError("academic draft composition arrays must be exact")
            return {
                "drafts": tuple(drafts_value),
                "gate_result": gate_value,
                "reviews": tuple(reviews_value),
                "disposition": disposition_value,
                "merged_draft": merged_value,
                "referenced_draft": referenced_value,
            }
        drafts = _trusted_drafts(drafts_value)
        gate = _trusted_gate(gate_value)
        reviews = _trusted_reviews(reviews_value)
        disposition = _trusted_disposition(disposition_value)
        merged = None if merged_value is None else _trusted_merged(merged_value)
        referenced = (
            None
            if referenced_value is None
            else _trusted_referenced(referenced_value)
        )
        if (
            type(drafts) is not tuple
            or type(gate) is not WorkflowCitationEvidenceGateResult
            or type(reviews) is not tuple
            or type(disposition) is not WorkflowCitationReviewDisposition
            or (merged is not None and type(merged) is not WorkflowMergedDraft)
            or (
                referenced is not None
                and type(referenced) is not WorkflowReferencedDraft
            )
        ):
            raise TypeError("academic draft composition values must be exact")
        return {
            "drafts": drafts,
            "gate_result": gate,
            "reviews": reviews,
            "disposition": disposition,
            "merged_draft": merged,
            "referenced_draft": referenced,
        }

    @_model_validator(mode="after")
    def _validate_binding_and_cap(self) -> WorkflowAcademicDraftComposition:
        section_count = tuple.__len__(self.drafts)
        if (
            not 1 <= section_count <= 12
            or tuple.__len__(self.reviews) != section_count
            or tuple.__len__(self.gate_result.section_ids) != section_count
            or tuple.__len__(self.gate_result.cited_source_ids_by_section)
            != section_count
            or tuple.__len__(self.disposition.section_ids) != section_count
            or tuple.__len__(self.disposition.section_dispositions) != section_count
        ):
            raise ValueError("academic draft composition section binding is invalid")
        outline_id = self.gate_result.outline_id
        section_ids = self.gate_result.section_ids
        routed: list[str] = []
        index = 0
        while index < section_count:
            draft = tuple.__getitem__(self.drafts, index)
            review = tuple.__getitem__(self.reviews, index)
            expected_section = tuple.__getitem__(section_ids, index)
            expected_citations = tuple.__getitem__(
                self.gate_result.cited_source_ids_by_section, index
            )
            if (
                draft.outline_id != outline_id
                or draft.section_id != expected_section
                or type(draft.attempt) is not int
                or draft.attempt != 1
                or review.outline_id != outline_id
                or review.section_id != expected_section
                or review.cited_source_ids != expected_citations
                or type(review.attempt) is not int
                or review.attempt != 1
            ):
                raise ValueError("academic draft composition artifact binding is invalid")
            routed.append(_route(review.verdict))
            index += 1
        routed_values = tuple(routed)
        if (
            type(self.gate_result.attempt) is not int
            or self.gate_result.attempt != 1
            or self.disposition.outline_id != outline_id
            or self.disposition.section_ids != section_ids
            or type(self.disposition.attempt) is not int
            or self.disposition.attempt != 1
            or self.disposition.section_dispositions != routed_values
            or self.disposition.disposition != _overall(routed_values)
        ):
            raise ValueError("academic draft composition routing binding is invalid")
        global_ids = _stable_global_ids(
            self.gate_result.cited_source_ids_by_section
        )
        if type(global_ids) is not tuple:
            raise ValueError("academic draft composition global citations are invalid")
        ready = self.disposition.disposition == "ready"
        if ready:
            if (
                type(self.merged_draft) is not WorkflowMergedDraft
                or type(self.referenced_draft) is not WorkflowReferencedDraft
                or self.merged_draft.outline_id != outline_id
                or self.merged_draft.section_ids != section_ids
                or type(self.merged_draft.attempt) is not int
                or self.merged_draft.attempt != 1
                or self.referenced_draft.outline_id != outline_id
                or self.referenced_draft.section_ids != section_ids
                or self.referenced_draft.reference_source_ids != global_ids
                or type(self.referenced_draft.attempt) is not int
                or self.referenced_draft.attempt != 1
                or not self.referenced_draft.content.startswith(
                    self.merged_draft.content + _REFERENCES_PREFIX
                )
            ):
                raise ValueError("academic draft composition ready binding is invalid")
            cap = _READY_MAX_BYTES
        else:
            if self.merged_draft is not None or self.referenced_draft is not None:
                raise ValueError("academic draft composition non-ready shape is invalid")
            cap = _NON_READY_MAX_BYTES
        dumped = _BaseModel.model_dump(self, mode="json")
        encoded = _canonical_bytes(dumped)
        if type(encoded) is not bytes or len(encoded) > cap:
            raise ValueError("academic draft composition canonical payload is too large")
        return self


def _snapshot_state(value: object) -> AcademicWorkflowState | _Marker:
    if type(value) is not AcademicWorkflowState:
        return _FAILURE
    try:
        dumped = AcademicWorkflowState.model_dump(value, mode="json")
        if type(dumped) is not dict or not _validate_json_value(dumped):
            return _FAILURE
        encoded = _canonical_bytes(dumped)
        if type(encoded) is not bytes:
            return _FAILURE
        restored = AcademicWorkflowState.model_validate_json(encoded)
        if type(restored) is not AcademicWorkflowState:
            return _FAILURE
        restored_dumped = AcademicWorkflowState.model_dump(restored, mode="json")
        restored_encoded = _canonical_bytes(restored_dumped)
        if (
            type(restored_dumped) is not dict
            or type(restored_encoded) is not bytes
            or not _same_json_shape(dumped, restored_dumped)
            or restored_encoded != encoded
            or restored.phase != "outline_approved"
            or restored.status != "completed"
        ):
            return _FAILURE
        return restored
    except Exception:
        return _FAILURE


def _create_production_section_writer_sequence() -> _SectionWriterSequence:
    from .section_writer_sequence import GPTResearcherSectionWriterSequence

    return GPTResearcherSectionWriterSequence()


def _create_production_citation_reviewer() -> _CitationReviewer:
    from .citation_reviewer import GPTResearcherCitationReviewerAdapter

    return GPTResearcherCitationReviewerAdapter()


def _extract_choices(value: object) -> tuple[object, object] | _Marker:
    if type(value) is not GPTResearcherAcademicDraftComposer:
        return _FAILURE
    try:
        namespace = object.__getattribute__(value, "__dict__")
        if type(namespace) is not dict:
            return _FAILURE
        keys = tuple(dict.keys(namespace))
        if any(type(key) is not str for key in keys):
            return _FAILURE
        if keys != (
            "_section_writer_sequence_choice",
            "_citation_reviewer_choice",
        ):
            return _FAILURE
        return (
            dict.__getitem__(namespace, "_section_writer_sequence_choice"),
            dict.__getitem__(namespace, "_citation_reviewer_choice"),
        )
    except _asyncio.CancelledError:
        raise
    except Exception:
        return _FAILURE


def _select_sequence(choice: object) -> object:
    if choice is _PRODUCTION_SEQUENCE:
        return _create_production_section_writer_sequence()
    return choice


def _select_reviewer(choice: object) -> object:
    if choice is _PRODUCTION_REVIEWER:
        return _create_production_citation_reviewer()
    return choice


async def _invoke_sequence(
    sequence: object,
    state: AcademicWorkflowState,
) -> object:
    operation: object | None = None
    try:
        method = object.__getattribute__(sequence, "write_sections")
        operation = method(state)
        del method
        del sequence
        del state
        result = await operation  # type: ignore[misc]
        del operation
        return result
    except _asyncio.CancelledError:
        operation = None
        sequence = None
        state = None  # type: ignore[assignment]
        del operation
        del sequence
        del state
        raise
    except Exception:
        return _FAILURE


async def _invoke_reviewer(
    reviewer: object,
    state: AcademicWorkflowState,
    drafts: tuple[WorkflowSectionDraft, ...],
    gate: WorkflowCitationEvidenceGateResult,
) -> object:
    operation: object | None = None
    try:
        method = object.__getattribute__(reviewer, "review_citations")
        operation = method(state, drafts, gate)
        del method
        del reviewer
        del state
        del drafts
        del gate
        result = await operation  # type: ignore[misc]
        del operation
        return result
    except _asyncio.CancelledError:
        operation = None
        reviewer = None
        state = None  # type: ignore[assignment]
        drafts = ()
        gate = None  # type: ignore[assignment]
        del operation
        del reviewer
        del state
        del drafts
        del gate
        raise
    except Exception:
        return _FAILURE


def _call_gate(
    state: AcademicWorkflowState,
    drafts: tuple[WorkflowSectionDraft, ...],
) -> WorkflowCitationEvidenceGateResult | _Marker:
    try:
        result = _gate_citation_evidence(state, drafts)
        return result if type(result) is WorkflowCitationEvidenceGateResult else _FAILURE
    except Exception:
        return _FAILURE


def _call_disposition(
    gate: WorkflowCitationEvidenceGateResult,
    reviews: tuple[WorkflowSectionCitationReview, ...],
) -> WorkflowCitationReviewDisposition | _Marker:
    try:
        result = _gate_citation_review_disposition(gate, reviews)
        return result if type(result) is WorkflowCitationReviewDisposition else _FAILURE
    except Exception:
        return _FAILURE


def _call_merge(
    state: AcademicWorkflowState,
    drafts: tuple[WorkflowSectionDraft, ...],
) -> WorkflowMergedDraft | _Marker:
    try:
        result = _merge_sections(state, drafts)
        return result if type(result) is WorkflowMergedDraft else _FAILURE
    except Exception:
        return _FAILURE


def _call_render(
    state: AcademicWorkflowState,
    merged: WorkflowMergedDraft,
    gate: WorkflowCitationEvidenceGateResult,
    disposition: WorkflowCitationReviewDisposition,
) -> WorkflowReferencedDraft | _Marker:
    try:
        result = _render_references(state, merged, gate, disposition)
        return result if type(result) is WorkflowReferencedDraft else _FAILURE
    except Exception:
        return _FAILURE


def _build_composition(
    drafts: tuple[WorkflowSectionDraft, ...],
    gate: WorkflowCitationEvidenceGateResult,
    reviews: tuple[WorkflowSectionCitationReview, ...],
    disposition: WorkflowCitationReviewDisposition,
    merged: WorkflowMergedDraft | None,
    referenced: WorkflowReferencedDraft | None,
) -> WorkflowAcademicDraftComposition | _Marker:
    try:
        value = WorkflowAcademicDraftComposition(
            drafts=drafts,
            gate_result=gate,
            reviews=reviews,
            disposition=disposition,
            merged_draft=merged,
            referenced_draft=referenced,
        )
        dumped = _BaseModel.model_dump(value, mode="json")
        encoded = _canonical_bytes(dumped)
        if type(encoded) is not bytes:
            return _FAILURE
        restored = WorkflowAcademicDraftComposition.model_validate_json(encoded)
        if type(restored) is not WorkflowAcademicDraftComposition:
            return _FAILURE
        restored_dumped = _BaseModel.model_dump(restored, mode="json")
        restored_encoded = _canonical_bytes(restored_dumped)
        if (
            type(restored_encoded) is not bytes
            or not _same_json_shape(dumped, restored_dumped)
            or restored_encoded != encoded
        ):
            return _FAILURE
        return restored
    except Exception:
        return _FAILURE


async def _execute_pipeline(
    state: AcademicWorkflowState,
    sequence_choice: object,
    reviewer_choice: object,
) -> WorkflowAcademicDraftComposition | _Marker:
    sequence: object | None = None
    reviewer: object | None = None
    operation: object | None = None
    raw_drafts: object | None = None
    drafts: tuple[WorkflowSectionDraft, ...] | None = None
    gate: WorkflowCitationEvidenceGateResult | None = None
    raw_reviews: object | None = None
    reviews: tuple[WorkflowSectionCitationReview, ...] | None = None
    disposition: WorkflowCitationReviewDisposition | None = None
    merged: WorkflowMergedDraft | None = None
    referenced: WorkflowReferencedDraft | None = None
    try:
        sequence = _select_sequence(sequence_choice)
        sequence_choice = None
        operation = _invoke_sequence(sequence, state)
        sequence = None
        raw_drafts = await operation
        operation = None
        if raw_drafts is _FAILURE:
            return _FAILURE
        trusted_drafts = _trusted_drafts(raw_drafts)
        raw_drafts = None
        if type(trusted_drafts) is not tuple:
            return _FAILURE
        drafts = trusted_drafts
        del trusted_drafts
        gate_result = _call_gate(state, drafts)
        if type(gate_result) is not WorkflowCitationEvidenceGateResult:
            return _FAILURE
        gate = gate_result
        del gate_result
        reviewer = _select_reviewer(reviewer_choice)
        reviewer_choice = None
        operation = _invoke_reviewer(reviewer, state, drafts, gate)
        reviewer = None
        raw_reviews = await operation
        operation = None
        if raw_reviews is _FAILURE:
            return _FAILURE
        trusted_reviews = _trusted_reviews(raw_reviews)
        raw_reviews = None
        if type(trusted_reviews) is not tuple:
            return _FAILURE
        reviews = trusted_reviews
        del trusted_reviews
        disposition_result = _call_disposition(gate, reviews)
        if type(disposition_result) is not WorkflowCitationReviewDisposition:
            return _FAILURE
        disposition = disposition_result
        del disposition_result
        if disposition.disposition != "ready":
            return _build_composition(
                drafts,
                gate,
                reviews,
                disposition,
                None,
                None,
            )
        merged_result = _call_merge(state, drafts)
        if type(merged_result) is not WorkflowMergedDraft:
            return _FAILURE
        merged = merged_result
        del merged_result
        referenced_result = _call_render(
            state,
            merged,
            gate,
            disposition,
        )
        state = None  # type: ignore[assignment]
        if type(referenced_result) is not WorkflowReferencedDraft:
            return _FAILURE
        referenced = referenced_result
        del referenced_result
        return _build_composition(
            drafts,
            gate,
            reviews,
            disposition,
            merged,
            referenced,
        )
    except _asyncio.CancelledError:
        state = None  # type: ignore[assignment]
        sequence_choice = None
        reviewer_choice = None
        sequence = None
        reviewer = None
        operation = None
        raw_drafts = None
        drafts = None
        gate = None
        raw_reviews = None
        reviews = None
        disposition = None
        merged = None
        referenced = None
        del state
        del sequence_choice
        del reviewer_choice
        del sequence
        del reviewer
        del operation
        del raw_drafts
        del drafts
        del gate
        del raw_reviews
        del reviews
        del disposition
        del merged
        del referenced
        raise
    except Exception:
        return _FAILURE


def _raise_failure() -> None:
    raise _AcademicDraftComposerError(_ERROR_TEXT)


class GPTResearcherAcademicDraftComposer:
    def __init__(
        self,
        *,
        section_writer_sequence: _SectionWriterSequence | None = None,
        citation_reviewer: _CitationReviewer | None = None,
    ) -> None:
        self._section_writer_sequence_choice = (
            _PRODUCTION_SEQUENCE
            if section_writer_sequence is None
            else section_writer_sequence
        )
        self._citation_reviewer_choice = (
            _PRODUCTION_REVIEWER if citation_reviewer is None else citation_reviewer
        )

    async def compose(
        self,
        state: AcademicWorkflowState,
    ) -> WorkflowAcademicDraftComposition:
        try:
            choices = _extract_choices(self)
        except _asyncio.CancelledError:
            del self
            del state
            raise
        del self
        if type(choices) is not tuple:
            del state
            del choices
            _raise_failure()
        sequence_choice = tuple.__getitem__(choices, 0)
        reviewer_choice = tuple.__getitem__(choices, 1)
        del choices
        trusted_state = _snapshot_state(state)
        del state
        if type(trusted_state) is not AcademicWorkflowState:
            del trusted_state
            del sequence_choice
            del reviewer_choice
            _raise_failure()
        operation = _execute_pipeline(
            trusted_state,
            sequence_choice,
            reviewer_choice,
        )
        del trusted_state
        del sequence_choice
        del reviewer_choice
        try:
            result = await operation
        except _asyncio.CancelledError:
            del operation
            raise
        del operation
        if type(result) is not WorkflowAcademicDraftComposition:
            del result
            _raise_failure()
        return result
