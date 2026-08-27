import hashlib
import logging
import os
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import requests
from pydantic import ValidationError

import gpt_researcher.retrievers.arxiv.arxiv as arxiv_module
import gpt_researcher.retrievers.semantic_scholar.semantic_scholar as semantic_module
from gpt_researcher.retrievers.academic_utils import format_academic_body
from gpt_researcher.retrievers.arxiv.arxiv import ARXIV_NUM_RETRIES, ArxivSearch
from gpt_researcher.retrievers.semantic_scholar.semantic_scholar import (
    SemanticScholarSearch,
)
from gpt_researcher.screening.models import (
    ExternalIdentifier,
    PaperCandidate,
    build_candidate_id,
)


API_KEY_ENV = "SEMANTIC_SCHOLAR_API_KEY"
JOURNALS_ENV = "SEMANTIC_SCHOLAR_JOURNALS"
SYNTHETIC_API_KEY = "synthetic-candidate-test-key"


@pytest.fixture(autouse=True)
def _isolate_environment_and_block_real_network(monkeypatch):
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    monkeypatch.delenv(JOURNALS_ENV, raising=False)

    def unexpected_network(*args, **kwargs):
        raise AssertionError("unexpected real provider access")

    monkeypatch.setattr(semantic_module.requests, "get", unexpected_network)
    monkeypatch.setattr(arxiv_module.arxiv, "Client", unexpected_network)


def _candidate(**overrides):
    values = {
        "candidate_id": "arxiv:2401.00001",
        "source": "arxiv",
        "source_record_id": "2401.00001",
        "retrieval_query": "synthetic query",
        "source_rank": 1,
        "title": "Synthetic Paper",
        "href": "https://arxiv.org/abs/2401.00001",
        "body": "Title: Synthetic Paper\n\nAbstract:\nSynthetic abstract.",
        "abstract": "Synthetic abstract.",
        "authors": ("First Author",),
        "published_year": 2024,
        "published_at": None,
        "updated_at": None,
        "venue": "arXiv",
        "publication_venue_id": None,
        "publication_venue_name": None,
        "publication_venue_type": None,
        "publication_venue_alternate_names": (),
        "doi": "10.1000/synthetic",
        "external_ids": (
            ExternalIdentifier(name="arXiv", value="2401.00001"),
        ),
        "citation_count": None,
        "publication_types": (),
        "categories": ("eess.IV",),
        "journal_reference": None,
    }
    values.update(overrides)
    return PaperCandidate(**values)


def _response(payload=None, status_code=200):
    response = MagicMock()
    response.status_code = status_code
    response.raise_for_status = MagicMock()
    response.json.return_value = {"data": []} if payload is None else payload
    return response


def _semantic_paper(**overrides):
    values = {
        "paperId": "  semantic-paper-id  ",
        "title": "  A Semantic Paper  ",
        "abstract": "  Abstract with\nsubstantive text.  ",
        "url": " https://www.semanticscholar.org/paper/example ",
        "authors": [
            {"name": " First Author "},
            {"name": "Second Author"},
            {"name": "First Author"},
            {"name": " "},
        ],
        "year": 2024,
        "venue": " Journal of Tests ",
        "publicationVenue": {
            "id": " venue-id ",
            "name": " Structured Journal ",
            "type": " journal ",
            "alternate_names": ["Journal Alias", " Journal Alias ", "Alt 2"],
        },
        "citationCount": 17,
        "publicationTypes": ["JournalArticle", " Review ", "JournalArticle"],
        "externalIds": {
            "CorpusId": " 12345 ",
            "DOI": " DOI:10.5555/Example ",
            "ArXiv": " 2401.00001 ",
            "Unusable": 42,
        },
    }
    values.update(overrides)
    return values


def _arxiv_paper(**overrides):
    values = {
        "title": "  An arXiv Paper  ",
        "entry_id": " https://arxiv.org/abs/2401.00001v2 ",
        "pdf_url": "https://arxiv.org/pdf/2401.00001v2",
        "summary": "  arXiv abstract with\nsubstantive text.  ",
        "authors": [
            SimpleNamespace(name=" First Author "),
            SimpleNamespace(name="Second Author"),
            SimpleNamespace(name="First Author"),
        ],
        "published": datetime(2024, 1, 2, tzinfo=timezone.utc),
        "updated": datetime(2024, 2, 3, tzinfo=timezone.utc),
        "doi": " HTTPS://DOI.ORG/10.1000/Example ",
        "journal_ref": " Journal Reference 1 ",
        "primary_category": " eess.IV ",
        "categories": ["cs.AI", "eess.IV", "cs.AI"],
        "get_short_id": lambda: " 2401.00001v2 ",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_models_are_frozen_strict_and_forbid_extra_fields():
    candidate = _candidate()

    with pytest.raises(ValidationError):
        candidate.title = "Changed"
    with pytest.raises(ValidationError):
        _candidate(unexpected="forbidden")
    with pytest.raises(ValidationError):
        ExternalIdentifier(name="DOI", value="10.1/x", unexpected=True)

    assert candidate.model_config["frozen"] is True
    assert candidate.model_config["extra"] == "forbid"
    assert candidate.model_config["strict"] is True


@pytest.mark.parametrize("field", ["source_rank", "published_year", "citation_count"])
@pytest.mark.parametrize("value", [True, False, "2024"])
def test_integer_fields_do_not_coerce_bool_or_numeric_strings(field, value):
    with pytest.raises(ValidationError):
        _candidate(**{field: value})


@pytest.mark.parametrize("rank", [0, -1])
def test_source_rank_must_be_positive(rank):
    with pytest.raises(ValidationError):
        _candidate(source_rank=rank)


@pytest.mark.parametrize("year", [1000, 2024, 9999])
def test_model_accepts_frozen_year_boundaries(year):
    assert _candidate(published_year=year).published_year == year


@pytest.mark.parametrize("year", [-1, 0, 999, 10000, 2024.0])
def test_model_rejects_invalid_non_none_years(year):
    with pytest.raises(ValidationError):
        _candidate(published_year=year)


def test_model_rejects_negative_citation_count():
    with pytest.raises(ValidationError):
        _candidate(citation_count=-1)


@pytest.mark.parametrize(
    "field",
    [
        "authors",
        "publication_venue_alternate_names",
        "external_ids",
        "publication_types",
        "categories",
    ],
)
def test_tuple_fields_do_not_coerce_lists(field):
    with pytest.raises(ValidationError):
        _candidate(**{field: []})


@pytest.mark.parametrize("field", ["candidate_id", "title", "href", "abstract"])
@pytest.mark.parametrize("value", ["", "  \t\n  "])
def test_required_candidate_strings_reject_blank_values(field, value):
    with pytest.raises(ValidationError):
        _candidate(**{field: value})


def test_external_identifier_trims_and_rejects_blank_values():
    identifier = ExternalIdentifier(name=" DOI ", value=" 10.1000/example ")

    assert identifier.name == "DOI"
    assert identifier.value == "10.1000/example"
    for values in (
        {"name": " ", "value": "usable"},
        {"name": "usable", "value": "\t"},
    ):
        with pytest.raises(ValidationError):
            ExternalIdentifier(**values)


def test_missing_optional_fields_use_none_and_immutable_empty_tuples():
    candidate = _candidate(
        source_record_id=None,
        authors=(),
        published_year=None,
        venue=None,
        publication_venue_alternate_names=(),
        doi=None,
        external_ids=(),
        publication_types=(),
        categories=(),
    )

    assert candidate.source_record_id is None
    assert candidate.published_year is None
    assert candidate.venue is None
    assert candidate.authors == ()
    assert candidate.external_ids == ()
    assert isinstance(candidate.authors, tuple)
    assert isinstance(candidate.external_ids, tuple)


def test_candidate_id_uses_frozen_three_level_precedence():
    href = "https://papers.example/record"
    assert build_candidate_id(
        source="arxiv",
        source_record_id=" 2401.00001 ",
        doi="10.1000/example",
        href=href,
    ) == "arxiv:2401.00001"
    assert build_candidate_id(
        source="semantic_scholar",
        source_record_id=None,
        doi="10.1000/example",
        href=href,
    ) == "semantic_scholar:doi:10.1000/example"

    digest = hashlib.sha256(href.encode("utf-8")).hexdigest()
    expected = f"semantic_scholar:href-sha256:{digest}"
    first = build_candidate_id(
        source="semantic_scholar",
        source_record_id=None,
        doi=None,
        href=href,
    )
    second = build_candidate_id(
        source="semantic_scholar",
        source_record_id=None,
        doi=None,
        href=href,
    )
    assert first == second == expected


def test_retriever_projection_is_exact_and_returns_a_new_dictionary():
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


def test_semantic_scholar_complete_candidate_mapping_and_requested_fields():
    response = _response({"data": [_semantic_paper()]})
    with patch.object(semantic_module.requests, "get", return_value=response) as get:
        candidates = SemanticScholarSearch("graph learning").search_candidates(
            max_results=4
        )

    get.assert_called_once()
    _, kwargs = get.call_args
    assert kwargs["params"]["fields"] == (
        "paperId,title,abstract,url,authors,year,venue,publicationVenue,"
        "citationCount,publicationTypes,externalIds"
    )
    candidate = candidates[0]
    assert candidate.candidate_id == "semantic_scholar:semantic-paper-id"
    assert candidate.source == "semantic_scholar"
    assert candidate.source_record_id == "semantic-paper-id"
    assert candidate.retrieval_query == "graph learning"
    assert candidate.source_rank == 1
    assert candidate.title == "A Semantic Paper"
    assert candidate.href == "https://www.semanticscholar.org/paper/example"
    assert candidate.abstract == "Abstract with\nsubstantive text."
    assert candidate.authors == ("First Author", "Second Author")
    assert candidate.published_year == 2024
    assert candidate.published_at is None
    assert candidate.updated_at is None
    assert candidate.venue == "Journal of Tests"
    assert candidate.publication_venue_id == "venue-id"
    assert candidate.publication_venue_name == "Structured Journal"
    assert candidate.publication_venue_type == "journal"
    assert candidate.publication_venue_alternate_names == (
        "Journal Alias",
        "Alt 2",
    )
    assert candidate.doi == "10.5555/example"
    assert candidate.external_ids == (
        ExternalIdentifier(name="CorpusId", value="12345"),
        ExternalIdentifier(name="DOI", value="10.5555/example"),
        ExternalIdentifier(name="ArXiv", value="2401.00001"),
    )
    assert candidate.citation_count == 17
    assert candidate.publication_types == ("JournalArticle", "Review")
    assert candidate.categories == ()
    assert candidate.journal_reference is None
    assert candidate.body == format_academic_body(
        title="A Semantic Paper",
        authors=("First Author", "Second Author"),
        year=2024,
        venue="Journal of Tests",
        doi="10.5555/example",
        source="Semantic Scholar",
        abstract="Abstract with\nsubstantive text.",
    )


def test_semantic_scholar_missing_optional_fields_are_explicit():
    paper = _semantic_paper(
        paperId=None,
        authors=None,
        year=None,
        venue=None,
        publicationVenue=None,
        citationCount=None,
        publicationTypes=None,
        externalIds=None,
    )
    response = _response({"data": [paper]})
    with patch.object(semantic_module.requests, "get", return_value=response):
        candidate = SemanticScholarSearch("query").search_candidates()[0]

    assert candidate.source_record_id is None
    assert candidate.candidate_id.startswith("semantic_scholar:href-sha256:")
    assert candidate.authors == ()
    assert candidate.published_year is None
    assert candidate.venue is None
    assert candidate.publication_venue_id is None
    assert candidate.publication_venue_name is None
    assert candidate.publication_venue_type is None
    assert candidate.publication_venue_alternate_names == ()
    assert candidate.doi is None
    assert candidate.external_ids == ()
    assert candidate.citation_count is None
    assert candidate.publication_types == ()
    assert "Year: N/A" in candidate.body


@pytest.mark.parametrize("raw_year", ["2024", " 2024 "])
def test_semantic_scholar_four_digit_string_year_preserves_body(raw_year):
    response = _response({"data": [_semantic_paper(year=raw_year)]})
    with patch.object(semantic_module.requests, "get", return_value=response):
        candidate = SemanticScholarSearch("query").search_candidates()[0]

    assert candidate.published_year == 2024
    assert "Year: 2024" in candidate.body
    assert candidate.body == format_academic_body(
        title=candidate.title,
        authors=candidate.authors,
        year=2024,
        venue=candidate.venue,
        doi=candidate.doi,
        source="Semantic Scholar",
        abstract=candidate.abstract,
    )


@pytest.mark.parametrize(
    "raw_year",
    [-1, 0, 999, 10000, True, False, 2024.0, "0999", "10000", "not-a-year"],
)
def test_semantic_scholar_invalid_years_map_to_none_and_na(raw_year):
    response = _response({"data": [_semantic_paper(year=raw_year)]})
    with patch.object(semantic_module.requests, "get", return_value=response):
        candidate = SemanticScholarSearch("query").search_candidates()[0]

    assert candidate.published_year is None
    assert "Year: N/A" in candidate.body


def test_semantic_scholar_negative_citation_count_maps_to_none():
    response = _response({"data": [_semantic_paper(citationCount=-5)]})
    with patch.object(semantic_module.requests, "get", return_value=response):
        candidate = SemanticScholarSearch("query").search_candidates()[0]

    assert candidate.citation_count is None


def test_semantic_scholar_source_rank_preserves_gaps_after_bad_records():
    response = _response(
        {
            "data": [
                _semantic_paper(title="First"),
                None,
                _semantic_paper(title="Third", paperId="third-id"),
            ]
        }
    )
    with patch.object(semantic_module.requests, "get", return_value=response):
        candidates = SemanticScholarSearch("query").search_candidates()

    assert [candidate.title for candidate in candidates] == ["First", "Third"]
    assert [candidate.source_rank for candidate in candidates] == [1, 3]


def test_semantic_search_calls_candidates_once_and_returns_exact_projection():
    retriever = SemanticScholarSearch("query")
    candidate = _candidate(
        source="semantic_scholar",
        candidate_id="semantic_scholar:paper-id",
        source_record_id="paper-id",
    )
    with patch.object(
        retriever, "search_candidates", return_value=[candidate]
    ) as search_candidates:
        result = retriever.search(max_results=7)

    search_candidates.assert_called_once_with(max_results=7)
    assert result == [candidate.to_retriever_result()]
    assert set(result[0]) == {"title", "href", "body"}


def test_semantic_key_venue_bulk_timeout_and_single_request_are_preserved(
    monkeypatch,
):
    monkeypatch.setenv(API_KEY_ENV, f"  {SYNTHETIC_API_KEY}\t")
    monkeypatch.setenv(JOURNALS_ENV, "tgars")
    response = _response({"data": []})
    with patch.object(semantic_module.requests, "get", return_value=response) as get:
        assert SemanticScholarSearch(
            "query", sort="citationCount"
        ).search_candidates(max_results=2) == []

    get.assert_called_once()
    args, kwargs = get.call_args
    assert args[0] == SemanticScholarSearch.BULK_URL
    assert kwargs["params"]["sort"] == "citationCount:desc"
    assert kwargs["params"]["venue"] == (
        "IEEE Transactions on Geoscience and Remote Sensing"
    )
    assert kwargs["timeout"] == 10
    assert kwargs["headers"] == {"x-api-key": SYNTHETIC_API_KEY}
    assert SYNTHETIC_API_KEY not in args[0]
    assert SYNTHETIC_API_KEY not in repr(kwargs["params"])


def test_semantic_429_returns_empty_without_retry_or_secret_logging(
    monkeypatch, caplog
):
    monkeypatch.setenv(API_KEY_ENV, SYNTHETIC_API_KEY)
    response = _response(status_code=429)
    response.raise_for_status.side_effect = requests.HTTPError(
        f"unsafe {SYNTHETIC_API_KEY}", response=response
    )
    with patch.object(semantic_module.requests, "get", return_value=response) as get:
        with caplog.at_level(logging.ERROR):
            assert SemanticScholarSearch("query").search_candidates() == []

    get.assert_called_once()
    assert "429" in caplog.text
    assert SYNTHETIC_API_KEY not in caplog.text


def test_arxiv_complete_candidate_mapping_and_bounded_retries():
    paper = _arxiv_paper()
    client = MagicMock()
    client.results.return_value = iter([paper])
    with patch.object(
        arxiv_module.arxiv, "Client", return_value=client
    ) as client_class, patch.object(arxiv_module.arxiv, "Search") as search_class:
        candidate = ArxivSearch("radar", sort="SubmittedDate").search_candidates(
            max_results=7
        )[0]

    client_class.assert_called_once_with(num_retries=ARXIV_NUM_RETRIES)
    search_class.assert_called_once()
    assert candidate.candidate_id == "arxiv:2401.00001v2"
    assert candidate.source == "arxiv"
    assert candidate.source_record_id == "2401.00001v2"
    assert candidate.retrieval_query == "radar"
    assert candidate.source_rank == 1
    assert candidate.title == "An arXiv Paper"
    assert candidate.href == "https://arxiv.org/abs/2401.00001v2"
    assert candidate.href != paper.pdf_url
    assert candidate.abstract == "arXiv abstract with\nsubstantive text."
    assert candidate.authors == ("First Author", "Second Author")
    assert candidate.published_year == 2024
    assert candidate.published_at == paper.published
    assert candidate.updated_at == paper.updated
    assert candidate.venue == "arXiv"
    assert candidate.publication_venue_id is None
    assert candidate.publication_venue_name is None
    assert candidate.publication_venue_type is None
    assert candidate.publication_venue_alternate_names == ()
    assert candidate.doi == "10.1000/example"
    assert candidate.external_ids == (
        ExternalIdentifier(name="arXiv", value="2401.00001v2"),
        ExternalIdentifier(name="DOI", value="10.1000/example"),
    )
    assert candidate.citation_count is None
    assert candidate.publication_types == ()
    assert candidate.categories == ("eess.IV", "cs.AI")
    assert candidate.journal_reference == "Journal Reference 1"
    assert candidate.body == format_academic_body(
        title="An arXiv Paper",
        authors=("First Author", "Second Author"),
        year=2024,
        venue="arXiv",
        doi="10.1000/example",
        source="arXiv",
        abstract="arXiv abstract with\nsubstantive text.",
    )


def test_arxiv_missing_optional_fields_are_explicit():
    paper = _arxiv_paper(
        authors=None,
        published=None,
        updated=None,
        doi=None,
        journal_ref=None,
        primary_category=None,
        categories=None,
        get_short_id=None,
    )
    client = MagicMock()
    client.results.return_value = iter([paper])
    with patch.object(arxiv_module.arxiv, "Client", return_value=client):
        candidate = ArxivSearch("query").search_candidates()[0]

    assert candidate.source_record_id is None
    assert candidate.candidate_id.startswith("arxiv:href-sha256:")
    assert candidate.authors == ()
    assert candidate.published_year is None
    assert candidate.published_at is None
    assert candidate.updated_at is None
    assert candidate.doi is None
    assert candidate.external_ids == ()
    assert candidate.categories == ()
    assert candidate.journal_reference is None
    assert "Year: N/A" in candidate.body


def test_arxiv_short_id_failure_keeps_candidate_and_uses_href_identity(caplog):
    href = "https://arxiv.org/abs/2401.00001v2"

    def failing_short_id():
        raise RuntimeError("sensitive short-id provider detail")

    paper = _arxiv_paper(doi=None, get_short_id=failing_short_id)
    client = MagicMock()
    client.results.side_effect = [iter([paper]), iter([paper])]

    with patch.object(arxiv_module.arxiv, "Client", return_value=client):
        with caplog.at_level(logging.WARNING):
            retriever = ArxivSearch("query")
            candidates = retriever.search_candidates()
            projected = retriever.search()

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.source_record_id is None
    assert candidate.candidate_id.startswith("arxiv:href-sha256:")
    assert candidate.candidate_id == (
        "arxiv:href-sha256:"
        f"{hashlib.sha256(href.encode('utf-8')).hexdigest()}"
    )
    assert projected == [candidate.to_retriever_result()]
    assert set(projected[0]) == {"title", "href", "body"}
    assert "sensitive short-id provider detail" not in caplog.text
    assert paper.summary not in caplog.text


@pytest.mark.parametrize("year", [1000, 2024, 9999])
def test_arxiv_accepts_frozen_year_boundaries(year):
    paper = _arxiv_paper(published=SimpleNamespace(year=year))
    client = MagicMock()
    client.results.return_value = iter([paper])
    with patch.object(arxiv_module.arxiv, "Client", return_value=client):
        candidate = ArxivSearch("query").search_candidates()[0]

    assert candidate.published_year == year
    assert f"Year: {year}" in candidate.body


@pytest.mark.parametrize("year", [-1, 0, 999, 10000, True, False, 2024.0])
def test_arxiv_invalid_years_map_to_none_and_na(year):
    paper = _arxiv_paper(published=SimpleNamespace(year=year))
    client = MagicMock()
    client.results.return_value = iter([paper])
    with patch.object(arxiv_module.arxiv, "Client", return_value=client):
        candidate = ArxivSearch("query").search_candidates()[0]

    assert candidate.published_year is None
    assert "Year: N/A" in candidate.body


def test_arxiv_source_rank_preserves_gaps_and_valid_results():
    class MalformedResult:
        @property
        def title(self):
            raise ValueError("malformed title")

    client = MagicMock()
    client.results.return_value = iter(
        [
            _arxiv_paper(title="First"),
            MalformedResult(),
            _arxiv_paper(title="Third", get_short_id=lambda: "third-id"),
        ]
    )
    with patch.object(arxiv_module.arxiv, "Client", return_value=client):
        candidates = ArxivSearch("query").search_candidates()

    assert [candidate.title for candidate in candidates] == ["First", "Third"]
    assert [candidate.source_rank for candidate in candidates] == [1, 3]


def test_arxiv_generator_failure_returns_partial_candidates(caplog):
    client = MagicMock()

    def results():
        yield _arxiv_paper()
        raise RuntimeError("provider stopped")

    client.results.return_value = results()
    with patch.object(arxiv_module.arxiv, "Client", return_value=client):
        with caplog.at_level(logging.ERROR):
            candidates = ArxivSearch("query").search_candidates()

    assert [candidate.title for candidate in candidates] == ["An arXiv Paper"]
    assert "arXiv provider failure" in caplog.text


def test_arxiv_search_calls_candidates_once_and_returns_exact_projection():
    retriever = ArxivSearch("query")
    candidate = _candidate()
    with patch.object(
        retriever, "search_candidates", return_value=[candidate]
    ) as search_candidates:
        result = retriever.search(max_results=6)

    search_candidates.assert_called_once_with(max_results=6)
    assert result == [candidate.to_retriever_result()]
    assert set(result[0]) == {"title", "href", "body"}


def test_environment_values_are_isolated_and_restored(monkeypatch):
    assert API_KEY_ENV not in os.environ
    assert JOURNALS_ENV not in os.environ

    monkeypatch.setenv(API_KEY_ENV, "caller-key")
    monkeypatch.setenv(JOURNALS_ENV, "tgars")
    with monkeypatch.context() as isolated:
        isolated.delenv(API_KEY_ENV, raising=False)
        isolated.delenv(JOURNALS_ENV, raising=False)
        assert API_KEY_ENV not in os.environ
        assert JOURNALS_ENV not in os.environ

    assert os.environ[API_KEY_ENV] == "caller-key"
    assert os.environ[JOURNALS_ENV] == "tgars"
