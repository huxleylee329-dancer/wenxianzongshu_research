import logging
from unittest.mock import MagicMock, patch

import pytest
import requests

from gpt_researcher.retrievers.semantic_scholar.semantic_scholar import (
    SemanticScholarSearch,
)


def _response(payload):
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = payload
    return response


def _paper(**overrides):
    values = {
        "title": "  A Semantic Paper  ",
        "abstract": "  Abstract with\nsubstantive text.  ",
        "url": " https://www.semanticscholar.org/paper/example ",
        "authors": [{"name": "First Author"}, {"name": "Second Author"}],
        "year": 2023,
        "venue": "Journal of Tests",
        "externalIds": {"DOI": "doi:10.5555/Example"},
        "isOpenAccess": False,
        "openAccessPdf": None,
    }
    values.update(overrides)
    return values


def test_semantic_scholar_requests_fields_timeout_and_maps_non_open_access_paper():
    response = _response({"data": [_paper()]})

    with patch(
        "gpt_researcher.retrievers.semantic_scholar.semantic_scholar.requests.get",
        return_value=response,
    ) as get:
        result = SemanticScholarSearch("graph learning", sort="citationCount").search(
            max_results=4
        )

    get.assert_called_once()
    _, kwargs = get.call_args
    assert kwargs["timeout"] == SemanticScholarSearch.REQUEST_TIMEOUT_SECONDS
    assert kwargs["params"]["query"] == "graph learning"
    assert kwargs["params"]["limit"] == 4
    assert kwargs["params"]["sort"] == "citationCount"
    fields = set(kwargs["params"]["fields"].split(","))
    assert {
        "title",
        "abstract",
        "url",
        "authors",
        "year",
        "venue",
        "externalIds",
    } <= fields
    assert "isOpenAccess" not in fields
    assert "openAccessPdf" not in fields
    assert result == [
        {
            "title": "A Semantic Paper",
            "href": "https://www.semanticscholar.org/paper/example",
            "body": (
                "Title: A Semantic Paper\n"
                "Authors: First Author; Second Author\n"
                "Year: 2023\n"
                "Venue: Journal of Tests\n"
                "DOI: 10.5555/example\n"
                "Source: Semantic Scholar\n\n"
                "Abstract:\n"
                "Abstract with substantive text."
            ),
        }
    ]
    assert set(result[0]) == {"title", "href", "body"}


def test_semantic_scholar_handles_missing_optional_metadata():
    response = _response(
        {
            "data": [
                _paper(authors=None, year=None, venue=None, externalIds=None)
            ]
        }
    )

    with patch(
        "gpt_researcher.retrievers.semantic_scholar.semantic_scholar.requests.get",
        return_value=response,
    ):
        result = SemanticScholarSearch("query").search()

    body = result[0]["body"]
    assert "Authors: N/A" in body
    assert "Year: N/A" in body
    assert "Venue: N/A" in body
    assert "DOI: N/A" in body


@pytest.mark.parametrize("abstract", [None, "", "  "])
def test_semantic_scholar_skips_missing_or_blank_abstract(abstract):
    response = _response({"data": [_paper(abstract=abstract)]})

    with patch(
        "gpt_researcher.retrievers.semantic_scholar.semantic_scholar.requests.get",
        return_value=response,
    ):
        assert SemanticScholarSearch("query").search() == []


def test_semantic_scholar_skips_blank_title_or_url():
    response = _response(
        {"data": [_paper(title=" "), _paper(url=None), _paper(title="Valid")]}
    )

    with patch(
        "gpt_researcher.retrievers.semantic_scholar.semantic_scholar.requests.get",
        return_value=response,
    ):
        result = SemanticScholarSearch("query").search()

    assert [item["title"] for item in result] == ["Valid"]


def test_semantic_scholar_preserves_valid_results_with_malformed_records():
    response = _response({"data": [_paper(), None, _paper(title="Second")]})

    with patch(
        "gpt_researcher.retrievers.semantic_scholar.semantic_scholar.requests.get",
        return_value=response,
    ):
        result = SemanticScholarSearch("query").search()

    assert [item["title"] for item in result] == ["A Semantic Paper", "Second"]


def test_semantic_scholar_honors_max_results():
    response = _response(
        {"data": [_paper(title="First"), _paper(title="Second")]}
    )

    with patch(
        "gpt_researcher.retrievers.semantic_scholar.semantic_scholar.requests.get",
        return_value=response,
    ):
        result = SemanticScholarSearch("query").search(max_results=1)

    assert [item["title"] for item in result] == ["First"]


@pytest.mark.parametrize(
    ("failure", "category"),
    [
        (requests.Timeout("timeout"), "timeout failure"),
        (requests.ConnectionError("offline"), "connection failure"),
        (requests.HTTPError("503"), "HTTP failure"),
    ],
)
def test_semantic_scholar_request_failures_return_empty_and_log(
    failure, category, caplog
):
    with patch(
        "gpt_researcher.retrievers.semantic_scholar.semantic_scholar.requests.get",
        side_effect=failure,
    ), caplog.at_level(logging.ERROR):
        result = SemanticScholarSearch("query").search()

    assert result == []
    assert "Semantic Scholar" in caplog.text
    assert category in caplog.text


def test_semantic_scholar_non_success_response_returns_empty_and_logs(caplog):
    response = _response({"data": []})
    response.raise_for_status.side_effect = requests.HTTPError("503")

    with patch(
        "gpt_researcher.retrievers.semantic_scholar.semantic_scholar.requests.get",
        return_value=response,
    ), caplog.at_level(logging.ERROR):
        result = SemanticScholarSearch("query").search()

    assert result == []
    assert "Semantic Scholar HTTP failure" in caplog.text


def test_semantic_scholar_invalid_json_returns_empty_and_logs(caplog):
    response = _response(None)
    response.json.side_effect = ValueError("invalid JSON")

    with patch(
        "gpt_researcher.retrievers.semantic_scholar.semantic_scholar.requests.get",
        return_value=response,
    ), caplog.at_level(logging.ERROR):
        result = SemanticScholarSearch("query").search()

    assert result == []
    assert "Semantic Scholar invalid JSON failure" in caplog.text


@pytest.mark.parametrize("payload", [None, [], {}, {"data": None}, {"data": {}}])
def test_semantic_scholar_invalid_response_shape_returns_empty_and_logs(
    payload, caplog
):
    response = _response(payload)

    with patch(
        "gpt_researcher.retrievers.semantic_scholar.semantic_scholar.requests.get",
        return_value=response,
    ), caplog.at_level(logging.ERROR):
        result = SemanticScholarSearch("query").search()

    assert result == []
    assert "Semantic Scholar invalid response shape" in caplog.text
