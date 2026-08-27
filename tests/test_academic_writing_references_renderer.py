"""Focused behavior tests for the deterministic references renderer."""

from __future__ import annotations

import hashlib
import inspect
import json

import pytest
from pydantic import ValidationError

from gpt_researcher.workflows.academic_writing import references_renderer as module
from gpt_researcher.workflows.academic_writing.citation_evidence_gate import (
    WorkflowCitationEvidenceGateResult,
)
from gpt_researcher.workflows.academic_writing.citation_review_disposition import (
    WorkflowCitationReviewDisposition,
)
from gpt_researcher.workflows.academic_writing.references_renderer import (
    WorkflowReferencedDraft,
    render_references,
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
    WorkflowTopicPlan,
    _outline_digest,
)
from gpt_researcher.workflows.academic_writing.report_profiles import (
    _get_report_profile,
)


_OMIT = object()


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
    sources: tuple[WorkflowEvidenceSource, ...] | None = None,
    provenance: object = _OMIT,
) -> AcademicWorkflowState:
    if sources is None:
        sources = (_source(1),)
    request = AcademicWorkflowRequest(
        workflow_mode="academic_langgraph",
        workflow_id="workflow-1",
        thread_id="thread-1",
        run_id="run-1",
        query="Deterministic references",
        report_type="research_report",
        report_source="web",
        tone="objective",
        language="en",
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
        research_topic="Deterministic references",
        research_questions=("How are references rendered?",),
    )
    evidence_values: dict[str, object] = {
        "evidence_id": "evidence:000001",
        "topic_plan_id": "topic-plan:000001",
        "attempt": 1,
        "context_blocks": ("Bounded evidence.",),
        "sources": sources,
    }
    if provenance is not _OMIT:
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
    decision = WorkflowOutlineDecisionRecord(
        decision_id="outline-decision:000001",
        schema_version="1",
        workflow_id="workflow-1",
        thread_id="thread-1",
        run_id="run-1",
        outline_id="outline:000001",
        outline_digest=_outline_digest(outline),
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


def _merged(section_count: int, content: object) -> WorkflowMergedDraft:
    if type(content) is str:
        return WorkflowMergedDraft(
            outline_id="outline:000001",
            section_ids=tuple(
                f"section:{order:06d}" for order in range(1, section_count + 1)
            ),
            attempt=1,
            content=content,
        )
    return WorkflowMergedDraft.model_construct(
        outline_id="outline:000001",
        section_ids=tuple(
            f"section:{order:06d}" for order in range(1, section_count + 1)
        ),
        attempt=1,
        content=content,
    )


def _gate(
    citations: tuple[tuple[str, ...], ...],
) -> WorkflowCitationEvidenceGateResult:
    return WorkflowCitationEvidenceGateResult(
        outline_id="outline:000001",
        section_ids=tuple(
            f"section:{order:06d}" for order in range(1, len(citations) + 1)
        ),
        cited_source_ids_by_section=citations,
        attempt=1,
    )


def _disposition(
    section_count: int,
    *,
    value: str = "ready",
) -> WorkflowCitationReviewDisposition:
    return WorkflowCitationReviewDisposition(
        outline_id="outline:000001",
        section_ids=tuple(
            f"section:{order:06d}" for order in range(1, section_count + 1)
        ),
        section_dispositions=tuple(value for _ in range(section_count)),
        disposition=value,
        attempt=1,
    )


def _bundle(
    *,
    section_count: int = 1,
    source_count: int = 1,
    content: str | None = None,
    citations: tuple[tuple[str, ...], ...] | None = None,
    provenance: object = _OMIT,
) -> tuple[
    AcademicWorkflowState,
    WorkflowMergedDraft,
    WorkflowCitationEvidenceGateResult,
    WorkflowCitationReviewDisposition,
]:
    sources = tuple(_source(order) for order in range(1, source_count + 1))
    if citations is None:
        citations = tuple(
            ((f"evidence-source:{order:06d}",))
            for order in range(1, section_count + 1)
        )
    if content is None:
        flattened: list[str] = []
        for inner in citations:
            for source_id in inner:
                if source_id not in flattened:
                    flattened.append(source_id)
        content = " ".join(f"[[cite:{source_id}]]" for source_id in flattened)
    return (
        _approved_state(
            section_count=section_count,
            sources=sources,
            provenance=provenance,
        ),
        _merged(section_count, content),
        _gate(citations),
        _disposition(section_count),
    )


def _canonical(value: object) -> bytes:
    if type(value) is WorkflowReferencedDraft:
        value = value.model_dump(mode="json")
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _assert_fixed(error: BaseException) -> None:
    assert type(error) is module._ReferencesRendererError
    assert str(error) == "references renderer failed"
    assert error.__cause__ is None
    assert error.__context__ is None
    assert error.__suppress_context__ is False


def _corrupt(model: object, field: str, value: object) -> object:
    copied = model.model_copy(deep=True)  # type: ignore[attr-defined]
    namespace = object.__getattribute__(copied, "__dict__")
    dict.__setitem__(namespace, field, value)
    return copied


class _TupleSubclass(tuple):
    pass


class _StringSubclass(str):
    pass


def test_public_surface_success_nonmutation_and_determinism() -> None:
    citations = (
        ("evidence-source:000002",),
        ("evidence-source:000001", "evidence-source:000002"),
    )
    opaque = (
        "[cite:evidence-source:000001] "
        "[[CITE:evidence-source:000001]] "
        " [[ cite:evidence-source:000001]]"
    )
    content = (
        "Body [[cite:evidence-source:000002]] "
        + opaque
        + " [[cite:evidence-source:000001]] "
        + "[[cite:evidence-source:000002]]"
    )
    inputs = _bundle(
        section_count=2,
        source_count=2,
        content=content,
        citations=citations,
    )
    before = tuple(value.model_dump(mode="json") for value in inputs)

    first = render_references(*inputs)
    second = render_references(*inputs)

    assert module.__all__ == ("WorkflowReferencedDraft", "render_references")
    assert str(inspect.signature(render_references)) == (
        "(state: 'AcademicWorkflowState', merged_draft: 'WorkflowMergedDraft', "
        "gate_result: 'WorkflowCitationEvidenceGateResult', disposition: "
        "'WorkflowCitationReviewDisposition') -> 'WorkflowReferencedDraft'"
    )
    assert type(first) is WorkflowReferencedDraft
    assert first == second
    assert first.reference_source_ids == (
        "evidence-source:000002",
        "evidence-source:000001",
    )
    assert first.content.startswith(content + "\n\n## References\n\n")
    assert opaque in first.content
    assert tuple(value.model_dump(mode="json") for value in inputs) == before
    assert type(first.section_ids) is tuple
    assert type(first.reference_source_ids) is tuple
    restored = WorkflowReferencedDraft.model_validate_json(_canonical(first))
    assert restored == first
    assert type(restored.section_ids) is tuple
    assert type(restored.reference_source_ids) is tuple
    with pytest.raises(ValidationError):
        first.attempt = 2  # type: ignore[misc]


@pytest.mark.parametrize(
    "case",
    (
        "mapping_subclass",
        "python_list",
        "tuple_subclass",
        "string_subclass",
        "bool_attempt",
        "null_content",
        "extra",
        "bad_section",
        "duplicate_reference",
        "blank_content",
        "canonical_bytes",
    ),
)
def test_output_dto_exactness_matrix(case: str) -> None:
    payload: dict[str, object] = {
        "outline_id": "outline:000001",
        "section_ids": ("section:000001",),
        "reference_source_ids": ("evidence-source:000001",),
        "attempt": 1,
        "content": "Referenced content",
    }
    value: object = payload
    if case == "mapping_subclass":
        class _MappingSubclass(dict):
            pass

        value = _MappingSubclass(payload)
    elif case == "python_list":
        payload["section_ids"] = ["section:000001"]
    elif case == "tuple_subclass":
        payload["section_ids"] = _TupleSubclass(("section:000001",))
    elif case == "string_subclass":
        payload["content"] = _StringSubclass("Referenced content")
    elif case == "bool_attempt":
        payload["attempt"] = True
    elif case == "null_content":
        payload["content"] = None
    elif case == "extra":
        payload["extra"] = "forbidden"
    elif case == "bad_section":
        payload["section_ids"] = ("section:000002",)
    elif case == "duplicate_reference":
        payload["reference_source_ids"] = (
            "evidence-source:000001",
            "evidence-source:000001",
        )
    elif case == "blank_content":
        payload["content"] = "   "
    else:
        payload["content"] = "😀" * 2_149_100
    with pytest.raises((TypeError, ValidationError)):
        WorkflowReferencedDraft.model_validate(value)


@pytest.mark.parametrize(
    ("case", "succeeds"),
    (
        ("omitted_empty", True),
        ("explicit_empty", True),
        ("omitted_nonempty", False),
        ("null", False),
        ("missing_dict_value", False),
        ("extra_state", False),
    ),
)
def test_provenance_surface_matrix(case: str, succeeds: bool) -> None:
    provenance: object = _OMIT if case == "omitted_empty" else ()
    state, merged, gate, disposition = _bundle(provenance=provenance)
    if case == "omitted_nonempty":
        entry = WorkflowEvidenceProvenance(
            source_id="evidence-source:000001",
            evidence_blocks=("Evidence.",),
        )
        state = _approved_state(provenance=(entry,))
        evidence = object.__getattribute__(state, "__dict__")["research_evidence"]
        fields_set = object.__getattribute__(evidence, "__pydantic_fields_set__")
        set.remove(fields_set, "provenance")
    elif case == "null":
        evidence = object.__getattribute__(state, "__dict__")["research_evidence"]
        namespace = object.__getattribute__(evidence, "__dict__")
        dict.__setitem__(namespace, "provenance", None)
    elif case == "missing_dict_value":
        evidence = object.__getattribute__(state, "__dict__")["research_evidence"]
        dict.__delitem__(object.__getattribute__(evidence, "__dict__"), "provenance")
    elif case == "extra_state":
        dict.__setitem__(object.__getattribute__(state, "__dict__"), "extra", 1)

    if succeeds:
        result = render_references(state, merged, gate, disposition)
        assert result.reference_source_ids == ("evidence-source:000001",)
    else:
        with pytest.raises(RuntimeError) as raised:
            render_references(state, merged, gate, disposition)
        _assert_fixed(raised.value)


@pytest.mark.parametrize(
    "case",
    (
        "state_type",
        "merged_type",
        "gate_type",
        "disposition_type",
        "state_phase",
        "state_attempt",
        "merged_attempt",
        "gate_attempt",
        "disposition_attempt",
        "outline_mismatch",
        "section_mismatch",
        "unknown_source",
        "not_ready",
        "extra_merged_field",
        "extra_gate_field_set",
    ),
)
def test_input_binding_failure_matrix(case: str) -> None:
    state, merged, gate, disposition = _bundle(source_count=2)
    values: list[object] = [state, merged, gate, disposition]
    if case == "state_type":
        class _StateSubclass(AcademicWorkflowState):
            pass

        values[0] = _StateSubclass.model_validate(state.model_dump())
    elif case == "merged_type":
        class _MergedSubclass(WorkflowMergedDraft):
            pass

        values[1] = _MergedSubclass.model_validate(merged.model_dump())
    elif case == "gate_type":
        class _GateSubclass(WorkflowCitationEvidenceGateResult):
            pass

        values[2] = _GateSubclass.model_validate(gate.model_dump())
    elif case == "disposition_type":
        class _DispositionSubclass(WorkflowCitationReviewDisposition):
            pass

        values[3] = _DispositionSubclass.model_validate(disposition.model_dump())
    elif case == "state_phase":
        values[0] = _corrupt(state, "phase", "initialized")
    elif case == "state_attempt":
        copied = state.model_copy(deep=True)
        outline = object.__getattribute__(copied, "__dict__")["outline"]
        dict.__setitem__(object.__getattribute__(outline, "__dict__"), "attempt", True)
        values[0] = copied
    elif case == "merged_attempt":
        values[1] = _corrupt(merged, "attempt", True)
    elif case == "gate_attempt":
        values[2] = _corrupt(gate, "attempt", True)
    elif case == "disposition_attempt":
        values[3] = _corrupt(disposition, "attempt", True)
    elif case == "outline_mismatch":
        values[1] = _corrupt(merged, "outline_id", "outline:999999")
    elif case == "section_mismatch":
        values[1] = WorkflowMergedDraft(
            outline_id="outline:000001",
            section_ids=("section:000002",),
            attempt=1,
            content=merged.content,
        )
    elif case == "unknown_source":
        values[1] = _merged(1, "[[cite:evidence-source:000002]]")
        values[2] = _gate((("evidence-source:000002",),))
        values[0] = _approved_state(sources=(_source(1),))
    elif case == "not_ready":
        values[3] = _disposition(1, value="needs_human_review")
    elif case == "extra_merged_field":
        copied = merged.model_copy(deep=True)
        dict.__setitem__(object.__getattribute__(copied, "__dict__"), "extra", 1)
        values[1] = copied
    else:
        copied = gate.model_copy(deep=True)
        set.add(object.__getattribute__(copied, "__pydantic_fields_set__"), "extra")
        values[2] = copied
    with pytest.raises(RuntimeError) as raised:
        render_references(*values)  # type: ignore[arg-type]
    _assert_fixed(raised.value)


def test_citation_scale_matrix_without_independent_200_case() -> None:
    ids = tuple(f"evidence-source:{order:06d}" for order in range(1, 65))
    state = _approved_state(sources=tuple(_source(order) for order in range(1, 65)))
    content = " ".join(f"[[cite:{source_id}]]" for source_id in ids)
    one = render_references(state, _merged(1, content), _gate((ids,)), _disposition(1))
    assert one.reference_source_ids == ids

    citations = tuple(ids for _ in range(12))
    aggregate = render_references(
        _approved_state(
            section_count=12,
            sources=tuple(_source(order) for order in range(1, 65)),
        ),
        _merged(12, content),
        _gate(citations),
        _disposition(12),
    )
    assert sum(len(inner) for inner in citations) == 768
    assert aggregate.reference_source_ids == ids

    with pytest.raises(ValidationError):
        _gate((tuple(f"evidence-source:{order:06d}" for order in range(1, 66)),))


@pytest.mark.parametrize(
    "case",
    (
        "unclosed",
        "empty",
        "invalid",
        "nested",
        "illegal_tail_bracket",
        "illegal_tail_open",
        "unknown",
        "extra",
        "missing",
        "reordered",
    ),
)
def test_global_marker_failure_matrix(case: str) -> None:
    state, _, gate, disposition = _bundle(source_count=2)
    contents = {
        "unclosed": "[[cite:evidence-source:000001]",
        "empty": "[[cite:]]",
        "invalid": "[[cite:evidence-source:00001]]",
        "nested": "[[cite:[[cite:evidence-source:000001]]]]",
        "illegal_tail_bracket": "[[cite:evidence-source:000001]]]",
        "illegal_tail_open": "[[cite:evidence-source:000001]][",
        "unknown": "[[cite:evidence-source:000003]]",
        "extra": (
            "[[cite:evidence-source:000001]] "
            "[[cite:evidence-source:000002]]"
        ),
        "missing": "[cite:evidence-source:000001]",
        "reordered": (
            "[[cite:evidence-source:000002]] "
            "[[cite:evidence-source:000001]]"
        ),
    }
    if case == "reordered":
        gate = _gate(
            (("evidence-source:000001", "evidence-source:000002"),)
        )
    with pytest.raises(RuntimeError) as raised:
        render_references(state, _merged(1, contents[case]), gate, disposition)
    _assert_fixed(raised.value)


def test_opaque_repeats_redistribution_and_fabricated_inputs_are_accepted() -> None:
    opaque = (
        "[cite:evidence-source:000002] "
        "[[CITE:evidence-source:000002]] "
        " [[ cite:evidence-source:000002]]"
    )
    content = (
        "## Apparent Section 2\n[[cite:evidence-source:000001]] "
        + opaque
        + "\n## Apparent Section 1\n[[cite:evidence-source:000002]] "
        + "[[cite:evidence-source:000001]]"
    )
    citations = (
        ("evidence-source:000001",),
        ("evidence-source:000002",),
    )
    state, merged, gate, disposition = _bundle(
        section_count=2,
        source_count=2,
        content=content,
        citations=citations,
        provenance=(),
    )
    result = render_references(state, merged, gate, disposition)
    assert result.content[: len(content)] == content
    assert result.reference_source_ids == (
        "evidence-source:000001",
        "evidence-source:000002",
    )
    assert opaque in result.content


def test_json_escaping_and_exact_template_golden() -> None:
    title = 'T[]()`_*\\"é\n\t\0'
    url = 'https://example.test/a(b)\\"\r\n\t\0'
    state = _approved_state(sources=(_source(1, title=title, url=url),))
    merged = _merged(1, "Body [[cite:evidence-source:000001]]")
    result = render_references(
        state,
        merged,
        _gate((("evidence-source:000001",),)),
        _disposition(1),
    )
    line = (
        "    [1] source_id="
        + json.dumps("evidence-source:000001", ensure_ascii=False)
        + " title="
        + json.dumps(title, ensure_ascii=False)
        + " url="
        + json.dumps(url, ensure_ascii=False)
    )
    expected = merged.content + "\n\n## References\n\n" + line
    assert result.content == expected
    assert not result.content.endswith("\n")


def test_joint_200_id_and_all_length_maxima() -> None:
    sources: list[WorkflowEvidenceSource] = []
    for order in range(1, 201):
        url_values = ["\0"] * 4096
        url_values[order] = "\x01"
        sources.append(
            _source(
                order,
                title="\0" * 512,
                url="".join(url_values),
            )
        )
    ids = tuple(f"evidence-source:{order:06d}" for order in range(1, 201))
    sizes = (64, 64, 63, 1, 1, 1, 1, 1, 1, 1, 1, 1)
    citations: list[tuple[str, ...]] = []
    offset = 0
    for size in sizes:
        citations.append(ids[offset : offset + size])
        offset += size
    markers = tuple(f"[[cite:{source_id}]]" for source_id in ids)
    nul_count = 359_538 - sum(len(marker) for marker in markers)
    merged_content = "\0".join(markers) + "\0" * (nul_count - 199)
    result = render_references(
        _approved_state(
            section_count=12,
            sources=tuple(sources),
            provenance=(),
        ),
        _merged(12, merged_content),
        _gate(tuple(citations)),
        _disposition(12),
    )
    bibliography = result.content[len(merged_content) :]
    assert max(len(inner) for inner in citations) == 64
    assert sum(len(inner) for inner in citations) == 200 <= 768
    assert result.reference_source_ids == ids
    assert len(bibliography) == 5_541_708
    assert len(result.content) == 5_901_246
    assert len(_canonical(result)) == 8_596_240

    invalid = result.model_dump()
    invalid["content"] = result.content + "X"
    with pytest.raises(ValidationError):
        WorkflowReferencedDraft.model_validate(invalid)


class _Hostile:
    def __init__(self, calls: list[str]) -> None:
        object.__setattr__(self, "_calls", calls)

    def __getattribute__(self, name: str) -> object:
        if name == "_calls":
            return object.__getattribute__(self, name)
        object.__getattribute__(self, "_calls").append("getattribute")
        raise AssertionError("dynamic attribute access executed")

    def __eq__(self, other: object) -> bool:
        object.__getattribute__(self, "_calls").append("eq")
        raise AssertionError("equality executed")

    def __iter__(self) -> object:
        object.__getattribute__(self, "_calls").append("iter")
        raise AssertionError("iteration executed")

    def __repr__(self) -> str:
        object.__getattribute__(self, "_calls").append("repr")
        raise AssertionError("repr executed")

    def __str__(self) -> str:
        object.__getattribute__(self, "_calls").append("str")
        raise AssertionError("str executed")


def test_hostile_input_and_fixed_error_traceback_are_safe() -> None:
    calls: list[str] = []
    state, _, gate, disposition = _bundle()
    hostile = _merged(1, _Hostile(calls))
    with pytest.raises(RuntimeError) as raised:
        render_references(state, hostile, gate, disposition)
    _assert_fixed(raised.value)
    assert calls == []

    sentinel = "sensitive-merged-content-" + "z" * 257
    sensitive_content = sentinel + " [[cite:evidence-source:000001]]"
    state, merged, gate, _ = _bundle(content=sensitive_content)
    blocked = _disposition(1, value="blocked")
    with pytest.raises(RuntimeError) as failed:
        render_references(state, merged, gate, blocked)
    _assert_fixed(failed.value)
    targets = (state, merged, gate, blocked, sentinel, sensitive_content)
    traceback = failed.value.__traceback__
    while traceback is not None:
        if traceback.tb_frame.f_globals.get("__name__") == module.__name__:
            local_values = tuple(traceback.tb_frame.f_locals.values())
            for value in local_values:
                assert all(value is not target for target in targets)
                if type(value) is tuple:
                    index = 0
                    while index < tuple.__len__(value):
                        item = tuple.__getitem__(value, index)
                        assert all(item is not target for target in targets)
                        index += 1
                elif type(value) is list:
                    index = 0
                    while index < list.__len__(value):
                        item = list.__getitem__(value, index)
                        assert all(item is not target for target in targets)
                        index += 1
                elif type(value) is dict:
                    for key, item in dict.items(value):
                        assert all(key is not target for target in targets)
                        assert all(item is not target for target in targets)
        traceback = traceback.tb_next


def test_duplicate_url_rejection_is_inherited_without_title_deduplication() -> None:
    with pytest.raises(ValidationError):
        _approved_state(
            sources=(
                _source(1, title="Same", url="https://same.test"),
                _source(2, title="Same", url="https://same.test"),
            )
        )

    sources = (
        _source(1, title="Same", url="https://one.test"),
        _source(2, title="Same", url="https://two.test"),
    )
    citations = (("evidence-source:000002", "evidence-source:000001"),)
    content = (
        "[[cite:evidence-source:000002]] "
        "[[cite:evidence-source:000001]]"
    )
    result = render_references(
        _approved_state(sources=sources),
        _merged(1, content),
        _gate(citations),
        _disposition(1),
    )
    assert result.reference_source_ids == (
        "evidence-source:000002",
        "evidence-source:000001",
    )
    assert result.content.count('title="Same"') == 2


def test_fixed_profile_state_renders_with_unchanged_global_marker_rules() -> None:
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
    content = "Body [[cite:evidence-source:000001]]"
    result = render_references(
        state,
        _merged(8, content),
        _gate(tuple(("evidence-source:000001",) for _ in range(8))),
        _disposition(8),
    )
    assert result.content.startswith(content + "\n\n## References\n\n")
    assert result.reference_source_ids == ("evidence-source:000001",)
