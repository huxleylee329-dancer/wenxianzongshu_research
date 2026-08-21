import logging
import os
from unittest.mock import MagicMock, PropertyMock, patch

import pytest
import requests

from gpt_researcher.retrievers.semantic_scholar.semantic_scholar import (
    SemanticScholarSearch,
)


API_KEY_ENV = "SEMANTIC_SCHOLAR_API_KEY"
SYNTHETIC_API_KEY = "synthetic-semantic-scholar-key"


@pytest.fixture(autouse=True)
def _isolate_semantic_scholar_api_key(monkeypatch):
    monkeypatch.delenv(API_KEY_ENV, raising=False)


def _response(payload, status_code=200):
    response = MagicMock()
    response.status_code = status_code
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
        result = SemanticScholarSearch("graph learning").search(max_results=4)

    get.assert_called_once()
    args, kwargs = get.call_args
    assert args[0] == SemanticScholarSearch.BASE_URL
    assert kwargs["timeout"] == SemanticScholarSearch.REQUEST_TIMEOUT_SECONDS
    assert kwargs["params"]["query"] == "graph learning"
    assert kwargs["params"]["limit"] == 4
    assert "sort" not in kwargs["params"]
    assert "x-api-key" not in (kwargs.get("headers") or {})
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


def test_semantic_scholar_normalizes_api_key_before_sending(monkeypatch, caplog):
    raw_key = f"  {SYNTHETIC_API_KEY}\t"
    monkeypatch.setenv(API_KEY_ENV, raw_key)
    response = _response({"data": []})

    with patch(
        "gpt_researcher.retrievers.semantic_scholar.semantic_scholar.requests.get",
        return_value=response,
    ) as get, caplog.at_level(logging.ERROR):
        assert SemanticScholarSearch("query").search() == []

    args, kwargs = get.call_args
    assert kwargs["headers"] == {"x-api-key": SYNTHETIC_API_KEY}
    assert raw_key not in repr(get.call_args)
    assert SYNTHETIC_API_KEY not in args[0]
    assert SYNTHETIC_API_KEY not in repr(kwargs["params"])
    assert SYNTHETIC_API_KEY not in caplog.text


@pytest.mark.parametrize("key_value", ["", " ", "\t\r\n"])
def test_semantic_scholar_empty_or_blank_api_key_uses_anonymous_mode(
    key_value, monkeypatch
):
    monkeypatch.setenv(API_KEY_ENV, key_value)
    response = _response({"data": []})

    with patch(
        "gpt_researcher.retrievers.semantic_scholar.semantic_scholar.requests.get",
        return_value=response,
    ) as get:
        assert SemanticScholarSearch("query").search() == []

    assert "x-api-key" not in (get.call_args.kwargs.get("headers") or {})


def test_semantic_scholar_missing_key_is_explicitly_removed_and_restored(
    monkeypatch,
):
    caller_value = "synthetic-caller-environment-key"
    monkeypatch.setenv(API_KEY_ENV, caller_value)
    response = _response({"data": []})

    with monkeypatch.context() as isolated_environment:
        isolated_environment.delenv(API_KEY_ENV, raising=False)
        with patch(
            "gpt_researcher.retrievers.semantic_scholar.semantic_scholar.requests.get",
            return_value=response,
        ) as get:
            assert SemanticScholarSearch("query").search() == []

        assert "x-api-key" not in (get.call_args.kwargs.get("headers") or {})

    assert os.environ[API_KEY_ENV] == caller_value


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


@pytest.mark.parametrize("status_code", [400, 403, 429, 503])
def test_semantic_scholar_http_status_failures_are_safe_and_logged(
    status_code, caplog
):
    response = _response({"data": []}, status_code=status_code)
    response.raise_for_status.side_effect = requests.HTTPError(
        "unsafe provider exception text",
        response=response,
    )

    with patch(
        "gpt_researcher.retrievers.semantic_scholar.semantic_scholar.requests.get",
        return_value=response,
    ) as get, caplog.at_level(logging.ERROR):
        result = SemanticScholarSearch("query").search()

    assert result == []
    assert get.call_count == 1
    assert str(status_code) in caplog.text
    assert "unsafe provider exception text" not in caplog.text


def test_semantic_scholar_http_error_without_response_logs_unknown(caplog):
    error = requests.HTTPError("unsafe exception without response")

    with patch(
        "gpt_researcher.retrievers.semantic_scholar.semantic_scholar.requests.get",
        side_effect=error,
    ) as get, caplog.at_level(logging.ERROR):
        result = SemanticScholarSearch("query").search()

    assert result == []
    assert get.call_count == 1
    assert "unknown" in caplog.text
    assert "unsafe exception without response" not in caplog.text


def test_semantic_scholar_http_failure_does_not_log_secrets_or_response(
    monkeypatch, caplog
):
    class SensitiveHTTPError(requests.HTTPError):
        def __str__(self):
            raise AssertionError("HTTPError string must not be read")

        def __repr__(self):
            raise AssertionError("HTTPError repr must not be read")

    response_body = "synthetic-private-response-body"
    raw_key = f"  {SYNTHETIC_API_KEY}  "
    monkeypatch.setenv(API_KEY_ENV, raw_key)
    response = _response({"data": []}, status_code=429)
    type(response).text = PropertyMock(
        side_effect=AssertionError("response.text must not be read")
    )
    type(response).content = PropertyMock(
        side_effect=AssertionError("response.content must not be read")
    )
    response.raise_for_status.side_effect = SensitiveHTTPError(
        f"unsafe {SYNTHETIC_API_KEY} {response_body}",
        response=response,
    )

    with patch(
        "gpt_researcher.retrievers.semantic_scholar.semantic_scholar.requests.get",
        return_value=response,
    ) as get, caplog.at_level(logging.ERROR):
        result = SemanticScholarSearch("query").search()

    args, kwargs = get.call_args
    assert result == []
    assert get.call_count == 1
    assert kwargs["headers"] == {"x-api-key": SYNTHETIC_API_KEY}
    assert SYNTHETIC_API_KEY not in args[0]
    assert SYNTHETIC_API_KEY not in repr(kwargs["params"])
    assert SYNTHETIC_API_KEY not in caplog.text
    assert response_body not in caplog.text
    assert "x-api-key" not in caplog.text


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
