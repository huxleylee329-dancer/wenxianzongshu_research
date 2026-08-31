import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

from gpt_researcher.skills.researcher import ResearchConductor


class FakeResearcher:
    def __init__(self, retriever_class):
        self.retrievers = [retriever_class]
        self.cfg = SimpleNamespace(max_search_results_per_query=5)
        self.research_sources = []

    def add_research_sources(self, sources):
        self.research_sources.extend(sources)


def _search_urls(retriever_class):
    researcher = FakeResearcher(retriever_class)
    conductor = ResearchConductor.__new__(ResearchConductor)
    conductor.researcher = researcher
    conductor.logger = logging.getLogger(__name__)
    conductor._get_new_urls = AsyncMock(side_effect=lambda urls: urls)

    result = asyncio.run(conductor._search_relevant_source_urls("query"))
    return result, researcher


def test_opted_in_body_is_prefetched_unchanged_and_source_is_recorded():
    body = "  Title: Paper\n\nAbstract:\nShort abstract.  "

    class AcademicRetriever:
        BODY_IS_PREFETCHED_CONTENT = True

        def __init__(self, query, query_domains=None):
            pass

        def search(self, max_results):
            return [self.result]

    AcademicRetriever.result = {
        "title": "Paper",
        "href": "https://papers.example/academic",
        "body": body,
    }

    (new_search_urls, prefetched_content), researcher = _search_urls(
        AcademicRetriever
    )

    assert new_search_urls == []
    assert prefetched_content == [
        {
            "url": "https://papers.example/academic",
            "raw_content": body,
        }
    ]
    assert researcher.research_sources == [
        {"url": "https://papers.example/academic"}
    ]
    assert set(AcademicRetriever.result) == {"title", "href", "body"}


def test_normal_retriever_body_still_uses_page_fetch_path():
    class WebRetriever:
        def __init__(self, query, query_domains=None):
            pass

        def search(self, max_results):
            return [
                {
                    "title": "Web result",
                    "href": "https://web.example/page",
                    "body": "A non-empty search-result snippet.",
                }
            ]

    (new_search_urls, prefetched_content), researcher = _search_urls(WebRetriever)

    assert new_search_urls == ["https://web.example/page"]
    assert prefetched_content == []
    assert researcher.research_sources == []


def test_existing_raw_content_path_keeps_priority():
    raw_content = "F" * 101

    class FullPageRetriever:
        BODY_IS_PREFETCHED_CONTENT = True

        def __init__(self, query, query_domains=None):
            pass

        def search(self, max_results):
            return [
                {
                    "title": "Full page",
                    "href": "https://web.example/full",
                    "body": "Academic-style body",
                    "raw_content": raw_content,
                }
            ]

    (new_search_urls, prefetched_content), researcher = _search_urls(
        FullPageRetriever
    )

    assert new_search_urls == []
    assert prefetched_content == [
        {
            "url": "https://web.example/full",
            "raw_content": raw_content,
        }
    ]
    assert researcher.research_sources == [
        {"url": "https://web.example/full"}
    ]


def test_blank_opted_in_body_is_not_prefetched():
    class BlankAcademicRetriever:
        BODY_IS_PREFETCHED_CONTENT = True

        def __init__(self, query, query_domains=None):
            pass

        def search(self, max_results):
            return [
                {
                    "title": "Paper",
                    "href": "https://papers.example/blank",
                    "body": " \n\t ",
                }
            ]

    (new_search_urls, prefetched_content), researcher = _search_urls(
        BlankAcademicRetriever
    )

    assert new_search_urls == ["https://papers.example/blank"]
    assert prefetched_content == []
    assert researcher.research_sources == []


def test_arxiv_retriever_declares_prefetched_body_capability():
    from gpt_researcher.retrievers.arxiv.arxiv import ArxivSearch

    assert ArxivSearch.BODY_IS_PREFETCHED_CONTENT is True


def test_semantic_scholar_retriever_declares_prefetched_body_capability():
    from gpt_researcher.retrievers.semantic_scholar.semantic_scholar import (
        SemanticScholarSearch,
    )

    assert SemanticScholarSearch.BODY_IS_PREFETCHED_CONTENT is True
