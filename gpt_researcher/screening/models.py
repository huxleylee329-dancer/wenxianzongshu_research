"""Immutable internal models for academic paper candidates."""

import hashlib
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ExternalIdentifier(BaseModel):
    """A minimal immutable provider identifier."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    name: str
    value: str

    @field_validator("name", "value")
    @classmethod
    def _strip_nonblank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("identifier values must not be blank")
        return normalized


class PaperCandidate(BaseModel):
    """Structured provider data retained before retriever projection."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    candidate_id: str
    source: Literal["arxiv", "semantic_scholar"]
    source_record_id: str | None
    retrieval_query: str
    source_rank: Annotated[int, Field(gt=0)]
    title: str
    href: str
    body: str
    abstract: str
    authors: tuple[str, ...] = ()
    published_year: Annotated[int, Field(ge=1000, le=9999)] | None = None
    published_at: datetime | None = None
    updated_at: datetime | None = None
    venue: str | None = None
    publication_venue_id: str | None = None
    publication_venue_name: str | None = None
    publication_venue_type: str | None = None
    publication_venue_alternate_names: tuple[str, ...] = ()
    doi: str | None = None
    external_ids: tuple[ExternalIdentifier, ...] = ()
    citation_count: Annotated[int, Field(ge=0)] | None = None
    publication_types: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()
    journal_reference: str | None = None

    @field_validator("candidate_id", "title", "href", "abstract")
    @classmethod
    def _reject_blank_required_strings(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("required candidate values must not be blank")
        return value

    def to_retriever_result(self) -> dict[str, str]:
        """Project to the established exact three-key retriever contract."""
        return {
            "title": self.title,
            "href": self.href,
            "body": self.body,
        }


def build_candidate_id(
    *,
    source: Literal["arxiv", "semantic_scholar"],
    source_record_id: str | None,
    doi: str | None,
    href: str,
) -> str:
    """Build the frozen deterministic provider-scoped candidate identity."""
    normalized_record_id = (
        source_record_id.strip() if isinstance(source_record_id, str) else ""
    )
    if normalized_record_id:
        return f"{source}:{normalized_record_id}"

    normalized_doi = doi.strip() if isinstance(doi, str) else ""
    if normalized_doi:
        return f"{source}:doi:{normalized_doi}"

    digest = hashlib.sha256(href.encode("utf-8")).hexdigest()
    return f"{source}:href-sha256:{digest}"
