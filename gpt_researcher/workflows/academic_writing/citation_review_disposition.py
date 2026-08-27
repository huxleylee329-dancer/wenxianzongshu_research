"""Deterministic routing for structurally validated citation-review opinions."""

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
from gpt_researcher.workflows.academic_writing.citation_reviewer import (
    WorkflowSectionCitationReview,
)


__all__ = (
    "WorkflowCitationReviewDisposition",
    "gate_citation_review_disposition",
)


_Disposition = _Literal["ready", "needs_human_review", "blocked"]

_ERROR_TEXT = "citation review disposition failed"
_SECTION_MAX_COUNT = 12
_CITATION_MAX_COUNT = 64
_SOURCE_MAX_ORDER = 200
_RATIONALE_MAX_CHARS = 2048
_GATE_MAX_BYTES = 19519
_REVIEW_MAX_BYTES = 14110
_RESULT_MAX_BYTES = 575
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
_RESULT_FIELDS = (
    "outline_id",
    "section_ids",
    "section_dispositions",
    "disposition",
    "attempt",
)
_ISSUE_ORDER = (
    "insufficient_evidence",
    "possible_contradiction",
    "citation_placement_unclear",
)


class _CitationReviewDispositionError(RuntimeError):
    pass


class _Marker:
    pass


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


def _aggregate_dispositions(values: tuple[str, ...]) -> str:
    aggregate = "ready"
    index = 0
    while index < tuple.__len__(values):
        value = tuple.__getitem__(values, index)
        if value == "blocked":
            return "blocked"
        if value == "needs_human_review":
            aggregate = "needs_human_review"
        index += 1
    return aggregate


class WorkflowCitationReviewDisposition(_BaseModel):
    """Strict immutable projection of mechanical review-routing labels."""

    model_config = _ConfigDict(frozen=True, extra="forbid", strict=True)

    outline_id: _Literal["outline:000001"]
    section_ids: tuple[str, ...]
    section_dispositions: tuple[_Disposition, ...]
    disposition: _Disposition
    attempt: _Literal[1]

    @_model_validator(mode="before")
    @classmethod
    def _require_exact_input(cls, value: object, info: _ValidationInfo) -> object:
        if type(value) is cls:
            return value
        if type(value) is not dict:
            raise TypeError("citation disposition must be an exact mapping")
        keys = tuple(dict.keys(value))
        if any(type(key) is not str for key in keys) or set(keys) != set(
            _RESULT_FIELDS
        ):
            raise TypeError("citation disposition fields must be exact")
        outline_id = dict.__getitem__(value, "outline_id")
        section_ids_value = dict.__getitem__(value, "section_ids")
        dispositions_value = dict.__getitem__(value, "section_dispositions")
        disposition = dict.__getitem__(value, "disposition")
        attempt = dict.__getitem__(value, "attempt")
        expected_type = list if info.mode == "json" else tuple
        section_ids = _copy_container_strings(section_ids_value, expected_type)
        dispositions = _copy_container_strings(dispositions_value, expected_type)
        if (
            type(outline_id) is not str
            or type(disposition) is not str
            or type(attempt) is not int
            or type(section_ids) is not tuple
            or type(dispositions) is not tuple
        ):
            raise TypeError("citation disposition values must use exact types")
        if info.mode == "json":
            return {
                "outline_id": outline_id,
                "section_ids": section_ids,
                "section_dispositions": dispositions,
                "disposition": disposition,
                "attempt": attempt,
            }
        return value

    @_field_validator("section_ids")
    @classmethod
    def _validate_section_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if not 1 <= tuple.__len__(values) <= _SECTION_MAX_COUNT:
            raise ValueError("citation disposition section count is invalid")
        index = 0
        while index < tuple.__len__(values):
            if tuple.__getitem__(values, index) != f"section:{index + 1:06d}":
                raise ValueError("citation disposition section ID is invalid")
            index += 1
        return values

    @_model_validator(mode="after")
    def _validate_binding(self) -> WorkflowCitationReviewDisposition:
        if type(self.attempt) is not int or self.attempt != 1:
            raise ValueError("citation disposition attempt is invalid")
        if (
            tuple.__len__(self.section_dispositions)
            != tuple.__len__(self.section_ids)
            or _aggregate_dispositions(self.section_dispositions) != self.disposition
        ):
            raise ValueError("citation disposition binding is invalid")
        return self


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
        if extra is not None or private is not None:
            return _FAILURE
        keys = tuple(dict.keys(namespace))
        members = tuple(set.__iter__(fields_set))
        if any(type(key) is not str for key in keys):
            return _FAILURE
        if any(type(member) is not str for member in members):
            return _FAILURE
        if keys != fields or fields_set != set(fields):
            return _FAILURE
        return namespace
    except Exception:
        return _FAILURE


def _copy_string_tuple(value: object) -> tuple[str, ...] | _Marker:
    return _copy_container_strings(value, tuple)


def _valid_section_id(value: object, index: int) -> bool:
    return type(value) is str and value == f"section:{index + 1:06d}"


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
    return 1 <= order <= _SOURCE_MAX_ORDER and value == f"{prefix}{order:06d}"


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


def _restore_gate_mapping(
    mapping: dict[str, object],
) -> bool:
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
        return type(rerestored) is WorkflowCitationEvidenceGateResult and rerestored == restored
    except Exception:
        return False


def _extract_gate(
    value: object,
) -> tuple[str, tuple[str, ...], tuple[tuple[str, ...], ...], int] | _Marker:
    namespace = _surface(value, WorkflowCitationEvidenceGateResult, _GATE_FIELDS)
    if type(namespace) is not dict:
        return _FAILURE
    try:
        outline_id = dict.__getitem__(namespace, "outline_id")
        section_ids = _copy_string_tuple(dict.__getitem__(namespace, "section_ids"))
        citations_value = dict.__getitem__(namespace, "cited_source_ids_by_section")
        attempt = dict.__getitem__(namespace, "attempt")
        if (
            type(outline_id) is not str
            or outline_id != "outline:000001"
            or type(section_ids) is not tuple
            or type(citations_value) is not tuple
            or type(attempt) is not int
            or attempt != 1
            or not 1 <= tuple.__len__(section_ids) <= _SECTION_MAX_COUNT
            or tuple.__len__(citations_value) != tuple.__len__(section_ids)
        ):
            return _FAILURE
        citations: list[tuple[str, ...]] = []
        index = 0
        while index < tuple.__len__(section_ids):
            section_id = tuple.__getitem__(section_ids, index)
            inner = _copy_string_tuple(tuple.__getitem__(citations_value, index))
            if (
                not _valid_section_id(section_id, index)
                or type(inner) is not tuple
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
            citations.append(inner)
            index += 1
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


def _valid_issues(values: tuple[str, ...]) -> bool:
    expected: list[str] = []
    index = 0
    while index < tuple.__len__(_ISSUE_ORDER):
        issue = tuple.__getitem__(_ISSUE_ORDER, index)
        if issue in values:
            expected.append(issue)
        index += 1
    return values == tuple(expected)


def _coherent(verdict: str, issues: tuple[str, ...]) -> bool:
    if verdict == "supported":
        return tuple.__len__(issues) == 0
    if verdict == "unsupported":
        return (
            "insufficient_evidence" in issues
            or "possible_contradiction" in issues
        )
    return verdict == "uncertain" and tuple.__len__(issues) > 0


def _restore_review_mapping(mapping: dict[str, object]) -> bool:
    try:
        encoded = _canonical_bytes(mapping)
        if type(encoded) is not bytes or len(encoded) > _REVIEW_MAX_BYTES:
            return False
        restored = WorkflowSectionCitationReview.model_validate_json(encoded)
        if type(restored) is not WorkflowSectionCitationReview:
            return False
        dumped = restored.model_dump(mode="json")
        restored_encoded = _canonical_bytes(dumped)
        if (
            type(restored_encoded) is not bytes
            or not _same_json_shape(mapping, dumped)
            or restored_encoded != encoded
        ):
            return False
        rerestored = WorkflowSectionCitationReview.model_validate_json(
            restored_encoded
        )
        return type(rerestored) is WorkflowSectionCitationReview and rerestored == restored
    except Exception:
        return False


def _extract_review(
    value: object,
) -> tuple[str, str, tuple[str, ...], str, int] | _Marker:
    namespace = _surface(value, WorkflowSectionCitationReview, _REVIEW_FIELDS)
    if type(namespace) is not dict:
        return _FAILURE
    try:
        outline_id = dict.__getitem__(namespace, "outline_id")
        section_id = dict.__getitem__(namespace, "section_id")
        cited_ids = _copy_string_tuple(dict.__getitem__(namespace, "cited_source_ids"))
        verdict = dict.__getitem__(namespace, "verdict")
        issues = _copy_string_tuple(dict.__getitem__(namespace, "issues"))
        rationale = dict.__getitem__(namespace, "rationale")
        attempt = dict.__getitem__(namespace, "attempt")
        if (
            type(outline_id) is not str
            or outline_id != "outline:000001"
            or type(section_id) is not str
            or type(cited_ids) is not tuple
            or type(verdict) is not str
            or verdict not in ("supported", "unsupported", "uncertain")
            or type(issues) is not tuple
            or type(rationale) is not str
            or not str.strip(rationale)
            or len(rationale) > _RATIONALE_MAX_CHARS
            or type(attempt) is not int
            or attempt != 1
            or not _valid_issues(issues)
            or not _coherent(verdict, issues)
        ):
            return _FAILURE
        suffix = section_id[len("section:") :]
        if (
            not section_id.startswith("section:")
            or len(suffix) != 6
            or not suffix.isascii()
            or not suffix.isdigit()
        ):
            return _FAILURE
        section_order = int(suffix)
        if (
            not 1 <= section_order <= _SECTION_MAX_COUNT
            or section_id != f"section:{section_order:06d}"
            or not 1 <= tuple.__len__(cited_ids) <= _CITATION_MAX_COUNT
        ):
            return _FAILURE
        seen: set[str] = set()
        index = 0
        while index < tuple.__len__(cited_ids):
            source_id = tuple.__getitem__(cited_ids, index)
            if not _valid_source_id(source_id) or source_id in seen:
                return _FAILURE
            seen.add(source_id)
            index += 1
        mapping: dict[str, object] = {
            "outline_id": outline_id,
            "section_id": section_id,
            "cited_source_ids": list(cited_ids),
            "verdict": verdict,
            "issues": list(issues),
            "rationale": rationale,
            "attempt": attempt,
        }
        if not _restore_review_mapping(mapping):
            return _FAILURE
        return outline_id, section_id, cited_ids, verdict, attempt
    except Exception:
        return _FAILURE


def _prepare_result_plan(
    gate_result: object,
    reviews: object,
) -> tuple[str, tuple[str, ...], tuple[str, ...], str] | _Marker:
    try:
        gate = _extract_gate(gate_result)
        del gate_result
        if type(gate) is not tuple:
            del reviews
            return _FAILURE
        if type(reviews) is not tuple:
            del gate
            return _FAILURE
        outline_id = tuple.__getitem__(gate, 0)
        section_ids = tuple.__getitem__(gate, 1)
        citations = tuple.__getitem__(gate, 2)
        gate_attempt = tuple.__getitem__(gate, 3)
        if (
            type(outline_id) is not str
            or type(section_ids) is not tuple
            or type(citations) is not tuple
            or type(gate_attempt) is not int
            or tuple.__len__(reviews) != tuple.__len__(section_ids)
        ):
            del gate
            del reviews
            return _FAILURE
        dispositions: list[str] = []
        index = 0
        while index < tuple.__len__(section_ids):
            member = tuple.__getitem__(reviews, index)
            review = _extract_review(member)
            del member
            if type(review) is not tuple:
                dispositions.clear()
                del dispositions
                del gate
                del reviews
                return _FAILURE
            review_outline = tuple.__getitem__(review, 0)
            review_section = tuple.__getitem__(review, 1)
            review_citations = tuple.__getitem__(review, 2)
            verdict = tuple.__getitem__(review, 3)
            review_attempt = tuple.__getitem__(review, 4)
            if (
                type(review_outline) is not str
                or type(review_section) is not str
                or type(review_citations) is not tuple
                or type(verdict) is not str
                or type(review_attempt) is not int
                or review_outline != outline_id
                or review_section != tuple.__getitem__(section_ids, index)
                or review_citations != tuple.__getitem__(citations, index)
                or review_attempt != gate_attempt
            ):
                del review_outline
                del review_section
                del review_citations
                del verdict
                del review_attempt
                del review
                dispositions.clear()
                del dispositions
                del gate
                del reviews
                return _FAILURE
            if verdict == "supported":
                disposition = "ready"
            elif verdict == "uncertain":
                disposition = "needs_human_review"
            else:
                disposition = "blocked"
            dispositions.append(disposition)
            del disposition
            del review_outline
            del review_section
            del review_citations
            del verdict
            del review_attempt
            del review
            index += 1
        del reviews
        del citations
        del gate_attempt
        del gate
        section_dispositions = tuple(dispositions)
        aggregate = _aggregate_dispositions(section_dispositions)
        dispositions.clear()
        del dispositions
        return outline_id, section_ids, section_dispositions, aggregate
    except Exception:
        return _FAILURE


def _build_result_bytes(
    plan: tuple[str, tuple[str, ...], tuple[str, ...], str],
) -> bytes | _Marker:
    try:
        outline_id = tuple.__getitem__(plan, 0)
        section_ids = tuple.__getitem__(plan, 1)
        section_dispositions = tuple.__getitem__(plan, 2)
        disposition = tuple.__getitem__(plan, 3)
        result = WorkflowCitationReviewDisposition(
            outline_id=outline_id,
            section_ids=section_ids,
            section_dispositions=section_dispositions,
            disposition=disposition,
            attempt=1,
        )
        dumped = result.model_dump(mode="json")
        encoded = _canonical_bytes(dumped)
        if type(encoded) is not bytes or len(encoded) > _RESULT_MAX_BYTES:
            return _FAILURE
        restored = WorkflowCitationReviewDisposition.model_validate_json(encoded)
        if type(restored) is not WorkflowCitationReviewDisposition:
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


def _restore_result(value: object) -> WorkflowCitationReviewDisposition | _Marker:
    try:
        if type(value) is not bytes:
            return _FAILURE
        result = WorkflowCitationReviewDisposition.model_validate_json(value)
        if type(result) is not WorkflowCitationReviewDisposition:
            return _FAILURE
        dumped = result.model_dump(mode="json")
        encoded = _canonical_bytes(dumped)
        if type(encoded) is not bytes or encoded != value:
            return _FAILURE
        return result
    except Exception:
        return _FAILURE


def _raise_failure() -> None:
    raise _CitationReviewDispositionError(_ERROR_TEXT)


def gate_citation_review_disposition(
    gate_result: WorkflowCitationEvidenceGateResult,
    reviews: tuple[WorkflowSectionCitationReview, ...],
) -> WorkflowCitationReviewDisposition:
    plan = _prepare_result_plan(gate_result, reviews)
    del gate_result
    del reviews
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
    if type(result) is not WorkflowCitationReviewDisposition:
        del result
        _raise_failure()
    return result
