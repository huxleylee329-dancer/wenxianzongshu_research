from __future__ import annotations

import ast
import asyncio
import gc
import inspect
import json
import types
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command, Interrupt, PregelTask, StateSnapshot

from gpt_researcher.workflows.academic_writing import graph as graph_module
from gpt_researcher.workflows.academic_writing import nodes as nodes_module
from gpt_researcher.workflows.academic_writing import report_profiles as profiles_module
from gpt_researcher.workflows.academic_writing import state as state_module
from gpt_researcher.workflows.academic_writing.adapters import AcademicWritingAdapter
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
from gpt_researcher.workflows.academic_writing.section_merger import (
    WorkflowMergedDraft,
)
from gpt_researcher.workflows.academic_writing.state import (
    AcademicWorkflowRequest,
    AcademicWorkflowIdentity,
    AdapterFailure,
    WorkflowEvidenceSource,
    WorkflowOutline,
    WorkflowOutlineSection,
    WorkflowResearchEvidence,
    WorkflowSectionDraft,
    WorkflowTopicPlan,
)


_FIXED_MODE = "stem_literature_review"
_FIXED_LOCALE = "zh-CN"
_FIXED_PROFILE = profiles_module._get_report_profile(_FIXED_MODE)


def _fixed_outline(
    *,
    evidence_id: str = "evidence:000001",
    title: str = "Root topic",
) -> WorkflowOutline:
    if _FIXED_PROFILE is None:  # pragma: no cover - frozen catalog guard
        raise AssertionError("fixed profile missing")
    return WorkflowOutline(
        outline_id="outline:000001",
        evidence_id=evidence_id,
        attempt=1,
        title=title,
        sections=tuple(
            WorkflowOutlineSection(
                section_id=f"section:{order:06d}",
                order=order,
                title=section_title,
                brief="Scope",
                section_role=section_role,
            )
            for order, (section_role, section_title) in enumerate(_FIXED_PROFILE, 1)
        ),
        report_mode=_FIXED_MODE,
        report_locale=_FIXED_LOCALE,
    )


_COMMAND_FIELDS = (
    "schema_version",
    "workflow_id",
    "thread_id",
    "run_id",
    "outline_id",
    "outline_digest",
    "decision",
    "actor_assertion",
)
_INTERRUPT_KEYS = (
    "allowed_decisions",
    "outline_digest",
    "outline_id",
    "run_id",
    "schema_version",
    "thread_id",
    "workflow_id",
)
_DEFAULT_OUTLINE_DIGEST = state_module._outline_digest(_fixed_outline())
_DEFAULT_INTERRUPT_PAYLOAD = {
    "allowed_decisions": ["approve", "reject"],
    "outline_digest": _DEFAULT_OUTLINE_DIGEST,
    "outline_id": "outline:000001",
    "run_id": "run-1",
    "schema_version": "1",
    "thread_id": "thread-1",
    "workflow_id": "workflow-1",
}
_APPROVE_COMMAND_PAYLOAD = {
    "schema_version": "1",
    "workflow_id": "workflow-1",
    "thread_id": "thread-1",
    "run_id": "run-1",
    "outline_id": "outline:000001",
    "outline_digest": _DEFAULT_OUTLINE_DIGEST,
    "decision": "approve",
    "actor_assertion": "actor-A",
}
_PAUSE_EVENTS = (
    ("node_started", "topic_planner"),
    ("node_completed", "topic_planner"),
    ("node_started", "research_evidence"),
    ("node_completed", "research_evidence"),
    ("node_started", "outline_writer"),
    ("node_completed", "outline_writer"),
)
_APPROVE_EVENTS = _PAUSE_EVENTS + (
    ("node_started", "outline_approval"),
    ("node_completed", "outline_approval"),
    ("node_started", "academic_draft_composer"),
    ("node_completed", "academic_draft_composer"),
    ("workflow_completed", None),
)
_REJECT_EVENTS = _PAUSE_EVENTS + (
    ("node_started", "outline_approval"),
    ("node_completed", "outline_approval"),
    ("workflow_rejected", None),
)


class _TextSubclass(str):
    pass


class _DictSubclass(dict[str, object]):
    pass


def _request() -> AcademicWorkflowRequest:
    return AcademicWorkflowRequest(
        workflow_mode="academic_langgraph",
        workflow_id="workflow-1",
        thread_id="thread-1",
        run_id="run-1",
        query="Root topic",
        report_type="research_report",
        report_source="web",
        tone="Objective",
        language="English",
        source_urls=(),
        document_urls=(),
        query_domains=(),
        max_search_results=None,
        report_mode=_FIXED_MODE,
        report_locale=_FIXED_LOCALE,
    )


def _identity() -> AcademicWorkflowIdentity:
    return AcademicWorkflowIdentity(
        workflow_id="workflow-1", thread_id="thread-1", run_id="run-1"
    )


def _events(state: object) -> tuple[tuple[str, str | None], ...]:
    return tuple((event.event_type, event.node_id) for event in state.events)


def _command(
    outline: WorkflowOutline, decision: str, actor: str
) -> object:
    command_type = state_module.AcademicOutlineDecisionCommand
    return command_type(
        schema_version="1",
        workflow_id="workflow-1",
        thread_id="thread-1",
        run_id="run-1",
        outline_id="outline:000001",
        outline_digest=state_module._outline_digest(outline),
        decision=decision,
        actor_assertion=actor,
    )


class _Adapter(AcademicWritingAdapter):
    def __init__(self) -> None:
        self.plan_calls = 0
        self.evidence_calls = 0
        self.outline_calls = 0

    async def plan_topic(
        self, request: AcademicWorkflowRequest
    ) -> WorkflowTopicPlan | AdapterFailure:
        self.plan_calls += 1
        return WorkflowTopicPlan(
            topic_plan_id="topic-plan:000001",
            workflow_id=request.workflow_id,
            run_id=request.run_id,
            attempt=1,
            research_topic=request.query,
            research_questions=("Question?",),
        )

    async def collect_research_evidence(
        self,
        request: AcademicWorkflowRequest,
        topic_plan: WorkflowTopicPlan,
    ) -> WorkflowResearchEvidence | AdapterFailure:
        self.evidence_calls += 1
        return WorkflowResearchEvidence(
            evidence_id="evidence:000001",
            topic_plan_id=topic_plan.topic_plan_id,
            attempt=1,
            context_blocks=("Evidence",),
            sources=(
                WorkflowEvidenceSource(
                    source_id="evidence-source:000001",
                    order=1,
                    title="Source",
                    url="https://example.com/source",
                    candidate_id=None,
                ),
            ),
        )

    async def write_outline(
        self,
        request: AcademicWorkflowRequest,
        topic_plan: WorkflowTopicPlan,
        evidence: WorkflowResearchEvidence,
    ) -> WorkflowOutline | AdapterFailure:
        self.outline_calls += 1
        return _fixed_outline(evidence_id=evidence.evidence_id, title=request.query)


def _composition(verdict: str = "supported") -> WorkflowAcademicDraftComposition:
    outline = _fixed_outline()
    section_ids = tuple(section.section_id for section in outline.sections)
    citations = tuple(("evidence-source:000001",) for _ in section_ids)
    drafts = tuple(
        WorkflowSectionDraft(
            outline_id=outline.outline_id,
            section_id=section_id,
            content="Claim [[cite:evidence-source:000001]]",
            attempt=1,
        )
        for section_id in section_ids
    )
    gate = WorkflowCitationEvidenceGateResult(
        outline_id=outline.outline_id,
        section_ids=section_ids,
        cited_source_ids_by_section=citations,
        attempt=1,
    )
    if verdict == "supported":
        routed = "ready"
        issues: tuple[str, ...] = ()
    elif verdict == "uncertain":
        routed = "needs_human_review"
        issues = ("insufficient_evidence",)
    else:
        routed = "blocked"
        issues = ("possible_contradiction",)
    reviews = tuple(
        WorkflowSectionCitationReview(
            outline_id=outline.outline_id,
            section_id=section_id,
            cited_source_ids=cited,
            verdict=verdict,
            issues=issues,
            rationale="Bounded model opinion.",
            attempt=1,
        )
        for section_id, cited in zip(section_ids, citations, strict=True)
    )
    disposition = WorkflowCitationReviewDisposition(
        outline_id=outline.outline_id,
        section_ids=section_ids,
        section_dispositions=tuple(routed for _ in section_ids),
        disposition=routed,
        attempt=1,
    )
    merged = None
    referenced = None
    if verdict == "supported":
        merged = WorkflowMergedDraft(
            outline_id=outline.outline_id,
            section_ids=section_ids,
            content="Merged",
            attempt=1,
        )
        referenced = WorkflowReferencedDraft(
            outline_id=outline.outline_id,
            section_ids=section_ids,
            reference_source_ids=("evidence-source:000001",),
            content="Merged\n\n## References\n\nopaque",
            attempt=1,
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
        verdict: str = "supported",
        *,
        error: BaseException | None = None,
    ) -> None:
        self.calls = 0
        self.verdict = verdict
        self.error = error
        self.states: list[object] = []

    async def compose(self, state: object) -> WorkflowAcademicDraftComposition:
        self.calls += 1
        self.states.append(state)
        assert type(state) is state_module.AcademicWorkflowState
        assert (state.phase, state.status) == ("outline_approved", "completed")
        if self.error is not None:
            raise self.error
        return _composition(self.verdict)


def test_command_has_exact_eight_field_resume_mapping_and_strict_bounds() -> None:
    command_type = state_module.AcademicOutlineDecisionCommand
    assert tuple(command_type.model_fields) == _COMMAND_FIELDS
    outline = _AdapterOutline.make()
    command = _command(outline, "approve", "actor-A")
    dumped = command.model_dump(mode="json")
    assert type(dumped) is dict
    assert tuple(dumped) == _COMMAND_FIELDS
    assert dumped == {
        "schema_version": "1",
        "workflow_id": "workflow-1",
        "thread_id": "thread-1",
        "run_id": "run-1",
        "outline_id": "outline:000001",
        "outline_digest": _DEFAULT_OUTLINE_DIGEST,
        "decision": "approve",
        "actor_assertion": "actor-A",
    }
    with pytest.raises(Exception):
        command_type.model_validate({**dumped, "extra": "forbidden"})
    with pytest.raises(Exception):
        command_type.model_validate({**dumped, "actor_assertion": "x" * 257})
    with pytest.raises(Exception):
        command_type.model_validate({**dumped, "actor_assertion": "bad\nactor"})


class _AdapterOutline:
    @staticmethod
    def make() -> WorkflowOutline:
        return _fixed_outline()


def test_interrupt_payload_is_an_independent_exact_seven_key_mapping() -> None:
    outline = _AdapterOutline.make()
    state = _pause_state(outline)
    payload = nodes_module._approval_interrupt_payload(state)
    assert type(payload) is dict
    assert tuple(sorted(payload)) == _INTERRUPT_KEYS
    assert payload == {
        "allowed_decisions": ["approve", "reject"],
        "outline_digest": _DEFAULT_OUTLINE_DIGEST,
        "outline_id": "outline:000001",
        "run_id": "run-1",
        "schema_version": "1",
        "thread_id": "thread-1",
        "workflow_id": "workflow-1",
    }
    assert "actor_assertion" not in payload
    assert "decision" not in payload


def _pause_state(outline: WorkflowOutline | None = None) -> object:
    outline = outline or _AdapterOutline.make()
    request = _request()
    plan = WorkflowTopicPlan(
        topic_plan_id="topic-plan:000001",
        workflow_id="workflow-1",
        run_id="run-1",
        attempt=1,
        research_topic="Root topic",
        research_questions=("Question?",),
    )
    evidence = WorkflowResearchEvidence(
        evidence_id="evidence:000001",
        topic_plan_id="topic-plan:000001",
        attempt=1,
        context_blocks=("Evidence",),
        sources=(),
    )
    events = tuple(
        state_module.WorkflowEvent(
            event_id=f"event:{order:06d}",
            order=order,
            event_type=event_type,
            node_id=node_id,
            attempt=1,
        )
        for order, (event_type, node_id) in enumerate(_PAUSE_EVENTS, 1)
    )
    return state_module.AcademicWorkflowState(
        schema_version="1",
        workflow_id="workflow-1",
        thread_id="thread-1",
        run_id="run-1",
        phase="outline_ready",
        status="running",
        request=request,
        topic_plan=plan,
        research_evidence=evidence,
        outline=outline,
        outline_decision=None,
        errors=(),
        events=events,
    )


@pytest.mark.asyncio
async def test_normal_pause_has_exact_shape_a_snapshot_and_tuple() -> None:
    saver = InMemorySaver()
    adapter = _Adapter()
    state = await graph_module.start_academic_workflow(
        _request(), adapter, checkpointer=saver
    )
    graph = graph_module._build_graph(
        adapter, saver, nodes_module._composer_slot(_Composer())
    )
    config = {"configurable": {"thread_id": "thread-1"}}
    snapshot = await graph.aget_state(config)
    checkpoint_tuple = await saver.aget_tuple(config)
    assert (state.phase, state.status, state.outline_decision) == (
        "outline_ready",
        "running",
        None,
    )
    assert _events(state) == _PAUSE_EVENTS
    assert snapshot.next == ("outline_approval",)
    assert len(snapshot.tasks) == 1
    task = snapshot.tasks[0]
    assert task.name == "outline_approval"
    assert task.path == ("__pregel_pull", "outline_approval")
    assert task.error is None and task.result is None and task.state is None
    assert len(task.interrupts) == 1
    assert task.interrupts[0].value == _DEFAULT_INTERRUPT_PAYLOAD
    assert checkpoint_tuple is not None
    assert [write[1] for write in checkpoint_tuple.pending_writes] == ["__interrupt__"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("decision", "verdict", "expected_phase", "expected_events"),
    [
        ("approve", "supported", "draft_ready", _APPROVE_EVENTS),
        ("approve", "uncertain", "review_required", _APPROVE_EVENTS),
        ("approve", "unsupported", "review_required", _APPROVE_EVENTS),
        ("reject", "supported", "outline_rejected", _REJECT_EVENTS),
    ],
)
async def test_decision_facade_commits_exact_terminal_golden(
    decision: str,
    verdict: str,
    expected_phase: str,
    expected_events: tuple[tuple[str, str | None], ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saver = InMemorySaver()
    adapter = _Adapter()
    paused = await graph_module.start_academic_workflow(
        _request(), adapter, checkpointer=saver
    )
    production_instances: list[_Composer] = []

    def production_composer() -> _Composer:
        instance = _Composer(verdict)
        production_instances.append(instance)
        return instance

    monkeypatch.setattr(
        nodes_module,
        "GPTResearcherAcademicDraftComposer",
        production_composer,
    )
    composer = None if decision == "approve" and verdict == "supported" else _Composer(verdict)
    result = await graph_module.submit_academic_outline_decision(
        _command(paused.outline, decision, "actor-A"),
        adapter,
        checkpointer=saver,
        composer=composer,
    )
    assert (result.phase, result.status) == (expected_phase, "completed")
    assert result.outline_decision is not None
    assert result.outline_decision.decision == decision
    assert result.outline_decision.actor_assertion == "actor-A"
    assert _events(result) == expected_events
    snapshot = await graph_module._build_graph(adapter, saver).aget_state(
        {"configurable": {"thread_id": "thread-1"}}
    )
    assert snapshot.next == () and snapshot.tasks == ()
    workflow = snapshot.values["workflow"]
    outcome = workflow["outcome"]
    if decision == "reject":
        assert outcome is None
        assert composer.calls == 0
        assert production_instances == []
    elif expected_phase == "draft_ready":
        assert tuple(outcome) == ("outcome_type", "referenced_draft")
        assert outcome["outcome_type"] == "draft_ready"
        assert len(production_instances) == 1
        assert production_instances[0].calls == 1
    else:
        assert tuple(outcome) == (
            "outcome_type",
            "drafts",
            "gate_result",
            "reviews",
        )
        assert outcome["outcome_type"] == "review_required"
        assert composer.calls == 1
        assert production_instances == []
    canonical = json.dumps(workflow, ensure_ascii=False, sort_keys=True)
    assert '"composition"' not in canonical
    assert '"merged_draft"' not in canonical
    assert '"disposition"' not in canonical
    terminal_guard = _Composer()
    with pytest.raises(
        state_module.ThreadProtocolError,
        match="^academic workflow thread is not resumable$",
    ):
        await graph_module.resume_academic_workflow(
            _identity(),
            adapter,
            checkpointer=saver,
            composer=terminal_guard,
        )
    assert terminal_guard.calls == 0


@pytest.mark.asyncio
async def test_ordinary_resume_rejects_shape_a_without_graph_execution() -> None:
    saver = InMemorySaver()
    adapter = _Adapter()
    await graph_module.start_academic_workflow(_request(), adapter, checkpointer=saver)
    with pytest.raises(
        state_module.ThreadProtocolError,
        match="^academic outline approval decision is required$",
    ):
        await graph_module.resume_academic_workflow(
            _identity(), adapter, checkpointer=saver
        )


@pytest.mark.asyncio
async def test_shape_b_same_decision_actor_replay_and_changed_decision_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saver = InMemorySaver()
    adapter = _Adapter()
    paused = await graph_module.start_academic_workflow(
        _request(), adapter, checkpointer=saver
    )
    original = nodes_module._build_approval_terminal_state
    calls = 0

    def crash_once(state: object, command: object) -> object:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("hostile secret")
        return original(state, command)

    monkeypatch.setattr(nodes_module, "_build_approval_terminal_state", crash_once)
    with pytest.raises(nodes_module._OutlineApproveCommitError):
        await graph_module.submit_academic_outline_decision(
            _command(paused.outline, "approve", "actor-A"),
            adapter,
            checkpointer=saver,
        )
    snapshot = await graph_module._build_graph(adapter, saver).aget_state(
        {"configurable": {"thread_id": "thread-1"}}
    )
    assert snapshot.next == () and len(snapshot.tasks) == 1
    task = snapshot.tasks[0]
    assert type(task.error) is str
    assert task.error == (
        "_OutlineApproveCommitError('academic outline decision commit failed')"
    )
    assert type(task.result) is dict and task.result == {}
    config = {"configurable": {"thread_id": "thread-1"}}
    before_rejection = await saver.aget_tuple(config)
    assert before_rejection is not None
    before_writes = tuple(before_rejection.pending_writes)
    with pytest.raises(
        state_module.OutlineDecisionProtocolError,
        match="^academic outline retry decision does not match failed attempt$",
    ):
        await graph_module.submit_academic_outline_decision(
            _command(paused.outline, "reject", "actor-C"),
            adapter,
            checkpointer=saver,
        )
    after_rejection = await saver.aget_tuple(config)
    assert after_rejection is not None
    assert tuple(after_rejection.pending_writes) == before_writes
    result = await graph_module.submit_academic_outline_decision(
        _command(paused.outline, "approve", "actor-B"),
        adapter,
        checkpointer=saver,
        composer=_Composer(),
    )
    assert result.outline_decision is not None
    assert result.outline_decision.decision == "approve"
    assert result.outline_decision.actor_assertion == "actor-A"
    assert "actor-B" not in json.dumps(result.model_dump(mode="json"))
    assert (adapter.plan_calls, adapter.evidence_calls, adapter.outline_calls) == (
        1,
        1,
        1,
    )


@pytest.mark.asyncio
async def test_terminal_repeated_decision_rejects_before_ainvoke() -> None:
    saver = InMemorySaver()
    adapter = _Adapter()
    paused = await graph_module.start_academic_workflow(
        _request(), adapter, checkpointer=saver
    )
    await graph_module.submit_academic_outline_decision(
        _command(paused.outline, "reject", "actor-A"),
        adapter,
        checkpointer=saver,
    )
    with pytest.raises(
        state_module.OutlineDecisionProtocolError,
        match="^academic outline decision has already been committed$",
    ):
        await graph_module.submit_academic_outline_decision(
            _command(paused.outline, "approve", "actor-B"),
            adapter,
            checkpointer=saver,
        )


def test_command_canonical_json_is_bounded_and_has_no_competing_mapping() -> None:
    command = _command(_AdapterOutline.make(), "approve", "actor-A")
    canonical = json.dumps(
        command.model_dump(mode="json"),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    assert len(canonical) <= 2048
    assert tuple(command.model_dump(mode="json")) == _COMMAND_FIELDS
    graph_source = inspect.getsource(graph_module.submit_academic_outline_decision)
    assert 'model_dump(mode="json")' in graph_source


def test_unicode_outline_digest_complete_byte_golden_and_mutations() -> None:
    outline = WorkflowOutline(
        outline_id="outline:000001",
        evidence_id="evidence:000001",
        attempt=1,
        title="研究 Ω",
        sections=(
            WorkflowOutlineSection(
                section_id="section:000001",
                order=1,
                title="引言",
                brief="范围 α",
            ),
            WorkflowOutlineSection(
                section_id="section:000002",
                order=2,
                title="分析",
                brief="证据 β",
            ),
            WorkflowOutlineSection(
                section_id="section:000003",
                order=3,
                title="结论",
                brief="综合 γ",
            ),
        ),
    )
    encoded = state_module._canonical_outline_bytes(outline)
    assert len(encoded.decode("utf-8")) == 313
    assert len(encoded) == 345
    assert state_module._outline_digest(outline) == (
        "6ec33d8656eb25d09990657737af1438d23cb5cc8749f2679e3f1ccc1dabff3a"
    )
    restored = WorkflowOutline.model_validate_json(encoded)
    assert restored == outline
    assert state_module._canonical_outline_bytes(restored) == encoded
    changed = outline.model_copy(update={"title": "研究 Ω!"})
    assert state_module._outline_digest(changed) != state_module._outline_digest(outline)


def _digest_matrix_outline() -> WorkflowOutline:
    return WorkflowOutline(
        outline_id="outline:000001",
        evidence_id="evidence:000001",
        attempt=1,
        title="Root topic",
        sections=(
            WorkflowOutlineSection(
                section_id="section:000001",
                order=1,
                title="Introduction",
                brief="Scope",
            ),
            WorkflowOutlineSection(
                section_id="section:000002",
                order=2,
                title="Analysis",
                brief="Evidence",
            ),
        ),
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "outline_id",
        "evidence_id",
        "attempt",
        "title",
        "section_id",
        "section_item_order",
        "section_title",
        "section_brief",
        "section_order",
    ],
)
def test_digest_covers_every_field_and_rejects_invalid_fixed_field_mutations(
    mutation: str,
) -> None:
    outline = _digest_matrix_outline()
    assert state_module._outline_digest(outline) == (
        "77845b02b924d7c93892ef1b2889c31e1ced9b852255056e071bc2388fd6154b"
    )
    data = outline.model_dump(mode="python")
    if mutation == "outline_id":
        changed = outline.model_copy(update={"outline_id": "outline:000002"})
    elif mutation == "evidence_id":
        changed = outline.model_copy(update={"evidence_id": "evidence:000002"})
    elif mutation == "attempt":
        changed = outline.model_copy(update={"attempt": 2})
    elif mutation == "title":
        data["title"] = "Root topid"
        changed = WorkflowOutline.model_validate(data)
    elif mutation == "section_id":
        section = outline.sections[0].model_copy(
            update={"section_id": "section:000003"}
        )
        changed = outline.model_copy(
            update={"sections": (section,) + outline.sections[1:]}
        )
    elif mutation == "section_item_order":
        section = outline.sections[0].model_copy(update={"order": 2})
        changed = outline.model_copy(
            update={"sections": (section,) + outline.sections[1:]}
        )
    elif mutation == "section_title":
        data["sections"][0]["title"] = "Introductions"
        changed = WorkflowOutline.model_validate(data)
    elif mutation == "section_brief":
        data["sections"][0]["brief"] = "Scopes"
        changed = WorkflowOutline.model_validate(data)
    else:
        changed = outline.model_copy(
            update={"sections": tuple(reversed(outline.sections))}
        )
    if mutation in {
        "outline_id",
        "evidence_id",
        "attempt",
        "section_id",
        "section_item_order",
        "section_order",
    }:
        with pytest.raises(Exception):
            state_module._outline_digest(changed)
    else:
        assert state_module._outline_digest(changed) != (
            "77845b02b924d7c93892ef1b2889c31e1ced9b852255056e071bc2388fd6154b"
        )


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("schema_version", 1),
        ("workflow_id", 1),
        ("thread_id", True),
        ("run_id", 1.0),
        ("outline_id", "outline:000002"),
        ("outline_digest", "A" * 64),
        ("outline_digest", "0" * 63),
        ("outline_digest", "0" * 65),
        ("decision", "edit"),
        ("decision", _TextSubclass("approve")),
        ("actor_assertion", b"actor"),
        ("actor_assertion", _TextSubclass("actor")),
        ("actor_assertion", ""),
        ("actor_assertion", "\u007f"),
    ],
)
def test_command_python_and_json_strict_field_matrix(
    field: str, bad_value: object
) -> None:
    command_type = state_module.AcademicOutlineDecisionCommand
    good = _command(_AdapterOutline.make(), "approve", "actor-A").model_dump(
        mode="json"
    )
    bad = {**good, field: bad_value}
    with pytest.raises(Exception):
        command_type.model_validate(bad)
    if type(bad_value) is bytes:
        with pytest.raises(TypeError):
            json.dumps(bad, ensure_ascii=False, separators=(",", ":"))
        return
    encoded = json.dumps(bad, ensure_ascii=False, separators=(",", ":"))
    if type(bad_value) is _TextSubclass:
        restored = command_type.model_validate_json(encoded)
        assert type(getattr(restored, field)) is str
    else:
        with pytest.raises(Exception):
            command_type.model_validate_json(encoded)


@pytest.mark.parametrize("case", ["missing", "extra", "mapping_subclass"])
def test_command_mapping_shape_is_strict_for_python_and_json(case: str) -> None:
    command_type = state_module.AcademicOutlineDecisionCommand
    good = dict(_APPROVE_COMMAND_PAYLOAD)
    if case == "missing":
        good.pop("actor_assertion")
    elif case == "extra":
        good["extra"] = "forbidden"
    else:
        with pytest.raises(Exception):
            command_type.model_validate(_DictSubclass(good))
        return
    with pytest.raises(Exception):
        command_type.model_validate(good)
    with pytest.raises(Exception):
        command_type.model_validate_json(
            json.dumps(good, ensure_ascii=False, separators=(",", ":"))
        )


def test_identity_actor_and_old_state_boundaries_are_closed() -> None:
    with pytest.raises(Exception):
        state_module.AcademicWorkflowIdentity(
            workflow_id="x" * 257, thread_id="thread", run_id="run"
        )
    with pytest.raises(Exception):
        state_module.AcademicOutlineDecisionCommand(
            **{
                **_command(
                    _AdapterOutline.make(), "approve", "actor-A"
                ).model_dump(mode="json"),
                "actor_assertion": "\u0001",
            }
        )
    paused = _pause_state()
    legacy_payload = paused.model_dump(mode="json")
    legacy_payload["request"].pop("report_mode")
    legacy_payload["request"].pop("report_locale")
    legacy_payload["outline"].pop("report_mode")
    legacy_payload["outline"].pop("report_locale")
    for section in legacy_payload["outline"]["sections"]:
        section.pop("section_role")
    restored_legacy = state_module.restore_workflow_state(
        {"workflow": legacy_payload}
    )
    assert restored_legacy.request.report_mode == "freeform"
    assert restored_legacy.request.report_locale is None
    assert restored_legacy.outline.report_mode == "freeform"
    assert restored_legacy.outline.report_locale is None
    assert all(
        section.section_role == "freeform"
        for section in restored_legacy.outline.sections
    )
    canonical_legacy = restored_legacy.model_dump(mode="json")
    assert canonical_legacy["request"]["report_mode"] == "freeform"
    assert canonical_legacy["request"]["report_locale"] is None
    assert canonical_legacy["outline"]["report_mode"] == "freeform"
    assert canonical_legacy["outline"]["report_locale"] is None
    assert all(
        section["section_role"] == "freeform"
        for section in canonical_legacy["outline"]["sections"]
    )
    with pytest.raises(Exception):
        state_module.AcademicWorkflowState.model_validate(
            {**paused.model_dump(mode="python"), "status": "completed"}
        )


@pytest.mark.parametrize(
    "case",
    [
        "pause_with_decision",
        "approved_without_decision",
        "approved_with_reject",
        "rejected_with_approve",
        "terminal_with_error",
        "terminal_wrong_event",
        "record_wrong_identity",
        "record_wrong_digest",
    ],
)
def test_approval_state_illegal_combinations_fail_closed(case: str) -> None:
    paused = _pause_state()

    def events_for(projection: tuple[tuple[str, str | None], ...]):
        return tuple(
            state_module.WorkflowEvent(
                event_id=f"event:{order:06d}",
                order=order,
                event_type=event_type,
                node_id=node_id,
                attempt=1,
            )
            for order, (event_type, node_id) in enumerate(projection, 1)
        )

    def record(decision: str):
        return state_module.WorkflowOutlineDecisionRecord(
            decision_id="outline-decision:000001",
            schema_version="1",
            workflow_id="workflow-1",
            thread_id="thread-1",
            run_id="run-1",
            outline_id="outline:000001",
            outline_digest=_DEFAULT_OUTLINE_DIGEST,
            decision=decision,
            actor_assertion="actor-A",
            attempt=1,
        )

    data = paused.model_dump(mode="python")
    if case == "pause_with_decision":
        data["outline_decision"] = record("approve")
    else:
        approve = case not in {"rejected_with_approve"}
        data.update(
            phase="outline_approved" if approve else "outline_rejected",
            status="completed",
            events=events_for(_APPROVE_EVENTS if approve else _REJECT_EVENTS),
            outline_decision=record("approve" if approve else "reject"),
        )
        if case == "approved_without_decision":
            data["outline_decision"] = None
        elif case == "approved_with_reject":
            data["outline_decision"] = record("reject")
        elif case == "rejected_with_approve":
            data["outline_decision"] = record("approve")
        elif case == "terminal_with_error":
            data["errors"] = (
                state_module.WorkflowError(
                    error_id="error:000001",
                    order=1,
                    failed_node_id="outline_writer",
                    attempt=1,
                    code="outline_writing_failed",
                ),
            )
        elif case == "terminal_wrong_event":
            data["events"] = events_for(_REJECT_EVENTS)
        elif case == "record_wrong_identity":
            data["outline_decision"] = record("approve").model_copy(
                update={"workflow_id": "workflow-other"}
            )
        elif case == "record_wrong_digest":
            data["outline_decision"] = record("approve").model_copy(
                update={"outline_digest": "0" * 64}
            )
    with pytest.raises(Exception):
        state_module.AcademicWorkflowState.model_validate(data)


def test_decision_record_is_strict_frozen_and_has_exact_fields() -> None:
    outline = _AdapterOutline.make()
    record_type = state_module.WorkflowOutlineDecisionRecord
    expected_fields = (
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
    assert tuple(record_type.model_fields) == expected_fields
    record = record_type(
        decision_id="outline-decision:000001",
        schema_version="1",
        workflow_id="x" * 256,
        thread_id="t" * 256,
        run_id="r" * 256,
        outline_id="outline:000001",
        outline_digest=_DEFAULT_OUTLINE_DIGEST,
        decision="approve",
        actor_assertion="a" * 256,
        attempt=1,
    )
    assert tuple(record.model_dump(mode="json")) == expected_fields
    with pytest.raises(Exception):
        record.actor_assertion = "changed"
    with pytest.raises(Exception):
        record_type.model_validate({**record.model_dump(), "extra": "forbidden"})
    with pytest.raises(Exception):
        record_type.model_validate_json(
            json.dumps({**record.model_dump(mode="json"), "attempt": True})
        )


def test_command_accepts_every_exact_upper_bound() -> None:
    command = state_module.AcademicOutlineDecisionCommand(
        schema_version="1",
        workflow_id="w" * 256,
        thread_id="t" * 256,
        run_id="r" * 256,
        outline_id="outline:000001",
        outline_digest="0" * 64,
        decision="reject",
        actor_assertion="a" * 256,
    )
    assert all(
        len(getattr(command, field)) == 256
        for field in ("workflow_id", "thread_id", "run_id", "actor_assertion")
    )
    canonical = json.dumps(
        command.model_dump(mode="json"),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    assert len(canonical) == 1245
    assert len(canonical) < 2048


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("workflow_id", ""),
        ("workflow_id", "w" * 257),
        ("thread_id", ""),
        ("thread_id", "t" * 257),
        ("run_id", ""),
        ("run_id", "r" * 257),
        ("actor_assertion", ""),
        ("actor_assertion", "a" * 257),
    ],
)
def test_identity_actor_edges_and_oversized_canonical_payload_reject(
    field: str, value: str
) -> None:
    payload = dict(_APPROVE_COMMAND_PAYLOAD)
    payload[field] = value
    with pytest.raises(Exception):
        state_module.AcademicOutlineDecisionCommand.model_validate(payload)

    oversized = dict(_APPROVE_COMMAND_PAYLOAD)
    oversized["actor_assertion"] = "x" * (2048 - len(json.dumps(
        {**oversized, "actor_assertion": ""},
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )))
    canonical = json.dumps(
        oversized,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    assert len(canonical) == 2048
    with pytest.raises(Exception):
        state_module.AcademicOutlineDecisionCommand.model_validate(oversized)
    oversized["actor_assertion"] += "x"
    assert len(json.dumps(
        oversized,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )) == 2049
    with pytest.raises(Exception):
        state_module.AcademicOutlineDecisionCommand.model_validate(oversized)


@pytest.mark.asyncio
async def test_shape_b_tuple_writes_and_ordinary_resume_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saver = InMemorySaver()
    adapter = _Adapter()
    paused = await graph_module.start_academic_workflow(
        _request(), adapter, checkpointer=saver
    )

    def crash(_state: object, _command_value: object) -> object:
        raise RuntimeError("raw-secret-must-not-be-stored")

    monkeypatch.setattr(nodes_module, "_build_approval_terminal_state", crash)
    command = _command(paused.outline, "reject", "actor-A")
    with pytest.raises(nodes_module._OutlineRejectCommitError):
        await graph_module.submit_academic_outline_decision(
            command, adapter, checkpointer=saver
        )
    config = {"configurable": {"thread_id": "thread-1"}}
    snapshot = await graph_module._build_graph(adapter, saver).aget_state(config)
    checkpoint_tuple = await saver.aget_tuple(config)
    assert snapshot.next == () and len(snapshot.tasks) == 1
    task = snapshot.tasks[0]
    assert task.error == (
        "_OutlineRejectCommitError('academic outline decision commit failed')"
    )
    assert task.interrupts[0].value == _DEFAULT_INTERRUPT_PAYLOAD
    assert type(task.result) is dict and task.result == {}
    assert checkpoint_tuple is not None
    assert [write[1] for write in checkpoint_tuple.pending_writes] == [
        "__interrupt__",
        "__resume__",
        "__resume__",
        "__error__",
    ]
    _assert_safe_reachable(
        (snapshot, checkpoint_tuple),
        forbidden_text=("raw-secret-must-not-be-stored",),
    )
    with pytest.raises(
        state_module.ThreadProtocolError,
        match="^academic outline approval decision is required$",
    ):
        await graph_module.resume_academic_workflow(
            _identity(), adapter, checkpointer=saver
        )


@pytest.mark.asyncio
async def test_reject_actor_a_crash_actor_b_retry_preserves_actor_a(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saver = InMemorySaver()
    adapter = _Adapter()
    paused = await graph_module.start_academic_workflow(
        _request(), adapter, checkpointer=saver
    )
    original = nodes_module._build_approval_terminal_state
    calls = 0

    def crash_once(state: object, command: object) -> object:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("reject-secret")
        return original(state, command)

    monkeypatch.setattr(nodes_module, "_build_approval_terminal_state", crash_once)
    with pytest.raises(nodes_module._OutlineRejectCommitError):
        await graph_module.submit_academic_outline_decision(
            _command(paused.outline, "reject", "actor-A"),
            adapter,
            checkpointer=saver,
        )
    result = await graph_module.submit_academic_outline_decision(
        _command(paused.outline, "reject", "actor-B"),
        adapter,
        checkpointer=saver,
    )
    assert result.outline_decision is not None
    assert result.outline_decision.decision == "reject"
    assert result.outline_decision.actor_assertion == "actor-A"
    assert "actor-B" not in json.dumps(result.model_dump(mode="json"))
    assert (adapter.plan_calls, adapter.evidence_calls, adapter.outline_calls) == (
        1,
        1,
        1,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "value"),
    [
        ("schema_version", "2"),
        ("workflow_id", "workflow-other"),
        ("thread_id", "thread-other"),
        ("run_id", "run-other"),
        ("outline_id", "outline:000002"),
        ("outline_digest", "0" * 64),
        ("decision", "edit"),
        ("actor_assertion", ""),
        ("missing", None),
        ("extra", "forbidden"),
        ("wrong_type", True),
    ],
)
async def test_invalid_first_payload_cannot_create_an_eligible_marker(
    case: str, value: object
) -> None:
    saver = InMemorySaver()
    adapter = _Adapter()
    await graph_module.start_academic_workflow(_request(), adapter, checkpointer=saver)
    graph = graph_module._build_graph(
        adapter, saver, nodes_module._composer_slot(_Composer())
    )
    config = {"configurable": {"thread_id": "thread-1"}}
    invalid = dict(_APPROVE_COMMAND_PAYLOAD)
    if case == "missing":
        invalid.pop("actor_assertion")
    elif case == "extra":
        invalid["extra"] = value
    elif case == "wrong_type":
        invalid["schema_version"] = value
    else:
        invalid[case] = value
    with pytest.raises(state_module.InvariantError):
        await graph.ainvoke(Command(resume=invalid), config=config)
    snapshot = await graph.aget_state(config)
    assert snapshot.next == () and len(snapshot.tasks) == 1
    assert snapshot.tasks[0].error not in {
        "_OutlineApproveCommitError('academic outline decision commit failed')",
        "_OutlineRejectCommitError('academic outline decision commit failed')",
    }


def _framework_snapshot(
    task: PregelTask, *, next_nodes: tuple[str, ...]
) -> StateSnapshot:
    return StateSnapshot(
        values={"workflow": {}},
        next=next_nodes,
        config={"configurable": {"thread_id": "thread-1"}},
        metadata={"source": "loop", "step": 3, "parents": {}},
        created_at="created",
        parent_config={"configurable": {"thread_id": "thread-1"}},
        tasks=(task,),
        interrupts=(),
    )


@pytest.mark.parametrize(
    ("shape", "case"),
    [
        ("pause", "name"),
        ("pause", "path"),
        ("pause", "error"),
        ("pause", "interrupts"),
        ("pause", "result"),
        ("pause", "state"),
        ("pause", "id"),
        ("pause", "no_tasks"),
        ("pause", "multiple_tasks"),
        ("pause", "wrong_next"),
        ("approve", "error_subclass"),
        ("approve", "error_trailing"),
        ("approve", "error_unknown"),
        ("approve", "error_none"),
        ("approve", "result_none"),
        ("approve", "result_extra"),
        ("approve", "interrupts"),
        ("approve", "wrong_next"),
        ("approve", "payload"),
    ],
)
def test_shape_a_b_predicate_fails_closed_for_every_near_miss(
    shape: str, case: str
) -> None:
    state = _pause_state()
    marker = "_OutlineApproveCommitError('academic outline decision commit failed')"
    good_task = PregelTask(
        id="task-1",
        name="outline_approval",
        path=("__pregel_pull", "outline_approval"),
        error=None if shape == "pause" else marker,
        interrupts=(Interrupt(value=dict(_DEFAULT_INTERRUPT_PAYLOAD)),),
        result=None if shape == "pause" else {},
        state=None,
    )
    next_nodes = ("outline_approval",) if shape == "pause" else ()
    snapshot = _framework_snapshot(good_task, next_nodes=next_nodes)
    assert graph_module._approval_shape(snapshot, state) == shape

    task_mutations: dict[str, dict[str, object]] = {
        "name": {"name": "outline_writer"},
        "path": {"path": ("__pregel_pull", "outline_writer")},
        "error": {"error": "unknown"},
        "interrupts": {"interrupts": ()},
        "result": {"result": {}},
        "state": {"state": object()},
        "id": {"id": ""},
        "error_subclass": {"error": _TextSubclass(marker)},
        "error_trailing": {"error": marker + " trailing"},
        "error_unknown": {"error": "unknown"},
        "error_none": {"error": None},
        "result_none": {"result": None},
        "result_extra": {"result": {"extra": True}},
        "payload": {"interrupts": (Interrupt(value={}),)},
    }
    if case == "no_tasks":
        snapshot = snapshot._replace(tasks=())
    elif case == "multiple_tasks":
        snapshot = snapshot._replace(tasks=(good_task, good_task))
    elif case == "wrong_next":
        snapshot = snapshot._replace(next=() if shape == "pause" else ("outline_approval",))
    else:
        snapshot = snapshot._replace(tasks=(good_task._replace(**task_mutations[case]),))
    assert graph_module._approval_shape(snapshot, state) is None


class _CancellationBarrierSaver(InMemorySaver):
    def __init__(self, mode: str) -> None:
        super().__init__()
        self.mode = mode
        self.blocked = asyncio.Event()
        self.release = asyncio.Event()
        self._used = False

    async def _block_once(self) -> None:
        if self._used:
            return
        self._used = True
        self.blocked.set()
        await self.release.wait()

    async def aput(
        self,
        config: object,
        checkpoint: object,
        metadata: object,
        new_versions: object,
    ) -> object:
        workflow = checkpoint.get("channel_values", {}).get("workflow", {})
        phase = workflow.get("phase")
        step = metadata.get("step")
        if self.mode == "before_interrupt" and phase == "outline_ready" and step == 3:
            result = await super().aput(config, checkpoint, metadata, new_versions)
            await self._block_once()
            return result
        if self.mode == "terminal_after" and phase in (
            "outline_approved",
            "outline_rejected",
        ):
            result = await super().aput(config, checkpoint, metadata, new_versions)
            await self._block_once()
            return result
        return await super().aput(config, checkpoint, metadata, new_versions)

    async def aput_writes(
        self,
        config: object,
        writes: object,
        task_id: str,
        task_path: str = "",
    ) -> None:
        channels = tuple(channel for channel, _value in writes)
        if self.mode == "command" and "__resume__" in channels:
            await super().aput_writes(config, writes, task_id, task_path)
            await self._block_once()
            return
        await super().aput_writes(config, writes, task_id, task_path)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "start_is_cancelled", "terminal_visible"),
    [
        ("before_interrupt", True, False),
        ("command", False, False),
        ("terminal_before", False, False),
        ("terminal_after", False, True),
    ],
)
async def test_real_cancellation_phases_and_replay(
    mode: str,
    start_is_cancelled: bool,
    terminal_visible: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saver = _CancellationBarrierSaver(mode)
    adapter = _Adapter()
    facade_task_holder: dict[str, asyncio.Task[object]] = {}
    if start_is_cancelled:
        task = asyncio.create_task(
            graph_module.start_academic_workflow(
                _request(), adapter, checkpointer=saver
            )
        )
        await saver.blocked.wait()
        paused = None
    else:
        paused = await graph_module.start_academic_workflow(
            _request(), adapter, checkpointer=saver
        )
        composer = _Composer()
        if mode == "terminal_before":
            original_build = nodes_module._build_approval_terminal_state

            def schedule_external_cancel(
                state: object, command_value: object
            ) -> object:
                result = original_build(state, command_value)
                asyncio.get_running_loop().call_soon(
                    facade_task_holder["task"].cancel,
                    "CANCEL-terminal_before",
                )
                return result

            monkeypatch.setattr(
                nodes_module,
                "_build_approval_terminal_state",
                schedule_external_cancel,
            )
        task = asyncio.create_task(
            graph_module.submit_academic_outline_decision(
                    _command(paused.outline, "approve", "actor-A"),
                    adapter,
                    checkpointer=saver,
                    composer=composer,
            )
        )
        facade_task_holder["task"] = task
        if mode != "terminal_before":
            await saver.blocked.wait()

    if mode != "terminal_before":
        task.cancel(f"CANCEL-{mode}")
        saver.release.set()
    with pytest.raises(asyncio.CancelledError) as caught:
        await task
    assert caught.value.args[0] == f"CANCEL-{mode}"

    graph = graph_module._build_graph(
        adapter, saver, nodes_module._composer_slot(_Composer())
    )
    config = {"configurable": {"thread_id": "thread-1"}}
    snapshot = await graph.aget_state(config)
    restored = graph_module._safe_persistent_restore(snapshot.values)
    assert (adapter.plan_calls, adapter.evidence_calls, adapter.outline_calls) == (
        1,
        1,
        1,
    )
    if terminal_visible:
        assert (restored.phase, restored.status) == (
            "outline_approved",
            "running",
        )
        assert composer.calls == 0
        assert restored.outline_decision is not None
        assert _events(restored) == _APPROVE_EVENTS[:8]
        assert snapshot.next == ("academic_draft_composer",)
        with pytest.raises(
            state_module.OutlineDecisionProtocolError,
            match="^academic outline decision has already been committed$",
        ):
            await graph_module.submit_academic_outline_decision(
                _command(restored.outline, "approve", "actor-B"),
                adapter,
                checkpointer=saver,
            )
        retry_composer = _Composer()
        completed = await graph_module.resume_academic_workflow(
            _identity(),
            adapter,
            checkpointer=saver,
            composer=retry_composer,
        )
        assert (completed.phase, completed.status) == ("draft_ready", "completed")
        assert retry_composer.calls == 1
        return

    assert (restored.phase, restored.status) == ("outline_ready", "running")
    assert restored.outline_decision is None
    assert _events(restored) == _PAUSE_EVENTS
    if start_is_cancelled:
        assert snapshot.next == ("outline_approval",)
        assert len(snapshot.tasks) == 1
        assert snapshot.tasks[0].interrupts == ()
        assert graph_module._approval_shape(snapshot, restored) is None
        return

    assert graph_module._approval_shape(snapshot, restored) == "pause"
    replay = await graph_module.submit_academic_outline_decision(
        _command(restored.outline, "reject", "actor-B"),
        adapter,
        checkpointer=saver,
    )
    assert (replay.phase, replay.status) == ("outline_rejected", "completed")
    assert _events(replay) == _REJECT_EVENTS
    assert (adapter.plan_calls, adapter.evidence_calls, adapter.outline_calls) == (
        1,
        1,
        1,
    )


@pytest.mark.asyncio
async def test_cancelling_finished_start_task_returns_false_and_keeps_shape_a() -> None:
    saver = InMemorySaver()
    adapter = _Adapter()
    task = asyncio.create_task(
        graph_module.start_academic_workflow(_request(), adapter, checkpointer=saver)
    )
    paused = await task
    assert task.cancel("LATE-CANCELLATION") is False
    snapshot = await graph_module._build_graph(adapter, saver).aget_state(
        {"configurable": {"thread_id": "thread-1"}}
    )
    assert graph_module._approval_shape(snapshot, paused) == "pause"


def test_production_api_and_graph_surface_are_statically_closed() -> None:
    root = Path(__file__).parents[1]
    graph_path = root / "gpt_researcher/workflows/academic_writing/graph.py"
    nodes_path = root / "gpt_researcher/workflows/academic_writing/nodes.py"
    graph_source = graph_path.read_text(encoding="utf-8")
    nodes_source = nodes_path.read_text(encoding="utf-8")
    graph_tree = ast.parse(graph_source, filename=str(graph_path))
    top_level_functions = {
        node.name: node
        for node in graph_tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    graph_call_owners = {
        name
        for name, function in top_level_functions.items()
        if any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"ainvoke", "aget_state"}
            for node in ast.walk(function)
        )
    }
    expected_facades = {
        "start_academic_workflow",
        "resume_academic_workflow",
        "submit_academic_outline_decision",
    }
    assert graph_call_owners == {
        "start_academic_workflow",
        "submit_academic_outline_decision",
        "_resume_with_slot",
        "_ainvoke_sync",
    }
    runnable_config_owners = {
        name
        for name, function in top_level_functions.items()
        if any(
            isinstance(node, ast.AnnAssign)
            and isinstance(node.annotation, ast.Name)
            and node.annotation.id == "RunnableConfig"
            for node in ast.walk(function)
        )
    }
    assert runnable_config_owners == {
        "start_academic_workflow",
        "submit_academic_outline_decision",
        "_resume_with_slot",
    }
    command_owners = {
        name
        for name, function in top_level_functions.items()
        if any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "Command"
            for node in ast.walk(function)
        )
    }
    assert command_owners == {"submit_academic_outline_decision"}
    assert "Command" not in ast.dump(
        top_level_functions["resume_academic_workflow"], include_attributes=False
    )
    assert "_build_graph" in top_level_functions
    assert not any(
        isinstance(node, ast.Return)
        and isinstance(node.value, ast.Name)
        and node.value.id == "graph"
        for facade in expected_facades
        for node in ast.walk(top_level_functions[facade])
    )
    for initializer in (
        root / "gpt_researcher/workflows/__init__.py",
        root / "gpt_researcher/workflows/academic_writing/__init__.py",
    ):
        initializer_text = initializer.read_text(encoding="utf-8")
        assert all(name not in initializer_text for name in expected_facades)
    assert "InMemorySaver" not in graph_source + nodes_source
    assert ".aget_tuple(" not in graph_source + nodes_source
    assert "get_tuple(" not in graph_source + nodes_source
    assert "update_state(" not in graph_source + nodes_source
    assert "interrupt(" in nodes_source
    assert "Send(" not in graph_source + nodes_source
    assert "RetryPolicy" not in graph_source + nodes_source
    assert tuple(state_module.WorkflowTopicPlan.model_fields) == (
        "topic_plan_id",
        "workflow_id",
        "run_id",
        "attempt",
        "research_topic",
        "research_questions",
    )


def test_all_fixed_decision_protocol_messages_are_exact_and_safe() -> None:
    expected = {
        "invalid": "academic outline decision command is invalid",
        "missing": "academic workflow checkpoint does not exist",
        "identity": "academic workflow identity does not match checkpoint",
        "committed": "academic outline decision has already been committed",
        "unavailable": "academic outline decision is not available",
        "outline": "academic outline identity does not match checkpoint",
        "digest": "academic outline revision does not match checkpoint",
        "retry": "academic outline retry decision does not match failed attempt",
    }
    for kind, message in expected.items():
        error = state_module.OutlineDecisionProtocolError(kind)
        assert type(error) is state_module.OutlineDecisionProtocolError
        assert error.args == (message,)
        assert error.__cause__ is None and error.__context__ is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "error_type", "message", "expected_builds"),
    [
        (
            "invalid",
            state_module.OutlineDecisionProtocolError,
            "academic outline decision command is invalid",
            0,
        ),
        (
            "missing",
            state_module.OutlineDecisionProtocolError,
            "academic workflow checkpoint does not exist",
            1,
        ),
        (
            "identity",
            state_module.OutlineDecisionProtocolError,
            "academic workflow identity does not match checkpoint",
            1,
        ),
        (
            "committed",
            state_module.OutlineDecisionProtocolError,
            "academic outline decision has already been committed",
            1,
        ),
        (
            "unavailable",
            state_module.OutlineDecisionProtocolError,
            "academic outline decision is not available",
            1,
        ),
        (
            "outline",
            state_module.OutlineDecisionProtocolError,
            "academic outline identity does not match checkpoint",
            1,
        ),
        (
            "digest",
            state_module.OutlineDecisionProtocolError,
            "academic outline revision does not match checkpoint",
            1,
        ),
        (
            "shape",
            state_module.InvariantError,
            "academic workflow invariant violation",
            1,
        ),
    ],
)
async def test_decision_facade_guard_table_has_fixed_priority_and_zero_mutation(
    case: str,
    error_type: type[BaseException],
    message: str,
    expected_builds: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paused = _pause_state()
    command: object = _command(paused.outline, "approve", "actor-A")
    checkpoint_state = paused
    approval_task = PregelTask(
        id="task-1",
        name="outline_approval",
        path=("__pregel_pull", "outline_approval"),
        error=None,
        interrupts=(Interrupt(value=dict(_DEFAULT_INTERRUPT_PAYLOAD)),),
        state=None,
        result=None,
    )
    snapshot = _framework_snapshot(
        approval_task, next_nodes=("outline_approval",)
    )
    if case == "invalid":
        command = object()
    elif case == "missing":
        snapshot = snapshot._replace(created_at=None)
    elif case == "identity":
        payload = dict(_APPROVE_COMMAND_PAYLOAD)
        payload["workflow_id"] = "workflow-other"
        command = state_module.AcademicOutlineDecisionCommand.model_validate(payload)
    elif case == "committed":
        checkpoint_state = paused.model_copy(update={"status": "completed"})
    elif case == "unavailable":
        checkpoint_state = paused.model_copy(update={"phase": "evidence_collected"})
    elif case == "outline":
        changed_outline = paused.outline.model_copy(
            update={"outline_id": "outline:000002"}
        )
        checkpoint_state = paused.model_copy(update={"outline": changed_outline})
    elif case == "digest":
        payload = dict(_APPROVE_COMMAND_PAYLOAD)
        payload["outline_digest"] = "0" * 64
        command = state_module.AcademicOutlineDecisionCommand.model_validate(payload)
    else:
        snapshot = snapshot._replace(tasks=())

    class CountingGraph:
        invoked = 0
        state_reads = 0

        async def aget_state(self, _config: object) -> StateSnapshot:
            self.state_reads += 1
            return snapshot

        async def ainvoke(self, _value: object, *, config: object) -> object:
            self.invoked += 1
            raise AssertionError("a rejected command must not execute the graph")

    fake = CountingGraph()
    builds = 0

    def build(
        _adapter: object,
        _checkpointer: object,
        _composer_slot: object,
    ) -> CountingGraph:
        nonlocal builds
        builds += 1
        return fake

    monkeypatch.setattr(graph_module, "_build_graph", build)
    monkeypatch.setattr(
        graph_module,
        "_safe_persistent_restore",
        lambda _value: checkpoint_state,
    )
    state_before = checkpoint_state.model_dump(mode="json")
    snapshot_before = tuple(snapshot)
    with pytest.raises(error_type, match=f"^{message}$") as caught:
        await graph_module.submit_academic_outline_decision(
            command, _Adapter(), checkpointer=InMemorySaver()
        )
    assert builds == expected_builds
    assert fake.state_reads == (0 if case == "invalid" else 1)
    assert fake.invoked == 0
    assert checkpoint_state.model_dump(mode="json") == state_before
    assert tuple(snapshot) == snapshot_before
    assert caught.value.__cause__ is None and caught.value.__context__ is None


@pytest.mark.asyncio
async def test_raw_terminal_command_adds_only_framework_resume_write() -> None:
    saver = InMemorySaver()
    adapter = _Adapter()
    paused = await graph_module.start_academic_workflow(
        _request(), adapter, checkpointer=saver
    )
    command = _command(paused.outline, "approve", "actor-A")
    terminal = await graph_module.submit_academic_outline_decision(
        command, adapter, checkpointer=saver, composer=_Composer()
    )
    replay_composer = _Composer()
    graph = graph_module._build_graph(
        adapter, saver, nodes_module._composer_slot(replay_composer)
    )
    config = {"configurable": {"thread_id": "thread-1"}}
    raw = await graph.ainvoke(
        Command(resume=command.model_dump(mode="json")), config=config
    )
    assert raw["workflow"] == terminal.model_dump(mode="json")
    assert replay_composer.calls == 0
    checkpoint_tuple = await saver.aget_tuple(config)
    assert checkpoint_tuple is not None
    assert [write[1] for write in checkpoint_tuple.pending_writes] == ["__resume__"]


@pytest.mark.asyncio
async def test_terminal_update_is_visible_before_blocked_saver_put_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BeforePutSaver(InMemorySaver):
        def __init__(self) -> None:
            super().__init__()
            self.blocked = asyncio.Event()
            self.release = asyncio.Event()
            self.approved_written = asyncio.Event()
            self.terminal_written = asyncio.Event()

        async def aput(
            self,
            config: object,
            checkpoint: object,
            metadata: object,
            new_versions: object,
        ) -> object:
            workflow = checkpoint.get("channel_values", {}).get("workflow", {})
            phase = workflow.get("phase")
            if phase == "outline_approved":
                self.blocked.set()
                await self.release.wait()
            result = await super().aput(config, checkpoint, metadata, new_versions)
            if phase == "outline_approved":
                self.approved_written.set()
            elif phase in ("draft_ready", "review_required"):
                self.terminal_written.set()
            return result

    saver = BeforePutSaver()
    adapter = _Adapter()
    compiled_type = type(graph_module._build_graph(adapter, InMemorySaver()))
    real_ainvoke = compiled_type.ainvoke
    durabilities: list[object] = []

    async def recording_ainvoke(
        self: object, value: object, *args: object, **kwargs: object
    ) -> object:
        durabilities.append(kwargs.get("durability"))
        return await real_ainvoke(self, value, *args, **kwargs)

    monkeypatch.setattr(compiled_type, "ainvoke", recording_ainvoke)
    paused = await graph_module.start_academic_workflow(
        _request(), adapter, checkpointer=saver
    )
    composer = _Composer()
    task = asyncio.create_task(
        graph_module.submit_academic_outline_decision(
            _command(paused.outline, "approve", "actor-A"),
            adapter,
            checkpointer=saver,
            composer=composer,
        )
    )
    await saver.blocked.wait()
    assert composer.calls == 0
    assert not saver.approved_written.is_set()
    saver.release.set()
    result = await task
    assert composer.calls == 1
    assert saver.approved_written.is_set()
    assert saver.terminal_written.is_set()
    assert durabilities[-1] == "sync"
    snapshot = await graph_module._build_graph(adapter, saver).aget_state(
        {"configurable": {"thread_id": "thread-1"}}
    )
    state = graph_module._safe_persistent_restore(snapshot.values)
    assert state == result
    assert (state.phase, state.status) == ("draft_ready", "completed")
    assert state.outline_decision is not None
    assert _events(state) == _APPROVE_EVENTS

    class TerminalFailureSaver(InMemorySaver):
        def __init__(self, *, after_write: bool) -> None:
            super().__init__()
            self.after_write = after_write
            self.failed = False
            self.enabled = True

        async def aput(
            self,
            config: object,
            checkpoint: object,
            metadata: object,
            new_versions: object,
        ) -> object:
            workflow = checkpoint.get("channel_values", {}).get("workflow", {})
            terminal = workflow.get("phase") in ("draft_ready", "review_required")
            if terminal and self.enabled and not self.after_write:
                raise RuntimeError("terminal-pre-write")
            if terminal and self.enabled and self.after_write and not self.failed:
                self.failed = True
                await super().aput(config, checkpoint, metadata, new_versions)
                raise RuntimeError("terminal-post-write")
            return await super().aput(config, checkpoint, metadata, new_versions)

        async def aput_writes(
            self,
            config: object,
            writes: object,
            task_id: str,
            task_path: str = "",
        ) -> None:
            writes_tuple = tuple(writes)
            terminal = any(
                channel == "workflow"
                and type(value) is dict
                and value.get("phase") in ("draft_ready", "review_required")
                for channel, value in writes_tuple
            )
            if terminal and self.enabled and not self.after_write:
                raise RuntimeError("terminal-pre-write")
            await super().aput_writes(config, writes_tuple, task_id, task_path)

    pre_write_saver = TerminalFailureSaver(after_write=False)
    pre_write_paused = await graph_module.start_academic_workflow(
        _request(), adapter, checkpointer=pre_write_saver
    )
    with pytest.raises(RuntimeError, match="^terminal-pre-write$"):
        await graph_module.submit_academic_outline_decision(
            _command(pre_write_paused.outline, "approve", "actor-A"),
            adapter,
            checkpointer=pre_write_saver,
            composer=_Composer(),
        )
    pre_write_snapshot = await graph_module._build_graph(
        adapter, pre_write_saver
    ).aget_state({"configurable": {"thread_id": "thread-1"}})
    pre_write_state = graph_module._safe_persistent_restore(
        pre_write_snapshot.values
    )
    assert (pre_write_state.phase, pre_write_state.status) == (
        "outline_approved",
        "running",
    )
    assert pre_write_snapshot.next == ("academic_draft_composer",)
    pre_write_saver.enabled = False
    retry_composer = _Composer()
    retried = await graph_module.resume_academic_workflow(
        _identity(), adapter, checkpointer=pre_write_saver, composer=retry_composer
    )
    assert retry_composer.calls == 1
    assert (retried.phase, retried.status) == ("draft_ready", "completed")

    post_write_saver = TerminalFailureSaver(after_write=True)
    post_write_paused = await graph_module.start_academic_workflow(
        _request(), adapter, checkpointer=post_write_saver
    )
    with pytest.raises(RuntimeError, match="^terminal-post-write$"):
        await graph_module.submit_academic_outline_decision(
            _command(post_write_paused.outline, "approve", "actor-A"),
            adapter,
            checkpointer=post_write_saver,
            composer=_Composer(),
        )
    post_write_snapshot = await graph_module._build_graph(
        adapter, post_write_saver
    ).aget_state({"configurable": {"thread_id": "thread-1"}})
    post_write_state = graph_module._safe_persistent_restore(
        post_write_snapshot.values
    )
    assert (post_write_state.phase, post_write_state.status) == (
        "draft_ready",
        "completed",
    )
    guarded_composer = _Composer()
    with pytest.raises(
        state_module.ThreadProtocolError,
        match="^academic workflow thread is not resumable$",
    ):
        await graph_module.resume_academic_workflow(
            _identity(),
            adapter,
            checkpointer=post_write_saver,
            composer=guarded_composer,
        )
    assert guarded_composer.calls == 0


@pytest.mark.asyncio
async def test_cancellation_after_terminal_visibility_preserves_commit() -> None:
    class AfterPutSaver(InMemorySaver):
        def __init__(self) -> None:
            super().__init__()
            self.blocked = asyncio.Event()

        async def aput(
            self,
            config: object,
            checkpoint: object,
            metadata: object,
            new_versions: object,
        ) -> object:
            result = await super().aput(config, checkpoint, metadata, new_versions)
            workflow = checkpoint.get("channel_values", {}).get("workflow", {})
            if workflow.get("phase") in ("outline_approved", "outline_rejected"):
                self.blocked.set()
                await asyncio.Event().wait()
            return result

    saver = AfterPutSaver()
    adapter = _Adapter()
    paused = await graph_module.start_academic_workflow(
        _request(), adapter, checkpointer=saver
    )
    command = _command(paused.outline, "reject", "actor-A")
    task = asyncio.create_task(
        graph_module.submit_academic_outline_decision(
            command, adapter, checkpointer=saver
        )
    )
    await saver.blocked.wait()
    task.cancel("AFTER-TERMINAL-VISIBLE")
    with pytest.raises(asyncio.CancelledError) as caught:
        await task
    assert caught.value.args[0] == "AFTER-TERMINAL-VISIBLE"
    snapshot = await graph_module._build_graph(adapter, saver).aget_state(
        {"configurable": {"thread_id": "thread-1"}}
    )
    terminal = graph_module._safe_persistent_restore(snapshot.values)
    assert (terminal.phase, terminal.status) == ("outline_rejected", "completed")
    assert terminal.outline_decision is not None
    assert terminal.outline_decision.actor_assertion == "actor-A"
    assert _events(terminal) == _REJECT_EVENTS
    with pytest.raises(
        state_module.OutlineDecisionProtocolError,
        match="^academic outline decision has already been committed$",
    ):
        await graph_module.submit_academic_outline_decision(
            command, adapter, checkpointer=saver
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("reuse_same_command", [True, False])
async def test_raw_shape_b_replay_accepts_same_or_value_equal_command(
    monkeypatch: pytest.MonkeyPatch,
    reuse_same_command: bool,
) -> None:
    saver = InMemorySaver()
    adapter = _Adapter()
    paused = await graph_module.start_academic_workflow(
        _request(), adapter, checkpointer=saver
    )
    graph = graph_module._build_graph(
        adapter, saver, nodes_module._composer_slot(_Composer())
    )
    config = {"configurable": {"thread_id": "thread-1"}}
    original = nodes_module._build_approval_terminal_state
    calls = 0

    def crash_once(state: object, command_value: object) -> object:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("raw-replay-secret")
        return original(state, command_value)

    monkeypatch.setattr(nodes_module, "_build_approval_terminal_state", crash_once)
    payload = _command(paused.outline, "approve", "actor-A").model_dump(mode="json")
    first = Command(resume=payload)
    with pytest.raises(nodes_module._OutlineApproveCommitError):
        await graph.ainvoke(first, config=config)
    retry = first if reuse_same_command else Command(resume=dict(payload))
    await graph.ainvoke(retry, config=config)
    snapshot = await graph.aget_state(config)
    terminal = graph_module._safe_persistent_restore(snapshot.values)
    assert terminal.outline_decision is not None
    assert terminal.outline_decision.decision == "approve"
    assert terminal.outline_decision.actor_assertion == "actor-A"


@pytest.mark.asyncio
async def test_raw_changed_decision_replays_retained_first_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saver = InMemorySaver()
    adapter = _Adapter()
    paused = await graph_module.start_academic_workflow(
        _request(), adapter, checkpointer=saver
    )
    graph = graph_module._build_graph(
        adapter, saver, nodes_module._composer_slot(_Composer())
    )
    config = {"configurable": {"thread_id": "thread-1"}}
    original = nodes_module._build_approval_terminal_state
    calls = 0

    def crash_once(state: object, command_value: object) -> object:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("retained-decision-secret")
        return original(state, command_value)

    monkeypatch.setattr(nodes_module, "_build_approval_terminal_state", crash_once)
    approve = _command(paused.outline, "approve", "actor-A")
    with pytest.raises(nodes_module._OutlineApproveCommitError):
        await graph.ainvoke(
            Command(resume=approve.model_dump(mode="json")), config=config
        )
    reject = _command(paused.outline, "reject", "actor-B")
    await graph.ainvoke(
        Command(resume=reject.model_dump(mode="json")), config=config
    )
    terminal = graph_module._safe_persistent_restore(
        (await graph.aget_state(config)).values
    )
    assert terminal.outline_decision is not None
    assert terminal.outline_decision.decision == "approve"
    assert terminal.outline_decision.actor_assertion == "actor-A"


def _assert_snapshot_oracle(
    kind: str,
    snapshot: StateSnapshot,
    checkpoint_tuple: object,
    state: object,
    *,
    decision: str,
) -> None:
    assert type(snapshot) is StateSnapshot
    assert type(snapshot.values) is dict
    expected_workflow = state.model_dump(mode="json")
    if kind != "terminal":
        expected_workflow.pop("outcome", None)
    assert snapshot.values == {"workflow": expected_workflow}
    workflow = snapshot.values["workflow"]
    assert workflow["request"]["report_mode"] == _FIXED_MODE
    assert workflow["request"]["report_locale"] == _FIXED_LOCALE
    assert workflow["outline"]["report_mode"] == _FIXED_MODE
    assert workflow["outline"]["report_locale"] == _FIXED_LOCALE
    assert tuple(
        (section["section_role"], section["title"])
        for section in workflow["outline"]["sections"]
    ) == _FIXED_PROFILE
    assert snapshot.created_at is not None and type(snapshot.created_at) is str
    assert snapshot.metadata == {
        "source": "loop",
        "step": 5 if kind == "terminal" and decision == "approve" else (
            4 if kind == "terminal" else 3
        ),
        "parents": {},
    }
    assert type(snapshot.config) is dict
    assert snapshot.config["configurable"]["thread_id"] == "thread-1"
    assert type(snapshot.config["configurable"]["checkpoint_id"]) is str
    assert snapshot.config["configurable"]["checkpoint_id"]
    assert type(snapshot.parent_config) is dict
    assert snapshot.parent_config["configurable"]["thread_id"] == "thread-1"
    assert type(snapshot.parent_config["configurable"]["checkpoint_id"]) is str
    assert checkpoint_tuple is not None
    assert checkpoint_tuple.config == snapshot.config
    assert checkpoint_tuple.metadata == snapshot.metadata
    assert checkpoint_tuple.parent_config == snapshot.parent_config
    checkpoint = checkpoint_tuple.checkpoint
    assert tuple(checkpoint) == (
        "v",
        "ts",
        "id",
        "channel_versions",
        "versions_seen",
        "updated_channels",
        "channel_values",
    )
    assert checkpoint["channel_values"]["workflow"] == expected_workflow

    if kind == "terminal":
        assert snapshot.next == ()
        assert snapshot.tasks == ()
        assert snapshot.interrupts == ()
        assert tuple(checkpoint["channel_values"]) == ("workflow",)
        assert checkpoint_tuple.pending_writes == []
        assert state.outline_decision is not None
        assert state.outline_decision.decision == decision
        assert state.outline_decision.actor_assertion == "actor-A"
        return

    assert tuple(checkpoint["channel_values"]) == (
        "workflow",
        "branch:to:outline_approval",
    )
    assert len(snapshot.tasks) == 1
    task = snapshot.tasks[0]
    assert type(task) is PregelTask
    assert type(task.id) is str and task.id
    assert task.name == "outline_approval"
    assert task.path == ("__pregel_pull", "outline_approval")
    assert task.state is None
    assert len(task.interrupts) == 1
    approval_interrupt = task.interrupts[0]
    assert type(approval_interrupt) is Interrupt
    assert approval_interrupt.value == _DEFAULT_INTERRUPT_PAYLOAD
    assert type(approval_interrupt.id) is str and approval_interrupt.id
    assert snapshot.interrupts == (approval_interrupt,)
    writes = checkpoint_tuple.pending_writes
    if kind == "pause":
        assert snapshot.next == ("outline_approval",)
        assert task.error is None and task.result is None
        assert writes == [(task.id, "__interrupt__", [approval_interrupt])]
        return

    marker_name = "Approve" if decision == "approve" else "Reject"
    marker = (
        f"_Outline{marker_name}CommitError("
        "'academic outline decision commit failed')"
    )
    expected_command = dict(_APPROVE_COMMAND_PAYLOAD)
    expected_command["decision"] = decision
    assert snapshot.next == ()
    assert type(task.error) is str and task.error == marker
    assert type(task.result) is dict and task.result == {}
    assert writes == [
        (task.id, "__interrupt__", [approval_interrupt]),
        ("00000000-0000-0000-0000-000000000000", "__resume__", expected_command),
        (task.id, "__resume__", [expected_command]),
        (task.id, "__error__", marker),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("decision", "error_type"),
    [
        ("approve", nodes_module._OutlineApproveCommitError),
        ("reject", nodes_module._OutlineRejectCommitError),
    ],
)
async def test_complete_snapshot_oracle_and_sensitive_walker(
    decision: str,
    error_type: type[BaseException],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saver = InMemorySaver()
    adapter = _Adapter()
    config = {"configurable": {"thread_id": "thread-1"}}
    paused = await graph_module.start_academic_workflow(
        _request(), adapter, checkpointer=saver
    )
    graph = graph_module._build_graph(
        adapter, saver, nodes_module._composer_slot(_Composer())
    )
    pause_snapshot = await graph.aget_state(config)
    pause_tuple = await saver.aget_tuple(config)
    _assert_snapshot_oracle(
        "pause", pause_snapshot, pause_tuple, paused, decision=decision
    )

    original = nodes_module._build_approval_terminal_state
    raw_error = RuntimeError("RAW-SNAPSHOT-SENTINEL")
    calls = 0

    def crash_once(state: object, command_value: object) -> object:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise raw_error
        return original(state, command_value)

    monkeypatch.setattr(nodes_module, "_build_approval_terminal_state", crash_once)
    first_command = _command(paused.outline, decision, "actor-A")
    with pytest.raises(error_type) as caught:
        await graph_module.submit_academic_outline_decision(
            first_command, adapter, checkpointer=saver
        )
    failure_snapshot = await graph.aget_state(config)
    failure_tuple = await saver.aget_tuple(config)
    failure_state = state_module.restore_workflow_state(failure_snapshot.values)
    _assert_snapshot_oracle(
        "failure",
        failure_snapshot,
        failure_tuple,
        failure_state,
        decision=decision,
    )
    _assert_safe_reachable(
        (failure_snapshot, failure_tuple),
        error=caught.value,
        forbidden_objects=(first_command, paused.outline, raw_error),
        forbidden_text=("RAW-SNAPSHOT-SENTINEL", "actor-B"),
    )

    retry_command = _command(paused.outline, decision, "actor-B")
    terminal = await graph_module.submit_academic_outline_decision(
        retry_command, adapter, checkpointer=saver, composer=_Composer()
    )
    terminal_snapshot = await graph.aget_state(config)
    terminal_tuple = await saver.aget_tuple(config)
    _assert_snapshot_oracle(
        "terminal",
        terminal_snapshot,
        terminal_tuple,
        terminal,
        decision=decision,
    )
    _assert_safe_reachable(
        (terminal_snapshot, terminal_tuple),
        forbidden_objects=(retry_command, paused.outline, raw_error),
        forbidden_text=("RAW-SNAPSHOT-SENTINEL", "actor-B"),
    )
    assert (adapter.plan_calls, adapter.evidence_calls, adapter.outline_calls) == (
        1,
        1,
        1,
    )


def _assert_safe_reachable(
    roots: tuple[object, ...] = (),
    *,
    error: BaseException | None = None,
    forbidden_objects: tuple[object, ...] = (),
    forbidden_types: tuple[type[object], ...] = (),
    forbidden_text: tuple[str, ...] = (),
) -> None:
    forbidden_ids = {id(value) for value in forbidden_objects}
    seen: set[int] = set()

    def visit(value: object) -> None:
        value_id = id(value)
        if value_id in seen:
            return
        seen.add(value_id)
        assert value_id not in forbidden_ids
        value_type = type(value)
        assert value_type not in forbidden_types
        if value_type is str:
            assert all(text not in value for text in forbidden_text)
            return
        if value_type is bytes:
            assert all(text.encode("utf-8") not in value for text in forbidden_text)
            return
        if value_type is dict:
            for key, item in value.items():
                visit(key)
                visit(item)
            return
        if value_type in (list, tuple, set, frozenset):
            for item in value:
                visit(item)
            return
        if value_type is types.FunctionType:
            for cell in value.__closure__ or ():
                for item in gc.get_referents(cell):
                    visit(item)
            return
        if value_type in (
            types.ModuleType,
            type,
            types.CodeType,
            types.FrameType,
            types.TracebackType,
        ):
            return
        for item in gc.get_referents(value):
            visit(item)

    for root in roots:
        visit(root)
    if error is not None:
        current = error.__traceback__
        assert current is not None
        current = current.tb_next
        while current is not None:
            for local_value in current.tb_frame.f_locals.values():
                visit(local_value)
            current = current.tb_next
        visit(error)


@pytest.mark.asyncio
async def test_fixed_errors_drop_command_actor_outline_and_validation_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saver = InMemorySaver()
    adapter = _Adapter()
    paused = await graph_module.start_academic_workflow(
        _request(), adapter, checkpointer=saver
    )
    command = _command(paused.outline, "approve", "ACTOR-TRACEBACK-SENTINEL")

    def crash(_state: object, _command_value: object) -> object:
        raise RuntimeError("RAW-TRACEBACK-SENTINEL")

    monkeypatch.setattr(nodes_module, "_build_approval_terminal_state", crash)
    with pytest.raises(nodes_module._OutlineApproveCommitError) as caught:
        await graph_module.submit_academic_outline_decision(
            command, adapter, checkpointer=saver
        )
    _assert_safe_reachable(
        error=caught.value,
        forbidden_objects=(command,),
        forbidden_types=(
            state_module.AcademicOutlineDecisionCommand,
            state_module.AcademicWorkflowState,
            WorkflowOutline,
        ),
        forbidden_text=(
            "ACTOR-TRACEBACK-SENTINEL",
            "RAW-TRACEBACK-SENTINEL",
        ),
    )

    class ValidationInput:
        pass

    invalid = ValidationInput()
    with pytest.raises(state_module.OutlineDecisionProtocolError) as invalid_caught:
        await graph_module.submit_academic_outline_decision(
            invalid, adapter, checkpointer=saver
        )
    _assert_safe_reachable(
        error=invalid_caught.value,
        forbidden_objects=(invalid,),
        forbidden_types=(ValidationInput,),
    )


def test_shape_classifier_never_executes_hostile_objects() -> None:
    counters = {
        "eq": 0,
        "property": 0,
        "descriptor": 0,
        "getattribute": 0,
        "repr": 0,
        "iter": 0,
    }

    class Hostile:
        def __eq__(self, _other: object) -> bool:
            counters["eq"] += 1
            raise AssertionError("equality must not execute")

        def __getattribute__(self, name: str) -> object:
            if name not in {"__class__", "__dict__"}:
                counters["getattribute"] += 1
                raise AssertionError("attribute access must not execute")
            return object.__getattribute__(self, name)

        @property
        def next(self) -> object:
            counters["property"] += 1
            raise AssertionError("property must not execute")

        def __repr__(self) -> str:
            counters["repr"] += 1
            raise AssertionError("repr must not execute")

        def __iter__(self):
            counters["iter"] += 1
            raise AssertionError("iteration must not execute")

    class Descriptor:
        def __get__(self, _instance: object, _owner: object) -> object:
            counters["descriptor"] += 1
            raise AssertionError("descriptor must not execute")

    class HostileDescriptor:
        next = Descriptor()

    state = _pause_state()
    assert graph_module._approval_shape(Hostile(), state) is None
    assert graph_module._approval_shape(HostileDescriptor(), state) is None

    hostile_task_snapshot = StateSnapshot(
        values={},
        next=("outline_approval",),
        config={},
        metadata={},
        created_at="created",
        parent_config=None,
        tasks=(Hostile(),),
        interrupts=(),
    )
    assert graph_module._approval_shape(hostile_task_snapshot, state) is None

    hostile_interrupt_task = PregelTask(
        id="task-1",
        name="outline_approval",
        path=("__pregel_pull", "outline_approval"),
        error=None,
        interrupts=(Hostile(),),
        state=None,
        result=None,
    )
    hostile_interrupt_snapshot = hostile_task_snapshot._replace(
        tasks=(hostile_interrupt_task,)
    )
    assert graph_module._approval_shape(hostile_interrupt_snapshot, state) is None

    hostile_payload_task = hostile_interrupt_task._replace(
        interrupts=(Interrupt(value={"workflow_id": Hostile()}),)
    )
    hostile_payload_snapshot = hostile_task_snapshot._replace(
        tasks=(hostile_payload_task,)
    )
    assert graph_module._approval_shape(hostile_payload_snapshot, state) is None

    class TextSubclass(str):
        pass

    text_subclass_task = hostile_interrupt_task._replace(
        name=TextSubclass("outline_approval"),
        interrupts=(Interrupt(value={}),),
    )
    text_subclass_snapshot = hostile_task_snapshot._replace(
        tasks=(text_subclass_task,)
    )
    assert graph_module._approval_shape(text_subclass_snapshot, state) is None
    assert counters == {
        "eq": 0,
        "property": 0,
        "descriptor": 0,
        "getattribute": 0,
        "repr": 0,
        "iter": 0,
    }
