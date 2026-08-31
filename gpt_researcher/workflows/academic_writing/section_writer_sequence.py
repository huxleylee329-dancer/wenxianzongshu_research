"""Strict, off-graph sequential section-writer orchestration."""

from __future__ import annotations

import asyncio
import json
from typing import Protocol

from .state import AcademicWorkflowState, WorkflowSectionDraft


__all__ = (
    "GPTResearcherSectionWriterSequence",
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

_STATE_TYPE_ERROR = (
    "academic section writer sequence state must be an exact AcademicWorkflowState"
)
_STATE_SHAPE_ERROR = (
    "academic section writer sequence requires outline_approved/completed state"
)
_SECTION_COUNT_ERROR = (
    "academic section writer sequence requires between 1 and 12 outline sections"
)
_INPUT_ERROR = (
    "academic section writer sequence input is not accepted by the single-section contract"
)
_SEQUENCE_ERROR = "section writer sequence failed"


class _SectionWriter(Protocol):
    async def write_section(
        self,
        state: AcademicWorkflowState,
        section_id: str,
    ) -> WorkflowSectionDraft: ...


class _SectionWriterSequenceError(RuntimeError):
    pass


class _Marker:
    pass


_PRODUCTION_WRITER = _Marker()
_STATE_TYPE_FAILURE = _Marker()
_STATE_SHAPE_FAILURE = _Marker()
_SECTION_COUNT_FAILURE = _Marker()
_INPUT_FAILURE = _Marker()
_CONTRACT_FAILURE = _Marker()
_SEQUENCE_FAILURE = _Marker()


def _create_production_section_writer() -> _SectionWriter:
    from .section_writer import GPTResearcherSectionWriterAdapter

    return GPTResearcherSectionWriterAdapter()


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
        return _CONTRACT_FAILURE


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


def _snapshot_state(
    state: AcademicWorkflowState,
) -> tuple[AcademicWorkflowState, bytes] | _Marker:
    try:
        dumped = state.model_dump(mode="json")
        _validate_json_value(dumped)
        text = _canonical_json(dumped)
        if type(text) is not str:
            return _CONTRACT_FAILURE
        encoded = text.encode("utf-8")
        restored = AcademicWorkflowState.model_validate_json(encoded)
        restored_dumped = restored.model_dump(mode="json")
        _validate_json_value(restored_dumped)
        restored_text = _canonical_json(restored_dumped)
        if type(restored) is not AcademicWorkflowState:
            return _CONTRACT_FAILURE
        if type(restored_text) is not str:
            return _CONTRACT_FAILURE
        if not _same_json_shape(dumped, restored_dumped):
            return _CONTRACT_FAILURE
        if state != restored or encoded != restored_text.encode("utf-8"):
            return _CONTRACT_FAILURE
        return restored, encoded
    except Exception:
        return _CONTRACT_FAILURE


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
        return _CONTRACT_FAILURE


def _target_is_accepted(
    state: AcademicWorkflowState,
    section_id: str,
) -> bool:
    try:
        request = state.request
        topic_plan = state.topic_plan
        evidence = state.research_evidence
        outline = state.outline
        if topic_plan is None or evidence is None or outline is None:
            return False
        target = None
        for candidate in outline.sections:
            if candidate.section_id == section_id:
                if target is not None:
                    return False
                target = candidate
        if target is None:
            return False
        if request.report_type != "research_report":
            return False
        if request.report_source != "web":
            return False
        if topic_plan.research_topic != request.query:
            return False
        if len(request.query) > _QUERY_MAX_CHARS:
            return False
        if len(request.language) > _LANGUAGE_MAX_CHARS:
            return False
        questions = topic_plan.research_questions
        if not _QUESTION_MIN_COUNT <= len(questions) <= _QUESTION_MAX_COUNT:
            return False
        if any(len(question) > _QUESTION_MAX_CHARS for question in questions):
            return False
        if sum(len(question) for question in questions) > _QUESTION_TOTAL_MAX_CHARS:
            return False
        context_blocks = _project_context(evidence)
        if type(context_blocks) is not list:
            return False
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
            "target_section_id": target.section_id,
        }
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
                return False
            if len(tentative) > _USER_MESSAGE_MAX_CHARS:
                projected_sources.pop()
                break
        encoded = _canonical_json(payload)
        return type(encoded) is str and len(encoded) <= _USER_MESSAGE_MAX_CHARS
    except Exception:
        return False


def _prepare_sequence(
    state: object,
) -> tuple[bytes, str, tuple[str, ...]] | _Marker:
    if type(state) is not AcademicWorkflowState:
        return _STATE_TYPE_FAILURE
    try:
        snapshot = _snapshot_state(state)
        if type(snapshot) is not tuple or len(snapshot) != 2:
            return _CONTRACT_FAILURE
        restored, state_bytes = snapshot
        if type(restored) is not AcademicWorkflowState or type(state_bytes) is not bytes:
            return _CONTRACT_FAILURE
        if restored.phase != "outline_approved" or restored.status != "completed":
            return _STATE_SHAPE_FAILURE
        outline = restored.outline
        if outline is None:
            return _CONTRACT_FAILURE
        sections = outline.sections
        if not 1 <= len(sections) <= 12:
            return _SECTION_COUNT_FAILURE
        section_ids: list[str] = []
        seen: set[str] = set()
        for section in sections:
            section_id = section.section_id
            if (
                type(section_id) is not str
                or not section_id.strip()
                or section_id in seen
            ):
                return _INPUT_FAILURE
            seen.add(section_id)
            section_ids.append(section_id)
        for section_id in section_ids:
            if not _target_is_accepted(restored, section_id):
                return _INPUT_FAILURE
        outline_id = outline.outline_id
        if type(outline_id) is not str:
            return _CONTRACT_FAILURE
        return state_bytes, outline_id, tuple(section_ids)
    except Exception:
        return _CONTRACT_FAILURE


def _extract_writer_choice(instance: object) -> object | _Marker:
    try:
        if type(instance) is not GPTResearcherSectionWriterSequence:
            return _CONTRACT_FAILURE
        namespace = object.__getattribute__(instance, "__dict__")
        if type(namespace) is not dict:
            return _CONTRACT_FAILURE
        keys = tuple(namespace)
        if keys != ("_section_writer_choice",) or type(keys[0]) is not str:
            return _CONTRACT_FAILURE
        return dict.__getitem__(namespace, "_section_writer_choice")
    except Exception:
        return _CONTRACT_FAILURE


def _select_writer(choice: object) -> object:
    if choice is _PRODUCTION_WRITER:
        try:
            writer = _create_production_section_writer()
        except asyncio.CancelledError:
            del choice
            raise
        del choice
        return writer
    return choice


def _select_writer_safely(choice: object) -> object | _Marker:
    try:
        writer = _select_writer(choice)
    except asyncio.CancelledError:
        del choice
        raise
    except Exception:
        return _SEQUENCE_FAILURE
    del choice
    return writer


async def _invoke_writer(
    writer: object,
    state_bytes: bytes,
    section_id: str,
) -> object | _Marker:
    operation: object | None = None
    restored: object | None = None
    try:
        restored = AcademicWorkflowState.model_validate_json(state_bytes)
        if type(restored) is not AcademicWorkflowState:
            return _SEQUENCE_FAILURE
        operation = writer.write_section(restored, section_id)  # type: ignore[attr-defined]
        writer = None
        restored = None
        state_bytes = b""
        section_id = ""
        result = await operation  # type: ignore[misc]
    except asyncio.CancelledError:
        del operation
        del writer
        del state_bytes
        del section_id
        del restored
        raise
    except Exception:
        return _SEQUENCE_FAILURE
    del operation
    return result


def _trusted_draft_bytes(
    result: object,
    expected_outline_id: str,
    expected_section_id: str,
) -> bytes | _Marker:
    if type(result) is not WorkflowSectionDraft:
        return _SEQUENCE_FAILURE
    try:
        namespace = object.__getattribute__(result, "__dict__")
        fields_set = object.__getattribute__(result, "__pydantic_fields_set__")
        extra = object.__getattribute__(result, "__pydantic_extra__")
        private = object.__getattribute__(result, "__pydantic_private__")
        if type(namespace) is not dict or type(fields_set) is not set:
            return _SEQUENCE_FAILURE
        if extra is not None or private is not None:
            return _SEQUENCE_FAILURE
        keys = tuple(namespace)
        expected_keys = ("outline_id", "section_id", "attempt", "content")
        if any(type(key) is not str for key in keys) or keys != expected_keys:
            return _SEQUENCE_FAILURE
        if any(type(field) is not str for field in fields_set):
            return _SEQUENCE_FAILURE
        if fields_set != set(expected_keys):
            return _SEQUENCE_FAILURE
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
            return _SEQUENCE_FAILURE
        copied_outline_id = outline_id
        copied_section_id = section_id
        copied_attempt = attempt
        copied_content = content
        del result
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
            return _SEQUENCE_FAILURE
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
            return _SEQUENCE_FAILURE
        dumped = trusted.model_dump(mode="json")
        _validate_json_value(dumped)
        text = _canonical_json(dumped)
        if type(text) is not str:
            return _SEQUENCE_FAILURE
        encoded = text.encode("utf-8")
        restored = WorkflowSectionDraft.model_validate_json(encoded)
        restored_dumped = restored.model_dump(mode="json")
        _validate_json_value(restored_dumped)
        restored_text = _canonical_json(restored_dumped)
        if type(restored) is not WorkflowSectionDraft or type(restored_text) is not str:
            return _SEQUENCE_FAILURE
        if not _same_json_shape(dumped, restored_dumped):
            return _SEQUENCE_FAILURE
        if trusted != restored or encoded != restored_text.encode("utf-8"):
            return _SEQUENCE_FAILURE
        return encoded
    except Exception:
        return _SEQUENCE_FAILURE


def _restore_drafts(values: list[bytes]) -> tuple[WorkflowSectionDraft, ...] | _Marker:
    try:
        drafts = tuple(WorkflowSectionDraft.model_validate_json(value) for value in values)
        if any(type(draft) is not WorkflowSectionDraft for draft in drafts):
            return _SEQUENCE_FAILURE
        return drafts
    except Exception:
        return _SEQUENCE_FAILURE


async def _run_sequence(
    plan: tuple[bytes, str, tuple[str, ...]],
    choice: object,
) -> tuple[WorkflowSectionDraft, ...] | _Marker:
    writer: object | None = None
    draft_bytes: list[bytes] = []
    try:
        state_bytes, outline_id, section_ids = plan
        del plan
        try:
            writer = _select_writer_safely(choice)
        except asyncio.CancelledError:
            del choice
            del state_bytes
            del outline_id
            del section_ids
            del writer
            del draft_bytes
            raise
        del choice
        if writer is _SEQUENCE_FAILURE:
            del state_bytes
            del outline_id
            del section_ids
            del writer
            return _SEQUENCE_FAILURE
        for current_id in section_ids:
            try:
                result = await _invoke_writer(writer, state_bytes, current_id)
            except asyncio.CancelledError:
                del writer
                del state_bytes
                del outline_id
                del section_ids
                del current_id
                del draft_bytes
                raise
            if result is _SEQUENCE_FAILURE:
                del result
                del writer
                del state_bytes
                del outline_id
                del section_ids
                del current_id
                del draft_bytes
                return _SEQUENCE_FAILURE
            encoded = _trusted_draft_bytes(result, outline_id, current_id)
            del result
            if type(encoded) is not bytes:
                del encoded
                del writer
                del state_bytes
                del outline_id
                del section_ids
                del current_id
                del draft_bytes
                return _SEQUENCE_FAILURE
            draft_bytes.append(encoded)
            del encoded
        del writer
        del state_bytes
        del outline_id
        del section_ids
        del current_id
        restored = _restore_drafts(draft_bytes)
        del draft_bytes
        return restored
    except Exception:
        return _SEQUENCE_FAILURE


def _finish_sequence(result: object) -> tuple[WorkflowSectionDraft, ...]:
    if result is _STATE_TYPE_FAILURE:
        raise TypeError(_STATE_TYPE_ERROR)
    if result is _STATE_SHAPE_FAILURE:
        raise ValueError(_STATE_SHAPE_ERROR)
    if result is _SECTION_COUNT_FAILURE:
        raise ValueError(_SECTION_COUNT_ERROR)
    if result is _INPUT_FAILURE:
        raise ValueError(_INPUT_ERROR)
    if result is _CONTRACT_FAILURE or result is _SEQUENCE_FAILURE:
        raise _SectionWriterSequenceError(_SEQUENCE_ERROR)
    if type(result) is not tuple or any(
        type(item) is not WorkflowSectionDraft for item in result
    ):
        raise _SectionWriterSequenceError(_SEQUENCE_ERROR)
    return result


class GPTResearcherSectionWriterSequence:
    def __init__(
        self,
        *,
        section_writer: _SectionWriter | None = None,
    ) -> None:
        self._section_writer_choice = (
            _PRODUCTION_WRITER if section_writer is None else section_writer
        )

    async def write_sections(
        self,
        state: AcademicWorkflowState,
    ) -> tuple[WorkflowSectionDraft, ...]:
        choice = _extract_writer_choice(self)
        del self
        if choice is _CONTRACT_FAILURE:
            del state
            del choice
            return _finish_sequence(_CONTRACT_FAILURE)
        plan = _prepare_sequence(state)
        del state
        if type(plan) is not tuple:
            del choice
            result = plan
            del plan
            return _finish_sequence(result)
        operation = _run_sequence(plan, choice)
        del plan
        del choice
        try:
            result = await operation
        except asyncio.CancelledError:
            del operation
            raise
        except Exception:
            result = _SEQUENCE_FAILURE
        del operation
        return _finish_sequence(result)
