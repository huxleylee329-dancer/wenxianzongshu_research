"""Pure deterministic grouping and rule screening for paper occurrences."""

import hashlib
import json
import unicodedata
from collections import defaultdict

from .decisions import (
    CandidateOccurrence,
    DuplicateGroup,
    PaperType,
    RetrievalRequestRoute,
    ScreeningDecision,
    ScreeningPolicy,
    ScreeningReasonCode,
    ScreeningResult,
    UnknownValuePolicy,
)
from .models import PaperCandidate


_GROUP_KIND_ORDER = {"doi": 0, "title": 1, "singleton": 2}
_TYPE_QUALITY = {
    PaperType.REVIEW: 0,
    PaperType.JOURNAL: 1,
    PaperType.CONFERENCE: 2,
    PaperType.BOOK_CHAPTER: 3,
    PaperType.PREPRINT: 4,
    PaperType.UNKNOWN: 5,
}
_PUBLICATION_TYPE_MAP = {
    "review": PaperType.REVIEW,
    "journalarticle": PaperType.JOURNAL,
    "conference": PaperType.CONFERENCE,
    "booksection": PaperType.BOOK_CHAPTER,
}
_VENUE_TYPE_MAP = {
    "journal": PaperType.JOURNAL,
    "conference": PaperType.CONFERENCE,
}


def _stable_candidate_payload(candidate: PaperCandidate) -> str:
    data = candidate.model_dump(mode="json")
    ordered = {
        field_name: data[field_name]
        for field_name in PaperCandidate.model_fields
    }
    return json.dumps(
        ordered,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=False,
    )


def _occurrence_sort_key(occurrence: CandidateOccurrence) -> tuple:
    candidate = occurrence.candidate
    return (
        occurrence.retrieval_request_id,
        candidate.retrieval_query,
        candidate.source,
        candidate.source_rank,
        candidate.candidate_id,
        _stable_candidate_payload(candidate),
        occurrence.occurrence_id,
    )


def _normalized_title(title: str) -> str:
    normalized = unicodedata.normalize("NFKC", title).casefold()
    return " ".join(normalized.split()).strip()


def _duplicate_key(occurrence: CandidateOccurrence) -> tuple[str, str]:
    candidate = occurrence.candidate
    doi = candidate.doi.strip().casefold() if isinstance(candidate.doi, str) else ""
    if doi:
        return "doi", doi
    title = _normalized_title(candidate.title)
    if sum(character.isalnum() for character in title) >= 12:
        return "title", title
    return "singleton", occurrence.occurrence_id


def _group_id(kind: str, value: str) -> str:
    digest = hashlib.sha256(f"{kind}:{value}".encode("utf-8")).hexdigest()
    return f"group:sha256:{digest}"


def _normalized_tokens(values: tuple[str, ...]) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        token = value.strip().casefold()
        if token and token not in seen:
            seen.add(token)
            normalized.append(token)
    return tuple(normalized)


def _classify(candidate: PaperCandidate) -> tuple[PaperType, tuple[str, ...]]:
    if candidate.source == "arxiv":
        return PaperType.PREPRINT, ("source:arxiv",)

    publication_tokens = _normalized_tokens(candidate.publication_types)
    evidence: list[str] = [
        f"publication_types:{token}" for token in publication_tokens
    ]
    venue_token = ""
    if isinstance(candidate.publication_venue_type, str):
        venue_token = candidate.publication_venue_type.strip().casefold()
        if venue_token:
            evidence.append(f"publication_venue_type:{venue_token}")

    deduplicated_evidence = tuple(dict.fromkeys(evidence)) or ("none",)

    if publication_tokens:
        recognized = {
            _PUBLICATION_TYPE_MAP[token]
            for token in publication_tokens
            if token in _PUBLICATION_TYPE_MAP
        }
        if not recognized:
            classified = PaperType.UNKNOWN
        elif len(recognized) == 1:
            classified = next(iter(recognized))
        elif recognized == {PaperType.REVIEW, PaperType.JOURNAL}:
            classified = PaperType.REVIEW
        else:
            classified = PaperType.UNKNOWN
        return classified, deduplicated_evidence

    return _VENUE_TYPE_MAP.get(venue_token, PaperType.UNKNOWN), deduplicated_evidence


def _intrinsic_reasons(
    occurrence: CandidateOccurrence,
    classified_type: PaperType,
    policy: ScreeningPolicy,
) -> tuple[ScreeningReasonCode, ...]:
    candidate = occurrence.candidate
    reasons: list[ScreeningReasonCode] = []
    if candidate.published_year is None:
        if policy.unknown_year is UnknownValuePolicy.EXCLUDE:
            reasons.append(ScreeningReasonCode.YEAR_UNKNOWN)
    else:
        if policy.min_year is not None and candidate.published_year < policy.min_year:
            reasons.append(ScreeningReasonCode.YEAR_BELOW_MIN)
        if policy.max_year is not None and candidate.published_year > policy.max_year:
            reasons.append(ScreeningReasonCode.YEAR_ABOVE_MAX)

    if classified_type is PaperType.UNKNOWN:
        if policy.unknown_paper_type is UnknownValuePolicy.EXCLUDE:
            reasons.append(ScreeningReasonCode.TYPE_UNKNOWN)
    elif (
        policy.allowed_paper_types is not None
        and classified_type not in policy.allowed_paper_types
    ):
        reasons.append(ScreeningReasonCode.TYPE_NOT_ALLOWED)
    return tuple(reasons)


def _canonical_key(
    occurrence: CandidateOccurrence,
    classified_type: PaperType,
) -> tuple:
    candidate = occurrence.candidate
    doi_present = isinstance(candidate.doi, str) and bool(candidate.doi.strip())
    structured_venue_present = any(
        isinstance(value, str) and bool(value.strip())
        for value in (
            candidate.publication_venue_id,
            candidate.publication_venue_name,
            candidate.publication_venue_type,
        )
    )
    citation_known = candidate.citation_count is not None
    year_known = candidate.published_year is not None
    provider_priority = 0 if candidate.source == "semantic_scholar" else 1
    return (
        _TYPE_QUALITY[classified_type],
        0 if doi_present else 1,
        0 if structured_venue_present else 1,
        0 if citation_known else 1,
        -(candidate.citation_count or 0),
        0 if year_known else 1,
        -(candidate.published_year or 0),
        -len(candidate.abstract),
        provider_priority,
        candidate.source_rank,
        candidate.candidate_id,
        _stable_candidate_payload(candidate),
        occurrence.occurrence_id,
    )


def _validate_inputs(
    occurrences: tuple[CandidateOccurrence, ...],
    policy: ScreeningPolicy,
) -> None:
    if type(occurrences) is not tuple:
        raise TypeError("occurrences must be exactly a tuple")
    if not all(isinstance(item, CandidateOccurrence) for item in occurrences):
        raise TypeError("occurrences must contain only CandidateOccurrence values")
    if not isinstance(policy, ScreeningPolicy):
        raise TypeError("policy must be a ScreeningPolicy")

    occurrence_ids = [item.occurrence_id for item in occurrences]
    if len(set(occurrence_ids)) != len(occurrence_ids):
        raise ValueError("occurrence_id values must be unique")

    request_kinds: dict[str, bool] = {}
    for occurrence in occurrences:
        previous = request_kinds.setdefault(
            occurrence.retrieval_request_id, occurrence.planning_only
        )
        if previous is not occurrence.planning_only:
            raise ValueError("retrieval requests must have consistent planning_only")


def screen_paper_occurrences(
    occurrences: tuple[CandidateOccurrence, ...],
    policy: ScreeningPolicy,
) -> ScreeningResult:
    """Screen occurrences with deterministic, side-effect-free rules."""
    _validate_inputs(occurrences, policy)
    ordered_occurrences = tuple(sorted(occurrences, key=_occurrence_sort_key))

    classifications: dict[str, tuple[PaperType, tuple[str, ...]]] = {}
    intrinsic_reasons: dict[str, tuple[ScreeningReasonCode, ...]] = {}
    for occurrence in ordered_occurrences:
        classification = _classify(occurrence.candidate)
        classifications[occurrence.occurrence_id] = classification
        intrinsic_reasons[occurrence.occurrence_id] = _intrinsic_reasons(
            occurrence, classification[0], policy
        )

    grouped: dict[tuple[str, str], list[CandidateOccurrence]] = defaultdict(list)
    for occurrence in ordered_occurrences:
        grouped[_duplicate_key(occurrence)].append(occurrence)

    ordered_group_keys = tuple(
        sorted(
            grouped,
            key=lambda item: (
                _GROUP_KIND_ORDER[item[0]],
                item[1],
                _group_id(*item),
            ),
        )
    )

    groups: list[DuplicateGroup] = []
    group_by_occurrence: dict[str, DuplicateGroup] = {}
    occurrence_by_id = {
        occurrence.occurrence_id: occurrence for occurrence in ordered_occurrences
    }
    for kind, value in ordered_group_keys:
        members = tuple(
            sorted(
                grouped[(kind, value)],
                key=lambda item: _canonical_key(
                    item, classifications[item.occurrence_id][0]
                ),
            )
        )
        eligible = tuple(
            member
            for member in members
            if not intrinsic_reasons[member.occurrence_id]
        )
        canonical = eligible[0] if eligible else None
        group = DuplicateGroup(
            group_id=_group_id(kind, value),
            key_kind=kind,
            key_value=value,
            member_occurrence_ids=tuple(member.occurrence_id for member in members),
            reference_occurrence_id=members[0].occurrence_id,
            canonical_occurrence_id=(
                canonical.occurrence_id if canonical is not None else None
            ),
        )
        groups.append(group)
        for member in members:
            group_by_occurrence[member.occurrence_id] = group

    decisions: list[ScreeningDecision] = []
    for decision_order, occurrence in enumerate(ordered_occurrences, start=1):
        group = group_by_occurrence[occurrence.occurrence_id]
        canonical_id = group.canonical_occurrence_id
        canonical = occurrence_by_id[canonical_id] if canonical_id is not None else None
        reasons = list(intrinsic_reasons[occurrence.occurrence_id])
        included = canonical_id == occurrence.occurrence_id
        if included:
            matched_rules = (ScreeningReasonCode.INCLUDED,)
            primary_reason = ScreeningReasonCode.INCLUDED
        else:
            if canonical_id is not None:
                reasons.append(ScreeningReasonCode.DUPLICATE_OF_CANONICAL)
            matched_rules = tuple(reasons)
            primary_reason = matched_rules[0]

        classified_type, type_evidence = classifications[occurrence.occurrence_id]
        decisions.append(
            ScreeningDecision(
                decision_order=decision_order,
                occurrence_id=occurrence.occurrence_id,
                retrieval_request_id=occurrence.retrieval_request_id,
                candidate_id=occurrence.candidate.candidate_id,
                source=occurrence.candidate.source,
                retrieval_query=occurrence.candidate.retrieval_query,
                source_rank=occurrence.candidate.source_rank,
                included=included,
                primary_reason=primary_reason,
                matched_rules=matched_rules,
                classified_type=classified_type,
                type_evidence=type_evidence,
                duplicate_group_id=group.group_id,
                duplicate_key_kind=group.key_kind,
                canonical_occurrence_id=canonical_id,
                canonical_candidate_id=(
                    canonical.candidate.candidate_id if canonical is not None else None
                ),
            )
        )

    included_canonical_ids = tuple(
        group.canonical_occurrence_id
        for group in groups
        if group.canonical_occurrence_id is not None
    )
    request_ids = tuple(
        sorted({occurrence.retrieval_request_id for occurrence in ordered_occurrences})
    )
    request_kinds = {
        occurrence.retrieval_request_id: occurrence.planning_only
        for occurrence in ordered_occurrences
    }
    routes: list[RetrievalRequestRoute] = []
    for request_id in request_ids:
        canonical_ids: list[str] = []
        if not request_kinds[request_id]:
            for group in groups:
                if group.canonical_occurrence_id is None:
                    continue
                if any(
                    occurrence_by_id[member_id].retrieval_request_id == request_id
                    and not occurrence_by_id[member_id].planning_only
                    for member_id in group.member_occurrence_ids
                ):
                    canonical_ids.append(group.canonical_occurrence_id)
        routes.append(
            RetrievalRequestRoute(
                retrieval_request_id=request_id,
                canonical_occurrence_ids=tuple(canonical_ids),
            )
        )

    return ScreeningResult(
        occurrences=ordered_occurrences,
        decisions=tuple(decisions),
        duplicate_groups=tuple(groups),
        included_canonical_occurrence_ids=included_canonical_ids,
        routes=tuple(routes),
    )
