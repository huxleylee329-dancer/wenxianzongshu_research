"""Focused behavior tests for the deterministic academic review handoff."""

from __future__ import annotations

import hashlib
import json

import pytest
from pydantic import ValidationError

from gpt_researcher.workflows.academic_writing import (
    academic_review_handoff as module,
)
from gpt_researcher.workflows.academic_writing.academic_draft_composer import (
    WorkflowAcademicDraftComposition,
)
from gpt_researcher.workflows.academic_writing.academic_review_handoff import (
    WorkflowAcademicReviewHandoff,
    build_academic_review_handoff,
)
from gpt_researcher.workflows.academic_writing.citation_evidence_gate import (
    WorkflowCitationEvidenceGateResult,
    gate_citation_evidence,
)
from gpt_researcher.workflows.academic_writing.citation_review_disposition import (
    WorkflowCitationReviewDisposition,
    gate_citation_review_disposition,
)
from gpt_researcher.workflows.academic_writing.citation_reviewer import (
    WorkflowSectionCitationReview,
)
from gpt_researcher.workflows.academic_writing.references_renderer import (
    render_references,
)
from gpt_researcher.workflows.academic_writing.section_merger import merge_sections
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
    _outline_digest,
)
from gpt_researcher.workflows.academic_writing.report_profiles import (
    _get_report_profile,
)


def _canonical(value: object) -> bytes:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")  # type: ignore[union-attr]
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


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


def _source(order: int) -> WorkflowEvidenceSource:
    return WorkflowEvidenceSource(
        source_id=f"evidence-source:{order:06d}",
        order=order,
        title=f"Source {order}",
        url=f"https://example.test/{order}",
        candidate_id=None,
    )


def _approved_state(
    *,
    section_count: int = 2,
    sources: tuple[WorkflowEvidenceSource, ...] | None = None,
    provenance: tuple[WorkflowEvidenceProvenance, ...] | None = None,
    omit_provenance: bool = False,
) -> AcademicWorkflowState:
    source_values = (
        tuple(_source(order) for order in range(1, 5))
        if sources is None
        else sources
    )
    provenance_values = (
        tuple(
            WorkflowEvidenceProvenance(
                source_id=source.source_id,
                evidence_blocks=("Evidence.",),
            )
            for source in source_values[:64]
        )
        if provenance is None
        else provenance
    )
    sections = tuple(
        WorkflowOutlineSection(
            section_id=f"section:{order:06d}",
            order=order,
            title=f"Section {order}",
            brief="B",
        )
        for order in range(1, section_count + 1)
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
    evidence_values: dict[str, object] = {
        "evidence_id": "evidence:000001",
        "topic_plan_id": "topic-plan:000001",
        "attempt": 1,
        "context_blocks": ("C",),
        "sources": source_values,
    }
    if not omit_provenance:
        evidence_values["provenance"] = provenance_values
    evidence = WorkflowResearchEvidence(**evidence_values)
    outline = WorkflowOutline(
        outline_id="outline:000001",
        evidence_id="evidence:000001",
        attempt=1,
        title="O",
        sections=sections,
    )
    outline_digest = _outline_digest(outline)
    decision = WorkflowOutlineDecisionRecord(
        decision_id="outline-decision:000001",
        schema_version="1",
        workflow_id="workflow-1",
        thread_id="thread-1",
        run_id="run-1",
        outline_id="outline:000001",
        outline_digest=outline_digest,
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


def _issues(verdict: str) -> tuple[str, ...]:
    if verdict == "supported":
        return ()
    if verdict == "uncertain":
        return ("insufficient_evidence",)
    return ("possible_contradiction",)


def _composition(
    state: AcademicWorkflowState,
    *,
    verdict: str = "uncertain",
    citation_orders: tuple[tuple[int, ...], ...] | None = None,
) -> WorkflowAcademicDraftComposition:
    assert state.outline is not None
    if citation_orders is None:
        citation_orders = tuple((1,) for _ in state.outline.sections)
    drafts = tuple(
        WorkflowSectionDraft(
            outline_id=state.outline.outline_id,
            section_id=section.section_id,
            attempt=1,
            content="Claim "
            + " ".join(
                f"[[cite:evidence-source:{order:06d}]]"
                for order in citation_orders[index]
            ),
        )
        for index, section in enumerate(state.outline.sections)
    )
    gate = gate_citation_evidence(state, drafts)
    reviews = tuple(
        WorkflowSectionCitationReview(
            outline_id=gate.outline_id,
            section_id=gate.section_ids[index],
            cited_source_ids=gate.cited_source_ids_by_section[index],
            verdict=verdict,
            issues=_issues(verdict),
            rationale="Bounded model opinion.",
            attempt=1,
        )
        for index in range(len(gate.section_ids))
    )
    disposition = gate_citation_review_disposition(gate, reviews)
    if disposition.disposition == "ready":
        merged = merge_sections(state, drafts)
        referenced = render_references(state, merged, gate, disposition)
    else:
        merged = None
        referenced = None
    return WorkflowAcademicDraftComposition(
        drafts=drafts,
        gate_result=gate,
        reviews=reviews,
        disposition=disposition,
        merged_draft=merged,
        referenced_draft=referenced,
    )


def _assert_fixed(error: BaseException) -> None:
    assert type(error) is module._AcademicReviewHandoffError
    assert str(error) == "academic review handoff failed"
    assert error.__cause__ is None
    assert error.__context__ is None


@pytest.mark.parametrize("verdict", ("uncertain", "unsupported"))
def test_non_ready_success_identity_projection_json_and_nonmutation(
    monkeypatch: pytest.MonkeyPatch,
    verdict: str,
) -> None:
    state = _approved_state()
    composition = _composition(
        state,
        verdict=verdict,
        citation_orders=((2, 1), (1, 3)),
    )
    state_before = _canonical(state)
    composition_before = _canonical(composition)
    events: list[str] = []

    def gate_wrapper(
        gate_state: AcademicWorkflowState,
        drafts: tuple[WorkflowSectionDraft, ...],
    ) -> WorkflowCitationEvidenceGateResult:
        events.append("gate")
        return gate_citation_evidence(gate_state, drafts)

    def disposition_wrapper(
        gate: WorkflowCitationEvidenceGateResult,
        reviews: tuple[WorkflowSectionCitationReview, ...],
    ) -> WorkflowCitationReviewDisposition:
        events.append("disposition")
        return gate_citation_review_disposition(gate, reviews)

    monkeypatch.setattr(module, "_gate_citation_evidence", gate_wrapper)
    monkeypatch.setattr(
        module,
        "_gate_citation_review_disposition",
        disposition_wrapper,
    )

    result = build_academic_review_handoff(state, composition)

    assert module.__all__ == (
        "WorkflowAcademicReviewHandoff",
        "build_academic_review_handoff",
    )
    assert result.composition is composition
    assert result.cited_sources == (
        state.research_evidence.sources[1],
        state.research_evidence.sources[0],
        state.research_evidence.sources[2],
    )
    assert result.cited_provenance == (
        state.research_evidence.provenance[1],
        state.research_evidence.provenance[0],
        state.research_evidence.provenance[2],
    )
    assert all(
        result.cited_sources[index] is state.research_evidence.sources[source_index]
        for index, source_index in enumerate((1, 0, 2))
    )
    assert all(
        result.cited_provenance[index]
        is state.research_evidence.provenance[source_index]
        for index, source_index in enumerate((1, 0, 2))
    )
    assert all(
        item is not state.research_evidence.sources[3]
        for item in result.cited_sources
    )
    assert all(
        item is not state.research_evidence.provenance[3]
        for item in result.cited_provenance
    )
    assert events == ["gate", "disposition"]
    dumped = result.model_dump(mode="json")
    assert type(dumped["cited_sources"]) is list
    assert type(dumped["cited_provenance"]) is list
    restored = WorkflowAcademicReviewHandoff.model_validate_json(_canonical(result))
    assert restored.model_dump(mode="json") == dumped
    with pytest.raises(ValidationError):
        result.cited_sources = ()  # type: ignore[misc]
    assert _canonical(state) == state_before
    assert _canonical(composition) == composition_before


@pytest.mark.parametrize(
    "case",
    ("ready", "python_list", "empty", "misaligned", "source_subclass"),
)
def test_strict_dto_and_non_ready_shape_matrix(case: str) -> None:
    state = _approved_state()
    composition = _composition(
        state,
        verdict="supported" if case == "ready" else "uncertain",
    )
    if case == "ready":
        with pytest.raises((ValidationError, module._AcademicReviewHandoffError)):
            build_academic_review_handoff(state, composition)
        return
    source = state.research_evidence.sources[0]
    provenance = state.research_evidence.provenance[0]
    values: dict[str, object] = {
        "composition": composition,
        "cited_sources": (source,),
        "cited_provenance": (provenance,),
    }
    if case == "python_list":
        values["cited_sources"] = [source]
    elif case == "empty":
        values["cited_sources"] = ()
        values["cited_provenance"] = ()
    elif case == "misaligned":
        values["cited_provenance"] = (
            WorkflowEvidenceProvenance(
                source_id="evidence-source:000002",
                evidence_blocks=("E",),
            ),
        )
    else:
        class _SourceSubclass(WorkflowEvidenceSource):
            pass

        values["cited_sources"] = (
            _SourceSubclass(**source.model_dump()),
        )
    with pytest.raises((TypeError, ValidationError)):
        WorkflowAcademicReviewHandoff(**values)


@pytest.mark.parametrize("shape", ("full_empty", "omitted_empty"))
def test_legacy_empty_provenance_shapes_fail_fixed(
    monkeypatch: pytest.MonkeyPatch,
    shape: str,
) -> None:
    valid_state = _approved_state()
    composition = _composition(valid_state)
    empty_state = _approved_state(
        provenance=(),
        omit_provenance=shape == "omitted_empty",
    )
    events: list[str] = []

    def gate_wrapper(
        gate_state: AcademicWorkflowState,
        drafts: tuple[WorkflowSectionDraft, ...],
    ) -> WorkflowCitationEvidenceGateResult:
        events.append("gate")
        return gate_citation_evidence(gate_state, drafts)

    def disposition_wrapper(
        gate: WorkflowCitationEvidenceGateResult,
        reviews: tuple[WorkflowSectionCitationReview, ...],
    ) -> WorkflowCitationReviewDisposition:
        events.append("disposition")
        return gate_citation_review_disposition(gate, reviews)

    monkeypatch.setattr(module, "_gate_citation_evidence", gate_wrapper)
    monkeypatch.setattr(
        module,
        "_gate_citation_review_disposition",
        disposition_wrapper,
    )

    with pytest.raises(module._AcademicReviewHandoffError) as caught:
        build_academic_review_handoff(empty_state, composition)
    _assert_fixed(caught.value)
    assert events == ["gate"]


@pytest.mark.parametrize(
    ("case", "expected_events"),
    (
        ("gate_error", ("gate",)),
        ("gate_mismatch", ("gate",)),
        ("disposition_error", ("gate", "disposition")),
        ("disposition_mismatch", ("gate", "disposition")),
    ),
)
def test_recomputation_order_mismatch_and_fixed_failure(
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    expected_events: tuple[str, ...],
) -> None:
    state = _approved_state()
    composition = _composition(state, citation_orders=((1, 2), (2, 1)))
    events: list[str] = []

    def gate_wrapper(
        gate_state: AcademicWorkflowState,
        drafts: tuple[WorkflowSectionDraft, ...],
    ) -> WorkflowCitationEvidenceGateResult:
        events.append("gate")
        if case == "gate_error":
            raise RuntimeError("sensitive gate failure")
        value = gate_citation_evidence(gate_state, drafts)
        if case == "gate_mismatch":
            first = tuple(reversed(value.cited_source_ids_by_section[0]))
            return WorkflowCitationEvidenceGateResult(
                outline_id=value.outline_id,
                section_ids=value.section_ids,
                cited_source_ids_by_section=(first,)
                + value.cited_source_ids_by_section[1:],
                attempt=1,
            )
        return value

    def disposition_wrapper(
        gate: WorkflowCitationEvidenceGateResult,
        reviews: tuple[WorkflowSectionCitationReview, ...],
    ) -> WorkflowCitationReviewDisposition:
        events.append("disposition")
        if case == "disposition_error":
            raise RuntimeError("sensitive disposition failure")
        value = gate_citation_review_disposition(gate, reviews)
        if case == "disposition_mismatch":
            return WorkflowCitationReviewDisposition(
                outline_id=value.outline_id,
                section_ids=value.section_ids,
                section_dispositions=("blocked",) * len(value.section_ids),
                disposition="blocked",
                attempt=1,
            )
        return value

    monkeypatch.setattr(module, "_gate_citation_evidence", gate_wrapper)
    monkeypatch.setattr(
        module,
        "_gate_citation_review_disposition",
        disposition_wrapper,
    )

    with pytest.raises(module._AcademicReviewHandoffError) as caught:
        build_academic_review_handoff(state, composition)
    _assert_fixed(caught.value)
    assert tuple(events) == expected_events


class _HostileMember:
    def __init__(self, target: str) -> None:
        self.target = target
        self.calls = 0

    def __hash__(self) -> int:
        self.calls += 1
        return hash(self.target)

    def __eq__(self, other: object) -> bool:
        self.calls += 1
        raise AssertionError("hostile equality executed")

    def __repr__(self) -> str:
        self.calls += 1
        raise AssertionError("hostile repr executed")

    def __iter__(self):
        self.calls += 1
        raise AssertionError("hostile iterator executed")

    def __getattr__(self, name: str) -> object:
        self.calls += 1
        raise AssertionError("hostile dynamic attribute executed")


class _HostileString(str):
    calls = 0

    def __eq__(self, other: object) -> bool:
        type(self).calls += 1
        raise AssertionError("hostile string equality executed")

    def __str__(self) -> str:
        type(self).calls += 1
        raise AssertionError("hostile string conversion executed")

    def __repr__(self) -> str:
        type(self).calls += 1
        raise AssertionError("hostile string repr executed")

    def __getattr__(self, name: str) -> object:
        type(self).calls += 1
        raise AssertionError("hostile string dynamic attribute executed")


class _HostileCallable:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, *args: object, **kwargs: object) -> object:
        self.calls += 1
        raise AssertionError("shadowed method executed")


@pytest.mark.parametrize(
    "case",
    (
        "composition_key",
        "fields_member",
        "source_key",
        "composition_cap_plus_one",
        "draft_string_subclass",
        "source_string_subclass",
        "provenance_string_subclass",
        "composition_string_subclass",
        "instance_shadow",
    ),
)
def test_hostile_static_surface_executes_no_dynamic_code(
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    state = _approved_state()
    composition = _composition(state)
    hostile: _HostileMember | _HostileCallable | None = None
    gate_calls = 0
    if case == "composition_key":
        target = "merged_draft"
        namespace = object.__getattribute__(composition, "__dict__")
        value = dict.pop(namespace, target)
        hostile = _HostileMember(target)
        dict.__setitem__(namespace, hostile, value)
    elif case == "fields_member":
        target = "reviews"
        fields_set = object.__getattribute__(composition, "__pydantic_fields_set__")
        set.remove(fields_set, target)
        hostile = _HostileMember(target)
        set.add(fields_set, hostile)
    elif case == "source_key":
        target = "title"
        source = state.research_evidence.sources[0]
        namespace = object.__getattribute__(source, "__dict__")
        value = dict.pop(namespace, target)
        hostile = _HostileMember(target)
        dict.__setitem__(namespace, hostile, value)
    if case == "composition_cap_plus_one":
        state = _max_state()
        composition = _max_composition(state)
        draft_index = 0
        while "A" not in composition.drafts[draft_index].content:
            draft_index += 1
        draft = composition.drafts[draft_index]
        replacement = WorkflowSectionDraft(
            outline_id=draft.outline_id,
            section_id=draft.section_id,
            attempt=1,
            content=draft.content.replace("A", "é", 1),
        )
        replacement_drafts = (
            composition.drafts[:draft_index]
            + (replacement,)
            + composition.drafts[draft_index + 1 :]
        )
        object.__getattribute__(composition, "__dict__")["drafts"] = replacement_drafts
        assert len(_canonical(composition)) == 1526997
        old_gate = gate_citation_evidence(state, replacement_drafts)
        old_disposition = gate_citation_review_disposition(
            old_gate,
            composition.reviews,
        )
        assert old_disposition.disposition == composition.disposition.disposition

        def gate_wrapper(
            gate_state: AcademicWorkflowState,
            drafts: tuple[WorkflowSectionDraft, ...],
        ) -> WorkflowCitationEvidenceGateResult:
            nonlocal gate_calls
            gate_calls += 1
            return gate_citation_evidence(gate_state, drafts)

        monkeypatch.setattr(module, "_gate_citation_evidence", gate_wrapper)
    elif case == "draft_string_subclass":
        draft = composition.drafts[0]
        object.__getattribute__(draft, "__dict__")["content"] = _HostileString(
            draft.content
        )
    elif case == "source_string_subclass":
        source = state.research_evidence.sources[0]
        object.__getattribute__(source, "__dict__")["title"] = _HostileString(
            source.title
        )
    elif case == "provenance_string_subclass":
        provenance = state.research_evidence.provenance[0]
        block = provenance.evidence_blocks[0]
        object.__getattribute__(provenance, "__dict__")["evidence_blocks"] = (
            _HostileString(block),
        )
    elif case == "composition_string_subclass":
        gate = composition.gate_result
        object.__getattribute__(gate, "__dict__")["outline_id"] = _HostileString(
            gate.outline_id
        )
    elif case == "instance_shadow":
        hostile = _HostileCallable()
        object.__getattribute__(composition, "__dict__")["model_dump"] = hostile
    if hostile is not None:
        hostile.calls = 0
    _HostileString.calls = 0

    with pytest.raises(module._AcademicReviewHandoffError) as caught:
        build_academic_review_handoff(state, composition)
    _assert_fixed(caught.value)
    if hostile is not None:
        assert hostile.calls == 0
    assert _HostileString.calls == 0
    if case == "composition_cap_plus_one":
        assert gate_calls == 0


def test_fixed_failure_traceback_releases_sensitive_inputs() -> None:
    sentinel = object()
    sensitive_content = "sensitive-content-" + "x" * 64
    state = _approved_state()
    composition = _composition(state)
    draft = composition.drafts[0]
    object.__getattribute__(draft, "__dict__")["content"] = sensitive_content
    targets = (sentinel, sensitive_content, state, composition, draft)

    with pytest.raises(module._AcademicReviewHandoffError) as caught:
        build_academic_review_handoff(state, composition)
    error = caught.value
    _assert_fixed(error)
    traceback = error.__traceback__
    while traceback is not None:
        frame = traceback.tb_frame
        if frame.f_globals.get("__name__") == module.__name__:
            for local_value in frame.f_locals.values():
                assert all(local_value is not target for target in targets)
                if type(local_value) is tuple or type(local_value) is list:
                    for item in local_value:
                        assert all(item is not target for target in targets)
                elif type(local_value) is dict:
                    for key, value in local_value.items():
                        assert all(key is not target for target in targets)
                        assert all(value is not target for target in targets)
        traceback = traceback.tb_next


def _max_control_value(length: int, index: int) -> str:
    bits = "".join(chr((index >> shift) & 1) for shift in range(6))
    return "\x00" + bits + "\x00" * (length - len(bits) - 1)


def _max_state() -> AcademicWorkflowState:
    sources = tuple(
        WorkflowEvidenceSource(
            source_id=f"evidence-source:{order:06d}",
            order=order,
            title="\x00" * 512,
            url=_max_control_value(4096, order - 1),
            candidate_id=_max_control_value(256, order - 1),
        )
        for order in range(1, 65)
    )
    provenance = tuple(
        WorkflowEvidenceProvenance(
            source_id=source.source_id,
            evidence_blocks=("\x00" * 4096,),
        )
        for source in sources
    )
    return _approved_state(
        section_count=12,
        sources=sources,
        provenance=provenance,
    )


def _max_composition(state: AcademicWorkflowState) -> WorkflowAcademicDraftComposition:
    marker_text = " ".join(
        f"[[cite:evidence-source:{order:06d}]]" for order in range(1, 65)
    )
    filler_length = 24576 - len(marker_text) - 1

    def make_drafts(nul_count: int, unicode_count: int) -> tuple[WorkflowSectionDraft, ...]:
        values: list[WorkflowSectionDraft] = []
        assert state.outline is not None
        for section in state.outline.sections:
            take_nul = min(filler_length, nul_count)
            nul_count -= take_nul
            available = filler_length - take_nul
            take_unicode = min(available, unicode_count)
            unicode_count -= take_unicode
            filler = (
                "\x00" * take_nul
                + "\U0010ffff" * take_unicode
                + "A" * (filler_length - take_nul - take_unicode)
            )
            values.append(
                WorkflowSectionDraft(
                    outline_id="outline:000001",
                    section_id=section.section_id,
                    attempt=1,
                    content=marker_text + " " + filler,
                )
            )
        assert nul_count == 0 and unicode_count == 0
        return tuple(values)

    def make_composition(
        drafts: tuple[WorkflowSectionDraft, ...],
    ) -> WorkflowAcademicDraftComposition:
        gate = gate_citation_evidence(state, drafts)
        reviews = tuple(
            WorkflowSectionCitationReview(
                outline_id=gate.outline_id,
                section_id=gate.section_ids[index],
                cited_source_ids=gate.cited_source_ids_by_section[index],
                verdict="uncertain",
                issues=(
                    "insufficient_evidence",
                    "possible_contradiction",
                    "citation_placement_unclear",
                ),
                rationale="\U0010ffff" * 2048,
                attempt=1,
            )
            for index in range(12)
        )
        disposition = gate_citation_review_disposition(gate, reviews)
        return WorkflowAcademicDraftComposition(
            drafts=drafts,
            gate_result=gate,
            reviews=reviews,
            disposition=disposition,
            merged_draft=None,
            referenced_draft=None,
        )

    base = make_composition(make_drafts(0, 0))
    delta = 1526996 - len(_canonical(base))
    assert delta >= 0
    unicode_count = next(
        count for count in range(5) if delta >= 3 * count and (delta - 3 * count) % 5 == 0
    )
    nul_count = (delta - 3 * unicode_count) // 5
    assert nul_count + unicode_count <= filler_length * 12
    result = make_composition(make_drafts(nul_count, unicode_count))
    assert len(_canonical(result)) == 1526996
    return result


def test_joint_natural_maximum_is_publicly_reachable() -> None:
    state = _max_state()
    composition = _max_composition(state)

    result = build_academic_review_handoff(state, composition)
    dumped = result.model_dump(mode="json")

    assert result.composition is composition
    assert len(result.cited_sources) == 64
    assert len(result.cited_provenance) == 64
    assert sum(len(item.evidence_blocks) for item in result.cited_provenance) == 64
    assert (
        sum(
            len(block)
            for item in result.cited_provenance
            for block in item.evidence_blocks
        )
        == 262144
    )
    assert sum(len(ids) for ids in composition.gate_result.cited_source_ids_by_section) == 768
    assert len(_canonical(dumped["composition"])) == 1526996
    assert len(_canonical(dumped["cited_sources"])) == 1873400
    assert len(_canonical(dumped["cited_provenance"])) == 1576833
    assert len(_canonical(dumped)) == 4977282


def test_fixed_profile_state_builds_same_non_ready_handoff_projection() -> None:
    state = _approved_state(section_count=8)
    profile = _get_report_profile("stem_literature_review")
    assert profile is not None and state.outline is not None
    request_namespace = object.__getattribute__(state.request, "__dict__")
    dict.__setitem__(request_namespace, "report_mode", "stem_literature_review")
    dict.__setitem__(request_namespace, "report_locale", "zh-CN")
    request_fields = object.__getattribute__(state.request, "__pydantic_fields_set__")
    set.add(request_fields, "report_mode")
    set.add(request_fields, "report_locale")
    outline_namespace = object.__getattribute__(state.outline, "__dict__")
    dict.__setitem__(outline_namespace, "report_mode", "stem_literature_review")
    dict.__setitem__(outline_namespace, "report_locale", "zh-CN")
    outline_fields = object.__getattribute__(state.outline, "__pydantic_fields_set__")
    set.add(outline_fields, "report_mode")
    set.add(outline_fields, "report_locale")
    for section, (role, title) in zip(state.outline.sections, profile, strict=True):
        namespace = object.__getattribute__(section, "__dict__")
        dict.__setitem__(namespace, "section_role", role)
        dict.__setitem__(namespace, "title", title)
        set.add(
            object.__getattribute__(section, "__pydantic_fields_set__"),
            "section_role",
        )
    assert state.outline_decision is not None
    dict.__setitem__(
        object.__getattribute__(state.outline_decision, "__dict__"),
        "outline_digest",
        _outline_digest(state.outline),
    )
    composition = _composition(state, verdict="uncertain")
    handoff = build_academic_review_handoff(state, composition)
    assert handoff.composition is composition
    assert handoff.cited_sources == (state.research_evidence.sources[0],)  # type: ignore[union-attr]
    assert handoff.cited_provenance == (
        state.research_evidence.provenance[0],  # type: ignore[union-attr]
    )
