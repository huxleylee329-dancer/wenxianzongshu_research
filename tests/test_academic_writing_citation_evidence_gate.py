"""Focused behavior tests for the deterministic citation-evidence gate."""

from __future__ import annotations

import hashlib
import inspect
import json
import types

import pytest
from pydantic import ValidationError

from gpt_researcher.workflows.academic_writing import citation_evidence_gate as module
from gpt_researcher.workflows.academic_writing.citation_evidence_gate import (
    WorkflowCitationEvidenceGateResult,
    gate_citation_evidence,
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


_OMIT = object()
_DEFAULT_PROVENANCE = object()


def _event(order: int, event_type: str, node_id: str | None) -> WorkflowEvent:
    return WorkflowEvent(
        event_id=f"event:{order:06d}",
        order=order,
        event_type=event_type,
        node_id=node_id,
        attempt=1,
    )


_PAUSE_EVENTS = (
    _event(1, "node_started", "topic_planner"),
    _event(2, "node_completed", "topic_planner"),
    _event(3, "node_started", "research_evidence"),
    _event(4, "node_completed", "research_evidence"),
    _event(5, "node_started", "outline_writer"),
    _event(6, "node_completed", "outline_writer"),
)
_APPROVE_EVENTS = _PAUSE_EVENTS + (
    _event(7, "node_started", "outline_approval"),
    _event(8, "node_completed", "outline_approval"),
    _event(9, "workflow_completed", None),
)
_REJECT_EVENTS = _PAUSE_EVENTS + (
    _event(7, "node_started", "outline_approval"),
    _event(8, "node_completed", "outline_approval"),
    _event(9, "workflow_rejected", None),
)


def _source(order: int) -> WorkflowEvidenceSource:
    return WorkflowEvidenceSource(
        source_id=f"evidence-source:{order:06d}",
        order=order,
        title=f"Source {order}",
        url=f"https://example.test/{order}",
        candidate_id=None,
    )


def _provenance(order: int, block: str = "Evidence block.") -> WorkflowEvidenceProvenance:
    return WorkflowEvidenceProvenance(
        source_id=f"evidence-source:{order:06d}",
        evidence_blocks=(block,),
    )


def _section(order: int) -> WorkflowOutlineSection:
    return WorkflowOutlineSection(
        section_id=f"section:{order:06d}",
        order=order,
        title=f"Section {order}",
        brief=f"Brief {order}",
    )


def _approved_state(
    *,
    section_count: int = 1,
    source_count: int = 1,
    provenance: object = _DEFAULT_PROVENANCE,
    approved: bool = True,
) -> AcademicWorkflowState:
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
        source_urls=(),
        document_urls=(),
        query_domains=(),
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
    evidence_values: dict[str, object] = {
        "evidence_id": "evidence:000001",
        "topic_plan_id": "topic-plan:000001",
        "attempt": 1,
        "context_blocks": ("Bounded evidence.",),
        "sources": tuple(_source(order) for order in range(1, source_count + 1)),
    }
    if provenance is _DEFAULT_PROVENANCE:
        evidence_values["provenance"] = (_provenance(1),)
    elif provenance is not _OMIT:
        evidence_values["provenance"] = provenance
    evidence = WorkflowResearchEvidence(**evidence_values)
    outline = WorkflowOutline(
        outline_id="outline:000001",
        evidence_id="evidence:000001",
        attempt=1,
        title="Approved outline",
        sections=tuple(_section(order) for order in range(1, section_count + 1)),
    )
    outline_bytes = json.dumps(
        outline.model_dump(mode="json"),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    decision = "approve" if approved else "reject"
    record = WorkflowOutlineDecisionRecord(
        decision_id="outline-decision:000001",
        schema_version="1",
        workflow_id="workflow-1",
        thread_id="thread-1",
        run_id="run-1",
        outline_id="outline:000001",
        outline_digest=hashlib.sha256(outline_bytes).hexdigest(),
        decision=decision,
        actor_assertion="actor-A",
        attempt=1,
    )
    return AcademicWorkflowState(
        schema_version="1",
        workflow_id="workflow-1",
        thread_id="thread-1",
        run_id="run-1",
        phase="outline_approved" if approved else "outline_rejected",
        status="completed",
        request=request,
        topic_plan=plan,
        research_evidence=evidence,
        outline=outline,
        outline_decision=record,
        errors=(),
        events=_APPROVE_EVENTS if approved else _REJECT_EVENTS,
    )


def _draft(order: int, content: object | None = None) -> WorkflowSectionDraft:
    value = (
        f"Body [[cite:evidence-source:{order:06d}]]"
        if content is None
        else content
    )
    if type(value) is str:
        return WorkflowSectionDraft(
            outline_id="outline:000001",
            section_id=f"section:{order:06d}",
            attempt=1,
            content=value,
        )
    return WorkflowSectionDraft.model_construct(
        outline_id="outline:000001",
        section_id=f"section:{order:06d}",
        attempt=1,
        content=value,
    )


def _drafts(count: int, content: str | None = None) -> tuple[WorkflowSectionDraft, ...]:
    return tuple(_draft(order, content) for order in range(1, count + 1))


def _assert_fixed(error: BaseException) -> None:
    assert type(error) is module._CitationEvidenceGateError
    assert str(error) == "citation evidence gate failed"
    assert error.__cause__ is None
    assert error.__context__ is None
    assert error.__suppress_context__ is False


def _canonical(result: WorkflowCitationEvidenceGateResult) -> bytes:
    return json.dumps(
        result.model_dump(mode="json"),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def test_public_surface_dto_and_success_are_exact() -> None:
    assert module.__all__ == (
        "WorkflowCitationEvidenceGateResult",
        "gate_citation_evidence",
    )
    assert str(inspect.signature(gate_citation_evidence)) == (
        "(state: 'AcademicWorkflowState', drafts: "
        "'tuple[WorkflowSectionDraft, ...]') -> "
        "'WorkflowCitationEvidenceGateResult'"
    )
    state = _approved_state()
    drafts = _drafts(1)
    state_before = state.model_dump(mode="json")
    drafts_before = tuple(draft.model_dump(mode="json") for draft in drafts)
    result = gate_citation_evidence(state, drafts)
    assert type(result) is WorkflowCitationEvidenceGateResult
    assert result == WorkflowCitationEvidenceGateResult(
        outline_id="outline:000001",
        section_ids=("section:000001",),
        cited_source_ids_by_section=(("evidence-source:000001",),),
        attempt=1,
    )
    restored = WorkflowCitationEvidenceGateResult.model_validate_json(_canonical(result))
    assert type(restored.section_ids) is tuple
    assert type(restored.cited_source_ids_by_section) is tuple
    assert type(restored.cited_source_ids_by_section[0]) is tuple
    assert restored == result
    assert _canonical(restored) == _canonical(result)
    assert state.model_dump(mode="json") == state_before
    assert tuple(draft.model_dump(mode="json") for draft in drafts) == drafts_before
    with pytest.raises(ValidationError):
        result.attempt = 2  # type: ignore[misc]


@pytest.mark.parametrize(
    "case",
    (
        "mapping_subclass",
        "section_list",
        "outer_list",
        "inner_list",
        "bool_attempt",
        "string_attempt",
        "string_subclass",
        "duplicate_citation",
        "empty_citation",
        "outer_mismatch",
        "zero_sections",
        "thirteen_sections",
        "sixty_five_citations",
        "json_bool_attempt",
        "missing_field",
        "extra_field",
    ),
)
def test_result_dto_rejects_invalid_shape_matrix(case: str) -> None:
    class _MappingSubclass(dict[str, object]):
        pass

    class _StringSubclass(str):
        pass

    values: dict[str, object] = {
        "outline_id": "outline:000001",
        "section_ids": ("section:000001",),
        "cited_source_ids_by_section": (("evidence-source:000001",),),
        "attempt": 1,
    }
    if case == "mapping_subclass":
        candidate: object = _MappingSubclass(values)
    else:
        candidate = values
        if case == "section_list":
            values["section_ids"] = ["section:000001"]
        elif case == "outer_list":
            values["cited_source_ids_by_section"] = [("evidence-source:000001",)]
        elif case == "inner_list":
            values["cited_source_ids_by_section"] = (["evidence-source:000001"],)
        elif case == "bool_attempt":
            values["attempt"] = True
        elif case == "string_attempt":
            values["attempt"] = "1"
        elif case == "string_subclass":
            values["section_ids"] = (_StringSubclass("section:000001"),)
        elif case == "duplicate_citation":
            values["cited_source_ids_by_section"] = (
                ("evidence-source:000001", "evidence-source:000001"),
            )
        elif case == "empty_citation":
            values["cited_source_ids_by_section"] = ((),)
        elif case == "outer_mismatch":
            values["cited_source_ids_by_section"] = (
                ("evidence-source:000001",),
                ("evidence-source:000001",),
            )
        elif case == "zero_sections":
            values["section_ids"] = ()
            values["cited_source_ids_by_section"] = ()
        elif case == "thirteen_sections":
            values["section_ids"] = tuple(
                f"section:{order:06d}" for order in range(1, 14)
            )
            values["cited_source_ids_by_section"] = tuple(
                (("evidence-source:000001",)) for _ in range(13)
            )
        elif case == "sixty_five_citations":
            values["cited_source_ids_by_section"] = (
                tuple(f"evidence-source:{order:06d}" for order in range(1, 66)),
            )
        elif case == "missing_field":
            del values["outline_id"]
        elif case == "extra_field":
            values["extra"] = "forbidden"
    with pytest.raises((TypeError, ValidationError)):
        if case == "json_bool_attempt":
            WorkflowCitationEvidenceGateResult.model_validate_json(
                json.dumps(dict(values, attempt=True), separators=(",", ":"))
            )
        else:
            WorkflowCitationEvidenceGateResult.model_validate(candidate)


@pytest.mark.parametrize(
    "case",
    (
        "state_type",
        "rejected",
        "draft_list",
        "count",
        "order",
        "request",
        "topic_plan",
        "research_evidence",
        "source",
        "provenance",
        "outline",
        "outline_section",
        "outline_decision",
        "event",
    ),
)
def test_preflight_and_nested_exact_type_matrix(case: str) -> None:
    state: object = _approved_state(section_count=2)
    drafts: object = _drafts(2)
    if case == "state_type":
        state = object()
    elif case == "rejected":
        state = _approved_state(section_count=2, approved=False)
    elif case == "draft_list":
        drafts = list(_drafts(2))
    elif case == "count":
        drafts = _drafts(1)
    elif case == "order":
        drafts = tuple(reversed(_drafts(2)))
    else:
        state_namespace = object.__getattribute__(state, "__dict__")
        if case in {"request", "topic_plan", "research_evidence", "outline", "outline_decision"}:
            dict.__setitem__(state_namespace, case, object())
        elif case == "event":
            events = dict.__getitem__(state_namespace, "events")
            dict.__setitem__(state_namespace, "events", (object(),) + tuple(events[1:]))
        else:
            evidence = dict.__getitem__(state_namespace, "research_evidence")
            evidence_namespace = object.__getattribute__(evidence, "__dict__")
            if case in {"source", "provenance"}:
                field = "sources" if case == "source" else "provenance"
                dict.__setitem__(evidence_namespace, field, (object(),))
            else:
                outline = dict.__getitem__(state_namespace, "outline")
                outline_namespace = object.__getattribute__(outline, "__dict__")
                dict.__setitem__(outline_namespace, "sections", (object(),))
    with pytest.raises(module._CitationEvidenceGateError) as caught:
        gate_citation_evidence(state, drafts)  # type: ignore[arg-type]
    _assert_fixed(caught.value)


class _HostileContent:
    calls = 0

    def _called(self) -> None:
        type(self).calls += 1
        raise AssertionError("hostile content executed")

    def __eq__(self, other: object) -> bool:
        self._called()

    def __iter__(self):
        self._called()

    def __repr__(self) -> str:
        self._called()

    def __str__(self) -> str:
        self._called()

    def strip(self) -> str:
        self._called()


@pytest.mark.parametrize("shape", ("omitted_empty", "full_empty", "omitted_nonempty"))
def test_empty_provenance_precedes_every_content_read(
    shape: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    provenance: object = _OMIT if shape == "omitted_empty" else ()
    state = _approved_state(provenance=provenance)
    if shape == "omitted_nonempty":
        state = _approved_state(provenance=(_provenance(1),))
        evidence = object.__getattribute__(state, "__dict__")["research_evidence"]
        fields_set = object.__getattribute__(evidence, "__pydantic_fields_set__")
        set.remove(fields_set, "provenance")
    content = _HostileContent()
    drafts = (_draft(1, content),)
    calls = {"content": 0, "marker": 0}
    original_content = module._copy_draft_contents
    original_marker = module._scan_citations

    def copy_contents(*args: object, **kwargs: object):
        calls["content"] += 1
        return original_content(*args, **kwargs)

    def scan_citations(*args: object, **kwargs: object):
        calls["marker"] += 1
        return original_marker(*args, **kwargs)

    monkeypatch.setattr(module, "_copy_draft_contents", copy_contents)
    monkeypatch.setattr(module, "_scan_citations", scan_citations)
    _HostileContent.calls = 0
    with pytest.raises(module._CitationEvidenceGateError) as caught:
        gate_citation_evidence(state, drafts)
    _assert_fixed(caught.value)
    assert calls == {"content": 0, "marker": 0}
    assert _HostileContent.calls == 0


@pytest.mark.parametrize(
    "case",
    (
        "marker_free",
        "missing_suffix",
        "empty_marker",
        "stray_bracket",
        "unknown_source",
        "source_without_provenance",
        "empty_block",
        "literal_url",
        "stable_success",
    ),
)
def test_marker_and_provenance_matrix(case: str) -> None:
    state = _approved_state(source_count=2, provenance=(_provenance(1),))
    content = "Body [[cite:evidence-source:000001]]"
    if case == "marker_free":
        content = "Body"
    elif case == "missing_suffix":
        content = "Body [[cite:evidence-source:000001]"
    elif case == "empty_marker":
        content = "Body [[cite:]]"
    elif case == "stray_bracket":
        content = "Body [ [[cite:evidence-source:000001]]"
    elif case == "unknown_source":
        content = "Body [[cite:evidence-source:999999]]"
    elif case == "source_without_provenance":
        content = "Body [[cite:evidence-source:000002]]"
    elif case == "empty_block":
        provenance = state.research_evidence.provenance[0]  # type: ignore[union-attr]
        object.__getattribute__(provenance, "__dict__")["evidence_blocks"] = ("",)
    elif case == "literal_url":
        content += " https://example.test"
    elif case == "stable_success":
        state = _approved_state(
            source_count=2,
            provenance=(_provenance(1), _provenance(2)),
        )
        content = (
            "[[cite:evidence-source:000002]] then "
            "[[cite:evidence-source:000001]] and [[cite:evidence-source:000002]]"
        )
    if case == "stable_success":
        result = gate_citation_evidence(state, (_draft(1, content),))
        assert result.cited_source_ids_by_section == (
            ("evidence-source:000002", "evidence-source:000001"),
        )
    else:
        with pytest.raises(module._CitationEvidenceGateError) as caught:
            gate_citation_evidence(state, (_draft(1, content),))
        _assert_fixed(caught.value)


@pytest.mark.parametrize(
    "case",
    (
        "hostile",
        "string_subclass",
        "shadowed_method",
        "descriptor_subclass",
        "hostile_block",
        "no_rebuild",
    ),
)
def test_stage_b_static_content_matrix(
    case: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _StringSubclass(str):
        pass

    class _DescriptorDraft(WorkflowSectionDraft):
        @property
        def hostile_descriptor(self) -> object:
            _HostileContent.calls += 1
            raise AssertionError("hostile descriptor executed")

    draft = _draft(1)
    state = _approved_state()
    hostile = _HostileContent()
    if case == "hostile":
        object.__getattribute__(draft, "__dict__")["content"] = hostile
    elif case == "string_subclass":
        object.__getattribute__(draft, "__dict__")["content"] = _StringSubclass(
            "Body [[cite:evidence-source:000001]]"
        )
    elif case == "shadowed_method":
        object.__getattribute__(draft, "__dict__")["model_dump"] = hostile
    elif case == "descriptor_subclass":
        draft = _DescriptorDraft(
            outline_id="outline:000001",
            section_id="section:000001",
            attempt=1,
            content="Body [[cite:evidence-source:000001]]",
        )
    elif case == "hostile_block":
        provenance = state.research_evidence.provenance[0]  # type: ignore[union-attr]
        object.__getattribute__(provenance, "__dict__")["evidence_blocks"] = (
            hostile,
        )
    else:
        calls = {"restore": 0}

        def forbidden_restore(*args: object, **kwargs: object) -> object:
            calls["restore"] += 1
            raise AssertionError("draft reconstruction executed")

        monkeypatch.setattr(
            WorkflowSectionDraft,
            "model_validate_json",
            forbidden_restore,
        )
        result = gate_citation_evidence(state, (draft,))
        assert result.section_ids == ("section:000001",)
        assert calls["restore"] == 0
        return
    _HostileContent.calls = 0
    with pytest.raises(module._CitationEvidenceGateError) as caught:
        gate_citation_evidence(state, (draft,))
    _assert_fixed(caught.value)
    assert _HostileContent.calls == 0


def test_source_25_is_allowed_without_projected_allowlist_reconstruction() -> None:
    state = _approved_state(
        source_count=25,
        provenance=(_provenance(25),),
    )
    draft = _draft(1, "Body [[cite:evidence-source:000025]]")
    result = gate_citation_evidence(state, (draft,))
    assert result.cited_source_ids_by_section == (("evidence-source:000025",),)


def test_reachable_12_by_64_maximum_is_exact() -> None:
    provenance = tuple(_provenance(order, "E") for order in range(1, 65))
    state = _approved_state(section_count=12, source_count=64, provenance=provenance)
    content = " ".join(
        f"[[cite:evidence-source:{order:06d}]]" for order in range(1, 65)
    )
    result = gate_citation_evidence(state, _drafts(12, content))
    assert sum(len(ids) for ids in result.cited_source_ids_by_section) == 768
    assert len(_canonical(result)) == 19519


def test_fixed_error_traceback_has_no_sensitive_inputs_or_partial_result() -> None:
    secret_block = "secret-evidence-block"
    state = _approved_state(
        section_count=2,
        provenance=(_provenance(1, secret_block),),
    )
    drafts = (
        _draft(1),
        _draft(2, "marker free final section"),
    )
    with pytest.raises(module._CitationEvidenceGateError) as caught:
        gate_citation_evidence(state, drafts)
    error = caught.value
    _assert_fixed(error)
    targets = (state, drafts, drafts[0], drafts[1], secret_block)
    traceback = error.__traceback__
    while traceback is not None:
        frame = traceback.tb_frame
        if frame.f_globals.get("__name__") == module.__name__:
            pending = list(frame.f_locals.values())
            seen: set[int] = set()
            while pending:
                value = pending.pop()
                if any(value is target for target in targets):
                    raise AssertionError("sensitive input retained by production traceback")
                identity = id(value)
                if identity in seen:
                    continue
                seen.add(identity)
                if type(value) is dict:
                    pending.extend(dict.keys(value))
                    pending.extend(dict.values(value))
                elif type(value) in (tuple, list, set, frozenset):
                    pending.extend(value)
                elif type(value) is types.FunctionType and value.__closure__ is not None:
                    pending.extend(cell.cell_contents for cell in value.__closure__)
        traceback = traceback.tb_next
