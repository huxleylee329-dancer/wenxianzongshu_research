"""Application service for the browser-facing academic LangGraph workflow."""

from __future__ import annotations

import logging
import asyncio
import json
import os
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol
from urllib.parse import unquote

from langgraph.checkpoint.memory import InMemorySaver

from gpt_researcher.workflows.academic_writing.academic_draft_composer import (
    GPTResearcherAcademicDraftComposer,
    WorkflowAcademicDraftComposition,
)
from gpt_researcher.workflows.academic_writing.citation_evidence_gate import (
    gate_citation_evidence,
)
from gpt_researcher.workflows.academic_writing.citation_review_disposition import (
    gate_citation_review_disposition,
)
from gpt_researcher.workflows.academic_writing.citation_reviewer import (
    WorkflowSectionCitationReview,
)
from gpt_researcher.workflows.academic_writing import citation_reviewer as _reviewer_core
from gpt_researcher.workflows.academic_writing.graph import (
    resume_academic_workflow,
    start_academic_workflow,
    submit_academic_outline_decision,
)
from gpt_researcher.workflows.academic_writing.nodes import _execution_view
from gpt_researcher.workflows.academic_writing.outline_writer import (
    GPTResearcherOutlineWriterAdapter,
)
from gpt_researcher.workflows.academic_writing.references_renderer import (
    render_references,
)
from gpt_researcher.workflows.academic_writing.research_evidence import (
    GPTResearcherResearchEvidenceAdapter,
)
from gpt_researcher.workflows.academic_writing.section_merger import merge_sections
from gpt_researcher.workflows.academic_writing.section_writer import (
    GPTResearcherSectionWriterAdapter,
    _create_production_section_writer_client,
    _extract_citations,
)
from gpt_researcher.workflows.academic_writing.section_writer_sequence import (
    GPTResearcherSectionWriterSequence,
)
from gpt_researcher.workflows.academic_writing.state import (
    AcademicOutlineDecisionCommand,
    AcademicWorkflowIdentity,
    AcademicWorkflowRequest,
    AcademicWorkflowState,
    WorkflowResearchEvidence,
    WorkflowSectionDraft,
    _outline_digest,
)
from gpt_researcher.workflows.academic_writing.topic_planner import (
    GPTResearcherTopicPlannerAdapter,
)
from gpt_researcher.workflows.academic_writing.workflow_outcome import (
    AcademicWorkflowPersistentState,
)
from gpt_researcher.utils.enum import Tone

logger = logging.getLogger(__name__)
_FIXED_PROFILES = frozenset(
    {
        "stem_literature_review",
        "technical_route_survey",
        "method_comparison",
        "equipment_material_selection",
        "proposal_research_status",
        "systematic_literature_review",
    }
)
_SESSION_ID = re.compile(r"^academic-[0-9a-f]{32}$")
_MARKER = re.compile(r"\[\[cite:([^\[\]]+)\]\]")
_MARKER_RUN = re.compile(
    r"\[\[cite:[^\[\]]+\]\](?:[ \t]*\[\[cite:[^\[\]]+\]\])*"
)
_DOI = re.compile(r"^10\.\d{4,9}/[-._;()/:A-Z0-9]+$", re.IGNORECASE)
_REVISION_MESSAGE_MAX_CHARS = 65_536
_REVISION_CONTEXT_MAX_COUNT = 8
_REVISION_CONTEXT_MAX_CHARS = 4_096
_REVISION_CONTEXT_TOTAL_MAX_CHARS = 24_576
_REVIEWER_CAVEAT = (
    "Reviewer output is a model opinion and may reflect bounded provenance projection."
)
_REVISION_SUFFIX = (
    " This is a single human-requested revision pass over existing_section_content. "
    "Use prior_review only to locate claims that need deletion, narrowing, or cautious "
    "restatement. Do not introduce new factual claims or new source IDs. Use only exact "
    "markers listed in original_section_citation_markers, and retain a marker only when "
    "the supplied evidence supports the retained text. Never fabricate or automatically "
    "insert a citation. Return only the normal production content JSON response."
)
_FEEDBACK_SUFFIX = (
    " Revise existing_section_content according to human_feedback.global and "
    "human_feedback.section. These are editorial requests, not factual evidence. "
    "You may reorganize, clarify, shorten, or expand explanations supported by the "
    "supplied evidence. If a request needs missing evidence, state that limitation; "
    "do not fabricate facts, sources, searches, or citations. Use prior_review as "
    "additional guidance, even if its verdict is supported. Use only markers in "
    "original_section_citation_markers. Return only the production content JSON."
)

SafeEmitter = Callable[[dict[str, object]], Awaitable[None]]


class AcademicWorkflowServiceError(RuntimeError):
    """A safe, fixed-code application error."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _Composer(Protocol):
    async def compose(
        self, state: AcademicWorkflowState
    ) -> WorkflowAcademicDraftComposition: ...


class _Reviewer(Protocol):
    async def review_citations(
        self,
        state: AcademicWorkflowState,
        drafts: tuple[WorkflowSectionDraft, ...],
        gate_result: object,
    ) -> tuple[WorkflowSectionCitationReview, ...]: ...


class _Sequence(Protocol):
    async def write_sections(
        self, state: AcademicWorkflowState
    ) -> tuple[WorkflowSectionDraft, ...]: ...


class _Writer(Protocol):
    async def write_section(
        self, state: AcademicWorkflowState, section_id: str
    ) -> WorkflowSectionDraft: ...


class _TerminalDelegateError(RuntimeError):
    pass


class _TerminalDelegate:
    async def plan_topic(self, request: object) -> object:
        raise _TerminalDelegateError("academic workflow terminal delegate invoked")

    async def collect_research_evidence(
        self, request: object, topic_plan: object
    ) -> object:
        raise _TerminalDelegateError("academic workflow terminal delegate invoked")

    async def write_outline(
        self, request: object, topic_plan: object, evidence: object
    ) -> object:
        raise _TerminalDelegateError("academic workflow terminal delegate invoked")


class _ProgressAdapter:
    def __init__(self, delegate: object, emit: SafeEmitter) -> None:
        self._delegate = delegate
        self._emit = emit

    async def plan_topic(self, request: AcademicWorkflowRequest) -> object:
        await self._emit(_progress("planning", "规划"))
        try:
            return await self._delegate.plan_topic(request)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "Academic topic planner adapter failed: exception_type=%s",
                type(exc).__name__,
            )
            raise

    async def collect_research_evidence(
        self, request: AcademicWorkflowRequest, topic_plan: object
    ) -> object:
        await self._emit(_progress("evidence", "证据检索"))
        return await self._delegate.collect_research_evidence(request, topic_plan)

    async def write_outline(
        self, request: AcademicWorkflowRequest, topic_plan: object, evidence: object
    ) -> object:
        await self._emit(_progress("outline", "目录生成"))
        return await self._delegate.write_outline(request, topic_plan, evidence)


class _RevisionClient:
    def __init__(
        self,
        inner: object,
        draft: WorkflowSectionDraft,
        review: WorkflowSectionCitationReview,
        original_ids: tuple[str, ...],
        evidence_blocks: tuple[str, ...],
        feedback: tuple[str, str] | None = None,
    ) -> None:
        self._inner = inner
        self._draft = draft
        self._review = review
        self._original_ids = original_ids
        self._evidence_blocks = evidence_blocks
        self._feedback = feedback

    async def complete(self, *, system_message: str, user_message: str) -> object:
        payload = json.loads(user_message)
        if type(payload) is not dict:
            raise AcademicWorkflowServiceError("revision_failed")
        payload["existing_section_content"] = self._draft.content
        payload["prior_review"] = self._review.model_dump(mode="json")
        if self._feedback is not None:
            payload["human_feedback"] = {
                "global": self._feedback[0], "section": self._feedback[1]
            }
        payload["original_section_citation_markers"] = [
            "[[cite:" + source_id + "]]" for source_id in self._original_ids
        ]
        evidence_sources = payload.get("evidence_sources")
        if type(evidence_sources) is not list:
            raise AcademicWorkflowServiceError("revision_failed")
        source_by_id = {
            source.get("source_id"): source
            for source in evidence_sources
            if type(source) is dict and type(source.get("source_id")) is str
        }
        if len(source_by_id) != len(evidence_sources):
            raise AcademicWorkflowServiceError("revision_failed")
        try:
            selected_sources = [
                source_by_id[source_id] for source_id in self._original_ids
            ]
        except KeyError:
            raise AcademicWorkflowServiceError("revision_failed") from None
        payload["evidence_sources"] = selected_sources
        payload["context_blocks"] = list(self._evidence_blocks)
        revised_message = _canonical_bytes(payload).decode("utf-8")
        if len(revised_message) > _REVISION_MESSAGE_MAX_CHARS:
            raise AcademicWorkflowServiceError("revision_failed")
        return await self._inner.complete(
            system_message=system_message + (
                _REVISION_SUFFIX if self._feedback is None else _FEEDBACK_SUFFIX
            ),
            user_message=revised_message,
        )


class _RevisionClientFactory:
    def __init__(
        self,
        draft: WorkflowSectionDraft,
        review: WorkflowSectionCitationReview,
        original_ids: tuple[str, ...],
        evidence_blocks: tuple[str, ...],
        feedback: tuple[str, str] | None = None,
    ) -> None:
        self._draft = draft
        self._review = review
        self._original_ids = original_ids
        self._evidence_blocks = evidence_blocks
        self._feedback = feedback

    def __call__(self) -> _RevisionClient:
        return _RevisionClient(
            _create_production_section_writer_client(),
            self._draft,
            self._review,
            self._original_ids,
            self._evidence_blocks,
            self._feedback,
        )


class _SafeRunRecorder:
    """Persist only fixed workflow stages and codes, never model data."""

    def __init__(self, session_id: str, output_dir: Path) -> None:
        self._session_id = session_id
        self._path = output_dir / "workflow-status.json"
        self._events: list[dict[str, object]] = []

    def record(
        self, stage: str, status: str, code: str | None = None,
        *, section_id: str | None = None,
    ) -> None:
        event: dict[str, object] = {
            "order": len(self._events) + 1,
            "stage": stage,
            "status": status,
        }
        if code is not None:
            event["code"] = code
        if section_id is not None:
            event["section_id"] = section_id
        self._events.append(event)
        _atomic_json(
            self._path,
            {
                "schema": "academic-web-run-status-v1",
                "session_id": self._session_id,
                "events": self._events,
            },
        )


class _ObservedSequence:
    def __init__(
        self, delegate: _Sequence, recorder: _SafeRunRecorder, output_dir: Path,
    ) -> None:
        self._delegate = delegate
        self._recorder = recorder
        self._output_dir = output_dir

    async def write_sections(
        self, state: AcademicWorkflowState
    ) -> tuple[WorkflowSectionDraft, ...]:
        self._recorder.record("section_writer", "started")
        try:
            drafts = await self._delegate.write_sections(state)
        except asyncio.CancelledError:
            self._recorder.record("section_writer", "cancelled")
            raise
        except Exception:
            self._recorder.record("section_writer", "failed", "section_writer_failed")
            raise
        if not _draft_citation_preflight(state, drafts):
            self._recorder.record(
                "citation_preflight", "failed", "citation_plan_invalid"
            )
            raise AcademicWorkflowServiceError("citation_plan_invalid")
        self._recorder.record("citation_preflight", "completed")
        # Preserve paid, structurally valid writing before any model review starts.
        # This is explicitly unreviewed, never a machine-approved final report.
        merged = merge_sections(state, drafts)
        _atomic_write(
            self._output_dir / "unreviewed-draft.md",
            "> 未审核草稿：自动引用审核尚未完成，不代表事实或引用已获确认。\n\n"
            + merged.content,
        )
        self._recorder.record("section_writer", "completed")
        return drafts


class _PreparedSectionWriter:
    """Run the production writer against the deterministic citable-source view."""

    def __init__(self, state: AcademicWorkflowState) -> None:
        self._state = state
        self._delegate = GPTResearcherSectionWriterAdapter()

    async def write_section(
        self, state: AcademicWorkflowState, section_id: str
    ) -> WorkflowSectionDraft:
        del state
        return await self._delegate.write_section(self._state, section_id)


class _WebCitationReviewer:
    """Keep valid writing usable when a model opinion is unavailable.

    Reuse the production preflight, client, parser and cancellation handling.
    Only the web orchestration policy differs: a failed per-section opinion
    becomes an explicit human-review placeholder, not a fabricated model verdict.
    Invalid evidence/Gate plans still stop before constructing any client.
    """

    def __init__(self, recorder: _SafeRunRecorder, emit: SafeEmitter) -> None:
        self._recorder = recorder
        self._emit = emit

    async def review_citations(
        self,
        state: AcademicWorkflowState,
        drafts: tuple[WorkflowSectionDraft, ...],
        gate_result: object,
    ) -> tuple[WorkflowSectionCitationReview, ...]:
        plan = _reviewer_core._prepare_review_plan(state, drafts, gate_result)
        if type(plan) is not tuple:
            self._recorder.record("citation_reviewer", "failed", "reviewer_preflight_failed")
            raise AcademicWorkflowServiceError("citation_plan_invalid")
        outline_id, sections = plan
        reviews: list[WorkflowSectionCitationReview] = []
        unavailable = 0
        user_message = None
        try:
            for index, (section_id, source_ids, user_message) in enumerate(sections, 1):
                await self._emit(_progress("review", f"引用审核 {index}/{len(sections)}"))
                review = await _reviewer_core._review_one(
                    _reviewer_core._create_production_citation_reviewer_client,
                    outline_id=outline_id, section_id=section_id,
                    cited_source_ids=source_ids, user_message=user_message,
                )
                user_message = None
                if type(review) is WorkflowSectionCitationReview:
                    code = None
                elif review is _reviewer_core._RESPONSE_FAILURE:
                    code = "reviewer_response_invalid"
                elif review is _reviewer_core._EXECUTION_FAILURE:
                    code = "reviewer_execution_failed"
                elif review is _reviewer_core._CONTRACT_FAILURE:
                    code = "reviewer_contract_failed"
                else:
                    raise AcademicWorkflowServiceError("citation_review_result_invalid")
                if code is not None:
                    unavailable += 1
                    logger.warning("Academic citation review unavailable: section=%s code=%s", section_id, code)
                    review = WorkflowSectionCitationReview(
                        outline_id=outline_id, section_id=section_id,
                        cited_source_ids=source_ids, verdict="uncertain",
                        issues=("insufficient_evidence",), attempt=1,
                        rationale=(
                            f"自动引用审核未完成（{code}）。这是系统待人工核查标记，"
                            "不是模型审核结论，也不代表证据确实不足。正文已保留；"
                            "请对照引用审计核查，或提出修订意见后复审。"
                        ),
                    )
                self._recorder.record(
                    "citation_review_section", "unavailable" if code else "completed",
                    code, section_id=section_id,
                )
                reviews.append(review)
            if unavailable:
                await self._emit(_progress(
                    "human_review", f"草稿已保留；{unavailable} 节自动审核未完成，需人工核查",
                ))
            return tuple(reviews)
        finally:
            # Do not retain evidence-bearing prompt aliases on failed/cancelled frames.
            del plan, sections, user_message


class _ObservedReviewer:
    def __init__(
        self,
        delegate: _Reviewer,
        recorder: _SafeRunRecorder,
        emit: SafeEmitter,
    ) -> None:
        self._delegate = delegate
        self._recorder = recorder
        self._emit = emit

    async def review_citations(
        self,
        state: AcademicWorkflowState,
        drafts: tuple[WorkflowSectionDraft, ...],
        gate_result: object,
    ) -> tuple[WorkflowSectionCitationReview, ...]:
        self._recorder.record("citation_reviewer", "started")
        await self._emit(_progress("review", "引用审核"))
        try:
            reviews = await self._delegate.review_citations(
                state, drafts, gate_result
            )
        except asyncio.CancelledError:
            self._recorder.record("citation_reviewer", "cancelled")
            raise
        except Exception:
            self._recorder.record(
                "citation_reviewer", "failed", "citation_reviewer_failed"
            )
            raise
        self._recorder.record("citation_reviewer", "completed")
        return reviews


class _ObservedProductionComposer:
    def __init__(
        self, recorder: _SafeRunRecorder, emit: SafeEmitter, output_dir: Path,
    ) -> None:
        self._recorder = recorder
        self._emit = emit
        self._output_dir = output_dir

    async def compose(
        self, state: AcademicWorkflowState
    ) -> WorkflowAcademicDraftComposition:
        self._recorder.record("composer", "started")
        writer_state = _section_writer_execution_state(state)
        self._recorder.record("composer_preflight", "completed")
        sequence = _ObservedSequence(
            GPTResearcherSectionWriterSequence(
                section_writer=_PreparedSectionWriter(writer_state)
            ),
            self._recorder,
            self._output_dir,
        )
        reviewer = _ObservedReviewer(
            _WebCitationReviewer(self._recorder, self._emit), self._recorder, self._emit
        )
        composer = GPTResearcherAcademicDraftComposer(
            section_writer_sequence=sequence,
            citation_reviewer=reviewer,
        )
        try:
            composition = await composer.compose(state)
        except asyncio.CancelledError:
            self._recorder.record("composer", "cancelled")
            raise
        except Exception:
            self._recorder.record("composer", "failed", "composer_failed")
            raise
        self._recorder.record("composer", "completed")
        return composition


class _CapturingComposer:
    """Retain editable drafts without expanding LangGraph's minimal outcome DTO."""

    def __init__(self, delegate: _Composer) -> None:
        self.delegate = delegate
        self.result: WorkflowAcademicDraftComposition | None = None

    async def compose(self, state: AcademicWorkflowState) -> WorkflowAcademicDraftComposition:
        self.result = await self.delegate.compose(state)
        return self.result


@dataclass
class _DraftVersion:
    composition: WorkflowAcademicDraftComposition
    parent: int | None
    selected: tuple[str, ...] = ()
    feedback: str = ""
    section_feedback: dict[str, str] = field(default_factory=dict)
    decision: str = "pending"


@dataclass
class _AcademicSession:
    session_id: str
    identity: AcademicWorkflowIdentity
    adapter: object
    output_dir: Path
    recorder: _SafeRunRecorder
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    state: AcademicWorkflowPersistentState | None = None
    composition: WorkflowAcademicDraftComposition | None = None
    status: str = "starting"
    revision_used: bool = False
    human_exported: bool = False
    versions: dict[int, _DraftVersion] = field(default_factory=dict)
    current_version: int = 0
    pending_version: int | None = None
    revision_requests: dict[str, int] = field(default_factory=dict)


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _section_writer_execution_state(
    state: AcademicWorkflowState,
) -> AcademicWorkflowState:
    """Keep the largest leading source plan whose entries are all citable."""

    evidence = state.research_evidence
    if evidence is None:
        raise AcademicWorkflowServiceError("composer_evidence_unavailable")
    provenance_ids = {entry.source_id for entry in evidence.provenance}
    retained_count = 0
    for source in evidence.sources:
        if retained_count == 64 or source.source_id not in provenance_ids:
            break
        retained_count += 1
    if retained_count == 0:
        raise AcademicWorkflowServiceError("composer_evidence_unavailable")
    if retained_count == len(evidence.sources):
        return state

    evidence_payload = evidence.model_dump(mode="json")
    evidence_payload["sources"] = evidence_payload["sources"][:retained_count]
    retained_ids = {
        source.source_id for source in evidence.sources[:retained_count]
    }
    evidence_payload["provenance"] = [
        entry
        for entry in evidence_payload["provenance"]
        if entry["source_id"] in retained_ids
    ]
    trusted_evidence = WorkflowResearchEvidence.model_validate_json(
        _canonical_bytes(evidence_payload), strict=True
    )
    state_payload = state.model_dump(mode="json")
    state_payload["research_evidence"] = trusted_evidence.model_dump(mode="json")
    return AcademicWorkflowState.model_validate_json(
        _canonical_bytes(state_payload), strict=True
    )


def _revision_evidence_blocks(
    state: AcademicWorkflowState,
    source_ids: tuple[str, ...],
) -> tuple[str, ...]:
    """Project bounded provenance only for the section's existing citations."""

    evidence = state.research_evidence
    if (
        evidence is None
        or type(source_ids) is not tuple
        or not source_ids
        or len(set(source_ids)) != len(source_ids)
    ):
        raise AcademicWorkflowServiceError("revision_failed")
    provenance_by_id = {entry.source_id: entry for entry in evidence.provenance}
    blocks: list[str] = []
    for source_id in source_ids:
        entry = provenance_by_id.get(source_id)
        if entry is None:
            raise AcademicWorkflowServiceError("revision_failed")
        blocks.extend(entry.evidence_blocks)

    projected: list[str] = []
    remaining = _REVISION_CONTEXT_TOTAL_MAX_CHARS
    for block in blocks:
        if len(projected) == _REVISION_CONTEXT_MAX_COUNT or remaining == 0:
            break
        take = min(len(block), _REVISION_CONTEXT_MAX_CHARS, remaining)
        projected.append(block[:take])
        remaining -= take
    if not projected:
        raise AcademicWorkflowServiceError("revision_failed")
    return tuple(projected)


def _draft_citation_preflight(
    state: AcademicWorkflowState,
    drafts: tuple[WorkflowSectionDraft, ...],
) -> bool:
    evidence = state.research_evidence
    if evidence is None:
        return False
    source_ids = tuple(source.source_id for source in evidence.sources)
    provenance_ids = {entry.source_id for entry in evidence.provenance}
    for draft in drafts:
        cited = _extract_citations(draft.content, source_ids)
        if type(cited) is not tuple or not cited:
            return False
        if any(source_id not in provenance_ids for source_id in cited):
            return False
    return True


def _progress(stage: str, label: str) -> dict[str, object]:
    return {"type": "academic_progress", "stage": stage, "label": label}


def _exact_payload(payload: object, fields: frozenset[str]) -> dict[str, object]:
    if type(payload) is not dict:
        raise AcademicWorkflowServiceError("invalid_request")
    keys = tuple(dict.keys(payload))
    if any(type(key) is not str for key in keys) or frozenset(keys) != fields:
        raise AcademicWorkflowServiceError("invalid_request")
    return payload


def _safe_session_id(payload: dict[str, object]) -> str:
    value = dict.__getitem__(payload, "session_id")
    if type(value) is not str or _SESSION_ID.fullmatch(value) is None:
        raise AcademicWorkflowServiceError("invalid_session")
    return value


def _atomic_write(path: Path, content: str) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(content, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def _atomic_json(path: Path, value: object) -> None:
    _atomic_write(
        path,
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            indent=2,
        )
        + "\n",
    )


def _artifact_url(session_id: str, filename: str) -> str:
    if _SESSION_ID.fullmatch(session_id) is None or "/" in filename or "\\" in filename:
        raise AcademicWorkflowServiceError("artifact_failed")
    return f"/outputs/academic/{session_id}/{filename}"


def _production_adapter() -> object:
    terminal = _TerminalDelegate()
    return GPTResearcherTopicPlannerAdapter(
        GPTResearcherResearchEvidenceAdapter(
            GPTResearcherOutlineWriterAdapter(terminal)
        )
    )


def _execution_state(state: AcademicWorkflowPersistentState) -> AcademicWorkflowState:
    approval_end = None
    for index, event in enumerate(state.events):
        if event.event_type == "node_completed" and event.node_id == "outline_approval":
            approval_end = index + 1
            break
    if approval_end is None:
        raise AcademicWorkflowServiceError("state_invalid")
    payload = state.model_dump(mode="json")
    payload["phase"] = "outline_approved"
    payload["status"] = "running"
    payload["outcome"] = None
    payload["events"] = payload["events"][:approval_end]
    restored = AcademicWorkflowPersistentState.model_validate_json(
        _canonical_bytes(payload), strict=True
    )
    return _execution_view(restored)


def _composition_from_review_state(
    state: AcademicWorkflowPersistentState,
) -> WorkflowAcademicDraftComposition:
    outcome = state.outcome
    if outcome is None or outcome.outcome_type != "review_required":
        raise AcademicWorkflowServiceError("state_invalid")
    disposition = gate_citation_review_disposition(outcome.gate_result, outcome.reviews)
    return WorkflowAcademicDraftComposition(
        drafts=outcome.drafts,
        gate_result=outcome.gate_result,
        reviews=outcome.reviews,
        disposition=disposition,
        merged_draft=None,
        referenced_draft=None,
    )


def _global_source_ids(composition: WorkflowAcademicDraftComposition) -> tuple[str, ...]:
    ordered: list[str] = []
    seen: set[str] = set()
    for section_ids in composition.gate_result.cited_source_ids_by_section:
        for source_id in section_ids:
            if source_id not in seen:
                seen.add(source_id)
                ordered.append(source_id)
    return tuple(ordered)


def _review_projection(
    reviews: tuple[WorkflowSectionCitationReview, ...],
) -> list[dict[str, object]]:
    return [
        {
            "section_id": review.section_id,
            "verdict": review.verdict,
            "issues": list(review.issues),
            "rationale_length": len(review.rationale),
            "caveat": _REVIEWER_CAVEAT,
        }
        for review in reviews
    ]


def _review_appendix(reviews: tuple[WorkflowSectionCitationReview, ...]) -> str:
    lines = ["", "", "## Remaining Citation Review Issues", ""]
    for review in reviews:
        if review.verdict == "supported":
            continue
        lines.extend(
            (
                f"### {review.section_id}",
                "",
                f"- verdict: `{review.verdict}`",
                "- issues: " + ", ".join(f"`{issue}`" for issue in review.issues),
                "- rationale: " + json.dumps(review.rationale, ensure_ascii=False),
                "- caveat: " + _REVIEWER_CAVEAT,
                "",
            )
        )
    return "\n".join(lines).rstrip()


def _citation_audit(
    state: AcademicWorkflowPersistentState,
    composition: WorkflowAcademicDraftComposition,
) -> tuple[str, dict[str, object]]:
    sources = {source.source_id: source for source in state.research_evidence.sources}
    provenance = {
        item.source_id: item for item in state.research_evidence.provenance
    }
    global_ids = _global_source_ids(composition)
    sections: list[dict[str, object]] = []
    for draft, review, ids in zip(
        composition.drafts,
        composition.reviews,
        composition.gate_result.cited_source_ids_by_section,
    ):
        sections.append(
            {
                "section_id": draft.section_id,
                "cited_source_ids": list(ids),
                "review": {
                    "verdict": review.verdict,
                    "issues": list(review.issues),
                    "rationale": review.rationale,
                    "caveat": _REVIEWER_CAVEAT,
                },
            }
        )
    evidence: list[dict[str, object]] = []
    markdown = [
        "# Citation Audit",
        "",
        "This file uses complete original provenance from the workflow state.",
        "Reviewer output may reflect bounded provenance projection.",
    ]
    for source_id in global_ids:
        source = sources.get(source_id)
        item = provenance.get(source_id)
        flags: list[str] = []
        if source is None:
            flags.append("unknown_source")
        if item is None:
            flags.append("missing_provenance")
        elif not item.evidence_blocks or not any(
            block.strip() for block in item.evidence_blocks
        ):
            flags.append("empty_evidence")
        evidence.append(
            {
                "source_id": source_id,
                "title": None if source is None else source.title,
                "url": None if source is None else source.url,
                "candidate_id": None if source is None else source.candidate_id,
                "evidence_blocks": [] if item is None else list(item.evidence_blocks),
                "flags": flags,
            }
        )
        markdown.extend(
            (
                "",
                f"## `{source_id}`",
                "",
                "- Title: "
                + json.dumps(None if source is None else source.title, ensure_ascii=False),
                "- URL: "
                + json.dumps(None if source is None else source.url, ensure_ascii=False),
                "- Flags: " + (", ".join(flags) or "none"),
            )
        )
        if item is not None:
            for block_index, block in enumerate(item.evidence_blocks, start=1):
                markdown.extend(("", f"### Evidence block {block_index}", ""))
                markdown.extend("    " + line for line in block.splitlines())
    value = {
        "schema": "academic-citation-audit-v1",
        "review_caveat": _REVIEWER_CAVEAT,
        "sections": sections,
        "evidence_appendix": evidence,
        "summary": {
            "section_count": len(sections),
            "unique_cited_source_count": len(global_ids),
            "abnormal_source_count": sum(1 for item in evidence if item["flags"]),
        },
    }
    return "\n".join(markdown) + "\n", value


def _extract_doi(candidate_id: object, url: str) -> str | None:
    values = [value for value in (candidate_id, url) if type(value) is str]
    for original in values:
        value = unquote(original.strip())
        lowered = value.casefold()
        if lowered.startswith("doi:"):
            value = value[4:].strip()
        else:
            for prefix in (
                "https://doi.org/",
                "http://doi.org/",
                "https://dx.doi.org/",
                "http://dx.doi.org/",
            ):
                if lowered.startswith(prefix):
                    value = value[len(prefix) :]
                    break
        if _DOI.fullmatch(value) is not None:
            return value
    return None


def _human_export(
    state: AcademicWorkflowPersistentState,
    composition: WorkflowAcademicDraftComposition,
    merged_content: str,
) -> str:
    ordered: list[str] = []
    seen: set[str] = set()
    for marker in _MARKER.finditer(merged_content):
        source_id = marker.group(1)
        if source_id not in seen:
            seen.add(source_id)
            ordered.append(source_id)
    ordered_ids = tuple(ordered)
    if ordered_ids != _global_source_ids(composition):
        raise AcademicWorkflowServiceError("export_failed")
    numbering = {source_id: index for index, source_id in enumerate(ordered_ids, 1)}
    sources = {source.source_id: source for source in state.research_evidence.sources}

    def replace_run(match: re.Match[str]) -> str:
        numbers: list[int] = []
        seen: set[int] = set()
        for marker in _MARKER.finditer(match.group(0)):
            source_id = marker.group(1)
            if source_id not in numbering:
                raise AcademicWorkflowServiceError("export_failed")
            number = numbering[source_id]
            if number not in seen:
                seen.add(number)
                numbers.append(number)
        return "[" + ",".join(str(number) for number in numbers) + "]"

    rendered = _MARKER_RUN.sub(replace_run, merged_content)
    if "[[cite:" in rendered:
        raise AcademicWorkflowServiceError("export_failed")
    references: list[str] = []
    for source_id in ordered_ids:
        source = sources.get(source_id)
        if source is None:
            raise AcademicWorkflowServiceError("export_failed")
        title = " ".join(source.title.split())
        url = source.url.strip()
        line = f"[{numbering[source_id]}] {title}. URL: {url}"
        doi = _extract_doi(source.candidate_id, url)
        if doi is not None:
            line += f". DOI: {doi}"
        references.append(line)
    return rendered.rstrip() + "\n\n## 参考文献\n\n" + "\n".join(references) + "\n"


class AcademicWorkflowService:
    """In-process academic workflow registry backed by a shared memory saver."""

    def __init__(
        self,
        *,
        checkpointer: InMemorySaver | None = None,
        output_root: Path | None = None,
        adapter_factory: Callable[[], object] | None = None,
        composer_factory: Callable[[], _Composer] | None = None,
        writer_factory: Callable[
            [WorkflowSectionDraft, WorkflowSectionCitationReview, tuple[str, ...]],
            _Writer,
        ]
        | None = None,
        reviewer_factory: Callable[[], _Reviewer] | None = None,
    ) -> None:
        self._checkpointer = InMemorySaver() if checkpointer is None else checkpointer
        self._output_root = (
            (Path.cwd() / "outputs" / "academic")
            if output_root is None
            else output_root
        ).resolve()
        self._output_root.mkdir(parents=True, exist_ok=True)
        self._adapter_factory = _production_adapter if adapter_factory is None else adapter_factory
        self._composer_factory = composer_factory
        self._writer_factory = writer_factory
        self._reviewer_factory = reviewer_factory
        self._sessions: dict[str, _AcademicSession] = {}

    def _session(self, session_id: str) -> _AcademicSession:
        session = self._sessions.get(session_id)
        if session is None:
            raise AcademicWorkflowServiceError("session_not_found")
        return session

    def _new_output_dir(self, session_id: str) -> Path:
        target = (self._output_root / session_id).resolve()
        if target.parent != self._output_root or _SESSION_ID.fullmatch(session_id) is None:
            raise AcademicWorkflowServiceError("artifact_failed")
        target.mkdir(parents=False, exist_ok=False)
        return target

    async def start(self, payload: object, emit: SafeEmitter) -> None:
        values = _exact_payload(payload, frozenset({"query", "report_mode"}))
        query = dict.__getitem__(values, "query")
        report_mode = dict.__getitem__(values, "report_mode")
        if (
            type(query) is not str
            or not query.strip()
            or len(query) > 4096
            or type(report_mode) is not str
            or report_mode not in _FIXED_PROFILES
        ):
            raise AcademicWorkflowServiceError("invalid_request")
        token = uuid.uuid4().hex
        session_id = "academic-" + token
        identity = AcademicWorkflowIdentity(
            workflow_id="academic-workflow:" + token,
            thread_id="academic-thread:" + token,
            run_id="academic-run:" + token,
        )
        output_dir = self._new_output_dir(session_id)
        adapter = _ProgressAdapter(self._adapter_factory(), emit)
        recorder = _SafeRunRecorder(session_id, output_dir)
        session = _AcademicSession(
            session_id, identity, adapter, output_dir, recorder
        )
        self._sessions[session_id] = session
        recorder.record("workflow_start", "started")
        request = AcademicWorkflowRequest(
            workflow_mode="academic_langgraph",
            workflow_id=identity.workflow_id,
            thread_id=identity.thread_id,
            run_id=identity.run_id,
            query=query,
            report_type="research_report",
            report_source="web",
            tone=Tone.Formal.value,
            language="zh-CN",
            source_urls=(),
            document_urls=(),
            query_domains=(),
            max_search_results=None,
            report_mode=report_mode,
            report_locale="zh-CN",
        )
        async with session.lock:
            try:
                state = await start_academic_workflow(
                    request, adapter, checkpointer=self._checkpointer
                )
            except asyncio.CancelledError:
                session.status = "cancelled"
                recorder.record("workflow_start", "cancelled")
                raise
            except Exception:
                session.status = "failed"
                recorder.record("workflow_start", "failed", "start_failed")
                logger.exception("Academic workflow start failed")
                raise AcademicWorkflowServiceError("start_failed") from None
            if state.phase != "outline_ready" or state.outline is None:
                session.status = "failed"
                raise AcademicWorkflowServiceError("start_failed")
            session.state = state
            session.status = "outline_ready"
            recorder.record("workflow_start", "completed")
            await emit(_progress("approval", "等待审批"))
            await emit(
                {
                    "type": "academic_outline",
                    "session_id": session_id,
                    "title": state.outline.title,
                    "sections": [
                        {
                            "section_id": section.section_id,
                            "section_role": section.section_role,
                            "title": section.title,
                        }
                        for section in state.outline.sections
                    ],
                    "phase": state.phase,
                    "status": state.status,
                }
            )

    async def decision(self, payload: object, emit: SafeEmitter) -> None:
        values = _exact_payload(payload, frozenset({"session_id", "decision"}))
        session_id = _safe_session_id(values)
        decision = dict.__getitem__(values, "decision")
        if type(decision) is not str or decision not in ("approve", "reject"):
            raise AcademicWorkflowServiceError("invalid_request")
        session = self._session(session_id)
        async with session.lock:
            state = session.state
            if session.status != "outline_ready" or state is None or state.outline is None:
                raise AcademicWorkflowServiceError("decision_already_submitted")
            command = AcademicOutlineDecisionCommand(
                schema_version="1",
                workflow_id=state.workflow_id,
                thread_id=state.thread_id,
                run_id=state.run_id,
                outline_id=state.outline.outline_id,
                outline_digest=_outline_digest(state.outline),
                decision=decision,
                actor_assertion="web academic workflow explicit decision",
            )
            if decision == "approve":
                composer = _CapturingComposer(
                    _ObservedProductionComposer(session.recorder, emit, session.output_dir)
                    if self._composer_factory is None
                    else self._composer_factory()
                )
                session.status = "composing"
                session.recorder.record("outline_decision", "approved")
                await emit(_progress("writing", "逐节写作"))
            else:
                composer = None
                session.status = "rejecting"
                session.recorder.record("outline_decision", "rejected")
            try:
                terminal = await submit_academic_outline_decision(
                    command,
                    session.adapter,
                    checkpointer=self._checkpointer,
                    composer=composer,
                )
            except asyncio.CancelledError:
                session.status = (
                    "decision_cancelled" if decision == "approve" else "failed"
                )
                session.recorder.record("outline_decision", "cancelled")
                raise
            except Exception:
                session.status = "decision_failed" if decision == "approve" else "failed"
                session.recorder.record(
                    "outline_decision", "failed", "decision_failed"
                )
                logger.exception("Academic workflow decision failed")
                raise AcademicWorkflowServiceError("decision_failed") from None
            session.state = terminal
            if isinstance(composer, _CapturingComposer):
                session.composition = composer.result
            session.recorder.record("outline_decision", "completed")
            if terminal.phase == "outline_rejected":
                session.status = "rejected"
                await emit(
                    {
                        "type": "academic_final",
                        "session_id": session_id,
                        "result": "rejected",
                        "machine_disposition_ready": False,
                    }
                )
                return
            await emit(_progress("review", "引用审核"))
            await self._publish_terminal(session, terminal, emit)

    async def _publish_terminal(
        self,
        session: _AcademicSession,
        state: AcademicWorkflowPersistentState,
        emit: SafeEmitter,
    ) -> None:
        outcome = state.outcome
        if state.phase == "draft_ready" and outcome is not None and outcome.outcome_type == "draft_ready":
            self._ensure_versions(session)
            _atomic_write(session.output_dir / "final-report.md", outcome.referenced_draft.content)
            session.status = "draft_ready"
            await emit(_progress("complete", "完成"))
            await emit(
                {
                    "type": "academic_ready",
                    "session_id": session.session_id,
                    "phase": state.phase,
                    "status": state.status,
                    "section_count": len(outcome.referenced_draft.section_ids),
                    "reference_count": len(outcome.referenced_draft.reference_source_ids),
                    "markdown": outcome.referenced_draft.content,
                    "url": _artifact_url(session.session_id, "final-report.md"),
                    "draft_workspace": self._workspace(session),
                }
            )
            return
        if state.phase != "review_required" or outcome is None or outcome.outcome_type != "review_required":
            session.status = "failed"
            raise AcademicWorkflowServiceError("terminal_invalid")
        composition = _composition_from_review_state(state)
        session.composition = composition
        session.status = "review_required"
        self._ensure_versions(session)
        await self._save_review_artifacts(session, composition)
        await emit(_progress("human_review", "人工复核"))
        await emit(self._review_message(session, composition))

    async def _save_review_artifacts(
        self,
        session: _AcademicSession,
        composition: WorkflowAcademicDraftComposition,
    ) -> None:
        if session.state is None:
            raise AcademicWorkflowServiceError("state_invalid")
        execution = _execution_state(session.state)
        merged = merge_sections(execution, composition.drafts)
        _atomic_write(
            session.output_dir / "final-report-draft.md",
            merged.content + _review_appendix(composition.reviews),
        )
        audit_markdown, audit_json = _citation_audit(session.state, composition)
        _atomic_write(session.output_dir / "citation-audit.md", audit_markdown)
        _atomic_json(session.output_dir / "citation-audit.json", audit_json)

    def _review_message(
        self,
        session: _AcademicSession,
        composition: WorkflowAcademicDraftComposition,
    ) -> dict[str, object]:
        return {
            "type": "academic_review_required",
            "session_id": session.session_id,
            "phase": "review_required",
            "status": "completed",
            "revision_used": session.revision_used,
            "reviews": _review_projection(composition.reviews),
            "draft_url": _artifact_url(session.session_id, "final-report-draft.md"),
            "audit_markdown_url": _artifact_url(session.session_id, "citation-audit.md"),
            "audit_json_url": _artifact_url(session.session_id, "citation-audit.json"),
            "draft_workspace": self._workspace(session),
        }

    def _version_url(self, session: _AcademicSession, version: int, filename: str) -> str:
        return f"/outputs/academic/{session.session_id}/versions/v{version:04d}/{filename}"

    def _save_version(self, session: _AcademicSession, number: int, version: _DraftVersion) -> None:
        if session.state is None:
            raise AcademicWorkflowServiceError("state_invalid")
        target = session.output_dir / "versions" / f"v{number:04d}"
        target.mkdir(parents=True, exist_ok=True)
        composition = version.composition
        execution = _execution_state(session.state)
        merged = merge_sections(execution, composition.drafts)
        _atomic_write(target / "draft.md", _human_export(session.state, composition, merged.content))
        _atomic_json(target / "composition.json", composition.model_dump(mode="json"))
        _atomic_json(target / "feedback.json", {
            "parent_version": version.parent,
            "section_ids": list(version.selected),
            "feedback": version.feedback,
            "section_feedback": version.section_feedback,
        })
        audit_markdown, audit_json = _citation_audit(session.state, composition)
        _atomic_write(target / "citation-audit.md", audit_markdown)
        _atomic_json(target / "citation-audit.json", audit_json)

    def _save_version_index(self, session: _AcademicSession) -> None:
        _atomic_json(session.output_dir / "versions.json", {
            "current_version": session.current_version,
            "pending_version": session.pending_version,
            "versions": [
                {"version": number, "parent_version": item.parent, "decision": item.decision}
                for number, item in session.versions.items()
            ],
        })

    def _ensure_versions(self, session: _AcademicSession) -> None:
        if session.current_version or session.composition is None:
            return
        version = _DraftVersion(session.composition, None, decision="accepted")
        self._save_version(session, 1, version)
        session.versions[1] = version
        session.current_version = 1
        self._save_version_index(session)

    def _workspace(self, session: _AcademicSession) -> dict[str, object] | None:
        if not session.current_version or session.state is None or session.state.outline is None:
            return None
        titles = {section.section_id: section.title for section in session.state.outline.sections}

        def sections(version: _DraftVersion) -> list[dict[str, object]]:
            return [
                {"section_id": draft.section_id, "title": titles[draft.section_id],
                 "content": draft.content, "verdict": review.verdict,
                 "issues": list(review.issues), "rationale": review.rationale}
                for draft, review in zip(version.composition.drafts, version.composition.reviews)
            ]

        current = session.versions[session.current_version]
        pending = session.versions.get(session.pending_version)
        return {
            "session_id": session.session_id,
            "current_version": session.current_version,
            "machine_ready": current.composition.disposition.disposition == "ready",
            "sections": sections(current),
            "versions": [
                {"version": number, "parent_version": item.parent, "decision": item.decision,
                 "draft_url": self._version_url(session, number, "draft.md"),
                 "audit_url": self._version_url(session, number, "citation-audit.md")}
                for number, item in session.versions.items()
            ],
            "pending": None if pending is None else {
                "version": session.pending_version, "parent_version": pending.parent,
                "section_ids": list(pending.selected), "feedback": pending.feedback,
                "section_feedback": pending.section_feedback,
                "machine_ready": pending.composition.disposition.disposition == "ready",
                "sections": sections(pending),
            },
        }

    async def draft_state(self, payload: object, emit: SafeEmitter) -> None:
        values = _exact_payload(payload, frozenset({"session_id"}))
        session = self._session(_safe_session_id(values))
        async with session.lock:
            await emit({"type": "academic_draft_updated", "draft_workspace": self._workspace(session)})

    async def revise(self, payload: object, emit: SafeEmitter) -> None:
        # Keep the previous client command valid; new clients use explicit feedback.
        if type(payload) is dict and set(payload) == {"session_id"}:
            await self._legacy_revise(payload, emit)
            return
        values = _exact_payload(payload, frozenset({
            "session_id", "base_version", "request_id", "section_ids", "feedback", "section_feedback"
        }))
        session = self._session(_safe_session_id(values))
        base = values["base_version"]
        request_id = values["request_id"]
        selected = values["section_ids"]
        feedback = values["feedback"]
        section_feedback = values["section_feedback"]
        if (type(base) is not int or base < 1 or type(request_id) is not str
                or re.fullmatch(r"[A-Za-z0-9-]{16,64}", request_id) is None):
            raise AcademicWorkflowServiceError("invalid_request")
        if (type(selected) is not list or not 1 <= len(selected) <= 10
                or any(type(item) is not str for item in selected)
                or len(set(selected)) != len(selected)):
            raise AcademicWorkflowServiceError("invalid_sections")
        if (type(feedback) is not str or len(feedback) > 4000
                or type(section_feedback) is not dict
                or any(type(key) is not str or key not in selected or type(value) is not str
                       or len(value) > 2000 for key, value in section_feedback.items())
                or len(feedback) + sum(len(value) for value in section_feedback.values()) > 12000):
            raise AcademicWorkflowServiceError("invalid_feedback")
        feedback = feedback.strip()
        section_feedback = {key: value.strip() for key, value in section_feedback.items()}
        if any(not feedback and not section_feedback.get(key) for key in selected):
            raise AcademicWorkflowServiceError("invalid_feedback")
        async with session.lock:
            if session.composition is None or session.state is None or not session.current_version:
                raise AcademicWorkflowServiceError("revision_unavailable")
            section_ids = tuple(draft.section_id for draft in session.composition.drafts)
            if not set(selected).issubset(section_ids):
                raise AcademicWorkflowServiceError("invalid_sections")
            selected_ids = tuple(key for key in section_ids if key in selected)
            if request_id in session.revision_requests:
                previous = session.versions[session.revision_requests[request_id]]
                if (previous.parent != base or previous.selected != selected_ids
                        or previous.feedback != feedback or previous.section_feedback != section_feedback):
                    raise AcademicWorkflowServiceError("revision_request_conflict")
                await emit({"type": "academic_revision_preview", "draft_workspace": self._workspace(session)})
                return
            if base != session.current_version:
                raise AcademicWorkflowServiceError("stale_version")
            if session.pending_version is not None:
                raise AcademicWorkflowServiceError("revision_pending")
            previous_status = session.status
            previous = session.composition
            session.status = "revising"
            number = max(session.versions) + 1
            try:
                session.recorder.record("feedback_revision", "started")
                await emit(_progress("writing", "按人工意见修订"))
                execution = _execution_state(session.state)
                writer_state = _section_writer_execution_state(execution)
                drafts = list(previous.drafts)
                for index, draft in enumerate(previous.drafts):
                    if draft.section_id not in selected_ids:
                        continue
                    original_ids = previous.gate_result.cited_source_ids_by_section[index]
                    writer = self._revision_writer(
                        draft, previous.reviews[index], original_ids,
                        _revision_evidence_blocks(execution, original_ids),
                        (feedback, section_feedback.get(draft.section_id, "")),
                    )
                    updated = await writer.write_section(writer_state, draft.section_id)
                    cited = _extract_citations(updated.content, original_ids)
                    if (updated.section_id != draft.section_id or updated.outline_id != draft.outline_id
                            or type(cited) is not tuple or not cited):
                        raise AcademicWorkflowServiceError("revision_failed")
                    drafts[index] = updated
                result_drafts = tuple(drafts)
                gate = gate_citation_evidence(execution, result_drafts)
                await emit(_progress("review", "修订后引用复审"))
                reviewer = (_WebCitationReviewer(session.recorder, emit) if self._reviewer_factory is None
                            else self._reviewer_factory())
                reviews = await reviewer.review_citations(execution, result_drafts, gate)
                disposition = gate_citation_review_disposition(gate, reviews)
                merged = referenced = None
                if disposition.disposition == "ready":
                    merged = merge_sections(execution, result_drafts)
                    referenced = render_references(execution, merged, gate, disposition)
                candidate = _DraftVersion(
                    WorkflowAcademicDraftComposition(
                        drafts=result_drafts, gate_result=gate, reviews=reviews,
                        disposition=disposition, merged_draft=merged, referenced_draft=referenced,
                    ), base, selected_ids, feedback, section_feedback,
                )
                self._save_version(session, number, candidate)
                session.versions[number] = candidate
                session.pending_version = number
                session.revision_requests[request_id] = number
                session.recorder.record("feedback_revision", "completed")
                self._save_version_index(session)
            except BaseException as exc:
                session.versions.pop(number, None)
                session.revision_requests.pop(request_id, None)
                session.pending_version = None
                session.status = previous_status
                if isinstance(exc, asyncio.CancelledError):
                    session.recorder.record("feedback_revision", "cancelled")
                    raise
                if not isinstance(exc, Exception):
                    raise
                session.recorder.record("feedback_revision", "failed", "revision_failed")
                raise AcademicWorkflowServiceError("revision_failed") from None
            session.status = previous_status
            # A delivery failure must not discard a completed, paid candidate.
            await emit(_progress("human_review", "比较并选择版本"))
            await emit({"type": "academic_revision_preview", "draft_workspace": self._workspace(session)})

    async def revision_decision(self, payload: object, emit: SafeEmitter) -> None:
        values = _exact_payload(payload, frozenset({"session_id", "base_version", "version", "decision"}))
        session = self._session(_safe_session_id(values))
        number, base, decision = values["version"], values["base_version"], values["decision"]
        if (type(number) is not int or type(base) is not int
                or type(decision) is not str or decision not in ("accept", "discard")):
            raise AcademicWorkflowServiceError("invalid_request")
        async with session.lock:
            version = session.versions.get(number)
            expected_decision = "accepted" if decision == "accept" else "discarded"
            if version is not None and version.parent == base and version.decision == expected_decision:
                await emit({"type": "academic_draft_updated", "draft_workspace": self._workspace(session)})
                return
            if base != session.current_version or version is None or session.pending_version != number:
                raise AcademicWorkflowServiceError("stale_version")
            version.decision = expected_decision
            session.pending_version = None
            if decision == "accept":
                session.current_version = number
            try:
                self._save_version_index(session)
            except Exception:
                session.current_version = base
                session.pending_version = number
                version.decision = "pending"
                raise AcademicWorkflowServiceError("revision_decision_failed") from None
            if decision == "accept":
                session.composition = version.composition
                session.revision_used = False
                session.human_exported = False
                session.status = ("draft_ready" if version.composition.disposition.disposition == "ready"
                                  else "review_required")
            await emit({"type": "academic_draft_updated", "draft_workspace": self._workspace(session)})

    def _revision_writer(
        self,
        draft: WorkflowSectionDraft,
        review: WorkflowSectionCitationReview,
        original_ids: tuple[str, ...],
        evidence_blocks: tuple[str, ...],
        feedback: tuple[str, str] | None = None,
    ) -> _Writer:
        if self._writer_factory is not None:
            return self._writer_factory(draft, review, original_ids)
        return GPTResearcherSectionWriterAdapter(
            section_writer_client_factory=_RevisionClientFactory(
                draft, review, original_ids, evidence_blocks, feedback
            )
        )

    async def _legacy_revise(self, payload: object, emit: SafeEmitter) -> None:
        values = _exact_payload(payload, frozenset({"session_id"}))
        session_id = _safe_session_id(values)
        session = self._session(session_id)
        async with session.lock:
            if (
                session.status != "review_required"
                or session.state is None
                or session.composition is None
            ):
                raise AcademicWorkflowServiceError("revision_unavailable")
            if session.revision_used:
                raise AcademicWorkflowServiceError("revision_already_used")
            if session.pending_version is not None:
                raise AcademicWorkflowServiceError("revision_pending")

            previous_composition = session.composition
            previous_version = session.current_version
            number = max(session.versions, default=0) + 1
            session.status = "revising"
            try:
                session.recorder.record("revision", "started")
                await emit(_progress("writing", "逐节写作"))
                execution = _execution_state(session.state)
                revised = list(previous_composition.drafts)
                for index, review in enumerate(previous_composition.reviews):
                    if review.verdict == "supported":
                        continue
                    original_ids = (
                        previous_composition.gate_result.cited_source_ids_by_section[index]
                    )
                    evidence_blocks = _revision_evidence_blocks(execution, original_ids)
                    writer = self._revision_writer(
                        previous_composition.drafts[index],
                        review,
                        original_ids,
                        evidence_blocks,
                    )
                    draft = await writer.write_section(execution, review.section_id)
                    cited = _extract_citations(draft.content, original_ids)
                    if type(cited) is not tuple or not cited:
                        raise AcademicWorkflowServiceError("revision_failed")
                    revised[index] = draft

                drafts = tuple(revised)
                gate = gate_citation_evidence(execution, drafts)
                reviewer = (
                    _WebCitationReviewer(session.recorder, emit)
                    if self._reviewer_factory is None
                    else self._reviewer_factory()
                )
                await emit(_progress("review", "引用审核"))
                reviews = await reviewer.review_citations(execution, drafts, gate)
                disposition = gate_citation_review_disposition(gate, reviews)

                merged = referenced = None
                if disposition.disposition == "ready":
                    merged = merge_sections(execution, drafts)
                    referenced = render_references(
                        execution, merged, gate, disposition
                    )
                updated = WorkflowAcademicDraftComposition(
                    drafts=drafts,
                    gate_result=gate,
                    reviews=reviews,
                    disposition=disposition,
                    merged_draft=merged,
                    referenced_draft=referenced,
                )
                report = None
                if referenced is not None:
                    report = _human_export(session.state, updated, merged.content)
                    _atomic_write(session.output_dir / "final-report.md", report)
                else:
                    await self._save_review_artifacts(session, updated)
                version = _DraftVersion(
                    updated, previous_version or None,
                    tuple(review.section_id for review in previous_composition.reviews
                          if review.verdict != "supported"),
                    decision="accepted",
                )
                self._save_version(session, number, version)
                session.versions[number] = version
                session.current_version = number
                session.recorder.record("revision", "completed")
                self._save_version_index(session)
            except asyncio.CancelledError:
                session.versions.pop(number, None)
                session.current_version = previous_version
                session.composition = previous_composition
                session.revision_used = False
                session.status = "review_required"
                session.recorder.record("revision", "cancelled")
                raise
            except Exception:
                session.versions.pop(number, None)
                session.current_version = previous_version
                session.composition = previous_composition
                session.revision_used = False
                session.status = "review_required"
                session.recorder.record("revision", "failed", "revision_failed")
                raise AcademicWorkflowServiceError("revision_failed") from None
            # Legacy clients auto-adopt, but their result still participates in versioning.
            # Delivery failure must not undo an already saved paid revision.
            session.composition = updated
            session.revision_used = True
            session.human_exported = False
            session.status = "draft_ready" if referenced is not None else "review_required"
            if referenced is not None:
                await emit(_progress("complete", "完成"))
                await emit({
                    "type": "academic_ready", "session_id": session.session_id,
                    "phase": "draft_ready", "status": "completed",
                    "section_count": len(drafts),
                    "reference_count": len(referenced.reference_source_ids),
                    "markdown": report,
                    "url": _artifact_url(session.session_id, "final-report.md"),
                    "revision_used": True, "draft_workspace": self._workspace(session),
                })
            else:
                await emit(_progress("human_review", "人工复核"))
                await emit(self._review_message(session, updated))

    async def export(self, payload: object, emit: SafeEmitter) -> None:
        fields = {"session_id", "version"} if type(payload) is dict and "version" in payload else {"session_id"}
        values = _exact_payload(payload, frozenset(fields))
        session_id = _safe_session_id(values)
        session = self._session(session_id)
        async with session.lock:
            if session.current_version:
                if "version" in values and (
                    type(values["version"]) is not int or values["version"] != session.current_version
                ):
                    raise AcademicWorkflowServiceError("stale_version")
                if session.pending_version is not None:
                    raise AcademicWorkflowServiceError("revision_pending")
                if session.state is None or session.composition is None:
                    raise AcademicWorkflowServiceError("export_unavailable")
                composition = session.composition
                ready = composition.disposition.disposition == "ready"
                merged = merge_sections(_execution_state(session.state), composition.drafts)
                report = _human_export(session.state, composition, merged.content)
                target = session.output_dir / "versions" / f"v{session.current_version:04d}"
                _atomic_write(target / "final-report.md", report)
                _atomic_json(target / "export-info.json", {
                    "version": session.current_version, "machine_disposition_ready": ready,
                    "basis": "machine_ready" if ready else "explicit_human_confirmation",
                })
                _atomic_write(session.output_dir / "final-report.md", report)
                session.human_exported = not ready
                session.status = "draft_ready" if ready else "manual_final"
                await emit({
                    "type": "academic_final", "session_id": session.session_id,
                    "result": "machine_ready_export" if ready else "human_confirmed_export",
                    "machine_disposition_ready": ready, "markdown": report,
                    "url": self._version_url(session, session.current_version, "final-report.md"),
                    "draft_workspace": self._workspace(session),
                })
                return
            if session.human_exported:
                await emit(
                    {
                        "type": "academic_final",
                        "session_id": session.session_id,
                        "result": "human_confirmed_export",
                        "machine_disposition_ready": False,
                        "url": _artifact_url(session.session_id, "final-report.md"),
                    }
                )
                return
            if session.state is None or session.composition is None or session.composition.disposition.disposition == "ready":
                raise AcademicWorkflowServiceError("export_unavailable")
            execution = _execution_state(session.state)
            merged = merge_sections(execution, session.composition.drafts)
            report = _human_export(session.state, session.composition, merged.content)
            _atomic_write(session.output_dir / "final-report.md", report)
            session.human_exported = True
            session.status = "manual_final"
            await emit(_progress("complete", "完成"))
            await emit(
                {
                    "type": "academic_final",
                    "session_id": session.session_id,
                    "result": "human_confirmed_export",
                    "machine_disposition_ready": False,
                    "markdown": report,
                    "url": _artifact_url(session.session_id, "final-report.md"),
                    "reference_count": len(_global_source_ids(session.composition)),
                }
            )

    async def resume_session(self, session_id: str, emit: SafeEmitter) -> None:
        """Internal-only resume hook; it is intentionally not exposed as a command."""
        if type(session_id) is not str or _SESSION_ID.fullmatch(session_id) is None:
            raise AcademicWorkflowServiceError("invalid_session")
        session = self._session(session_id)
        async with session.lock:
            if session.state is None:
                raise AcademicWorkflowServiceError("resume_unavailable")
            composer = None if self._composer_factory is None else self._composer_factory()
            try:
                state = await resume_academic_workflow(
                    session.identity,
                    session.adapter,
                    checkpointer=self._checkpointer,
                    composer=composer,
                )
            except Exception:
                raise AcademicWorkflowServiceError("resume_failed") from None
            session.state = state
            await self._publish_terminal(session, state, emit)


academic_workflow_service = AcademicWorkflowService()


__all__ = (
    "AcademicWorkflowService",
    "AcademicWorkflowServiceError",
    "academic_workflow_service",
)
