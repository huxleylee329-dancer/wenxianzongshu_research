"""Load byte-identical production contracts without importing the app or providers.

Private production helpers are deliberately reused, not reimplemented. Every
dataset pins the source bytes; a change requires regenerating/reviewing data.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import sys
import types
from pathlib import Path


HERE = Path(__file__).resolve().parent
SOURCE_NAMES = (
    "report_profiles.py", "state.py", "section_writer.py",
    "section_merger.py", "citation_evidence_gate.py", "citation_reviewer.py",
    "citation_review_disposition.py", "references_renderer.py",
    "academic_draft_composer.py", "workflow_outcome.py",
)
SOURCE_DIR = HERE / "production"
if not SOURCE_DIR.is_dir():
    SOURCE_DIR = HERE.parents[1] / "gpt_researcher/workflows/academic_writing"


def canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False,
                      sort_keys=True, separators=(",", ":"))


def digest(value: str | bytes) -> str:
    raw = value.encode("utf-8") if type(value) is str else value
    return hashlib.sha256(raw).hexdigest()


def source_hashes() -> dict[str, str]:
    return {name: digest((SOURCE_DIR / name).read_bytes()) for name in SOURCE_NAMES}


def production(name: str):
    if name + ".py" not in SOURCE_NAMES:
        raise ValueError("module outside training allowlist")
    package_name = "gpt_researcher.workflows.academic_writing"
    # Several DTO modules use absolute imports. Preserve their genuine module
    # identities, but skip application __init__ in this standalone tool process.
    for parent in ("gpt_researcher", "gpt_researcher.workflows", package_name):
        if parent not in sys.modules:
            package = types.ModuleType(parent)
            package.__path__ = [str(SOURCE_DIR)] if parent == package_name else []
            sys.modules[parent] = package
    return importlib.import_module(package_name + "." + name)


def approved_view(payload: dict):
    """Validate a real checkpoint, then make an in-memory writer execution view."""
    outcomes = production("workflow_outcome")
    state_module = production("state")
    value = payload["workflow"] if set(payload) == {"workflow"} else payload
    restored = outcomes._restore_persistent_workflow_state({"workflow": value})
    if restored.outline_decision is None or restored.outline_decision.decision != "approve":
        raise ValueError("an actually approved checkpoint is required")
    if restored.phase not in ("outline_approved", "draft_ready", "review_required"):
        raise ValueError("checkpoint phase cannot produce writer examples")
    data = restored.model_dump(mode="json", exclude={"outcome"})
    data.update(phase="outline_approved", status="completed", errors=[])
    data["events"] = data["events"][:8] + [{
        "event_id": "event:000009", "order": 9, "event_type": "workflow_completed",
        "node_id": None, "attempt": 1,
    }]
    return state_module.AcademicWorkflowState.model_validate_json(canonical(data))


def writer_prompt(state, section_id: str) -> tuple[list[dict[str, str]], list[str]]:
    writer = production("section_writer")
    projection = writer._prepare_attempt(state, section_id)
    if type(projection) is not tuple:
        raise ValueError("production writer preflight rejected example")
    _, _, user_message, ids = projection
    return ([{"role": "system", "content": writer._SYSTEM_MESSAGE},
             {"role": "user", "content": user_message}], list(ids))


def parse_response(response: object, allowed_source_ids: list[str]) -> str | None:
    """One raw completion, no adapter, retry, client, Config or provider calls."""
    writer = production("section_writer")
    parsed = writer._parse_response(response, allowed_source_ids=tuple(allowed_source_ids))
    if parsed is writer._CONTRACT_FAILURE:
        raise RuntimeError("production parser contract failure")
    return parsed if type(parsed) is str else None
