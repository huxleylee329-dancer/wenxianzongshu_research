"""Contracts for durable academic-workflow terminal outcomes."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

import gpt_researcher.workflows.academic_writing.workflow_outcome as module
from gpt_researcher.workflows.academic_writing.academic_draft_composer import (
    WorkflowAcademicDraftComposition,
)
from gpt_researcher.workflows.academic_writing.citation_evidence_gate import (
    WorkflowCitationEvidenceGateResult,
)
from gpt_researcher.workflows.academic_writing.citation_reviewer import (
    WorkflowSectionCitationReview,
)
from gpt_researcher.workflows.academic_writing.references_renderer import (
    WorkflowReferencedDraft,
)
from gpt_researcher.workflows.academic_writing.state import (
    AcademicWorkflowRequest,
    WorkflowEvent,
    WorkflowEvidenceSource,
    WorkflowOutline,
    WorkflowOutlineDecisionRecord,
    WorkflowOutlineSection,
    WorkflowResearchEvidence,
    WorkflowSectionDraft,
    WorkflowTopicPlan,
    _outline_digest,
)
from gpt_researcher.workflows.academic_writing.workflow_outcome import (
    AcademicWorkflowPersistentState,
    WorkflowDraftReadyOutcome,
    WorkflowReviewRequiredOutcome,
    _persistent_workflow_to_graph_state,
    _restore_persistent_workflow_state,
)


class _HostileKey:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def __hash__(self) -> int:
        self.calls.append("hash")
        return 1

    def __eq__(self, other: object) -> bool:
        self.calls.append("eq")
        return False

    def __repr__(self) -> str:
        self.calls.append("repr")
        return "hostile"


class _HostileString(str):
    calls: list[str]

    def __new__(cls, value: str, calls: list[str]) -> _HostileString:
        instance = str.__new__(cls, value)
        instance.calls = calls
        return instance

    def __eq__(self, other: object) -> bool:
        self.calls.append("eq")
        return str.__eq__(self, other)

    def __str__(self) -> str:
        self.calls.append("str")
        return str.__str__(self)

    def __repr__(self) -> str:
        self.calls.append("repr")
        return str.__repr__(self)


class _HostileCallable:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def __call__(self, *args: object, **kwargs: object) -> object:
        self.calls.append("call")
        raise AssertionError("instance shadow executed")


def _canonical(value: object) -> bytes:
    dumped = value.model_dump(mode="json")  # type: ignore[attr-defined]
    return json.dumps(
        dumped,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _text_with_json_contribution(size: int, prefix: str = "") -> str:
    prefix_size = len(json.dumps(prefix, ensure_ascii=False).encode("utf-8")) - 2
    remaining = size - prefix_size
    if remaining < 0:
        raise AssertionError("prefix exceeds requested JSON contribution")
    nul_count, remainder = divmod(remaining, 6)
    suffixes = ("", "X", "é", "€", "\U00010000", "\U00010000X")
    result = prefix + "\0" * nul_count + suffixes[remainder]
    assert len(json.dumps(result, ensure_ascii=False).encode("utf-8")) - 2 == size
    return result


def _event(order: int, kind: str, node: str | None) -> WorkflowEvent:
    return WorkflowEvent(
        event_id=f"event:{order:06d}",
        order=order,
        event_type=kind,
        node_id=node,
        attempt=1,
    )


def _artifacts(section_count: int = 1) -> tuple[
    AcademicWorkflowRequest,
    WorkflowTopicPlan,
    WorkflowResearchEvidence,
    WorkflowOutline,
    WorkflowOutlineDecisionRecord,
]:
    request = AcademicWorkflowRequest(
        workflow_mode="academic_langgraph",
        workflow_id="workflow-1",
        thread_id="thread-1",
        run_id="run-1",
        query="Deterministic research",
        report_type="research_report",
        report_source="web",
        tone="objective",
        language="en",
        source_urls=("https://example.test/source",),
        document_urls=(),
        query_domains=("example.test",),
        max_search_results=5,
    )
    plan = WorkflowTopicPlan(
        topic_plan_id="topic-plan:000001",
        workflow_id="workflow-1",
        run_id="run-1",
        attempt=1,
        research_topic="Deterministic research",
        research_questions=("What is deterministic?",),
    )
    source = WorkflowEvidenceSource(
        source_id="evidence-source:000001",
        order=1,
        title="Source",
        url="https://example.test/source",
        candidate_id="candidate-1",
    )
    evidence = WorkflowResearchEvidence(
        evidence_id="evidence:000001",
        topic_plan_id=plan.topic_plan_id,
        attempt=1,
        context_blocks=("Evidence",),
        sources=(source,),
    )
    outline = WorkflowOutline(
        outline_id="outline:000001",
        evidence_id=evidence.evidence_id,
        attempt=1,
        title="Outline",
        sections=tuple(
            WorkflowOutlineSection(
                section_id=f"section:{index:06d}",
                order=index,
                title=f"Section {index}",
                brief=f"Brief {index}",
            )
            for index in range(1, section_count + 1)
        ),
    )
    decision = WorkflowOutlineDecisionRecord(
        decision_id="outline-decision:000001",
        schema_version="1",
        workflow_id="workflow-1",
        thread_id="thread-1",
        run_id="run-1",
        outline_id=outline.outline_id,
        outline_digest=_outline_digest(outline),
        decision="approve",
        actor_assertion="actor",
        attempt=1,
    )
    return request, plan, evidence, outline, decision


def _events() -> tuple[WorkflowEvent, ...]:
    pairs = (
        ("node_started", "topic_planner"),
        ("node_completed", "topic_planner"),
        ("node_started", "research_evidence"),
        ("node_completed", "research_evidence"),
        ("node_started", "outline_writer"),
        ("node_completed", "outline_writer"),
        ("node_started", "outline_approval"),
        ("node_completed", "outline_approval"),
        ("node_started", "academic_draft_composer"),
        ("node_completed", "academic_draft_composer"),
        ("workflow_completed", None),
    )
    return tuple(_event(index, *pair) for index, pair in enumerate(pairs, 1))


def _draft() -> WorkflowSectionDraft:
    return WorkflowSectionDraft(
        outline_id="outline:000001",
        section_id="section:000001",
        attempt=1,
        content="Claim [[cite:evidence-source:000001]]",
    )


def _gate() -> WorkflowCitationEvidenceGateResult:
    return WorkflowCitationEvidenceGateResult(
        outline_id="outline:000001",
        section_ids=("section:000001",),
        cited_source_ids_by_section=(("evidence-source:000001",),),
        attempt=1,
    )


def _review(verdict: str = "unsupported") -> WorkflowSectionCitationReview:
    return WorkflowSectionCitationReview(
        outline_id="outline:000001",
        section_id="section:000001",
        cited_source_ids=("evidence-source:000001",),
        verdict=verdict,
        issues=("insufficient_evidence",) if verdict == "unsupported" else (),
        rationale="Human review required",
        attempt=1,
    )


def _ready() -> WorkflowDraftReadyOutcome:
    return WorkflowDraftReadyOutcome(
        outcome_type="draft_ready",
        referenced_draft=WorkflowReferencedDraft(
            outline_id="outline:000001",
            section_ids=("section:000001",),
            reference_source_ids=("evidence-source:000001",),
            attempt=1,
            content="Body [[cite:evidence-source:000001]]\n\n## References\n\nEntry",
        ),
    )


def _review_required(verdict: str = "unsupported") -> WorkflowReviewRequiredOutcome:
    return WorkflowReviewRequiredOutcome(
        outcome_type="review_required",
        drafts=(_draft(),),
        gate_result=_gate(),
        reviews=(_review(verdict),),
    )


def _state(
    phase: str,
    outcome: WorkflowDraftReadyOutcome | WorkflowReviewRequiredOutcome | None,
    *,
    status: str = "completed",
    section_count: int = 1,
) -> AcademicWorkflowPersistentState:
    request, plan, evidence, outline, decision = _artifacts(section_count)
    return AcademicWorkflowPersistentState(
        schema_version="1",
        workflow_id="workflow-1",
        thread_id="thread-1",
        run_id="run-1",
        phase=phase,
        status=status,
        request=request,
        topic_plan=plan,
        research_evidence=evidence,
        outline=outline,
        outline_decision=decision,
        errors=(),
        events=_events() if phase in ("draft_ready", "review_required") else _events()[:8],
        outcome=outcome,
    )


def test_outcome_dtos_are_strict_frozen_discriminated_and_json_compatible() -> None:
    for outcome in (_ready(), _review_required()):
        restored = type(outcome).model_validate_json(outcome.model_dump_json())
        assert restored == outcome
        assert type(restored) is type(outcome)
        with pytest.raises(ValidationError):
            restored.outcome_type = "other"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        WorkflowDraftReadyOutcome(outcome_type="review_required", referenced_draft=_ready().referenced_draft)
    with pytest.raises((TypeError, ValidationError)):
        WorkflowReviewRequiredOutcome(
            outcome_type="review_required",
            drafts=[_draft()],  # type: ignore[arg-type]
            gate_result=_gate(),
            reviews=(_review(),),
        )


@pytest.mark.parametrize(
    ("phase", "outcome"),
    (("draft_ready", _ready()), ("review_required", _review_required())),
)
def test_terminal_phase_outcome_matrix_and_binding(
    phase: str,
    outcome: WorkflowDraftReadyOutcome | WorkflowReviewRequiredOutcome,
) -> None:
    state = _state(phase, outcome)
    assert state.phase == phase
    assert state.outcome is outcome
    with pytest.raises(ValidationError):
        _state(phase, None)
    other = _review_required() if phase == "draft_ready" else _ready()
    with pytest.raises(ValidationError):
        _state(phase, other)


def test_legacy_missing_and_explicit_null_restore_one_way_canonical() -> None:
    base = _state("outline_approved", None, status="running")
    complete = _persistent_workflow_to_graph_state(base)
    restored_complete = _restore_persistent_workflow_state(complete)
    assert restored_complete.outcome is None
    assert "outcome" in restored_complete.model_fields_set
    legacy = {"workflow": dict(complete["workflow"])}
    legacy["workflow"].pop("outcome")
    restored_legacy = _restore_persistent_workflow_state(legacy)
    assert restored_legacy.outcome is None
    assert "outcome" not in restored_legacy.model_fields_set
    assert _persistent_workflow_to_graph_state(restored_legacy)["workflow"]["outcome"] is None
    assert restored_legacy.schema_version == "1"


def test_ready_checks_only_structure_source_membership_and_binding() -> None:
    state = _state("draft_ready", _ready())
    assert state.outcome.referenced_draft.content.startswith("Body")  # type: ignore[union-attr]
    fabricated = _ready().model_copy(
        update={
            "referenced_draft": _ready().referenced_draft.model_copy(
                update={"content": "Structurally legal fabricated output"}
            )
        }
    )
    assert _state("draft_ready", fabricated).outcome is fabricated
    unknown = _ready().model_copy(
        update={
            "referenced_draft": _ready().referenced_draft.model_copy(
                update={"reference_source_ids": ("evidence-source:000002",)}
            )
        }
    )
    with pytest.raises(ValidationError):
        _state("draft_ready", unknown)


def test_review_required_recomputes_disposition_once_and_rejects_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = module.gate_citation_review_disposition
    calls = 0

    def spy(gate: object, reviews: object) -> object:
        nonlocal calls
        calls += 1
        return original(gate, reviews)  # type: ignore[arg-type]

    monkeypatch.setattr(module, "gate_citation_review_disposition", spy)
    state = _state("review_required", _review_required())
    assert state.outcome.outcome_type == "review_required"
    assert calls == 1
    with pytest.raises(ValidationError):
        _state("review_required", _review_required("supported"))
    assert calls == 2

    invalid_draft = WorkflowSectionDraft(
        outline_id="outline:000001",
        section_id="section:000002",
        attempt=1,
        content="Invalid position",
    )
    invalid_position = _review_required().model_copy(
        update={"drafts": (invalid_draft,)}
    )
    with pytest.raises(ValidationError):
        _state("review_required", invalid_position)
    assert calls == 2

    ids = tuple(f"evidence-source:{index:06d}" for index in range(1, 66))
    gate = WorkflowCitationEvidenceGateResult(
        outline_id="outline:000001",
        section_ids=("section:000001", "section:000002"),
        cited_source_ids_by_section=(ids[:64], ids[64:]),
        attempt=1,
    )
    drafts = (
        _draft(),
        WorkflowSectionDraft(
            outline_id="outline:000001",
            section_id="section:000002",
            attempt=1,
            content="Second section",
        ),
    )
    reviews = tuple(
        WorkflowSectionCitationReview(
            outline_id="outline:000001",
            section_id=section_id,
            cited_source_ids=citations,
            verdict="unsupported",
            issues=("insufficient_evidence",),
            rationale="Human review required",
            attempt=1,
        )
        for section_id, citations in zip(
            gate.section_ids,
            gate.cited_source_ids_by_section,
            strict=True,
        )
    )
    too_many = WorkflowReviewRequiredOutcome(
        outcome_type="review_required",
        drafts=drafts,
        gate_result=gate,
        reviews=reviews,
    )
    with pytest.raises(ValidationError):
        _state("review_required", too_many, section_count=2)
    assert calls == 2


@pytest.mark.parametrize(
    ("phase", "status"),
    (("draft_ready", "running"), ("review_required", "failed")),
)
def test_invalid_terminal_status_and_old_phase_outcome_are_rejected(
    phase: str,
    status: str,
) -> None:
    outcome = _ready() if phase == "draft_ready" else _review_required()
    with pytest.raises(ValidationError):
        _state(phase, outcome, status=status)
    with pytest.raises(ValidationError):
        _state("outline_approved", _ready(), status="running")


def test_persistent_graph_boundary_is_canonical_and_fixed_failure() -> None:
    state = _state("draft_ready", _ready())
    graph = _persistent_workflow_to_graph_state(state)
    restored = _restore_persistent_workflow_state(graph)
    assert restored == state
    assert restored.model_dump(mode="json") == graph["workflow"]
    assert json.loads(restored.model_dump_json())["outcome"]["outcome_type"] == "draft_ready"
    with pytest.raises(module._PersistentOutcomeContractError) as captured:
        _restore_persistent_workflow_state({"workflow": {}})
    assert str(captured.value) == module._ERROR_TEXT
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    traceback = captured.value.__traceback__
    while traceback is not None:
        if traceback.tb_frame.f_globals.get("__name__") == module.__name__:
            assert all(
                value is not graph for value in traceback.tb_frame.f_locals.values()
            )
        traceback = traceback.tb_next

    calls: list[str] = []
    hostile = _HostileKey(calls)
    corrupted = state.model_copy()
    namespace = object.__getattribute__(corrupted, "__dict__")
    saved = dict.pop(namespace, "outcome")
    dict.__setitem__(namespace, hostile, saved)
    calls.clear()
    with pytest.raises(module._PersistentOutcomeContractError):
        _persistent_workflow_to_graph_state(corrupted)
    assert calls == []

    for corruption in ("subclass", "shadow", "unknown"):
        corrupted = state.model_copy(deep=True)
        outcome = corrupted.outcome
        assert type(outcome) is WorkflowDraftReadyOutcome
        referenced = outcome.referenced_draft
        namespace = object.__getattribute__(referenced, "__dict__")
        if corruption == "subclass":
            namespace["reference_source_ids"] = (
                _HostileString("evidence-source:000001", calls),
            )
        elif corruption == "shadow":
            namespace["model_dump"] = _HostileCallable(calls)
        else:
            namespace["reference_source_ids"] = ("evidence-source:000002",)
        calls.clear()
        with pytest.raises(module._PersistentOutcomeContractError):
            _persistent_workflow_to_graph_state(corrupted)
        assert calls == []

    corrupted = state.model_copy()
    fields_set = set(object.__getattribute__(corrupted, "__pydantic_fields_set__"))
    set.add(fields_set, hostile)
    object.__setattr__(corrupted, "__pydantic_fields_set__", fields_set)
    calls.clear()
    with pytest.raises(module._PersistentOutcomeContractError):
        _persistent_workflow_to_graph_state(corrupted)
    assert calls == []


def test_outcome_joint_natural_maxima_are_legal_without_wrapper_caps() -> None:
    ids = tuple(f"evidence-source:{index:06d}" for index in range(1, 65))
    sections = tuple(f"section:{index:06d}" for index in range(1, 13))
    referenced_empty = {
        "outline_id": "outline:000001",
        "section_ids": list(sections),
        "reference_source_ids": list(ids),
        "attempt": 1,
        "content": "",
    }
    fixed = len(
        json.dumps(
            referenced_empty,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )
    referenced = WorkflowReferencedDraft(
        outline_id="outline:000001",
        section_ids=sections,
        reference_source_ids=ids,
        attempt=1,
        content=_text_with_json_contribution(8_596_240 - fixed),
    )
    ready = WorkflowDraftReadyOutcome(
        outcome_type="draft_ready",
        referenced_draft=referenced,
    )
    assert len(_canonical(referenced)) == 8_596_240
    assert len(_canonical(ready)) == 8_596_290

    empty_drafts = [
        {
            "outline_id": "outline:000001",
            "section_id": section,
            "attempt": 1,
            "content": "",
        }
        for section in sections
    ]
    draft_fixed = len(
        json.dumps(
            empty_drafts,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )
    extra = 1_337_616 - draft_fixed
    quotient, remainder = divmod(extra, 12)
    marker = "[[cite:evidence-source:000001]]"
    drafts = tuple(
        WorkflowSectionDraft(
            outline_id="outline:000001",
            section_id=section,
            attempt=1,
            content=_text_with_json_contribution(
                quotient + (1 if index < remainder else 0),
                marker,
            ),
        )
        for index, section in enumerate(sections)
    )
    gate = WorkflowCitationEvidenceGateResult(
        outline_id="outline:000001",
        section_ids=sections,
        cited_source_ids_by_section=tuple(ids for _ in sections),
        attempt=1,
    )
    reviews = tuple(
        WorkflowSectionCitationReview(
            outline_id="outline:000001",
            section_id=section,
            cited_source_ids=ids,
            verdict="unsupported",
            issues=(
                "insufficient_evidence",
                "possible_contradiction",
                "citation_placement_unclear",
            ),
            rationale="\0" * 2048,
            attempt=1,
        )
        for section in sections
    )
    review = WorkflowReviewRequiredOutcome(
        outcome_type="review_required",
        drafts=drafts,
        gate_result=gate,
        reviews=reviews,
    )
    assert len(
        json.dumps(
            [draft.model_dump(mode="json") for draft in drafts],
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ) == 1_337_616
    assert len(_canonical(gate)) == 19_519
    assert len(
        json.dumps(
            [item.model_dump(mode="json") for item in reviews],
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ) == 169_333
    assert len(_canonical(review)) == 1_526_538
