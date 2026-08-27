"""Strict immutable models for deterministic paper screening decisions."""

import hashlib
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import PaperCandidate


class PaperType(str, Enum):
    JOURNAL = "journal"
    CONFERENCE = "conference"
    PREPRINT = "preprint"
    REVIEW = "review"
    BOOK_CHAPTER = "book_chapter"
    UNKNOWN = "unknown"


class UnknownValuePolicy(str, Enum):
    INCLUDE = "include"
    EXCLUDE = "exclude"


class ScreeningReasonCode(str, Enum):
    INCLUDED = "included"
    DUPLICATE_OF_CANONICAL = "duplicate_of_canonical"
    YEAR_BELOW_MIN = "year_below_min"
    YEAR_ABOVE_MAX = "year_above_max"
    YEAR_UNKNOWN = "year_unknown"
    TYPE_NOT_ALLOWED = "type_not_allowed"
    TYPE_UNKNOWN = "type_unknown"


class CandidateOccurrence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    occurrence_id: str
    retrieval_request_id: str
    planning_only: bool
    candidate: PaperCandidate

    @field_validator("occurrence_id", "retrieval_request_id")
    @classmethod
    def _strip_nonblank_identifier(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("occurrence identifiers must not be blank")
        return normalized


class ScreeningPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    min_year: Annotated[int, Field(ge=1000, le=9999)] | None = None
    max_year: Annotated[int, Field(ge=1000, le=9999)] | None = None
    unknown_year: UnknownValuePolicy = UnknownValuePolicy.INCLUDE
    allowed_paper_types: tuple[PaperType, ...] | None = None
    unknown_paper_type: UnknownValuePolicy = UnknownValuePolicy.INCLUDE

    @model_validator(mode="after")
    def _validate_policy(self) -> "ScreeningPolicy":
        if (
            self.min_year is not None
            and self.max_year is not None
            and self.min_year > self.max_year
        ):
            raise ValueError("min_year must not exceed max_year")
        if self.allowed_paper_types is not None:
            if len(set(self.allowed_paper_types)) != len(self.allowed_paper_types):
                raise ValueError("allowed_paper_types must not contain duplicates")
            if PaperType.UNKNOWN in self.allowed_paper_types:
                raise ValueError("unknown paper type uses unknown_paper_type policy")
        return self


class ScreeningDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    decision_order: Annotated[int, Field(gt=0)]
    occurrence_id: str
    retrieval_request_id: str
    candidate_id: str
    source: Literal["arxiv", "semantic_scholar"]
    retrieval_query: str
    source_rank: Annotated[int, Field(gt=0)]
    included: bool
    primary_reason: ScreeningReasonCode
    matched_rules: tuple[ScreeningReasonCode, ...]
    classified_type: PaperType
    type_evidence: tuple[str, ...]
    duplicate_group_id: str
    duplicate_key_kind: Literal["doi", "title", "singleton"]
    canonical_occurrence_id: str | None
    canonical_candidate_id: str | None

    @field_validator(
        "occurrence_id",
        "retrieval_request_id",
        "duplicate_group_id",
    )
    @classmethod
    def _strip_nonblank_string(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("decision identifiers must not be blank")
        return normalized

    @field_validator("candidate_id", "retrieval_query")
    @classmethod
    def _reject_blank_exact_candidate_value(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("decision candidate values must not be blank")
        return value

    @field_validator("canonical_occurrence_id", "canonical_candidate_id")
    @classmethod
    def _strip_optional_canonical_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("canonical identifiers must not be blank")
        return normalized

    @field_validator("type_evidence")
    @classmethod
    def _validate_type_evidence(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if not values or any(not value.strip() for value in values):
            raise ValueError("type_evidence must contain nonblank values")
        if len(set(values)) != len(values):
            raise ValueError("type_evidence must not contain duplicates")
        for value in values:
            if value in {"source:arxiv", "none"}:
                continue
            prefix, separator, token = value.partition(":")
            if (
                separator != ":"
                or prefix not in {"publication_types", "publication_venue_type"}
                or not token
                or token != token.strip().casefold()
            ):
                raise ValueError("type_evidence contains an invalid value")
        if "none" in values and values != ("none",):
            raise ValueError("none type evidence must be the sole value")
        return values

    @field_validator("matched_rules")
    @classmethod
    def _validate_matched_rules(
        cls, values: tuple[ScreeningReasonCode, ...]
    ) -> tuple[ScreeningReasonCode, ...]:
        if not values or len(set(values)) != len(values):
            raise ValueError("matched_rules must be nonempty and unique")
        return values

    @model_validator(mode="after")
    def _validate_decision_invariants(self) -> "ScreeningDecision":
        reason_order = {
            ScreeningReasonCode.YEAR_BELOW_MIN: 0,
            ScreeningReasonCode.YEAR_ABOVE_MAX: 1,
            ScreeningReasonCode.YEAR_UNKNOWN: 2,
            ScreeningReasonCode.TYPE_NOT_ALLOWED: 3,
            ScreeningReasonCode.TYPE_UNKNOWN: 4,
            ScreeningReasonCode.DUPLICATE_OF_CANONICAL: 5,
        }
        if self.included:
            if self.primary_reason is not ScreeningReasonCode.INCLUDED:
                raise ValueError("included decisions require INCLUDED reason")
            if self.matched_rules != (ScreeningReasonCode.INCLUDED,):
                raise ValueError("included decisions contain only INCLUDED")
            if self.canonical_occurrence_id != self.occurrence_id:
                raise ValueError("included occurrence must be canonical")
            if self.canonical_candidate_id != self.candidate_id:
                raise ValueError("included candidate must be canonical")
        else:
            if self.primary_reason is ScreeningReasonCode.INCLUDED:
                raise ValueError("excluded decisions cannot use INCLUDED")
            if ScreeningReasonCode.INCLUDED in self.matched_rules:
                raise ValueError("excluded decisions cannot match INCLUDED")
            if self.primary_reason is not self.matched_rules[0]:
                raise ValueError("primary reason must be first matched rule")
            if tuple(sorted(self.matched_rules, key=reason_order.__getitem__)) != (
                self.matched_rules
            ):
                raise ValueError("matched_rules use the frozen order")
        if (self.canonical_occurrence_id is None) != (
            self.canonical_candidate_id is None
        ):
            raise ValueError("canonical occurrence and candidate must align")
        if self.canonical_occurrence_id is None:
            if ScreeningReasonCode.DUPLICATE_OF_CANONICAL in self.matched_rules:
                raise ValueError("duplicates require an included canonical")
        elif not self.included:
            if self.canonical_occurrence_id == self.occurrence_id:
                raise ValueError("an excluded occurrence cannot be canonical")
            if ScreeningReasonCode.DUPLICATE_OF_CANONICAL not in self.matched_rules:
                raise ValueError("noncanonical occurrences must match duplicate rule")
        return self


class DuplicateGroup(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    group_id: str
    key_kind: Literal["doi", "title", "singleton"]
    key_value: str
    member_occurrence_ids: tuple[str, ...]
    reference_occurrence_id: str
    canonical_occurrence_id: str | None

    @field_validator("group_id", "key_value", "reference_occurrence_id")
    @classmethod
    def _strip_nonblank_string(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("duplicate-group values must not be blank")
        return normalized

    @field_validator("member_occurrence_ids")
    @classmethod
    def _validate_members(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(value.strip() for value in values)
        if not normalized or any(not value for value in normalized):
            raise ValueError("duplicate groups require nonblank members")
        if len(set(normalized)) != len(normalized):
            raise ValueError("duplicate-group members must be unique")
        return normalized

    @model_validator(mode="after")
    def _validate_group_references(self) -> "DuplicateGroup":
        members = set(self.member_occurrence_ids)
        if self.reference_occurrence_id not in members:
            raise ValueError("group reference must be a member")
        if (
            self.canonical_occurrence_id is not None
            and self.canonical_occurrence_id not in members
        ):
            raise ValueError("group canonical must be a member")
        digest = hashlib.sha256(
            f"{self.key_kind}:{self.key_value}".encode("utf-8")
        ).hexdigest()
        if self.group_id != f"group:sha256:{digest}":
            raise ValueError("group id must use the frozen SHA-256 identity")
        return self


class RetrievalRequestRoute(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    retrieval_request_id: str
    canonical_occurrence_ids: tuple[str, ...]

    @field_validator("retrieval_request_id")
    @classmethod
    def _strip_nonblank_identifier(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("retrieval request id must not be blank")
        return normalized

    @field_validator("canonical_occurrence_ids")
    @classmethod
    def _validate_canonical_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(value.strip() for value in values)
        if any(not value for value in normalized):
            raise ValueError("route canonical ids must not be blank")
        if len(set(normalized)) != len(normalized):
            raise ValueError("route canonical ids must be unique")
        return normalized


class ScreeningResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    occurrences: tuple[CandidateOccurrence, ...]
    decisions: tuple[ScreeningDecision, ...]
    duplicate_groups: tuple[DuplicateGroup, ...]
    included_canonical_occurrence_ids: tuple[str, ...]
    routes: tuple[RetrievalRequestRoute, ...]

    @model_validator(mode="after")
    def _validate_cross_references(self) -> "ScreeningResult":
        occurrence_ids = tuple(item.occurrence_id for item in self.occurrences)
        occurrence_by_id = {item.occurrence_id: item for item in self.occurrences}
        if len(set(occurrence_ids)) != len(occurrence_ids):
            raise ValueError("result occurrence ids must be unique")
        if tuple(item.occurrence_id for item in self.decisions) != occurrence_ids:
            raise ValueError("decisions must align with occurrences")
        if tuple(item.decision_order for item in self.decisions) != tuple(
            range(1, len(self.decisions) + 1)
        ):
            raise ValueError("decision order must be contiguous")

        grouped = tuple(
            occurrence_id
            for group in self.duplicate_groups
            for occurrence_id in group.member_occurrence_ids
        )
        if len(grouped) != len(set(grouped)) or set(grouped) != set(occurrence_ids):
            raise ValueError("groups must partition occurrences")

        group_ids = {group.group_id for group in self.duplicate_groups}
        if len(group_ids) != len(self.duplicate_groups):
            raise ValueError("duplicate group ids must be unique")
        if any(decision.duplicate_group_id not in group_ids for decision in self.decisions):
            raise ValueError("decision group references must resolve")

        group_by_occurrence = {
            occurrence_id: group
            for group in self.duplicate_groups
            for occurrence_id in group.member_occurrence_ids
        }
        for decision in self.decisions:
            occurrence = occurrence_by_id[decision.occurrence_id]
            expected_occurrence_fields = (
                occurrence.retrieval_request_id,
                occurrence.candidate.candidate_id,
                occurrence.candidate.source,
                occurrence.candidate.retrieval_query,
                occurrence.candidate.source_rank,
            )
            decision_occurrence_fields = (
                decision.retrieval_request_id,
                decision.candidate_id,
                decision.source,
                decision.retrieval_query,
                decision.source_rank,
            )
            if decision_occurrence_fields != expected_occurrence_fields:
                raise ValueError("decision fields must match its occurrence")
            group = group_by_occurrence[decision.occurrence_id]
            if (
                decision.duplicate_group_id != group.group_id
                or decision.duplicate_key_kind != group.key_kind
                or decision.canonical_occurrence_id != group.canonical_occurrence_id
            ):
                raise ValueError("decision duplicate references must match its group")
            if group.canonical_occurrence_id is None:
                expected_candidate_id = None
            else:
                expected_candidate_id = occurrence_by_id[
                    group.canonical_occurrence_id
                ].candidate.candidate_id
            if decision.canonical_candidate_id != expected_candidate_id:
                raise ValueError("decision canonical candidate must resolve")

        included = tuple(
            group.canonical_occurrence_id
            for group in self.duplicate_groups
            if group.canonical_occurrence_id is not None
        )
        if self.included_canonical_occurrence_ids != included:
            raise ValueError("included canonical ids must follow group order")
        included_decisions = {
            decision.occurrence_id for decision in self.decisions if decision.included
        }
        if included_decisions != set(included):
            raise ValueError("included decisions must equal group canonicals")

        requests = tuple(sorted({item.retrieval_request_id for item in self.occurrences}))
        if tuple(route.retrieval_request_id for route in self.routes) != requests:
            raise ValueError("routes must cover requests in sorted order")
        included_set = set(self.included_canonical_occurrence_ids)
        if any(
            canonical_id not in included_set
            for route in self.routes
            for canonical_id in route.canonical_occurrence_ids
        ):
            raise ValueError("route targets must be included canonicals")
        request_kinds: dict[str, bool] = {}
        for occurrence in self.occurrences:
            previous = request_kinds.setdefault(
                occurrence.retrieval_request_id, occurrence.planning_only
            )
            if previous is not occurrence.planning_only:
                raise ValueError("result requests must have one planning kind")
        for route in self.routes:
            expected: list[str] = []
            if not request_kinds[route.retrieval_request_id]:
                for group in self.duplicate_groups:
                    if group.canonical_occurrence_id is None:
                        continue
                    if any(
                        occurrence_by_id[member_id].retrieval_request_id
                        == route.retrieval_request_id
                        and not occurrence_by_id[member_id].planning_only
                        for member_id in group.member_occurrence_ids
                    ):
                        expected.append(group.canonical_occurrence_id)
            if route.canonical_occurrence_ids != tuple(expected):
                raise ValueError("route does not match request group evidence")
        return self
