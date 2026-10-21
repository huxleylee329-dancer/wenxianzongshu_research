"""Offline contract fixtures for all six public scholarly APIs."""

from dataclasses import replace

import pytest

from gpt_researcher.academic_library.models import LibraryError, Paper, deduplicate
from gpt_researcher.academic_library.http import PublicHTTP
from gpt_researcher.retrievers.scholarly import SCHOLARLY_RETRIEVERS
from gpt_researcher.actions.retriever import get_retriever


FIXTURES = {
    "hal": {"response": {"docs": [{"halId_s": "hal-123", "title_s": ["Battery"], "abstract_s": ["HAL evidence"], "uri_s": "https://hal.science/hal-123", "fileMain_s": "https://hal.science/hal-123/document", "doiId_s": "10.1000/battery", "docType_s": "ART"}]}},
    "osti": [{"osti_id": 123, "title": "Battery", "description": "OSTI evidence", "doi": "10.1000/battery", "product_type": "Technical Report", "links": [{"rel": "fulltext", "href": "https://www.osti.gov/servlets/purl/123"}]}],
    "openaire": {"results": [{"id": "record123", "mainTitle": "Battery", "descriptions": ["OpenAIRE evidence"], "pids": [{"scheme": "doi", "value": "10.1000/battery"}], "instances": [{"accessRight": {"label": "Open Access"}, "urls": ["https://example.org/paper.pdf"]}]}]},
    "crossref": {"message": {"items": [{"DOI": "10.1000/battery", "title": ["Battery"], "URL": "https://doi.org/10.1000/battery", "abstract": "<jats:p>Crossref evidence</jats:p>", "published": {"date-parts": [[2025, 1, 1]]}, "type": "journal-article"}]}},
    "europe_pmc": {"resultList": {"result": [{"id": "123", "source": "MED", "title": "Battery", "abstractText": "PMC evidence", "doi": "10.1000/battery", "isOpenAccess": "Y", "pmcid": "PMC123"}]}},
    "zenodo": {"hits": {"hits": [{"id": 123, "metadata": {"title": "Battery", "description": "Zenodo evidence", "doi": "10.1000/battery", "access_right": "open", "resource_type": {"type": "publication", "subtype": "preprint"}}, "files": [{"key": "paper.pdf", "links": {"self": "https://zenodo.org/api/records/123/files/paper.pdf/content"}}]}]}},
}


class HTTP:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def json(self, url, params=None):
        self.calls.append((url, params))
        return self.payload


@pytest.mark.parametrize("provider", FIXTURES)
def test_provider_records_candidates_and_registration(provider):
    http = HTTP(FIXTURES[provider])
    cls = get_retriever(provider)
    assert cls is SCHOLARLY_RETRIEVERS[provider]
    retriever = cls("battery", http=http)
    record, = retriever.search_records(1)
    assert len(http.calls) == 1
    assert record.doi == "10.1000/battery" and record.content_status == "abstract"
    assert record.fulltext_urls or provider == "crossref"
    candidate, = retriever.search_candidates(1)
    assert candidate.source == provider and candidate.abstract.endswith("evidence")
    assert set(candidate.to_retriever_result()) == {"title", "href", "body"}


def test_metadata_only_not_evidence_and_strong_deduplication():
    http = HTTP({"message": {"items": [{"DOI": "10.1000/test", "title": ["Paper"], "URL": "https://doi.org/10.1000/test"}]}})
    retriever = SCHOLARLY_RETRIEVERS["crossref"]("test", http=http)
    assert retriever.search_records()[0].content_status == "metadata_only"
    assert retriever.search_candidates() == () and retriever.search() == []
    first = Paper("hal", "1", "Same title", "https://example.org/1", doi="10.1000/a")
    second = replace(first, provider="osti", fulltext_urls=("https://example.org/a.pdf",))
    merged, = deduplicate([first, second])
    assert merged.origins == ("hal:1", "osti:1") and merged.fulltext_urls == second.fulltext_urls
    assert len(deduplicate([replace(first, doi=None), replace(second, doi=None)])) == 2


@pytest.mark.parametrize("payload", [None, [], {"message": {"items": [None, {}, {"title": ["No DOI"]}]}}])
def test_malformed_provider_shapes_are_not_synthetic_evidence(payload):
    assert SCHOLARLY_RETRIEVERS["crossref"]("battery", http=HTTP(payload)).search_records() == ()


def test_http_pins_dns_checks_redirects_and_bounds(monkeypatch):
    from gpt_researcher.academic_library import http as module
    from types import SimpleNamespace
    calls = []
    monkeypatch.setattr(module.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))])
    class Response:
        status = 200
        headers = {"Content-Type": "application/json"}
        def stream(self, *a, **k):
            yield b'{"ok":true}'
        def close(self):
            pass
    class Pool:
        def __init__(self, **kwargs):
            calls.append(kwargs)
        def urlopen(self, *args, **kwargs):
            assert kwargs["retries"] is False and kwargs["redirect"] is False
            return Response()
        def close(self):
            pass
    monkeypatch.setattr(module.urllib3, "HTTPSConnectionPool", Pool)
    assert PublicHTTP().json("https://example.org/paper") == {"ok": True}
    assert calls[0]["host"] == "93.184.216.34" and calls[0]["server_hostname"] == "example.org"
    with pytest.raises(LibraryError, match="download_too_large"):
        PublicHTTP().get("https://example.org/paper", max_bytes=2)
    Response.status, Response.headers = 302, {"Location": "https://127.0.0.1/private"}
    monkeypatch.setattr(module.socket, "getaddrinfo", lambda host, *a, **k: [(2, 1, 6, "", ("127.0.0.1" if host == "127.0.0.1" else "93.184.216.34", 443))])
    with pytest.raises(LibraryError, match="unsafe_url"):
        PublicHTTP().get("https://example.org/paper")


def test_failure_isolation_and_no_unrelated_fallback():
    from gpt_researcher.academic_library.research import PaperTools
    tools = PaperTools(http=HTTP({}))
    calls = []
    def search(provider, query, limit):
        calls.append(provider)
        if provider == "hal":
            raise RuntimeError("private remote body")
        return [Paper(provider, "1", "Paper", "https://example.org/paper", "Evidence")]
    tools._provider_search = search
    assert len(tools.search_papers("battery", ("hal", "osti"))) == 1
    assert sorted(calls) == ["hal", "osti"]
    assert tools.failures == [{"provider": "hal", "code": "provider_search_failed"}]
