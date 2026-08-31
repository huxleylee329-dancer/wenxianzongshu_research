import logging
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from gpt_researcher.retrievers.academic_utils import (
    format_academic_body,
    normalize_doi,
)
from gpt_researcher.retrievers.arxiv.arxiv import (
    ARXIV_NUM_RETRIES,
    ArxivSearch,
)


def _paper(**overrides):
    values = {
        "title": "  A Useful Paper  ",
        "entry_id": " https://arxiv.org/abs/2401.00001 ",
        "pdf_url": "https://arxiv.org/pdf/2401.00001",
        "summary": "  An abstract with\nmeaningful text.  ",
        "authors": [
            SimpleNamespace(name="Ada Lovelace"),
            SimpleNamespace(name="Alan Turing"),
        ],
        "published": datetime(2024, 1, 2, tzinfo=timezone.utc),
        "doi": " HTTPS://DOI.ORG/10.1234/Example ",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("10.1234/Example", "10.1234/example"),
        (" DOI: 10.1234/Example ", "10.1234/example"),
        ("https://doi.org/10.1234/Example", "10.1234/example"),
        ("http://doi.org/10.1234/Example", "10.1234/example"),
        ("https://dx.doi.org/10.1234/Example", "10.1234/example"),
        ("http://dx.doi.org/10.1234/Example", "10.1234/example"),
        (None, None),
        (" DOI:  ", None),
    ],
)
def test_normalize_doi(raw, expected):
    assert normalize_doi(raw) == expected


def test_format_academic_body_uses_frozen_field_order_and_missing_values():
    body = format_academic_body(
        title=" Paper ",
        authors=["First Author", "Second Author"],
        year=None,
        venue=" ",
        doi=None,
        source="arXiv",
        abstract="First line.\nSecond line.",
    )

    assert body == (
        "Title: Paper\n"
        "Authors: First Author; Second Author\n"
        "Year: N/A\n"
        "Venue: N/A\n"
        "DOI: N/A\n"
        "Source: arXiv\n\n"
        "Abstract:\n"
        "First line. Second line."
    )
    assert "First line." in body and "Second line." in body


def test_format_academic_body_represents_missing_authors():
    body = format_academic_body(
        title="Paper",
        authors=[],
        year=2024,
        venue="arXiv",
        doi="10.1/X",
        source="arXiv",
        abstract="Abstract",
    )

    assert "Authors: N/A" in body


def test_arxiv_maps_complete_result_and_configures_search_and_retries():
    client = MagicMock()
    client.results.return_value = iter([_paper()])

    with patch(
        "gpt_researcher.retrievers.arxiv.arxiv.arxiv.Client",
        return_value=client,
    ) as client_class, patch(
        "gpt_researcher.retrievers.arxiv.arxiv.arxiv.Search"
    ) as search_class:
        result = ArxivSearch("quantum widgets", sort="SubmittedDate").search(
            max_results=7
        )

    client_class.assert_called_once_with(num_retries=ARXIV_NUM_RETRIES)
    search_class.assert_called_once_with(
        query="quantum widgets",
        max_results=7,
        sort_by=ArxivSearch("unused", sort="SubmittedDate").sort,
    )
    client.results.assert_called_once_with(search_class.return_value)
    assert result == [
        {
            "title": "A Useful Paper",
            "href": "https://arxiv.org/abs/2401.00001",
            "body": (
                "Title: A Useful Paper\n"
                "Authors: Ada Lovelace; Alan Turing\n"
                "Year: 2024\n"
                "Venue: arXiv\n"
                "DOI: 10.1234/example\n"
                "Source: arXiv\n\n"
                "Abstract:\n"
                "An abstract with meaningful text."
            ),
        }
    ]
    assert set(result[0]) == {"title", "href", "body"}
    assert result[0]["href"] != _paper().pdf_url


def test_arxiv_handles_missing_optional_metadata():
    client = MagicMock()
    client.results.return_value = iter(
        [_paper(authors=None, published=None, doi=None)]
    )

    with patch(
        "gpt_researcher.retrievers.arxiv.arxiv.arxiv.Client",
        return_value=client,
    ):
        result = ArxivSearch("query").search()

    assert "Authors: N/A" in result[0]["body"]
    assert "Year: N/A" in result[0]["body"]
    assert "DOI: N/A" in result[0]["body"]


@pytest.mark.parametrize("summary", [None, "", "   "])
def test_arxiv_skips_missing_or_blank_abstract(summary):
    client = MagicMock()
    client.results.return_value = iter([_paper(summary=summary)])

    with patch(
        "gpt_researcher.retrievers.arxiv.arxiv.arxiv.Client",
        return_value=client,
    ):
        assert ArxivSearch("query").search() == []


def test_arxiv_preserves_valid_results_after_a_malformed_record():
    client = MagicMock()

    class MalformedResult:
        @property
        def title(self):
            raise ValueError("malformed title")

    malformed = MalformedResult()
    client.results.return_value = iter([_paper(), malformed, _paper(title="Second")])

    with patch(
        "gpt_researcher.retrievers.arxiv.arxiv.arxiv.Client",
        return_value=client,
    ):
        result = ArxivSearch("query").search()

    assert [item["title"] for item in result] == ["A Useful Paper", "Second"]


def test_arxiv_returns_partial_results_when_generator_later_fails(caplog):
    client = MagicMock()

    def results():
        yield _paper()
        raise RuntimeError("provider stopped")

    client.results.return_value = results()

    with patch(
        "gpt_researcher.retrievers.arxiv.arxiv.arxiv.Client",
        return_value=client,
    ), caplog.at_level(logging.ERROR):
        result = ArxivSearch("query").search()

    assert [item["title"] for item in result] == ["A Useful Paper"]
    assert "arXiv provider failure" in caplog.text


def test_arxiv_provider_failure_returns_empty_and_logs(caplog):
    with patch(
        "gpt_researcher.retrievers.arxiv.arxiv.arxiv.Client",
        side_effect=RuntimeError("provider unavailable"),
    ), caplog.at_level(logging.ERROR):
        result = ArxivSearch("query").search()

    assert result == []
    assert "arXiv provider failure" in caplog.text
