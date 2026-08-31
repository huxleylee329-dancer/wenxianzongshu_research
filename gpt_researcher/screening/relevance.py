"""Strict immutable models for topic-relevance paper screening."""

from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .decisions import PaperType, RetrievalRequestRoute, ScreeningResult


class TopicRelevanceVerdict(str, Enum):
    RELEVANT = "relevant"
    IRRELEVANT = "irrelevant"
    UNCERTAIN = "uncertain"


class TopicRelevanceLLMReasonCode(str, Enum):
    DIRECT_TOPIC_MATCH = "direct_topic_match"
    METHOD_OR_DATASET_MATCH = "method_or_dataset_match"
    SUPPORTING_CONTEXT = "supporting_context"
    OUT_OF_SCOPE = "out_of_scope"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class TopicRelevanceReasonCode(str, Enum):
    DIRECT_TOPIC_MATCH = "direct_topic_match"
    METHOD_OR_DATASET_MATCH = "method_or_dataset_match"
    SUPPORTING_CONTEXT = "supporting_context"
    OUT_OF_SCOPE = "out_of_scope"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    LLM_INVALID_OUTPUT = "llm_invalid_output"
    LLM_TIMEOUT = "llm_timeout"
    LLM_FAILURE = "llm_failure"


_LLM_ALLOWED_REASONS = {
    TopicRelevanceVerdict.RELEVANT: {
        TopicRelevanceLLMReasonCode.DIRECT_TOPIC_MATCH,
        TopicRelevanceLLMReasonCode.METHOD_OR_DATASET_MATCH,
        TopicRelevanceLLMReasonCode.SUPPORTING_CONTEXT,
    },
    TopicRelevanceVerdict.IRRELEVANT: {
        TopicRelevanceLLMReasonCode.OUT_OF_SCOPE,
    },
    TopicRelevanceVerdict.UNCERTAIN: {
        TopicRelevanceLLMReasonCode.INSUFFICIENT_EVIDENCE,
    },
}

_DECISION_ALLOWED_REASONS = {
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


def _validate_rationale(value: str) -> str:
    normalized = value.strip()
    if not 1 <= len(normalized) <= 500:
        raise ValueError("rationale must contain 1 through 500 characters")
    return normalized


class TopicRelevanceInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    research_topic: str
    title: str
    abstract: str
    paper_type: PaperType
    published_year: Annotated[int, Field(ge=1000, le=9999)] | None = None
    venue: str | None = None
    doi: str | None = None

    @field_validator("research_topic", "title", "abstract")
    @classmethod
    def _validate_required_text(cls, value: str) -> str:
        return _strip_nonblank(value, "topic input text")

    @field_validator("venue", "doi")
    @classmethod
    def _normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class TopicRelevanceLLMOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    verdict: TopicRelevanceVerdict
    reason_code: TopicRelevanceLLMReasonCode
    rationale: str
    confidence: Annotated[int, Field(ge=0, le=100)]

    @field_validator("rationale")
    @classmethod
    def _normalize_rationale(cls, value: str) -> str:
        return _validate_rationale(value)

    @model_validator(mode="after")
    def _validate_verdict_reason(self) -> "TopicRelevanceLLMOutput":
        if self.reason_code not in _LLM_ALLOWED_REASONS[self.verdict]:
            raise ValueError("verdict and reason_code are incompatible")
        return self


class TopicRelevanceDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    decision_order: Annotated[int, Field(gt=0)]
    duplicate_group_id: str
    canonical_occurrence_id: str
    canonical_candidate_id: str
    verdict: TopicRelevanceVerdict
    reason_code: TopicRelevanceReasonCode
    rationale: str
    confidence: Annotated[int, Field(ge=0, le=100)]

    @field_validator(
        "duplicate_group_id",
        "canonical_occurrence_id",
        "canonical_candidate_id",
    )
    @classmethod
    def _normalize_identifier(cls, value: str) -> str:
        return _strip_nonblank(value, "topic decision identifier")

    @field_validator("rationale")
    @classmethod
    def _normalize_rationale(cls, value: str) -> str:
        return _validate_rationale(value)

    @model_validator(mode="after")
    def _validate_verdict_reason(self) -> "TopicRelevanceDecision":
        if self.reason_code not in _DECISION_ALLOWED_REASONS[self.verdict]:
            raise ValueError("verdict and reason_code are incompatible")
        return self


class TopicScreeningResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    deterministic_result: ScreeningResult
    relevance_decisions: tuple[TopicRelevanceDecision, ...]
    effective_routes: tuple[RetrievalRequestRoute, ...]

    @model_validator(mode="after")
    def _validate_result(self) -> "TopicScreeningResult":
        deterministic = self.deterministic_result
        canonical_groups = tuple(
            group
            for group in deterministic.duplicate_groups
            if group.canonical_occurrence_id is not None
        )
        if len(self.relevance_decisions) != len(canonical_groups):
            raise ValueError("each canonical-bearing group requires one decision")
        if tuple(
            decision.decision_order for decision in self.relevance_decisions
        ) != tuple(range(1, len(canonical_groups) + 1)):
            raise ValueError("topic decision order must be contiguous")

        occurrence_by_id = {
            occurrence.occurrence_id: occurrence
            for occurrence in deterministic.occurrences
        }
        decision_by_canonical: dict[str, TopicRelevanceDecision] = {}
        for group, decision in zip(canonical_groups, self.relevance_decisions):
            canonical_id = group.canonical_occurrence_id
            assert canonical_id is not None
            occurrence = occurrence_by_id.get(canonical_id)
            if occurrence is None:
                raise ValueError("canonical occurrence does not resolve")
            if (
                decision.duplicate_group_id != group.group_id
                or decision.canonical_occurrence_id != canonical_id
                or decision.canonical_candidate_id
                != occurrence.candidate.candidate_id
            ):
                raise ValueError("topic decision identifiers do not mirror screening")
            if canonical_id in decision_by_canonical:
                raise ValueError("canonical occurrence has duplicate decisions")
            decision_by_canonical[canonical_id] = decision

        if len(self.effective_routes) != len(deterministic.routes):
            raise ValueError("effective routes must preserve every request")

        request_planning: dict[str, bool] = {}
        for occurrence in deterministic.occurrences:
            previous = request_planning.setdefault(
                occurrence.retrieval_request_id, occurrence.planning_only
            )
            if previous is not occurrence.planning_only:
                raise ValueError("request planning metadata is inconsistent")

        for original, effective in zip(
            deterministic.routes, self.effective_routes
        ):
            if effective.retrieval_request_id != original.retrieval_request_id:
                raise ValueError("effective route identity or order changed")
            expected = tuple(
                canonical_id
                for canonical_id in original.canonical_occurrence_ids
                if decision_by_canonical[canonical_id].verdict
                is not TopicRelevanceVerdict.IRRELEVANT
            )
            if effective.canonical_occurrence_ids != expected:
                raise ValueError("effective route is not the frozen filtered route")
            if request_planning.get(original.retrieval_request_id) is True and (
                effective.canonical_occurrence_ids
            ):
                raise ValueError("planning routes must remain empty")
        return self
