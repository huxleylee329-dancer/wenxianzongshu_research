"""Strict immutable audit snapshots for screened Basic/Web research runs."""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .decisions import (
    PaperType,
    ScreeningPolicy,
    ScreeningReasonCode,
    ScreeningResult,
)
from .relevance import (
    TopicRelevanceDecision,
    TopicRelevanceReasonCode,
    TopicRelevanceVerdict,
    TopicScreeningResult,
)


AUDIT_UNAVAILABLE_MESSAGE = (
    "paper screening audit is available only after successful finalization"
)


class AuditCollectorState(str, Enum):
    OPEN = "open"
    FINALIZED = "finalized"
    ABORTED = "aborted"


class AuditRoutingStatus(str, Enum):
    EXCLUDED = "excluded"
    ROUTED = "routed"
    PLANNING_ONLY = "planning_only"


class AuditExclusionReason(str, Enum):
    DUPLICATE_OF_CANONICAL = "duplicate_of_canonical"
    YEAR_BELOW_MIN = "year_below_min"
    YEAR_ABOVE_MAX = "year_above_max"
    YEAR_UNKNOWN = "year_unknown"
    TYPE_NOT_ALLOWED = "type_not_allowed"
    TYPE_UNKNOWN = "type_unknown"
    TOPIC_IRRELEVANT = "topic_irrelevant"


class ProviderWarningCategory(str, Enum):
    CALL = "call"
    MATERIALIZATION = "materialization"
    CONTRACT = "contract"


_WARNING_ORDER = {
    ProviderWarningCategory.CALL: 0,
    ProviderWarningCategory.MATERIALIZATION: 1,
    ProviderWarningCategory.CONTRACT: 2,
}

_DETERMINISTIC_EXCLUSION_MAP = {
    ScreeningReasonCode.DUPLICATE_OF_CANONICAL:
        AuditExclusionReason.DUPLICATE_OF_CANONICAL,
    ScreeningReasonCode.YEAR_BELOW_MIN: AuditExclusionReason.YEAR_BELOW_MIN,
    ScreeningReasonCode.YEAR_ABOVE_MAX: AuditExclusionReason.YEAR_ABOVE_MAX,
    ScreeningReasonCode.YEAR_UNKNOWN: AuditExclusionReason.YEAR_UNKNOWN,
    ScreeningReasonCode.TYPE_NOT_ALLOWED: AuditExclusionReason.TYPE_NOT_ALLOWED,
    ScreeningReasonCode.TYPE_UNKNOWN: AuditExclusionReason.TYPE_UNKNOWN,
}

_TOPIC_REASON_COMPATIBILITY = {
    TopicRelevanceVerdict.RELEVANT: {
        TopicRelevanceReasonCode.DIRECT_TOPIC_MATCH,
        TopicRelevanceReasonCode.METHOD_OR_DATASET_MATCH,
        TopicRelevanceReasonCode.SUPPORTING_CONTEXT,
    },
    TopicRelevanceVerdict.IRRELEVANT: {
        TopicRelevanceReasonCode.OUT_OF_SCOPE,
    },
    TopicRelevanceVerdict.UNCERTAIN: {
        TopicRelevanceReasonCode.INSUFFICIENT_EVIDENCE,
        TopicRelevanceReasonCode.LLM_INVALID_OUTPUT,
        TopicRelevanceReasonCode.LLM_TIMEOUT,
        TopicRelevanceReasonCode.LLM_FAILURE,
    },
}


def _strip_nonblank(value: str, label: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{label} must not be blank")
    return normalized


class _StrictAuditModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class PaperScreeningOccurrenceRef(_StrictAuditModel):
    web_pass_id: str
    occurrence_id: str

    @field_validator("web_pass_id", "occurrence_id")
    @classmethod
    def _normalize_ids(cls, value: str) -> str:
        return _strip_nonblank(value, "occurrence reference")


class PaperScreeningWebPassRef(_StrictAuditModel):
    web_pass_order: Annotated[int, Field(gt=0)]
    web_pass_id: str

    @field_validator("web_pass_id")
    @classmethod
    def _normalize_id(cls, value: str) -> str:
        return _strip_nonblank(value, "web pass id")


class PaperScreeningProviderWarning(_StrictAuditModel):
    retriever_index: Annotated[int, Field(gt=0)]
    source_identifier: str
    category: ProviderWarningCategory

    @field_validator("source_identifier")
    @classmethod
    def _normalize_source(cls, value: str) -> str:
        return _strip_nonblank(value, "provider warning source")


def build_paper_screening_provider_warning(
    retriever: object | None,
    retriever_index: int,
    category: ProviderWarningCategory,
) -> PaperScreeningProviderWarning:
    """Build safe warning identity without rendering instance or exception data."""
    source_identifier = "unknown_retriever"
    if retriever is not None:
        try:
            retriever_type = type(retriever)
            module = retriever_type.__module__
            qualname = retriever_type.__qualname__
            if (
                isinstance(module, str)
                and isinstance(qualname, str)
                and module.strip()
                and qualname.strip()
            ):
                source_identifier = f"{module.strip()}.{qualname.strip()}"
        except Exception:
            source_identifier = "unknown_retriever"
    return PaperScreeningProviderWarning(
        retriever_index=retriever_index,
        source_identifier=source_identifier,
        category=category,
    )


class PaperScreeningRequestMetadata(_StrictAuditModel):
    retrieval_request_id: str
    planning_only: bool
    retrieval_query: str
    provider_warnings: tuple[PaperScreeningProviderWarning, ...]

    @field_validator("retrieval_request_id")
    @classmethod
    def _normalize_request_id(cls, value: str) -> str:
        return _strip_nonblank(value, "request id")

    @field_validator("retrieval_query")
    @classmethod
    def _require_query(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("retrieval query must not be blank")
        return value

    @field_validator("provider_warnings")
    @classmethod
    def _validate_warning_order(
        cls, values: tuple[PaperScreeningProviderWarning, ...]
    ) -> tuple[PaperScreeningProviderWarning, ...]:
        expected = tuple(
            sorted(values, key=lambda item: (item.retriever_index, _WARNING_ORDER[item.category]))
        )
        if values != expected:
            raise ValueError("provider warnings use frozen index/category order")
        if len(set(values)) != len(values):
            raise ValueError("provider warnings must not contain duplicates")
        return values


class _AuditEntryBase(_StrictAuditModel):
    web_pass_id: str
    occurrence_id: str
    candidate_id: str
    title: str
    href: str
    doi: str | None
    source: str
    source_rank: Annotated[int, Field(gt=0)]
    published_year: Annotated[int, Field(ge=1000, le=9999)] | None
    venue: str | None
    classified_type: PaperType
    duplicate_group_id: str
    duplicate_key_kind: Literal["doi", "title", "singleton"]
    canonical_occurrence_id: str | None
    is_canonical: bool
    planning_only: bool
    originating_request_id: str
    routed_request_ids: tuple[str, ...]
    deterministic_included: bool
    deterministic_primary_reason: ScreeningReasonCode
    deterministic_matched_rules: tuple[ScreeningReasonCode, ...]
    topic_verdict: TopicRelevanceVerdict | None
    topic_reason_code: TopicRelevanceReasonCode | None
    topic_rationale: str | None
    topic_confidence: Annotated[int, Field(ge=0, le=100)] | None
    screening_included: bool
    routed_to_evidence: bool
    routing_status: AuditRoutingStatus
    final_exclusion_reasons: tuple[AuditExclusionReason, ...]

    @field_validator(
        "web_pass_id",
        "occurrence_id",
        "candidate_id",
        "duplicate_group_id",
        "originating_request_id",
    )
    @classmethod
    def _normalize_identifiers(cls, value: str) -> str:
        return _strip_nonblank(value, "audit entry identifier")

    @field_validator("title", "href", "source")
    @classmethod
    def _require_nonblank_value(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("audit entry values must not be blank")
        return value

    @field_validator("canonical_occurrence_id", "doi", "venue")
    @classmethod
    def _normalize_optional(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("routed_request_ids")
    @classmethod
    def _validate_routed_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(_strip_nonblank(value, "routed request id") for value in values)
        if len(set(normalized)) != len(normalized):
            raise ValueError("routed request ids must be unique")
        return normalized

    @field_validator("final_exclusion_reasons")
    @classmethod
    def _validate_final_reasons(
        cls, values: tuple[AuditExclusionReason, ...]
    ) -> tuple[AuditExclusionReason, ...]:
        if len(set(values)) != len(values):
            raise ValueError("final exclusion reasons must be unique")
        return values

    @model_validator(mode="after")
    def _validate_entry(self) -> "_AuditEntryBase":
        topic_values = (
            self.topic_verdict,
            self.topic_reason_code,
            self.topic_rationale,
            self.topic_confidence,
        )
        if any(value is None for value in topic_values) and any(
            value is not None for value in topic_values
        ):
            raise ValueError("topic audit fields must be all present or all absent")
        if self.topic_rationale is not None:
            normalized = self.topic_rationale.strip()
            if not 1 <= len(normalized) <= 500 or normalized != self.topic_rationale:
                raise ValueError("topic rationale must be validated and normalized")
            if self.topic_reason_code not in _TOPIC_REASON_COMPATIBILITY[
                self.topic_verdict
            ]:
                raise ValueError("topic verdict and reason code are incompatible")
        if self.is_canonical != (self.canonical_occurrence_id == self.occurrence_id):
            raise ValueError("canonical entry identity is inconsistent")
        if self.deterministic_included != self.is_canonical:
            raise ValueError("deterministic inclusion must mirror canonical status")
        if self.deterministic_included:
            if (
                self.deterministic_primary_reason is not ScreeningReasonCode.INCLUDED
                or self.deterministic_matched_rules
                != (ScreeningReasonCode.INCLUDED,)
            ):
                raise ValueError("included entry deterministic reasons are invalid")
        elif (
            not self.deterministic_matched_rules
            or self.deterministic_primary_reason
            is not self.deterministic_matched_rules[0]
            or ScreeningReasonCode.INCLUDED in self.deterministic_matched_rules
        ):
            raise ValueError("excluded entry deterministic reasons are invalid")
        expected_screening_included = self.deterministic_included and (
            self.topic_verdict is not TopicRelevanceVerdict.IRRELEVANT
        )
        if self.screening_included is not expected_screening_included:
            raise ValueError("screening inclusion does not mirror decisions")
        expected_final_reasons = (
            tuple(
                _DETERMINISTIC_EXCLUSION_MAP[reason]
                for reason in self.deterministic_matched_rules
            )
            if not self.deterministic_included
            else (
                (AuditExclusionReason.TOPIC_IRRELEVANT,)
                if self.topic_verdict is TopicRelevanceVerdict.IRRELEVANT
                else ()
            )
        )
        if self.final_exclusion_reasons != expected_final_reasons:
            raise ValueError("final exclusion reasons do not mirror decisions")
        if not self.is_canonical and self.routed_request_ids:
            raise ValueError("noncanonical entries cannot be routed")
        if self.routed_to_evidence != bool(self.routed_request_ids):
            raise ValueError("routed_to_evidence must mirror routed request ids")
        if self.routing_status is AuditRoutingStatus.EXCLUDED:
            if self.screening_included or self.routed_request_ids or self.routed_to_evidence:
                raise ValueError("excluded entries cannot be included or routed")
            if not self.final_exclusion_reasons:
                raise ValueError("excluded entries require exclusion reasons")
        elif self.routing_status is AuditRoutingStatus.ROUTED:
            if not self.screening_included or not self.routed_request_ids or not self.routed_to_evidence:
                raise ValueError("routed entries must be included and routed")
            if self.final_exclusion_reasons:
                raise ValueError("routed entries cannot have exclusion reasons")
        elif self.routing_status is AuditRoutingStatus.PLANNING_ONLY:
            if (
                not self.screening_included
                or self.routed_request_ids
                or self.routed_to_evidence
                or not self.planning_only
            ):
                raise ValueError("planning-only routing status is inconsistent")
            if self.final_exclusion_reasons:
                raise ValueError("planning-only entries cannot have exclusion reasons")
        return self


class PaperScreeningAuditEntry(_AuditEntryBase):
    audit_order: Annotated[int, Field(gt=0)]


class _PassAuditEntry(_AuditEntryBase):
    pass_occurrence_order: Annotated[int, Field(gt=0)]


class _GroupAuditBase(_StrictAuditModel):
    web_pass_id: str
    group_id: str
    key_kind: Literal["doi", "title", "singleton"]
    member_occurrence_ids: tuple[str, ...]
    reference_occurrence_id: str
    canonical_occurrence_id: str | None
    canonical_candidate_id: str | None
    duplicate_count: Annotated[int, Field(ge=0)]
    deterministic_included: bool
    topic_decision: TopicRelevanceDecision | None
    screening_included: bool
    routed_request_ids: tuple[str, ...]
    planning_only_group: bool

    @field_validator("web_pass_id", "group_id", "reference_occurrence_id")
    @classmethod
    def _normalize_ids(cls, value: str) -> str:
        return _strip_nonblank(value, "group audit identifier")

    @field_validator("member_occurrence_ids", "routed_request_ids")
    @classmethod
    def _normalize_id_tuple(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(_strip_nonblank(value, "group audit reference") for value in values)
        if len(set(normalized)) != len(normalized):
            raise ValueError("group audit references must be unique")
        return normalized

    @field_validator("canonical_occurrence_id", "canonical_candidate_id")
    @classmethod
    def _normalize_optional_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _strip_nonblank(value, "canonical group identifier")

    @model_validator(mode="after")
    def _validate_group(self) -> "_GroupAuditBase":
        if self.duplicate_count != len(self.member_occurrence_ids) - 1:
            raise ValueError("duplicate_count must equal members minus one")
        if self.reference_occurrence_id not in self.member_occurrence_ids:
            raise ValueError("group reference must be a member")
        if self.canonical_occurrence_id is not None and (
            self.canonical_occurrence_id not in self.member_occurrence_ids
        ):
            raise ValueError("group canonical must be a member")
        if (self.canonical_occurrence_id is None) != (
            self.canonical_candidate_id is None
        ):
            raise ValueError("group canonical identifiers must align")
        if self.deterministic_included != (self.canonical_occurrence_id is not None):
            raise ValueError("group deterministic inclusion must mirror canonical")
        if self.topic_decision is not None:
            if (
                self.topic_decision.duplicate_group_id != self.group_id
                or self.topic_decision.canonical_occurrence_id
                != self.canonical_occurrence_id
                or self.topic_decision.canonical_candidate_id
                != self.canonical_candidate_id
            ):
                raise ValueError("group topic decision identifiers must mirror")
        if self.screening_included != (
            self.deterministic_included
            and (
                self.topic_decision is None
                or self.topic_decision.verdict is not TopicRelevanceVerdict.IRRELEVANT
            )
        ):
            raise ValueError("group screening inclusion is inconsistent")
        if not self.screening_included and self.routed_request_ids:
            raise ValueError("excluded groups cannot be routed")
        return self


class DuplicateGroupAudit(_GroupAuditBase):
    group_order: Annotated[int, Field(gt=0)]


class _PassGroupAudit(_GroupAuditBase):
    pass_group_order: Annotated[int, Field(gt=0)]


class _RequestAuditBase(_StrictAuditModel):
    web_pass_id: str
    retrieval_request_id: str
    planning_only: bool
    retrieval_query: str
    occurrence_ids: tuple[str, ...]
    deterministic_route_present: bool
    deterministic_canonical_occurrence_ids: tuple[str, ...]
    effective_route_present: bool
    effective_canonical_occurrence_ids: tuple[str, ...]
    provider_warnings: tuple[PaperScreeningProviderWarning, ...]

    @field_validator("web_pass_id", "retrieval_request_id")
    @classmethod
    def _normalize_ids(cls, value: str) -> str:
        return _strip_nonblank(value, "request audit identifier")

    @field_validator("retrieval_query")
    @classmethod
    def _require_query(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("request audit query must not be blank")
        return value

    @field_validator(
        "occurrence_ids",
        "deterministic_canonical_occurrence_ids",
        "effective_canonical_occurrence_ids",
    )
    @classmethod
    def _normalize_references(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(_strip_nonblank(value, "request audit occurrence") for value in values)
        if len(set(normalized)) != len(normalized):
            raise ValueError("request audit occurrence references must be unique")
        return normalized

    @field_validator("provider_warnings")
    @classmethod
    def _validate_warning_order(
        cls, values: tuple[PaperScreeningProviderWarning, ...]
    ) -> tuple[PaperScreeningProviderWarning, ...]:
        expected = tuple(
            sorted(
                values,
                key=lambda item: (
                    item.retriever_index,
                    _WARNING_ORDER[item.category],
                ),
            )
        )
        if values != expected:
            raise ValueError("provider warnings use frozen index/category order")
        if len(set(values)) != len(values):
            raise ValueError("provider warnings must not contain duplicates")
        return values

    @model_validator(mode="after")
    def _validate_routes(self) -> "_RequestAuditBase":
        if not self.deterministic_route_present and self.deterministic_canonical_occurrence_ids:
            raise ValueError("missing deterministic route must have empty ids")
        if not self.effective_route_present and self.effective_canonical_occurrence_ids:
            raise ValueError("missing effective route must have empty ids")
        if not self.occurrence_ids and (
            self.deterministic_route_present or self.effective_route_present
        ):
            raise ValueError("requests without occurrences cannot fabricate routes")
        return self


class PaperScreeningRequestAudit(_RequestAuditBase):
    request_order: Annotated[int, Field(gt=0)]


class _PassRequestAudit(_RequestAuditBase):
    pass_request_order: Annotated[int, Field(gt=0)]


class PaperScreeningAuditSummary(_StrictAuditModel):
    total_occurrences: Annotated[int, Field(ge=0)]
    duplicate_occurrences: Annotated[int, Field(ge=0)]
    deterministically_included_occurrences: Annotated[int, Field(ge=0)]
    deterministically_excluded_occurrences: Annotated[int, Field(ge=0)]
    excluded_by_year_occurrences: Annotated[int, Field(ge=0)]
    excluded_by_type_occurrences: Annotated[int, Field(ge=0)]
    excluded_as_duplicate_occurrences: Annotated[int, Field(ge=0)]
    total_groups: Annotated[int, Field(ge=0)]
    groups_with_canonical: Annotated[int, Field(ge=0)]
    deterministically_included_groups: Annotated[int, Field(ge=0)]
    topic_relevant_groups: Annotated[int, Field(ge=0)]
    topic_irrelevant_groups: Annotated[int, Field(ge=0)]
    topic_uncertain_groups: Annotated[int, Field(ge=0)]
    screening_included_groups: Annotated[int, Field(ge=0)]
    routed_groups: Annotated[int, Field(ge=0)]
    planning_only_groups: Annotated[int, Field(ge=0)]
    total_requests: Annotated[int, Field(ge=0)]
    requests_without_academic_occurrences: Annotated[int, Field(ge=0)]
    provider_warning_count: Annotated[int, Field(ge=0)]


class PaperScreeningWebPassAudit(_StrictAuditModel):
    web_pass_order: Annotated[int, Field(gt=0)]
    web_pass_id: str
    policy: ScreeningPolicy
    topic_relevance_enabled: bool
    request_audits: tuple[_PassRequestAudit, ...]
    group_audits: tuple[_PassGroupAudit, ...]
    occurrence_entries: tuple[_PassAuditEntry, ...]
    screening_included_canonical_occurrence_refs: tuple[
        PaperScreeningOccurrenceRef, ...
    ]
    routed_canonical_occurrence_refs: tuple[PaperScreeningOccurrenceRef, ...]

    @field_validator("web_pass_id")
    @classmethod
    def _normalize_pass_id(cls, value: str) -> str:
        return _strip_nonblank(value, "web pass id")

    @model_validator(mode="after")
    def _validate_pass(self) -> "PaperScreeningWebPassAudit":
        if tuple(item.pass_request_order for item in self.request_audits) != tuple(
            range(1, len(self.request_audits) + 1)
        ):
            raise ValueError("pass request order must be contiguous")
        if tuple(item.pass_group_order for item in self.group_audits) != tuple(
            range(1, len(self.group_audits) + 1)
        ):
            raise ValueError("pass group order must be contiguous")
        if tuple(item.pass_occurrence_order for item in self.occurrence_entries) != tuple(
            range(1, len(self.occurrence_entries) + 1)
        ):
            raise ValueError("pass occurrence order must be contiguous")
        if any(
            item.web_pass_id != self.web_pass_id
            for item in (*self.request_audits, *self.group_audits, *self.occurrence_entries)
        ):
            raise ValueError("pass audit records must use one pass id")
        expected_included_refs = tuple(
            PaperScreeningOccurrenceRef(
                web_pass_id=self.web_pass_id,
                occurrence_id=group.canonical_occurrence_id,
            )
            for group in self.group_audits
            if group.screening_included and group.canonical_occurrence_id is not None
        )
        expected_routed_refs = tuple(
            PaperScreeningOccurrenceRef(
                web_pass_id=self.web_pass_id,
                occurrence_id=group.canonical_occurrence_id,
            )
            for group in self.group_audits
            if group.routed_request_ids and group.canonical_occurrence_id is not None
        )
        if self.screening_included_canonical_occurrence_refs != expected_included_refs:
            raise ValueError("pass included references must follow group order")
        if self.routed_canonical_occurrence_refs != expected_routed_refs:
            raise ValueError("pass routed references must follow group order")
        for group in self.group_audits:
            if (
                group.topic_decision is not None
                and group.topic_decision.decision_order != group.pass_group_order
            ):
                raise ValueError("pass topic decision order must mirror group order")
        return self


class PaperScreeningAuditSnapshot(_StrictAuditModel):
    schema_version: Literal["1"]
    run_ordinal: Annotated[int, Field(gt=0)]
    web_passes: tuple[PaperScreeningWebPassRef, ...]
    policy: ScreeningPolicy
    topic_relevance_enabled: bool
    summary: PaperScreeningAuditSummary
    request_audits: tuple[PaperScreeningRequestAudit, ...]
    group_audits: tuple[DuplicateGroupAudit, ...]
    occurrence_entries: tuple[PaperScreeningAuditEntry, ...]
    screening_included_canonical_occurrence_refs: tuple[
        PaperScreeningOccurrenceRef, ...
    ]
    routed_canonical_occurrence_refs: tuple[PaperScreeningOccurrenceRef, ...]

    @model_validator(mode="after")
    def _validate_snapshot(self) -> "PaperScreeningAuditSnapshot":
        if tuple(item.web_pass_order for item in self.web_passes) != tuple(
            range(1, len(self.web_passes) + 1)
        ):
            raise ValueError("web pass order must be contiguous")
        pass_ids = tuple(item.web_pass_id for item in self.web_passes)
        if len(set(pass_ids)) != len(pass_ids):
            raise ValueError("web pass ids must be unique")
        if tuple(item.request_order for item in self.request_audits) != tuple(
            range(1, len(self.request_audits) + 1)
        ):
            raise ValueError("snapshot request order must be contiguous")
        if tuple(item.group_order for item in self.group_audits) != tuple(
            range(1, len(self.group_audits) + 1)
        ):
            raise ValueError("snapshot group order must be contiguous")
        if tuple(item.audit_order for item in self.occurrence_entries) != tuple(
            range(1, len(self.occurrence_entries) + 1)
        ):
            raise ValueError("snapshot occurrence order must be contiguous")
        if any(
            item.web_pass_id not in pass_ids
            for item in (
                *self.request_audits,
                *self.group_audits,
                *self.occurrence_entries,
            )
        ):
            raise ValueError("snapshot records must resolve to a declared web pass")

        request_by_key = {
            (item.web_pass_id, item.retrieval_request_id): item
            for item in self.request_audits
        }
        occurrence_by_key = {
            (item.web_pass_id, item.occurrence_id): item
            for item in self.occurrence_entries
        }
        group_by_key = {
            (item.web_pass_id, item.group_id): item for item in self.group_audits
        }
        if len(request_by_key) != len(self.request_audits):
            raise ValueError("request compound identities must be unique")
        if len(occurrence_by_key) != len(self.occurrence_entries):
            raise ValueError("occurrence compound identities must be unique")
        if len(group_by_key) != len(self.group_audits):
            raise ValueError("group compound identities must be unique")

        for entry in self.occurrence_entries:
            request = request_by_key.get(
                (entry.web_pass_id, entry.originating_request_id)
            )
            if request is None or entry.occurrence_id not in request.occurrence_ids:
                raise ValueError("entry originating request must resolve in its pass")
            if request.planning_only is not entry.planning_only:
                raise ValueError("entry planning metadata must mirror its request")
            if any(
                (entry.web_pass_id, request_id) not in request_by_key
                for request_id in entry.routed_request_ids
            ):
                raise ValueError("entry routed requests must resolve in its pass")
            if (entry.web_pass_id, entry.duplicate_group_id) not in group_by_key:
                raise ValueError("entry duplicate group must resolve in its pass")

        for group in self.group_audits:
            keys = tuple((group.web_pass_id, item) for item in group.member_occurrence_ids)
            if any(key not in occurrence_by_key for key in keys):
                raise ValueError("group members must resolve in their pass")
            if (group.web_pass_id, group.reference_occurrence_id) not in occurrence_by_key:
                raise ValueError("group reference must resolve in its pass")
            if group.canonical_occurrence_id is not None and (
                group.web_pass_id,
                group.canonical_occurrence_id,
            ) not in occurrence_by_key:
                raise ValueError("group canonical must resolve in its pass")
            if group.canonical_occurrence_id is not None:
                canonical_entry = occurrence_by_key[
                    (group.web_pass_id, group.canonical_occurrence_id)
                ]
                if group.canonical_candidate_id != canonical_entry.candidate_id:
                    raise ValueError("group canonical candidate must resolve")
            member_planning = tuple(occurrence_by_key[key].planning_only for key in keys)
            if group.planning_only_group != all(member_planning):
                raise ValueError("planning-only group must mirror every member")
            if any(
                (group.web_pass_id, request_id) not in request_by_key
                for request_id in group.routed_request_ids
            ):
                raise ValueError("group routed requests must resolve in its pass")
            for key in keys:
                entry = occurrence_by_key[key]
                if entry.duplicate_group_id != group.group_id:
                    raise ValueError("entry group reference must mirror membership")
                topic_values = (
                    entry.topic_verdict,
                    entry.topic_reason_code,
                    entry.topic_rationale,
                    entry.topic_confidence,
                )
                expected_topic = (
                    (None, None, None, None)
                    if group.topic_decision is None
                    else (
                        group.topic_decision.verdict,
                        group.topic_decision.reason_code,
                        group.topic_decision.rationale,
                        group.topic_decision.confidence,
                    )
                )
                if topic_values != expected_topic:
                    raise ValueError("entry topic fields must mirror its group")

        for request in self.request_audits:
            expected_occurrences = tuple(
                entry.occurrence_id
                for entry in self.occurrence_entries
                if entry.web_pass_id == request.web_pass_id
                and entry.originating_request_id == request.retrieval_request_id
            )
            if request.occurrence_ids != expected_occurrences:
                raise ValueError("request occurrence membership must be exact")
            if request.planning_only and any(
                warning.retriever_index != 1
                for warning in request.provider_warnings
            ):
                raise ValueError("planning warnings require retriever index one")
            if request.planning_only and (
                request.deterministic_canonical_occurrence_ids
                or request.effective_canonical_occurrence_ids
            ):
                raise ValueError("planning routes must remain empty")
            for occurrence_id in (
                *request.deterministic_canonical_occurrence_ids,
                *request.effective_canonical_occurrence_ids,
            ):
                entry = occurrence_by_key.get((request.web_pass_id, occurrence_id))
                if entry is None or not entry.is_canonical:
                    raise ValueError("request routes must resolve canonical occurrences")
            deterministic_ids = request.deterministic_canonical_occurrence_ids
            effective_ids = request.effective_canonical_occurrence_ids
            effective_iterator = iter(deterministic_ids)
            if any(
                not any(candidate == occurrence_id for candidate in effective_iterator)
                for occurrence_id in effective_ids
            ):
                raise ValueError("effective routes may only preserve-order delete")
            if not self.topic_relevance_enabled and (
                request.effective_route_present
                is not request.deterministic_route_present
                or request.effective_canonical_occurrence_ids
                != request.deterministic_canonical_occurrence_ids
            ):
                raise ValueError("topic-disabled routes must mirror deterministic routes")

        if self.topic_relevance_enabled:
            if any(
                (group.canonical_occurrence_id is not None)
                != (group.topic_decision is not None)
                for group in self.group_audits
            ):
                raise ValueError("topic decisions must cover every canonical group")
        elif any(group.topic_decision is not None for group in self.group_audits):
            raise ValueError("topic-disabled snapshot cannot contain topic decisions")

        for entry in self.occurrence_entries:
            expected_routed_requests = tuple(
                request.retrieval_request_id
                for request in self.request_audits
                if request.web_pass_id == entry.web_pass_id
                and entry.occurrence_id
                in request.effective_canonical_occurrence_ids
            )
            if entry.routed_request_ids != expected_routed_requests:
                raise ValueError("entry routed requests must mirror effective routes")
        for group in self.group_audits:
            expected_routed_requests = (
                ()
                if group.canonical_occurrence_id is None
                else tuple(
                    request.retrieval_request_id
                    for request in self.request_audits
                    if request.web_pass_id == group.web_pass_id
                    and group.canonical_occurrence_id
                    in request.effective_canonical_occurrence_ids
                )
            )
            if group.routed_request_ids != expected_routed_requests:
                raise ValueError("group routed requests must mirror effective routes")

        included_refs = tuple(
            PaperScreeningOccurrenceRef(
                web_pass_id=group.web_pass_id,
                occurrence_id=group.canonical_occurrence_id,
            )
            for group in self.group_audits
            if group.screening_included and group.canonical_occurrence_id is not None
        )
        routed_refs = tuple(
            PaperScreeningOccurrenceRef(
                web_pass_id=group.web_pass_id,
                occurrence_id=group.canonical_occurrence_id,
            )
            for group in self.group_audits
            if group.routed_request_ids and group.canonical_occurrence_id is not None
        )
        if self.screening_included_canonical_occurrence_refs != included_refs:
            raise ValueError("included canonical references must follow group order")
        if self.routed_canonical_occurrence_refs != routed_refs:
            raise ValueError("routed canonical references must follow group order")
        if self.summary != _build_summary(
            self.occurrence_entries, self.group_audits, self.request_audits
        ):
            raise ValueError("audit summary does not match snapshot records")
        return self


def _final_reasons(decision, topic_decision):
    if not decision.included:
        return tuple(
            _DETERMINISTIC_EXCLUSION_MAP[reason]
            for reason in decision.matched_rules
        )
    if (
        topic_decision is not None
        and topic_decision.verdict is TopicRelevanceVerdict.IRRELEVANT
    ):
        return (AuditExclusionReason.TOPIC_IRRELEVANT,)
    return ()


def _build_summary(entries, groups, requests) -> PaperScreeningAuditSummary:
    year_reasons = {
        ScreeningReasonCode.YEAR_BELOW_MIN,
        ScreeningReasonCode.YEAR_ABOVE_MAX,
        ScreeningReasonCode.YEAR_UNKNOWN,
    }
    type_reasons = {
        ScreeningReasonCode.TYPE_NOT_ALLOWED,
        ScreeningReasonCode.TYPE_UNKNOWN,
    }
    return PaperScreeningAuditSummary(
        total_occurrences=len(entries),
        duplicate_occurrences=sum(group.duplicate_count for group in groups),
        deterministically_included_occurrences=sum(
            item.deterministic_included for item in entries
        ),
        deterministically_excluded_occurrences=sum(
            not item.deterministic_included for item in entries
        ),
        excluded_by_year_occurrences=sum(
            bool(set(item.deterministic_matched_rules) & year_reasons) for item in entries
        ),
        excluded_by_type_occurrences=sum(
            bool(set(item.deterministic_matched_rules) & type_reasons) for item in entries
        ),
        excluded_as_duplicate_occurrences=sum(
            ScreeningReasonCode.DUPLICATE_OF_CANONICAL
            in item.deterministic_matched_rules
            for item in entries
        ),
        total_groups=len(groups),
        groups_with_canonical=sum(
            group.canonical_occurrence_id is not None for group in groups
        ),
        deterministically_included_groups=sum(
            group.canonical_occurrence_id is not None for group in groups
        ),
        topic_relevant_groups=sum(
            group.topic_decision is not None
            and group.topic_decision.verdict is TopicRelevanceVerdict.RELEVANT
            for group in groups
        ),
        topic_irrelevant_groups=sum(
            group.topic_decision is not None
            and group.topic_decision.verdict is TopicRelevanceVerdict.IRRELEVANT
            for group in groups
        ),
        topic_uncertain_groups=sum(
            group.topic_decision is not None
            and group.topic_decision.verdict is TopicRelevanceVerdict.UNCERTAIN
            for group in groups
        ),
        screening_included_groups=sum(group.screening_included for group in groups),
        routed_groups=sum(bool(group.routed_request_ids) for group in groups),
        planning_only_groups=sum(group.planning_only_group for group in groups),
        total_requests=len(requests),
        requests_without_academic_occurrences=sum(
            not request.occurrence_ids for request in requests
        ),
        provider_warning_count=sum(len(request.provider_warnings) for request in requests),
    )


def build_paper_screening_web_pass_audit(
    *,
    web_pass_order: int,
    web_pass_id: str,
    policy: ScreeningPolicy,
    topic_relevance_enabled: bool,
    deterministic_result: ScreeningResult,
    topic_result: TopicScreeningResult | None,
    request_metadata: tuple[PaperScreeningRequestMetadata, ...],
) -> PaperScreeningWebPassAudit:
    """Build one fully validated pass unit without retaining source object graphs."""
    if type(request_metadata) is not tuple:
        raise TypeError("request metadata must be exactly a tuple")
    if topic_relevance_enabled != (topic_result is not None):
        raise ValueError("topic result presence must match topic enablement")
    if topic_result is not None and topic_result.deterministic_result != deterministic_result:
        raise ValueError("topic result must wrap the deterministic result")
    request_ids = tuple(item.retrieval_request_id for item in request_metadata)
    if len(set(request_ids)) != len(request_ids):
        raise ValueError("request metadata ids must be unique")

    occurrence_by_id = {
        item.occurrence_id: item for item in deterministic_result.occurrences
    }
    decision_by_id = {
        item.occurrence_id: item for item in deterministic_result.decisions
    }
    group_by_occurrence = {
        occurrence_id: group
        for group in deterministic_result.duplicate_groups
        for occurrence_id in group.member_occurrence_ids
    }
    topic_by_group = (
        {}
        if topic_result is None
        else {
            item.duplicate_group_id: item
            for item in topic_result.relevance_decisions
        }
    )
    deterministic_routes = {
        item.retrieval_request_id: item for item in deterministic_result.routes
    }
    effective_route_items = (
        deterministic_result.routes
        if topic_result is None
        else topic_result.effective_routes
    )
    effective_routes = {
        item.retrieval_request_id: item for item in effective_route_items
    }
    if any(
        item.retrieval_request_id not in request_ids
        for item in deterministic_result.occurrences
    ):
        raise ValueError("all occurrences require request metadata")

    routed_by_canonical: dict[str, list[str]] = {}
    for metadata in request_metadata:
        route = effective_routes.get(metadata.retrieval_request_id)
        if route is None:
            continue
        for occurrence_id in route.canonical_occurrence_ids:
            routed_by_canonical.setdefault(occurrence_id, []).append(
                metadata.retrieval_request_id
            )

    pass_requests = []
    for order, metadata in enumerate(request_metadata, start=1):
        occurrence_ids = tuple(
            item.occurrence_id
            for item in deterministic_result.occurrences
            if item.retrieval_request_id == metadata.retrieval_request_id
        )
        deterministic_route = deterministic_routes.get(metadata.retrieval_request_id)
        effective_route = effective_routes.get(metadata.retrieval_request_id)
        pass_requests.append(
            _PassRequestAudit(
                pass_request_order=order,
                web_pass_id=web_pass_id,
                retrieval_request_id=metadata.retrieval_request_id,
                planning_only=metadata.planning_only,
                retrieval_query=metadata.retrieval_query,
                occurrence_ids=occurrence_ids,
                deterministic_route_present=deterministic_route is not None,
                deterministic_canonical_occurrence_ids=(
                    () if deterministic_route is None else deterministic_route.canonical_occurrence_ids
                ),
                effective_route_present=effective_route is not None,
                effective_canonical_occurrence_ids=(
                    () if effective_route is None else effective_route.canonical_occurrence_ids
                ),
                provider_warnings=metadata.provider_warnings,
            )
        )

    pass_entries = []
    for order, occurrence in enumerate(deterministic_result.occurrences, start=1):
        decision = decision_by_id[occurrence.occurrence_id]
        group = group_by_occurrence[occurrence.occurrence_id]
        topic_decision = topic_by_group.get(group.group_id)
        is_canonical = group.canonical_occurrence_id == occurrence.occurrence_id
        screening_included = decision.included and (
            topic_decision is None
            or topic_decision.verdict is not TopicRelevanceVerdict.IRRELEVANT
        )
        routed_ids = (
            tuple(routed_by_canonical.get(occurrence.occurrence_id, ()))
            if is_canonical and screening_included
            else ()
        )
        if not screening_included:
            routing_status = AuditRoutingStatus.EXCLUDED
        elif routed_ids:
            routing_status = AuditRoutingStatus.ROUTED
        elif occurrence.planning_only:
            routing_status = AuditRoutingStatus.PLANNING_ONLY
        else:
            raise ValueError("included non-planning canonical must be routed")
        reasons = _final_reasons(decision, topic_decision)
        pass_entries.append(
            _PassAuditEntry(
                pass_occurrence_order=order,
                web_pass_id=web_pass_id,
                occurrence_id=occurrence.occurrence_id,
                candidate_id=occurrence.candidate.candidate_id,
                title=occurrence.candidate.title,
                href=occurrence.candidate.href,
                doi=occurrence.candidate.doi,
                source=occurrence.candidate.source,
                source_rank=occurrence.candidate.source_rank,
                published_year=occurrence.candidate.published_year,
                venue=occurrence.candidate.venue,
                classified_type=decision.classified_type,
                duplicate_group_id=group.group_id,
                duplicate_key_kind=group.key_kind,
                canonical_occurrence_id=group.canonical_occurrence_id,
                is_canonical=is_canonical,
                planning_only=occurrence.planning_only,
                originating_request_id=occurrence.retrieval_request_id,
                routed_request_ids=routed_ids,
                deterministic_included=decision.included,
                deterministic_primary_reason=decision.primary_reason,
                deterministic_matched_rules=decision.matched_rules,
                topic_verdict=None if topic_decision is None else topic_decision.verdict,
                topic_reason_code=None if topic_decision is None else topic_decision.reason_code,
                topic_rationale=None if topic_decision is None else topic_decision.rationale,
                topic_confidence=None if topic_decision is None else topic_decision.confidence,
                screening_included=screening_included,
                routed_to_evidence=bool(routed_ids),
                routing_status=routing_status,
                final_exclusion_reasons=reasons,
            )
        )

    entry_by_id = {item.occurrence_id: item for item in pass_entries}
    pass_groups = []
    for order, group in enumerate(deterministic_result.duplicate_groups, start=1):
        topic_decision = topic_by_group.get(group.group_id)
        canonical_entry = (
            None
            if group.canonical_occurrence_id is None
            else entry_by_id[group.canonical_occurrence_id]
        )
        routed_ids = () if canonical_entry is None else canonical_entry.routed_request_ids
        pass_groups.append(
            _PassGroupAudit(
                pass_group_order=order,
                web_pass_id=web_pass_id,
                group_id=group.group_id,
                key_kind=group.key_kind,
                member_occurrence_ids=group.member_occurrence_ids,
                reference_occurrence_id=group.reference_occurrence_id,
                canonical_occurrence_id=group.canonical_occurrence_id,
                canonical_candidate_id=(
                    None
                    if canonical_entry is None
                    else canonical_entry.candidate_id
                ),
                duplicate_count=len(group.member_occurrence_ids) - 1,
                deterministic_included=group.canonical_occurrence_id is not None,
                topic_decision=topic_decision,
                screening_included=(
                    False if canonical_entry is None else canonical_entry.screening_included
                ),
                routed_request_ids=routed_ids,
                planning_only_group=all(
                    occurrence_by_id[item].planning_only
                    for item in group.member_occurrence_ids
                ),
            )
        )

    included_refs = tuple(
        PaperScreeningOccurrenceRef(
            web_pass_id=web_pass_id,
            occurrence_id=group.canonical_occurrence_id,
        )
        for group in pass_groups
        if group.screening_included and group.canonical_occurrence_id is not None
    )
    routed_refs = tuple(
        PaperScreeningOccurrenceRef(
            web_pass_id=web_pass_id,
            occurrence_id=group.canonical_occurrence_id,
        )
        for group in pass_groups
        if group.routed_request_ids and group.canonical_occurrence_id is not None
    )
    return PaperScreeningWebPassAudit(
        web_pass_order=web_pass_order,
        web_pass_id=web_pass_id,
        policy=policy,
        topic_relevance_enabled=topic_relevance_enabled,
        request_audits=tuple(pass_requests),
        group_audits=tuple(pass_groups),
        occurrence_entries=tuple(pass_entries),
        screening_included_canonical_occurrence_refs=included_refs,
        routed_canonical_occurrence_refs=routed_refs,
    )


class PaperScreeningAuditCollector:
    """Collect complete pass audits and publish one immutable run snapshot."""

    def __init__(
        self,
        *,
        run_ordinal: int,
        policy: ScreeningPolicy,
        topic_relevance_enabled: bool,
    ) -> None:
        if type(run_ordinal) is not int or run_ordinal <= 0:
            raise ValueError("run_ordinal must be a strict positive integer")
        if not isinstance(policy, ScreeningPolicy):
            raise TypeError("policy must be a ScreeningPolicy")
        if type(topic_relevance_enabled) is not bool:
            raise TypeError("topic_relevance_enabled must be a strict boolean")
        self._state = AuditCollectorState.OPEN
        self._run_ordinal = run_ordinal
        self._policy = policy
        self._topic_relevance_enabled = topic_relevance_enabled
        self._passes: list[PaperScreeningWebPassAudit] = []
        self._pending_snapshot: PaperScreeningAuditSnapshot | None = None
        self._snapshot: PaperScreeningAuditSnapshot | None = None
        self._allocated_pass_count = 0

    @property
    def state(self) -> AuditCollectorState:
        return self._state

    def allocate_web_pass(self) -> PaperScreeningWebPassRef:
        self._require_open("allocate a web pass")
        self._allocated_pass_count += 1
        return PaperScreeningWebPassRef(
            web_pass_order=self._allocated_pass_count,
            web_pass_id=f"web-pass:{self._allocated_pass_count:06d}",
        )

    def add_pass(self, pass_audit: PaperScreeningWebPassAudit) -> None:
        self._require_open("add a pass audit")
        if self._pending_snapshot is not None:
            raise RuntimeError("cannot add a pass after audit preparation")
        if not isinstance(pass_audit, PaperScreeningWebPassAudit):
            raise TypeError("pass audit must be a PaperScreeningWebPassAudit")
        expected_order = len(self._passes) + 1
        if pass_audit.web_pass_order != expected_order:
            raise ValueError("pass audits must use contiguous preallocated order")
        if any(item.web_pass_id == pass_audit.web_pass_id for item in self._passes):
            raise ValueError("pass audit ids must be unique")
        if pass_audit.policy != self._policy:
            raise ValueError("pass audit policy must match its collector")
        if pass_audit.topic_relevance_enabled is not self._topic_relevance_enabled:
            raise ValueError("pass topic mode must match its collector")
        self._passes.append(pass_audit)

    def prepare(self) -> None:
        self._require_open("prepare")
        if self._pending_snapshot is not None:
            raise RuntimeError("audit snapshot is already prepared")
        requests = []
        groups = []
        entries = []
        for pass_audit in self._passes:
            for item in pass_audit.request_audits:
                values = item.model_dump(exclude={"pass_request_order"})
                requests.append(
                    PaperScreeningRequestAudit(
                        request_order=len(requests) + 1,
                        **values,
                    )
                )
            for item in pass_audit.group_audits:
                values = item.model_dump(exclude={"pass_group_order"})
                groups.append(
                    DuplicateGroupAudit(group_order=len(groups) + 1, **values)
                )
            for item in pass_audit.occurrence_entries:
                values = item.model_dump(exclude={"pass_occurrence_order"})
                entries.append(
                    PaperScreeningAuditEntry(audit_order=len(entries) + 1, **values)
                )

        included_refs = tuple(
            PaperScreeningOccurrenceRef(
                web_pass_id=group.web_pass_id,
                occurrence_id=group.canonical_occurrence_id,
            )
            for group in groups
            if group.screening_included and group.canonical_occurrence_id is not None
        )
        routed_refs = tuple(
            PaperScreeningOccurrenceRef(
                web_pass_id=group.web_pass_id,
                occurrence_id=group.canonical_occurrence_id,
            )
            for group in groups
            if group.routed_request_ids and group.canonical_occurrence_id is not None
        )
        self._pending_snapshot = PaperScreeningAuditSnapshot(
            schema_version="1",
            run_ordinal=self._run_ordinal,
            web_passes=tuple(
                PaperScreeningWebPassRef(
                    web_pass_order=item.web_pass_order,
                    web_pass_id=item.web_pass_id,
                )
                for item in self._passes
            ),
            policy=self._policy,
            topic_relevance_enabled=self._topic_relevance_enabled,
            summary=_build_summary(tuple(entries), tuple(groups), tuple(requests)),
            request_audits=tuple(requests),
            group_audits=tuple(groups),
            occurrence_entries=tuple(entries),
            screening_included_canonical_occurrence_refs=included_refs,
            routed_canonical_occurrence_refs=routed_refs,
        )

    def commit(self) -> None:
        self._snapshot = self._pending_snapshot
        self._pending_snapshot = None
        self._state = AuditCollectorState.FINALIZED

    def abort(self) -> None:
        self._require_open("abort")
        self._passes.clear()
        self._pending_snapshot = None
        self._snapshot = None
        self._state = AuditCollectorState.ABORTED

    def snapshot(self) -> PaperScreeningAuditSnapshot:
        if self._state is not AuditCollectorState.FINALIZED or self._snapshot is None:
            raise RuntimeError(AUDIT_UNAVAILABLE_MESSAGE)
        return self._snapshot

    def _require_open(self, operation: str) -> None:
        if self._state is not AuditCollectorState.OPEN:
            raise RuntimeError(
                f"cannot {operation} when audit collector is {self._state.value}"
            )
