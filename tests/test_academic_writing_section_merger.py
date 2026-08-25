"""Focused behavior tests for the off-graph deterministic section merger."""

from __future__ import annotations

import hashlib
import inspect
import json
from typing import get_type_hints

import pytest
from pydantic import ValidationError

from gpt_researcher.workflows.academic_writing import section_merger as module
from gpt_researcher.workflows.academic_writing.section_merger import (
    WorkflowMergedDraft,
    merge_sections,
)
from gpt_researcher.workflows.academic_writing.section_writer import (
    GPTResearcherSectionWriterAdapter,
)
from gpt_researcher.workflows.academic_writing.state import (
    AcademicWorkflowRequest,
    AcademicWorkflowState,
    WorkflowEvent,
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
_REJECT_EVENTS = _APPROVE_EVENTS[:-3] + (
    _event(7, "node_started", "outline_approval"),
    _event(8, "node_completed", "outline_approval"),
    _event(9, "workflow_rejected", None),
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


def _section(
    order: int,
    *,
    title: str | None = None,
    brief: str | None = None,
) -> WorkflowOutlineSection:
    return WorkflowOutlineSection(
        section_id=f"section:{order:06d}",
        order=order,
        title=f"Title {order}" if title is None else title,
        brief=f"Brief {order}" if brief is None else brief,
    )


def _approved_state(
    *,
    query: str = "r",
    language: str = "l",
    questions: tuple[str, ...] = ("q",),
    research_topic: str | None = None,
    context_blocks: tuple[str, ...] = ("c",),
    sources: tuple[WorkflowEvidenceSource, ...] = (),
    sections: tuple[WorkflowOutlineSection, ...] | None = None,
    outline_title: str = "o",
    report_type: str = "research_report",
    report_source: str = "web",
    approved: bool = True,
) -> AcademicWorkflowState:
    request = AcademicWorkflowRequest(
        workflow_mode="academic_langgraph",
        workflow_id="workflow-1",
        thread_id="thread-1",
        run_id="run-1",
        query=query,
        report_type=report_type,
        report_source=report_source,
        tone="objective",
        language=language,
        source_urls=(),
        document_urls=(),
        query_domains=(),
        max_search_results=1,
    )
    plan = WorkflowTopicPlan(
        topic_plan_id="topic-plan:000001",
        workflow_id="workflow-1",
        run_id="run-1",
        attempt=1,
        research_topic=query if research_topic is None else research_topic,
        research_questions=questions,
    )
    evidence = WorkflowResearchEvidence(
        evidence_id="evidence:000001",
        topic_plan_id="topic-plan:000001",
        attempt=1,
        context_blocks=context_blocks,
        sources=sources,
    )
    outline = WorkflowOutline(
        outline_id="outline:000001",
        evidence_id="evidence:000001",
        attempt=1,
        title=outline_title,
        sections=(_section(1, title="Title", brief="b"),)
        if sections is None
        else sections,
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
        decision="approve" if approved else "reject",
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
        outline_decision=decision,
        errors=(),
        events=_APPROVE_EVENTS if approved else _REJECT_EVENTS,
    )


def test_merge_preserves_the_frozen_markdown_template() -> None:
    result = merge_sections(
        _approved_state(),
        (
            WorkflowSectionDraft(
                outline_id="outline:000001",
                section_id="section:000001",
                attempt=1,
                content="Body",
            ),
        ),
    )

    assert type(result) is WorkflowMergedDraft
    assert result.model_dump() == {
        "outline_id": "outline:000001",
        "section_ids": ("section:000001",),
        "attempt": 1,
        "content": "## Title\n\nBody",
    }
    assert module.__all__ == ("WorkflowMergedDraft", "merge_sections")
    assert not inspect.iscoroutinefunction(merge_sections)
    signature = inspect.signature(merge_sections)
    assert tuple(signature.parameters) == ("state", "drafts")
    assert all(
        parameter.default is inspect.Parameter.empty
        for parameter in signature.parameters.values()
    )
    hints = get_type_hints(merge_sections)
    assert hints == {
        "state": AcademicWorkflowState,
        "drafts": tuple[WorkflowSectionDraft, ...],
        "return": WorkflowMergedDraft,
    }
    with pytest.raises(ValidationError):
        result.content = "changed"  # type: ignore[misc]


class _TupleSubclass(tuple):
    pass


class _StringSubclass(str):
    pass


@pytest.mark.parametrize(
    "case",
    [
        "python",
        "json",
        "max_content",
        "list_ids",
        "tuple_subclass",
        "id_subclass",
        "outline_subclass",
        "content_subclass",
        "bool_attempt",
        "wrong_attempt",
        "empty_ids",
        "thirteen_ids",
        "duplicate_ids",
        "blank_id",
        "blank_content",
        "overlong_content",
        "extra",
    ],
)
def test_merged_dto_strict_json_and_bounds(case: str) -> None:
    data: dict[str, object] = {
        "outline_id": "outline:000001",
        "section_ids": ("section:000001",),
        "attempt": 1,
        "content": "Body",
    }
    if case == "python":
        value = WorkflowMergedDraft(**data)
    elif case == "json":
        encoded = json.dumps(
            {**data, "section_ids": ["section:000001"]},
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        value = WorkflowMergedDraft.model_validate_json(encoded)
        assert value.section_ids == ("section:000001",)
        assert json.dumps(
            value.model_dump(mode="json"),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8") == encoded
    elif case == "max_content":
        value = WorkflowMergedDraft(**{**data, "content": "C" * 359538})
        assert len(value.content) == 359538
    else:
        if case == "list_ids":
            data["section_ids"] = ["section:000001"]
        elif case == "tuple_subclass":
            data["section_ids"] = _TupleSubclass(("section:000001",))
        elif case == "id_subclass":
            data["section_ids"] = (_StringSubclass("section:000001"),)
        elif case == "outline_subclass":
            data["outline_id"] = _StringSubclass("outline:000001")
        elif case == "content_subclass":
            data["content"] = _StringSubclass("Body")
        elif case == "bool_attempt":
            data["attempt"] = True
        elif case == "wrong_attempt":
            data["attempt"] = 2
        elif case == "empty_ids":
            data["section_ids"] = ()
        elif case == "thirteen_ids":
            data["section_ids"] = tuple(f"section:{i:06d}" for i in range(1, 14))
        elif case == "duplicate_ids":
            data["section_ids"] = ("section:000001", "section:000001")
        elif case == "blank_id":
            data["section_ids"] = (" ",)
        elif case == "blank_content":
            data["content"] = " \n "
        elif case == "overlong_content":
            data["content"] = "C" * 359539
        elif case == "extra":
            data["extra"] = "forbidden"
        with pytest.raises((TypeError, ValidationError)):
            WorkflowMergedDraft(**data)
        return
    assert type(value) is WorkflowMergedDraft


def _draft(section_id: str, content: str = "Body") -> WorkflowSectionDraft:
    return WorkflowSectionDraft(
        outline_id="outline:000001",
        section_id=section_id,
        attempt=1,
        content=content,
    )


def _draft_tuple_for_state(
    state: AcademicWorkflowState,
    first_content: str = "Body",
) -> tuple[WorkflowSectionDraft, ...]:
    assert state.outline is not None
    return tuple(
        _draft(section.section_id, first_content if index == 0 else "Body")
        for index, section in enumerate(state.outline.sections)
    )


def _assert_fixed(error: BaseException) -> None:
    assert type(error) is module._SectionMergerError
    assert str(error) == "section merger failed"
    assert error.__cause__ is None
    assert error.__context__ is None
    assert error.__suppress_context__ is False


def _preflight_case(case: str) -> tuple[object, object]:
    sections = (_section(1), _section(2))
    state: object = _approved_state(sections=sections)
    drafts: object = (_draft("section:000001"), _draft("section:000002"))
    if case == "wrong_state_type":
        state = object()
    elif case == "draft_list":
        drafts = list(drafts)
    elif case == "rejected_state":
        state = _approved_state(sections=sections, approved=False)
    elif case == "wrong_count":
        drafts = (_draft("section:000001"),)
    elif case == "wrong_order":
        drafts = tuple(reversed(drafts))
    elif case == "wrong_outline":
        object.__getattribute__(drafts[0], "__dict__")["outline_id"] = "outline:wrong"
    elif case == "wrong_section":
        object.__getattribute__(drafts[0], "__dict__")["section_id"] = "section:000002"
    elif case == "bool_attempt":
        object.__getattribute__(drafts[0], "__dict__")["attempt"] = True
    elif case == "thirteen_sections":
        many = tuple(_section(i) for i in range(1, 14))
        state = _approved_state(sections=many)
        drafts = tuple(_draft(section.section_id) for section in many)
    elif case == "damaged_empty_outline":
        state = _approved_state()
        object.__getattribute__(state.outline, "__dict__")["sections"] = ()
        drafts = ()
    else:  # pragma: no cover - test construction guard
        raise AssertionError(case)
    return state, drafts


@pytest.mark.parametrize(
    "case",
    [
        "wrong_state_type",
        "draft_list",
        "rejected_state",
        "wrong_count",
        "wrong_order",
        "wrong_outline",
        "wrong_section",
        "bool_attempt",
        "thirteen_sections",
        "damaged_empty_outline",
    ],
)
def test_complete_preflight_rejects_invalid_shape_before_merge(case: str) -> None:
    state, drafts = _preflight_case(case)
    with pytest.raises(module._SectionMergerError) as captured:
        merge_sections(state, drafts)  # type: ignore[arg-type]
    _assert_fixed(captured.value)


class _DraftSubclass(WorkflowSectionDraft):
    pass


class _Hostile:
    calls = 0

    def _called(self) -> bool:
        type(self).calls += 1
        raise AssertionError("hostile code executed")

    __eq__ = lambda self, other: self._called()
    __repr__ = lambda self: self._called()
    __str__ = lambda self: self._called()
    __iter__ = lambda self: self._called()


def _hostile_draft(case: str) -> object:
    if case == "subclass":
        return _DraftSubclass(
            outline_id="outline:000001",
            section_id="section:000001",
            attempt=1,
            content="Body",
        )
    value = _draft("section:000001")
    namespace = object.__getattribute__(value, "__dict__")
    if case == "shadowed_method":
        namespace["model_dump"] = _Hostile()
    elif case == "extra":
        object.__setattr__(value, "__pydantic_extra__", {"hostile": _Hostile()})
    elif case == "private":
        object.__setattr__(value, "__pydantic_private__", {"hostile": _Hostile()})
    elif case == "hostile_field":
        namespace["content"] = _Hostile()
    elif case == "string_subclass":
        namespace["content"] = _StringSubclass("Body")
    else:  # pragma: no cover - test construction guard
        raise AssertionError(case)
    return value


@pytest.mark.parametrize(
    "case",
    ["subclass", "shadowed_method", "extra", "private", "hostile_field", "string_subclass"],
)
def test_untrusted_drafts_never_execute_dynamic_behavior(case: str) -> None:
    _Hostile.calls = 0
    with pytest.raises(module._SectionMergerError) as captured:
        merge_sections(_approved_state(), (_hostile_draft(case),))  # type: ignore[arg-type]
    _assert_fixed(captured.value)
    assert _Hostile.calls == 0


def _response(citations: list[str], content: str) -> str:
    return json.dumps(
        {"citations": citations, "content": content},
        ensure_ascii=False,
        separators=(",", ":"),
    )


class _Client:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls = 0
        self.user_message: str | None = None

    async def complete(self, *, system_message: str, user_message: str) -> object:
        self.calls += 1
        self.user_message = user_message
        return self.response


class _Factory:
    def __init__(self, client: _Client) -> None:
        self.client = client
        self.calls = 0

    def __call__(self) -> _Client:
        self.calls += 1
        return self.client


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _payload(
    state: AcademicWorkflowState,
    projected_sources: list[dict[str, str]],
) -> dict[str, object]:
    assert state.topic_plan is not None
    assert state.research_evidence is not None
    assert state.outline is not None
    return {
        "context_blocks": list(state.research_evidence.context_blocks[:8]),
        "evidence_sources": projected_sources,
        "language": state.request.language,
        "outline": {
            "outline_id": state.outline.outline_id,
            "sections": [
                {
                    "brief": section.brief,
                    "order": section.order,
                    "section_id": section.section_id,
                    "title": section.title,
                }
                for section in state.outline.sections
            ],
            "title": state.outline.title,
        },
        "research_questions": list(state.topic_plan.research_questions),
        "root_topic": state.topic_plan.research_topic,
        "target_section_id": "section:000001",
    }


def _boundary_state(
    first_context: str,
    *,
    sources: tuple[WorkflowEvidenceSource, ...] | None = None,
) -> AcademicWorkflowState:
    tail = (
        "B" * 4096,
        "C" * 4096,
        "D" * 4096,
        "E" * 4096,
        "F" * 4093,
        "GG",
        "H",
    )
    input_sources = (
        tuple(
            _source(
                index,
                title=chr(64 + index) * 256,
                url=f"https://example.test/{index}",
            )
            for index in range(1, 25)
        )
        if sources is None
        else sources
    )
    sections = tuple(
        _section(
            index,
            title=f"S{index:02d}-" + chr(96 + index) * 156,
            brief=chr(65 + index) * 1024,
        )
        for index in range(1, 9)
    )
    return _approved_state(
        query="\0" * 4096,
        language="L" * 128,
        questions=("Q" * 512, "R" * 511, "S"),
        context_blocks=(first_context, *tail),
        sources=input_sources,
        sections=sections,
        outline_title="T" * 256,
    )


def _projected(source: WorkflowEvidenceSource) -> dict[str, str]:
    return {
        "source_id": source.source_id,
        "title": source.title[:256],
        "url": source.url,
    }


def _golden_vector(
    case: str,
) -> tuple[
    AcademicWorkflowState,
    list[dict[str, str]] | None,
    int | None,
    str,
    list[str],
    tuple[tuple[str, list[str]], ...],
]:
    marker_1 = "[[cite:evidence-source:000001]]"
    if case == "count_cutoff":
        sources = tuple(
            _source(i, title=f"S{i}", url=f"https://e.test/{i}")
            for i in range(1, 26)
        )
        state = _approved_state(
            sources=sources,
            sections=(_section(1, title="s", brief="b"),),
        )
        expected = [_projected(source) for source in sources[:24]]
        return (
            state,
            expected,
            2152,
            "Body [[cite:evidence-source:000024]]",
            ["evidence-source:000024"],
            (("Body [[cite:evidence-source:000025]]", ["evidence-source:000025"]),),
        )
    if case in ("prompt_65536", "prompt_65537"):
        first = (
            ("\0" * 952) + "\n" + ("A" * 3143)
            if case == "prompt_65536"
            else ("\0" * 952) + ("\n" * 2) + ("A" * 3142)
        )
        state = _boundary_state(first)
        if case == "prompt_65537":
            return state, None, None, "Body", [], ()
        return state, [], 65536, "Body", [], ((f"Body {marker_1}", ["evidence-source:000001"]),)
    if case in ("unicode_admit", "unicode_reject"):
        source = _source(1, title="é" * 257, url="https://example.test/1")
        first = (
            ("\0" * 885) + ("A" * 3211)
            if case == "unicode_admit"
            else ("\0" * 885) + "\n" + ("A" * 3210)
        )
        state = _boundary_state(first, sources=(source,))
        if case == "unicode_admit":
            return state, [_projected(source)], 65536, f"Body {marker_1}", [source.source_id], ()
        return state, [], 65201, "Body", [], ((f"Body {marker_1}", [source.source_id]),)
    if case == "duplicate_title":
        sources = (
            _source(1, title="DUP", url="https://e.test/1"),
            _source(2, title="DUP", url="https://e.test/2"),
        )
        state = _approved_state(
            sources=sources,
            sections=(_section(1, title="s", brief="b"),),
        )
        body = "Body [[cite:evidence-source:000001]] [[cite:evidence-source:000002]]"
        return (
            state,
            [_projected(source) for source in sources],
            430,
            body,
            [source.source_id for source in sources],
            (),
        )
    if case == "empty_sources":
        state = _approved_state(sections=(_section(1, title="s", brief="b"),))
        return state, [], 275, "Body", [], ((f"Body {marker_1}", ["evidence-source:000001"]),)
    if case == "first_wins":
        long_url = "https://e.test/" + ("X" * 4081)
        sources = (
            _source(1, title="DUP", url=long_url),
            _source(2, title="DUP", url="https://e.test/2"),
        )
        state = _boundary_state(("\0" * 125) + ("A" * 3971), sources=sources)
        rejected = tuple(
            (f"Body [[cite:{source.source_id}]]", [source.source_id])
            for source in sources
        )
        return state, [], 61400, "Body", [], rejected
    raise AssertionError(case)  # pragma: no cover - test construction guard


@pytest.mark.parametrize(
    "case",
    [
        "count_cutoff",
        "prompt_65536",
        "prompt_65537",
        "unicode_admit",
        "unicode_reject",
        "duplicate_title",
        "empty_sources",
        "first_wins",
    ],
)
@pytest.mark.asyncio
async def test_public_35_oracle_and_merger_allowlists_match(case: str) -> None:
    state, expected_sources, expected_length, body, citations, rejected = _golden_vector(case)
    client = _Client(_response(citations, body))
    factory = _Factory(client)
    adapter = GPTResearcherSectionWriterAdapter(section_writer_client_factory=factory)
    if case == "prompt_65537":
        with pytest.raises(ValueError):
            await adapter.write_section(state, "section:000001")
        assert factory.calls == client.calls == 0
        assert len(_canonical(_payload(state, []))) == 65537
        with pytest.raises(module._SectionMergerError):
            merge_sections(state, _draft_tuple_for_state(state))
        return
    await adapter.write_section(state, "section:000001")
    assert factory.calls == client.calls == 1
    assert type(client.user_message) is str
    assert len(client.user_message) == expected_length
    parsed = json.loads(client.user_message)
    assert parsed["evidence_sources"] == expected_sources
    assert client.user_message == _canonical(parsed)
    merge_drafts = _draft_tuple_for_state(state, body)
    merged = merge_sections(state, merge_drafts)
    assert state.outline is not None
    assert merged.content == "\n\n".join(
        f"## {section.title}\n\n{draft.content}"
        for section, draft in zip(state.outline.sections, merge_drafts, strict=True)
    )

    if case.startswith("unicode"):
        source = state.research_evidence.sources[0]  # type: ignore[union-attr]
        projected = _projected(source)
        base = len(_canonical(_payload(state, [])))
        assert len(_canonical(_payload(state, [projected]))) == base + 336
        assert len(_canonical(_payload(state, [{**projected, "title": "é" * 255}]))) == base + 335
        assert len(_canonical(_payload(state, [{**projected, "title": "é" * 257}]))) == base + 337
        assert len(
            json.dumps(
                _payload(state, [projected]),
                ensure_ascii=True,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        ) == base + 1616
        assert len(_canonical(_payload(state, [{**projected, "title": "é" * 128}]))) == base + 208
    if case == "first_wins":
        sources = state.research_evidence.sources  # type: ignore[union-attr]
        assert len(_canonical(_payload(state, []))) == 61400
        assert len(_canonical(_payload(state, [_projected(sources[0])]))) == 65557
        assert len(_canonical(_payload(state, [_projected(sources[1])]))) == 61477

    for rejected_body, rejected_citations in rejected:
        rejected_client = _Client(_response(rejected_citations, rejected_body))
        rejected_factory = _Factory(rejected_client)
        with pytest.raises(RuntimeError):
            await GPTResearcherSectionWriterAdapter(
                section_writer_client_factory=rejected_factory
            ).write_section(state, "section:000001")
        with pytest.raises(module._SectionMergerError):
            merge_sections(state, _draft_tuple_for_state(state, rejected_body))


@pytest.mark.parametrize(
    ("content", "valid"),
    [
        ("Body [[cite:evidence-source:000001]] twice [[cite:evidence-source:000001]]", True),
        ("[[cite:evidence-source:000002]] then [[cite:evidence-source:000001]]", True),
        ("www.example.test DOI 10.1/example natural attribution", True),
        ("Body [[cite:evidence-source:000001", False),
        ("Body [[cite:]]", False),
        ("Body [[cite:evidence[source:000001]]", False),
        ("Body [unmarked]", False),
        ("Body https://example.test", False),
    ],
)
def test_citation_and_literal_url_matrix(content: str, valid: bool) -> None:
    state = _approved_state(sources=(_source(1), _source(2)))
    if valid:
        result = merge_sections(state, (_draft("section:000001", content),))
        assert result.content == f"## Title\n\n{content}"
    else:
        with pytest.raises(module._SectionMergerError) as captured:
            merge_sections(state, (_draft("section:000001", content),))
        _assert_fixed(captured.value)


@pytest.mark.parametrize("section_count", [12])
def test_markdown_order_unicode_and_opaque_headings(section_count: int) -> None:
    sections = tuple(_section(i, title=f"标题 {i}") for i in range(1, section_count + 1))
    state = _approved_state(sections=sections)
    bodies = tuple(f"正文 Ω {i}\n### Inner {i}" for i in range(1, section_count + 1))
    drafts = tuple(
        _draft(section.section_id, body)
        for section, body in zip(sections, bodies, strict=True)
    )
    before = (
        state.model_dump(mode="json"),
        tuple(draft.model_dump(mode="json") for draft in drafts),
    )
    result = merge_sections(state, drafts)
    expected = "\n\n".join(
        f"## {section.title}\n\n{body}"
        for section, body in zip(sections, bodies, strict=True)
    )
    assert result.content == expected
    assert not result.content.startswith("\n")
    assert not result.content.endswith("\n")
    assert result.section_ids == tuple(section.section_id for section in sections)
    assert before == (
        state.model_dump(mode="json"),
        tuple(draft.model_dump(mode="json") for draft in drafts),
    )


def test_reachable_merged_maximum_and_formula() -> None:
    sections = (_section(1, title="A" * 64533, brief="b"),) + tuple(
        _section(i, title=chr(64 + i), brief="b") for i in range(2, 13)
    )
    state = _approved_state(sections=sections)
    assert len(_canonical(_payload(state, []))) == 65536
    drafts = tuple(_draft(section.section_id, "C" * 24576) for section in sections)
    result = merge_sections(state, drafts)
    assert len(result.content) == 359538
    assert sum(len(section.title) for section in sections) == 64544
    assert [209 + 66 * n + max(0, n - 9) for n in range(1, 13)][-1] == 1004


def test_fixed_error_traceback_releases_sensitive_inputs() -> None:
    state = _approved_state(sections=(_section(1), _section(2)))
    first = _draft("section:000001", "Sensitive body 981734")
    second = _draft("section:000002")
    object.__getattribute__(second, "__dict__")["section_id"] = "section:000001"
    drafts = (first, second)
    targets = (state, drafts, first, second, object.__getattribute__(first, "__dict__"))
    with pytest.raises(module._SectionMergerError) as captured:
        merge_sections(state, drafts)
    error = captured.value
    _assert_fixed(error)
    assert all(argument is not target for argument in error.args for target in targets)
    traceback = error.__traceback__
    while traceback is not None:
        frame = traceback.tb_frame
        if frame.f_globals.get("__name__") == module.__name__:
            local_values = tuple(frame.f_locals.values())
            assert all(value is not target for value in local_values for target in targets)
            for value in local_values:
                if inspect.isfunction(value):
                    closure = object.__getattribute__(value, "__closure__")
                    if type(closure) is tuple:
                        assert all(
                            cell.cell_contents is not target
                            for cell in closure
                            for target in targets
                        )
        traceback = traceback.tb_next


def test_repeated_calls_are_deterministic_and_nonmutating() -> None:
    state = _approved_state(sources=(_source(1),))
    drafts = (_draft("section:000001", "Body [[cite:evidence-source:000001]]"),)
    before = (state.model_dump_json(), tuple(draft.model_dump_json() for draft in drafts))
    first = merge_sections(state, drafts)
    second = merge_sections(state, drafts)
    assert first == second
    first_bytes = _canonical(first.model_dump(mode="json")).encode("utf-8")
    second_bytes = _canonical(second.model_dump(mode="json")).encode("utf-8")
    assert first_bytes == second_bytes
    assert before == (state.model_dump_json(), tuple(draft.model_dump_json() for draft in drafts))
