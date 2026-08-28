"""Focused, offline tests for the browser academic workflow service."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from backend.server.academic_workflow_service import (
    AcademicWorkflowService,
    AcademicWorkflowServiceError,
    _section_writer_execution_state,
)
from gpt_researcher.workflows.academic_writing.academic_draft_composer import (
    WorkflowAcademicDraftComposition,
)
from gpt_researcher.workflows.academic_writing.citation_evidence_gate import (
    WorkflowCitationEvidenceGateResult,
)
from gpt_researcher.workflows.academic_writing.citation_review_disposition import (
    WorkflowCitationReviewDisposition,
)
from gpt_researcher.workflows.academic_writing.citation_reviewer import (
    WorkflowSectionCitationReview,
)
from gpt_researcher.workflows.academic_writing.references_renderer import (
    WorkflowReferencedDraft,
)
from gpt_researcher.workflows.academic_writing.report_profiles import (
    _get_report_profile,
)
from gpt_researcher.workflows.academic_writing.section_merger import (
    WorkflowMergedDraft,
)
from gpt_researcher.workflows.academic_writing.state import (
    AcademicWorkflowRequest,
    WorkflowEvidenceProvenance,
    WorkflowEvidenceSource,
    WorkflowOutline,
    WorkflowOutlineSection,
    WorkflowResearchEvidence,
    WorkflowSectionDraft,
    WorkflowTopicPlan,
)
from gpt_researcher.utils.enum import Tone


SOURCE_ID = "evidence-source:000001"
MARKER = "[[cite:evidence-source:000001]]"


class _Adapter:
    def __init__(self) -> None:
        self.requests: list[AcademicWorkflowRequest] = []

    async def plan_topic(self, request: AcademicWorkflowRequest) -> WorkflowTopicPlan:
        self.requests.append(request)
        return WorkflowTopicPlan(
            topic_plan_id="topic-plan:000001",
            workflow_id=request.workflow_id,
            run_id=request.run_id,
            attempt=1,
            research_topic=request.query,
            research_questions=("What does the evidence show?",),
        )

    async def collect_research_evidence(
        self, request: AcademicWorkflowRequest, topic_plan: WorkflowTopicPlan
    ) -> WorkflowResearchEvidence:
        return WorkflowResearchEvidence(
            evidence_id="evidence:000001",
            topic_plan_id=topic_plan.topic_plan_id,
            attempt=1,
            context_blocks=("Bounded evidence.",),
            sources=(
                WorkflowEvidenceSource(
                    source_id=SOURCE_ID,
                    order=1,
                    title="Source",
                    url="https://example.test/source",
                    candidate_id="10.1000/example",
                ),
                WorkflowEvidenceSource(
                    source_id="evidence-source:000002",
                    order=2,
                    title="Metadata-only source",
                    url="https://example.test/metadata-only",
                    candidate_id=None,
                ),
            ),
            provenance=(
                WorkflowEvidenceProvenance(
                    source_id=SOURCE_ID,
                    evidence_blocks=("Bounded evidence.",),
                ),
            ),
        )

    async def write_outline(
        self,
        request: AcademicWorkflowRequest,
        topic_plan: WorkflowTopicPlan,
        evidence: WorkflowResearchEvidence,
    ) -> WorkflowOutline:
        profile = _get_report_profile(request.report_mode)
        assert profile is not None
        return WorkflowOutline(
            outline_id="outline:000001",
            evidence_id=evidence.evidence_id,
            attempt=1,
            title="Academic outline",
            sections=tuple(
                WorkflowOutlineSection(
                    section_id=f"section:{index:06d}",
                    order=index,
                    title=title,
                    brief=f"Brief {index}",
                    section_role=role,
                )
                for index, (role, title) in enumerate(profile, start=1)
            ),
            report_mode=request.report_mode,
            report_locale=request.report_locale,
        )


def _composition(state: object, verdict: str) -> WorkflowAcademicDraftComposition:
    outline = state.outline
    section_ids = tuple(section.section_id for section in outline.sections)
    citations = tuple((SOURCE_ID,) for _ in section_ids)
    drafts = tuple(
        WorkflowSectionDraft(
            outline_id=outline.outline_id,
            section_id=section_id,
            attempt=1,
            content=f"Claim {MARKER}",
        )
        for section_id in section_ids
    )
    gate = WorkflowCitationEvidenceGateResult(
        outline_id=outline.outline_id,
        section_ids=section_ids,
        cited_source_ids_by_section=citations,
        attempt=1,
    )
    ready = verdict == "supported"
    review_verdicts = tuple(
        "supported" if ready or (verdict == "mixed" and index == 0) else "uncertain"
        for index in range(len(section_ids))
    )
    section_dispositions = tuple(
        "ready" if item == "supported" else "needs_human_review"
        for item in review_verdicts
    )
    reviews = tuple(
        WorkflowSectionCitationReview(
            outline_id=outline.outline_id,
            section_id=section_id,
            cited_source_ids=(SOURCE_ID,),
            verdict=review_verdicts[index],
            issues=() if review_verdicts[index] == "supported" else ("insufficient_evidence",),
            rationale="Bounded opinion.",
            attempt=1,
        )
        for index, section_id in enumerate(section_ids)
    )
    disposition = WorkflowCitationReviewDisposition(
        outline_id=outline.outline_id,
        section_ids=section_ids,
        section_dispositions=section_dispositions,
        disposition="ready" if ready else "needs_human_review",
        attempt=1,
    )
    merged = None
    referenced = None
    if ready:
        merged = WorkflowMergedDraft(
            outline_id=outline.outline_id,
            section_ids=section_ids,
            attempt=1,
            content="Merged",
        )
        referenced = WorkflowReferencedDraft(
            outline_id=outline.outline_id,
            section_ids=section_ids,
            reference_source_ids=(SOURCE_ID,),
            attempt=1,
            content="Merged\n\n## References\n\nopaque",
        )
    return WorkflowAcademicDraftComposition(
        drafts=drafts,
        gate_result=gate,
        reviews=reviews,
        disposition=disposition,
        merged_draft=merged,
        referenced_draft=referenced,
    )


class _Composer:
    def __init__(
        self,
        verdict: str,
        entered: asyncio.Event | None = None,
        error: Exception | None = None,
    ) -> None:
        self.verdict = verdict
        self.entered = entered
        self.error = error
        self.calls = 0
        self.states: list[object] = []

    async def compose(self, state: object) -> WorkflowAcademicDraftComposition:
        self.calls += 1
        self.states.append(state)
        if self.entered is not None:
            self.entered.set()
            await asyncio.sleep(0)
        if self.error is not None:
            raise self.error
        return _composition(state, self.verdict)


class _Writer:
    def __init__(self, draft: WorkflowSectionDraft) -> None:
        self.draft = draft

    async def write_section(self, state: object, section_id: str) -> WorkflowSectionDraft:
        return WorkflowSectionDraft(
            outline_id=self.draft.outline_id,
            section_id=section_id,
            attempt=1,
            content=f"Narrowed claim {MARKER}",
        )


class _Reviewer:
    async def review_citations(
        self,
        state: object,
        drafts: tuple[WorkflowSectionDraft, ...],
        gate_result: WorkflowCitationEvidenceGateResult,
    ) -> tuple[WorkflowSectionCitationReview, ...]:
        return tuple(
            WorkflowSectionCitationReview(
                outline_id=gate_result.outline_id,
                section_id=draft.section_id,
                cited_source_ids=gate_result.cited_source_ids_by_section[index],
                verdict="supported",
                issues=(),
                rationale="Supported after one revision.",
                attempt=1,
            )
            for index, draft in enumerate(drafts)
        )


async def _start(
    service: AcademicWorkflowService,
) -> tuple[str, list[dict[str, object]]]:
    messages: list[dict[str, object]] = []

    await service.start(
        {"query": "Academic query", "report_mode": "stem_literature_review"},
        _emit_to(messages),
    )
    outline = next(message for message in messages if message["type"] == "academic_outline")
    return str(outline["session_id"]), messages


def _emit_to(messages: list[dict[str, object]]):
    async def emit(message: dict[str, object]) -> None:
        messages.append(message)

    return emit


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("verdict", "message_type", "filename"),
    (
        ("supported", "academic_ready", "final-report.md"),
        ("uncertain", "academic_review_required", "citation-audit.json"),
    ),
)
async def test_start_and_approve_reach_both_safe_terminal_projections(
    tmp_path: Path, verdict: str, message_type: str, filename: str
) -> None:
    composer = _Composer(verdict)
    adapter = _Adapter()
    service = AcademicWorkflowService(
        output_root=tmp_path,
        adapter_factory=lambda: adapter,
        composer_factory=lambda: composer,
    )
    session_id, messages = await _start(service)
    assert adapter.requests[0].tone == Tone.Formal.value
    assert len(next(item for item in messages if item["type"] == "academic_outline")["sections"]) == 8

    await service.decision(
        {"session_id": session_id, "decision": "approve"}, _emit_to(messages)
    )

    assert composer.calls == 1
    writer_state = _section_writer_execution_state(composer.states[0])
    assert len(composer.states[0].research_evidence.sources) == 2
    assert tuple(source.source_id for source in writer_state.research_evidence.sources) == (
        SOURCE_ID,
    )
    assert messages[-1]["type"] == message_type
    assert (tmp_path / session_id / filename).is_file()
    forbidden = {"composition", "disposition", "merged_draft", "handoff"}
    assert forbidden.isdisjoint(messages[-1])


@pytest.mark.asyncio
async def test_reject_and_duplicate_approval_do_not_repeat_paid_work(
    tmp_path: Path,
) -> None:
    factory_calls = 0

    def composer_factory() -> _Composer:
        nonlocal factory_calls
        factory_calls += 1
        return _Composer("supported")

    reject_service = AcademicWorkflowService(
        output_root=tmp_path / "reject",
        adapter_factory=_Adapter,
        composer_factory=composer_factory,
    )
    rejected_id, rejected_messages = await _start(reject_service)
    await reject_service.decision(
        {"session_id": rejected_id, "decision": "reject"},
        _emit_to(rejected_messages),
    )
    assert factory_calls == 0
    assert rejected_messages[-1]["result"] == "rejected"

    composer = _Composer("supported", asyncio.Event())
    approve_service = AcademicWorkflowService(
        output_root=tmp_path / "approve",
        adapter_factory=_Adapter,
        composer_factory=lambda: composer,
    )
    session_id, messages = await _start(approve_service)
    results = await asyncio.gather(
        approve_service.decision(
            {"session_id": session_id, "decision": "approve"}, _emit_to(messages)
        ),
        approve_service.decision(
            {"session_id": session_id, "decision": "approve"}, _emit_to(messages)
        ),
        return_exceptions=True,
    )
    assert composer.calls == 1
    assert sum(isinstance(result, AcademicWorkflowServiceError) for result in results) == 1

    failing = _Composer("supported", error=RuntimeError("private provider detail"))
    failure_service = AcademicWorkflowService(
        output_root=tmp_path / "failure",
        adapter_factory=_Adapter,
        composer_factory=lambda: failing,
    )
    failed_id, failed_messages = await _start(failure_service)
    with pytest.raises(AcademicWorkflowServiceError, match="decision_failed"):
        await failure_service.decision(
            {"session_id": failed_id, "decision": "approve"},
            _emit_to(failed_messages),
        )
    status_text = (
        tmp_path / "failure" / failed_id / "workflow-status.json"
    ).read_text(encoding="utf-8")
    assert '"code": "decision_failed"' in status_text
    assert "private provider detail" not in status_text


@pytest.mark.asyncio
async def test_review_can_be_revised_once_without_rewriting_supported_sections(
    tmp_path: Path,
) -> None:
    composer = _Composer("mixed")
    rewritten: list[str] = []

    def writer_factory(
        draft: WorkflowSectionDraft,
        review: WorkflowSectionCitationReview,
        ids: tuple[str, ...],
    ) -> _Writer:
        rewritten.append(draft.section_id)
        return _Writer(draft)

    service = AcademicWorkflowService(
        output_root=tmp_path,
        adapter_factory=_Adapter,
        composer_factory=lambda: composer,
        writer_factory=writer_factory,
        reviewer_factory=_Reviewer,
    )
    session_id, messages = await _start(service)
    await service.decision(
        {"session_id": session_id, "decision": "approve"}, _emit_to(messages)
    )

    await service.revise({"session_id": session_id}, _emit_to(messages))
    assert messages[-1]["type"] == "academic_ready"
    assert messages[-1]["revision_used"] is True
    assert "section:000001" not in rewritten
    assert len(rewritten) == 7
    with pytest.raises(AcademicWorkflowServiceError, match="revision_unavailable"):
        await service.revise({"session_id": session_id}, _emit_to(messages))


@pytest.mark.asyncio
async def test_human_export_is_deterministic_and_idempotent_without_llm(
    tmp_path: Path,
) -> None:
    composer = _Composer("uncertain")
    service = AcademicWorkflowService(
        output_root=tmp_path,
        adapter_factory=_Adapter,
        composer_factory=lambda: composer,
    )
    session_id, messages = await _start(service)
    await service.decision(
        {"session_id": session_id, "decision": "approve"}, _emit_to(messages)
    )

    await service.export({"session_id": session_id}, _emit_to(messages))
    report = (tmp_path / session_id / "final-report.md").read_text(encoding="utf-8")
    assert MARKER not in report
    assert "[1]" in report
    assert "## 参考文献" in report
    assert messages[-1]["machine_disposition_ready"] is False
    await service.export({"session_id": session_id}, _emit_to(messages))
    assert composer.calls == 1
