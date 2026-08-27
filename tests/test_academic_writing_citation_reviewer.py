"""Focused behavior tests for the bounded citation-reviewer adapter."""

from __future__ import annotations

import asyncio
import builtins
import dis
import inspect
import json
import sys
import types

import pytest
from pydantic import ValidationError

from gpt_researcher.workflows.academic_writing import citation_reviewer as module
from gpt_researcher.workflows.academic_writing import graph as graph_module
from gpt_researcher.workflows.academic_writing.citation_evidence_gate import (
    WorkflowCitationEvidenceGateResult,
    gate_citation_evidence,
)
from gpt_researcher.workflows.academic_writing.citation_reviewer import (
    GPTResearcherCitationReviewerAdapter,
    WorkflowSectionCitationReview,
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


def _provenance(
    order: int, block: str = "Bounded evidence."
) -> WorkflowEvidenceProvenance:
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


def _state(
    *,
    section_count: int = 1,
    source_count: int = 1,
    provenance: tuple[WorkflowEvidenceProvenance, ...] | None = None,
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
    topic = WorkflowTopicPlan(
        topic_plan_id="topic-plan:000001",
        workflow_id="workflow-1",
        run_id="run-1",
        attempt=1,
        research_topic="Deterministic research",
        research_questions=("What is deterministic?",),
    )
    evidence = WorkflowResearchEvidence(
        evidence_id="evidence:000001",
        topic_plan_id="topic-plan:000001",
        attempt=1,
        context_blocks=("Context.",),
        sources=tuple(_source(order) for order in range(1, source_count + 1)),
        provenance=(
            (_provenance(1),) if provenance is None else provenance
        ),
    )
    outline = WorkflowOutline(
        outline_id="outline:000001",
        evidence_id="evidence:000001",
        attempt=1,
        title="Approved outline",
        sections=tuple(_section(order) for order in range(1, section_count + 1)),
    )
    decision = "approve" if approved else "reject"
    record = WorkflowOutlineDecisionRecord(
        decision_id="outline-decision:000001",
        schema_version="1",
        workflow_id="workflow-1",
        thread_id="thread-1",
        run_id="run-1",
        outline_id="outline:000001",
        outline_digest=graph_module._outline_digest(outline),
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
        topic_plan=topic,
        research_evidence=evidence,
        outline=outline,
        outline_decision=record,
        errors=(),
        events=_APPROVE_EVENTS if approved else _REJECT_EVENTS,
    )


def _drafts(
    count: int,
    content: str = "Body [[cite:evidence-source:000001]]",
) -> tuple[WorkflowSectionDraft, ...]:
    return tuple(
        WorkflowSectionDraft(
            outline_id="outline:000001",
            section_id=f"section:{order:06d}",
            attempt=1,
            content=content,
        )
        for order in range(1, count + 1)
    )


def _response(
    payload: dict[str, object],
    *,
    verdict: str = "supported",
    issues: list[str] | None = None,
    rationale: str = "The bounded evidence supports the cited use.",
) -> str:
    return json.dumps(
        {
            "section_id": payload["section_id"],
            "cited_source_ids": payload["cited_source_ids"],
            "verdict": verdict,
            "issues": [] if issues is None else issues,
            "rationale": rationale,
        },
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )


class _Client:
    def __init__(self, owner: "_Factory", index: int) -> None:
        self.owner = owner
        self.index = index

    async def complete(self, *, system_message: str, user_message: str) -> object:
        self.owner.calls.append((self.index, system_message, user_message))
        payload = json.loads(user_message)
        behavior = self.owner.behaviors[self.index - 1]
        if isinstance(behavior, BaseException):
            raise behavior
        if callable(behavior):
            result = behavior(payload)
            if inspect.isawaitable(result):
                return await result
            return result
        return behavior if behavior is not None else _response(payload)


class _Factory:
    def __init__(self, *behaviors: object) -> None:
        self.behaviors = list(behaviors) if behaviors else [None] * 12
        self.count = 0
        self.calls: list[tuple[int, str, str]] = []

    def __call__(self) -> _Client:
        self.count += 1
        behavior = self.behaviors[self.count - 1]
        if isinstance(behavior, BaseException):
            raise behavior
        return _Client(self, self.count)


def _assert_fixed(error: BaseException) -> None:
    assert type(error) is module._CitationReviewerError
    assert str(error) == "citation reviewer failed"
    assert error.__cause__ is None
    assert error.__context__ is None
    assert error.__suppress_context__ is False


def _canonical(value: object) -> bytes:
    if isinstance(value, WorkflowSectionCitationReview):
        value = value.model_dump(mode="json")
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _suspended_module_frames(task: asyncio.Task[object]) -> list[types.FrameType]:
    frames: list[types.FrameType] = []
    awaitable: object | None = task.get_coro()
    while inspect.iscoroutine(awaitable):
        frame = awaitable.cr_frame
        if frame is not None and frame.f_globals.get("__name__") == module.__name__:
            frames.append(frame)
        awaitable = awaitable.cr_await
    return frames


def _assert_module_traceback_cannot_reach(
    error: BaseException,
    targets: tuple[object, ...],
) -> None:
    traceback = error.__traceback__
    pending: list[object] = []
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
            raise AssertionError("production traceback retained a completed review")
        identity = id(value)
        if identity in seen:
            continue
        seen.add(identity)
        if type(value) is dict:
            pending.extend(dict.keys(value))
            pending.extend(dict.values(value))
        elif type(value) in (tuple, list, set, frozenset):
            pending.extend(value)
        elif type(value) is WorkflowSectionCitationReview:
            pending.extend(object.__getattribute__(value, "__dict__").values())
        elif type(value) is types.FunctionType and value.__closure__ is not None:
            pending.extend(cell.cell_contents for cell in value.__closure__)


@pytest.mark.asyncio
async def test_public_contract_success_and_input_nonmutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert module.__all__ == (
        "WorkflowSectionCitationReview",
        "CitationReviewerClientFactory",
        "GPTResearcherCitationReviewerAdapter",
    )
    assert str(inspect.signature(GPTResearcherCitationReviewerAdapter)) == (
        "(*, citation_reviewer_client_factory: "
        "'CitationReviewerClientFactory | None' = None) -> 'None'"
    )
    assert str(inspect.signature(GPTResearcherCitationReviewerAdapter.review_citations)) == (
        "(self, state: 'AcademicWorkflowState', drafts: "
        "'tuple[WorkflowSectionDraft, ...]', gate_result: "
        "'WorkflowCitationEvidenceGateResult') -> "
        "'tuple[WorkflowSectionCitationReview, ...]'"
    )
    state = _state()
    drafts = _drafts(1)
    gate = gate_citation_evidence(state, drafts)
    before = (
        state.model_dump(mode="json"),
        tuple(item.model_dump(mode="json") for item in drafts),
        gate.model_dump(mode="json"),
    )
    factory = _Factory(None)
    production_calls = 0

    def forbidden_production() -> object:
        nonlocal production_calls
        production_calls += 1
        raise AssertionError("injected path touched production")

    gate_calls = 0
    original_gate = module._gate_citation_evidence

    def counted_gate(*args: object) -> WorkflowCitationEvidenceGateResult:
        nonlocal gate_calls
        gate_calls += 1
        return original_gate(*args)  # type: ignore[arg-type]

    monkeypatch.setattr(
        module,
        "_create_production_citation_reviewer_client",
        forbidden_production,
    )
    monkeypatch.setattr(module, "_gate_citation_evidence", counted_gate)
    adapter = GPTResearcherCitationReviewerAdapter(
        citation_reviewer_client_factory=factory
    )
    namespace_before = {
        key: id(value)
        for key, value in vars(module).items()
        if type(value) in (dict, list, set)
    }
    result = await adapter.review_citations(state, drafts, gate)
    assert type(result) is tuple
    assert tuple(type(item) for item in result) == (WorkflowSectionCitationReview,)
    assert result[0].rationale == "The bounded evidence supports the cited use."
    assert result[0].cited_source_ids == ("evidence-source:000001",)
    restored = WorkflowSectionCitationReview.model_validate_json(_canonical(result[0]))
    assert type(restored.cited_source_ids) is tuple
    assert type(restored.issues) is tuple
    assert restored == result[0]
    with pytest.raises(ValidationError):
        result[0].attempt = 2  # type: ignore[misc]
    assert factory.count == len(factory.calls) == 1
    assert gate_calls == 1
    assert production_calls == 0
    assert object.__getattribute__(adapter, "_citation_reviewer_client_factory") is factory
    assert namespace_before == {
        key: id(value)
        for key, value in vars(module).items()
        if type(value) in (dict, list, set)
    }
    assert before == (
        state.model_dump(mode="json"),
        tuple(item.model_dump(mode="json") for item in drafts),
        gate.model_dump(mode="json"),
    )


@pytest.mark.parametrize(
    "case",
    (
        "mapping_subclass",
        "python_list",
        "string_subclass",
        "bool_attempt",
        "bad_section",
        "empty_ids",
        "duplicate_ids",
        "sixty_five_ids",
        "bad_source_id",
        "supported_issue",
        "unsupported_no_issue",
        "uncertain_no_issue",
        "duplicate_issue",
        "reordered_issue",
        "blank_rationale",
        "long_rationale",
        "missing",
        "extra",
        "json_bool_attempt",
    ),
)
def test_review_dto_strict_matrix(case: str) -> None:
    class _Mapping(dict[str, object]):
        pass

    class _String(str):
        pass

    values: dict[str, object] = {
        "outline_id": "outline:000001",
        "section_id": "section:000001",
        "cited_source_ids": ("evidence-source:000001",),
        "verdict": "supported",
        "issues": (),
        "rationale": "R",
        "attempt": 1,
    }
    candidate: object = values
    if case == "mapping_subclass":
        candidate = _Mapping(values)
    elif case == "python_list":
        values["cited_source_ids"] = ["evidence-source:000001"]
    elif case == "string_subclass":
        values["rationale"] = _String("R")
    elif case == "bool_attempt":
        values["attempt"] = True
    elif case == "bad_section":
        values["section_id"] = "section:000013"
    elif case == "empty_ids":
        values["cited_source_ids"] = ()
    elif case == "duplicate_ids":
        values["cited_source_ids"] = (
            "evidence-source:000001",
            "evidence-source:000001",
        )
    elif case == "sixty_five_ids":
        values["cited_source_ids"] = tuple(
            f"evidence-source:{order:06d}" for order in range(1, 66)
        )
    elif case == "bad_source_id":
        values["cited_source_ids"] = ("evidence-source:000201",)
    elif case == "supported_issue":
        values["issues"] = ("insufficient_evidence",)
    elif case == "unsupported_no_issue":
        values["verdict"] = "unsupported"
    elif case == "uncertain_no_issue":
        values["verdict"] = "uncertain"
    elif case == "duplicate_issue":
        values["verdict"] = "uncertain"
        values["issues"] = ("insufficient_evidence", "insufficient_evidence")
    elif case == "reordered_issue":
        values["verdict"] = "unsupported"
        values["issues"] = ("possible_contradiction", "insufficient_evidence")
    elif case == "blank_rationale":
        values["rationale"] = " "
    elif case == "long_rationale":
        values["rationale"] = "R" * 2049
    elif case == "missing":
        del values["rationale"]
    elif case == "extra":
        values["extra"] = "x"
    with pytest.raises((TypeError, ValidationError)):
        if case == "json_bool_attempt":
            WorkflowSectionCitationReview.model_validate_json(
                json.dumps(dict(values, attempt=True), separators=(",", ":"))
            )
        else:
            WorkflowSectionCitationReview.model_validate(candidate)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    ("rejected", "draft_list", "gate_mismatch", "hostile_gate", "source_25"),
)
async def test_preflight_gate_and_source_limitation_matrix(case: str) -> None:
    if case == "rejected":
        state = _state(approved=False)
        drafts = _drafts(1)
        gate = WorkflowCitationEvidenceGateResult(
            outline_id="outline:000001",
            section_ids=("section:000001",),
            cited_source_ids_by_section=(("evidence-source:000001",),),
            attempt=1,
        )
    elif case == "source_25":
        state = _state(
            source_count=25,
            provenance=(_provenance(25),),
        )
        drafts = _drafts(1, "Body [[cite:evidence-source:000025]]")
        gate = gate_citation_evidence(state, drafts)
    else:
        state = _state()
        drafts = _drafts(1)
        gate = gate_citation_evidence(state, drafts)
        if case == "draft_list":
            drafts = list(drafts)  # type: ignore[assignment]
        elif case == "gate_mismatch":
            gate = WorkflowCitationEvidenceGateResult(
                outline_id="outline:000001",
                section_ids=("section:000001",),
                cited_source_ids_by_section=(("evidence-source:000002",),),
                attempt=1,
            )
        elif case == "hostile_gate":
            class _HostileString(str):
                calls = 0

                def __eq__(self, other: object) -> bool:
                    del other
                    type(self).calls += 1
                    raise AssertionError("hostile equality executed")

            object.__getattribute__(gate, "__dict__")["outline_id"] = (
                _HostileString("outline:000001")
            )
    factory = _Factory(None)
    adapter = GPTResearcherCitationReviewerAdapter(
        citation_reviewer_client_factory=factory
    )
    if case == "source_25":
        result = await adapter.review_citations(state, drafts, gate)  # type: ignore[arg-type]
        assert result[0].cited_source_ids == ("evidence-source:000025",)
        assert factory.count == 1
    else:
        with pytest.raises(module._CitationReviewerError) as caught:
            await adapter.review_citations(state, drafts, gate)  # type: ignore[arg-type]
        _assert_fixed(caught.value)
        assert factory.count == 0
        if case == "hostile_gate":
            assert _HostileString.calls == 0


@pytest.mark.asyncio
async def test_prompt_is_canonical_partitioned_and_current_section_only() -> None:
    state = _state(
        section_count=2,
        source_count=2,
        provenance=(_provenance(1, "é-one"), _provenance(2, "two")),
    )
    drafts = (
        _drafts(1, "S1 [[cite:evidence-source:000002]] [[cite:evidence-source:000001]]")[0],
        WorkflowSectionDraft(
            outline_id="outline:000001",
            section_id="section:000002",
            attempt=1,
            content="S2 [[cite:evidence-source:000001]]",
        ),
    )
    gate = gate_citation_evidence(state, drafts)
    factory = _Factory(None, None)
    result = await GPTResearcherCitationReviewerAdapter(
        citation_reviewer_client_factory=factory
    ).review_citations(state, drafts, gate)
    assert len(result) == 2
    first = factory.calls[0][2]
    assert first == json.dumps(
        {
            "cited_source_ids": [
                "evidence-source:000002",
                "evidence-source:000001",
            ],
            "evidence_by_source": [
                {"source_id": "evidence-source:000002", "evidence_blocks": ["two"]},
                {"source_id": "evidence-source:000001", "evidence_blocks": ["é-one"]},
            ],
            "section_content": drafts[0].content,
            "section_id": "section:000001",
        },
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    assert "S2" not in first

    large_provenance = tuple(
        WorkflowEvidenceProvenance(
            source_id=f"evidence-source:{order:06d}",
            evidence_blocks=("甲" * 8192, "乙" * 8192),
        )
        for order in range(1, 5)
    )
    large_state = _state(
        source_count=4,
        provenance=large_provenance,
    )
    large_content = " ".join(
        f"[[cite:evidence-source:{order:06d}]]" for order in range(1, 5)
    )
    large_drafts = _drafts(1, large_content)
    large_gate = gate_citation_evidence(large_state, large_drafts)
    full_payload = {
        "cited_source_ids": list(large_gate.cited_source_ids_by_section[0]),
        "evidence_by_source": [
            {
                "source_id": entry.source_id,
                "evidence_blocks": list(entry.evidence_blocks),
            }
            for entry in large_provenance
        ],
        "section_content": large_content,
        "section_id": "section:000001",
    }
    assert len(
        json.dumps(
            full_payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ) > 65536

    large_factory = _Factory(None)
    large_result = await GPTResearcherCitationReviewerAdapter(
        citation_reviewer_client_factory=large_factory
    ).review_citations(large_state, large_drafts, large_gate)
    assert len(large_result) == 1
    assert large_factory.count == len(large_factory.calls) == 1
    projected_message = large_factory.calls[0][2]
    assert len(projected_message.encode("utf-8")) <= 65536
    projected_payload = json.loads(projected_message)
    cited_ids = [entry.source_id for entry in large_provenance]
    assert projected_payload["cited_source_ids"] == cited_ids
    assert [
        record["source_id"] for record in projected_payload["evidence_by_source"]
    ] == cited_ids
    for original, projected in zip(
        large_provenance,
        projected_payload["evidence_by_source"],
        strict=True,
    ):
        projected_blocks = projected["evidence_blocks"]
        assert 1 <= len(projected_blocks) <= len(original.evidence_blocks)
        for index, projected_block in enumerate(projected_blocks):
            assert projected_block.strip()
            assert original.evidence_blocks[index].startswith(projected_block)
            if index < len(projected_blocks) - 1:
                assert projected_block == original.evidence_blocks[index]


@pytest.mark.asyncio
@pytest.mark.parametrize("length", (65536, 65537))
async def test_prompt_adjacent_boundary(
    length: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = "[[cite:evidence-source:000001]]"
    content = marker + ("C" * (24576 - len(marker)))
    block = ("\0" * 4878) + ("\n" * (3 if length == 65536 else 4))
    block += "E" * (16384 - len(block))
    state = _state(provenance=(_provenance(1, block),))
    drafts = _drafts(1, content)
    gate = gate_citation_evidence(state, drafts)
    factory = _Factory(None)
    adapter = GPTResearcherCitationReviewerAdapter(
        citation_reviewer_client_factory=factory
    )
    result = await adapter.review_citations(state, drafts, gate)
    assert len(result) == 1
    assert factory.count == len(factory.calls) == 1
    user_message = factory.calls[0][2]
    assert len(user_message.encode("utf-8")) <= 65536
    projected = json.loads(user_message)
    assert projected["cited_source_ids"] == ["evidence-source:000001"]
    assert [
        record["source_id"] for record in projected["evidence_by_source"]
    ] == ["evidence-source:000001"]
    projected_block = projected["evidence_by_source"][0]["evidence_blocks"][0]
    assert projected_block.strip()
    assert block.startswith(projected_block)
    if length == 65536:
        assert len(user_message.encode("utf-8")) == 65536
        assert projected_block == block
    else:
        assert projected_block != block
        monkeypatch.setattr(module, "_USER_MESSAGE_MAX_BYTES", 1)
        impossible_factory = _Factory(None)
        with pytest.raises(module._CitationReviewerError) as caught:
            await GPTResearcherCitationReviewerAdapter(
                citation_reviewer_client_factory=impossible_factory
            ).review_citations(state, drafts, gate)
        _assert_fixed(caught.value)
        assert impossible_factory.count == 0
        assert impossible_factory.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    (
        "none",
        "string_subclass",
        "blank",
        "too_long",
        "malformed_json",
        "missing_key",
        "extra_key",
        "wrong_section",
        "wrong_ids",
        "duplicate_ids",
        "unknown_verdict",
        "duplicate_issue",
        "reordered_issue",
        "incoherent_supported",
        "blank_rationale",
        "long_rationale",
    ),
)
async def test_response_failure_matrix(case: str) -> None:
    class _String(str):
        pass

    def behavior(payload: dict[str, object]) -> object:
        values: dict[str, object] = {
            "section_id": payload["section_id"],
            "cited_source_ids": payload["cited_source_ids"],
            "verdict": "supported",
            "issues": [],
            "rationale": "R",
        }
        if case == "none":
            return None
        if case == "string_subclass":
            return _String(json.dumps(values))
        if case == "blank":
            return "  "
        if case == "too_long":
            return " " * 24577
        if case == "malformed_json":
            return "{"
        if case == "missing_key":
            del values["rationale"]
        elif case == "extra_key":
            values["extra"] = "x"
        elif case == "wrong_section":
            values["section_id"] = "section:000002"
        elif case == "wrong_ids":
            values["cited_source_ids"] = ["evidence-source:000002"]
        elif case == "duplicate_ids":
            values["cited_source_ids"] = [
                "evidence-source:000001",
                "evidence-source:000001",
            ]
        elif case == "unknown_verdict":
            values["verdict"] = "maybe"
        elif case == "duplicate_issue":
            values["verdict"] = "uncertain"
            values["issues"] = ["insufficient_evidence", "insufficient_evidence"]
        elif case == "reordered_issue":
            values["verdict"] = "unsupported"
            values["issues"] = ["possible_contradiction", "insufficient_evidence"]
        elif case == "incoherent_supported":
            values["issues"] = ["citation_placement_unclear"]
        elif case == "blank_rationale":
            values["rationale"] = " "
        elif case == "long_rationale":
            values["rationale"] = "R" * 2049
        return json.dumps(values, separators=(",", ":"))

    state = _state()
    drafts = _drafts(1)
    gate = gate_citation_evidence(state, drafts)
    factory = _Factory(behavior)
    with pytest.raises(module._CitationReviewerError) as caught:
        await GPTResearcherCitationReviewerAdapter(
            citation_reviewer_client_factory=factory
        ).review_citations(state, drafts, gate)
    _assert_fixed(caught.value)
    assert factory.count == len(factory.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode",
    ("success", "retry", "alias_failure", "alias_cancel"),
)
async def test_sequential_cost_and_partial_result_matrix(mode: str) -> None:
    state = _state(section_count=3)
    drafts = _drafts(3)
    gate = gate_citation_evidence(state, drafts)
    if mode in {"alias_failure", "alias_cancel"}:
        sentinel_text = "completed-review-rationale-sentinel"
        ready = asyncio.Event()
        release = asyncio.Event()

        def first(payload: dict[str, object]) -> str:
            return _response(payload, rationale=sentinel_text)

        async def second(payload: dict[str, object]) -> object:
            del payload
            ready.set()
            await release.wait()
            return None

        factory = _Factory(first, second, None)
        adapter = GPTResearcherCitationReviewerAdapter(
            citation_reviewer_client_factory=factory
        )
        task = asyncio.create_task(adapter.review_citations(state, drafts, gate))
        await ready.wait()
        frames = _suspended_module_frames(task)  # type: ignore[arg-type]
        finisher = next(
            frame for frame in frames if frame.f_code.co_name == "_execute_review_plan"
        )
        assert type(finisher.f_locals["index"]) is int
        assert all(
            not type(value).__name__.endswith("_iterator")
            for value in finisher.f_locals.values()
        )
        loop_opnames = {
            instruction.opname for instruction in dis.get_instructions(finisher.f_code)
        }
        assert "GET_ITER" not in loop_opnames
        assert "FOR_ITER" not in loop_opnames
        accumulated = finisher.f_locals["accumulated"]
        assert type(accumulated) is list and len(accumulated) == 1
        completed = accumulated[0]
        assert type(completed) is WorkflowSectionCitationReview
        sentinel = completed.rationale
        assert sentinel == sentinel_text
        if mode == "alias_cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError) as caught:
                await task
        else:
            release.set()
            with pytest.raises(module._CitationReviewerError) as caught:
                await task
            _assert_fixed(caught.value)
        _assert_module_traceback_cannot_reach(
            caught.value,
            (completed, sentinel),
        )
        assert factory.count == len(factory.calls) == 2
        return

    behaviors: tuple[object, ...] = (None, None, None)
    if mode == "retry":
        behaviors = (None, RuntimeError("secret"), None)
    factory = _Factory(*behaviors)
    adapter = GPTResearcherCitationReviewerAdapter(
        citation_reviewer_client_factory=factory
    )
    if mode == "success":
        result = await adapter.review_citations(state, drafts, gate)
        assert tuple(item.section_id for item in result) == (
            "section:000001",
            "section:000002",
            "section:000003",
        )
        assert factory.count == len(factory.calls) == 3
    else:
        with pytest.raises(module._CitationReviewerError) as caught:
            await adapter.review_citations(state, drafts, gate)
        _assert_fixed(caught.value)
        assert factory.count == 2
        assert len(factory.calls) == 1
        if mode == "retry":
            retry = _Factory(None, None, None)
            result = await GPTResearcherCitationReviewerAdapter(
                citation_reviewer_client_factory=retry
            ).review_citations(state, drafts, gate)
            assert len(result) == 3
            assert retry.count == 3


@pytest.mark.asyncio
async def test_fully_mocked_production_wrapper(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    class _Config:
        strategic_llm_model = "strategic-model"
        strategic_llm_provider = "strategic-provider"
        strategic_token_limit = 9000
        temperature = 0.25
        reasoning_effort = "high"
        llm_kwargs = {"seed": 7}

    async def completion(**kwargs: object) -> str:
        calls.append(kwargs)
        payload = json.loads(kwargs["messages"][1]["content"])  # type: ignore[index]
        return _response(payload)

    config_module = types.ModuleType("gpt_researcher.config")
    config_module.Config = _Config  # type: ignore[attr-defined]
    llm_module = types.ModuleType("gpt_researcher.utils.llm")
    llm_module.create_chat_completion = completion  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "gpt_researcher.config", config_module)
    monkeypatch.setitem(sys.modules, "gpt_researcher.utils.llm", llm_module)
    state = _state()
    drafts = _drafts(1)
    result = await GPTResearcherCitationReviewerAdapter().review_citations(
        state,
        drafts,
        gate_citation_evidence(state, drafts),
    )
    assert len(result) == len(calls) == 1
    assert calls[0]["model"] == "strategic-model"
    assert calls[0]["llm_provider"] == "strategic-provider"
    assert calls[0]["max_tokens"] == 3072
    assert calls[0]["safe_mode"] is True
    assert calls[0]["stream"] is False
    assert calls[0]["websocket"] is None
    assert calls[0]["cost_callback"] is None
    assert calls[0]["llm_kwargs"] == {"seed": 7}

    async def empty_completion(**kwargs: object) -> object:
        del kwargs
        return None

    llm_module.create_chat_completion = empty_completion  # type: ignore[attr-defined]
    with pytest.raises(module._CitationReviewerError) as caught:
        await GPTResearcherCitationReviewerAdapter().review_citations(
            state,
            drafts,
            gate_citation_evidence(state, drafts),
        )
    _assert_fixed(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stage",
    (
        "gate",
        "production_import",
        "production_config",
        "factory",
        "completion_first",
        "completion_middle",
        "completion_last",
    ),
)
async def test_cancellation_preserves_identity_and_stops(
    stage: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    cancellation = asyncio.CancelledError("cancel-sentinel", stage)
    cancellation.secret = "must-be-cleared"  # type: ignore[attr-defined]
    state = _state(section_count=3)
    drafts = _drafts(3)
    gate = gate_citation_evidence(state, drafts)
    factory = _Factory(None, None, None)
    adapter: GPTResearcherCitationReviewerAdapter
    if stage == "gate":
        def cancelled_gate(*args: object) -> object:
            raise cancellation

        monkeypatch.setattr(module, "_gate_citation_evidence", cancelled_gate)
        adapter = GPTResearcherCitationReviewerAdapter(
            citation_reviewer_client_factory=factory
        )
    elif stage == "production_import":
        original_import = builtins.__import__

        def cancelled_import(
            name: str,
            globals: dict[str, object] | None = None,
            locals: dict[str, object] | None = None,
            fromlist: tuple[str, ...] = (),
            level: int = 0,
        ) -> object:
            if name == "gpt_researcher.config":
                raise cancellation
            return original_import(name, globals, locals, fromlist, level)

        monkeypatch.setattr(builtins, "__import__", cancelled_import)
        adapter = GPTResearcherCitationReviewerAdapter()
    elif stage == "production_config":
        def cancelled_config() -> object:
            raise cancellation

        config_module = types.ModuleType("gpt_researcher.config")
        config_module.Config = cancelled_config  # type: ignore[attr-defined]
        llm_module = types.ModuleType("gpt_researcher.utils.llm")
        llm_module.create_chat_completion = object()  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "gpt_researcher.config", config_module)
        monkeypatch.setitem(sys.modules, "gpt_researcher.utils.llm", llm_module)
        adapter = GPTResearcherCitationReviewerAdapter()
    elif stage == "factory":
        factory = _Factory(cancellation, None, None)
        adapter = GPTResearcherCitationReviewerAdapter(
            citation_reviewer_client_factory=factory
        )
    else:
        target = {
            "completion_first": 1,
            "completion_middle": 2,
            "completion_last": 3,
        }[stage]

        async def cancelled_completion(payload: dict[str, object]) -> object:
            del payload
            raise cancellation

        behaviors = [None] * target
        behaviors[target - 1] = cancelled_completion
        factory = _Factory(*behaviors)
        adapter = GPTResearcherCitationReviewerAdapter(
            citation_reviewer_client_factory=factory
        )
    try:
        await adapter.review_citations(state, drafts, gate)
    except asyncio.CancelledError as caught:
        assert caught is cancellation
        assert caught.args == ("cancel-sentinel", stage)
    else:
        raise AssertionError("cancellation did not propagate")
    assert not hasattr(cancellation, "secret")
    if stage in {"gate", "production_import", "production_config"}:
        assert factory.count == 0
    elif stage == "factory":
        assert factory.count == 1
    else:
        expected = {
            "completion_first": 1,
            "completion_middle": 2,
            "completion_last": 3,
        }[stage]
        assert factory.count == len(factory.calls) == expected
    if stage.startswith("completion"):
        assert len(factory.calls) == factory.count
    elif stage == "factory":
        assert factory.calls == []


@pytest.mark.asyncio
async def test_reachable_output_maxima_and_direct_max_plus_one() -> None:
    provenance = tuple(_provenance(order, "E") for order in range(1, 65))
    state = _state(section_count=12, source_count=64, provenance=provenance)
    content = " ".join(
        f"[[cite:evidence-source:{order:06d}]]" for order in range(1, 65)
    )
    drafts = _drafts(12, content)
    gate = gate_citation_evidence(state, drafts)

    def maximum(payload: dict[str, object]) -> str:
        return _response(
            payload,
            verdict="unsupported",
            issues=[
                "insufficient_evidence",
                "possible_contradiction",
                "citation_placement_unclear",
            ],
            rationale="\0" * 2048,
        )

    factory = _Factory(*(maximum for _ in range(12)))
    result = await GPTResearcherCitationReviewerAdapter(
        citation_reviewer_client_factory=factory
    ).review_citations(state, drafts, gate)
    assert all(len(_canonical(item)) == 14110 for item in result)
    assert len(_canonical([item.model_dump(mode="json") for item in result])) == 169333
    invalid = result[0].model_dump(mode="python")
    invalid["rationale"] = ("\0" * 2048) + "A"
    assert len(_canonical(invalid)) == 14111
    with pytest.raises(ValidationError):
        WorkflowSectionCitationReview.model_validate(invalid)
