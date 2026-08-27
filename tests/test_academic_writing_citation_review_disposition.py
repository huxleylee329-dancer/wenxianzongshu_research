"""Focused behavior tests for deterministic citation-review disposition."""

from __future__ import annotations

import inspect
import json
import types

import pytest
from pydantic import ValidationError

from gpt_researcher.workflows.academic_writing import (
    citation_review_disposition as module,
)
from gpt_researcher.workflows.academic_writing.citation_evidence_gate import (
    WorkflowCitationEvidenceGateResult,
)
from gpt_researcher.workflows.academic_writing.citation_review_disposition import (
    WorkflowCitationReviewDisposition,
    gate_citation_review_disposition,
)
from gpt_researcher.workflows.academic_writing.citation_reviewer import (
    WorkflowSectionCitationReview,
)


def _gate(
    section_count: int = 1,
    *,
    citations: tuple[tuple[str, ...], ...] | None = None,
) -> WorkflowCitationEvidenceGateResult:
    if citations is None:
        citations = tuple(
            ((f"evidence-source:{order:06d}",))
            for order in range(1, section_count + 1)
        )
    return WorkflowCitationEvidenceGateResult(
        outline_id="outline:000001",
        section_ids=tuple(
            f"section:{order:06d}" for order in range(1, section_count + 1)
        ),
        cited_source_ids_by_section=citations,
        attempt=1,
    )


def _default_issues(verdict: str) -> tuple[str, ...]:
    return () if verdict == "supported" else ("insufficient_evidence",)


def _review(
    order: int,
    *,
    verdict: str = "supported",
    issues: tuple[str, ...] | None = None,
    cited_source_ids: tuple[str, ...] | None = None,
    rationale: str | None = None,
) -> WorkflowSectionCitationReview:
    return WorkflowSectionCitationReview(
        outline_id="outline:000001",
        section_id=f"section:{order:06d}",
        cited_source_ids=(
            (f"evidence-source:{order:06d}",)
            if cited_source_ids is None
            else cited_source_ids
        ),
        verdict=verdict,
        issues=_default_issues(verdict) if issues is None else issues,
        rationale=(f"Bounded review opinion {order}." if rationale is None else rationale),
        attempt=1,
    )


def _reviews(
    verdicts: tuple[str, ...],
    *,
    issues: tuple[tuple[str, ...], ...] | None = None,
) -> tuple[WorkflowSectionCitationReview, ...]:
    return tuple(
        _review(
            index + 1,
            verdict=verdict,
            issues=None if issues is None else issues[index],
        )
        for index, verdict in enumerate(verdicts)
    )


def _canonical(value: object) -> bytes:
    if type(value) is WorkflowCitationReviewDisposition:
        value = value.model_dump(mode="json")
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _assert_fixed(error: BaseException) -> None:
    assert type(error) is module._CitationReviewDispositionError
    assert str(error) == "citation review disposition failed"
    assert error.__cause__ is None
    assert error.__context__ is None
    assert error.__suppress_context__ is False


def _assert_module_traceback_cannot_reach(
    error: BaseException,
    targets: tuple[object, ...],
) -> None:
    pending: list[object] = []
    traceback = error.__traceback__
    while traceback is not None:
        frame = traceback.tb_frame
        if frame.f_globals.get("__name__") == module.__name__:
            pending.extend(frame.f_locals.values())
        traceback = traceback.tb_next
    if error.__cause__ is not None:
        pending.append(error.__cause__)
    if error.__context__ is not None:
        pending.append(error.__context__)
    seen: set[int] = set()
    while pending:
        value = pending.pop()
        if any(value is target for target in targets):
            raise AssertionError("fixed error retained sensitive disposition input")
        identity = id(value)
        if identity in seen:
            continue
        seen.add(identity)
        if type(value) is dict:
            pending.extend(dict.keys(value))
            pending.extend(dict.values(value))
        elif type(value) in (tuple, list, set, frozenset):
            pending.extend(value)
        elif type(value) in (
            WorkflowCitationEvidenceGateResult,
            WorkflowSectionCitationReview,
            WorkflowCitationReviewDisposition,
        ):
            pending.extend(object.__getattribute__(value, "__dict__").values())
        elif type(value) is types.FunctionType and value.__closure__ is not None:
            pending.extend(cell.cell_contents for cell in value.__closure__)


def _corrupt(model: object, field: str, value: object) -> object:
    namespace = object.__getattribute__(model, "__dict__")
    dict.__setitem__(namespace, field, value)
    return model


def test_public_contract_success_json_frozen_nonmutation_and_repeat() -> None:
    assert module.__all__ == (
        "WorkflowCitationReviewDisposition",
        "gate_citation_review_disposition",
    )
    assert str(inspect.signature(gate_citation_review_disposition)) == (
        "(gate_result: 'WorkflowCitationEvidenceGateResult', reviews: "
        "'tuple[WorkflowSectionCitationReview, ...]') -> "
        "'WorkflowCitationReviewDisposition'"
    )
    gate = _gate()
    reviews = _reviews(("supported",))
    before = (
        gate.model_dump(mode="json"),
        tuple(review.model_dump(mode="json") for review in reviews),
    )
    first = gate_citation_review_disposition(gate, reviews)
    second = gate_citation_review_disposition(gate, reviews)
    assert type(first) is WorkflowCitationReviewDisposition
    assert first == second == WorkflowCitationReviewDisposition(
        outline_id="outline:000001",
        section_ids=("section:000001",),
        section_dispositions=("ready",),
        disposition="ready",
        attempt=1,
    )
    assert _canonical(first) == _canonical(second)
    restored = WorkflowCitationReviewDisposition.model_validate_json(_canonical(first))
    assert type(restored.section_ids) is tuple
    assert type(restored.section_dispositions) is tuple
    assert restored == first
    assert before == (
        gate.model_dump(mode="json"),
        tuple(review.model_dump(mode="json") for review in reviews),
    )
    with pytest.raises(ValidationError):
        first.attempt = 2  # type: ignore[misc]


@pytest.mark.parametrize(
    "case",
    (
        "mapping_subclass",
        "section_list",
        "disposition_list",
        "tuple_subclass",
        "string_subclass",
        "bool_attempt",
        "missing",
        "extra",
        "empty",
        "thirteen",
        "wrong_section_order",
        "length_mismatch",
        "unknown_disposition",
        "inconsistent_overall",
        "json_null",
    ),
)
def test_output_dto_strict_matrix(case: str) -> None:
    class _Mapping(dict[str, object]):
        pass

    class _Tuple(tuple[object, ...]):
        pass

    class _String(str):
        pass

    values: dict[str, object] = {
        "outline_id": "outline:000001",
        "section_ids": ("section:000001",),
        "section_dispositions": ("ready",),
        "disposition": "ready",
        "attempt": 1,
    }
    if case == "mapping_subclass":
        candidate: object = _Mapping(values)
    else:
        candidate = values
        if case == "section_list":
            values["section_ids"] = ["section:000001"]
        elif case == "disposition_list":
            values["section_dispositions"] = ["ready"]
        elif case == "tuple_subclass":
            values["section_ids"] = _Tuple(("section:000001",))
        elif case == "string_subclass":
            values["disposition"] = _String("ready")
        elif case == "bool_attempt":
            values["attempt"] = True
        elif case == "missing":
            del values["attempt"]
        elif case == "extra":
            values["extra"] = "x"
        elif case == "empty":
            values["section_ids"] = ()
            values["section_dispositions"] = ()
        elif case == "thirteen":
            values["section_ids"] = tuple(
                f"section:{order:06d}" for order in range(1, 14)
            )
            values["section_dispositions"] = ("ready",) * 13
        elif case == "wrong_section_order":
            values["section_ids"] = ("section:000002",)
        elif case == "length_mismatch":
            values["section_dispositions"] = ("ready", "ready")
        elif case == "unknown_disposition":
            values["section_dispositions"] = ("publish",)
        elif case == "inconsistent_overall":
            values["disposition"] = "blocked"
        elif case == "json_null":
            with pytest.raises((ValidationError, TypeError)):
                WorkflowCitationReviewDisposition.model_validate_json(
                    json.dumps({**values, "attempt": None})
                )
            return
    with pytest.raises((ValidationError, TypeError)):
        WorkflowCitationReviewDisposition.model_validate(candidate)


@pytest.mark.parametrize(
    ("verdicts", "issues", "expected_sections", "expected_overall"),
    (
        (("supported",), ((),), ("ready",), "ready"),
        (
            ("uncertain",),
            (("insufficient_evidence",),),
            ("needs_human_review",),
            "needs_human_review",
        ),
        (
            ("uncertain",),
            (("possible_contradiction",),),
            ("needs_human_review",),
            "needs_human_review",
        ),
        (
            ("uncertain",),
            (("citation_placement_unclear",),),
            ("needs_human_review",),
            "needs_human_review",
        ),
        (
            ("unsupported",),
            (("insufficient_evidence",),),
            ("blocked",),
            "blocked",
        ),
        (
            ("unsupported",),
            (("possible_contradiction", "citation_placement_unclear"),),
            ("blocked",),
            "blocked",
        ),
        (
            ("supported", "uncertain"),
            ((), ("citation_placement_unclear",)),
            ("ready", "needs_human_review"),
            "needs_human_review",
        ),
        (
            ("uncertain", "unsupported"),
            (("possible_contradiction",), ("insufficient_evidence",)),
            ("needs_human_review", "blocked"),
            "blocked",
        ),
        (
            ("unsupported", "supported", "uncertain"),
            (
                ("insufficient_evidence", "citation_placement_unclear"),
                (),
                (
                    "insufficient_evidence",
                    "possible_contradiction",
                    "citation_placement_unclear",
                ),
            ),
            ("blocked", "ready", "needs_human_review"),
            "blocked",
        ),
    ),
)
def test_verdict_routing_issue_independence_and_priority(
    verdicts: tuple[str, ...],
    issues: tuple[tuple[str, ...], ...],
    expected_sections: tuple[str, ...],
    expected_overall: str,
) -> None:
    result = gate_citation_review_disposition(
        _gate(len(verdicts)),
        _reviews(verdicts, issues=issues),
    )
    assert result.section_dispositions == expected_sections
    assert result.disposition == expected_overall


@pytest.mark.parametrize(
    "case",
    (
        "gate_subclass",
        "gate_attempt",
        "gate_duplicate_citation",
        "reviews_list",
        "missing",
        "extra",
        "repeated",
        "reordered",
        "wrong_outline",
        "wrong_section",
        "wrong_citations",
        "wrong_attempt",
        "bad_verdict",
        "bad_issues",
        "blank_rationale",
        "long_rationale",
    ),
)
def test_input_binding_and_fixed_failure_matrix(case: str) -> None:
    class _GateSubclass(WorkflowCitationEvidenceGateResult):
        pass

    gate: object = _gate(2)
    reviews: object = _reviews(("supported", "supported"))
    if case == "gate_subclass":
        gate = _GateSubclass.model_validate(_gate(2).model_dump())
    elif case == "gate_attempt":
        _corrupt(gate, "attempt", True)
    elif case == "gate_duplicate_citation":
        _corrupt(
            gate,
            "cited_source_ids_by_section",
            (
                ("evidence-source:000001", "evidence-source:000001"),
                ("evidence-source:000002",),
            ),
        )
    elif case == "reviews_list":
        reviews = list(reviews)
    elif case == "missing":
        reviews = tuple(reviews)[:1]
    elif case == "extra":
        reviews = tuple(reviews) + (_review(3),)
    elif case == "repeated":
        reviews = (tuple(reviews)[0], tuple(reviews)[0])
    elif case == "reordered":
        reviews = (tuple(reviews)[1], tuple(reviews)[0])
    else:
        target = tuple(reviews)[1]
        if case == "wrong_outline":
            _corrupt(target, "outline_id", "outline:999999")
        elif case == "wrong_section":
            _corrupt(target, "section_id", "section:000001")
        elif case == "wrong_citations":
            _corrupt(target, "cited_source_ids", ("evidence-source:000001",))
        elif case == "wrong_attempt":
            _corrupt(target, "attempt", 2)
        elif case == "bad_verdict":
            _corrupt(target, "verdict", "verified")
        elif case == "bad_issues":
            _corrupt(
                target,
                "issues",
                ("insufficient_evidence", "insufficient_evidence"),
            )
        elif case == "blank_rationale":
            _corrupt(target, "rationale", " ")
        elif case == "long_rationale":
            _corrupt(target, "rationale", "R" * 2049)
    with pytest.raises(module._CitationReviewDispositionError) as caught:
        gate_citation_review_disposition(gate, reviews)  # type: ignore[arg-type]
    _assert_fixed(caught.value)


def test_legal_fabricated_artifacts_route_without_origin_claim() -> None:
    gate = WorkflowCitationEvidenceGateResult(
        outline_id="outline:000001",
        section_ids=("section:000001",),
        cited_source_ids_by_section=(("evidence-source:000025",),),
        attempt=1,
    )
    review = WorkflowSectionCitationReview(
        outline_id="outline:000001",
        section_id="section:000001",
        cited_source_ids=("evidence-source:000025",),
        verdict="uncertain",
        issues=("insufficient_evidence",),
        rationale="Directly constructed bounded opinion.",
        attempt=1,
    )
    result = gate_citation_review_disposition(gate, (review,))
    assert result.section_dispositions == ("needs_human_review",)
    assert result.disposition == "needs_human_review"
    assert set(result.model_dump()) == {
        "outline_id",
        "section_ids",
        "section_dispositions",
        "disposition",
        "attempt",
    }


@pytest.mark.parametrize(
    "case",
    (
        "hostile_section_string",
        "hostile_issue_tuple",
        "instance_shadow",
        "private_state",
        "review_subclass",
        "reviews_tuple_subclass",
    ),
)
def test_hostile_inputs_execute_no_dynamic_code(case: str) -> None:
    calls: list[str] = []

    class _HostileString(str):
        def __eq__(self, other: object) -> bool:
            calls.append("eq")
            raise AssertionError("hostile equality executed")

        def __repr__(self) -> str:
            calls.append("repr")
            raise AssertionError("hostile repr executed")

        def strip(self, *args: object) -> str:
            calls.append("strip")
            raise AssertionError("hostile strip executed")

    class _HostileTuple(tuple[object, ...]):
        def __iter__(self):  # type: ignore[no-untyped-def]
            calls.append("iter")
            raise AssertionError("hostile iterator executed")

        def __getitem__(self, key: object) -> object:
            calls.append("getitem")
            raise AssertionError("hostile item access executed")

    class _ReviewSubclass(WorkflowSectionCitationReview):
        @property
        def dangerous(self) -> object:
            calls.append("descriptor")
            raise AssertionError("hostile descriptor executed")

    gate = _gate()
    review: object = _review(1)
    reviews: object = (review,)
    if case == "hostile_section_string":
        _corrupt(review, "section_id", _HostileString("section:000001"))
    elif case == "hostile_issue_tuple":
        _corrupt(review, "issues", _HostileTuple(()))
    elif case == "instance_shadow":
        namespace = object.__getattribute__(review, "__dict__")
        dict.__setitem__(namespace, "model_dump", lambda: calls.append("call"))
    elif case == "private_state":
        object.__setattr__(review, "__pydantic_private__", {"sentinel": calls})
    elif case == "review_subclass":
        review = _ReviewSubclass.model_validate(_review(1).model_dump())
        reviews = (review,)
    elif case == "reviews_tuple_subclass":
        reviews = _HostileTuple((review,))
        calls.clear()
    with pytest.raises(module._CitationReviewDispositionError) as caught:
        gate_citation_review_disposition(gate, reviews)  # type: ignore[arg-type]
    _assert_fixed(caught.value)
    assert calls == []


def test_fixed_failure_releases_final_rationale_inputs_and_partial_routes() -> None:
    gate = _gate(2)
    first = _review(1)
    rationale = "final-review-rationale-sentinel"
    final = _review(2, rationale=rationale)
    _corrupt(final, "section_id", "section:000001")
    reviews = (first, final)
    with pytest.raises(module._CitationReviewDispositionError) as caught:
        gate_citation_review_disposition(gate, reviews)
    _assert_fixed(caught.value)
    _assert_module_traceback_cannot_reach(
        caught.value,
        (gate, reviews, first, final, rationale),
    )


def test_reachable_575_and_direct_invalid_576() -> None:
    gate = _gate(12)
    reviews = _reviews(("uncertain",) * 12)
    result = gate_citation_review_disposition(gate, reviews)
    assert result.section_dispositions == ("needs_human_review",) * 12
    assert result.disposition == "needs_human_review"
    assert len(_canonical(result)) == 575
    invalid = result.model_dump(mode="python")
    invalid["disposition"] = "needs_human_reviewX"
    assert len(_canonical(invalid)) == 576
    with pytest.raises(ValidationError):
        WorkflowCitationReviewDisposition.model_validate(invalid)
