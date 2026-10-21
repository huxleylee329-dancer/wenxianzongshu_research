"""Four bounded tools and a ResearchEvidence-compatible, non-LLM handle."""

from __future__ import annotations

import asyncio
import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace

from .http import PublicHTTP
from .models import LibraryError, Paper, Passage, Reading, deduplicate, doi, public_url
from .reading import PaperReader, select_passages
from .store import get_library


PROVIDERS = ("openalex", "arxiv", "semantic_scholar", "pubmed_central", "hal", "osti", "openaire", "crossref", "europe_pmc", "zenodo")
DEFAULT_PROVIDERS = ("openalex", "hal", "osti")


def validate_selection(mode, providers, document_ids):
    if type(mode) is not str or mode not in ("web", "local", "hybrid"):
        raise LibraryError("invalid_source_mode")
    if type(providers) not in (list, tuple) or len(providers) > 6 or any(type(p) is not str or p not in PROVIDERS for p in providers) or len(set(providers)) != len(providers):
        raise LibraryError("invalid_provider_selection")
    if mode != "local" and not providers:
        raise LibraryError("provider_selection_required")
    if type(document_ids) not in (list, tuple) or len(document_ids) > 64 or any(type(d) is not str or re.fullmatch(r"[0-9a-f]{32}", d) is None for d in document_ids) or len(set(document_ids)) != len(document_ids):
        raise LibraryError("invalid_document_selection")
    if mode in ("local", "hybrid") and not document_ids:
        raise LibraryError("document_selection_required")
    if mode == "web" and document_ids:
        raise LibraryError("invalid_document_selection")
    return mode, tuple(providers), tuple(document_ids)


class PaperTools:
    def __init__(self, *, http=None, library=None):
        self.http = PublicHTTP() if http is None else http
        self.library = library
        self.reader = PaperReader(self.http)
        self.failures: list[dict[str, str]] = []

    def _provider_search(self, provider, query, limit):
        from gpt_researcher.retrievers.scholarly import SCHOLARLY_RETRIEVERS
        if provider in SCHOLARLY_RETRIEVERS:
            return SCHOLARLY_RETRIEVERS[provider](query, http=self.http).search_records(limit)
        if provider == "pubmed_central":
            from gpt_researcher.retrievers.scholarly import EuropePMCSearch
            return EuropePMCSearch(f"({query}) AND IN_PMC:y", http=self.http).search_records(limit)
        if provider == "openalex":
            params = {"search": query, "per_page": limit}
            key = os.getenv("OPENALEX_API_KEY")
            if key:
                params["api_key"] = key
            payload = self.http.json("https://api.openalex.org/works", params)
            return tuple(self._openalex(row) for row in payload.get("results", []) if type(row) is dict)
        if provider in ("arxiv", "semantic_scholar"):
            from gpt_researcher.actions.retriever import get_retriever
            candidates = get_retriever(provider)(query).search_candidates(max_results=limit)
            return tuple(Paper(provider, c.source_record_id or c.candidate_id, c.title, c.href, c.abstract, c.doi, c.published_year, c.authors, "preprint" if provider == "arxiv" else "unknown", (c.href.replace("/abs/", "/pdf/"),) if provider == "arxiv" and c.href.startswith("https://arxiv.org/abs/") else ()) for c in candidates)
        raise LibraryError("unknown_provider")

    @staticmethod
    def _openalex(row):
        from gpt_researcher.retrievers.openalex.openalex import OpenAlexSearch
        from gpt_researcher.academic_library.models import text
        location = row.get("best_oa_location") or {}
        if type(location) is not dict:
            location = {}
        link = public_url(location.get("pdf_url")) or public_url(location.get("landing_page_url"))
        return Paper("openalex", text(row.get("id"), 200), text(row.get("title"), 512), public_url(row.get("doi")) or public_url(row.get("id")), OpenAlexSearch._reconstruct_abstract(row.get("abstract_inverted_index")) or "", doi(row.get("doi")), row.get("publication_year") if type(row.get("publication_year")) is int else None, (), {"article": "journalarticle", "preprint": "preprint", "dissertation": "thesis"}.get(row.get("type"), "unknown"), (link,) if link else (), location.get("license") if type(location.get("license")) is str else None)

    def search_papers(self, query: str, providers: tuple[str, ...], *, per_source: int = 6) -> list[Paper]:
        if type(query) is not str or not query.strip() or len(query) > 4096 or type(per_source) is not int or not 1 <= per_source <= 20:
            raise LibraryError("invalid_query")
        validate_selection("web", providers, ())
        # Results are consumed in configured order, never completion order.
        def run(provider):
            try:
                return list(self._provider_search(provider, query, per_source)), None
            except Exception:
                return [], {"provider": provider, "code": "provider_search_failed"}
        with ThreadPoolExecutor(max_workers=3) as executor:
            batches = list(executor.map(run, providers))
        self.failures.extend(error for _, error in batches if error)
        return deduplicate([paper for batch, _ in batches for paper in batch if paper.title and paper.url])

    def resolve_paper(self, identifier: str) -> Paper:
        from gpt_researcher.retrievers.scholarly import CrossrefSearch
        return CrossrefSearch("doi", http=self.http).resolve_doi(identifier)

    def read_paper(self, paper: Paper) -> Reading:
        return self.reader.read(paper)

    def search_library(self, query: str, document_ids: tuple[str, ...], limit=12):
        library = self.library or get_library()
        hits = library.search(query, document_ids=document_ids, limit=limit)
        selected = list(dict.fromkeys(hit["document_id"] for hit in hits))
        readings = []
        for document_id in selected:
            reading = library.passages(document_id)
            locators = {hit["locator"] for hit in hits if hit["document_id"] == document_id}
            readings.append(replace(reading, passages=tuple(p for p in reading.passages if p.locator in locators)))
        return readings


class AcademicPaperResearcher:
    """Feeds existing bounded ResearchEvidence without altering workflow DTOs."""

    def __init__(self, request, topic_plan, *, providers=DEFAULT_PROVIDERS, document_ids=(), tools=None):
        self.mode, self.providers, self.document_ids = validate_selection(request.report_source, providers, document_ids)
        self.topic_plan = topic_plan
        self.tools = PaperTools() if tools is None else tools
        self.cfg = SimpleNamespace(language=request.language, max_search_results_per_query=request.max_search_results or 6)
        self.image_generator = None
        self.sources = []
        self.summaries = []

    async def conduct_research(self):
        queries = tuple(dict.fromkeys((self.topic_plan.research_topic, *self.topic_plan.research_questions)))[:3]
        readings = []
        if self.mode != "web":
            for query in queries:
                readings.extend(await asyncio.to_thread(self.tools.search_library, query, self.document_ids))
        if self.mode != "local":
            papers = []
            for query in queries:
                papers.extend(await asyncio.to_thread(self.tools.search_papers, query, self.providers, per_source=min(10, self.cfg.max_search_results_per_query)))
            # Discovery may be broad, but reading and workflow projection have
            # a separate fixed budget; unavailable papers never become evidence.
            for paper in deduplicate(papers)[:16]:
                readings.append(await asyncio.to_thread(self.tools.read_paper, paper))
        seen = set()
        for reading in readings:
            paper = reading.paper
            if paper.key in seen:
                continue
            seen.add(paper.key)
            chosen = select_passages(reading, " ".join(queries), limit=2)
            self.summaries.append({
                "title": paper.title, "url": paper.url, "provider": paper.provider,
                "doi": paper.doi, "publication_type": paper.publication_type,
                "content_status": reading.status, "failure_code": reading.failure_code,
                "locations": [p.locator for p in chosen], "origins": list(paper.origins),
                "content_sha256": reading.content_sha256, "parser": reading.parser,
            })
            if not chosen or len(self.sources) >= 16:
                continue
            body = "Content status: " + reading.status + "\n" + "\n\n".join(p.locator + "\n" + p.text for p in chosen)
            # One <=4096-character block per source keeps all selected sources
            # provenance-backed under the existing 64-block global contract.
            self.sources.append({"title": paper.title[:512], "url": paper.url, "raw_content": body[:4000] + "\n"})
        return self.get_research_context()

    def get_research_context(self):
        return [s["title"] + "\n" + s["raw_content"] for s in self.sources]

    def get_paper_candidates(self):
        # Do not let candidate.abstract overwrite acquired fulltext provenance.
        return ()

    def get_paper_screening_audit(self):
        return None

    def get_research_sources(self):
        return self.sources

    def get_source_urls(self):
        return [s["url"] for s in self.sources]


def create_library_researcher(request, topic_plan):
    providers = () if request.report_source == "local" else tuple(p.strip() for p in os.getenv("ACADEMIC_PAPER_PROVIDERS", ",".join(DEFAULT_PROVIDERS)).split(",") if p.strip())
    # Local document IDs are supplied by the application, never filesystem paths.
    if type(request.document_urls) is not tuple or any(type(value) is not str or not value.startswith("library:") for value in request.document_urls):
        raise LibraryError("invalid_document_selection")
    ids = tuple(value.removeprefix("library:") for value in request.document_urls)
    return AcademicPaperResearcher(request, topic_plan, providers=providers, document_ids=ids)
