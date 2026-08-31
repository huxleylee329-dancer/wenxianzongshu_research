"""Focused, offline tests for the browser academic workflow service."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from backend.server.academic_workflow_service import (
    AcademicWorkflowService,
    AcademicWorkflowServiceError,
    _RevisionClient,
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
from backend.server import academic_workflow_service as service_module
from gpt_researcher.workflows.academic_writing import citation_reviewer as reviewer_module


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
    session = service._session(session_id)
    assert session.current_version == 2
    assert session.versions[2].composition is session.composition
    assert messages[-1]["draft_workspace"]["current_version"] == 2
    with pytest.raises(AcademicWorkflowServiceError, match="revision_unavailable"):
        await service.revise({"session_id": session_id}, _emit_to(messages))


@pytest.mark.asyncio
async def test_failed_revision_is_retryable_and_does_not_consume_the_pass(
    tmp_path: Path,
) -> None:
    composer = _Composer("mixed")
    factory_calls = 0

    class _FailingWriter:
        async def write_section(
            self, state: object, section_id: str
        ) -> WorkflowSectionDraft:
            raise RuntimeError("private revision detail")

    def writer_factory(
        draft: WorkflowSectionDraft,
        review: WorkflowSectionCitationReview,
        ids: tuple[str, ...],
    ) -> _Writer | _FailingWriter:
        nonlocal factory_calls
        factory_calls += 1
        if factory_calls == 1:
            return _FailingWriter()
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

    with pytest.raises(AcademicWorkflowServiceError, match="revision_failed"):
        await service.revise({"session_id": session_id}, _emit_to(messages))

    await service.revise({"session_id": session_id}, _emit_to(messages))
    assert messages[-1]["type"] == "academic_ready"
    assert messages[-1]["revision_used"] is True
    assert factory_calls == 8

    status_text = (tmp_path / session_id / "workflow-status.json").read_text(
        encoding="utf-8"
    )
    assert '"stage": "revision"' in status_text
    assert '"code": "revision_failed"' in status_text
    assert "private revision detail" not in status_text


@pytest.mark.asyncio
async def test_revision_client_exposes_only_original_citations_and_provenance() -> None:
    captured: dict[str, str] = {}

    class _Client:
        async def complete(self, *, system_message: str, user_message: str) -> str:
            captured["system_message"] = system_message
            captured["user_message"] = user_message
            return '{"content":"ok"}'

    draft = WorkflowSectionDraft(
        outline_id="outline:000001",
        section_id="section:000001",
        attempt=1,
        content=f"Original {MARKER}",
    )
    review = WorkflowSectionCitationReview(
        outline_id="outline:000001",
        section_id="section:000001",
        cited_source_ids=(SOURCE_ID,),
        verdict="uncertain",
        issues=("insufficient_evidence",),
        rationale="Narrow the claim.",
        attempt=1,
    )
    client = _RevisionClient(
        _Client(),
        draft,
        review,
        ("evidence-source:000002", SOURCE_ID),
        ("trusted block two", "trusted block one"),
    )
    await client.complete(
        system_message="base",
        user_message=(
            '{"context_blocks":["untrusted"],"evidence_sources":['
            '{"citation_marker":"[[cite:evidence-source:000001]]",'
            '"source_id":"evidence-source:000001","title":"one","url":"one"},'
            '{"citation_marker":"[[cite:evidence-source:000002]]",'
            '"source_id":"evidence-source:000002","title":"two","url":"two"}]}'
        ),
    )

    projected = __import__("json").loads(captured["user_message"])
    assert projected["context_blocks"] == ["trusted block two", "trusted block one"]
    assert [
        source["source_id"] for source in projected["evidence_sources"]
    ] == ["evidence-source:000002", SOURCE_ID]
    assert projected["original_section_citation_markers"] == [
        "[[cite:evidence-source:000002]]",
        MARKER,
    ]
    assert projected["existing_section_content"] == draft.content
    assert captured["system_message"].startswith("base")


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


def _feedback_request(session_id: str, base: int = 1, token: str = "request-0000000001") -> dict[str, object]:
    return {
        "session_id": session_id, "base_version": base, "request_id": token,
        "section_ids": ["section:000001"], "feedback": "简明比较，不要泛泛描述。",
        "section_feedback": {"section:000001": "解释局限，并保留有证据的结论。"},
    }


async def _feedback_service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, verdict: str = "supported"):
    calls: list[dict[str, object]] = []

    class Client:
        async def complete(self, *, system_message: str, user_message: str) -> str:
            payload = json.loads(user_message)
            calls.append(payload)
            assert "editorial requests, not factual evidence" in system_message
            assert payload["human_feedback"]["global"] or payload["human_feedback"]["section"]
            return json.dumps({"content": f"Edited version {len(calls)}. {MARKER}"})

    monkeypatch.setattr(service_module, "_create_production_section_writer_client", Client)
    service = AcademicWorkflowService(
        output_root=tmp_path, adapter_factory=_Adapter,
        composer_factory=lambda: _Composer(verdict), reviewer_factory=_Reviewer,
    )
    session_id, messages = await _start(service)
    await service.decision({"session_id": session_id, "decision": "approve"}, _emit_to(messages))
    return service, session_id, messages, calls


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["json", "echo", "coherence", "provider", "factory"])
async def test_default_composer_preserves_drafts_when_one_model_review_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    writer_calls = []
    reviewer_calls = []
    factory_calls = []

    class Writer:
        def __init__(self, state):
            pass

        async def write_section(self, state, section_id):
            writer_calls.append(section_id)
            return WorkflowSectionDraft(
                outline_id=state.outline.outline_id, section_id=section_id,
                attempt=1, content=f"Claim {MARKER}",
            )

    class Client:
        async def complete(self, *, system_message, user_message):
            payload = json.loads(user_message)
            section_id = payload["section_id"]
            reviewer_calls.append(section_id)
            response = dict(
                section_id=section_id, cited_source_ids=payload["cited_source_ids"],
                verdict="supported", issues=[], rationale="Supported model opinion.",
            )
            if section_id == "section:000002":
                if failure == "json":
                    return "not-json SECRET_RESPONSE"
                if failure == "echo":
                    response["section_id"] = "section:000001"
                if failure == "coherence":
                    response["issues"] = ["insufficient_evidence"]
                if failure == "provider":
                    raise RuntimeError("SECRET_PROVIDER_DETAIL")
            return json.dumps(response)

    def factory():
        factory_calls.append(1)
        if failure == "factory" and len(factory_calls) == 2:
            raise RuntimeError("SECRET_FACTORY_DETAIL")
        return Client()

    monkeypatch.setattr(service_module, "_PreparedSectionWriter", Writer)
    monkeypatch.setattr(reviewer_module, "_create_production_citation_reviewer_client", factory)
    service = AcademicWorkflowService(output_root=tmp_path, adapter_factory=_Adapter)
    sid, messages = await _start(service)
    await service.decision({"session_id": sid, "decision": "approve"}, _emit_to(messages))
    session = service._session(sid)
    assert len(writer_calls) == len(factory_calls) == 8
    assert len(reviewer_calls) == (7 if failure == "factory" else 8)
    assert session.state.phase == "review_required" and session.state.status == "completed"
    assert session.composition.reviews[1].verdict == "uncertain"
    assert "自动引用审核未完成" in session.composition.reviews[1].rationale
    assert all(review.verdict == "supported" for i, review in enumerate(session.composition.reviews) if i != 1)
    assert messages[-1]["type"] == "academic_review_required"
    assert messages[-1]["draft_workspace"]["machine_ready"] is False
    assert len(messages[-1]["draft_workspace"]["sections"]) == 8
    assert (tmp_path / sid / "unreviewed-draft.md").exists()
    status = (tmp_path / sid / "workflow-status.json").read_text(encoding="utf-8")
    assert "reviewer_response_invalid" in status or "reviewer_execution_failed" in status
    assert "SECRET" not in status + json.dumps(messages)
    await service.export({"session_id": sid, "version": 1}, _emit_to(messages))
    assert (tmp_path / sid / "versions/v0001/final-report.md").exists()
    assert len(writer_calls) == len(factory_calls) == 8


@pytest.mark.asyncio
async def test_web_review_keeps_preflight_strict_and_propagates_same_cancellation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, sid, messages, _ = await _feedback_service(tmp_path, monkeypatch)
    session = service._session(sid)
    execution = service_module._execution_state(session.state)
    composition = session.composition
    reviewer = service_module._WebCitationReviewer(session.recorder, _emit_to(messages))
    calls = []
    factories = []
    cancellation = asyncio.CancelledError("cancel-review")

    class Client:
        def __init__(self):
            factories.append(1)

        async def complete(self, **kwargs):
            calls.append(1)
            raise cancellation

    monkeypatch.setattr(reviewer_module, "_create_production_citation_reviewer_client", Client)
    bad_drafts = (composition.drafts[0].model_copy(update={"content": "No citation"}),) + composition.drafts[1:]
    with pytest.raises(AcademicWorkflowServiceError, match="citation_plan_invalid"):
        await reviewer.review_citations(execution, bad_drafts, composition.gate_result)
    assert not calls and not factories
    with pytest.raises(asyncio.CancelledError) as caught:
        await reviewer.review_citations(execution, composition.drafts, composition.gate_result)
    assert caught.value is cancellation and caught.value.args == ("cancel-review",)
    assert calls == factories == [1]
    assert session.composition is composition
    assert session.pending_version is None
    assert "unavailable" not in (tmp_path / sid / "workflow-status.json").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_feedback_candidate_survives_unavailable_default_review_without_rewriting_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, sid, messages, writes = await _feedback_service(tmp_path, monkeypatch)
    session = service._session(sid)
    original = session.composition
    calls = []

    class Client:
        async def complete(self, *, system_message, user_message):
            payload = json.loads(user_message)
            calls.append(payload["section_id"])
            if payload["section_id"] == "section:000001":
                return "{invalid json"
            return json.dumps(dict(
                section_id=payload["section_id"], cited_source_ids=payload["cited_source_ids"],
                verdict="supported", issues=[], rationale="Valid reviewer opinion.",
            ))

    monkeypatch.setattr(reviewer_module, "_create_production_citation_reviewer_client", Client)
    service._reviewer_factory = None
    request = _feedback_request(sid)
    await service.revise(request, _emit_to(messages))
    assert len(writes) == 1 and len(calls) == 8
    assert session.composition is original and session.pending_version == 2
    candidate = session.versions[2].composition
    assert candidate.drafts[1:] == original.drafts[1:]
    assert candidate.reviews[0].verdict == "uncertain"
    assert "不是模型审核结论" in candidate.reviews[0].rationale
    assert messages[-1]["type"] == "academic_revision_preview"
    await service.revise(request, _emit_to(messages))
    assert len(writes) == 1 and len(calls) == 8
    await service.revision_decision(
        {"session_id": sid, "base_version": 1, "version": 2, "decision": "accept"},
        _emit_to(messages),
    )
    assert session.composition is candidate
    assert not messages[-1]["draft_workspace"]["machine_ready"]


@pytest.mark.asyncio
@pytest.mark.parametrize("verdict", ["supported", "uncertain"])
async def test_feedback_edits_supported_sections_and_keeps_versions_until_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, verdict: str,
) -> None:
    service, sid, messages, calls = await _feedback_service(tmp_path, monkeypatch, verdict)
    session = service._session(sid)
    original = session.composition
    checkpoint = session.state
    original_file = (tmp_path / sid / "versions/v0001/composition.json").read_bytes()
    assert messages[-1]["draft_workspace"]["current_version"] == 1
    request = _feedback_request(sid)
    await service.revise(request, _emit_to(messages))
    assert len(calls) == 1
    assert calls[0]["human_feedback"] == {
        "global": request["feedback"], "section": request["section_feedback"]["section:000001"]
    }
    assert calls[0]["existing_section_content"] == original.drafts[0].content
    assert [item["source_id"] for item in calls[0]["evidence_sources"]] == [SOURCE_ID]
    assert session.composition is original
    assert session.current_version == 1 and session.pending_version == 2
    assert session.versions[2].composition.drafts[1:] == original.drafts[1:]
    assert messages[-1]["type"] == "academic_revision_preview"
    await service.revise(request, _emit_to(messages))
    assert len(calls) == 1
    with pytest.raises(AcademicWorkflowServiceError, match="revision_pending"):
        await service.revise(_feedback_request(sid, token="request-0000000002"), _emit_to(messages))
    with pytest.raises(AcademicWorkflowServiceError, match="revision_pending"):
        await service.export({"session_id": sid, "version": 1}, _emit_to(messages))
    decision = {"session_id": sid, "base_version": 1, "version": 2, "decision": "accept"}
    await service.revision_decision(decision, _emit_to(messages))
    await service.revision_decision(decision, _emit_to(messages))
    assert session.current_version == 2 and session.pending_version is None
    assert len(calls) == 1
    await service.export({"session_id": sid, "version": 2}, _emit_to(messages))
    assert "Edited version 1" in messages[-1]["markdown"]
    assert "[[cite:" not in messages[-1]["markdown"]
    assert "[1]" in messages[-1]["markdown"]
    assert "/versions/v0002/final-report.md" in messages[-1]["url"]

    await service.revise(_feedback_request(sid, 2, "request-0000000003"), _emit_to(messages))
    assert calls[1]["existing_section_content"] == session.versions[2].composition.drafts[0].content
    await service.revision_decision(
        {"session_id": sid, "base_version": 2, "version": 3, "decision": "discard"}, _emit_to(messages)
    )
    assert session.current_version == 2
    assert session.versions[3].decision == "discarded"
    await service.revise(_feedback_request(sid, 2, "request-0000000004"), _emit_to(messages))
    assert session.pending_version == 4 and len(calls) == 3
    assert (tmp_path / sid / "versions/v0001/composition.json").read_bytes() == original_file
    assert session.state is checkpoint


@pytest.mark.asyncio
@pytest.mark.parametrize("change,code", [
    ({"section_ids": []}, "invalid_sections"),
    ({"section_ids": ["section:999999"]}, "invalid_feedback"),
    ({"section_ids": ["section:000001", "section:000001"]}, "invalid_sections"),
    ({"feedback": "", "section_feedback": {}}, "invalid_feedback"),
    ({"feedback": "a" * 4001}, "invalid_feedback"),
    ({"section_feedback": {"section:000001": "a" * 2001}}, "invalid_feedback"),
    ({"base_version": True}, "invalid_request"),
    ({"base_version": 9}, "stale_version"),
    ({"request_id": "bad"}, "invalid_request"),
])
async def test_invalid_feedback_never_enters_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: dict[str, object], code: str,
) -> None:
    service, sid, messages, calls = await _feedback_service(tmp_path, monkeypatch)
    request = {**_feedback_request(sid), **change}
    with pytest.raises(AcademicWorkflowServiceError, match=code):
        await service.revise(request, _emit_to(messages))
    assert calls == []
    assert service._session(sid).current_version == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["writer", "reviewer", "cancel", "storage", "index", "recorder"])
async def test_feedback_failure_preserves_current_and_allows_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    service, sid, messages, calls = await _feedback_service(tmp_path, monkeypatch)
    session = service._session(sid)
    original = session.composition
    original_status = session.status
    working_client = service_module._create_production_section_writer_client
    save_version = service._save_version
    save_index = service._save_version_index
    record = session.recorder.record
    error = asyncio.CancelledError("cancelled") if failure == "cancel" else RuntimeError("private detail")

    class BrokenClient:
        async def complete(self, **kwargs):
            raise error

    class BrokenReviewer:
        async def review_citations(self, *args):
            raise error

    if failure in ("writer", "cancel"):
        monkeypatch.setattr(service_module, "_create_production_section_writer_client", BrokenClient)
    elif failure == "reviewer":
        service._reviewer_factory = BrokenReviewer
    elif failure == "storage":
        def broken_save(*args):
            raise error
        monkeypatch.setattr(service, "_save_version", broken_save)
    elif failure == "index":
        def broken_index(*args):
            raise error
        monkeypatch.setattr(service, "_save_version_index", broken_index)
    else:
        def broken_record(stage, status, code=None):
            if stage == "feedback_revision" and status == "completed":
                raise error
            return record(stage, status, code)
        monkeypatch.setattr(session.recorder, "record", broken_record)
    expected = asyncio.CancelledError if failure == "cancel" else AcademicWorkflowServiceError
    with pytest.raises(expected):
        await service.revise(_feedback_request(sid), _emit_to(messages))
    assert session.composition is original and session.current_version == 1
    assert session.pending_version is None and len(session.versions) == 1
    assert session.status == original_status
    manifest = json.loads((tmp_path / sid / "versions.json").read_text())
    assert manifest["current_version"] == 1 and manifest["pending_version"] is None
    monkeypatch.setattr(service_module, "_create_production_section_writer_client", working_client)
    monkeypatch.setattr(service, "_save_version", save_version)
    monkeypatch.setattr(service, "_save_version_index", save_index)
    monkeypatch.setattr(session.recorder, "record", record)
    service._reviewer_factory = _Reviewer
    await service.revise(_feedback_request(sid), _emit_to(messages))
    assert session.pending_version == 2
    assert "private detail" not in (tmp_path / sid / "workflow-status.json").read_text()


@pytest.mark.asyncio
async def test_feedback_delivery_failure_and_duplicate_request_do_not_repeat_paid_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, sid, messages, calls = await _feedback_service(tmp_path, monkeypatch)

    async def disconnected(message):
        if message["type"] == "academic_revision_preview":
            raise ConnectionError("disconnected")

    with pytest.raises(ConnectionError):
        await service.revise(_feedback_request(sid), disconnected)
    assert service._session(sid).pending_version == 2
    await service.revise(_feedback_request(sid), _emit_to(messages))
    assert len(calls) == 1
    with pytest.raises(AcademicWorkflowServiceError, match="revision_request_conflict"):
        await service.revise({**_feedback_request(sid), "feedback": "different"}, _emit_to(messages))
    with pytest.raises(AcademicWorkflowServiceError, match="stale_version"):
        await service.revision_decision(
            {"session_id": sid, "base_version": 9, "version": 2, "decision": "accept"}, _emit_to(messages)
        )


@pytest.mark.asyncio
async def test_section_only_feedback_and_websocket_dispatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "backend"))
    from backend.server import server_utils
    service, sid, messages, calls = await _feedback_service(tmp_path, monkeypatch)
    monkeypatch.setattr(server_utils, "academic_workflow_service", service)

    class Socket:
        async def send_json(self, message):
            messages.append(message)

    payload = {**_feedback_request(sid), "feedback": ""}
    await server_utils.handle_academic_command(Socket(), "academic_revise " + json.dumps(payload))
    assert messages[-1]["type"] == "academic_revision_preview"
    assert len(calls) == 1
    await server_utils.handle_academic_command(Socket(), "academic_revision_decision " + json.dumps({
        "session_id": sid, "base_version": 1, "version": 2, "decision": "discard",
    }))
    assert messages[-1]["draft_workspace"]["current_version"] == 1
    await server_utils.handle_academic_command(Socket(), "academic_draft_state " + json.dumps({"session_id": sid}))
    assert messages[-1]["draft_workspace"]["pending"] is None
    await server_utils.handle_academic_command(Socket(), "academic_revise " + json.dumps({**payload, "feedback": "x"}))
    assert messages[-1] == {"type": "academic_error", "code": "revision_request_conflict"}

    class DisconnectedSocket:
        async def send_json(self, message):
            if message["type"] in ("academic_revision_preview", "academic_error"):
                raise ConnectionError("closed")

    await server_utils.handle_academic_command(DisconnectedSocket(), "academic_revise " + json.dumps({
        **payload, "request_id": "request-0000000002",
    }))
    assert service._session(sid).pending_version == 3 and len(calls) == 2
    await server_utils.handle_academic_command(DisconnectedSocket(), "academic_revise invalid-json")
    await server_utils.handle_academic_command(Socket(), "academic_draft_state " + json.dumps({"session_id": sid}))
    assert messages[-1]["draft_workspace"]["pending"]["version"] == 3
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_failed_version_adoption_keeps_pending_candidate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service, sid, messages, calls = await _feedback_service(tmp_path, monkeypatch)
    await service.revise(_feedback_request(sid), _emit_to(messages))
    save_index = service._save_version_index

    def fail_save(session):
        raise OSError("disk")

    monkeypatch.setattr(service, "_save_version_index", fail_save)
    decision = {"session_id": sid, "base_version": 1, "version": 2, "decision": "accept"}
    with pytest.raises(AcademicWorkflowServiceError, match="revision_decision_failed"):
        await service.revision_decision(decision, _emit_to(messages))
    session = service._session(sid)
    assert session.current_version == 1 and session.pending_version == 2
    assert session.versions[2].decision == "pending"
    monkeypatch.setattr(service, "_save_version_index", save_index)
    await service.revision_decision(decision, _emit_to(messages))
    assert session.current_version == 2 and len(calls) == 1
