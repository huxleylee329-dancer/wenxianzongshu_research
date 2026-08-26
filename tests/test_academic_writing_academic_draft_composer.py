"""Focused behavior tests for the off-graph academic draft composer."""

from __future__ import annotations

import asyncio
import hashlib
import json
import types

import pytest
from pydantic import ValidationError

from gpt_researcher.workflows.academic_writing import (
    academic_draft_composer as module,
)
from gpt_researcher.workflows.academic_writing.academic_draft_composer import (
    GPTResearcherAcademicDraftComposer,
    WorkflowAcademicDraftComposition,
)
from gpt_researcher.workflows.academic_writing.citation_evidence_gate import (
    WorkflowCitationEvidenceGateResult,
    gate_citation_evidence,
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
from gpt_researcher.workflows.academic_writing.section_merger import (
    WorkflowMergedDraft,
)
from gpt_researcher.workflows.academic_writing.state import (
    AcademicWorkflowRequest,
    AcademicWorkflowState,
    WorkflowEvent,
    WorkflowEvidenceProvenance,
    WorkflowEvidenceSource,
    WorkflowOutline,
    WorkflowOutlineDecisionRecord,
    WorkflowOutlineSection,
    WorkflowResearchEvidence,
    WorkflowSectionDraft,
    WorkflowTopicPlan,
)


_MARKER = "[[cite:evidence-source:000001]]"


def _event(order: int, event_type: str, node_id: str | None) -> WorkflowEvent:
    return WorkflowEvent(
        event_id=f"event:{order:06d}",
        order=order,
        event_type=event_type,
        node_id=node_id,
        attempt=1,
    )


_APPROVE_EVENTS = (
    _event(1, "node_started", "topic_planner"),
    _event(2, "node_completed", "topic_planner"),
    _event(3, "node_started", "research_evidence"),
    _event(4, "node_completed", "research_evidence"),
    _event(5, "node_started", "outline_writer"),
    _event(6, "node_completed", "outline_writer"),
    _event(7, "node_started", "outline_approval"),
    _event(8, "node_completed", "outline_approval"),
    _event(9, "workflow_completed", None),
)


def _source(
    order: int,
    *,
    title: str | None = None,
    url: str | None = None,
) -> WorkflowEvidenceSource:
    return WorkflowEvidenceSource(
        source_id=f"evidence-source:{order:06d}",
        order=order,
        title=f"Source {order}" if title is None else title,
        url=f"https://example.test/{order}" if url is None else url,
        candidate_id=None,
    )


def _section(order: int, *, title: str | None = None) -> WorkflowOutlineSection:
    return WorkflowOutlineSection(
        section_id=f"section:{order:06d}",
        order=order,
        title=f"Section {order}" if title is None else title,
        brief="B",
    )


def _approved_state(
    *,
    section_count: int = 1,
    source_count: int = 1,
    section_titles: tuple[str, ...] | None = None,
    source_title: str | None = None,
    source_url: str | None = None,
) -> AcademicWorkflowState:
    if section_titles is None:
        sections = tuple(_section(order) for order in range(1, section_count + 1))
    else:
        sections = tuple(
            _section(order, title=section_titles[order - 1])
            for order in range(1, section_count + 1)
        )
    sources = tuple(
        _source(
            order,
            title=source_title if order == 1 else None,
            url=source_url if order == 1 else None,
        )
        for order in range(1, source_count + 1)
    )
    provenance = tuple(
        WorkflowEvidenceProvenance(
            source_id=source.source_id,
            evidence_blocks=("E",),
        )
        for source in sources[:64]
    )
    request = AcademicWorkflowRequest(
        workflow_mode="academic_langgraph",
        workflow_id="workflow-1",
        thread_id="thread-1",
        run_id="run-1",
        query="Q",
        report_type="research_report",
        report_source="web",
        tone="objective",
        language="L",
        source_urls=(),
        document_urls=(),
        query_domains=(),
        max_search_results=5,
    )
    topic = WorkflowTopicPlan(
        topic_plan_id="topic-plan:000001",
        workflow_id="workflow-1",
        run_id="run-1",
        attempt=1,
        research_topic="Q",
        research_questions=("R",),
    )
    evidence = WorkflowResearchEvidence(
        evidence_id="evidence:000001",
        topic_plan_id="topic-plan:000001",
        attempt=1,
        context_blocks=("C",),
        sources=sources,
        provenance=provenance,
    )
    outline = WorkflowOutline(
        outline_id="outline:000001",
        evidence_id="evidence:000001",
        attempt=1,
        title="O",
        sections=sections,
    )
    outline_bytes = json.dumps(
        outline.model_dump(mode="json"),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    decision = WorkflowOutlineDecisionRecord(
        decision_id="outline-decision:000001",
        schema_version="1",
        workflow_id="workflow-1",
        thread_id="thread-1",
        run_id="run-1",
        outline_id="outline:000001",
        outline_digest=hashlib.sha256(outline_bytes).hexdigest(),
        decision="approve",
        actor_assertion="actor-A",
        attempt=1,
    )
    return AcademicWorkflowState(
        schema_version="1",
        workflow_id="workflow-1",
        thread_id="thread-1",
        run_id="run-1",
        phase="outline_approved",
        status="completed",
        request=request,
        topic_plan=topic,
        research_evidence=evidence,
        outline=outline,
        outline_decision=decision,
        errors=(),
        events=_APPROVE_EVENTS,
    )


def _drafts(
    section_count: int,
    *,
    content: str | None = None,
) -> tuple[WorkflowSectionDraft, ...]:
    return tuple(
        WorkflowSectionDraft(
            outline_id="outline:000001",
            section_id=f"section:{order:06d}",
            attempt=1,
            content=(f"Claim {order} {_MARKER}" if content is None else content),
        )
        for order in range(1, section_count + 1)
    )


def _issues(verdict: str) -> tuple[str, ...]:
    if verdict == "supported":
        return ()
    if verdict == "uncertain":
        return ("insufficient_evidence",)
    return ("possible_contradiction",)


def _reviews_for_gate(
    gate: WorkflowCitationEvidenceGateResult,
    *,
    verdict: str = "supported",
    rationale: str = "Bounded opinion.",
) -> tuple[WorkflowSectionCitationReview, ...]:
    values: list[WorkflowSectionCitationReview] = []
    index = 0
    while index < len(gate.section_ids):
        values.append(
            WorkflowSectionCitationReview(
                outline_id=gate.outline_id,
                section_id=gate.section_ids[index],
                cited_source_ids=gate.cited_source_ids_by_section[index],
                verdict=verdict,
                issues=_issues(verdict),
                rationale=rationale,
                attempt=1,
            )
        )
        index += 1
    return tuple(values)


class _Sequence:
    def __init__(
        self,
        values: object,
        *,
        events: list[str] | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.values = values
        self.events = [] if events is None else events
        self.error = error
        self.calls = 0

    async def write_sections(self, state: AcademicWorkflowState) -> object:
        assert type(state) is AcademicWorkflowState
        self.calls += 1
        self.events.append("write")
        if self.error is not None:
            raise self.error
        return self.values


class _Reviewer:
    def __init__(
        self,
        *,
        verdict: str = "supported",
        rationale: str = "Bounded opinion.",
        events: list[str] | None = None,
        error: BaseException | None = None,
        invalid: bool = False,
    ) -> None:
        self.verdict = verdict
        self.rationale = rationale
        self.events = [] if events is None else events
        self.error = error
        self.invalid = invalid
        self.calls = 0
        self.gate_checks = 0

    async def review_citations(
        self,
        state: AcademicWorkflowState,
        drafts: tuple[WorkflowSectionDraft, ...],
        gate_result: WorkflowCitationEvidenceGateResult,
    ) -> object:
        self.calls += 1
        self.events.append("review")
        rerun = gate_citation_evidence(state, drafts)
        assert rerun == gate_result
        self.gate_checks += 1
        if self.error is not None:
            raise self.error
        if self.invalid:
            return (object(),)
        return _reviews_for_gate(
            gate_result,
            verdict=self.verdict,
            rationale=self.rationale,
        )


class _CancellingReviewer(_Reviewer):
    def __init__(self, error: asyncio.CancelledError, cancel_at: int) -> None:
        super().__init__()
        self.cancel_error = error
        self.cancel_at = cancel_at
        self.section_calls = 0

    async def review_citations(
        self,
        state: AcademicWorkflowState,
        drafts: tuple[WorkflowSectionDraft, ...],
        gate_result: WorkflowCitationEvidenceGateResult,
    ) -> object:
        rerun = gate_citation_evidence(state, drafts)
        assert rerun == gate_result
        index = 0
        while index < len(drafts):
            self.section_calls += 1
            await asyncio.sleep(0)
            if index == self.cancel_at:
                raise self.cancel_error
            index += 1
        return _reviews_for_gate(gate_result)


def _canonical(value: object) -> bytes:
    if isinstance(value, WorkflowAcademicDraftComposition):
        value = value.model_dump(mode="json")
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _assert_fixed(error: BaseException) -> None:
    assert type(error) is module._AcademicDraftComposerError
    assert str(error) == "academic draft composer failed"
    assert error.__cause__ is None
    assert error.__context__ is None
    assert error.__suppress_context__ is False


def _module_traceback_values(error: BaseException) -> list[object]:
    values: list[object] = []
    traceback = error.__traceback__
    while traceback is not None:
        frame = traceback.tb_frame
        if frame.f_globals.get("__name__") == module.__name__:
            values.extend(frame.f_locals.values())
        traceback = traceback.tb_next
    return values


def _assert_module_frames_do_not_hold(
    error: BaseException,
    targets: tuple[object, ...],
) -> None:
    for value in _module_traceback_values(error):
        assert all(value is not target for target in targets)
        if type(value) is tuple:
            assert all(
                member is not target
                for member in value
                for target in targets
            )
        elif type(value) is list:
            assert all(
                member is not target
                for member in value
                for target in targets
            )
        elif type(value) is dict:
            assert all(
                member is not target
                for member in (*dict.keys(value), *dict.values(value))
                for target in targets
            )


@pytest.mark.asyncio
async def test_ready_pipeline_public_contract_binding_and_nonmutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    state = _approved_state()
    drafts = _drafts(1)
    sequence = _Sequence(drafts, events=events)
    reviewer = _Reviewer(events=events)
    state_before = _canonical(state.model_dump(mode="json"))
    drafts_before = _canonical([draft.model_dump(mode="json") for draft in drafts])
    real_gate = module._gate_citation_evidence
    real_disposition = module._gate_citation_review_disposition
    real_merge = module._merge_sections
    real_render = module._render_references

    def gate(*args: object) -> object:
        events.append("gate")
        return real_gate(*args)

    def disposition(*args: object) -> object:
        events.append("disposition")
        return real_disposition(*args)

    def merge(*args: object) -> object:
        events.append("merge")
        return real_merge(*args)

    def render(*args: object) -> object:
        events.append("render")
        return real_render(*args)

    monkeypatch.setattr(module, "_gate_citation_evidence", gate)
    monkeypatch.setattr(module, "_gate_citation_review_disposition", disposition)
    monkeypatch.setattr(module, "_merge_sections", merge)
    monkeypatch.setattr(module, "_render_references", render)
    result = await GPTResearcherAcademicDraftComposer(
        section_writer_sequence=sequence,
        citation_reviewer=reviewer,
    ).compose(state)

    assert module.__all__ == (
        "WorkflowAcademicDraftComposition",
        "GPTResearcherAcademicDraftComposer",
    )
    assert type(result) is WorkflowAcademicDraftComposition
    assert events == ["write", "gate", "review", "disposition", "merge", "render"]
    assert reviewer.gate_checks == 1
    assert type(result.merged_draft) is WorkflowMergedDraft
    assert type(result.referenced_draft) is WorkflowReferencedDraft
    assert result.referenced_draft.content.startswith(result.merged_draft.content)
    assert result.referenced_draft.reference_source_ids == (
        "evidence-source:000001",
    )
    restored = WorkflowAcademicDraftComposition.model_validate_json(_canonical(result))
    assert restored == result
    with pytest.raises(ValidationError):
        result.reviews = ()  # type: ignore[misc]
    assert _canonical(state.model_dump(mode="json")) == state_before
    assert _canonical([draft.model_dump(mode="json") for draft in drafts]) == drafts_before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("verdict", "expected"),
    (("uncertain", "needs_human_review"), ("unsupported", "blocked")),
)
async def test_non_ready_returns_complete_handoff_without_merge_or_render(
    monkeypatch: pytest.MonkeyPatch,
    verdict: str,
    expected: str,
) -> None:
    state = _approved_state()
    drafts = _drafts(1)
    calls = {"merge": 0, "render": 0}

    def forbidden_merge(*args: object) -> object:
        calls["merge"] += 1
        raise AssertionError

    def forbidden_render(*args: object) -> object:
        calls["render"] += 1
        raise AssertionError

    monkeypatch.setattr(module, "_merge_sections", forbidden_merge)
    monkeypatch.setattr(module, "_render_references", forbidden_render)
    result = await GPTResearcherAcademicDraftComposer(
        section_writer_sequence=_Sequence(drafts),
        citation_reviewer=_Reviewer(verdict=verdict),
    ).compose(state)
    assert result.disposition.disposition == expected
    assert result.merged_draft is None
    assert result.referenced_draft is None
    assert len(result.drafts) == len(result.gate_result.section_ids) == len(result.reviews)
    assert calls == {"merge": 0, "render": 0}


def _direct_values(
    unique_count: int,
    *,
    ready: bool,
) -> dict[str, object]:
    section_count = (unique_count + 63) // 64
    ids = tuple(
        f"evidence-source:{order:06d}" for order in range(1, unique_count + 1)
    )
    citations = tuple(
        ids[index : index + 64] for index in range(0, unique_count, 64)
    )
    gate = WorkflowCitationEvidenceGateResult(
        outline_id="outline:000001",
        section_ids=tuple(
            f"section:{order:06d}" for order in range(1, section_count + 1)
        ),
        cited_source_ids_by_section=citations,
        attempt=1,
    )
    drafts = _drafts(section_count)
    verdict = "supported" if ready else "uncertain"
    reviews = _reviews_for_gate(gate, verdict=verdict)
    disposition_value = "ready" if ready else "needs_human_review"
    disposition = WorkflowCitationReviewDisposition(
        outline_id=gate.outline_id,
        section_ids=gate.section_ids,
        section_dispositions=tuple(disposition_value for _ in gate.section_ids),
        disposition=disposition_value,
        attempt=1,
    )
    merged = None
    referenced = None
    if ready:
        merged = WorkflowMergedDraft(
            outline_id=gate.outline_id,
            section_ids=gate.section_ids,
            attempt=1,
            content="Merged",
        )
        referenced = WorkflowReferencedDraft(
            outline_id=gate.outline_id,
            section_ids=gate.section_ids,
            reference_source_ids=ids,
            attempt=1,
            content="Merged\n\n## References\n\nopaque",
        )
    return {
        "drafts": drafts,
        "gate_result": gate,
        "reviews": reviews,
        "disposition": disposition,
        "merged_draft": merged,
        "referenced_draft": referenced,
    }


@pytest.mark.parametrize("ready", (False, True))
@pytest.mark.parametrize("unique_count", (64, 65, 200))
def test_composition_global_stable_first_wins_limit(
    ready: bool,
    unique_count: int,
) -> None:
    values = _direct_values(unique_count, ready=ready)
    if unique_count == 64:
        result = WorkflowAcademicDraftComposition(**values)
        assert len(result.referenced_draft.reference_source_ids) == 64 if ready else True
        return
    with pytest.raises(ValidationError):
        WorkflowAcademicDraftComposition(**values)


@pytest.mark.parametrize(
    "case",
    (
        "outer_list",
        "extra",
        "mixed_ready_shape",
        "draft_order",
        "review_citations",
        "routing",
        "reference_ids",
        "draft_shadow",
    ),
)
@pytest.mark.asyncio
async def test_dto_and_nested_binding_near_miss_matrix(case: str) -> None:
    values = _direct_values(1, ready=True)
    dynamic_counters: list[dict[str, int]] = []
    if case == "outer_list":
        values["drafts"] = list(values["drafts"])  # type: ignore[arg-type]
    elif case == "extra":
        values["extra"] = None
    elif case == "mixed_ready_shape":
        values["merged_draft"] = None
    elif case == "draft_order":
        values["drafts"] = (
            WorkflowSectionDraft(
                outline_id="outline:000001",
                section_id="section:999999",
                attempt=1,
                content=_MARKER,
            ),
        )
    elif case == "review_citations":
        values["reviews"] = (
            WorkflowSectionCitationReview(
                outline_id="outline:000001",
                section_id="section:000001",
                cited_source_ids=("evidence-source:000002",),
                verdict="supported",
                issues=(),
                rationale="Opinion",
                attempt=1,
            ),
        )
    elif case == "routing":
        values["disposition"] = WorkflowCitationReviewDisposition(
            outline_id="outline:000001",
            section_ids=("section:000001",),
            section_dispositions=("needs_human_review",),
            disposition="needs_human_review",
            attempt=1,
        )
        values["merged_draft"] = None
        values["referenced_draft"] = None
    elif case == "reference_ids":
        values["referenced_draft"] = WorkflowReferencedDraft(
            outline_id="outline:000001",
            section_ids=("section:000001",),
            reference_source_ids=("evidence-source:000002",),
            attempt=1,
            content="Merged\n\n## References\n\nopaque",
        )
    else:
        class HostileMember:
            def __init__(self, counters: dict[str, int]) -> None:
                self._counters = counters

            def __hash__(self) -> int:
                self._counters["hash"] += 1
                return hash("content")

            def __eq__(self, other: object) -> bool:
                self._counters["eq"] += 1
                return False

            def __repr__(self) -> str:
                self._counters["repr"] += 1
                return "hostile-member"

            def __iter__(self) -> object:
                self._counters["iter"] += 1
                return iter(())

            def __getattr__(self, name: str) -> object:
                self._counters["getattr"] += 1
                raise AttributeError(name)

        shadow_values = _direct_values(1, ready=True)
        shadow_draft = shadow_values["drafts"][0]  # type: ignore[index]
        object.__getattribute__(shadow_draft, "__dict__")["model_dump"] = object()
        with pytest.raises((TypeError, ValidationError)):
            WorkflowAcademicDraftComposition(**shadow_values)

        field_values = _direct_values(1, ready=True)
        field_draft = field_values["drafts"][0]  # type: ignore[index]
        fields_set = object.__getattribute__(field_draft, "__pydantic_fields_set__")
        field_counters = {name: 0 for name in ("hash", "eq", "repr", "iter", "getattr")}
        hostile_field = HostileMember(field_counters)
        set.remove(fields_set, "content")
        set.add(fields_set, hostile_field)
        for name in field_counters:
            field_counters[name] = 0
        with pytest.raises((TypeError, ValidationError)):
            WorkflowAcademicDraftComposition(**field_values)
        assert all(count == 0 for count in field_counters.values())

        composer = GPTResearcherAcademicDraftComposer(
            section_writer_sequence=_Sequence(_drafts(1)),
            citation_reviewer=_Reviewer(),
        )
        composer_namespace = object.__getattribute__(composer, "__dict__")
        composer_counters = {
            name: 0 for name in ("hash", "eq", "repr", "iter", "getattr")
        }
        hostile_choice = HostileMember(composer_counters)
        choice = dict.pop(composer_namespace, "_citation_reviewer_choice")
        dict.__setitem__(composer_namespace, hostile_choice, choice)
        for name in composer_counters:
            composer_counters[name] = 0
        with pytest.raises(module._AcademicDraftComposerError) as captured:
            await composer.compose(_approved_state())
        _assert_fixed(captured.value)
        assert all(count == 0 for count in composer_counters.values())

        draft = values["drafts"][0]  # type: ignore[index]
        namespace = object.__getattribute__(draft, "__dict__")
        mapping_counters = {
            name: 0 for name in ("hash", "eq", "repr", "iter", "getattr")
        }
        hostile_key = HostileMember(mapping_counters)
        content = dict.pop(namespace, "content")
        dict.__setitem__(namespace, hostile_key, content)
        for name in mapping_counters:
            mapping_counters[name] = 0
        dynamic_counters.append(mapping_counters)
    with pytest.raises((TypeError, ValidationError)):
        WorkflowAcademicDraftComposition(**values)
    for counters in dynamic_counters:
        assert all(count == 0 for count in counters.values())


@pytest.mark.asyncio
async def test_injected_and_production_choices_are_isolated_and_lazy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _approved_state()
    drafts = _drafts(1)
    sequence = _Sequence(drafts)
    reviewer = _Reviewer()
    calls = {"sequence": 0, "reviewer": 0}

    def sequence_factory() -> _Sequence:
        calls["sequence"] += 1
        return sequence

    def reviewer_factory() -> _Reviewer:
        calls["reviewer"] += 1
        return reviewer

    monkeypatch.setattr(module, "_create_production_section_writer_sequence", sequence_factory)
    monkeypatch.setattr(module, "_create_production_citation_reviewer", reviewer_factory)
    injected = GPTResearcherAcademicDraftComposer(
        section_writer_sequence=sequence,
        citation_reviewer=reviewer,
    )
    assert calls == {"sequence": 0, "reviewer": 0}
    await injected.compose(state)
    assert calls == {"sequence": 0, "reviewer": 0}
    defaulted = GPTResearcherAcademicDraftComposer()
    assert calls == {"sequence": 0, "reviewer": 0}
    await defaulted.compose(state)
    assert calls == {"sequence": 1, "reviewer": 1}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stage",
    (
        "preflight",
        "sequence_construction",
        "sequence",
        "draft_validation",
        "gate",
        "reviewer_construction",
        "reviewer",
        "review_validation",
        "disposition",
        "merge",
        "render",
        "result",
    ),
)
async def test_stage_failure_matrix_is_fixed_and_stops_later_work(
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
) -> None:
    events: list[str] = []
    artifacts: dict[str, object] = {}
    sensitive_content = "stage-sensitive-content " + _MARKER
    state: object = _approved_state()
    drafts: object = _drafts(1, content=sensitive_content)
    sequence_error = RuntimeError("secret sequence") if stage == "sequence" else None
    reviewer_error = RuntimeError("secret reviewer") if stage == "reviewer" else None
    sequence = _Sequence(
        (object(),) if stage == "draft_validation" else drafts,
        events=events,
        error=sequence_error,
    )
    reviewer = _Reviewer(
        rationale=sensitive_content,
        events=events,
        error=reviewer_error,
        invalid=stage == "review_validation",
    )

    real_snapshot = module._snapshot_state
    real_trusted_drafts = module._trusted_drafts
    real_gate = module._gate_citation_evidence
    real_trusted_reviews = module._trusted_reviews
    real_disposition = module._gate_citation_review_disposition
    real_merge = module._merge_sections
    real_render = module._render_references
    real_build = module._build_composition

    def snapshot(value: object) -> object:
        events.append("preflight")
        return real_snapshot(value)

    def trusted_drafts(value: object) -> object:
        events.append("draft_validation")
        artifacts["raw_drafts"] = value
        result = real_trusted_drafts(value)
        artifacts["trusted_drafts"] = result
        return result

    def gate(*args: object) -> object:
        events.append("gate")
        if stage == "gate":
            raise RuntimeError("secret gate")
        result = real_gate(*args)
        artifacts["gate"] = result
        return result

    def trusted_reviews(value: object) -> object:
        events.append("review_validation")
        artifacts["raw_reviews"] = value
        result = real_trusted_reviews(value)
        artifacts["trusted_reviews"] = result
        return result

    def disposition(*args: object) -> object:
        events.append("disposition")
        if stage == "disposition":
            raise RuntimeError("secret route")
        result = real_disposition(*args)
        artifacts["disposition"] = result
        return result

    def merge(*args: object) -> object:
        events.append("merge")
        if stage == "merge":
            raise RuntimeError("secret merge")
        result = real_merge(*args)
        artifacts["merged"] = result
        return result

    def render(*args: object) -> object:
        events.append("render")
        if stage == "render":
            raise RuntimeError("secret render")
        result = real_render(*args)
        artifacts["referenced"] = result
        return result

    def build(*args: object) -> object:
        events.append("result")
        if stage == "result":
            return module._FAILURE
        return real_build(*args)

    monkeypatch.setattr(module, "_snapshot_state", snapshot)
    monkeypatch.setattr(module, "_trusted_drafts", trusted_drafts)
    monkeypatch.setattr(module, "_gate_citation_evidence", gate)
    monkeypatch.setattr(module, "_trusted_reviews", trusted_reviews)
    monkeypatch.setattr(module, "_gate_citation_review_disposition", disposition)
    monkeypatch.setattr(module, "_merge_sections", merge)
    monkeypatch.setattr(module, "_render_references", render)
    monkeypatch.setattr(module, "_build_composition", build)

    composer = GPTResearcherAcademicDraftComposer(
        section_writer_sequence=sequence,
        citation_reviewer=reviewer,
    )
    if stage == "preflight":
        state = object()
    elif stage == "sequence_construction":
        composer = GPTResearcherAcademicDraftComposer(citation_reviewer=reviewer)

        def failing_sequence_factory() -> object:
            events.append("sequence_construction")
            raise RuntimeError("secret construction")

        monkeypatch.setattr(
            module, "_create_production_section_writer_sequence", failing_sequence_factory
        )
    elif stage == "reviewer_construction":
        composer = GPTResearcherAcademicDraftComposer(section_writer_sequence=sequence)

        def failing_reviewer_factory() -> object:
            events.append("reviewer_construction")
            raise RuntimeError("secret construction")

        monkeypatch.setattr(
            module, "_create_production_citation_reviewer", failing_reviewer_factory
        )
    with pytest.raises(module._AcademicDraftComposerError) as captured:
        await composer.compose(state)  # type: ignore[arg-type]
    _assert_fixed(captured.value)
    assert "secret" not in str(captured.value)
    expected_events = {
        "preflight": ("preflight",),
        "sequence_construction": ("preflight", "sequence_construction"),
        "sequence": ("preflight", "write"),
        "draft_validation": ("preflight", "write", "draft_validation"),
        "gate": ("preflight", "write", "draft_validation", "gate"),
        "reviewer_construction": (
            "preflight",
            "write",
            "draft_validation",
            "gate",
            "reviewer_construction",
        ),
        "reviewer": (
            "preflight",
            "write",
            "draft_validation",
            "gate",
            "review",
        ),
        "review_validation": (
            "preflight",
            "write",
            "draft_validation",
            "gate",
            "review",
            "review_validation",
        ),
        "disposition": (
            "preflight",
            "write",
            "draft_validation",
            "gate",
            "review",
            "review_validation",
            "disposition",
        ),
        "merge": (
            "preflight",
            "write",
            "draft_validation",
            "gate",
            "review",
            "review_validation",
            "disposition",
            "merge",
        ),
        "render": (
            "preflight",
            "write",
            "draft_validation",
            "gate",
            "review",
            "review_validation",
            "disposition",
            "merge",
            "render",
        ),
        "result": (
            "preflight",
            "write",
            "draft_validation",
            "gate",
            "review",
            "review_validation",
            "disposition",
            "merge",
            "render",
            "result",
        ),
    }[stage]
    all_events = (
        "preflight",
        "sequence_construction",
        "write",
        "draft_validation",
        "gate",
        "reviewer_construction",
        "review",
        "review_validation",
        "disposition",
        "merge",
        "render",
        "result",
    )
    assert tuple(events) == expected_events
    assert {name: events.count(name) for name in all_events} == {
        name: int(name in expected_events) for name in all_events
    }
    targets: list[object] = [state, drafts, sensitive_content]
    if type(drafts) is tuple:
        targets.extend(drafts)
    for artifact in artifacts.values():
        targets.append(artifact)
        if type(artifact) is tuple:
            targets.extend(artifact)
    _assert_module_frames_do_not_hold(captured.value, tuple(targets))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stage",
    (
        "sequence_construction",
        "sequence_await",
        "reviewer_construction",
        "review_first",
        "review_middle",
        "review_last",
    ),
)
async def test_cancellation_is_bare_and_composer_frames_release_artifacts(
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
) -> None:
    section_count = 3 if stage.startswith("review_") else 1
    state = _approved_state(section_count=section_count)
    drafts = _drafts(section_count)
    cancellation = asyncio.CancelledError("cancel-sentinel", stage)
    sequence = _Sequence(
        drafts,
        error=cancellation if stage == "sequence_await" else None,
    )
    cancel_index = {"review_first": 0, "review_middle": 1, "review_last": 2}.get(
        stage, 0
    )
    reviewer: object = (
        _CancellingReviewer(cancellation, cancel_index)
        if stage.startswith("review_")
        else _Reviewer()
    )
    composer = GPTResearcherAcademicDraftComposer(
        section_writer_sequence=sequence,
        citation_reviewer=reviewer,  # type: ignore[arg-type]
    )
    if stage == "sequence_construction":
        composer = GPTResearcherAcademicDraftComposer(citation_reviewer=_Reviewer())

        def cancelled_sequence_factory() -> object:
            raise cancellation

        monkeypatch.setattr(
            module,
            "_create_production_section_writer_sequence",
            cancelled_sequence_factory,
        )
    elif stage == "reviewer_construction":
        composer = GPTResearcherAcademicDraftComposer(section_writer_sequence=sequence)

        def cancelled_reviewer_factory() -> object:
            raise cancellation

        monkeypatch.setattr(
            module,
            "_create_production_citation_reviewer",
            cancelled_reviewer_factory,
        )
    with pytest.raises(asyncio.CancelledError) as captured:
        await composer.compose(state)
    assert captured.value is cancellation
    assert captured.value.args == ("cancel-sentinel", stage)
    _assert_module_frames_do_not_hold(captured.value, (state, drafts, *drafts))


@pytest.mark.asyncio
async def test_whole_call_retry_restarts_writer_and_reviewer_cost() -> None:
    state = _approved_state()
    drafts = _drafts(1)
    sequence = _Sequence(drafts)
    reviewer = _Reviewer(error=RuntimeError("first failure"))
    composer = GPTResearcherAcademicDraftComposer(
        section_writer_sequence=sequence,
        citation_reviewer=reviewer,
    )
    with pytest.raises(module._AcademicDraftComposerError):
        await composer.compose(state)
    reviewer.error = None
    result = await composer.compose(state)
    assert type(result) is WorkflowAcademicDraftComposition
    assert sequence.calls == 2
    assert reviewer.calls == 2


def _max_content() -> str:
    return _MARKER + "\0" * 8155 + "😀" * (24576 - len(_MARKER) - 8155)


def _max_ready_state() -> AcademicWorkflowState:
    titles = ("😀" * 64218,) + tuple(
        chr(0x10000 + index) for index in range(11)
    )
    assert sum(len(title) for title in titles) == 64229
    return _approved_state(
        section_count=12,
        section_titles=titles,
        source_title="😀" * 256 + "\0" * 256,
        source_url="😀",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("ready", "expected"),
    ((False, 1526996), (True, 4791994)),
)
async def test_joint_public_maximum_and_direct_cap_plus_one(
    ready: bool,
    expected: int,
) -> None:
    state = _max_ready_state() if ready else _approved_state(section_count=12)
    drafts = _drafts(12, content=_max_content())
    reviewer = _Reviewer(
        verdict="supported" if ready else "uncertain",
        rationale="\0" * 2048,
    )
    if not ready:
        original = reviewer.review_citations

        async def all_issues(*args: object) -> object:
            values = await original(*args)  # type: ignore[arg-type]
            return tuple(
                WorkflowSectionCitationReview(
                    outline_id=review.outline_id,
                    section_id=review.section_id,
                    cited_source_ids=review.cited_source_ids,
                    verdict="uncertain",
                    issues=(
                        "insufficient_evidence",
                        "possible_contradiction",
                        "citation_placement_unclear",
                    ),
                    rationale=review.rationale,
                    attempt=1,
                )
                for review in values
            )

        reviewer.review_citations = all_issues  # type: ignore[method-assign]
    result = await GPTResearcherAcademicDraftComposer(
        section_writer_sequence=_Sequence(drafts),
        citation_reviewer=reviewer,
    ).compose(state)
    assert len(_canonical(result)) == expected
    if ready:
        assert len(result.merged_draft.content) == 359223
        assert len(result.referenced_draft.content) == 361091
    assert 24672 + 88 + 5 * 8155 == 65535

    values = result.model_dump(mode="python")
    first = result.drafts[0]
    content = first.content
    first_astral = content.index("😀")
    second_astral = content.index("😀", first_astral + 1)
    enlarged = (
        content[:first_astral]
        + "\0"
        + content[first_astral + 1 : second_astral]
        + "€"
        + content[second_astral + 1 :]
    )
    replacement = WorkflowSectionDraft(
        outline_id=first.outline_id,
        section_id=first.section_id,
        attempt=1,
        content=enlarged,
    )
    replacement_drafts = (replacement,) + result.drafts[1:]
    values["drafts"] = tuple(
        draft.model_dump(mode="python") for draft in replacement_drafts
    )
    assert len(_canonical(values)) == expected + 1
    with pytest.raises(ValidationError):
        WorkflowAcademicDraftComposition.model_validate_json(_canonical(values))
