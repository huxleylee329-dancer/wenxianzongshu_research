import logging
from collections.abc import Iterable
from datetime import datetime

import arxiv

from ...screening.models import ExternalIdentifier, PaperCandidate, build_candidate_id
from ..academic_utils import format_academic_body, normalize_doi


logger = logging.getLogger(__name__)
ARXIV_NUM_RETRIES = 3


class ArxivSearch:
    """
    Arxiv API Retriever
    """

    BODY_IS_PREFETCHED_CONTENT = True

    def __init__(self, query, sort="Relevance", query_domains=None):
        self.arxiv = arxiv
        self.query = query
        assert sort in ["Relevance", "SubmittedDate"], "Invalid sort criterion"
        self.sort = (
            arxiv.SortCriterion.SubmittedDate
            if sort == "SubmittedDate"
            else arxiv.SortCriterion.Relevance
        )

    def search(self, max_results=5):
        """
        Performs the search
        :param query:
        :param max_results:
        :return:
        """
        return [
            candidate.to_retriever_result()
            for candidate in self.search_candidates(max_results=max_results)
        ]

    def search_candidates(self, max_results=5):
        """Return immutable structured candidates in provider result order."""
        candidates = []

        try:
            client = self.arxiv.Client(num_retries=ARXIV_NUM_RETRIES)
            results = client.results(
                self.arxiv.Search(
                    query=self.query,
                    max_results=max_results,
                    sort_by=self.sort,
                )
            )

            for source_rank, result in enumerate(results, start=1):
                try:
                    candidate = self._normalize_candidate(result, source_rank)
                except Exception as exc:
                    logger.warning(
                        "arXiv result processing failure (%s)",
                        type(exc).__name__,
                    )
                    continue

                if candidate is not None:
                    candidates.append(candidate)
        except Exception as exc:
            logger.error("arXiv provider failure (%s)", type(exc).__name__)

        return candidates

    def _normalize_candidate(self, result, source_rank):
        title = result.title.strip() if isinstance(result.title, str) else ""
        href = result.entry_id.strip() if isinstance(result.entry_id, str) else ""
        abstract = result.summary.strip() if isinstance(result.summary, str) else ""
        if not title or not href or not abstract:
            return None

        raw_authors = getattr(result, "authors", None)
        authors = []
        if raw_authors and not isinstance(raw_authors, (str, bytes)):
            for author in raw_authors:
                name = getattr(author, "name", None)
                self._append_unique_text(authors, name)

        published = getattr(result, "published", None)
        published_year = self._normalize_year(getattr(published, "year", None))
        published_at = published if isinstance(published, datetime) else None
        updated = getattr(result, "updated", None)
        updated_at = updated if isinstance(updated, datetime) else None
        doi = normalize_doi(getattr(result, "doi", None))

        try:
            short_id_getter = getattr(result, "get_short_id", None)
            raw_short_id = short_id_getter() if callable(short_id_getter) else None
            source_record_id = self._optional_text(raw_short_id)
        except Exception:
            source_record_id = None

        categories = []
        self._append_unique_text(
            categories,
            getattr(result, "primary_category", None),
        )
        raw_categories = getattr(result, "categories", None)
        if isinstance(raw_categories, Iterable) and not isinstance(
            raw_categories, (str, bytes, dict)
        ):
            for category in raw_categories:
                self._append_unique_text(categories, category)

        external_ids = []
        if source_record_id:
            external_ids.append(
                ExternalIdentifier(name="arXiv", value=source_record_id)
            )
        if doi:
            external_ids.append(ExternalIdentifier(name="DOI", value=doi))

        body = format_academic_body(
            title=title,
            authors=tuple(authors),
            year=published_year,
            venue="arXiv",
            doi=doi,
            source="arXiv",
            abstract=abstract,
        )

        return PaperCandidate(
            candidate_id=build_candidate_id(
                source="arxiv",
                source_record_id=source_record_id,
                doi=doi,
                href=href,
            ),
            source="arxiv",
            source_record_id=source_record_id,
            retrieval_query=self.query,
            source_rank=source_rank,
            title=title,
            href=href,
            body=body,
            abstract=abstract,
            authors=tuple(authors),
            published_year=published_year,
            published_at=published_at,
            updated_at=updated_at,
            venue="arXiv",
            publication_venue_id=None,
            publication_venue_name=None,
            publication_venue_type=None,
            publication_venue_alternate_names=(),
            doi=doi,
            external_ids=tuple(external_ids),
            citation_count=None,
            publication_types=(),
            categories=tuple(categories),
            journal_reference=self._optional_text(
                getattr(result, "journal_ref", None)
            ),
        )

    @staticmethod
    def _optional_text(value):
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        return normalized or None

    @classmethod
    def _append_unique_text(cls, values, value):
        normalized = cls._optional_text(value)
        if normalized and normalized not in values:
            values.append(normalized)

    @staticmethod
    def _normalize_year(value):
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        return value if 1000 <= value <= 9999 else None
