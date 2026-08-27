"""Mechanical checks for the frozen LangGraph dependency baseline."""

from __future__ import annotations

import ast
import importlib.metadata
from pathlib import Path
import re
import tomllib

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
import pytest

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph


ROOT = Path(__file__).parents[1]
LANGGRAPH_RANGE = SpecifierSet(">=1.2.11,<2")
CHECKPOINT_RANGE = SpecifierSet(">=4.2.0,<5")
PERSISTENT_SAVER_PREFIX = "langgraph-checkpoint-"


def _requirement_map(lines: list[str]) -> dict[str, Requirement]:
    requirements: dict[str, Requirement] = {}
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        line = line.split("  #", maxsplit=1)[0].strip()
        requirement = Requirement(line)
        requirements[canonicalize_name(requirement.name)] = requirement
    return requirements


def _assert_range(requirement: Requirement, expected: SpecifierSet) -> None:
    assert requirement.specifier == expected


def _assert_no_persistent_saver_names(names: set[str]) -> None:
    forbidden = sorted(
        canonicalize_name(name)
        for name in names
        if canonicalize_name(name).startswith(PERSISTENT_SAVER_PREFIX)
    )
    assert forbidden == []


def test_installed_versions_and_exact_import_paths() -> None:
    assert importlib.metadata.version("langgraph") in LANGGRAPH_RANGE
    assert importlib.metadata.version("langgraph-checkpoint") in CHECKPOINT_RANGE
    assert StateGraph is not None
    assert START is not None
    assert END is not None
    assert InMemorySaver is not None


def test_poetry_and_pep621_use_exact_ranges() -> None:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    poetry = data["tool"]["poetry"]["dependencies"]
    assert SpecifierSet(poetry["langgraph"]) == LANGGRAPH_RANGE
    assert SpecifierSet(poetry["langgraph-checkpoint"]) == CHECKPOINT_RANGE

    pep621 = _requirement_map(data["project"]["dependencies"])
    _assert_range(pep621["langgraph"], LANGGRAPH_RANGE)
    _assert_range(pep621["langgraph-checkpoint"], CHECKPOINT_RANGE)


def test_root_and_multi_agent_requirements_use_exact_ranges() -> None:
    root_requirements = _requirement_map(
        (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
    )
    _assert_range(root_requirements["langgraph"], LANGGRAPH_RANGE)
    _assert_range(root_requirements["langgraph-checkpoint"], CHECKPOINT_RANGE)

    multi_agent_requirements = _requirement_map(
        (ROOT / "multi_agents" / "requirements.txt")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    _assert_range(multi_agent_requirements["langgraph"], LANGGRAPH_RANGE)
    assert "langgraph-checkpoint" not in multi_agent_requirements


def _literal_string_collection(node: ast.AST) -> set[str]:
    values: set[str] = set()
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        for item in node.elts:
            if isinstance(item, ast.Constant) and isinstance(item.value, str):
                values.add(item.value.lower().replace("_", "-"))
    return values


def test_setup_does_not_filter_langgraph_dependencies() -> None:
    source = (ROOT / "setup.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    exclusions: set[str] | None = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "exclude_packages"
            for target in node.targets
        ):
            exclusions = _literal_string_collection(node.value)
            break
    assert exclusions is not None
    assert "langgraph" not in exclusions
    assert "langgraph-checkpoint" not in exclusions


def test_no_dependency_entry_adds_a_persistent_saver() -> None:
    pyproject = tomllib.loads(
        (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    poetry_names = {
        canonicalize_name(name)
        for name in pyproject["tool"]["poetry"]["dependencies"]
    }
    pep621_names = set(
        _requirement_map(pyproject["project"]["dependencies"])
    )
    root_names = set(
        _requirement_map(
            (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
        )
    )
    multi_names = set(
        _requirement_map(
            (ROOT / "multi_agents" / "requirements.txt")
            .read_text(encoding="utf-8")
            .splitlines()
        )
    )
    setup_tokens = {
        canonicalize_name(token)
        for token in re.findall(
            r"[A-Za-z0-9_.-]+", (ROOT / "setup.py").read_text(encoding="utf-8")
        )
    }
    for names in (poetry_names, pep621_names, root_names, multi_names, setup_tokens):
        _assert_no_persistent_saver_names(names)


def test_persistent_saver_classifier_rejects_every_checkpoint_extension() -> None:
    _assert_no_persistent_saver_names({"langgraph-checkpoint"})
    for name in (
        "langgraph-checkpoint-sqlite",
        "langgraph-checkpoint-postgres",
        "langgraph-checkpoint-redis",
        "langgraph-checkpoint-future-backend",
        "LANGGRAPH-CHECKPOINT-FUTURE-BACKEND",
        "langgraph_checkpoint_future_backend",
        "langgraph.checkpoint.future.backend",
        "langgraph__checkpoint__future__backend",
    ):
        with pytest.raises(AssertionError):
            _assert_no_persistent_saver_names({name})
