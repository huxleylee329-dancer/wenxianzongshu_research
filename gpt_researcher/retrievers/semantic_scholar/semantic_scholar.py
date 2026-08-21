import logging
from typing import Dict, List

import requests

from ..academic_utils import format_academic_body


logger = logging.getLogger(__name__)


class SemanticScholarSearch:
    """
    Semantic Scholar API Retriever
    """

    BODY_IS_PREFETCHED_CONTENT = True

    BASE_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
    VALID_SORT_CRITERIA = ["relevance", "citationCount", "publicationDate"]
    REQUEST_TIMEOUT_SECONDS = 10

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
        params = {
            "query": self.query,
            "limit": max_results,
            "fields": "title,abstract,url,authors,year,venue,externalIds",
            "sort": self.sort,
        }

        try:
            response = requests.get(
                self.BASE_URL,
                params=params,
                timeout=self.REQUEST_TIMEOUT_SECONDS,
            )
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
            logger.error("Semantic Scholar HTTP failure (%s)", type(exc).__name__)
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

        search_result = []
        for result in results:
            try:
                normalized = self._normalize_result(result)
            except Exception as exc:
                logger.warning(
                    "Semantic Scholar result processing failure (%s)",
                    type(exc).__name__,
                )
                continue

            if normalized is not None:
                search_result.append(normalized)
                if len(search_result) >= max_results:
                    break

        return search_result

    @staticmethod
    def _normalize_result(result):
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
                if isinstance(name, str) and name.strip():
                    authors.append(name.strip())

        external_ids = result.get("externalIds")
        doi = external_ids.get("DOI") if isinstance(external_ids, dict) else None

        return {
            "title": title,
            "href": href,
            "body": format_academic_body(
                title=title,
                authors=authors,
                year=result.get("year"),
                venue=result.get("venue"),
                doi=doi,
                source="Semantic Scholar",
                abstract=abstract,
            ),
        }
