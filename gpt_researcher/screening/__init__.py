"""Internal paper-screening models and run-scoped collection."""

from .collection import CollectorState, PaperCandidateCollector
from .decisions import (
    CandidateOccurrence,
    DuplicateGroup,
    PaperType,
    RetrievalRequestRoute,
    ScreeningDecision,
    ScreeningPolicy,
    ScreeningReasonCode,
    ScreeningResult,
    UnknownValuePolicy,
)
from .models import ExternalIdentifier, PaperCandidate, build_candidate_id
from .rules import screen_paper_occurrences

__all__ = [
    "CandidateOccurrence",
    "CollectorState",
    "DuplicateGroup",
    "ExternalIdentifier",
    "PaperCandidate",
    "PaperCandidateCollector",
    "PaperType",
    "RetrievalRequestRoute",
    "ScreeningDecision",
    "ScreeningPolicy",
    "ScreeningReasonCode",
    "ScreeningResult",
    "UnknownValuePolicy",
    "build_candidate_id",
    "screen_paper_occurrences",
]
