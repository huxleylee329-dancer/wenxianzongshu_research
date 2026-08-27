import logging
import os
import re
from typing import Dict, List

import requests

from ...screening.models import ExternalIdentifier, PaperCandidate, build_candidate_id
from ..academic_utils import format_academic_body, normalize_doi


logger = logging.getLogger(__name__)


class SemanticScholarSearch:
    """
    Semantic Scholar API Retriever
    """

    BODY_IS_PREFETCHED_CONTENT = True

    BASE_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
    BULK_URL = f"{BASE_URL}/bulk"
    VALID_SORT_CRITERIA = ["relevance", "citationCount", "publicationDate"]
    REQUEST_TIMEOUT_SECONDS = 10
    JOURNAL_TOKEN_TO_VENUES = {
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

    def __init__(self, query: str, sort: str = "relevance", query_domains=None):
        """
        Initialize the SemanticScholarSearch class with a query and sort criterion.

        :param query: Search query string
        :param sort: Sort criterion ('relevance', 'citationCount', 'publicationDate')
        """
        self.query = query
        assert sort in self.VALID_SORT_CRITERIA, "Invalid sort criterion"
        # Preserve the exact (camelCase) criterion. The Semantic Scholar API
        # expects ``citationCount`` / ``publicationDate`` verbatim; lowercasing
        # them produced ``citationcount`` / ``publicationdate``, which the API
        # rejects or silently ignores.
        self.sort = sort

    def search(self, max_results: int = 20) -> List[Dict[str, str]]:
        """
        Perform the search on Semantic Scholar and return results.

        :param max_results: Maximum number of results to retrieve
        :return: List of dictionaries containing title, href, and body of each paper
        """
        return [
            candidate.to_retriever_result()
            for candidate in self.search_candidates(max_results=max_results)
        ]

    def search_candidates(self, max_results: int = 20) -> List[PaperCandidate]:
        """Perform one provider request and return structured paper candidates."""
        try:
            venue_filter = self._configured_venue_filter()
        except Exception as exc:
            logger.error(
                "Semantic Scholar journal configuration failure (%s)",
                type(exc).__name__,
            )
            return []

        if venue_filter == "":
            return []

        params = {
            "query": self.query,
            "limit": max_results,
            "fields": (
                "paperId,title,abstract,url,authors,year,venue,publicationVenue,"
                "citationCount,publicationTypes,externalIds"
            ),
        }
        if venue_filter is not None:
            params["venue"] = venue_filter
        request_url = self.BASE_URL
        if self.sort != "relevance":
            request_url = self.BULK_URL
            params["sort"] = f"{self.sort}:desc"

        raw_key = os.getenv("SEMANTIC_SCHOLAR_API_KEY", "")
        normalized_key = raw_key.strip()
        request_kwargs = {
            "params": params,
            "timeout": self.REQUEST_TIMEOUT_SECONDS,
        }
        if normalized_key:
            request_kwargs["headers"] = {"x-api-key": normalized_key}

        try:
            response = requests.get(request_url, **request_kwargs)
            response.raise_for_status()
        except requests.Timeout as exc:
            logger.error(
                "Semantic Scholar timeout failure (%s)", type(exc).__name__
            )
            return []
        except requests.ConnectionError as exc:
            logger.error(
                "Semantic Scholar connection failure (%s)", type(exc).__name__
            )
            return []
        except requests.HTTPError as exc:
            try:
                candidate_status = (
                    exc.response.status_code
                    if exc.response is not None
                    else "unknown"
                )
                status_code = (
                    candidate_status
                    if isinstance(candidate_status, int)
                    else "unknown"
                )
            except Exception:
                status_code = "unknown"
            logger.error("Semantic Scholar HTTP failure (status=%s)", status_code)
            return []
        except requests.RequestException as exc:
            logger.error(
                "Semantic Scholar provider failure (%s)", type(exc).__name__
            )
            return []
        except Exception as exc:
            logger.error(
                "Semantic Scholar unexpected provider failure (%s)",
                type(exc).__name__,
            )
            return []

        try:
            payload = response.json()
        except Exception as exc:
            logger.error(
                "Semantic Scholar invalid JSON failure (%s)", type(exc).__name__
            )
            return []

        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            logger.error("Semantic Scholar invalid response shape")
            return []
        results = payload["data"]

        candidates = []
        for source_rank, result in enumerate(results, start=1):
            try:
                candidate = self._normalize_candidate(result, source_rank)
            except Exception as exc:
                logger.warning(
                    "Semantic Scholar result processing failure (%s)",
                    type(exc).__name__,
                )
                continue

            if candidate is not None:
                candidates.append(candidate)
                if len(candidates) >= max_results:
                    break

        return candidates

    @classmethod
    def _configured_venue_filter(cls):
        raw_journals = os.getenv("SEMANTIC_SCHOLAR_JOURNALS", "")
        if not raw_journals.strip():
            return None

        tokens = []
        seen_tokens = set()
        for item in raw_journals.split(","):
            token = item.strip().lower()
            if not token or token in seen_tokens:
                continue
            seen_tokens.add(token)
            tokens.append(token)

        venues = []
        seen_venues = set()
        invalid_count = 0
        for token in tokens:
            token_venues = cls.JOURNAL_TOKEN_TO_VENUES.get(token)
            if token_venues is None:
                invalid_count += 1
                continue
            for venue in token_venues:
                if venue in seen_venues:
                    continue
                seen_venues.add(venue)
                venues.append(venue)

        if invalid_count or not venues:
            logger.warning(
                "Semantic Scholar journal configuration rejected entries "
                "(count=%d)",
                invalid_count,
            )

        if not venues:
            return ""
        return ",".join(venues)

    def _normalize_candidate(self, result, source_rank):
        if not isinstance(result, dict):
            return None

        title_value = result.get("title")
        href_value = result.get("url")
        abstract_value = result.get("abstract")
        title = title_value.strip() if isinstance(title_value, str) else ""
        href = href_value.strip() if isinstance(href_value, str) else ""
        abstract = (
            abstract_value.strip() if isinstance(abstract_value, str) else ""
        )
        if not title or not href or not abstract:
            return None

        raw_authors = result.get("authors")
        authors = []
        if isinstance(raw_authors, list):
            for author in raw_authors:
                if not isinstance(author, dict):
                    continue
                name = author.get("name")
                self._append_unique_text(authors, name)

        raw_external_ids = result.get("externalIds")
        raw_doi = (
            raw_external_ids.get("DOI")
            if isinstance(raw_external_ids, dict)
            else None
        )
        doi = normalize_doi(raw_doi)
        external_ids = self._normalize_external_ids(raw_external_ids, doi)
        published_year = self._normalize_year(result.get("year"))
        venue = self._optional_text(result.get("venue"))

        publication_venue = result.get("publicationVenue")
        if not isinstance(publication_venue, dict):
            publication_venue = {}

        source_record_id = self._optional_text(result.get("paperId"))
        citation_count = self._normalize_citation_count(
            result.get("citationCount")
        )
        publication_types = self._normalize_string_sequence(
            result.get("publicationTypes")
        )
        alternate_names = self._normalize_string_sequence(
            publication_venue.get("alternate_names")
        )

        body = format_academic_body(
            title=title,
            authors=tuple(authors),
            year=published_year,
            venue=venue,
            doi=doi,
            source="Semantic Scholar",
            abstract=abstract,
        )

        return PaperCandidate(
            candidate_id=build_candidate_id(
                source="semantic_scholar",
                source_record_id=source_record_id,
                doi=doi,
                href=href,
            ),
            source="semantic_scholar",
            source_record_id=source_record_id,
            retrieval_query=self.query,
            source_rank=source_rank,
            title=title,
            href=href,
            body=body,
            abstract=abstract,
            authors=tuple(authors),
            published_year=published_year,
            published_at=None,
            updated_at=None,
            venue=venue,
            publication_venue_id=self._optional_text(
                publication_venue.get("id")
            ),
            publication_venue_name=self._optional_text(
                publication_venue.get("name")
            ),
            publication_venue_type=self._optional_text(
                publication_venue.get("type")
            ),
            publication_venue_alternate_names=alternate_names,
            doi=doi,
            external_ids=external_ids,
            citation_count=citation_count,
            publication_types=publication_types,
            categories=(),
            journal_reference=None,
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

    @classmethod
    def _normalize_string_sequence(cls, values):
        if not isinstance(values, (list, tuple)):
            return ()
        normalized = []
        for value in values:
            cls._append_unique_text(normalized, value)
        return tuple(normalized)

    @classmethod
    def _normalize_external_ids(cls, values, doi):
        if not isinstance(values, dict):
            return ()

        identifiers = []
        seen_names = set()
        for raw_name, raw_value in values.items():
            name = cls._optional_text(raw_name)
            if not name or name in seen_names:
                continue
            value = doi if name == "DOI" else cls._optional_text(raw_value)
            if not value:
                continue
            seen_names.add(name)
            identifiers.append(ExternalIdentifier(name=name, value=value))
        return tuple(identifiers)

    @staticmethod
    def _normalize_year(value):
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value if 1000 <= value <= 9999 else None
        if isinstance(value, str):
            normalized = value.strip()
            if re.fullmatch(r"\d{4}", normalized):
                year = int(normalized)
                return year if 1000 <= year <= 9999 else None
        return None

    @staticmethod
    def _normalize_citation_count(value):
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        return value if value >= 0 else None
