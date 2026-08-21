import logging

import arxiv

from ..academic_utils import format_academic_body


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

        search_result = []

        try:
            client = self.arxiv.Client(num_retries=ARXIV_NUM_RETRIES)
            results = client.results(
                self.arxiv.Search(
                    query=self.query,
                    max_results=max_results,
                    sort_by=self.sort,
                )
            )

            for result in results:
                try:
                    normalized = self._normalize_result(result)
                except Exception as exc:
                    logger.warning(
                        "arXiv result processing failure (%s)",
                        type(exc).__name__,
                    )
                    continue

                if normalized is not None:
                    search_result.append(normalized)
        except Exception as exc:
            logger.error("arXiv provider failure (%s)", type(exc).__name__)

        return search_result

    @staticmethod
    def _normalize_result(result):
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
                if isinstance(name, str) and name.strip():
                    authors.append(name.strip())

        published = getattr(result, "published", None)
        year = getattr(published, "year", None)
        doi = getattr(result, "doi", None)

        return {
            "title": title,
            "href": href,
            "body": format_academic_body(
                title=title,
                authors=authors,
                year=year,
                venue="arXiv",
                doi=doi,
                source="arXiv",
                abstract=abstract,
            ),
        }
