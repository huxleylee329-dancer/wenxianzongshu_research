"""Persistent terminal outcome contracts for the academic-writing workflow."""

from __future__ import annotations

from typing import Annotated, Literal, TypeAlias

import json
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

from .academic_draft_composer import WorkflowAcademicDraftComposition
from .citation_evidence_gate import WorkflowCitationEvidenceGateResult
from .citation_review_disposition import gate_citation_review_disposition
from .citation_reviewer import WorkflowSectionCitationReview
from .references_renderer import WorkflowReferencedDraft
from .state import (
    AcademicWorkflowRequest,
    AcademicWorkflowGraphState,
    AcademicWorkflowState,
    WorkflowError,
    WorkflowEvent,
    WorkflowEvidenceProvenance,
    WorkflowEvidenceSource,
    WorkflowOutline,
    WorkflowOutlineDecisionRecord,
    WorkflowOutlineSection,
    WorkflowResearchEvidence,
    WorkflowSectionDraft,
    WorkflowTopicPlan,
    validate_json_value,
)


__all__ = (
    "WorkflowDraftReadyOutcome",
    "WorkflowReviewRequiredOutcome",
    "WorkflowOutcome",
    "AcademicWorkflowPersistentState",
)


_BASE_FIELDS = (
    "schema_version",
    "workflow_id",
    "thread_id",
    "run_id",
    "phase",
    "status",
    "request",
    "topic_plan",
    "research_evidence",
    "outline",
    "outline_decision",
    "errors",
    "events",
)
_PERSISTENT_FIELDS = _BASE_FIELDS + ("outcome",)
_READY_FIELDS = ("outcome_type", "referenced_draft")
_REVIEW_FIELDS = ("outcome_type", "drafts", "gate_result", "reviews")
_REQUEST_FIELDS = (
    "workflow_mode",
    "workflow_id",
    "thread_id",
    "run_id",
    "query",
    "report_type",
    "report_source",
    "tone",
    "language",
    "source_urls",
    "document_urls",
    "query_domains",
    "max_search_results",
    "report_mode",
    "report_locale",
)
_TOPIC_FIELDS = (
    "topic_plan_id",
    "workflow_id",
    "run_id",
    "attempt",
    "research_topic",
    "research_questions",
)
_EVIDENCE_FIELDS = (
    "evidence_id",
    "topic_plan_id",
    "attempt",
    "context_blocks",
    "sources",
    "provenance",
)
_SOURCE_FIELDS = ("source_id", "order", "title", "url", "candidate_id")
_PROVENANCE_FIELDS = ("source_id", "evidence_blocks")
_OUTLINE_FIELDS = (
    "outline_id",
    "evidence_id",
    "attempt",
    "title",
    "sections",
    "report_mode",
    "report_locale",
)
_SECTION_FIELDS = ("section_id", "order", "title", "brief", "section_role")
_DECISION_FIELDS = (
    "decision_id",
    "schema_version",
    "workflow_id",
    "thread_id",
    "run_id",
    "outline_id",
    "outline_digest",
    "decision",
    "actor_assertion",
    "attempt",
)
_ERROR_FIELDS = ("error_id", "order", "failed_node_id", "attempt", "code")
_EVENT_FIELDS = ("event_id", "order", "event_type", "node_id", "attempt")
_DRAFT_FIELDS = ("outline_id", "section_id", "attempt", "content")
_GATE_FIELDS = (
    "outline_id",
    "section_ids",
    "cited_source_ids_by_section",
    "attempt",
)
_CITATION_REVIEW_FIELDS = (
    "outline_id",
    "section_id",
    "cited_source_ids",
    "verdict",
    "issues",
    "rationale",
    "attempt",
)
_REFERENCED_FIELDS = (
    "outline_id",
    "section_ids",
    "reference_source_ids",
    "attempt",
    "content",
)
_OLD_PHASES = frozenset(
    {
        "initialized",
        "topic_planned",
        "evidence_collected",
        "outline_ready",
        "outline_approved",
        "outline_rejected",
    }
)
_ERROR_TEXT = "academic workflow persistent outcome contract violation"


class _PersistentOutcomeContractError(RuntimeError):
    pass


class _OutcomeModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


def _exact_mapping(
    value: object,
    expected_type: type[object],
    fields: tuple[str, ...],
) -> dict[str, object]:
    if type(value) is expected_type:
        namespace = object.__getattribute__(value, "__dict__")
        fields_set = object.__getattribute__(value, "__pydantic_fields_set__")
        extra = object.__getattribute__(value, "__pydantic_extra__")
        private = object.__getattribute__(value, "__pydantic_private__")
        if type(namespace) is not dict or type(fields_set) is not set:
            raise TypeError(_ERROR_TEXT)
        keys = tuple(dict.keys(namespace))
        members = tuple(set.__iter__(fields_set))
        if (
            extra is not None
            or private is not None
            or any(type(key) is not str for key in keys)
            or any(type(member) is not str for member in members)
            or set(keys) != set(fields)
            or fields_set != set(fields)
        ):
            raise TypeError(_ERROR_TEXT)
        return {name: dict.__getitem__(namespace, name) for name in fields}
    if type(value) is not dict:
        raise TypeError(_ERROR_TEXT)
    keys = tuple(dict.keys(value))
    if any(type(key) is not str for key in keys) or set(keys) != set(fields):
        raise TypeError(_ERROR_TEXT)
    return {name: dict.__getitem__(value, name) for name in fields}


class WorkflowDraftReadyOutcome(_OutcomeModel):
    outcome_type: Literal["draft_ready"]
    referenced_draft: WorkflowReferencedDraft

    @model_validator(mode="before")
    @classmethod
    def _require_exact_input(cls, value: object, info: ValidationInfo) -> object:
        mapping = _exact_mapping(value, cls, _READY_FIELDS)
        if type(mapping["outcome_type"]) is not str:
            raise TypeError(_ERROR_TEXT)
        referenced = mapping["referenced_draft"]
        if info.mode != "json" and type(referenced) is not WorkflowReferencedDraft:
            raise TypeError(_ERROR_TEXT)
        return mapping


class WorkflowReviewRequiredOutcome(_OutcomeModel):
    outcome_type: Literal["review_required"]
    drafts: tuple[WorkflowSectionDraft, ...]
    gate_result: WorkflowCitationEvidenceGateResult
    reviews: tuple[WorkflowSectionCitationReview, ...]

    @model_validator(mode="before")
    @classmethod
    def _require_exact_input(cls, value: object, info: ValidationInfo) -> object:
        mapping = _exact_mapping(value, cls, _REVIEW_FIELDS)
        if type(mapping["outcome_type"]) is not str:
            raise TypeError(_ERROR_TEXT)
        expected_container = list if info.mode == "json" else tuple
        drafts = mapping["drafts"]
        reviews = mapping["reviews"]
        if type(drafts) is not expected_container or type(reviews) is not expected_container:
            raise TypeError(_ERROR_TEXT)
        if info.mode != "json":
            if any(type(item) is not WorkflowSectionDraft for item in drafts):
                raise TypeError(_ERROR_TEXT)
            if any(type(item) is not WorkflowSectionCitationReview for item in reviews):
                raise TypeError(_ERROR_TEXT)
            if type(mapping["gate_result"]) is not WorkflowCitationEvidenceGateResult:
                raise TypeError(_ERROR_TEXT)
        return {
            "outcome_type": mapping["outcome_type"],
            "drafts": tuple(drafts),
            "gate_result": mapping["gate_result"],
            "reviews": tuple(reviews),
        }


WorkflowOutcome: TypeAlias = Annotated[
    WorkflowDraftReadyOutcome | WorkflowReviewRequiredOutcome,
    Field(discriminator="outcome_type"),
]


class AcademicWorkflowPersistentState(AcademicWorkflowState):
    """Workflow state plus its minimal durable terminal outcome."""

    outcome: WorkflowOutcome | None = None

    @field_validator("outcome", mode="before")
    @classmethod
    def _require_exact_python_outcome(
        cls, value: object, info: ValidationInfo
    ) -> object:
        if info.mode != "json" and value is not None and type(value) not in (
            WorkflowDraftReadyOutcome,
            WorkflowReviewRequiredOutcome,
        ):
            raise TypeError(_ERROR_TEXT)
        return value

    @model_validator(mode="after")
    def _validate_phase_outcome(self) -> AcademicWorkflowPersistentState:
        namespace = object.__getattribute__(self, "__dict__")
        fields_set = object.__getattribute__(self, "__pydantic_fields_set__")
        extra = object.__getattribute__(self, "__pydantic_extra__")
        private = object.__getattribute__(self, "__pydantic_private__")
        if type(namespace) is not dict or type(fields_set) is not set:
            raise ValueError(_ERROR_TEXT)
        keys = tuple(dict.keys(namespace))
        members = tuple(set.__iter__(fields_set))
        if (
            extra is not None
            or private is not None
            or any(type(key) is not str for key in keys)
            or any(type(member) is not str for member in members)
            or set(keys) != set(_PERSISTENT_FIELDS)
        ):
            raise ValueError(_ERROR_TEXT)
        if fields_set not in (
            set(_PERSISTENT_FIELDS),
            set(_BASE_FIELDS),
        ):
            raise ValueError(_ERROR_TEXT)

        outcome = self.outcome
        phase = self.phase
        if phase in _OLD_PHASES:
            if outcome is not None:
                raise ValueError(_ERROR_TEXT)
            return self
        if phase == "draft_ready":
            if self.status != "completed" or type(outcome) is not WorkflowDraftReadyOutcome:
                raise ValueError(_ERROR_TEXT)
            self._validate_ready(outcome)
            return self
        if phase == "review_required":
            if (
                self.status != "completed"
                or type(outcome) is not WorkflowReviewRequiredOutcome
            ):
                raise ValueError(_ERROR_TEXT)
            self._validate_review(outcome)
            return self
        raise ValueError(_ERROR_TEXT)

    def _validate_ready(self, outcome: WorkflowDraftReadyOutcome) -> None:
        referenced = _trusted_artifact(
            outcome.referenced_draft,
            WorkflowReferencedDraft,
        )
        outline = _trusted_artifact(self.outline, WorkflowOutline)
        evidence = _trusted_artifact(
            self.research_evidence,
            WorkflowResearchEvidence,
        )
        if (
            type(referenced) is not WorkflowReferencedDraft
            or type(outline) is not WorkflowOutline
            or type(evidence) is not WorkflowResearchEvidence
        ):
            raise ValueError(_ERROR_TEXT)
        ids = referenced.reference_source_ids
        if type(ids) is not tuple or not 1 <= len(ids) <= 64:
            raise ValueError(_ERROR_TEXT)
        if any(type(item) is not str or not item for item in ids) or len(set(ids)) != len(ids):
            raise ValueError(_ERROR_TEXT)
        source_ids = tuple(source.source_id for source in evidence.sources)
        if (
            referenced.outline_id != outline.outline_id
            or referenced.section_ids
            != tuple(section.section_id for section in outline.sections)
            or type(referenced.attempt) is not int
            or referenced.attempt != 1
            or any(item not in source_ids for item in ids)
        ):
            raise ValueError(_ERROR_TEXT)

    def _validate_review(self, outcome: WorkflowReviewRequiredOutcome) -> None:
        plan = _prepare_review_plan(outcome, self.outline)
        if type(plan) is not tuple:
            raise ValueError(_ERROR_TEXT)
        drafts, gate, reviews = plan
        disposition = gate_citation_review_disposition(
            gate,
            reviews,
        )
        if disposition.disposition == "ready":
            raise ValueError(_ERROR_TEXT)
        composition = WorkflowAcademicDraftComposition(
            drafts=drafts,
            gate_result=gate,
            reviews=reviews,
            disposition=disposition,
            merged_draft=None,
            referenced_draft=None,
        )
        if composition.disposition.disposition not in (
            "blocked",
            "needs_human_review",
        ):
            raise ValueError(_ERROR_TEXT)


class _Invalid:
    __slots__ = ()


_INVALID = _Invalid()


def _model_contract(
    model_type: type[object],
) -> tuple[tuple[str, ...], tuple[frozenset[str], ...]] | _Invalid:
    contracts: tuple[
        tuple[type[object], tuple[str, ...], tuple[frozenset[str], ...]], ...
    ] = (
        (
            AcademicWorkflowPersistentState,
            _PERSISTENT_FIELDS,
            (frozenset(_PERSISTENT_FIELDS), frozenset(_BASE_FIELDS)),
        ),
        (
            AcademicWorkflowRequest,
            _REQUEST_FIELDS,
            (
                frozenset(_REQUEST_FIELDS),
                frozenset(_REQUEST_FIELDS[:-2] + ("report_mode",)),
                frozenset(_REQUEST_FIELDS[:-2] + ("report_locale",)),
                frozenset(_REQUEST_FIELDS[:-2]),
            ),
        ),
        (WorkflowTopicPlan, _TOPIC_FIELDS, (frozenset(_TOPIC_FIELDS),)),
        (
            WorkflowResearchEvidence,
            _EVIDENCE_FIELDS,
            (frozenset(_EVIDENCE_FIELDS), frozenset(_EVIDENCE_FIELDS[:-1])),
        ),
        (WorkflowEvidenceSource, _SOURCE_FIELDS, (frozenset(_SOURCE_FIELDS),)),
        (
            WorkflowEvidenceProvenance,
            _PROVENANCE_FIELDS,
            (frozenset(_PROVENANCE_FIELDS),),
        ),
        (
            WorkflowOutline,
            _OUTLINE_FIELDS,
            (
                frozenset(_OUTLINE_FIELDS),
                frozenset(_OUTLINE_FIELDS[:-2] + ("report_mode",)),
                frozenset(_OUTLINE_FIELDS[:-2] + ("report_locale",)),
                frozenset(_OUTLINE_FIELDS[:-2]),
            ),
        ),
        (
            WorkflowOutlineSection,
            _SECTION_FIELDS,
            (frozenset(_SECTION_FIELDS), frozenset(_SECTION_FIELDS[:-1])),
        ),
        (
            WorkflowOutlineDecisionRecord,
            _DECISION_FIELDS,
            (frozenset(_DECISION_FIELDS),),
        ),
        (WorkflowError, _ERROR_FIELDS, (frozenset(_ERROR_FIELDS),)),
        (WorkflowEvent, _EVENT_FIELDS, (frozenset(_EVENT_FIELDS),)),
        (
            WorkflowDraftReadyOutcome,
            _READY_FIELDS,
            (frozenset(_READY_FIELDS),),
        ),
        (
            WorkflowReviewRequiredOutcome,
            _REVIEW_FIELDS,
            (frozenset(_REVIEW_FIELDS),),
        ),
        (
            WorkflowReferencedDraft,
            _REFERENCED_FIELDS,
            (frozenset(_REFERENCED_FIELDS),),
        ),
        (WorkflowSectionDraft, _DRAFT_FIELDS, (frozenset(_DRAFT_FIELDS),)),
        (
            WorkflowCitationEvidenceGateResult,
            _GATE_FIELDS,
            (frozenset(_GATE_FIELDS),),
        ),
        (
            WorkflowSectionCitationReview,
            _CITATION_REVIEW_FIELDS,
            (frozenset(_CITATION_REVIEW_FIELDS),),
        ),
    )
    index = 0
    while index < tuple.__len__(contracts):
        item = tuple.__getitem__(contracts, index)
        if model_type is tuple.__getitem__(item, 0):
            return tuple.__getitem__(item, 1), tuple.__getitem__(item, 2)
        index += 1
    return _INVALID


def _static_json_value(value: object) -> object:
    value_type = type(value)
    if value is None or value_type in (bool, int, str):
        return value
    if value_type is tuple:
        copied: list[object] = []
        index = 0
        while index < tuple.__len__(value):
            item = _static_json_value(tuple.__getitem__(value, index))
            if item is _INVALID:
                copied.clear()
                return _INVALID
            copied.append(item)
            index += 1
        return copied
    contract = _model_contract(value_type)
    if type(contract) is not tuple:
        return _INVALID
    fields, allowed_field_sets = contract
    try:
        namespace = object.__getattribute__(value, "__dict__")
        fields_set = object.__getattribute__(value, "__pydantic_fields_set__")
        extra = object.__getattribute__(value, "__pydantic_extra__")
        private = object.__getattribute__(value, "__pydantic_private__")
        if type(namespace) is not dict or type(fields_set) is not set:
            return _INVALID
        keys = tuple(dict.keys(namespace))
        members = tuple(set.__iter__(fields_set))
        if extra is not None or private is not None:
            return _INVALID
        if any(type(key) is not str for key in keys) or any(
            type(member) is not str for member in members
        ):
            return _INVALID
        if set(keys) != set(fields) or not any(
            fields_set == allowed for allowed in allowed_field_sets
        ):
            return _INVALID
        copied_mapping: dict[str, object] = {}
        index = 0
        while index < tuple.__len__(fields):
            name = tuple.__getitem__(fields, index)
            item = _static_json_value(dict.__getitem__(namespace, name))
            if item is _INVALID:
                copied_mapping.clear()
                return _INVALID
            copied_mapping[name] = item
            index += 1
        return copied_mapping
    except Exception:
        return _INVALID


def _trusted_artifact(value: object, expected_type: type[BaseModel]) -> object:
    if type(value) is not expected_type:
        return _INVALID
    copied = _static_json_value(value)
    if type(copied) is not dict:
        return _INVALID
    try:
        encoded = json.dumps(
            copied,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        trusted = expected_type.model_validate_json(encoded)
        if type(trusted) is not expected_type:
            return _INVALID
        return trusted
    except Exception:
        return _INVALID


def _prepare_review_plan(
    outcome: object,
    outline: object,
) -> tuple[
    tuple[WorkflowSectionDraft, ...],
    WorkflowCitationEvidenceGateResult,
    tuple[WorkflowSectionCitationReview, ...],
] | _Invalid:
    trusted_outcome = _trusted_artifact(outcome, WorkflowReviewRequiredOutcome)
    trusted_outline = _trusted_artifact(outline, WorkflowOutline)
    if (
        type(trusted_outcome) is not WorkflowReviewRequiredOutcome
        or type(trusted_outline) is not WorkflowOutline
    ):
        return _INVALID
    drafts = trusted_outcome.drafts
    gate = trusted_outcome.gate_result
    reviews = trusted_outcome.reviews
    section_count = tuple.__len__(drafts)
    if (
        not 1 <= section_count <= 12
        or tuple.__len__(reviews) != section_count
        or tuple.__len__(gate.section_ids) != section_count
        or tuple.__len__(gate.cited_source_ids_by_section) != section_count
        or gate.outline_id != trusted_outline.outline_id
        or gate.section_ids
        != tuple(section.section_id for section in trusted_outline.sections)
    ):
        return _INVALID
    global_ids: list[str] = []
    seen: set[str] = set()
    index = 0
    while index < section_count:
        draft = tuple.__getitem__(drafts, index)
        review = tuple.__getitem__(reviews, index)
        section_id = tuple.__getitem__(gate.section_ids, index)
        citations = tuple.__getitem__(gate.cited_source_ids_by_section, index)
        if (
            draft.outline_id != gate.outline_id
            or draft.section_id != section_id
            or type(draft.attempt) is not int
            or draft.attempt != 1
            or review.outline_id != gate.outline_id
            or review.section_id != section_id
            or review.cited_source_ids != citations
            or type(review.attempt) is not int
            or review.attempt != 1
        ):
            global_ids.clear()
            seen.clear()
            return _INVALID
        source_index = 0
        while source_index < tuple.__len__(citations):
            source_id = tuple.__getitem__(citations, source_index)
            if source_id not in seen:
                seen.add(source_id)
                global_ids.append(source_id)
                if len(global_ids) > 64:
                    global_ids.clear()
                    seen.clear()
                    return _INVALID
            source_index += 1
        index += 1
    if not global_ids:
        seen.clear()
        return _INVALID
    global_ids.clear()
    seen.clear()
    return drafts, gate, reviews


def _try_persistent_workflow_to_graph_state(
    state: AcademicWorkflowPersistentState,
) -> AcademicWorkflowGraphState | None:
    try:
        trusted = _trusted_artifact(state, AcademicWorkflowPersistentState)
        if type(trusted) is not AcademicWorkflowPersistentState:
            raise TypeError(_ERROR_TEXT)
        workflow = BaseModel.model_dump(trusted, mode="json")
        del trusted
        validate_json_value(workflow)
        return {"workflow": workflow}
    except Exception:
        return None


def _persistent_workflow_to_graph_state(
    state: AcademicWorkflowPersistentState,
) -> AcademicWorkflowGraphState:
    result = _try_persistent_workflow_to_graph_state(state)
    del state
    if result is None:
        raise _PersistentOutcomeContractError(_ERROR_TEXT)
    return result


def _try_canonical_persistent_bytes(
    graph_state: AcademicWorkflowGraphState,
) -> bytes | None:
    try:
        if type(graph_state) is not dict:
            return None
        keys = tuple(dict.keys(graph_state))
        if any(type(key) is not str for key in keys) or set(keys) != {"workflow"}:
            return None
        workflow = dict.__getitem__(graph_state, "workflow")
        if type(workflow) is not dict:
            return None
        workflow_keys = tuple(dict.keys(workflow))
        if any(type(key) is not str for key in workflow_keys) or set(
            workflow_keys
        ) not in (set(_PERSISTENT_FIELDS), set(_BASE_FIELDS)):
            raise TypeError(_ERROR_TEXT)
        validate_json_value(workflow)
        return json.dumps(
            workflow,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except Exception:
        return None


def _canonical_persistent_bytes(graph_state: AcademicWorkflowGraphState) -> bytes:
    result = _try_canonical_persistent_bytes(graph_state)
    del graph_state
    if result is None:
        raise _PersistentOutcomeContractError(_ERROR_TEXT)
    return result


def _try_restore_persistent_workflow_state(
    graph_state: AcademicWorkflowGraphState,
) -> AcademicWorkflowPersistentState | None:
    try:
        encoded = _canonical_persistent_bytes(graph_state)
        result = AcademicWorkflowPersistentState.model_validate_json(encoded)
        dumped = BaseModel.model_dump(result, mode="json")
        workflow = dict.__getitem__(graph_state, "workflow")
        expected = dict(workflow)
        if "outcome" not in expected:
            expected["outcome"] = None
        if dumped != expected:
            return None
        return result
    except Exception:
        return None


def _restore_persistent_workflow_state(
    graph_state: AcademicWorkflowGraphState,
) -> AcademicWorkflowPersistentState:
    result = _try_restore_persistent_workflow_state(graph_state)
    del graph_state
    if result is None:
        raise _PersistentOutcomeContractError(_ERROR_TEXT)
    return result
