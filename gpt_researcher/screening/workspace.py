"""Run/web-pass-scoped state for Basic/Web paper screening."""

import hashlib
import json
from enum import Enum

from .decisions import (
    CandidateOccurrence,
    RetrievalRequestRoute,
    ScreeningPolicy,
    ScreeningResult,
)
from .models import PaperCandidate
from .rules import screen_paper_occurrences


class WorkspaceState(str, Enum):
    OPEN = "open"
    SCREENED = "screened"
    FINALIZED = "finalized"
    ABORTED = "aborted"


def _strict_positive_index(value: int, name: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name} must be a strict positive integer")
    return value


def _nonblank(value: str, name: str) -> str:
    if type(value) is not str:
        raise TypeError(f"{name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{name} must not be blank")
    return normalized


def build_occurrence_id(
    retrieval_request_id: str,
    retriever_index: int,
    candidate_index: int,
    candidate_id: str,
) -> str:
    """Build the frozen unambiguous run-local occurrence identity."""
    request_id = _nonblank(retrieval_request_id, "retrieval_request_id")
    candidate_identity = _nonblank(candidate_id, "candidate_id")
    provider_position = _strict_positive_index(retriever_index, "retriever_index")
    candidate_position = _strict_positive_index(candidate_index, "candidate_index")
    payload = json.dumps(
        [request_id, provider_position, candidate_position, candidate_identity],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"occurrence:sha256:{digest}"


class ScreeningWorkspace:
    """Own only screening identities, occurrences, and one screened result."""

    def __init__(self, policy: ScreeningPolicy):
        if not isinstance(policy, ScreeningPolicy):
            raise TypeError("policy must be a ScreeningPolicy")
        self._policy = policy
        self._state = WorkspaceState.OPEN
        self._request_kinds: dict[str, bool] = {}
        self._occurrences: list[CandidateOccurrence] = []
        self._occurrence_by_id: dict[str, CandidateOccurrence] = {}
        self._result: ScreeningResult | None = None
        self._routes: dict[str, RetrievalRequestRoute] = {}

    @property
    def state(self) -> WorkspaceState:
        return self._state

    @property
    def policy(self) -> ScreeningPolicy:
        return self._policy

    @property
    def result(self) -> ScreeningResult:
        self._require_screened("read the screening result")
        assert self._result is not None
        return self._result

    def add_request(self, retrieval_request_id: str, *, planning_only: bool) -> None:
        self._require_open("add a retrieval request")
        request_id = _nonblank(retrieval_request_id, "retrieval_request_id")
        if type(planning_only) is not bool:
            raise TypeError("planning_only must be a strict boolean")
        previous = self._request_kinds.get(request_id)
        if previous is not None and previous is not planning_only:
            raise ValueError("retrieval request has conflicting planning_only metadata")
        if planning_only and any(self._request_kinds.values()) and request_id not in self._request_kinds:
            raise ValueError("workspace accepts only one planning request")
        self._request_kinds.setdefault(request_id, planning_only)

    def add_candidates(
        self,
        retrieval_request_id: str,
        planning_only: bool,
        retriever_index: int,
        candidates: tuple[PaperCandidate, ...],
    ) -> tuple[str, ...]:
        self._require_open("add candidates")
        if type(candidates) is not tuple:
            raise TypeError("candidate batch must be exactly a tuple")
        if not all(isinstance(candidate, PaperCandidate) for candidate in candidates):
            raise TypeError("candidate batch must contain only PaperCandidate values")
        request_id = _nonblank(retrieval_request_id, "retrieval_request_id")
        if request_id not in self._request_kinds:
            raise ValueError("retrieval request must be registered before candidates")
        if self._request_kinds[request_id] is not planning_only:
            raise ValueError("candidate batch planning_only metadata conflicts")
        _strict_positive_index(retriever_index, "retriever_index")

        pending: list[CandidateOccurrence] = []
        pending_ids: set[str] = set()
        for candidate_index, candidate in enumerate(candidates, start=1):
            occurrence_id = build_occurrence_id(
                request_id,
                retriever_index,
                candidate_index,
                candidate.candidate_id,
            )
            if occurrence_id in self._occurrence_by_id or occurrence_id in pending_ids:
                raise ValueError("occurrence_id collision")
            pending_ids.add(occurrence_id)
            pending.append(
                CandidateOccurrence(
                    occurrence_id=occurrence_id,
                    retrieval_request_id=request_id,
                    planning_only=planning_only,
                    candidate=candidate,
                )
            )

        self._occurrences.extend(pending)
        self._occurrence_by_id.update(
            (occurrence.occurrence_id, occurrence) for occurrence in pending
        )
        return tuple(occurrence.occurrence_id for occurrence in pending)

    def screen(self) -> ScreeningResult:
        self._require_open("screen occurrences")
        occurrences = tuple(self._occurrences)
        try:
            result = screen_paper_occurrences(occurrences, self._policy)
            routes = {route.retrieval_request_id: route for route in result.routes}
            if len(routes) != len(result.routes):
                raise ValueError("screening result contains duplicate routes")
            for request_id in routes:
                if request_id not in self._request_kinds:
                    raise ValueError("screening result contains an unknown request")
        except BaseException:
            self._clear_screening_state()
            self._state = WorkspaceState.ABORTED
            raise

        self._result = result
        self._routes = routes
        self._state = WorkspaceState.SCREENED
        return result

    def route_for(self, retrieval_request_id: str) -> tuple[str, ...]:
        self._require_screened("read a route")
        request_id = _nonblank(retrieval_request_id, "retrieval_request_id")
        if request_id not in self._request_kinds:
            raise KeyError("unknown retrieval request")
        route = self._routes.get(request_id)
        return route.canonical_occurrence_ids if route is not None else ()

    def canonical_candidate(self, occurrence_id: str) -> PaperCandidate:
        self._require_screened("resolve a canonical candidate")
        normalized = _nonblank(occurrence_id, "occurrence_id")
        if self._result is None or normalized not in set(
            self._result.included_canonical_occurrence_ids
        ):
            raise KeyError("occurrence is not an included canonical")
        try:
            return self._occurrence_by_id[normalized].candidate
        except KeyError:
            raise ValueError("canonical occurrence does not resolve") from None

    def finalize(self) -> None:
        self._require_screened("finalize")
        self._state = WorkspaceState.FINALIZED

    def abort(self) -> None:
        if self._state not in (WorkspaceState.OPEN, WorkspaceState.SCREENED):
            raise RuntimeError(f"cannot abort when workspace is {self._state.value}")
        self._clear_screening_state()
        self._state = WorkspaceState.ABORTED

    def _clear_screening_state(self) -> None:
        self._request_kinds.clear()
        self._occurrences.clear()
        self._occurrence_by_id.clear()
        self._routes.clear()
        self._result = None

    def _require_open(self, operation: str) -> None:
        if self._state is not WorkspaceState.OPEN:
            raise RuntimeError(f"cannot {operation} when workspace is {self._state.value}")

    def _require_screened(self, operation: str) -> None:
        if self._state is not WorkspaceState.SCREENED:
            raise RuntimeError(f"cannot {operation} when workspace is {self._state.value}")
