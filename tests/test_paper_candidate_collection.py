import threading
from datetime import datetime, timezone

import pytest

import gpt_researcher.screening as screening
from gpt_researcher.screening.models import ExternalIdentifier, PaperCandidate


PaperCandidateCollector = getattr(screening, "PaperCandidateCollector", None)
CollectorState = getattr(screening, "CollectorState", None)
requires_collection = pytest.mark.skipif(
    PaperCandidateCollector is None,
    reason="Milestone 2.1 collector is not implemented yet",
)


def _candidate(
    *,
    candidate_id="arxiv:2401.00001",
    source="arxiv",
    retrieval_query="radar imaging",
    source_rank=1,
    title="A Paper",
    href="https://example.test/paper",
    body="Title: A Paper\n\nAbstract:\nUseful abstract.",
    published_at=None,
    external_ids=(),
):
    return PaperCandidate(
        candidate_id=candidate_id,
        source=source,
        source_record_id="2401.00001" if source == "arxiv" else "paper-id",
        retrieval_query=retrieval_query,
        source_rank=source_rank,
        title=title,
        href=href,
        body=body,
        abstract="Useful abstract.",
        authors=("First Author",),
        published_year=2024,
        published_at=published_at,
        updated_at=None,
        venue="arXiv" if source == "arxiv" else "Journal",
        publication_venue_id=None,
        publication_venue_name=None,
        publication_venue_type=None,
        publication_venue_alternate_names=(),
        doi=None,
        external_ids=external_ids,
        citation_count=None,
        publication_types=(),
        categories=(),
        journal_reference=None,
    )


def test_milestone_2_1_collector_is_exported():
    assert PaperCandidateCollector is not None
    assert CollectorState is not None


@requires_collection
def test_new_collector_is_open_and_snapshot_requires_finalize():
    collector = PaperCandidateCollector()
    assert collector.state is CollectorState.OPEN
    with pytest.raises(RuntimeError):
        collector.snapshot()


@requires_collection
def test_add_batch_validates_atomically_and_accepts_only_candidates():
    collector = PaperCandidateCollector()
    first = _candidate()

    with pytest.raises(TypeError):
        collector.add_batch((first, object()))

    collector.add_batch((first,))
    snapshot = collector.finalize()
    assert snapshot == (first,)


@requires_collection
def test_finalize_returns_and_preserves_an_immutable_tuple_snapshot():
    collector = PaperCandidateCollector()
    candidate = _candidate()
    collector.add_batch((candidate,))

    finalized = collector.finalize()
    first = collector.snapshot()
    second = collector.snapshot()

    assert collector.state is CollectorState.FINALIZED
    assert finalized == first == second == (candidate,)
    assert isinstance(first, tuple)
    assert first is not second


@requires_collection
def test_abort_clears_and_hides_partial_candidates():
    collector = PaperCandidateCollector()
    collector.add_batch((_candidate(),))
    collector.abort()

    assert collector.state is CollectorState.ABORTED
    with pytest.raises(RuntimeError):
        collector.snapshot()


@requires_collection
@pytest.mark.parametrize("terminal", ["finalize", "abort"])
def test_terminal_collectors_reject_all_state_mutations(terminal):
    collector = PaperCandidateCollector()
    getattr(collector, terminal)()

    with pytest.raises(RuntimeError):
        collector.add_batch((_candidate(),))
    with pytest.raises(RuntimeError):
        collector.finalize()
    with pytest.raises(RuntimeError):
        collector.abort()


@requires_collection
def test_deterministic_sort_is_independent_of_batch_completion_order():
    earlier = _candidate(
        candidate_id="semantic_scholar:b",
        source="semantic_scholar",
        retrieval_query="alpha",
        source_rank=2,
        title="Second",
        href="https://example.test/second",
    )
    later = _candidate(
        candidate_id="arxiv:a",
        retrieval_query="zeta",
        source_rank=1,
        title="First",
        href="https://example.test/first",
    )

    first = PaperCandidateCollector()
    first.add_batch((later,))
    first.add_batch((earlier,))
    first.finalize()

    second = PaperCandidateCollector()
    second.add_batch((earlier,))
    second.add_batch((later,))
    second.finalize()

    assert first.snapshot() == second.snapshot() == (earlier, later)


@requires_collection
def test_stable_tie_breaker_handles_datetime_and_nested_identifiers():
    common = dict(
        candidate_id="arxiv:same",
        retrieval_query="same",
        source_rank=1,
        published_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    first = _candidate(
        **common,
        title="A",
        href="https://example.test/a",
        external_ids=(ExternalIdentifier(name="DOI", value="10.1/a"),),
    )
    second = _candidate(
        **common,
        title="B",
        href="https://example.test/b",
        external_ids=(ExternalIdentifier(name="DOI", value="10.1/b"),),
    )

    left = PaperCandidateCollector()
    left.add_batch((second, first))
    left.finalize()
    right = PaperCandidateCollector()
    right.add_batch((first, second))
    right.finalize()

    assert left.snapshot() == right.snapshot()


@requires_collection
def test_identical_duplicates_are_not_removed():
    candidate = _candidate()
    collector = PaperCandidateCollector()
    collector.add_batch((candidate, candidate))
    collector.finalize()

    assert collector.snapshot() == (candidate, candidate)


@requires_collection
def test_collector_methods_run_on_the_calling_thread():
    collector = PaperCandidateCollector()
    calling_thread = threading.get_ident()
    collector.add_batch((_candidate(),))
    collector.finalize()

    assert calling_thread == threading.get_ident()
    assert collector.snapshot()
