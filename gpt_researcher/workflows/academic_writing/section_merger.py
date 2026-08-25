"""Synchronous deterministic merger for approved academic section drafts."""

from __future__ import annotations

import json
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    ValidationInfo,
    field_validator,
    model_validator,
)

from .state import AcademicWorkflowState, WorkflowSectionDraft


__all__ = (
    "WorkflowMergedDraft",
    "merge_sections",
)


_QUERY_MAX_CHARS = 4096
_LANGUAGE_MAX_CHARS = 128
_QUESTION_MIN_COUNT = 1
_QUESTION_MAX_COUNT = 3
_QUESTION_MAX_CHARS = 512
_QUESTION_TOTAL_MAX_CHARS = 1024
_CONTEXT_MAX_COUNT = 8
_CONTEXT_MAX_CHARS = 4096
_CONTEXT_TOTAL_MAX_CHARS = 24576
_SOURCE_MAX_COUNT = 24
_SOURCE_TITLE_MAX_CHARS = 256
_USER_MESSAGE_MAX_CHARS = 65536
_SECTION_MAX_COUNT = 12
_MERGED_CONTENT_MAX_CHARS = 359538
_MERGER_ERROR_TEXT = "section merger failed"
_DRAFT_FIELDS = ("outline_id", "section_id", "attempt", "content")
_MERGED_FIELDS = ("outline_id", "section_ids", "attempt", "content")
_CITATION_PREFIX = "[[cite:"
_CITATION_SUFFIX = "]]"


class WorkflowMergedDraft(BaseModel):
    """Strict immutable deterministic merged-section projection."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    outline_id: Literal["outline:000001"]
    section_ids: tuple[str, ...]
    attempt: Literal[1]
    content: str

    @model_validator(mode="before")
    @classmethod
    def _require_exact_input(cls, value: object, info: ValidationInfo) -> object:
        if type(value) is cls:
            return value
        if type(value) is not dict:
            raise TypeError("merged draft must be an exact mapping")
        keys = tuple(value)
        if any(type(key) is not str for key in keys) or set(keys) != set(
            _MERGED_FIELDS
        ):
            raise TypeError("merged draft mapping keys must be exact")
        outline_id = dict.__getitem__(value, "outline_id")
        section_ids = dict.__getitem__(value, "section_ids")
        attempt = dict.__getitem__(value, "attempt")
        content = dict.__getitem__(value, "content")
        expected_collection_type = list if info.mode == "json" else tuple
        if (
            type(outline_id) is not str
            or type(section_ids) is not expected_collection_type
            or any(type(section_id) is not str for section_id in section_ids)
            or type(attempt) is not int
            or type(content) is not str
        ):
            raise TypeError("merged draft values must use exact types")
        if info.mode == "json":
            return {
                "outline_id": outline_id,
                "section_ids": tuple(section_ids),
                "attempt": attempt,
                "content": content,
            }
        return value

    @field_validator("section_ids")
    @classmethod
    def _validate_section_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not 1 <= len(value) <= _SECTION_MAX_COUNT:
            raise ValueError("merged draft section count is invalid")
        if any(not section_id.strip() for section_id in value):
            raise ValueError("merged draft section IDs must not be blank")
        if len(set(value)) != len(value):
            raise ValueError("merged draft section IDs must be unique")
        return value

    @field_validator("attempt")
    @classmethod
    def _validate_attempt(cls, value: Literal[1]) -> Literal[1]:
        if type(value) is not int or value != 1:
            raise ValueError("merged draft attempt must be the exact integer one")
        return 1

    @field_validator("content")
    @classmethod
    def _validate_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("merged draft content must not be blank")
        if len(value) > _MERGED_CONTENT_MAX_CHARS:
            raise ValueError("merged draft content is too long")
        return value


class _SectionMergerError(RuntimeError):
    pass


class _Marker:
    __slots__ = ()


_FAILURE = _Marker()


def _canonical_json(value: object) -> str | _Marker:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except Exception:
        return _FAILURE


def _validate_json_value(value: object) -> None:
    value_type = type(value)
    if value is None or value_type in (bool, int, str):
        return
    if value_type is list:
        for item in value:  # type: ignore[union-attr]
            _validate_json_value(item)
        return
    if value_type is dict:
        for key, item in value.items():  # type: ignore[union-attr]
            if type(key) is not str:
                raise TypeError("JSON object keys must be exact strings")
            _validate_json_value(item)
        return
    raise TypeError("value is outside the strict JSON domain")


def _same_json_shape(left: object, right: object) -> bool:
    if type(left) is not type(right):
        return False
    if type(left) is dict:
        if tuple(left) != tuple(right):  # type: ignore[arg-type]
            return False
        return all(
            _same_json_shape(left[key], right[key])  # type: ignore[index]
            for key in left  # type: ignore[union-attr]
        )
    if type(left) is list:
        return len(left) == len(right) and all(  # type: ignore[arg-type]
            _same_json_shape(before, after)
            for before, after in zip(left, right, strict=True)  # type: ignore[arg-type]
        )
    return left == right


def _restore_state(value: AcademicWorkflowState) -> AcademicWorkflowState | _Marker:
    try:
        dumped = value.model_dump(mode="json")
        _validate_json_value(dumped)
        text = _canonical_json(dumped)
        if type(text) is not str:
            return _FAILURE
        encoded = text.encode("utf-8")
        restored = AcademicWorkflowState.model_validate_json(encoded)
        restored_dumped = restored.model_dump(mode="json")
        _validate_json_value(restored_dumped)
        restored_text = _canonical_json(restored_dumped)
        if type(restored) is not AcademicWorkflowState or type(restored_text) is not str:
            return _FAILURE
        if not _same_json_shape(dumped, restored_dumped):
            return _FAILURE
        if value != restored or encoded != restored_text.encode("utf-8"):
            return _FAILURE
        return restored
    except Exception:
        return _FAILURE


def _project_context(evidence: object) -> list[str] | _Marker:
    try:
        projected: list[str] = []
        remaining = _CONTEXT_TOTAL_MAX_CHARS
        for block in evidence.context_blocks:  # type: ignore[attr-defined]
            if len(projected) == _CONTEXT_MAX_COUNT or remaining == 0:
                break
            take = min(len(block), _CONTEXT_MAX_CHARS, remaining)
            projected.append(block[:take])
            remaining -= take
        return projected
    except Exception:
        return _FAILURE


def _project_allowlist(
    state: AcademicWorkflowState,
    target_section_id: str,
) -> tuple[str, ...] | _Marker:
    try:
        request = state.request
        topic_plan = state.topic_plan
        evidence = state.research_evidence
        outline = state.outline
        if topic_plan is None or evidence is None or outline is None:
            return _FAILURE
        if request.report_type != "research_report":
            return _FAILURE
        if request.report_source != "web":
            return _FAILURE
        if topic_plan.research_topic != request.query:
            return _FAILURE
        if len(request.query) > _QUERY_MAX_CHARS:
            return _FAILURE
        if len(request.language) > _LANGUAGE_MAX_CHARS:
            return _FAILURE
        questions = topic_plan.research_questions
        if not _QUESTION_MIN_COUNT <= len(questions) <= _QUESTION_MAX_COUNT:
            return _FAILURE
        if any(len(question) > _QUESTION_MAX_CHARS for question in questions):
            return _FAILURE
        if sum(len(question) for question in questions) > _QUESTION_TOTAL_MAX_CHARS:
            return _FAILURE
        context_blocks = _project_context(evidence)
        if type(context_blocks) is not list:
            return _FAILURE
        projected_sources: list[dict[str, str]] = []
        payload: dict[str, object] = {
            "context_blocks": context_blocks,
            "evidence_sources": projected_sources,
            "language": request.language,
            "outline": {
                "outline_id": outline.outline_id,
                "sections": [
                    {
                        "brief": section.brief,
                        "order": section.order,
                        "section_id": section.section_id,
                        "title": section.title,
                    }
                    for section in outline.sections
                ],
                "title": outline.title,
            },
            "research_questions": list(questions),
            "root_topic": topic_plan.research_topic,
            "target_section_id": target_section_id,
        }
        allowed_source_ids: list[str] = []
        for source in evidence.sources[:_SOURCE_MAX_COUNT]:
            projected_sources.append(
                {
                    "source_id": source.source_id,
                    "title": source.title[:_SOURCE_TITLE_MAX_CHARS],
                    "url": source.url,
                }
            )
            tentative = _canonical_json(payload)
            if type(tentative) is not str:
                return _FAILURE
            if len(tentative) > _USER_MESSAGE_MAX_CHARS:
                projected_sources.pop()
                break
            allowed_source_ids.append(source.source_id)
        encoded = _canonical_json(payload)
        if type(encoded) is not str or len(encoded) > _USER_MESSAGE_MAX_CHARS:
            return _FAILURE
        return tuple(allowed_source_ids)
    except Exception:
        return _FAILURE


def _trusted_draft_values(
    value: object,
    expected_outline_id: str,
    expected_section_id: str,
) -> tuple[str, str, str] | _Marker:
    if type(value) is not WorkflowSectionDraft:
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
        keys = tuple(namespace)
        if any(type(key) is not str for key in keys) or keys != _DRAFT_FIELDS:
            return _FAILURE
        if any(type(field) is not str for field in fields_set):
            return _FAILURE
        if fields_set != set(_DRAFT_FIELDS):
            return _FAILURE
        outline_id = dict.__getitem__(namespace, "outline_id")
        section_id = dict.__getitem__(namespace, "section_id")
        attempt = dict.__getitem__(namespace, "attempt")
        content = dict.__getitem__(namespace, "content")
        if (
            type(outline_id) is not str
            or type(section_id) is not str
            or type(content) is not str
            or type(attempt) is not int
            or attempt != 1
        ):
            return _FAILURE
        copied_outline_id = outline_id
        copied_section_id = section_id
        copied_attempt = attempt
        copied_content = content
        del value
        del namespace
        del fields_set
        del extra
        del private
        del outline_id
        del section_id
        del attempt
        del content
        if (
            type(expected_outline_id) is not str
            or type(expected_section_id) is not str
            or copied_outline_id != expected_outline_id
            or copied_section_id != expected_section_id
        ):
            return _FAILURE
        trusted = WorkflowSectionDraft(
            outline_id=copied_outline_id,
            section_id=copied_section_id,
            attempt=copied_attempt,
            content=copied_content,
        )
        trusted_namespace = object.__getattribute__(trusted, "__dict__")
        if (
            type(trusted_namespace) is not dict
            or dict.__getitem__(trusted_namespace, "outline_id") != copied_outline_id
            or dict.__getitem__(trusted_namespace, "section_id") != copied_section_id
            or dict.__getitem__(trusted_namespace, "attempt") != copied_attempt
            or dict.__getitem__(trusted_namespace, "content") != copied_content
        ):
            return _FAILURE
        dumped = trusted.model_dump(mode="json")
        _validate_json_value(dumped)
        text = _canonical_json(dumped)
        if type(text) is not str:
            return _FAILURE
        encoded = text.encode("utf-8")
        restored = WorkflowSectionDraft.model_validate_json(encoded)
        restored_dumped = restored.model_dump(mode="json")
        _validate_json_value(restored_dumped)
        restored_text = _canonical_json(restored_dumped)
        if type(restored) is not WorkflowSectionDraft or type(restored_text) is not str:
            return _FAILURE
        if not _same_json_shape(dumped, restored_dumped):
            return _FAILURE
        if trusted != restored or encoded != restored_text.encode("utf-8"):
            return _FAILURE
        return copied_outline_id, copied_section_id, copied_content
    except Exception:
        return _FAILURE


def _scan_citations(content: str, allowed_ids: tuple[str, ...]) -> tuple[str, ...] | _Marker:
    try:
        if "://" in content:
            return _FAILURE
        allowed = set(allowed_ids)
        seen: set[str] = set()
        ordered: list[str] = []
        position = 0
        while position < len(content):
            if content.startswith(_CITATION_PREFIX, position):
                end = content.find(_CITATION_SUFFIX, position + len(_CITATION_PREFIX))
                if end < 0:
                    return _FAILURE
                source_id = content[position + len(_CITATION_PREFIX) : end]
                if not source_id or "[" in source_id or "]" in source_id:
                    return _FAILURE
                if source_id not in allowed:
                    return _FAILURE
                if source_id not in seen:
                    seen.add(source_id)
                    ordered.append(source_id)
                position = end + len(_CITATION_SUFFIX)
                continue
            if content[position] in "[]":
                return _FAILURE
            position += 1
        return tuple(ordered)
    except Exception:
        return _FAILURE


def _prepare_merge(
    state: object,
    drafts: object,
) -> tuple[str, tuple[tuple[str, str, str], ...]] | _Marker:
    if type(state) is not AcademicWorkflowState or type(drafts) is not tuple:
        return _FAILURE
    try:
        restored = _restore_state(state)
        if type(restored) is not AcademicWorkflowState:
            return _FAILURE
        if restored.phase != "outline_approved" or restored.status != "completed":
            return _FAILURE
        outline = restored.outline
        if outline is None:
            return _FAILURE
        sections = outline.sections
        if not 1 <= len(sections) <= _SECTION_MAX_COUNT:
            return _FAILURE
        if len(drafts) != len(sections):
            return _FAILURE
        outline_id = outline.outline_id
        if type(outline_id) is not str:
            return _FAILURE
        expected_ids: list[str] = []
        seen_ids: set[str] = set()
        for section in sections:
            section_id = section.section_id
            if (
                type(section_id) is not str
                or not section_id.strip()
                or section_id in seen_ids
            ):
                return _FAILURE
            seen_ids.add(section_id)
            expected_ids.append(section_id)
        plan_items: list[tuple[str, str, str]] = []
        for index in range(len(sections)):
            section = sections[index]
            section_id = expected_ids[index]
            values = _trusted_draft_values(
                tuple.__getitem__(drafts, index),
                outline_id,
                section_id,
            )
            if type(values) is not tuple or len(values) != 3:
                return _FAILURE
            copied_outline_id, copied_section_id, copied_content = values
            if (
                type(copied_outline_id) is not str
                or type(copied_section_id) is not str
                or type(copied_content) is not str
                or copied_outline_id != outline_id
                or copied_section_id != section_id
            ):
                return _FAILURE
            allowed_ids = _project_allowlist(restored, section_id)
            if type(allowed_ids) is not tuple:
                return _FAILURE
            citations = _scan_citations(copied_content, allowed_ids)
            if type(citations) is not tuple:
                return _FAILURE
            title = section.title
            if type(title) is not str:
                return _FAILURE
            plan_items.append((copied_section_id, title, copied_content))
        planned_ids = tuple(item[0] for item in plan_items)
        if planned_ids != tuple(expected_ids) or len(set(planned_ids)) != len(
            planned_ids
        ):
            return _FAILURE
        return outline_id, tuple(plan_items)
    except Exception:
        return _FAILURE


def _build_merged_bytes(
    plan: tuple[str, tuple[tuple[str, str, str], ...]],
) -> bytes | _Marker:
    try:
        outline_id, items = plan
        section_strings = [
            "## " + title + "\n\n" + content for _, title, content in items
        ]
        merged_content = "\n\n".join(section_strings)
        expected_length = (
            sum(len(title) for _, title, _ in items)
            + sum(len(content) for _, _, content in items)
            + 7 * len(items)
            - 2
        )
        if (
            len(merged_content) != expected_length
            or len(merged_content) > _MERGED_CONTENT_MAX_CHARS
        ):
            return _FAILURE
        section_ids = tuple(section_id for section_id, _, _ in items)
        merged = WorkflowMergedDraft(
            outline_id=outline_id,
            section_ids=section_ids,
            attempt=1,
            content=merged_content,
        )
        dumped = merged.model_dump(mode="json")
        _validate_json_value(dumped)
        text = _canonical_json(dumped)
        if type(text) is not str:
            return _FAILURE
        encoded = text.encode("utf-8")
        restored = WorkflowMergedDraft.model_validate_json(encoded)
        restored_dumped = restored.model_dump(mode="json")
        _validate_json_value(restored_dumped)
        restored_text = _canonical_json(restored_dumped)
        if type(restored) is not WorkflowMergedDraft or type(restored_text) is not str:
            return _FAILURE
        if not _same_json_shape(dumped, restored_dumped):
            return _FAILURE
        if merged != restored or encoded != restored_text.encode("utf-8"):
            return _FAILURE
        return encoded
    except Exception:
        return _FAILURE


def _restore_merged(value: bytes) -> WorkflowMergedDraft | _Marker:
    try:
        restored = WorkflowMergedDraft.model_validate_json(value)
        if type(restored) is not WorkflowMergedDraft:
            return _FAILURE
        dumped = restored.model_dump(mode="json")
        _validate_json_value(dumped)
        text = _canonical_json(dumped)
        if type(text) is not str or value != text.encode("utf-8"):
            return _FAILURE
        return restored
    except Exception:
        return _FAILURE


def _raise_fixed_error() -> None:
    raise _SectionMergerError(_MERGER_ERROR_TEXT)


def merge_sections(
    state: AcademicWorkflowState,
    drafts: tuple[WorkflowSectionDraft, ...],
) -> WorkflowMergedDraft:
    plan = _prepare_merge(state, drafts)
    del state
    del drafts
    if type(plan) is not tuple:
        del plan
        _raise_fixed_error()
    encoded = _build_merged_bytes(plan)
    del plan
    if type(encoded) is not bytes:
        del encoded
        _raise_fixed_error()
    result = _restore_merged(encoded)
    del encoded
    if type(result) is not WorkflowMergedDraft:
        del result
        _raise_fixed_error()
    return result
