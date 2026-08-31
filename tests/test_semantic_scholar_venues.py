import logging
import os
from unittest.mock import MagicMock

import pytest
import requests

import gpt_researcher.retrievers.semantic_scholar.semantic_scholar as semantic_scholar_module
from gpt_researcher.retrievers.semantic_scholar.semantic_scholar import (
    SemanticScholarSearch,
)


JOURNALS_ENV = "SEMANTIC_SCHOLAR_JOURNALS"
API_KEY_ENV = "SEMANTIC_SCHOLAR_API_KEY"
SYNTHETIC_API_KEY = "synthetic-semantic-scholar-key"
UNSUPPORTED_SENTINEL = "synthetic-unsupported-journal-sentinel"

FROZEN_VENUES = {
    "tgars": ("IEEE Transactions on Geoscience and Remote Sensing",),
    "jstars": (
        "IEEE Journal of Selected Topics in Applied Earth Observations and "
        "Remote Sensing",
    ),
    "taes": ("IEEE Transactions on Aerospace and Electronic Systems",),
    "remote_sensing": ("Remote Sensing",),
    "journal_of_radars": (
        "Journal of Radars",
        "雷达学报",
        "雷达学报(中英文)",
    ),
}


def _response(payload=None, status_code=200):
    response = MagicMock()
    response.status_code = status_code
    response.raise_for_status = MagicMock()
    response.json.return_value = {"data": []} if payload is None else payload
    return response


def _paper():
    return {
        "title": "A Radar Paper",
        "abstract": "A substantive mocked abstract.",
        "url": "https://www.semanticscholar.org/paper/synthetic",
        "authors": [{"name": "First Author"}],
        "year": 2024,
        "venue": "IEEE Transactions on Geoscience and Remote Sensing",
        "externalIds": {"DOI": "10.1000/synthetic"},
    }


@pytest.fixture(autouse=True)
def _isolate_environment_and_mock_network(monkeypatch):
    monkeypatch.delenv(JOURNALS_ENV, raising=False)
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    request_get = MagicMock(return_value=_response())
    monkeypatch.setattr(semantic_scholar_module.requests, "get", request_get)
    yield request_get


def test_missing_journal_configuration_preserves_unrestricted_request(
    _isolate_environment_and_mock_network,
):
    request_get = _isolate_environment_and_mock_network

    assert SemanticScholarSearch("radar").search() == []

    request_get.assert_called_once()
    args, kwargs = request_get.call_args
    assert args[0] == SemanticScholarSearch.BASE_URL
    assert "venue" not in kwargs["params"]
    assert "sort" not in kwargs["params"]


@pytest.mark.parametrize("configured_value", ["", "  \t\r\n  "])
def test_empty_or_blank_journal_configuration_preserves_unrestricted_request(
    configured_value,
    monkeypatch,
    _isolate_environment_and_mock_network,
):
    request_get = _isolate_environment_and_mock_network
    monkeypatch.setenv(JOURNALS_ENV, configured_value)

    assert SemanticScholarSearch("radar").search() == []

    request_get.assert_called_once()
    assert "venue" not in request_get.call_args.kwargs["params"]


@pytest.mark.parametrize(("token", "expected_venues"), FROZEN_VENUES.items())
def test_each_frozen_token_maps_to_exact_venues(
    token,
    expected_venues,
    monkeypatch,
    _isolate_environment_and_mock_network,
):
    request_get = _isolate_environment_and_mock_network
    monkeypatch.setenv(JOURNALS_ENV, token)

    assert SemanticScholarSearch("radar").search() == []

    request_get.assert_called_once()
    assert request_get.call_args.kwargs["params"]["venue"] == ",".join(
        expected_venues
    )


def test_journal_of_radars_alias_order_is_frozen(
    monkeypatch,
    _isolate_environment_and_mock_network,
):
    request_get = _isolate_environment_and_mock_network
    monkeypatch.setenv(JOURNALS_ENV, "journal_of_radars")

    SemanticScholarSearch("radar").search()

    assert request_get.call_args.kwargs["params"]["venue"] == (
        "Journal of Radars,雷达学报,雷达学报(中英文)"
    )


def test_tokens_are_normalized_deduplicated_and_expanded_in_first_seen_order(
    monkeypatch,
    _isolate_environment_and_mock_network,
):
    request_get = _isolate_environment_and_mock_network
    monkeypatch.setenv(
        JOURNALS_ENV,
        "  JSTARS, , TGARS,jstars,, remote_sensing, TGARS, ",
    )

    SemanticScholarSearch("radar").search()

    assert request_get.call_args.kwargs["params"]["venue"] == ",".join(
        (
            *FROZEN_VENUES["jstars"],
            *FROZEN_VENUES["tgars"],
            *FROZEN_VENUES["remote_sensing"],
        )
    )


def test_relevance_sends_venue_without_sort(
    monkeypatch,
    _isolate_environment_and_mock_network,
):
    request_get = _isolate_environment_and_mock_network
    monkeypatch.setenv(JOURNALS_ENV, "taes")

    SemanticScholarSearch("radar", sort="relevance").search()

    args, kwargs = request_get.call_args
    assert args[0] == SemanticScholarSearch.BASE_URL
    assert kwargs["params"]["venue"] == FROZEN_VENUES["taes"][0]
    assert "sort" not in kwargs["params"]


@pytest.mark.parametrize(
    ("sort", "expected_sort"),
    [
        ("citationCount", "citationCount:desc"),
        ("publicationDate", "publicationDate:desc"),
    ],
)
def test_bulk_sorts_send_the_same_venue_and_descending_sort(
    sort,
    expected_sort,
    monkeypatch,
    _isolate_environment_and_mock_network,
):
    request_get = _isolate_environment_and_mock_network
    monkeypatch.setenv(JOURNALS_ENV, "tgars,journal_of_radars")
    expected_venue = ",".join(
        (*FROZEN_VENUES["tgars"], *FROZEN_VENUES["journal_of_radars"])
    )

    SemanticScholarSearch("radar", sort=sort).search()

    args, kwargs = request_get.call_args
    assert args[0] == SemanticScholarSearch.BULK_URL
    assert kwargs["params"]["sort"] == expected_sort
    assert kwargs["params"]["venue"] == expected_venue


def test_mixed_valid_and_invalid_tokens_send_only_valid_venues_and_warn_once(
    monkeypatch,
    caplog,
    _isolate_environment_and_mock_network,
):
    request_get = _isolate_environment_and_mock_network
    another_invalid = "another-synthetic-unsupported-token"
    monkeypatch.setenv(
        JOURNALS_ENV,
        f"tgars,{UNSUPPORTED_SENTINEL},{UNSUPPORTED_SENTINEL},jstars,{another_invalid}",
    )

    with caplog.at_level(logging.WARNING):
        assert SemanticScholarSearch("radar").search() == []

    request_get.assert_called_once()
    assert request_get.call_args.kwargs["params"]["venue"] == ",".join(
        (*FROZEN_VENUES["tgars"], *FROZEN_VENUES["jstars"])
    )
    warnings = [record.getMessage() for record in caplog.records]
    assert len(warnings) == 1
    assert "count=2" in warnings[0]
    assert UNSUPPORTED_SENTINEL not in caplog.text
    assert another_invalid not in caplog.text
    assert "tgars" not in caplog.text.lower()
    assert "jstars" not in caplog.text.lower()


def test_all_invalid_tokens_fail_closed_without_a_request(
    monkeypatch,
    caplog,
    _isolate_environment_and_mock_network,
):
    request_get = _isolate_environment_and_mock_network
    monkeypatch.setenv(
        JOURNALS_ENV,
        f"{UNSUPPORTED_SENTINEL},{UNSUPPORTED_SENTINEL}",
    )

    with caplog.at_level(logging.WARNING):
        assert SemanticScholarSearch("radar").search() == []

    request_get.assert_not_called()
    warnings = [record.getMessage() for record in caplog.records]
    assert len(warnings) == 1
    assert "count=1" in warnings[0]
    assert UNSUPPORTED_SENTINEL not in caplog.text


def test_separator_only_nonblank_configuration_fails_closed_without_a_request(
    monkeypatch,
    caplog,
    _isolate_environment_and_mock_network,
):
    request_get = _isolate_environment_and_mock_network
    monkeypatch.setenv(JOURNALS_ENV, ",  ,\t, ,")

    with caplog.at_level(logging.WARNING):
        assert SemanticScholarSearch("radar").search() == []

    request_get.assert_not_called()
    assert len(caplog.records) == 1
    assert "count=0" in caplog.records[0].getMessage()


def test_unsupported_sentinel_never_enters_request_or_diagnostics(
    monkeypatch,
    caplog,
    _isolate_environment_and_mock_network,
):
    request_get = _isolate_environment_and_mock_network
    monkeypatch.setenv(JOURNALS_ENV, f"tgars,{UNSUPPORTED_SENTINEL}")

    with caplog.at_level(logging.WARNING):
        assert SemanticScholarSearch("radar").search() == []

    request_get.assert_called_once()
    args, kwargs = request_get.call_args
    assert UNSUPPORTED_SENTINEL not in args[0]
    assert UNSUPPORTED_SENTINEL not in repr(kwargs["params"])
    assert UNSUPPORTED_SENTINEL not in repr(kwargs.get("headers") or {})
    assert UNSUPPORTED_SENTINEL not in caplog.text


def test_journal_and_api_key_environment_values_are_deleted_and_restored(
    monkeypatch,
    _isolate_environment_and_mock_network,
):
    request_get = _isolate_environment_and_mock_network
    caller_journals = "tgars"
    caller_key = "synthetic-caller-key"
    monkeypatch.setenv(JOURNALS_ENV, caller_journals)
    monkeypatch.setenv(API_KEY_ENV, caller_key)

    with monkeypatch.context() as isolated_environment:
        isolated_environment.delenv(JOURNALS_ENV, raising=False)
        isolated_environment.delenv(API_KEY_ENV, raising=False)
        SemanticScholarSearch("radar").search()

        request_get.assert_called_once()
        kwargs = request_get.call_args.kwargs
        assert "venue" not in kwargs["params"]
        assert "x-api-key" not in (kwargs.get("headers") or {})

    assert os.environ[JOURNALS_ENV] == caller_journals
    assert os.environ[API_KEY_ENV] == caller_key


def test_api_key_and_venue_are_sent_only_in_their_approved_locations(
    monkeypatch,
    caplog,
    _isolate_environment_and_mock_network,
):
    request_get = _isolate_environment_and_mock_network
    raw_key = f"  {SYNTHETIC_API_KEY}\t"
    monkeypatch.setenv(JOURNALS_ENV, "remote_sensing")
    monkeypatch.setenv(API_KEY_ENV, raw_key)

    with caplog.at_level(logging.WARNING):
        SemanticScholarSearch("radar").search()

    args, kwargs = request_get.call_args
    assert args[0] == SemanticScholarSearch.BASE_URL
    assert kwargs["params"]["venue"] == FROZEN_VENUES["remote_sensing"][0]
    assert kwargs["headers"] == {"x-api-key": SYNTHETIC_API_KEY}
    assert raw_key not in repr(kwargs["headers"])
    assert SYNTHETIC_API_KEY not in args[0]
    assert SYNTHETIC_API_KEY not in repr(kwargs["params"])
    assert SYNTHETIC_API_KEY not in caplog.text


def test_valid_filter_keeps_timeout_and_uses_one_request(
    monkeypatch,
    _isolate_environment_and_mock_network,
):
    request_get = _isolate_environment_and_mock_network
    monkeypatch.setenv(JOURNALS_ENV, "tgars")

    SemanticScholarSearch("radar").search()

    request_get.assert_called_once()
    assert (
        request_get.call_args.kwargs["timeout"]
        == SemanticScholarSearch.REQUEST_TIMEOUT_SECONDS
        == 10
    )


def test_429_with_valid_filter_returns_safely_without_retry_or_secret_logging(
    monkeypatch,
    caplog,
    _isolate_environment_and_mock_network,
):
    request_get = _isolate_environment_and_mock_network
    monkeypatch.setenv(JOURNALS_ENV, "tgars")
    monkeypatch.setenv(API_KEY_ENV, SYNTHETIC_API_KEY)
    response = _response(status_code=429)
    response.raise_for_status.side_effect = requests.HTTPError(
        f"unsafe {SYNTHETIC_API_KEY}",
        response=response,
    )
    request_get.return_value = response

    with caplog.at_level(logging.ERROR):
        assert SemanticScholarSearch("radar").search() == []

    request_get.assert_called_once()
    assert "429" in caplog.text
    assert SYNTHETIC_API_KEY not in caplog.text
    assert "x-api-key" not in caplog.text


def test_filtered_result_keeps_exact_contract_and_metadata_body(
    monkeypatch,
    _isolate_environment_and_mock_network,
):
    request_get = _isolate_environment_and_mock_network
    monkeypatch.setenv(JOURNALS_ENV, "tgars")
    request_get.return_value = _response({"data": [_paper()]})

    results = SemanticScholarSearch("radar").search()

    assert len(results) == 1
    assert set(results[0]) == {"title", "href", "body"}
    assert "Venue: IEEE Transactions on Geoscience and Remote Sensing" in (
        results[0]["body"]
    )
    assert "DOI: 10.1000/synthetic" in results[0]["body"]
    assert SemanticScholarSearch.BODY_IS_PREFETCHED_CONTENT is True
