import hashlib
import itertools
import socket
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from gpt_researcher.screening import (
    CandidateOccurrence,
    DuplicateGroup,
    PaperCandidate,
    PaperType,
    RetrievalRequestRoute,
    ScreeningDecision,
    ScreeningPolicy,
    ScreeningReasonCode,
    ScreeningResult,
    UnknownValuePolicy,
    screen_paper_occurrences,
)


@pytest.fixture(autouse=True)
def _isolate_environment_and_block_network(monkeypatch):
    for name in (
        "ARXIV_API_KEY",
        "SEMANTIC_SCHOLAR_API_KEY",
        "SEMANTIC_SCHOLAR_JOURNALS",
        "OPENAI_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    def unexpected_network(*_args, **_kwargs):
        raise AssertionError("deterministic screening must not access the network")

    monkeypatch.setattr(socket, "create_connection", unexpected_network)


def _candidate(**overrides):
    values = {
        "candidate_id": "semantic_scholar:paper-1",
        "source": "semantic_scholar",
        "source_record_id": "paper-1",
        "retrieval_query": "radar imaging",
        "source_rank": 1,
        "title": "Deterministic Radar Imaging Study",
        "href": "https://example.test/paper-1",
        "body": "Title: Deterministic Radar Imaging Study\n\nAbstract:\nAbstract.",
        "abstract": "A sufficiently useful synthetic abstract.",
        "authors": ("First Author",),
        "published_year": 2024,
        "published_at": None,
        "updated_at": None,
        "venue": "Journal",
        "publication_venue_id": None,
        "publication_venue_name": None,
        "publication_venue_type": None,
        "publication_venue_alternate_names": (),
        "doi": "10.1000/example",
        "external_ids": (),
        "citation_count": None,
        "publication_types": ("JournalArticle",),
        "categories": (),
        "journal_reference": None,
    }
    values.update(overrides)
    return PaperCandidate(**values)


def _occurrence(
    occurrence_id="occ-1",
    retrieval_request_id="request-1",
    planning_only=False,
    **candidate_overrides,
):
    return CandidateOccurrence(
        occurrence_id=occurrence_id,
        retrieval_request_id=retrieval_request_id,
        planning_only=planning_only,
        candidate=_candidate(**candidate_overrides),
    )


def _decision(result, occurrence_id):
    return next(
        decision
        for decision in result.decisions
        if decision.occurrence_id == occurrence_id
    )


def _group(result, occurrence_id):
    return next(
        group
        for group in result.duplicate_groups
        if occurrence_id in group.member_occurrence_ids
    )


def test_new_models_are_strict_frozen_and_forbid_extra_fields():
    occurrence = _occurrence()
    result = screen_paper_occurrences((occurrence,), ScreeningPolicy())

    model_values = (
        occurrence,
        ScreeningPolicy(),
        result.decisions[0],
        result.duplicate_groups[0],
        result.routes[0],
        result,
    )
    for value in model_values:
        assert value.model_config["frozen"] is True
        assert value.model_config["extra"] == "forbid"
        assert value.model_config["strict"] is True
        with pytest.raises(ValidationError):
            value.__setattr__(next(iter(type(value).model_fields)), "changed")

    with pytest.raises(ValidationError):
        CandidateOccurrence(
            occurrence_id="occ",
            retrieval_request_id="request",
            planning_only=False,
            candidate=_candidate(),
            unexpected=True,
        )


def test_occurrence_identifiers_trim_and_reject_blank_values():
    occurrence = _occurrence(
        occurrence_id="  occurrence  ", retrieval_request_id="  request  "
    )
    assert occurrence.occurrence_id == "occurrence"
    assert occurrence.retrieval_request_id == "request"

    for field in ("occurrence_id", "retrieval_request_id"):
        kwargs = {
            "occurrence_id": "occurrence",
            "retrieval_request_id": "request",
            "planning_only": False,
            "candidate": _candidate(),
            field: " \t ",
        }
        with pytest.raises(ValidationError):
            CandidateOccurrence(**kwargs)


@pytest.mark.parametrize("value", [0, 1, "false", "true", None])
def test_planning_only_is_a_strict_boolean(value):
    with pytest.raises(ValidationError):
        _occurrence(planning_only=value)


def test_tuple_fields_and_nested_candidate_are_strict():
    with pytest.raises(ValidationError):
        ScreeningPolicy(allowed_paper_types=[PaperType.JOURNAL])
    with pytest.raises(ValidationError):
        CandidateOccurrence(
            occurrence_id="occurrence",
            retrieval_request_id="request",
            planning_only=False,
            candidate={"candidate_id": "not-coerced"},
        )


def test_policy_validation_is_strict_and_has_no_runtime_switch():
    assert "enabled" not in ScreeningPolicy.model_fields
    assert "screening_enabled" not in ScreeningPolicy.model_fields

    for field in ("min_year", "max_year"):
        for value in (True, False, 2024.0, "2024", 999, 10000):
            with pytest.raises(ValidationError):
                ScreeningPolicy(**{field: value})
    with pytest.raises(ValidationError):
        ScreeningPolicy(min_year=2025, max_year=2024)
    with pytest.raises(ValidationError):
        ScreeningPolicy(
            allowed_paper_types=(PaperType.JOURNAL, PaperType.JOURNAL)
        )
    with pytest.raises(ValidationError):
        ScreeningPolicy(allowed_paper_types=(PaperType.UNKNOWN,))


def test_empty_input_returns_a_fully_empty_immutable_result():
    result = screen_paper_occurrences((), ScreeningPolicy())
    assert result == ScreeningResult(
        occurrences=(),
        decisions=(),
        duplicate_groups=(),
        included_canonical_occurrence_ids=(),
        routes=(),
    )


def test_engine_rejects_non_tuple_duplicate_ids_and_mixed_request_kind_atomically():
    occurrence = _occurrence()
    with pytest.raises(TypeError):
        screen_paper_occurrences([occurrence], ScreeningPolicy())
    with pytest.raises(TypeError):
        screen_paper_occurrences((object(),), ScreeningPolicy())
    with pytest.raises(ValueError, match="occurrence_id"):
        screen_paper_occurrences((occurrence, occurrence), ScreeningPolicy())
    with pytest.raises(ValueError, match="planning_only"):
        screen_paper_occurrences(
            (
                occurrence,
                _occurrence(
                    "occ-2",
                    retrieval_request_id="request-1",
                    planning_only=True,
                ),
            ),
            ScreeningPolicy(),
        )
    with pytest.raises(TypeError):
        screen_paper_occurrences((occurrence,), object())


def test_equal_doi_groups_across_providers_and_uses_deterministic_sha256():
    arxiv = _occurrence(
        "arxiv-occ",
        retrieval_request_id="arxiv-request",
        source="arxiv",
        candidate_id="arxiv:1",
        source_record_id="1",
        doi=" 10.1000/EXAMPLE ",
        publication_types=(),
        venue="arXiv",
    )
    semantic = _occurrence(
        "semantic-occ",
        retrieval_request_id="semantic-request",
        doi="10.1000/example",
    )
    result = screen_paper_occurrences((arxiv, semantic), ScreeningPolicy())

    assert len(result.duplicate_groups) == 1
    group = result.duplicate_groups[0]
    assert group.key_kind == "doi"
    assert group.key_value == "10.1000/example"
    digest = hashlib.sha256(b"doi:10.1000/example").hexdigest()
    assert group.group_id == f"group:sha256:{digest}"
    assert set(group.member_occurrence_ids) == {"arxiv-occ", "semantic-occ"}


def test_differing_or_one_missing_doi_never_title_merge():
    common_title = "Identical Long Deterministic Paper Title"
    occurrences = (
        _occurrence("first", doi="10.1/first", title=common_title),
        _occurrence("second", retrieval_request_id="r2", doi="10.1/second", title=common_title),
        _occurrence("missing", retrieval_request_id="r3", doi=None, title=common_title),
    )
    result = screen_paper_occurrences(occurrences, ScreeningPolicy())
    assert len(result.duplicate_groups) == 3
    assert {group.key_kind for group in result.duplicate_groups} == {"doi", "title"}


def test_title_fallback_is_nfkc_casefold_and_unicode_whitespace_only():
    first = _occurrence(
        "first",
        doi=None,
        title="ＡＢＣＤＥＦ  Radar\tImaging",
    )
    second = _occurrence(
        "second",
        retrieval_request_id="request-2",
        doi=None,
        title="abcdef radar imaging",
    )
    result = screen_paper_occurrences((second, first), ScreeningPolicy())
    assert len(result.duplicate_groups) == 1
    assert result.duplicate_groups[0].key_kind == "title"
    assert result.duplicate_groups[0].key_value == "abcdef radar imaging"


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("Long-Title Alpha 123", "Long Title Alpha 123"),
        ("Étude Radar Longue 123", "Etude Radar Longue 123"),
        ("Radar Study Version 1", "Radar Study Version 2"),
        ("Radar + Imaging 123", "Radar - Imaging 123"),
    ],
)
def test_title_fallback_retains_punctuation_accents_symbols_and_versions(left, right):
    result = screen_paper_occurrences(
        (
            _occurrence("left", doi=None, title=left),
            _occurrence("right", retrieval_request_id="r2", doi=None, title=right),
        ),
        ScreeningPolicy(),
    )
    assert len(result.duplicate_groups) == 2


def test_title_threshold_is_inclusive_and_short_equal_titles_are_singletons():
    twelve = (
        _occurrence("twelve-1", doi=None, title="ABCDEFGHIJKL"),
        _occurrence("twelve-2", retrieval_request_id="r2", doi=None, title="abcdefghijkl"),
    )
    assert len(screen_paper_occurrences(twelve, ScreeningPolicy()).duplicate_groups) == 1

    eleven = (
        _occurrence("eleven-1", doi=None, title="ABCDEFGHIJK"),
        _occurrence("eleven-2", retrieval_request_id="r2", doi=None, title="abcdefghijk"),
    )
    result = screen_paper_occurrences(eleven, ScreeningPolicy())
    assert len(result.duplicate_groups) == 2
    assert all(group.key_kind == "singleton" for group in result.duplicate_groups)


@pytest.mark.parametrize(
    ("year", "policy", "reason"),
    [
        (2020, ScreeningPolicy(min_year=2021), ScreeningReasonCode.YEAR_BELOW_MIN),
        (2025, ScreeningPolicy(max_year=2024), ScreeningReasonCode.YEAR_ABOVE_MAX),
        (None, ScreeningPolicy(unknown_year=UnknownValuePolicy.EXCLUDE), ScreeningReasonCode.YEAR_UNKNOWN),
    ],
)
def test_year_exclusions_have_exact_reason_codes(year, policy, reason):
    result = screen_paper_occurrences((_occurrence(published_year=year),), policy)
    decision = result.decisions[0]
    assert decision.included is False
    assert decision.primary_reason is reason
    assert decision.matched_rules == (reason,)
    assert result.occurrences[0].candidate.published_year == year


def test_year_bounds_are_inclusive_independent_and_unknown_defaults_to_include():
    occurrences = (
        _occurrence("min", retrieval_request_id="r1", doi="10.1/min", published_year=2020),
        _occurrence("max", retrieval_request_id="r2", doi="10.1/max", published_year=2024),
        _occurrence("unknown", retrieval_request_id="r3", doi="10.1/unknown", published_year=None),
    )
    result = screen_paper_occurrences(
        occurrences, ScreeningPolicy(min_year=2020, max_year=2024)
    )
    assert all(decision.included for decision in result.decisions)


OFFICIAL_TYPE_CASES = (
    ("Review", PaperType.REVIEW),
    ("JournalArticle", PaperType.JOURNAL),
    ("Conference", PaperType.CONFERENCE),
    ("BookSection", PaperType.BOOK_CHAPTER),
    ("Book", PaperType.UNKNOWN),
    ("CaseReport", PaperType.UNKNOWN),
    ("ClinicalTrial", PaperType.UNKNOWN),
    ("Dataset", PaperType.UNKNOWN),
    ("Editorial", PaperType.UNKNOWN),
    ("LettersAndComments", PaperType.UNKNOWN),
    ("MetaAnalysis", PaperType.UNKNOWN),
    ("News", PaperType.UNKNOWN),
    ("Study", PaperType.UNKNOWN),
)


@pytest.mark.parametrize(("provider_value", "expected"), OFFICIAL_TYPE_CASES)
def test_all_official_semantic_scholar_publication_types(provider_value, expected):
    result = screen_paper_occurrences(
        (_occurrence(publication_types=(f"  {provider_value.swapcase()}  ",)),),
        ScreeningPolicy(),
    )
    decision = result.decisions[0]
    assert decision.classified_type is expected
    assert decision.type_evidence == (
        f"publication_types:{provider_value.casefold()}",
    )


def test_book_and_book_section_are_distinct():
    book = screen_paper_occurrences(
        (_occurrence(publication_types=("Book",)),), ScreeningPolicy()
    ).decisions[0]
    section = screen_paper_occurrences(
        (_occurrence(publication_types=("BookSection",)),), ScreeningPolicy()
    ).decisions[0]
    assert book.classified_type is PaperType.UNKNOWN
    assert section.classified_type is PaperType.BOOK_CHAPTER


def test_semantic_unknown_and_preprint_tokens_remain_evidence_but_unknown():
    for token in ("SyntheticUnsupported", "preprint"):
        decision = screen_paper_occurrences(
            (_occurrence(publication_types=(token,)),), ScreeningPolicy()
        ).decisions[0]
        assert decision.classified_type is PaperType.UNKNOWN
        assert decision.type_evidence == (f"publication_types:{token.casefold()}",)


def test_arxiv_is_always_preprint_and_ignores_journal_reference_for_classification():
    decision = screen_paper_occurrences(
        (
            _occurrence(
                source="arxiv",
                candidate_id="arxiv:1",
                source_record_id="1",
                publication_types=("JournalArticle",),
                publication_venue_type="journal",
                journal_reference="Published Journal",
                venue="arXiv",
            ),
        ),
        ScreeningPolicy(),
    ).decisions[0]
    assert decision.classified_type is PaperType.PREPRINT
    assert decision.type_evidence == ("source:arxiv",)


def test_review_plus_journal_is_the_only_recognized_multi_type_exception():
    recognized = {
        "Review": PaperType.REVIEW,
        "JournalArticle": PaperType.JOURNAL,
        "Conference": PaperType.CONFERENCE,
        "BookSection": PaperType.BOOK_CHAPTER,
    }
    tokens = tuple(recognized)
    for size in range(2, len(tokens) + 1):
        for combination in itertools.combinations(tokens, size):
            decision = screen_paper_occurrences(
                (_occurrence(publication_types=combination),), ScreeningPolicy()
            ).decisions[0]
            expected = (
                PaperType.REVIEW
                if set(combination) == {"Review", "JournalArticle"}
                else PaperType.UNKNOWN
            )
            assert decision.classified_type is expected


@pytest.mark.parametrize(
    ("venue_type", "expected"),
    [
        (" JOURNAL ", PaperType.JOURNAL),
        ("Conference", PaperType.CONFERENCE),
        ("bookchapter", PaperType.UNKNOWN),
        ("preprint", PaperType.UNKNOWN),
        ("synthetic", PaperType.UNKNOWN),
    ],
)
def test_venue_fallback_is_limited_and_preserves_evidence(venue_type, expected):
    decision = screen_paper_occurrences(
        (
            _occurrence(
                publication_types=(), publication_venue_type=venue_type
            ),
        ),
        ScreeningPolicy(),
    ).decisions[0]
    assert decision.classified_type is expected
    assert decision.type_evidence == (
        f"publication_venue_type:{venue_type.strip().casefold()}",
    )


def test_unmapped_publication_type_suppresses_venue_fallback():
    decision = screen_paper_occurrences(
        (
            _occurrence(
                publication_types=("Book",),
                publication_venue_type="journal",
            ),
        ),
        ScreeningPolicy(),
    ).decisions[0]
    assert decision.classified_type is PaperType.UNKNOWN
    assert decision.type_evidence == (
        "publication_types:book",
        "publication_venue_type:journal",
    )


def test_type_evidence_preserves_field_provider_order_and_first_duplicate():
    decision = screen_paper_occurrences(
        (
            _occurrence(
                publication_types=(
                    " JournalArticle ",
                    "Review",
                    "journalarticle",
                    " Unknown ",
                ),
                publication_venue_type=" CONFERENCE ",
            ),
        ),
        ScreeningPolicy(),
    ).decisions[0]
    assert decision.type_evidence == (
        "publication_types:journalarticle",
        "publication_types:review",
        "publication_types:unknown",
        "publication_venue_type:conference",
    )


def test_no_type_evidence_uses_exact_none_tuple():
    decision = screen_paper_occurrences(
        (_occurrence(publication_types=(), publication_venue_type=None),),
        ScreeningPolicy(),
    ).decisions[0]
    assert decision.classified_type is PaperType.UNKNOWN
    assert decision.type_evidence == ("none",)


def test_type_policy_none_empty_populated_and_unknown_are_distinct():
    journal = _occurrence(publication_types=("JournalArticle",))
    assert screen_paper_occurrences(
        (journal,), ScreeningPolicy(allowed_paper_types=None)
    ).decisions[0].included

    empty = screen_paper_occurrences(
        (journal,), ScreeningPolicy(allowed_paper_types=())
    ).decisions[0]
    assert empty.primary_reason is ScreeningReasonCode.TYPE_NOT_ALLOWED

    allowed = screen_paper_occurrences(
        (journal,), ScreeningPolicy(allowed_paper_types=(PaperType.JOURNAL,))
    ).decisions[0]
    assert allowed.included

    unknown = _occurrence(publication_types=("Book",))
    excluded = screen_paper_occurrences(
        (unknown,),
        ScreeningPolicy(unknown_paper_type=UnknownValuePolicy.EXCLUDE),
    ).decisions[0]
    assert excluded.primary_reason is ScreeningReasonCode.TYPE_UNKNOWN


def test_multiple_rule_hits_have_fixed_order_and_primary_reason():
    first = _occurrence(
        "first",
        published_year=2010,
        publication_types=("Conference",),
    )
    second = _occurrence(
        "second",
        retrieval_request_id="request-2",
        published_year=2024,
        publication_types=("JournalArticle",),
    )
    result = screen_paper_occurrences(
        (first, second),
        ScreeningPolicy(
            min_year=2020,
            allowed_paper_types=(PaperType.JOURNAL,),
        ),
    )
    decision = _decision(result, "first")
    assert decision.included is False
    assert decision.primary_reason is ScreeningReasonCode.YEAR_BELOW_MIN
    assert decision.matched_rules == (
        ScreeningReasonCode.YEAR_BELOW_MIN,
        ScreeningReasonCode.TYPE_NOT_ALLOWED,
        ScreeningReasonCode.DUPLICATE_OF_CANONICAL,
    )


def test_all_excluded_group_has_reference_but_no_canonical_or_duplicate_reason():
    occurrences = (
        _occurrence("first", published_year=2010),
        _occurrence("second", retrieval_request_id="r2", published_year=2011),
    )
    result = screen_paper_occurrences(
        occurrences, ScreeningPolicy(min_year=2020)
    )
    group = result.duplicate_groups[0]
    assert group.reference_occurrence_id in {"first", "second"}
    assert group.canonical_occurrence_id is None
    assert result.included_canonical_occurrence_ids == ()
    assert all(
        ScreeningReasonCode.DUPLICATE_OF_CANONICAL not in decision.matched_rules
        for decision in result.decisions
    )
    assert all(route.canonical_occurrence_ids == () for route in result.routes)


def _canonical_of(first_overrides, second_overrides):
    common = {"doi": "10.1/canonical", "title": "Canonical Selection Long Title"}
    first = _occurrence("first", **(common | first_overrides))
    second = _occurrence(
        "second", retrieval_request_id="r2", **(common | second_overrides)
    )
    result = screen_paper_occurrences((second, first), ScreeningPolicy())
    return result.duplicate_groups[0].canonical_occurrence_id


@pytest.mark.parametrize(
    ("preferred", "other"),
    [
        ({"publication_types": ("Review",)}, {"publication_types": ("JournalArticle",)}),
        ({"publication_venue_id": "venue"}, {"publication_venue_id": None}),
        ({"citation_count": 0}, {"citation_count": None}),
        ({"citation_count": 10}, {"citation_count": 9}),
        ({"published_year": 2020}, {"published_year": None}),
        ({"published_year": 2024}, {"published_year": 2023}),
        ({"abstract": "Longer abstract text"}, {"abstract": "Short"}),
        ({"source": "semantic_scholar"}, {"source": "arxiv", "publication_types": (), "venue": "arXiv"}),
        ({"source_rank": 1}, {"source_rank": 2}),
        ({"candidate_id": "semantic_scholar:a"}, {"candidate_id": "semantic_scholar:b"}),
        ({"body": "A"}, {"body": "B"}),
    ],
)
def test_canonical_selection_tie_breakers(preferred, other):
    assert _canonical_of(preferred, other) == "first"


def test_occurrence_id_is_the_final_canonical_tie_breaker():
    candidate = _candidate(doi="10.1/tie")
    later = CandidateOccurrence(
        occurrence_id="z", retrieval_request_id="r2", planning_only=False, candidate=candidate
    )
    earlier = CandidateOccurrence(
        occurrence_id="a", retrieval_request_id="r1", planning_only=False, candidate=candidate
    )
    result = screen_paper_occurrences((later, earlier), ScreeningPolicy())
    assert result.duplicate_groups[0].canonical_occurrence_id == "a"


def test_planning_occurrences_screen_but_planning_routes_are_empty():
    planning = _occurrence(
        "planning",
        retrieval_request_id="planning-request",
        planning_only=True,
    )
    result = screen_paper_occurrences((planning,), ScreeningPolicy())
    assert result.decisions[0].included is True
    assert result.duplicate_groups[0].canonical_occurrence_id == "planning"
    assert result.routes == (
        RetrievalRequestRoute(
            retrieval_request_id="planning-request",
            canonical_occurrence_ids=(),
        ),
    )


def test_normal_route_requires_its_own_group_member_and_can_target_planning_canonical():
    planning = _occurrence(
        "planning",
        retrieval_request_id="planning-request",
        planning_only=True,
        publication_types=("Review",),
    )
    normal_duplicate = _occurrence(
        "normal-duplicate",
        retrieval_request_id="normal-request",
        publication_types=("JournalArticle",),
    )
    unrelated = _occurrence(
        "unrelated",
        retrieval_request_id="unrelated-request",
        doi="10.1/unrelated",
    )
    result = screen_paper_occurrences(
        (unrelated, normal_duplicate, planning), ScreeningPolicy()
    )
    routes = {route.retrieval_request_id: route for route in result.routes}
    assert routes["planning-request"].canonical_occurrence_ids == ()
    assert routes["normal-request"].canonical_occurrence_ids == ("planning",)
    assert routes["unrelated-request"].canonical_occurrence_ids == ("unrelated",)


def test_planning_only_discovery_does_not_inject_group_into_normal_route():
    planning = _occurrence(
        "planning",
        retrieval_request_id="planning-request",
        planning_only=True,
    )
    normal = _occurrence(
        "normal",
        retrieval_request_id="normal-request",
        doi="10.1/other",
    )
    result = screen_paper_occurrences((planning, normal), ScreeningPolicy())
    routes = {route.retrieval_request_id: route for route in result.routes}
    assert routes["normal-request"].canonical_occurrence_ids == ("normal",)
    assert "planning" not in routes["normal-request"].canonical_occurrence_ids


def test_route_deduplicates_same_canonical_and_preserves_all_occurrence_evidence():
    occurrences = (
        _occurrence("one", retrieval_request_id="request"),
        _occurrence("two", retrieval_request_id="request"),
        _occurrence("three", retrieval_request_id="request"),
    )
    result = screen_paper_occurrences(occurrences, ScreeningPolicy())
    assert len(result.occurrences) == len(result.decisions) == 3
    assert len(result.duplicate_groups) == 1
    assert len(result.duplicate_groups[0].member_occurrence_ids) == 3
    assert len(result.routes[0].canonical_occurrence_ids) == 1


def test_result_order_and_values_are_independent_of_input_permutation():
    occurrences = (
        _occurrence("c", retrieval_request_id="r3", doi="10.1/c"),
        _occurrence("a", retrieval_request_id="r1", doi="10.1/a"),
        _occurrence("b", retrieval_request_id="r2", doi="10.1/b"),
    )
    expected = screen_paper_occurrences(occurrences, ScreeningPolicy())
    for permutation in itertools.permutations(occurrences):
        assert screen_paper_occurrences(permutation, ScreeningPolicy()) == expected


def test_result_cross_references_and_decision_order_are_complete():
    result = screen_paper_occurrences(
        (
            _occurrence("two", retrieval_request_id="r2", doi="10.1/shared"),
            _occurrence("one", retrieval_request_id="r1", doi="10.1/shared"),
            _occurrence("three", retrieval_request_id="r3", doi="10.1/three"),
        ),
        ScreeningPolicy(),
    )
    occurrence_ids = tuple(item.occurrence_id for item in result.occurrences)
    assert tuple(item.occurrence_id for item in result.decisions) == occurrence_ids
    assert tuple(item.decision_order for item in result.decisions) == (1, 2, 3)
    grouped_ids = tuple(
        occurrence_id
        for group in result.duplicate_groups
        for occurrence_id in group.member_occurrence_ids
    )
    assert set(grouped_ids) == set(occurrence_ids)
    assert len(grouped_ids) == len(set(grouped_ids))
    included = tuple(
        decision.occurrence_id for decision in result.decisions if decision.included
    )
    assert set(included) == set(result.included_canonical_occurrence_ids)


def test_projection_contract_is_unchanged_and_allocates_exact_three_keys():
    candidate = _candidate()
    first = candidate.to_retriever_result()
    second = candidate.to_retriever_result()
    assert first == {
        "title": candidate.title,
        "href": candidate.href,
        "body": candidate.body,
    }
    assert set(first) == {"title", "href", "body"}
    assert first is not second


def test_engine_models_reject_list_collections_directly():
    occurrence = _occurrence()
    result = screen_paper_occurrences((occurrence,), ScreeningPolicy())
    decision = result.decisions[0]
    with pytest.raises(ValidationError):
        ScreeningDecision(**(decision.model_dump() | {"matched_rules": []}))
    with pytest.raises(ValidationError):
        DuplicateGroup(**(result.duplicate_groups[0].model_dump() | {"member_occurrence_ids": []}))
    with pytest.raises(ValidationError):
        RetrievalRequestRoute(
            retrieval_request_id="request", canonical_occurrence_ids=[]
        )


def test_duplicate_group_member_ids_strip_each_tuple_element():
    result = screen_paper_occurrences((_occurrence(),), ScreeningPolicy())
    group = result.duplicate_groups[0]
    normalized = DuplicateGroup(
        **(
            group.model_dump()
            | {
                "member_occurrence_ids": ("  occ-1  ",),
                "reference_occurrence_id": "occ-1",
                "canonical_occurrence_id": "occ-1",
            }
        )
    )
    assert normalized.member_occurrence_ids == ("occ-1",)


@pytest.mark.parametrize(
    "member_ids",
    [
        ("occ-1", "  occ-1  "),
        (" \t ",),
    ],
)
def test_duplicate_group_member_ids_reject_normalized_duplicates_and_blanks(
    member_ids,
):
    result = screen_paper_occurrences((_occurrence(),), ScreeningPolicy())
    group = result.duplicate_groups[0]
    with pytest.raises(ValidationError):
        DuplicateGroup(
            **(
                group.model_dump()
                | {
                    "member_occurrence_ids": member_ids,
                    "reference_occurrence_id": "occ-1",
                    "canonical_occurrence_id": "occ-1",
                }
            )
        )


def test_route_canonical_ids_strip_each_tuple_element():
    route = RetrievalRequestRoute(
        retrieval_request_id="request",
        canonical_occurrence_ids=("  canonical  ",),
    )
    assert route.canonical_occurrence_ids == ("canonical",)


@pytest.mark.parametrize(
    "canonical_ids",
    [
        ("canonical", " canonical "),
        (" \n ",),
    ],
)
def test_route_canonical_ids_reject_normalized_duplicates_and_blanks(canonical_ids):
    with pytest.raises(ValidationError):
        RetrievalRequestRoute(
            retrieval_request_id="request",
            canonical_occurrence_ids=canonical_ids,
        )


@pytest.mark.parametrize(
    "field",
    ["canonical_occurrence_id", "canonical_candidate_id"],
)
def test_optional_decision_canonical_ids_strip_non_none_values(field):
    decision = screen_paper_occurrences(
        (_occurrence(),), ScreeningPolicy()
    ).decisions[0]
    raw_value = getattr(decision, field)
    normalized = ScreeningDecision(
        **(decision.model_dump() | {field: f"  {raw_value}  "})
    )
    assert getattr(normalized, field) == raw_value


@pytest.mark.parametrize(
    "field",
    ["canonical_occurrence_id", "canonical_candidate_id"],
)
def test_optional_decision_canonical_ids_reject_blank_non_none_values(field):
    decision = screen_paper_occurrences(
        (_occurrence(),), ScreeningPolicy()
    ).decisions[0]
    with pytest.raises(ValidationError):
        ScreeningDecision(**(decision.model_dump() | {field: " \t "}))


@pytest.mark.parametrize(
    ("field", "tampered_value"),
    [
        ("retrieval_request_id", "tampered-request"),
        ("candidate_id", "semantic_scholar:tampered"),
        ("source", "arxiv"),
        ("retrieval_query", "tampered query"),
        ("source_rank", 99),
    ],
)
def test_screening_result_rejects_decision_occurrence_field_mismatch(
    field, tampered_value
):
    result = screen_paper_occurrences(
        (
            _occurrence("first", retrieval_request_id="request-1"),
            _occurrence("second", retrieval_request_id="request-2"),
        ),
        ScreeningPolicy(),
    )
    target = _decision(result, "second")
    tampered = target.model_copy(update={field: tampered_value})
    decisions = tuple(
        tampered if decision.occurrence_id == "second" else decision
        for decision in result.decisions
    )

    with pytest.raises(ValidationError):
        ScreeningResult(
            occurrences=result.occurrences,
            decisions=decisions,
            duplicate_groups=result.duplicate_groups,
            included_canonical_occurrence_ids=(
                result.included_canonical_occurrence_ids
            ),
            routes=result.routes,
        )
