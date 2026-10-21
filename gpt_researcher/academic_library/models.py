"""Records outside the bounded workflow DTOs; metadata is not evidence."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace
from urllib.parse import urlsplit

from gpt_researcher.retrievers.academic_utils import normalize_doi


class LibraryError(ValueError):
    """Public-safe classification, never provider text or a local file path."""


@dataclass(frozen=True)
class Paper:
    provider: str
    record_id: str
    title: str
    url: str
    abstract: str = ""
    doi: str | None = None
    year: int | None = None
    authors: tuple[str, ...] = ()
    publication_type: str = "unknown"
    fulltext_urls: tuple[str, ...] = ()
    license: str | None = None
    origins: tuple[str, ...] = ()

    @property
    def key(self) -> str:
        # Never merge merely similar (or identical) titles across providers.
        if self.doi:
            return "doi:" + self.doi
        return self.provider + ":" + self.record_id

    @property
    def content_status(self) -> str:
        return "abstract" if self.abstract else "metadata_only"


@dataclass(frozen=True)
class Passage:
    text: str
    locator: str


@dataclass(frozen=True)
class Reading:
    paper: Paper
    status: str
    passages: tuple[Passage, ...]
    content_sha256: str | None = None
    parser: str | None = None
    retrieved_url: str | None = None
    truncated: bool = False
    failure_code: str | None = None


def text(value: object, limit: int = 16_384) -> str:
    if type(value) is not str:
        return ""
    # API metadata sometimes embeds JATS/HTML. Never emit active markup.
    from bs4 import BeautifulSoup

    return " ".join(BeautifulSoup(value[: limit * 2], "html.parser").get_text(" ").split())[:limit]


def public_url(value: object) -> str:
    if type(value) is not str or len(value) > 4096:
        return ""
    try:
        parts = urlsplit(value.strip())
        if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
            return ""
        return value.strip()
    except ValueError:
        return ""


def doi(value: object) -> str | None:
    candidate = normalize_doi(value)
    if candidate and re.fullmatch(r"10\.\d{4,9}/\S{1,512}", candidate):
        return candidate
    return None


def deduplicate(papers: list[Paper]) -> list[Paper]:
    """Preserve discovery order and all origins/locations, not duplicate counts."""
    kept: dict[str, Paper] = {}
    for paper in papers:
        origins = paper.origins or (paper.provider + ":" + paper.record_id,)
        if paper.key not in kept:
            kept[paper.key] = replace(paper, origins=origins)
            continue
        old = kept[paper.key]
        kept[paper.key] = replace(
            old,
            abstract=old.abstract or paper.abstract,
            authors=old.authors or paper.authors,
            year=old.year or paper.year,
            fulltext_urls=tuple(dict.fromkeys(old.fulltext_urls + paper.fulltext_urls)),
            origins=tuple(dict.fromkeys(old.origins + origins)),
            # Do not transfer a licence to another provider's copy.
        )
    return list(kept.values())


def content_id(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
