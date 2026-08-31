"""Run-scoped collection for immutable academic paper candidates."""

import json
from enum import Enum
from typing import Iterable

from .models import PaperCandidate


class CollectorState(str, Enum):
    """Lifecycle states for one candidate-collection run."""

    OPEN = "open"
    FINALIZED = "finalized"
    ABORTED = "aborted"


def _stable_candidate_payload(candidate: PaperCandidate) -> str:
    """Serialize a candidate deterministically using declared model-field order."""
    data = candidate.model_dump(mode="json")
    ordered = {
        field_name: data[field_name]
        for field_name in PaperCandidate.model_fields
    }
    return json.dumps(
        ordered,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=False,
    )


def _candidate_sort_key(candidate: PaperCandidate) -> tuple:
    return (
        candidate.retrieval_query,
        candidate.source,
        candidate.source_rank,
        candidate.candidate_id,
        _stable_candidate_payload(candidate),
    )


class PaperCandidateCollector:
    """Collect candidates for exactly one top-level research run."""

    def __init__(self) -> None:
        self._state = CollectorState.OPEN
        self._candidates: list[PaperCandidate] = []
        self._snapshot: tuple[PaperCandidate, ...] | None = None

    @property
    def state(self) -> CollectorState:
        return self._state

    def add_batch(self, candidates: Iterable[PaperCandidate]) -> None:
        self._require_open("add candidates")
        batch = tuple(candidates)
        if not all(isinstance(candidate, PaperCandidate) for candidate in batch):
            raise TypeError("candidate batches must contain only PaperCandidate values")
        self._candidates.extend(batch)

    def finalize(self) -> tuple[PaperCandidate, ...]:
        self._require_open("finalize")
        self._snapshot = tuple(sorted(self._candidates, key=_candidate_sort_key))
        self._candidates.clear()
        self._state = CollectorState.FINALIZED
        return self.snapshot()

    def abort(self) -> None:
        self._require_open("abort")
        self._candidates.clear()
        self._snapshot = None
        self._state = CollectorState.ABORTED

    def snapshot(self) -> tuple[PaperCandidate, ...]:
        if self._state is not CollectorState.FINALIZED or self._snapshot is None:
            raise RuntimeError("paper candidates are available only after finalization")
        return tuple(list(self._snapshot))

    def _require_open(self, operation: str) -> None:
        if self._state is not CollectorState.OPEN:
            raise RuntimeError(
                f"cannot {operation} when collector is {self._state.value}"
            )
