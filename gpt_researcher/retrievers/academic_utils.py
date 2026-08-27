"""Shared normalization helpers for academic retrievers."""

import re
from collections.abc import Iterable


MISSING_VALUE = "N/A"
_DOI_PREFIX = re.compile(
    r"^(?:doi:\s*|https?://(?:dx\.)?doi\.org/)",
    flags=re.IGNORECASE,
)


def normalize_doi(value: object) -> str | None:
    """Return a bare, lowercase DOI, or ``None`` when no DOI is usable."""
    if not isinstance(value, str):
        return None

    normalized = _DOI_PREFIX.sub("", value.strip(), count=1).strip().lower()
    return normalized or None


def _clean_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())


def _format_authors(authors: Iterable[object] | None) -> str:
    if authors is None or isinstance(authors, (str, bytes)):
        return MISSING_VALUE

    names = [_clean_text(author) for author in authors]
    names = [name for name in names if name]
    return "; ".join(names) or MISSING_VALUE


def _format_year(year: object) -> str:
    value = str(year).strip() if year is not None else ""
    return value if re.fullmatch(r"\d{4}", value) else MISSING_VALUE


def format_academic_body(
    *,
    title: str,
    authors: Iterable[object] | None,
    year: object,
    venue: object,
    doi: object,
    source: str,
    abstract: str,
) -> str:
    """Build the frozen academic metadata block followed by the abstract."""
    normalized_doi = normalize_doi(doi) or MISSING_VALUE
    normalized_venue = _clean_text(venue) or MISSING_VALUE

    return "\n".join(
        (
            f"Title: {_clean_text(title)}",
            f"Authors: {_format_authors(authors)}",
            f"Year: {_format_year(year)}",
            f"Venue: {normalized_venue}",
            f"DOI: {normalized_doi}",
            f"Source: {_clean_text(source)}",
            "",
            "Abstract:",
            _clean_text(abstract),
        )
    )
