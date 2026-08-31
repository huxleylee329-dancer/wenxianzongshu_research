import hashlib
import json
import socket

import pytest

from gpt_researcher.screening.decisions import ScreeningPolicy
from gpt_researcher.screening.models import PaperCandidate
from gpt_researcher.screening.workspace import (
    ScreeningWorkspace,
    WorkspaceState,
    build_occurrence_id,
)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    monkeypatch.setattr(
        socket,
        "create_connection",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("real network is forbidden")
        ),
    )


def _candidate(candidate_id="arxiv:one", title="A sufficiently long paper title"):
    return PaperCandidate(
        candidate_id=candidate_id,
        source="arxiv",
        source_record_id="one",
        retrieval_query="query",
        source_rank=1,
        title=title,
        href="https://example.test/paper",
        body="academic body",
        abstract="abstract",
        authors=(),
        published_year=2024,
        published_at=None,
        updated_at=None,
        venue=None,
        publication_venue_id=None,
        publication_venue_name=None,
        publication_venue_type=None,
        publication_venue_alternate_names=(),
        doi=None,
        external_ids=(),
        citation_count=None,
        publication_types=(),
        categories=(),
        journal_reference=None,
    )


def test_occurrence_id_uses_frozen_json_sha256_algorithm():
    expected_payload = json.dumps(
        ["evidence:000002", 3, 4, "arxiv:one"],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    expected = hashlib.sha256(expected_payload.encode("utf-8")).hexdigest()

    assert build_occurrence_id("evidence:000002", 3, 4, "arxiv:one") == (
        f"occurrence:sha256:{expected}"
    )


@pytest.mark.parametrize("retriever_index,candidate_index", [(0, 1), (1, 0), (-1, 1)])
def test_occurrence_id_requires_strict_positive_indexes(retriever_index, candidate_index):
    with pytest.raises((TypeError, ValueError)):
        build_occurrence_id(
            "evidence:000001", retriever_index, candidate_index, "arxiv:one"
        )


def test_workspace_state_machine_and_route_lookup():
    policy = ScreeningPolicy()
    workspace = ScreeningWorkspace(policy)
    candidate = _candidate()

    assert workspace.state is WorkspaceState.OPEN
    workspace.add_request("planning:000001", planning_only=True)
    workspace.add_request("evidence:000001", planning_only=False)
    planning_ids = workspace.add_candidates(
        "planning:000001", True, 1, (candidate,)
    )
    evidence_ids = workspace.add_candidates(
        "evidence:000001", False, 1, (candidate,)
    )

    result = workspace.screen()
    assert workspace.state is WorkspaceState.SCREENED
    assert workspace.policy is policy
    assert workspace.route_for("planning:000001") == ()
    assert workspace.route_for("evidence:000001") == planning_ids
    assert workspace.canonical_candidate(planning_ids[0]) is candidate
    assert evidence_ids[0] != planning_ids[0]
    assert result is workspace.result

    workspace.finalize()
    assert workspace.state is WorkspaceState.FINALIZED
    with pytest.raises(RuntimeError):
        workspace.add_request("evidence:000002", planning_only=False)
    with pytest.raises(RuntimeError):
        workspace.screen()


def test_workspace_rejects_request_kind_conflict_and_occurrence_collision(monkeypatch):
    workspace = ScreeningWorkspace(ScreeningPolicy())
    workspace.add_request("evidence:000001", planning_only=False)
    with pytest.raises(ValueError):
        workspace.add_request("evidence:000001", planning_only=True)

    monkeypatch.setattr(
        "gpt_researcher.screening.workspace.build_occurrence_id",
        lambda *_args: "occurrence:sha256:" + "0" * 64,
    )
    workspace.add_candidates("evidence:000001", False, 1, (_candidate(),))
    with pytest.raises(ValueError, match="occurrence"):
        workspace.add_candidates(
            "evidence:000001", False, 2, (_candidate("arxiv:two"),)
        )


def test_screen_failure_is_atomic_and_aborts(monkeypatch):
    workspace = ScreeningWorkspace(ScreeningPolicy())
    workspace.add_request("evidence:000001", planning_only=False)
    workspace.add_candidates("evidence:000001", False, 1, (_candidate(),))
    original = RuntimeError("screen failed")

    def fail(*_args, **_kwargs):
        raise original

    monkeypatch.setattr(
        "gpt_researcher.screening.workspace.screen_paper_occurrences", fail
    )
    with pytest.raises(RuntimeError) as raised:
        workspace.screen()
    assert raised.value is original
    assert workspace.state is WorkspaceState.ABORTED
    with pytest.raises(RuntimeError):
        workspace.result


def test_abort_only_clears_workspace_owned_state():
    ordinary = [{"url": "https://ordinary.test", "raw_content": "x" * 101}]
    mcp_cache = [{"content": "mcp"}]
    visited = {"https://visited.test"}
    sources = [{"url": "https://source.test"}]
    snapshots = (list(ordinary), list(mcp_cache), set(visited), list(sources))

    workspace = ScreeningWorkspace(ScreeningPolicy())
    workspace.add_request("evidence:000001", planning_only=False)
    workspace.add_candidates("evidence:000001", False, 1, (_candidate(),))
    workspace.abort()

    assert workspace.state is WorkspaceState.ABORTED
    assert ordinary == snapshots[0]
    assert mcp_cache == snapshots[1]
    assert visited == snapshots[2]
    assert sources == snapshots[3]
    assert not any(
        name in vars(workspace)
        for name in (
            "ordinary_urls",
            "prefetched_content",
            "mcp_context",
            "scraped_data",
            "compressed_context",
            "visited_urls",
            "research_sources",
        )
    )


def test_empty_registered_request_has_empty_route():
    workspace = ScreeningWorkspace(ScreeningPolicy())
    workspace.add_request("planning:000001", planning_only=True)
    workspace.add_request("evidence:000001", planning_only=False)
    workspace.screen()
    assert workspace.route_for("planning:000001") == ()
    assert workspace.route_for("evidence:000001") == ()
