import socket

import pytest
from pydantic import ValidationError

from gpt_researcher.screening.audit import (
    AUDIT_UNAVAILABLE_MESSAGE,
    AuditCollectorState,
    AuditExclusionReason,
    AuditRoutingStatus,
    PaperScreeningAuditCollector,
    PaperScreeningAuditEntry,
    PaperScreeningAuditSnapshot,
    PaperScreeningOccurrenceRef,
    PaperScreeningProviderWarning,
    PaperScreeningRequestMetadata,
    ProviderWarningCategory,
    build_paper_screening_provider_warning,
    build_paper_screening_web_pass_audit,
)
from gpt_researcher.screening.decisions import (
    CandidateOccurrence,
    ScreeningPolicy,
)
from gpt_researcher.screening.models import PaperCandidate
from gpt_researcher.screening.rules import screen_paper_occurrences
from gpt_researcher.screening.decisions import RetrievalRequestRoute
from gpt_researcher.screening.relevance import (
    TopicRelevanceDecision,
    TopicRelevanceReasonCode,
    TopicRelevanceVerdict,
    TopicScreeningResult,
)


@pytest.fixture(autouse=True)
def _no_external_access(monkeypatch):
    def blocked(*_args, **_kwargs):
        raise AssertionError("real external access is forbidden")

    monkeypatch.setattr(socket, "create_connection", blocked)


def _candidate(candidate_id, *, doi=None, year=2024, rank=1, query="query"):
    return PaperCandidate(
        candidate_id=candidate_id,
        source="arxiv",
        source_record_id=candidate_id,
        retrieval_query=query,
        source_rank=rank,
        title=f"Title for {candidate_id}",
        href=f"https://papers.invalid/{candidate_id}",
        body="RAW_BODY_SENTINEL",
        abstract="RAW_ABSTRACT_SENTINEL",
        authors=(),
        published_year=year,
        published_at=None,
        updated_at=None,
        venue="Venue",
        publication_venue_id=None,
        publication_venue_name=None,
        publication_venue_type=None,
        publication_venue_alternate_names=(),
        doi=doi,
        external_ids=(),
        citation_count=None,
        publication_types=(),
        categories=(),
        journal_reference=None,
    )


def _screening_result():
    occurrences = (
        CandidateOccurrence(
            occurrence_id="occ:planning",
            retrieval_request_id="planning:000001",
            planning_only=True,
            candidate=_candidate("planning", doi="10.1/shared", rank=1),
        ),
        CandidateOccurrence(
            occurrence_id="occ:duplicate",
            retrieval_request_id="evidence:000001",
            planning_only=False,
            candidate=_candidate("duplicate", doi="10.1/shared", rank=2),
        ),
        CandidateOccurrence(
            occurrence_id="occ:included",
            retrieval_request_id="evidence:000001",
            planning_only=False,
            candidate=_candidate("included", doi="10.1/included", rank=3),
        ),
        CandidateOccurrence(
            occurrence_id="occ:old",
            retrieval_request_id="evidence:000002",
            planning_only=False,
            candidate=_candidate("old", doi="10.1/old", year=2010, rank=4),
        ),
    )
    return screen_paper_occurrences(occurrences, ScreeningPolicy(min_year=2020))


def _pass(pass_order=1, pass_id="web-pass:000001"):
    result = _screening_result()
    requests = (
        PaperScreeningRequestMetadata(
            retrieval_request_id="planning:000001",
            planning_only=True,
            retrieval_query="root query",
            provider_warnings=(),
        ),
        PaperScreeningRequestMetadata(
            retrieval_request_id="evidence:000001",
            planning_only=False,
            retrieval_query="evidence one",
            provider_warnings=(),
        ),
        PaperScreeningRequestMetadata(
            retrieval_request_id="evidence:000002",
            planning_only=False,
            retrieval_query="evidence two",
            provider_warnings=(),
        ),
        PaperScreeningRequestMetadata(
            retrieval_request_id="evidence:000003",
            planning_only=False,
            retrieval_query="ordinary only",
            provider_warnings=(),
        ),
    )
    return build_paper_screening_web_pass_audit(
        web_pass_order=pass_order,
        web_pass_id=pass_id,
        policy=ScreeningPolicy(min_year=2020),
        topic_relevance_enabled=False,
        deterministic_result=result,
        topic_result=None,
        request_metadata=requests,
    )


def test_reference_and_warning_models_are_strict_frozen_and_extra_forbid():
    ref = PaperScreeningOccurrenceRef(
        web_pass_id=" web-pass:000001 ", occurrence_id=" occ:one "
    )
    assert ref.web_pass_id == "web-pass:000001"
    assert ref.occurrence_id == "occ:one"
    with pytest.raises(ValidationError):
        PaperScreeningOccurrenceRef(web_pass_id="p", occurrence_id="o", extra=True)
    with pytest.raises(ValidationError):
        ref.occurrence_id = "other"
    with pytest.raises(ValidationError):
        PaperScreeningProviderWarning(
            retriever_index=True,
            source_identifier="source",
            category=ProviderWarningCategory.CALL,
        )


def test_warning_identity_index_and_fallback_never_use_instance_text():
    class Retriever:
        def __str__(self):
            raise AssertionError("str forbidden")

        def __repr__(self):
            raise AssertionError("repr forbidden")

    retriever = Retriever()
    warning = build_paper_screening_provider_warning(
        retriever, 1, ProviderWarningCategory.CALL
    )
    assert warning.retriever_index == 1
    assert warning.source_identifier == f"{Retriever.__module__}.{Retriever.__qualname__}"

    class BrokenMeta(type):
        def __getattribute__(cls, name):
            if name in {"__module__", "__qualname__"}:
                raise RuntimeError("SYNTHETIC_SECRET")
            return super().__getattribute__(name)

    class Broken(metaclass=BrokenMeta):
        pass

    fallback = build_paper_screening_provider_warning(
        Broken(), 2, ProviderWarningCategory.CONTRACT
    )
    assert fallback.source_identifier == "unknown_retriever"

    class Blank:
        pass

    Blank.__module__ = " "
    Blank.__qualname__ = " "
    assert build_paper_screening_provider_warning(
        Blank(), 3, ProviderWarningCategory.MATERIALIZATION
    ).source_identifier == "unknown_retriever"


def test_builder_derives_routes_exclusions_empty_request_and_no_raw_content():
    audit_pass = _pass()
    entries = {entry.occurrence_id: entry for entry in audit_pass.occurrence_entries}
    assert entries["occ:planning"].routing_status is AuditRoutingStatus.ROUTED
    assert entries["occ:duplicate"].routing_status is AuditRoutingStatus.EXCLUDED
    assert entries["occ:duplicate"].final_exclusion_reasons == (
        AuditExclusionReason.DUPLICATE_OF_CANONICAL,
    )
    assert entries["occ:old"].final_exclusion_reasons == (
        AuditExclusionReason.YEAR_BELOW_MIN,
    )
    empty = next(
        item
        for item in audit_pass.request_audits
        if item.retrieval_request_id == "evidence:000003"
    )
    assert empty.occurrence_ids == ()
    assert empty.deterministic_route_present is False
    payload = audit_pass.model_dump_json()
    assert "RAW_BODY_SENTINEL" not in payload
    assert "RAW_ABSTRACT_SENTINEL" not in payload


def test_entry_rejects_legacy_and_inconsistent_routing_states():
    base = _pass().occurrence_entries[0].model_dump()
    with pytest.raises(ValidationError):
        PaperScreeningAuditEntry(**{**base, "routing_status": "not_routed"})
    with pytest.raises(ValidationError):
        PaperScreeningAuditEntry(
            **{
                **base,
                "routing_status": AuditRoutingStatus.EXCLUDED,
                "screening_included": False,
                "routed_request_ids": ("evidence:000001",),
                "routed_to_evidence": True,
            }
        )


def test_collector_prepare_commit_compound_refs_global_order_and_summary():
    collector = PaperScreeningAuditCollector(
        run_ordinal=1,
        policy=ScreeningPolicy(min_year=2020),
        topic_relevance_enabled=False,
    )
    collector.add_pass(_pass())
    collector.add_pass(_pass(2, "web-pass:000002"))
    collector.prepare()
    assert collector.state is AuditCollectorState.OPEN
    with pytest.raises(RuntimeError, match=AUDIT_UNAVAILABLE_MESSAGE):
        collector.snapshot()
    collector.commit()
    snapshot = collector.snapshot()
    assert snapshot is collector.snapshot()
    assert tuple(item.audit_order for item in snapshot.occurrence_entries) == tuple(
        range(1, len(snapshot.occurrence_entries) + 1)
    )
    assert len({tuple(ref) for ref in snapshot.screening_included_canonical_occurrence_refs}) == len(
        snapshot.screening_included_canonical_occurrence_refs
    )
    assert snapshot.summary.total_occurrences == len(snapshot.occurrence_entries)
    assert snapshot.summary.deterministically_included_groups == snapshot.summary.groups_with_canonical
    assert snapshot.summary.requests_without_academic_occurrences == 2
    forged = snapshot.model_dump()
    forged["summary"]["total_occurrences"] += 1
    with pytest.raises(ValidationError):
        PaperScreeningAuditSnapshot(**forged)
    with pytest.raises(ValidationError):
        PaperScreeningAuditSnapshot(**{**snapshot.model_dump(), "request_audits": []})

    undeclared_pass = snapshot.model_dump()
    undeclared_pass["request_audits"][0]["web_pass_id"] = "web-pass:999999"
    with pytest.raises(ValidationError):
        PaperScreeningAuditSnapshot(**undeclared_pass)

    unknown_route = snapshot.model_dump()
    evidence = next(
        item
        for item in unknown_route["request_audits"]
        if item["retrieval_request_id"] == "evidence:000001"
    )
    evidence["deterministic_canonical_occurrence_ids"] = ("occ:missing",)
    evidence["effective_canonical_occurrence_ids"] = ("occ:missing",)
    with pytest.raises(ValidationError):
        PaperScreeningAuditSnapshot(**unknown_route)


def test_collector_abort_hides_pending_snapshot():
    collector = PaperScreeningAuditCollector(
        run_ordinal=1,
        policy=ScreeningPolicy(),
        topic_relevance_enabled=False,
    )
    collector.add_pass(
        build_paper_screening_web_pass_audit(
            web_pass_order=1,
            web_pass_id="web-pass:000001",
            policy=ScreeningPolicy(),
            topic_relevance_enabled=False,
            deterministic_result=screen_paper_occurrences((), ScreeningPolicy()),
            topic_result=None,
            request_metadata=(),
        )
    )
    collector.prepare()
    collector.abort()
    assert collector.state is AuditCollectorState.ABORTED
    with pytest.raises(RuntimeError, match=AUDIT_UNAVAILABLE_MESSAGE):
        collector.snapshot()


@pytest.mark.parametrize(
    ("verdict", "reason", "expected_status", "expected_reason"),
    (
        (
            TopicRelevanceVerdict.RELEVANT,
            TopicRelevanceReasonCode.DIRECT_TOPIC_MATCH,
            AuditRoutingStatus.ROUTED,
            (),
        ),
        (
            TopicRelevanceVerdict.UNCERTAIN,
            TopicRelevanceReasonCode.LLM_FAILURE,
            AuditRoutingStatus.ROUTED,
            (),
        ),
        (
            TopicRelevanceVerdict.IRRELEVANT,
            TopicRelevanceReasonCode.OUT_OF_SCOPE,
            AuditRoutingStatus.EXCLUDED,
            (AuditExclusionReason.TOPIC_IRRELEVANT,),
        ),
    ),
)
def test_topic_decisions_drive_routes_and_preserve_validated_rationale(
    verdict, reason, expected_status, expected_reason
):
    occurrence = CandidateOccurrence(
        occurrence_id="occ:topic",
        retrieval_request_id="evidence:000001",
        planning_only=False,
        candidate=_candidate("topic"),
    )
    deterministic = screen_paper_occurrences((occurrence,), ScreeningPolicy())
    group = deterministic.duplicate_groups[0]
    decision = TopicRelevanceDecision(
        decision_order=1,
        duplicate_group_id=group.group_id,
        canonical_occurrence_id="occ:topic",
        canonical_candidate_id="topic",
        verdict=verdict,
        reason_code=reason,
        rationale="VALIDATED_RATIONALE_SENTINEL",
        confidence=50,
    )
    effective_ids = () if verdict is TopicRelevanceVerdict.IRRELEVANT else ("occ:topic",)
    topic = TopicScreeningResult(
        deterministic_result=deterministic,
        relevance_decisions=(decision,),
        effective_routes=(
            RetrievalRequestRoute(
                retrieval_request_id="evidence:000001",
                canonical_occurrence_ids=effective_ids,
            ),
        ),
    )
    audit_pass = build_paper_screening_web_pass_audit(
        web_pass_order=1,
        web_pass_id="web-pass:000001",
        policy=ScreeningPolicy(),
        topic_relevance_enabled=True,
        deterministic_result=deterministic,
        topic_result=topic,
        request_metadata=(
            PaperScreeningRequestMetadata(
                retrieval_request_id="evidence:000001",
                planning_only=False,
                retrieval_query="query",
                provider_warnings=(),
            ),
        ),
    )
    entry = audit_pass.occurrence_entries[0]
    assert entry.routing_status is expected_status
    assert entry.final_exclusion_reasons == expected_reason
    assert entry.topic_rationale == "VALIDATED_RATIONALE_SENTINEL"
    payload = audit_pass.model_dump_json()
    assert "RAW_ABSTRACT_SENTINEL" not in payload
    assert "RAW_BODY_SENTINEL" not in payload


def _build_topic_audit_with_group_canonical_pattern(pattern):
    policy = ScreeningPolicy(min_year=2020)
    occurrences = tuple(
        CandidateOccurrence(
            occurrence_id=f"occ:{index}",
            retrieval_request_id="evidence:000001",
            planning_only=False,
            candidate=_candidate(
                f"candidate-{index}",
                doi=f"10.1/{chr(96 + index)}",
                year=2024 if has_canonical else 2010,
                rank=index,
            ),
        )
        for index, has_canonical in enumerate(pattern, start=1)
    )
    deterministic = screen_paper_occurrences(occurrences, policy)
    canonical_groups = tuple(
        group
        for group in deterministic.duplicate_groups
        if group.canonical_occurrence_id is not None
    )
    occurrence_by_id = {
        occurrence.occurrence_id: occurrence
        for occurrence in deterministic.occurrences
    }
    decisions = tuple(
        TopicRelevanceDecision(
            decision_order=decision_order,
            duplicate_group_id=group.group_id,
            canonical_occurrence_id=group.canonical_occurrence_id,
            canonical_candidate_id=occurrence_by_id[
                group.canonical_occurrence_id
            ].candidate.candidate_id,
            verdict=TopicRelevanceVerdict.RELEVANT,
            reason_code=TopicRelevanceReasonCode.DIRECT_TOPIC_MATCH,
            rationale="Relevant.",
            confidence=100,
        )
        for decision_order, group in enumerate(canonical_groups, start=1)
    )
    topic = TopicScreeningResult(
        deterministic_result=deterministic,
        relevance_decisions=decisions,
        effective_routes=deterministic.routes,
    )
    return build_paper_screening_web_pass_audit(
        web_pass_order=1,
        web_pass_id="web-pass:000001",
        policy=policy,
        topic_relevance_enabled=True,
        deterministic_result=deterministic,
        topic_result=topic,
        request_metadata=(
            PaperScreeningRequestMetadata(
                retrieval_request_id="evidence:000001",
                planning_only=False,
                retrieval_query="query",
                provider_warnings=(),
            ),
        ),
    )


def test_topic_decision_order_ignores_leading_group_without_canonical():
    audit_pass = _build_topic_audit_with_group_canonical_pattern((False, True))

    assert tuple(group.pass_group_order for group in audit_pass.group_audits) == (
        1,
        2,
    )
    assert tuple(
        None if group.topic_decision is None else group.topic_decision.decision_order
        for group in audit_pass.group_audits
    ) == (None, 1)


def test_topic_decision_order_remains_contiguous_across_group_gap():
    audit_pass = _build_topic_audit_with_group_canonical_pattern((True, False, True))

    assert tuple(group.pass_group_order for group in audit_pass.group_audits) == (
        1,
        2,
        3,
    )
    assert tuple(
        None if group.topic_decision is None else group.topic_decision.decision_order
        for group in audit_pass.group_audits
    ) == (1, None, 2)
