"""Regression test for Semantic Scholar sort-criterion casing.

``VALID_SORT_CRITERIA`` holds the API's exact camelCase values
(``citationCount``, ``publicationDate``), and ``__init__`` asserts the
incoming value is one of them. It then stored ``sort.lower()``, corrupting
``citationCount`` -> ``citationcount`` before it was sent as the ``sort``
query parameter. The Semantic Scholar API expects the camelCase form.
"""

from unittest.mock import MagicMock, patch

import pytest

from gpt_researcher.retrievers.semantic_scholar.semantic_scholar import (
    SemanticScholarSearch,
)


@pytest.fixture(autouse=True)
def _remove_semantic_scholar_api_key(monkeypatch):
    monkeypatch.delenv("SEMANTIC_SCHOLAR_API_KEY", raising=False)


def test_camelcase_sort_preserved():
    s = SemanticScholarSearch("anything", sort="citationCount")
    assert s.sort == "citationCount"


def test_publication_date_sort_preserved():
    s = SemanticScholarSearch("anything", sort="publicationDate")
    assert s.sort == "publicationDate"


def test_relevance_default_preserved():
    s = SemanticScholarSearch("anything")
    assert s.sort == "relevance"


def test_stored_sort_is_a_valid_api_value():
    # Whatever is stored must round-trip as an accepted API criterion.
    s = SemanticScholarSearch("anything", sort="citationCount")
    assert s.sort in SemanticScholarSearch.VALID_SORT_CRITERIA


def _search_and_get_request(sort):
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"data": []}
    with patch(
        "gpt_researcher.retrievers.semantic_scholar.semantic_scholar.requests.get",
        return_value=response,
    ) as get:
        SemanticScholarSearch("anything", sort=sort).search(max_results=3)
    return get.call_args


def test_relevance_uses_search_endpoint_without_sort_parameter():
    args, kwargs = _search_and_get_request("relevance")

    assert args[0] == "https://api.semanticscholar.org/graph/v1/paper/search"
    assert "sort" not in kwargs["params"]


@pytest.mark.parametrize(
    ("sort", "api_sort"),
    [
        ("citationCount", "citationCount:desc"),
        ("publicationDate", "publicationDate:desc"),
    ],
)
def test_explicit_sorts_use_bulk_endpoint_and_descending_order(sort, api_sort):
    args, kwargs = _search_and_get_request(sort)

    assert args[0] == (
        "https://api.semanticscholar.org/graph/v1/paper/search/bulk"
    )
    assert kwargs["params"]["sort"] == api_sort
