"""Offline contract, integration, recovery, and safety tests for Milestone 3.1."""

from __future__ import annotations

import asyncio
import ast
import builtins
from contextlib import contextmanager
import gc
import importlib
import importlib.metadata
import inspect
import io
import logging
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import threading
from types import (
    BuiltinFunctionType,
    BuiltinMethodType,
    ClassMethodDescriptorType,
    CodeType,
    FrameType,
    FunctionType,
    GetSetDescriptorType,
    MemberDescriptorType,
    MethodDescriptorType,
    MethodType,
    MethodWrapperType,
    ModuleType,
    SimpleNamespace,
    TracebackType,
    WrapperDescriptorType,
)
from typing import Callable, get_type_hints
import urllib.request

import aiohttp
import httpx
import pytest
import pytest_asyncio
import requests
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import StateGraph

from gpt_researcher.screening.audit import (
    AUDIT_UNAVAILABLE_MESSAGE,
    PaperScreeningAuditEntry,
    PaperScreeningAuditSnapshot,
    PaperScreeningOccurrenceRef,
)
from gpt_researcher.screening.models import PaperCandidate
from gpt_researcher.utils.enum import Tone
from gpt_researcher.workflows.academic_writing.adapters import AcademicWritingAdapter
from gpt_researcher.workflows.academic_writing.graph import (
    resume_academic_workflow,
    start_academic_workflow,
)
from gpt_researcher.workflows.academic_writing.report_profiles import (
    _get_report_profile,
)
from gpt_researcher.workflows.academic_writing.state import (
    AcademicWorkflowIdentity,
    AcademicWorkflowRequest,
    AdapterFailure,
    ExecutionError,
    ThreadProtocolError,
    WorkflowOutline,
    WorkflowOutlineSection,
    WorkflowEvidenceProvenance,
    WorkflowEvidenceSource,
    WorkflowResearchEvidence,
    WorkflowTopicPlan,
)
from gpt_researcher.workflows.academic_writing.research_evidence import (
    GPTResearcherResearchEvidenceAdapter,
    ResearcherFactory,
    _ResearchEvidenceContractError,
    _create_production_researcher,
)


_EXTERNAL_ENTRYPOINTS: tuple[tuple[str, str], ...] = (
    ("gpt_researcher", "GPTResearcher"),
    ("gpt_researcher.agent", "GPTResearcher"),
    ("gpt_researcher.skills.researcher", "ResearchConductor"),
    ("gpt_researcher.utils.llm", "create_chat_completion"),
    ("gpt_researcher.actions.web_scraping", "scrape_urls"),
    ("gpt_researcher.actions.retriever", "get_retriever"),
    ("gpt_researcher.actions.retriever", "get_retrievers"),
    ("gpt_researcher.llm_provider.generic.base", "GenericLLMProvider"),
    ("gpt_researcher.llm_provider.generic.base", "_check_pkg"),
    ("gpt_researcher.context.retriever", "SearchAPIRetriever"),
    ("gpt_researcher.context.retriever", "SectionRetriever"),
    ("gpt_researcher.context.compression", "VectorstoreCompressor"),
    ("gpt_researcher.context.compression", "WrittenContentCompressor"),
    ("gpt_researcher.context.compression", "ContextualCompressionRetriever"),
    ("gpt_researcher.context.compression", "DocumentCompressorPipeline"),
    ("gpt_researcher.context.compression", "ContextCompressor"),
    (
        "gpt_researcher.llm_provider.image.image_generator",
        "ImageGeneratorProvider",
    ),
    (
        "gpt_researcher.llm_provider.image.modelslab_image_generator",
        "ModelsLabImageGeneratorProvider",
    ),
    ("gpt_researcher.skills.image_generator", "ImageGenerator"),
    ("gpt_researcher.scraper.arxiv.arxiv", "ArxivScraper"),
    (
        "gpt_researcher.scraper.beautiful_soup.beautiful_soup",
        "BeautifulSoupScraper",
    ),
    ("gpt_researcher.scraper.browser.browser", "BrowserScraper"),
    ("gpt_researcher.scraper.browser.nodriver_scraper", "NoDriverScraper"),
    ("gpt_researcher.scraper.pymupdf.pymupdf", "PyMuPDFScraper"),
    (
        "gpt_researcher.scraper.web_base_loader.web_base_loader",
        "WebBaseLoaderScraper",
    ),
    ("gpt_researcher.scraper.scraper", "Scraper"),
    (
        "gpt_researcher.scraper.browser.processing.scrape_skills",
        "ArxivRetriever",
    ),
    ("gpt_researcher.retrievers.mcp.retriever", "MCPRetriever"),
    ("gpt_researcher.mcp.client", "MCPClientManager"),
    ("gpt_researcher.retrievers.utils", "check_pkg"),
    ("gpt_researcher.actions.utils", "stream_output"),
    ("gpt_researcher.actions.utils", "safe_send_json"),
    ("gpt_researcher.skills.researcher", "stream_output"),
    ("gpt_researcher.skills.image_generator", "stream_output"),
    ("gpt_researcher.retrievers.utils", "stream_output"),
)

_RAW_SECRET = "RESEARCH-EVIDENCE-RAW-SECRET"
_CONTRACT_SECRET = object()


def _resolve_external_entrypoints() -> dict[tuple[str, str], object]:
    resolved: dict[tuple[str, str], object] = {}
    for module_name, attribute_name in _EXTERNAL_ENTRYPOINTS:
        module = importlib.import_module(module_name)
        try:
            value = inspect.getattr_static(module, attribute_name)
        except AttributeError as error:
            raise AssertionError(
                f"missing external entrypoint: {module_name}.{attribute_name}"
            ) from error
        resolved[(module_name, attribute_name)] = value
    return resolved


@contextmanager
def _patched_external_entrypoints(
    replacement: object,
    originals: dict[tuple[str, str], object] | None = None,
):
    if originals is None:
        originals = _resolve_external_entrypoints()
    try:
        for module_name, attribute_name in _EXTERNAL_ENTRYPOINTS:
            setattr(importlib.import_module(module_name), attribute_name, replacement)
        yield originals
    finally:
        for (module_name, attribute_name), original in originals.items():
            setattr(importlib.import_module(module_name), attribute_name, original)
        restored = _resolve_external_entrypoints()
        assert all(restored[key] is value for key, value in originals.items())


@pytest_asyncio.fixture(autouse=True)
async def _fail_fast_external_io(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
):
    def blocked(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("external I/O is forbidden in research-evidence tests")

    class BlockedPopen:
        @classmethod
        def __class_getitem__(cls, item: object):
            return cls

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            blocked()

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(requests.sessions.Session, "request", blocked)
    monkeypatch.setattr(urllib.request, "urlopen", blocked)
    monkeypatch.setattr(httpx.Client, "request", blocked)
    monkeypatch.setattr(httpx.AsyncClient, "request", blocked)
    monkeypatch.setattr(aiohttp.ClientSession, "_request", blocked)
    monkeypatch.setattr(subprocess, "Popen", BlockedPopen)
    monkeypatch.setattr(subprocess, "run", blocked)
    monkeypatch.setattr(subprocess, "call", blocked)
    monkeypatch.setattr(subprocess, "check_call", blocked)
    monkeypatch.setattr(subprocess, "check_output", blocked)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", blocked)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", blocked)

    real_open = builtins.open
    real_io_open = io.open
    real_os_open = os.open
    real_path_open = Path.open
    real_path_read_text = Path.read_text
    real_path_read_bytes = Path.read_bytes
    approved_read_paths = frozenset(
        {
            Path(__file__).resolve(),
            Path(
                "gpt_researcher/workflows/academic_writing/research_evidence.py"
            ).resolve(),
            (
                Path(importlib.metadata.distribution("arxiv")._path) / "METADATA"
            ).resolve(),
            Path(
                ".venv/Lib/site-packages/anyio/streams/__pycache__/"
                "file.cpython-311-pytest-9.1.1.pyc"
            ).resolve(),
            Path(
                ".venv/Lib/site-packages/anyio/streams/__pycache__/"
                "text.cpython-311-pytest-9.1.1.pyc"
            ).resolve(),
        }
    )

    def approved_path(file: object) -> Path:
        if type(file) is int:
            blocked(file)
        try:
            resolved = Path(os.fspath(file)).resolve()  # type: ignore[arg-type]
        except (TypeError, ValueError, OSError):
            blocked(file)
            raise AssertionError("unreachable")
        if resolved not in approved_read_paths:
            blocked(resolved)
        return resolved

    def guarded_open(file: object, mode: str = "r", *args: object, **kwargs: object):
        if type(mode) is not str or any(
            marker in mode for marker in ("w", "a", "x", "+")
        ):
            blocked(file, mode)
        approved_path(file)
        return real_open(file, mode, *args, **kwargs)

    def guarded_io_open(
        file: object, mode: str = "r", *args: object, **kwargs: object
    ):
        if type(mode) is not str or any(
            marker in mode for marker in ("w", "a", "x", "+")
        ):
            blocked(file, mode)
        approved_path(file)
        return real_io_open(file, mode, *args, **kwargs)

    def guarded_os_open(path: object, flags: int, *args: object, **kwargs: object):
        write_flags = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC
        if type(flags) is not int or flags & write_flags:
            blocked(path, flags)
        approved_path(path)
        return real_os_open(path, flags, *args, **kwargs)

    def guarded_path_open(
        path: Path, mode: str = "r", *args: object, **kwargs: object
    ):
        if type(mode) is not str or any(
            marker in mode for marker in ("w", "a", "x", "+")
        ):
            blocked(path, mode)
        approved_path(path)
        return real_path_open(path, mode, *args, **kwargs)

    def guarded_path_read_text(path: Path, *args: object, **kwargs: object):
        approved_path(path)
        return real_path_read_text(path, *args, **kwargs)

    def guarded_path_read_bytes(path: Path):
        approved_path(path)
        return real_path_read_bytes(path)

    monkeypatch.setattr(builtins, "open", guarded_open)
    monkeypatch.setattr(io, "open", guarded_io_open)
    monkeypatch.setattr(os, "open", guarded_os_open)
    monkeypatch.setattr(Path, "open", guarded_path_open)
    monkeypatch.setattr(Path, "read_text", guarded_path_read_text)
    monkeypatch.setattr(Path, "read_bytes", guarded_path_read_bytes)

    # Resolve project entrypoints only after the second I/O barrier is active.
    originals = _resolve_external_entrypoints()
    import gpt_researcher

    agent_module = sys.modules["gpt_researcher.agent"]
    real_agent_researcher = inspect.getattr_static(agent_module, "GPTResearcher")
    real_root_researcher = inspect.getattr_static(gpt_researcher, "GPTResearcher")
    assert real_agent_researcher is real_root_researcher
    with _patched_external_entrypoints(blocked, originals):
        yield {
            "blocked": blocked,
            "originals": originals,
            "agent_module": agent_module,
            "real_agent_researcher": real_agent_researcher,
            "real_root_researcher": real_root_researcher,
            "approved_read_paths": approved_read_paths,
        }


def _request(**changes: object) -> AcademicWorkflowRequest:
    values: dict[str, object] = {
        "workflow_mode": "academic_langgraph",
        "workflow_id": "workflow-1",
        "thread_id": "thread-1",
        "run_id": "run-1",
        "query": "original query",
        "report_type": "research_report",
        "report_source": "web",
        "tone": Tone.Objective.value,
        "language": "English",
        "source_urls": ("https://seed.example",),
        "document_urls": ("https://doc.example",),
        "query_domains": ("example.org",),
        "max_search_results": 7,
        "report_mode": "stem_literature_review",
        "report_locale": "zh-CN",
    }
    values.update(changes)
    return AcademicWorkflowRequest(**values)


def _plan() -> WorkflowTopicPlan:
    return WorkflowTopicPlan(
        topic_plan_id="topic-plan:000001",
        workflow_id="workflow-1",
        run_id="run-1",
        attempt=1,
        research_topic="bounded research topic",
        research_questions=("question one",),
    )


def _outline() -> WorkflowOutline:
    profile = _get_report_profile("stem_literature_review")
    assert profile is not None
    return WorkflowOutline(
        outline_id="outline:000001",
        evidence_id="evidence:000001",
        attempt=1,
        title="Outline",
        sections=tuple(
            WorkflowOutlineSection(
                section_id=f"section:{order:06d}",
                order=order,
                title=title,
                brief=f"Brief {order}",
                section_role=role,
            )
            for order, (role, title) in enumerate(profile, start=1)
        ),
        report_mode="stem_literature_review",
        report_locale="zh-CN",
    )


def _candidate(
    *,
    candidate_id: str = "arxiv:1",
    title: str = "Academic source",
    href: str = "https://paper.example/1",
    rank: int = 1,
    body: str = "forbidden body",
) -> PaperCandidate:
    return PaperCandidate(
        candidate_id=candidate_id,
        source="arxiv",
        source_record_id="1",
        retrieval_query="query",
        source_rank=rank,
        title=title,
        href=href,
        body=body,
        abstract="forbidden abstract",
    )


def _audit_for(candidate: PaperCandidate, *, duplicate_ref: bool = False):
    entry = PaperScreeningAuditEntry.model_construct(
        web_pass_id="web-pass:000001",
        occurrence_id="occurrence:000001",
        candidate_id=candidate.candidate_id,
        title=candidate.title,
        href=candidate.href,
        routed_to_evidence=True,
        planning_only=False,
    )
    ref = PaperScreeningOccurrenceRef(
        web_pass_id="web-pass:000001", occurrence_id="occurrence:000001"
    )
    refs = (ref, ref) if duplicate_ref else (ref,)
    return PaperScreeningAuditSnapshot.model_construct(
        occurrence_entries=(entry,), routed_canonical_occurrence_refs=refs
    )


def _audit_entry(
    candidate: PaperCandidate,
    *,
    web_pass_id: str,
    occurrence_id: str,
    routed_to_evidence: bool = True,
    planning_only: bool = False,
) -> PaperScreeningAuditEntry:
    return PaperScreeningAuditEntry.model_construct(
        web_pass_id=web_pass_id,
        occurrence_id=occurrence_id,
        candidate_id=candidate.candidate_id,
        title=candidate.title,
        href=candidate.href,
        routed_to_evidence=routed_to_evidence,
        planning_only=planning_only,
    )


def _audit_snapshot(
    entries: tuple[PaperScreeningAuditEntry, ...],
    refs: tuple[PaperScreeningOccurrenceRef, ...],
) -> PaperScreeningAuditSnapshot:
    return PaperScreeningAuditSnapshot.model_construct(
        occurrence_entries=entries,
        routed_canonical_occurrence_refs=refs,
    )


def _event_pairs(state: object) -> tuple[tuple[str, str | None], ...]:
    events = state.events  # type: ignore[attr-defined]
    return tuple((event.event_type, event.node_id) for event in events)


def _payload_event_pairs(values: object) -> tuple[tuple[str, str | None], ...]:
    assert type(values) is dict
    workflow = values["workflow"]
    assert type(workflow) is dict
    events = workflow["events"]
    assert type(events) is list
    return tuple((event["event_type"], event["node_id"]) for event in events)


_SUCCESS_EVENTS = (
    ("node_started", "topic_planner"),
    ("node_completed", "topic_planner"),
    ("node_started", "research_evidence"),
    ("node_completed", "research_evidence"),
    ("node_started", "outline_writer"),
    ("node_completed", "outline_writer"),
)


class _Delegate(AcademicWritingAdapter):
    def __init__(self) -> None:
        self.plan_calls = 0
        self.outline_calls = 0
        self.plan_result: object = _plan()
        self.outline_result: object = _outline()

    async def plan_topic(self, request: AcademicWorkflowRequest):
        self.plan_calls += 1
        return self.plan_result

    async def collect_research_evidence(self, request, topic_plan):
        raise AssertionError("aggregate adapter must replace evidence collection")

    async def write_outline(self, request, topic_plan, evidence):
        self.outline_calls += 1
        return self.outline_result


class _Researcher:
    def __init__(
        self,
        *,
        context: object = "context",
        candidates: object = (),
        audit: object = None,
        research_sources: object = None,
        visited: object = None,
        conduct_error: BaseException | None = None,
        getter_errors: dict[str, BaseException] | None = None,
        gate: asyncio.Event | None = None,
    ) -> None:
        self.cfg = SimpleNamespace(language="before", max_search_results_per_query=99)
        self.image_generator = object()
        self._context = context
        self._candidates = candidates
        self._audit = audit
        self._sources = [] if research_sources is None else research_sources
        self._visited = [] if visited is None else visited
        self._conduct_error = conduct_error
        self._getter_errors = {} if getter_errors is None else dict(getter_errors)
        self._gate = gate
        self.calls: list[str] = []

    async def conduct_research(self) -> object:
        self.calls.append("conduct")
        if self._gate is not None:
            self._gate.set()
            await asyncio.Event().wait()
        if self._conduct_error is not None:
            raise self._conduct_error
        return object()

    def get_research_context(self) -> object:
        self.calls.append("context")
        if "context" in self._getter_errors:
            raise self._getter_errors["context"]
        return self._context

    def get_paper_candidates(self) -> object:
        self.calls.append("candidates")
        if "candidates" in self._getter_errors:
            raise self._getter_errors["candidates"]
        return self._candidates

    def get_paper_screening_audit(self) -> object:
        self.calls.append("audit")
        if "audit" in self._getter_errors:
            raise self._getter_errors["audit"]
        if self._audit is None:
            raise RuntimeError(AUDIT_UNAVAILABLE_MESSAGE)
        if isinstance(self._audit, BaseException):
            raise self._audit
        return self._audit

    def get_research_sources(self) -> object:
        self.calls.append("sources")
        if "sources" in self._getter_errors:
            raise self._getter_errors["sources"]
        return self._sources

    def get_source_urls(self) -> object:
        self.calls.append("visited")
        if "visited" in self._getter_errors:
            raise self._getter_errors["visited"]
        return self._visited


class _Factory:
    def __init__(self, researchers: list[_Researcher]) -> None:
        self._researchers = list(researchers)
        self.calls: list[tuple[AcademicWorkflowRequest, WorkflowTopicPlan]] = []

    def __call__(self, request: AcademicWorkflowRequest, topic_plan: WorkflowTopicPlan):
        self.calls.append((request, topic_plan))
        return self._researchers.pop(0)


class _FalsyFactory(_Factory):
    def __bool__(self) -> bool:
        return False


def _adapter(researchers: list[_Researcher]):
    delegate = _Delegate()
    factory = _Factory(researchers)
    adapter = GPTResearcherResearchEvidenceAdapter(
        delegate, researcher_factory=factory
    )
    return adapter, delegate, factory


async def _capture_exception(awaitable: object) -> BaseException:
    task = asyncio.create_task(awaitable)  # type: ignore[arg-type]
    try:
        await task
    except BaseException as error:
        traceback = error.__traceback__
        assert traceback is not None
        assert traceback.tb_frame.f_code is _capture_exception.__code__
        error.__traceback__ = traceback.tb_next
        return error
    raise AssertionError("expected awaitable to fail")


def _walk_reachable(root: object, target: object) -> bool:
    interpreter_metadata_types = (
        ModuleType,
        type,
        CodeType,
        MethodType,
        BuiltinFunctionType,
        BuiltinMethodType,
        MethodDescriptorType,
        ClassMethodDescriptorType,
        WrapperDescriptorType,
        MethodWrapperType,
        GetSetDescriptorType,
        MemberDescriptorType,
    )
    seen: set[int] = set()
    pending: list[object] = [root]
    while pending:
        value = pending.pop()
        if value is target:
            return True
        identity = id(value)
        if identity in seen:
            continue
        seen.add(identity)
        value_type = type(value)
        if value_type in interpreter_metadata_types or issubclass(value_type, type):
            continue
        if value_type is tuple or value_type is list:
            pending.extend(value)
        elif value_type is dict:
            pending.extend(value.keys())
            pending.extend(value.values())
        elif value_type is set or value_type is frozenset:
            pending.extend(value)
        elif value_type is FunctionType:
            closure = value.__closure__
            if closure is not None:
                pending.extend(closure)
        elif value_type is TracebackType:
            pending.append(value.tb_frame.f_locals)
            if value.tb_next is not None:
                pending.append(value.tb_next)
        elif value_type is FrameType:
            pending.append(value.f_locals)
        else:
            pending.extend(
                referent
                for referent in gc.get_referents(value)
                if type(referent) not in interpreter_metadata_types
                and not issubclass(type(referent), type)
            )
    return False


_HOSTILE_ACCESS_COUNTS = {
    "repr": 0,
    "str": 0,
    "getattribute": 0,
    "iter": 0,
    "property": 0,
    "descriptor": 0,
}


class _Hostile:
    def __init__(self, nested: object) -> None:
        object.__setattr__(self, "nested", nested)

    @property
    def forbidden_property(self):
        _HOSTILE_ACCESS_COUNTS["property"] += 1
        raise AssertionError("property executed")

    def __repr__(self) -> str:
        _HOSTILE_ACCESS_COUNTS["repr"] += 1
        raise AssertionError("repr executed")

    def __str__(self) -> str:
        _HOSTILE_ACCESS_COUNTS["str"] += 1
        raise AssertionError("str executed")

    def __getattribute__(self, name: str):
        _HOSTILE_ACCESS_COUNTS["getattribute"] += 1
        raise AssertionError("dynamic attribute access executed")

    def __iter__(self):
        _HOSTILE_ACCESS_COUNTS["iter"] += 1
        raise AssertionError("custom iterator executed")


class _HostileDescriptor:
    def __get__(self, instance: object, owner: object) -> object:
        _HOSTILE_ACCESS_COUNTS["descriptor"] += 1
        raise AssertionError("descriptor executed")


class _DescriptorHostile(_Hostile):
    forbidden_descriptor = _HostileDescriptor()


def test_public_surface_and_protocol_signatures() -> None:
    module = importlib.import_module(
        "gpt_researcher.workflows.academic_writing.research_evidence"
    )
    assert module.__all__ == (
        "GPTResearcherResearchEvidenceAdapter",
        "ResearcherFactory",
    )
    def assert_signature(
        callable_value: object,
        names: tuple[str, ...],
        *,
        keyword_only: tuple[str, ...] = (),
        defaults: dict[str, object] | None = None,
        async_function: bool,
    ) -> inspect.Signature:
        signature = inspect.signature(callable_value)  # type: ignore[arg-type]
        assert tuple(signature.parameters) == names
        for name, parameter in signature.parameters.items():
            expected_kind = (
                inspect.Parameter.KEYWORD_ONLY
                if name in keyword_only
                else inspect.Parameter.POSITIONAL_OR_KEYWORD
            )
            assert parameter.kind is expected_kind
        for name, default in (defaults or {}).items():
            assert signature.parameters[name].default is default
        assert inspect.iscoroutinefunction(callable_value) is async_function
        return signature

    config_handle = inspect.getattr_static(module, "_ResearcherConfigHandle")
    researcher_handle = inspect.getattr_static(module, "_ResearcherHandle")
    assert get_type_hints(config_handle) == {
        "language": str,
        "max_search_results_per_query": int,
    }
    assert get_type_hints(researcher_handle) == {
        "cfg": config_handle,
        "image_generator": object | None,
    }
    expected_handle_methods = {
        "conduct_research": True,
        "get_research_context": False,
        "get_paper_candidates": False,
        "get_paper_screening_audit": False,
        "get_research_sources": False,
        "get_source_urls": False,
    }
    for method_name, async_function in expected_handle_methods.items():
        method = inspect.getattr_static(researcher_handle, method_name)
        assert_signature(method, ("self",), async_function=async_function)
        assert get_type_hints(method) == {"return": object}

    factory_signature = assert_signature(
        ResearcherFactory.__call__,
        ("self", "request", "topic_plan"),
        async_function=False,
    )
    assert get_type_hints(ResearcherFactory.__call__) == {
        "request": AcademicWorkflowRequest,
        "topic_plan": WorkflowTopicPlan,
        "return": researcher_handle,
    }

    init_signature = assert_signature(
        GPTResearcherResearchEvidenceAdapter.__init__,
        ("self", "delegate", "researcher_factory"),
        keyword_only=("researcher_factory",),
        defaults={"researcher_factory": None},
        async_function=False,
    )
    assert init_signature.parameters["researcher_factory"].kind is (
        inspect.Parameter.KEYWORD_ONLY
    )
    assert init_signature.parameters["researcher_factory"].default is None
    assert get_type_hints(GPTResearcherResearchEvidenceAdapter.__init__) == {
        "delegate": AcademicWritingAdapter,
        "researcher_factory": ResearcherFactory | None,
        "return": type(None),
    }
    expected_adapter_methods = {
        "plan_topic": (
            ("self", "request"),
            {
                "request": AcademicWorkflowRequest,
                "return": WorkflowTopicPlan | AdapterFailure,
            },
        ),
        "collect_research_evidence": (
            ("self", "request", "topic_plan"),
            {
                "request": AcademicWorkflowRequest,
                "topic_plan": WorkflowTopicPlan,
                "return": WorkflowResearchEvidence | AdapterFailure,
            },
        ),
        "write_outline": (
            ("self", "request", "topic_plan", "evidence"),
            {
                "request": AcademicWorkflowRequest,
                "topic_plan": WorkflowTopicPlan,
                "evidence": WorkflowResearchEvidence,
                "return": WorkflowOutline | AdapterFailure,
            },
        ),
    }
    for name, (parameters, expected_hints) in expected_adapter_methods.items():
        method = inspect.getattr_static(GPTResearcherResearchEvidenceAdapter, name)
        assert_signature(
            method,
            parameters,
            async_function=True,
        )
        assert get_type_hints(method) == expected_hints
    assert_signature(
        _create_production_researcher,
        ("request", "topic_plan"),
        async_function=False,
    )
    assert get_type_hints(_create_production_researcher) == {
        "request": AcademicWorkflowRequest,
        "topic_plan": WorkflowTopicPlan,
        "return": researcher_handle,
    }
    source = Path(module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert not any(
        isinstance(node, (ast.Import, ast.ImportFrom))
        and (
            getattr(node, "module", None) == "gpt_researcher.agent"
            or any(alias.name == "gpt_researcher.agent" for alias in node.names)
        )
        for node in tree.body
    )
    assert "Any" not in {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    public_definitions = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and not node.name.startswith("_")
    }
    assert public_definitions == {
        "GPTResearcherResearchEvidenceAdapter",
        "ResearcherFactory",
    }

    def executable_nodes(nodes: list[ast.stmt]) -> list[ast.AST]:
        pending: list[ast.AST] = list(nodes)
        visited: list[ast.AST] = []
        while pending:
            node = pending.pop()
            visited.append(node)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                pending.extend(node.decorator_list)
                pending.extend(node.args.defaults)
                pending.extend(
                    default for default in node.args.kw_defaults if default is not None
                )
                pending.extend(
                    annotation
                    for annotation in (
                        *(argument.annotation for argument in node.args.posonlyargs),
                        *(argument.annotation for argument in node.args.args),
                        *(argument.annotation for argument in node.args.kwonlyargs),
                        node.args.vararg.annotation if node.args.vararg else None,
                        node.args.kwarg.annotation if node.args.kwarg else None,
                        node.returns,
                    )
                    if annotation is not None
                )
                continue
            pending.extend(ast.iter_child_nodes(node))
        return visited

    for node in executable_nodes(tree.body):
        if isinstance(node, ast.Attribute):
            assert not (
                isinstance(node.value, ast.Name)
                and node.value.id == "os"
                and node.attr == "environ"
            )
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            assert node.func.id != "open"
        if isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name):
                assert (node.func.value.id, node.func.attr) not in {
                    ("io", "open"),
                    ("os", "open"),
                    ("os", "getenv"),
                    ("Path", "open"),
                    ("Path", "read_text"),
                    ("Path", "read_bytes"),
                }
            if isinstance(node.func.value, ast.Call) and isinstance(
                node.func.value.func, ast.Name
            ):
                assert not (
                    node.func.value.func.id == "Path"
                    and node.func.attr in {"open", "read_text", "read_bytes"}
                )


@pytest.mark.asyncio
async def test_delegate_methods_preserve_identity_and_do_not_create_researcher() -> None:
    delegate = _Delegate()

    def forbidden_factory(request, topic_plan):
        raise AssertionError("factory called")

    adapter = GPTResearcherResearchEvidenceAdapter(
        delegate, researcher_factory=forbidden_factory
    )
    plan = await adapter.plan_topic(_request())
    outline = await adapter.write_outline(_request(), _plan(), _Researcher)  # type: ignore[arg-type]
    assert plan is delegate.plan_result
    assert outline is delegate.outline_result
    assert delegate.plan_calls == delegate.outline_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("method_name", ["plan_topic", "write_outline"])
@pytest.mark.parametrize("cancelled", [False, True])
async def test_delegate_exceptions_and_cancellation_propagate_without_factory_or_import(
    method_name: str,
    cancelled: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    thrown: BaseException = (
        asyncio.CancelledError() if cancelled else RuntimeError("delegate sentinel")
    )
    calls: list[str] = []
    imports: list[str] = []
    real_import = builtins.__import__

    class RaisingDelegate:
        async def plan_topic(self, request: AcademicWorkflowRequest):
            calls.append("plan_topic")
            raise thrown

        async def collect_research_evidence(self, request, topic_plan):
            raise AssertionError("delegate evidence method called")

        async def write_outline(self, request, topic_plan, evidence):
            calls.append("write_outline")
            raise thrown

    def forbidden_factory(request, topic_plan):
        raise AssertionError("factory called")

    def recording_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name in {"gpt_researcher.agent", "gpt_researcher.utils.enum"}:
            imports.append(name)
        return real_import(name, globals, locals, fromlist, level)

    with monkeypatch.context() as patch:
        patch.setattr(builtins, "__import__", recording_import)
        adapter = GPTResearcherResearchEvidenceAdapter(
            RaisingDelegate(), researcher_factory=forbidden_factory
        )
        with pytest.raises(type(thrown)) as caught:
            if method_name == "plan_topic":
                await adapter.plan_topic(_request())
            else:
                await adapter.write_outline(_request(), _plan(), _outline())  # type: ignore[arg-type]
    assert caught.value is thrown
    assert calls == [method_name]
    assert imports == []


@pytest.mark.asyncio
async def test_falsy_injected_factory_is_still_the_selected_factory() -> None:
    researcher = _Researcher(context="fake path")
    factory = _FalsyFactory([researcher])
    adapter = GPTResearcherResearchEvidenceAdapter(
        _Delegate(), researcher_factory=factory
    )
    result = await adapter.collect_research_evidence(_request(), _plan())
    assert type(result) is WorkflowResearchEvidence
    assert len(factory.calls) == 1


def test_production_factory_mapping_and_fixed_rejection_priority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import gpt_researcher.agent as agent_module

    constructed: list[object] = []

    class FakeGPTResearcher:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs
            self.cfg = SimpleNamespace(
                language="old", max_search_results_per_query=91
            )
            self.image_generator = object()
            constructed.append(self)

    with monkeypatch.context() as patch:
        patch.setattr(agent_module, "GPTResearcher", FakeGPTResearcher)
        request = _request()
        researcher = _create_production_researcher(request, _plan())
        assert len(constructed) == 1
        assert researcher.kwargs == {
            "query": "bounded research topic",
            "report_type": "research_report",
            "report_source": "web",
            "source_urls": ["https://seed.example"],
            "document_urls": ["https://doc.example"],
            "query_domains": ["example.org"],
            "tone": Tone.Objective,
            "websocket": None,
            "verbose": False,
            "log_handler": None,
            "mcp_strategy": "disabled",
        }
        assert researcher.cfg.language == "English"
        assert researcher.cfg.max_search_results_per_query == 7
        assert researcher.image_generator is None
        lists = (
            researcher.kwargs["source_urls"],
            researcher.kwargs["document_urls"],
            researcher.kwargs["query_domains"],
        )
        assert len({id(value) for value in lists}) == 3

        before = len(constructed)
        cases = (
            (
                _request(report_type="bad", report_source="bad", tone="bad"),
                "academic research evidence requires report_type 'research_report'",
            ),
            (
                _request(report_source="bad", tone="bad"),
                "academic research evidence requires report_source 'web'",
            ),
            (
                _request(tone="bad"),
                "academic research evidence requires a valid Tone value",
            ),
        )
        for invalid, message in cases:
            with pytest.raises(ValueError) as caught:
                _create_production_researcher(invalid, _plan())
            assert type(caught.value) is ValueError
            assert str(caught.value) == message
        assert len(constructed) == before

        untouched = _create_production_researcher(
            _request(max_search_results=None), _plan()
        )
        assert untouched.cfg.max_search_results_per_query == 91


@pytest.mark.parametrize(
    ("request_value", "message", "expected_imports"),
    [
        (
            _request(report_type="bad", report_source="bad", tone="bad"),
            "academic research evidence requires report_type 'research_report'",
            [],
        ),
        (
            _request(report_source="bad", tone="bad"),
            "academic research evidence requires report_source 'web'",
            [],
        ),
        (
            _request(tone="bad"),
            "academic research evidence requires a valid Tone value",
            ["gpt_researcher.utils.enum"],
        ),
    ],
)
def test_production_factory_rejections_precede_agent_config_and_environment(
    request_value: AcademicWorkflowRequest,
    message: str,
    expected_imports: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    imports: list[str] = []
    real_import = builtins.__import__

    class ForbiddenEnvironment(dict[str, str]):
        def __getitem__(self, key: str) -> str:
            raise AssertionError("environment read")

        def get(self, key: str, default: object = None) -> object:
            raise AssertionError("environment read")

    def recording_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name in {"gpt_researcher.utils.enum", "gpt_researcher.agent"}:
            imports.append(name)
        return real_import(name, globals, locals, fromlist, level)

    def forbidden_getenv(*args: object, **kwargs: object) -> None:
        raise AssertionError("environment read")

    with monkeypatch.context() as patch:
        patch.setattr(builtins, "__import__", recording_import)
        patch.setattr(os, "getenv", forbidden_getenv)
        patch.setattr(os, "environ", ForbiddenEnvironment())
        with pytest.raises(ValueError) as caught:
            _create_production_researcher(request_value, _plan())
    assert type(caught.value) is ValueError
    assert str(caught.value) == message
    assert imports == expected_imports


@pytest.mark.asyncio
async def test_collect_order_context_and_deterministic_source_projection() -> None:
    candidate = _candidate()
    researcher = _Researcher(
        context=["  first\r\nline  ", "", "second"],
        candidates=(candidate,),
        audit=None,
        research_sources=[
            {"url": candidate.href, "title": " Z title ", "raw_content": " text "},
            {"url": "https://b.example", "title": "B", "raw_content": "body"},
            {"url": "https://a.example", "title": "A"},
            {"url": "https://discard.example", "raw_content": "\u2003"},
        ],
        visited=[" https://z.example ", "https://a.example", "https://z.example"],
    )
    adapter, _, factory = _adapter([researcher])
    result = await adapter.collect_research_evidence(_request(), _plan())
    assert type(result) is WorkflowResearchEvidence
    assert result.context_blocks == ("first\nline", "second")
    assert researcher.calls == [
        "conduct",
        "context",
        "candidates",
        "audit",
        "sources",
        "visited",
    ]
    assert len(factory.calls) == 1
    assert tuple((item.order, item.source_id) for item in result.sources) == tuple(
        (index, f"evidence-source:{index:06d}")
        for index in range(1, len(result.sources) + 1)
    )
    assert tuple(item.url for item in result.sources) == (
        candidate.href,
        "https://a.example",
        "https://b.example",
        "https://z.example",
    )
    assert result.sources[0].candidate_id == candidate.candidate_id
    assert "forbidden body" not in result.model_dump_json()
    assert "forbidden abstract" not in result.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        "audit_multiple",
        "ordinary_unusable_first",
        "ordinary_url_only_first",
        "candidate_without_fallback",
        "visited_only",
    ],
)
async def test_provenance_winner_and_no_backfill_matrix(
    monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    module = importlib.import_module(
        "gpt_researcher.workflows.academic_writing.research_evidence"
    )
    candidate_reads: list[object] = []
    research_reads: list[object] = []
    original_candidate_read = module._read_candidate_body
    original_research_read = module._read_research_content

    def read_candidate(candidate: object) -> str:
        candidate_reads.append(candidate)
        return original_candidate_read(candidate)

    def read_research(record: object) -> str:
        assert type(record) is tuple and len(record) == 4
        research_reads.append(record[2])
        return original_research_read(record)

    monkeypatch.setattr(module, "_read_candidate_body", read_candidate)
    monkeypatch.setattr(module, "_read_research_content", read_research)

    expected_blocks: tuple[str, ...] = ()
    expected_title: str
    expected_candidate_reads: tuple[object, ...] = ()
    expected_research_reads: tuple[object, ...] = ()
    if case == "audit_multiple":
        first = _candidate(rank=1, body="first")
        second = _candidate(rank=2, body="second")
        researcher = _Researcher(
            candidates=(first, second), audit=_audit_for(first)
        )
        expected_blocks = ("firs",)
        expected_title = first.title
        expected_candidate_reads = (first,)
    elif case == "ordinary_unusable_first":
        first_record = {
            "url": "https://duplicate.example",
            "title": "Z",
            "raw_content": None,
        }
        second_record = {
            "url": "https://duplicate.example",
            "title": "A",
            "raw_content": "second",
        }
        researcher = _Researcher(research_sources=[first_record, second_record])
        expected_blocks = ("secon",)
        expected_title = "A"
        expected_research_reads = (second_record,)
    elif case == "ordinary_url_only_first":
        first_record = {"url": "https://duplicate.example", "title": "Z"}
        second_record = {
            "url": "https://duplicate.example",
            "title": "A",
            "raw_content": "later",
        }
        researcher = _Researcher(research_sources=[first_record, second_record])
        expected_title = "A"
    elif case == "candidate_without_fallback":
        candidate = _candidate(body="A")
        researcher = _Researcher(
            candidates=(candidate,),
            research_sources=[
                {"url": candidate.href, "raw_content": "ordinary fallback"}
            ],
        )
        expected_title = candidate.title
        expected_candidate_reads = (candidate,)
    elif case == "visited_only":
        researcher = _Researcher(visited=["https://visited.example"])
        expected_title = "https://visited.example"
    else:
        raise AssertionError(f"unknown case: {case}")

    adapter, _, _ = _adapter([researcher])
    result = await adapter.collect_research_evidence(_request(), _plan())
    assert type(result) is WorkflowResearchEvidence
    assert len(result.sources) == 1
    assert result.sources[0].source_id == "evidence-source:000001"
    assert result.sources[0].title == expected_title
    if expected_blocks:
        assert len(result.provenance) == 1
        entry = result.provenance[0]
        assert type(entry) is WorkflowEvidenceProvenance
        assert entry.source_id == result.sources[0].source_id
        assert entry.evidence_blocks == expected_blocks
    else:
        assert result.provenance == ()
    assert tuple(candidate_reads) == expected_candidate_reads
    assert tuple(research_reads) == expected_research_reads


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "expected_blocks"),
    [
        ("", ()),
        ("A", ()),
        ("AB", ("A",)),
        ("é😀", ("é",)),
        ("AB\n", ("A",)),
        ("A\0", ("A",)),
        ("A\r\nB\rCZ", ("A\nB\nC",)),
    ],
)
async def test_provenance_retainable_prefix_short_text_matrix(
    body: str, expected_blocks: tuple[str, ...]
) -> None:
    candidate = _candidate(body=body)
    researcher = _Researcher(
        candidates=(candidate,), research_sources=[{"url": candidate.href}]
    )
    adapter, _, _ = _adapter([researcher])
    result = await adapter.collect_research_evidence(_request(), _plan())
    assert type(result) is WorkflowResearchEvidence
    if expected_blocks:
        assert result.provenance[0].evidence_blocks == expected_blocks
        assert body not in result.model_dump_json()
    else:
        assert result.provenance == ()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["split", "empty_window", "block_limit", "character_limit"]
)
async def test_provenance_chunk_and_aggregate_boundaries(case: str) -> None:
    if case == "split":
        body = ("x" * 16385) + "Z"
        expected_lengths = (16384, 1)
        expected_total = 16385
    elif case == "empty_window":
        body = ("A" * 16384) + (" " * 16384) + "TAILZ"
        expected_lengths = (16384, 4)
        expected_total = 16388
    elif case == "block_limit":
        body = (("A" + (" " * 16383)) * 64) + "Z"
        expected_lengths = (1,) * 64
        expected_total = 64
    elif case == "character_limit":
        body = ("\0" * 262144) + "Z"
        expected_lengths = (16384,) * 16
        expected_total = 262144
    else:
        raise AssertionError(f"unknown case: {case}")
    candidate = _candidate(body=body)
    researcher = _Researcher(
        candidates=(candidate,), research_sources=[{"url": candidate.href}]
    )
    adapter, _, _ = _adapter([researcher])
    result = await adapter.collect_research_evidence(_request(), _plan())
    assert type(result) is WorkflowResearchEvidence
    blocks = result.provenance[0].evidence_blocks
    assert tuple(map(len, blocks)) == expected_lengths
    assert sum(map(len, blocks)) == expected_total


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["candidate_and_audit", "research_source"])
async def test_provenance_budget_stop_has_hostile_tail(
    monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    for name in _HOSTILE_ACCESS_COUNTS:
        _HOSTILE_ACCESS_COUNTS[name] = 0
    module = importlib.import_module(
        "gpt_researcher.workflows.academic_writing.research_evidence"
    )
    candidate_reads: list[object] = []
    research_reads: list[object] = []
    audit_reads = 0
    audit_armed = False
    original_candidate_read = module._read_candidate_body
    original_research_read = module._read_research_content
    original_project = module._project_provenance
    original_audit_getattribute = PaperScreeningAuditSnapshot.__getattribute__

    def read_candidate(candidate: object) -> str:
        candidate_reads.append(candidate)
        return original_candidate_read(candidate)

    def read_research(record: object) -> str:
        assert type(record) is tuple and len(record) == 4
        research_reads.append(record[2])
        return original_research_read(record)

    def audit_getattribute(self: object, name: str) -> object:
        nonlocal audit_reads
        if audit_armed:
            audit_reads += 1
        return original_audit_getattribute(self, name)

    def project(*args: object, **kwargs: object) -> object:
        nonlocal audit_armed
        audit_armed = True
        try:
            return original_project(*args, **kwargs)
        finally:
            audit_armed = False

    monkeypatch.setattr(module, "_read_candidate_body", read_candidate)
    monkeypatch.setattr(module, "_read_research_content", read_research)
    monkeypatch.setattr(module, "_project_provenance", project)
    monkeypatch.setattr(
        PaperScreeningAuditSnapshot, "__getattribute__", audit_getattribute
    )

    hostile = _DescriptorHostile(object())
    if case == "candidate_and_audit":
        first = _candidate(
            candidate_id="candidate-first",
            href="https://a.example",
            body=(("A" + (" " * 16383)) * 64) + "Z",
        ).model_copy(update={"abstract": hostile})
        second = _candidate(
            candidate_id="candidate-second",
            href="https://b.example",
            rank=2,
            body="unread tail",
        ).model_copy(update={"body": hostile})
        entries = (
            _audit_entry(first, web_pass_id="web-pass:000001", occurrence_id="occurrence:000001"),
            _audit_entry(second, web_pass_id="web-pass:000002", occurrence_id="occurrence:000002"),
        )
        refs = tuple(
            PaperScreeningOccurrenceRef(
                web_pass_id=entry.web_pass_id, occurrence_id=entry.occurrence_id
            )
            for entry in entries
        )
        researcher = _Researcher(
            candidates=(first, second), audit=_audit_snapshot(entries, refs)
        )
        expected_candidate_reads = (first,)
        expected_research_reads: tuple[object, ...] = ()
    elif case == "research_source":
        first_record = {
            "url": "https://a.example",
            "raw_content": ("\0" * 262144) + "Z",
            "ignored": hostile,
        }
        second_record = {
            "url": "https://b.example",
            "raw_content": "unread tail",
            "ignored": hostile,
        }
        researcher = _Researcher(research_sources=[first_record, second_record])
        expected_candidate_reads = ()
        expected_research_reads = (first_record,)
    else:
        raise AssertionError(f"unknown case: {case}")

    adapter, _, _ = _adapter([researcher])
    result = await adapter.collect_research_evidence(_request(), _plan())
    assert type(result) is WorkflowResearchEvidence
    assert len(result.sources) == 2
    assert len(result.provenance) == 1
    assert tuple(candidate_reads) == expected_candidate_reads
    assert tuple(research_reads) == expected_research_reads
    assert audit_reads == 0
    assert _HOSTILE_ACCESS_COUNTS == {
        "repr": 0,
        "str": 0,
        "getattribute": 0,
        "iter": 0,
        "property": 0,
        "descriptor": 0,
    }


@pytest.mark.asyncio
async def test_context_boundaries_source_limits_and_first_wins() -> None:
    context = "x" * 16385
    sources = [
        {"url": f"https://source.example/{index:03d}", "title": "T" * 513}
        for index in range(205)
    ]
    sources.extend(
        [
            {"url": "https://dup.example", "title": "Z"},
            {"url": "https://dup.example", "title": "A"},
        ]
    )
    researcher = _Researcher(context=context, research_sources=sources)
    adapter, _, _ = _adapter([researcher])
    result = await adapter.collect_research_evidence(_request(), _plan())
    assert tuple(map(len, result.context_blocks)) == (16384, 1)
    assert len(result.sources) == 200
    assert all(len(item.title) <= 512 for item in result.sources)
    assert result.evidence_id == "evidence:000001"
    assert result.attempt == 1

    maxed = _Researcher(context=["y" * 16384] * 65)
    adapter, _, _ = _adapter([maxed])
    maxed_result = await adapter.collect_research_evidence(_request(), _plan())
    assert len(maxed_result.context_blocks) == 16
    assert sum(map(len, maxed_result.context_blocks)) == 262144


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_context",
    [
        ("tuple",),
        b"bytes",
        type("StrictStringSubclass", (str,), {})("text"),
        type("StrictListSubclass", (list,), {})(["text"]),
        ["valid", 1],
    ],
)
async def test_context_rejects_every_non_exact_container_and_member(
    invalid_context: object,
) -> None:
    adapter, _, _ = _adapter([_Researcher(context=invalid_context)])
    with pytest.raises(_ResearchEvidenceContractError):
        await adapter.collect_research_evidence(_request(), _plan())


@pytest.mark.asyncio
async def test_context_exact_block_aggregate_unicode_and_suffix_boundaries() -> None:
    sixty_five = [f"block-{index:02d}" for index in range(65)]
    adapter, _, _ = _adapter([_Researcher(context=sixty_five)])
    result = await adapter.collect_research_evidence(_request(), _plan())
    assert result.context_blocks == tuple(sixty_five[:64])

    exact = "x" * 262144
    overflow = exact + "y"
    adapter, _, _ = _adapter(
        [
            _Researcher(context=exact),
            _Researcher(context=overflow),
            _Researcher(context="\u2003😀\r\ntext\rmore\u3000"),
            _Researcher(context=("A" * 16384) + (" " * 16384) + "TAIL"),
        ]
    )
    exact_result = await adapter.collect_research_evidence(_request(), _plan())
    overflow_result = await adapter.collect_research_evidence(_request(), _plan())
    unicode_result = await adapter.collect_research_evidence(_request(), _plan())
    blank_window_result = await adapter.collect_research_evidence(_request(), _plan())
    assert len(exact_result.context_blocks) == 16
    assert sum(map(len, exact_result.context_blocks)) == 262144
    assert overflow_result.context_blocks == exact_result.context_blocks
    assert unicode_result.context_blocks == ("😀\ntext\nmore",)
    assert len(unicode_result.context_blocks[0]) == 11
    assert blank_window_result.context_blocks == ("A" * 16384, "TAIL")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_context",
    [
        iter(["text"]),
        (item for item in ["text"]),
        {"context": "text"},
    ],
)
async def test_context_rejects_iterator_generator_and_mapping(
    invalid_context: object,
) -> None:
    researcher = _Researcher(context=invalid_context)
    adapter, _, _ = _adapter([researcher])
    with pytest.raises(_ResearchEvidenceContractError) as caught:
        await adapter.collect_research_evidence(_request(), _plan())
    assert str(caught.value) == "research evidence adapter contract violation"
    assert researcher.calls == ["conduct", "context"]


@pytest.mark.asyncio
async def test_context_invalid_middle_member_stops_before_later_getters() -> None:
    researcher = _Researcher(context=["first", object(), "unread"])
    adapter, _, _ = _adapter([researcher])
    with pytest.raises(_ResearchEvidenceContractError):
        await adapter.collect_research_evidence(_request(), _plan())
    assert researcher.calls == ["conduct", "context"]


@pytest.mark.asyncio
async def test_url_title_candidate_id_and_raw_content_boundaries() -> None:
    url_4096 = "https://limit.example/" + "a" * (4096 - len("https://limit.example/"))
    url_4097 = url_4096 + "b"
    identifier = "c" * 256
    candidates = (
        _candidate(candidate_id=identifier, title="T" * 513, href="https://id.example/1", rank=1),
        _candidate(candidate_id=identifier, href="https://id.example/2", rank=2),
        _candidate(candidate_id="d" * 257, href="https://id.example/3", rank=3),
    )

    class StringSubclass(str):
        pass

    researcher = _Researcher(
        candidates=candidates,
        research_sources=[
            {"url": candidate.href} for candidate in candidates
        ]
        + [
            {"url": url_4096, "title": "limit"},
            {"url": url_4097, "title": "discard"},
            {
                "url": "https://raw-subclass.example",
                "raw_content": StringSubclass("not exact"),
            },
            {"url": "https://url-only.example", "ignored": object()},
        ],
    )
    adapter, _, _ = _adapter([researcher])
    result = await adapter.collect_research_evidence(_request(), _plan())
    by_url = {source.url: source for source in result.sources}
    assert by_url["https://id.example/1"].candidate_id == identifier
    assert by_url["https://id.example/2"].candidate_id is None
    assert by_url["https://id.example/3"].candidate_id is None
    assert len(by_url["https://id.example/1"].title) == 512
    assert url_4096 in by_url
    assert url_4097 not in by_url
    assert "https://raw-subclass.example" not in by_url
    assert "https://url-only.example" in by_url


@pytest.mark.asyncio
async def test_source_exact_limits_raw_content_and_minimum_title() -> None:
    url_4096 = "https://limit.example/" + "u" * (4096 - len("https://limit.example/"))
    url_4097 = url_4096 + "x"
    candidate_blank = _candidate(href="https://candidate.example/blank").model_copy(
        update={"candidate_id": "   "}
    )
    candidate_256 = _candidate(candidate_id="i" * 256, href="https://candidate.example/256", rank=2)
    candidate_257 = _candidate(candidate_id="j" * 257, href="https://candidate.example/257", rank=3)

    class StringSubclass(str):
        pass

    research_sources = [
        {"url": "   ", "title": "blank URL"},
        {"url": url_4096, "title": "L" * 512},
        {"url": url_4097, "title": "discard"},
        {"url": "https://title.example/513", "title": "M" * 513},
        {"url": candidate_blank.href},
        {"url": candidate_256.href},
        {"url": candidate_257.href},
        {"url": "https://raw.example/missing"},
        {"url": "https://raw.example/none", "raw_content": None},
        {"url": "https://raw.example/empty", "raw_content": ""},
        {"url": "https://raw.example/unicode", "raw_content": "\u2003\u3000"},
        {"url": "https://raw.example/int", "raw_content": 1},
        {"url": "https://raw.example/subclass", "raw_content": StringSubclass("text")},
        {"url": "https://title.example/min", "title": " zeta ", "raw_content": "x"},
        {"url": "https://title.example/min", "title": " alpha ", "raw_content": "x"},
    ]
    researcher = _Researcher(
        candidates=(candidate_blank, candidate_256, candidate_257),
        research_sources=research_sources,
    )
    adapter, _, _ = _adapter([researcher])
    result = await adapter.collect_research_evidence(_request(), _plan())
    by_url = {source.url: source for source in result.sources}
    assert url_4096 in by_url
    assert by_url[url_4096].title == "L" * 512
    assert url_4097 not in by_url
    assert by_url["https://title.example/513"].title == "M" * 512
    assert by_url[candidate_blank.href].candidate_id is None
    assert by_url[candidate_256.href].candidate_id == "i" * 256
    assert by_url[candidate_257.href].candidate_id is None
    assert "https://raw.example/missing" in by_url
    assert {
        "https://raw.example/none",
        "https://raw.example/empty",
        "https://raw.example/unicode",
        "https://raw.example/int",
        "https://raw.example/subclass",
    }.isdisjoint(by_url)
    assert by_url["https://title.example/min"].title == "alpha"


@pytest.mark.asyncio
async def test_source_priority_first_wins_and_deterministic_input_order() -> None:
    candidate = _candidate(
        candidate_id="shared-id", title="Academic", href="https://priority.example"
    )
    second = _candidate(
        candidate_id="shared-id", title="Second", href="https://second.example", rank=2
    )
    research_sources = [
        {"title": "Research", "url": candidate.href},
        {"url": second.href, "title": "Second research"},
        {"url": "https://ordinary.example", "title": "Ordinary"},
    ]
    first_researcher = _Researcher(
        candidates=(candidate, second),
        research_sources=research_sources,
        visited=["https://visited.example", "https://ordinary.example"],
    )
    second_researcher = _Researcher(
        candidates=(candidate, second),
        research_sources=[dict(reversed(tuple(item.items()))) for item in reversed(research_sources)],
        visited=["https://ordinary.example", "https://visited.example"],
    )
    adapter, _, _ = _adapter([first_researcher, second_researcher])
    first_result = await adapter.collect_research_evidence(_request(), _plan())
    second_result = await adapter.collect_research_evidence(_request(), _plan())
    expected = (
        ("https://priority.example", "Academic", "shared-id"),
        ("https://second.example", "Second", None),
        ("https://ordinary.example", "Ordinary", None),
        ("https://visited.example", "https://visited.example", None),
    )
    assert tuple((source.url, source.title, source.candidate_id) for source in first_result.sources) == expected
    assert second_result == first_result


@pytest.mark.asyncio
async def test_source_cap_precedes_contiguous_identity_and_empty_sources_are_valid() -> None:
    sources = [
        {"url": f"https://cap.example/{index:03d}", "title": f"Title {index:03d}"}
        for index in range(205)
    ]
    adapter, _, _ = _adapter(
        [_Researcher(research_sources=sources), _Researcher(research_sources=[])]
    )
    capped = await adapter.collect_research_evidence(_request(), _plan())
    empty = await adapter.collect_research_evidence(_request(), _plan())
    assert len(capped.sources) == 200
    assert tuple((source.order, source.source_id) for source in capped.sources) == tuple(
        (order, f"evidence-source:{order:06d}") for order in range(1, 201)
    )
    assert empty.context_blocks == ("context",)
    assert empty.sources == ()


@pytest.mark.asyncio
async def test_empty_context_validates_all_getters_then_returns_only_failure() -> None:
    researcher = _Researcher(context=[" \r\n "], candidates=(), audit=None)
    adapter, _, _ = _adapter([researcher])
    result = await adapter.collect_research_evidence(_request(), _plan())
    assert result == AdapterFailure(code="research_evidence_failed")
    assert researcher.calls == [
        "conduct",
        "context",
        "candidates",
        "audit",
        "sources",
        "visited",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("broken", "expected_calls"),
    [
        ({"context": ("invalid",)}, ["conduct", "context"]),
        ({"candidates": []}, ["conduct", "context", "candidates"]),
        (
            {"audit": object()},
            ["conduct", "context", "candidates", "audit"],
        ),
        (
            {"research_sources": ()},
            ["conduct", "context", "candidates", "audit", "sources"],
        ),
        (
            {"visited": ()},
            ["conduct", "context", "candidates", "audit", "sources", "visited"],
        ),
        (
            {
                "candidates": (
                    _candidate().model_copy(update={"body": object()}),
                ),
                "research_sources": [{"url": "https://paper.example/1"}],
            },
            ["conduct", "context", "candidates", "audit", "sources", "visited"],
        ),
        (
            {
                "candidates": (
                    _candidate().model_copy(
                        update={
                            "body": type("BodyStringSubclass", (str,), {})("body")
                        }
                    ),
                ),
                "research_sources": [{"url": "https://paper.example/1"}],
            },
            ["conduct", "context", "candidates", "audit", "sources", "visited"],
        ),
    ],
)
async def test_invalid_getter_shapes_are_fixed_contract_errors_and_short_circuit(
    broken: dict[str, object], expected_calls: list[str]
) -> None:
    researcher = _Researcher(**broken)
    adapter, _, _ = _adapter([researcher])
    with pytest.raises(_ResearchEvidenceContractError) as caught:
        await adapter.collect_research_evidence(_request(), _plan())
    assert str(caught.value) == "research evidence adapter contract violation"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert researcher.calls == expected_calls


@pytest.mark.asyncio
async def test_contract_error_traceback_does_not_retain_invalid_response() -> None:
    researcher = _Researcher(context={"nested": [_CONTRACT_SECRET]})
    adapter, _, _ = _adapter([researcher])
    with pytest.raises(_ResearchEvidenceContractError) as caught:
        await adapter.collect_research_evidence(_request(), _plan())
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    researcher._context = None
    del researcher
    assert not _walk_reachable(caught.value, _CONTRACT_SECRET)


@pytest.mark.asyncio
async def test_audit_matching_accepts_duplicate_candidates_and_rejects_duplicate_ref() -> None:
    candidate = _candidate()
    successful = _Researcher(
        candidates=(candidate, candidate), audit=_audit_for(candidate)
    )
    adapter, _, _ = _adapter([successful])
    result = await adapter.collect_research_evidence(_request(), _plan())
    assert type(result) is WorkflowResearchEvidence
    assert len(result.sources) == 1
    assert result.sources[0].candidate_id == candidate.candidate_id

    invalid = _Researcher(
        candidates=(candidate,), audit=_audit_for(candidate, duplicate_ref=True)
    )
    adapter, _, _ = _adapter([invalid])
    with pytest.raises(_ResearchEvidenceContractError):
        await adapter.collect_research_evidence(_request(), _plan())


@pytest.mark.asyncio
async def test_audit_multi_pass_order_and_nonrouted_entries_are_exact() -> None:
    first = _candidate(candidate_id="paper:first", title="First", href="https://paper.example/first")
    second = _candidate(candidate_id="paper:second", title="Second", href="https://paper.example/second", rank=2)
    ignored = _candidate(candidate_id="paper:ignored", title="Ignored", href="https://paper.example/ignored", rank=3)
    first_entry = _audit_entry(
        first, web_pass_id="web-pass:000001", occurrence_id="occurrence:000001"
    )
    second_entry = _audit_entry(
        second, web_pass_id="web-pass:000002", occurrence_id="occurrence:000002"
    )
    planning_entry = _audit_entry(
        ignored,
        web_pass_id="web-pass:000001",
        occurrence_id="occurrence:000003",
        routed_to_evidence=False,
        planning_only=True,
    )
    included_not_routed = _audit_entry(
        ignored,
        web_pass_id="web-pass:000002",
        occurrence_id="occurrence:000004",
        routed_to_evidence=False,
    )
    refs = (
        PaperScreeningOccurrenceRef(
            web_pass_id="web-pass:000002", occurrence_id="occurrence:000002"
        ),
        PaperScreeningOccurrenceRef(
            web_pass_id="web-pass:000001", occurrence_id="occurrence:000001"
        ),
    )
    audit = _audit_snapshot(
        (first_entry, second_entry, planning_entry, included_not_routed), refs
    )
    researcher = _Researcher(
        candidates=(first, second, ignored),
        audit=audit,
        research_sources=[{"url": "https://ordinary.example", "title": "Ordinary"}],
        visited=["https://visited.example"],
    )
    adapter, _, _ = _adapter([researcher])
    result = await adapter.collect_research_evidence(_request(), _plan())
    assert tuple((source.url, source.candidate_id) for source in result.sources) == (
        (second.href, second.candidate_id),
        (first.href, first.candidate_id),
        ("https://ordinary.example", None),
        ("https://visited.example", None),
    )
    assert ignored.href not in {source.url for source in result.sources}


@pytest.mark.asyncio
async def test_deterministically_excluded_audit_entry_never_becomes_a_source() -> None:
    excluded = _candidate(
        candidate_id="paper:excluded",
        title="Excluded",
        href="https://paper.example/excluded",
    )
    excluded_entry = _audit_entry(
        excluded,
        web_pass_id="web-pass:000001",
        occurrence_id="occurrence:000001",
        routed_to_evidence=False,
    ).model_copy(
        update={
            "deterministic_included": False,
            "screening_included": False,
            "routed_to_evidence": False,
            "planning_only": False,
        }
    )
    researcher = _Researcher(
        context="context",
        candidates=(excluded,),
        audit=_audit_snapshot((excluded_entry,), ()),
        research_sources=[
            {"url": "https://ordinary.example", "title": "Ordinary"}
        ],
    )
    adapter, _, _ = _adapter([researcher])
    result = await adapter.collect_research_evidence(_request(), _plan())
    assert result == WorkflowResearchEvidence(
        evidence_id="evidence:000001",
        topic_plan_id="topic-plan:000001",
        attempt=1,
        context_blocks=("context",),
        sources=(
            WorkflowEvidenceSource(
                source_id="evidence-source:000001",
                order=1,
                title="Ordinary",
                url="https://ordinary.example",
                candidate_id=None,
            ),
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "audit_builder",
    [
        lambda candidate, entry, ref: _audit_snapshot((entry, entry), (ref,)),
        lambda candidate, entry, ref: _audit_snapshot(
            (entry,),
            (
                PaperScreeningOccurrenceRef(
                    web_pass_id="web-pass:999999", occurrence_id="occurrence:999999"
                ),
            ),
        ),
        lambda candidate, entry, ref: _audit_snapshot(
            (entry.model_copy(update={"routed_to_evidence": False}),), (ref,)
        ),
        lambda candidate, entry, ref: _audit_snapshot(
            (entry.model_copy(update={"planning_only": True}),), (ref,)
        ),
    ],
)
async def test_audit_compound_and_route_invariants_are_fixed_contract_errors(
    audit_builder: Callable[..., PaperScreeningAuditSnapshot],
) -> None:
    candidate = _candidate()
    entry = _audit_entry(
        candidate,
        web_pass_id="web-pass:000001",
        occurrence_id="occurrence:000001",
    )
    ref = PaperScreeningOccurrenceRef(
        web_pass_id="web-pass:000001", occurrence_id="occurrence:000001"
    )
    researcher = _Researcher(
        candidates=(candidate,), audit=audit_builder(candidate, entry, ref)
    )
    adapter, _, _ = _adapter([researcher])
    with pytest.raises(_ResearchEvidenceContractError) as caught:
        await adapter.collect_research_evidence(_request(), _plan())
    assert str(caught.value) == "research evidence adapter contract violation"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


@pytest.mark.asyncio
async def test_audit_with_no_routed_refs_uses_only_ordinary_and_visited_sources() -> None:
    candidate = _candidate()
    audit = _audit_snapshot(
        (
            _audit_entry(
                candidate,
                web_pass_id="web-pass:000001",
                occurrence_id="occurrence:000001",
                routed_to_evidence=False,
                planning_only=True,
            ),
        ),
        (),
    )
    researcher = _Researcher(
        candidates=(candidate,),
        audit=audit,
        research_sources=[{"url": "https://ordinary.example", "title": "B"}],
        visited=["https://visited.example"],
    )
    adapter, _, _ = _adapter([researcher])
    result = await adapter.collect_research_evidence(_request(), _plan())
    assert tuple((source.url, source.candidate_id) for source in result.sources) == (
        ("https://ordinary.example", None),
        ("https://visited.example", None),
    )

    missing = _Researcher(
        candidates=(), audit=_audit_for(candidate)
    )
    adapter, _, _ = _adapter([missing])
    with pytest.raises(_ResearchEvidenceContractError):
        await adapter.collect_research_evidence(_request(), _plan())


@pytest.mark.asyncio
async def test_runtime_exceptions_and_cancelled_error_propagate_unchanged() -> None:
    runtime = RuntimeError("runtime sentinel")
    researcher = _Researcher(conduct_error=runtime)
    adapter, _, _ = _adapter([researcher])
    with pytest.raises(RuntimeError) as caught:
        await adapter.collect_research_evidence(_request(), _plan())
    assert caught.value is runtime
    assert researcher.calls == ["conduct"]

    near_miss = RuntimeError(AUDIT_UNAVAILABLE_MESSAGE, "extra")
    researcher = _Researcher(audit=near_miss)
    adapter, _, _ = _adapter([researcher])
    with pytest.raises(RuntimeError) as caught:
        await adapter.collect_research_evidence(_request(), _plan())
    assert caught.value is near_miss
    assert researcher.calls == ["conduct", "context", "candidates", "audit"]


class _RuntimeSubclass(RuntimeError):
    pass


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stage", "error", "expected_calls"),
    [
        ("factory", RuntimeError("factory raw"), []),
        ("conduct", RuntimeError("conduct raw"), ["conduct"]),
        ("context", LookupError("context raw"), ["conduct", "context"]),
        (
            "candidates",
            LookupError("candidate raw"),
            ["conduct", "context", "candidates"],
        ),
        (
            "audit",
            RuntimeError("other audit"),
            ["conduct", "context", "candidates", "audit"],
        ),
        (
            "audit",
            RuntimeError(AUDIT_UNAVAILABLE_MESSAGE, "extra"),
            ["conduct", "context", "candidates", "audit"],
        ),
        (
            "audit",
            _RuntimeSubclass(AUDIT_UNAVAILABLE_MESSAGE),
            ["conduct", "context", "candidates", "audit"],
        ),
        (
            "audit",
            LookupError("audit raw"),
            ["conduct", "context", "candidates", "audit"],
        ),
        (
            "sources",
            LookupError("sources raw"),
            ["conduct", "context", "candidates", "audit", "sources"],
        ),
        (
            "visited",
            LookupError("visited raw"),
            ["conduct", "context", "candidates", "audit", "sources", "visited"],
        ),
    ],
)
async def test_runtime_exception_identity_and_exact_getter_short_circuit(
    stage: str,
    error: BaseException,
    expected_calls: list[str],
) -> None:
    researcher = _Researcher(
        conduct_error=error if stage == "conduct" else None,
        getter_errors={} if stage in {"factory", "conduct"} else {stage: error},
    )
    delegate = _Delegate()
    factory_calls = 0

    def factory(request: AcademicWorkflowRequest, topic_plan: WorkflowTopicPlan):
        nonlocal factory_calls
        factory_calls += 1
        if stage == "factory":
            raise error
        return researcher

    adapter = GPTResearcherResearchEvidenceAdapter(
        delegate, researcher_factory=factory
    )
    with pytest.raises(type(error)) as caught:
        await adapter.collect_research_evidence(_request(), _plan())
    assert caught.value is error
    assert str(caught.value) == str(error)
    assert factory_calls == 1
    assert researcher.calls == expected_calls


@pytest.mark.asyncio
async def test_only_exact_audit_unavailable_runtime_error_continues() -> None:
    researcher = _Researcher(context="valid", audit=None)
    adapter, _, _ = _adapter([researcher])
    result = await adapter.collect_research_evidence(_request(), _plan())
    assert type(result) is WorkflowResearchEvidence
    assert researcher.calls == [
        "conduct",
        "context",
        "candidates",
        "audit",
        "sources",
        "visited",
    ]

    invalid_after_audit = _Researcher(
        context="valid", audit=None, research_sources=()
    )
    adapter, _, _ = _adapter([invalid_after_audit])
    with pytest.raises(_ResearchEvidenceContractError) as caught:
        await adapter.collect_research_evidence(_request(), _plan())
    assert str(caught.value) == "research evidence adapter contract violation"
    assert invalid_after_audit.calls == [
        "conduct",
        "context",
        "candidates",
        "audit",
        "sources",
    ]


def test_safe_walker_is_cycle_safe_and_never_uses_dynamic_access() -> None:
    for name in _HOSTILE_ACCESS_COUNTS:
        _HOSTILE_ACCESS_COUNTS[name] = 0
    nested_sentinel = object()
    hostile = _DescriptorHostile({"nested": [nested_sentinel]})
    cycle: list[object] = []
    cycle.append(cycle)
    cycle.append(hostile)
    assert _walk_reachable(cycle, hostile)
    assert _walk_reachable(hostile, nested_sentinel)

    class CustomB:
        def __init__(self, value: object) -> None:
            self.value = value

    class CustomA:
        def __init__(self, value: object) -> None:
            self.value = value

    custom_chain = [CustomA(CustomB(nested_sentinel))]
    assert _walk_reachable(custom_chain, nested_sentinel)
    assert not _walk_reachable([CustomA(CustomB(object()))], nested_sentinel)

    def closure() -> object:
        return nested_sentinel

    assert _walk_reachable(closure, nested_sentinel)
    source = inspect.getsource(_walk_reachable)
    tree = ast.parse(source)
    allowed_names = {"id", "issubclass", "type", "set"}
    allowed_attributes = {
        ("pending", "pop"),
        ("pending", "extend"),
        ("pending", "append"),
        ("seen", "add"),
        ("value", "keys"),
        ("value", "values"),
        ("gc", "get_referents"),
    }
    for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
        if isinstance(call.func, ast.Name):
            assert call.func.id in allowed_names
        elif isinstance(call.func, ast.Attribute):
            assert isinstance(call.func.value, ast.Name)
            assert (call.func.value.id, call.func.attr) in allowed_attributes
        else:
            pytest.fail("walker contains a dynamically resolved call")
    prohibited_attributes = {
        "__dict__",
        "__iter__",
        "__next__",
        "forbidden_property",
        "forbidden_descriptor",
    }
    assert not any(
        isinstance(node, ast.Attribute) and node.attr in prohibited_attributes
        for node in ast.walk(tree)
    )
    assert _HOSTILE_ACCESS_COUNTS == {
        "repr": 0,
        "str": 0,
        "getattribute": 0,
        "iter": 0,
        "property": 0,
        "descriptor": 0,
    }


@pytest.mark.asyncio
async def test_raw_content_secret_is_unreachable_from_dto_graph_and_checkpoint() -> None:
    raw_secret = "".join(("RAW-CONTENT-", "IDENTITY-SECRET-", "000001"))
    researcher = _Researcher(
        research_sources=[
            {
                "url": "https://safe.example",
                "title": "Safe",
                "raw_content": raw_secret,
            }
        ]
    )
    adapter, _, _ = _adapter([researcher])
    saver = InMemorySaver()
    state = await start_academic_workflow(
        _request(thread_id="thread-raw-content-string"),
        adapter,
        checkpointer=saver,
    )
    assert state.research_evidence is not None
    assert state.research_evidence.sources == (
        WorkflowEvidenceSource(
            source_id="evidence-source:000001",
            order=1,
            title="Safe",
            url="https://safe.example",
            candidate_id=None,
        ),
    )
    assert state.research_evidence.provenance == (
        WorkflowEvidenceProvenance(
            source_id="evidence-source:000001",
            evidence_blocks=(raw_secret[:-1],),
        ),
    )
    assert raw_secret not in state.research_evidence.model_dump_json()
    graph_module = importlib.import_module(
        "gpt_researcher.workflows.academic_writing.graph"
    )
    snapshot = await graph_module._build_graph(adapter, saver).aget_state(
        {"configurable": {"thread_id": "thread-raw-content-string"}}
    )
    for surface in (
        state.research_evidence,
        state,
        snapshot.values,
        snapshot.tasks,
        snapshot.metadata,
    ):
        assert not _walk_reachable(surface, raw_secret)


@pytest.mark.asyncio
async def test_hostile_non_string_raw_content_is_discarded_without_dynamic_access() -> None:
    for name in _HOSTILE_ACCESS_COUNTS:
        _HOSTILE_ACCESS_COUNTS[name] = 0
    nested_secret = object()
    hostile = _DescriptorHostile({"nested": [nested_secret]})
    researcher = _Researcher(
        research_sources=[
            {
                "url": "https://discarded.example",
                "title": "Discarded",
                "raw_content": hostile,
            }
        ]
    )
    adapter, _, _ = _adapter([researcher])
    saver = InMemorySaver()
    state = await start_academic_workflow(
        _request(thread_id="thread-hostile-raw-content"),
        adapter,
        checkpointer=saver,
    )
    assert state.research_evidence is not None
    assert state.research_evidence.sources == ()
    graph_module = importlib.import_module(
        "gpt_researcher.workflows.academic_writing.graph"
    )
    snapshot = await graph_module._build_graph(adapter, saver).aget_state(
        {"configurable": {"thread_id": "thread-hostile-raw-content"}}
    )
    for surface in (
        state.research_evidence,
        state,
        snapshot.values,
        snapshot.tasks,
        snapshot.metadata,
    ):
        assert not _walk_reachable(surface, hostile)
        assert not _walk_reachable(surface, nested_secret)
    assert _HOSTILE_ACCESS_COUNTS == {
        "repr": 0,
        "str": 0,
        "getattribute": 0,
        "iter": 0,
        "property": 0,
        "descriptor": 0,
    }


@pytest.mark.asyncio
async def test_dto_validation_input_is_sanitized_directly_and_by_graph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    production_module = importlib.import_module(
        "gpt_researcher.workflows.academic_writing.research_evidence"
    )
    dto_secret = object()
    real_evidence_type = WorkflowResearchEvidence

    def invalid_dto_constructor(**values: object) -> object:
        invalid_values = dict(values)
        invalid_values["context_blocks"] = (dto_secret,)
        return real_evidence_type(**invalid_values)

    adapter, _, _ = _adapter([_Researcher(), _Researcher()])
    saver = InMemorySaver()
    with monkeypatch.context() as patch:
        patch.setattr(
            production_module,
            "WorkflowResearchEvidence",
            invalid_dto_constructor,
        )
        direct_error = await _capture_exception(
            adapter.collect_research_evidence(_request(), _plan())
        )
        graph_error = await _capture_exception(
            start_academic_workflow(
                _request(thread_id="thread-dto-validation"),
                adapter,
                checkpointer=saver,
            )
        )
    assert type(direct_error) is _ResearchEvidenceContractError
    assert str(direct_error) == "research evidence adapter contract violation"
    assert direct_error.__cause__ is None
    assert direct_error.__context__ is None
    assert type(graph_error) is ExecutionError
    assert str(graph_error) == "academic workflow execution failed"
    assert graph_error.__cause__ is None
    assert graph_error.__context__ is None
    graph_module = importlib.import_module(
        "gpt_researcher.workflows.academic_writing.graph"
    )
    snapshot = await graph_module._build_graph(adapter, saver).aget_state(
        {"configurable": {"thread_id": "thread-dto-validation"}}
    )
    assert snapshot.next == ("research_evidence",)
    for surface in (
        direct_error,
        graph_error,
        snapshot.values,
        snapshot.tasks,
        snapshot.metadata,
    ):
        assert not _walk_reachable(surface, dto_secret)


@pytest.mark.asyncio
async def test_sensitive_live_surfaces_are_not_reachable_from_results_or_contract_errors() -> None:
    successful = _Researcher(
        research_sources=[
            {"url": "https://safe.example", "raw_content": _DescriptorHostile(object())}
        ]
    )
    adapter, _, _ = _adapter([successful])
    result = await adapter.collect_research_evidence(_request(), _plan())
    assert not _walk_reachable(result, successful)
    assert not _walk_reachable(result, successful.cfg)
    assert result.sources == ()

    invalid_candidate = object()
    invalid_audit = object()
    raw_outer = type("RawSourceListSubclass", (list,), {})([_CONTRACT_SECRET])
    raw_dict = {"sentinel": _CONTRACT_SECRET}
    invalid_visited = (_CONTRACT_SECRET,)
    corrupt_candidate = _candidate().model_copy(update={"body": _CONTRACT_SECRET})
    cases = (
        (_Researcher(candidates=(invalid_candidate,)), _plan(), invalid_candidate, "_candidates"),
        (_Researcher(audit=invalid_audit), _plan(), invalid_audit, "_audit"),
        (_Researcher(research_sources=raw_outer), _plan(), raw_outer, "_sources"),
        (_Researcher(research_sources=[raw_dict]), _plan(), raw_dict, "_sources"),
        (_Researcher(visited=invalid_visited), _plan(), invalid_visited, "_visited"),
        (
            _Researcher(
                candidates=(corrupt_candidate,),
                research_sources=[{"url": corrupt_candidate.href}],
            ),
            _plan(),
            _CONTRACT_SECRET,
            "_candidates",
        ),
    )
    for researcher, topic_plan, sentinel, attribute in cases:
        adapter, _, _ = _adapter([researcher])
        error = await _capture_exception(
            adapter.collect_research_evidence(_request(), topic_plan)
        )
        assert type(error) is _ResearchEvidenceContractError
        assert str(error) == "research evidence adapter contract violation"
        assert error.__cause__ is None
        assert error.__context__ is None
        setattr(researcher, attribute, None)
        assert not _walk_reachable(error, sentinel)


@pytest.mark.asyncio
async def test_contract_violation_is_sanitized_by_graph_and_checkpoint() -> None:
    researcher = _Researcher(context={"sentinel": _CONTRACT_SECRET})
    recovered = _Researcher(context="recovered")
    adapter, delegate, factory = _adapter([researcher, recovered])
    saver = InMemorySaver()
    error = await _capture_exception(
        start_academic_workflow(
            _request(thread_id="thread-contract"), adapter, checkpointer=saver
        )
    )
    assert type(error) is ExecutionError
    assert str(error) == "academic workflow execution failed"
    assert error.__cause__ is None
    assert error.__context__ is None
    researcher._context = None
    graph_module = importlib.import_module(
        "gpt_researcher.workflows.academic_writing.graph"
    )
    graph = graph_module._build_graph(adapter, saver)
    snapshot = await graph.aget_state(
        {"configurable": {"thread_id": "thread-contract"}}
    )
    assert snapshot.next == ("research_evidence",)
    for surface in (
        error,
        snapshot.values,
        snapshot.tasks,
        snapshot.metadata,
    ):
        assert not _walk_reachable(surface, _CONTRACT_SECRET)
        assert not _walk_reachable(surface, researcher)
        assert not _walk_reachable(surface, researcher.cfg)
    state = await resume_academic_workflow(
        AcademicWorkflowIdentity(
            workflow_id="workflow-1",
            thread_id="thread-contract",
            run_id="run-1",
        ),
        adapter,
        checkpointer=saver,
    )
    assert state.status == "running"
    assert _event_pairs(state) == _SUCCESS_EVENTS
    assert delegate.plan_calls == 1
    assert delegate.outline_calls == 1
    assert len(factory.calls) == 2
    assert recovered.calls == [
        "conduct",
        "context",
        "candidates",
        "audit",
        "sources",
        "visited",
    ]


def test_fail_fast_registry_is_single_resolvable_and_directly_blocked() -> None:
    assert len(_EXTERNAL_ENTRYPOINTS) == len(set(_EXTERNAL_ENTRYPOINTS)) == 35
    for module_name, attribute_name in _EXTERNAL_ENTRYPOINTS:
        target = inspect.getattr_static(importlib.import_module(module_name), attribute_name)
        with pytest.raises(AssertionError, match="external I/O is forbidden"):
            target()
    assert subprocess.Popen[bytes] is subprocess.Popen
    with pytest.raises(AssertionError, match="external I/O is forbidden"):
        subprocess.Popen(["forbidden"])


def test_all_unapproved_file_read_surfaces_fail_fast() -> None:
    secret_path = Path("secret").resolve()
    read_attempts = (
        lambda: builtins.open(secret_path, "r"),
        lambda: io.open(secret_path, "r"),
        lambda: os.open(secret_path, os.O_RDONLY),
        lambda: secret_path.open("r"),
        lambda: secret_path.read_text(encoding="utf-8"),
        lambda: secret_path.read_bytes(),
    )
    for read in read_attempts:
        with pytest.raises(AssertionError, match="external I/O is forbidden"):
            read()


@pytest.mark.asyncio
async def test_canonical_import_differential_restores_module_and_parent_binding(
    monkeypatch: pytest.MonkeyPatch,
    _fail_fast_external_io: dict[str, object],
) -> None:
    import gpt_researcher
    import gpt_researcher.workflows.academic_writing as parent

    canonical = "gpt_researcher.workflows.academic_writing.research_evidence"
    old_module = sys.modules.get(canonical)
    had_binding = "research_evidence" in parent.__dict__
    old_binding = parent.__dict__.get("research_evidence")
    agent_module = _fail_fast_external_io["agent_module"]
    real_agent_researcher = _fail_fast_external_io["real_agent_researcher"]
    real_root_researcher = _fail_fast_external_io["real_root_researcher"]
    blocked = _fail_fast_external_io["blocked"]
    assert sys.modules["gpt_researcher.agent"] is agent_module
    assert inspect.getattr_static(agent_module, "GPTResearcher") is blocked
    assert inspect.getattr_static(gpt_researcher, "GPTResearcher") is blocked
    baseline = set(sys.modules)
    tasks_before = set(asyncio.all_tasks())
    threads_before = set(threading.enumerate())
    handlers_before = {
        name: tuple(logger.handlers)
        for name, logger in logging.Logger.manager.loggerDict.items()
        if type(logger) is logging.Logger
    }

    def forbidden_side_effect(*args: object, **kwargs: object) -> None:
        raise AssertionError("canonical import side effect")

    try:
        sys.modules.pop(canonical, None)
        parent.__dict__.pop("research_evidence", None)
        with monkeypatch.context() as patch:
            patch.setattr(agent_module, "GPTResearcher", real_agent_researcher)
            patch.setattr(gpt_researcher, "GPTResearcher", real_root_researcher)
            patch.setattr(real_agent_researcher, "__init__", forbidden_side_effect)
            patch.setattr(StateGraph, "compile", forbidden_side_effect)
            patch.setattr(InMemorySaver, "__init__", forbidden_side_effect)
            patch.setattr(asyncio, "create_task", forbidden_side_effect)
            patch.setattr(asyncio, "Lock", forbidden_side_effect)
            patch.setattr(threading.Thread, "start", forbidden_side_effect)
            patch.setattr(threading, "Lock", forbidden_side_effect)
            patch.setattr(logging.Logger, "addHandler", forbidden_side_effect)
            patch.setattr(os, "getenv", forbidden_side_effect)
            patch.setattr(type(os.environ), "__getitem__", forbidden_side_effect)
            patch.setattr(type(os.environ), "get", forbidden_side_effect)
            patch.setattr(Path, "read_text", forbidden_side_effect)
            patch.setattr(Path, "read_bytes", forbidden_side_effect)
            patch.setattr(Path, "open", forbidden_side_effect)
            assert inspect.getattr_static(
                agent_module, "GPTResearcher"
            ) is real_agent_researcher
            assert inspect.getattr_static(
                gpt_researcher, "GPTResearcher"
            ) is real_root_researcher
            imported = importlib.import_module(canonical)
            assert sys.modules["gpt_researcher.agent"] is agent_module
            assert inspect.getattr_static(
                agent_module, "GPTResearcher"
            ) is real_agent_researcher
            assert inspect.getattr_static(
                gpt_researcher, "GPTResearcher"
            ) is real_root_researcher
        assert imported.__name__ == canonical
        assert sys.modules["gpt_researcher.agent"] is agent_module
        assert inspect.getattr_static(agent_module, "GPTResearcher") is blocked
        assert inspect.getattr_static(gpt_researcher, "GPTResearcher") is blocked
        assert set(sys.modules) - baseline == set()
        assert set(asyncio.all_tasks()) == tasks_before
        assert set(threading.enumerate()) == threads_before
        assert {
            name: tuple(logger.handlers)
            for name, logger in logging.Logger.manager.loggerDict.items()
            if type(logger) is logging.Logger
        } == handlers_before
    finally:
        sys.modules.pop(canonical, None)
        if old_module is not None:
            sys.modules[canonical] = old_module
        if had_binding:
            parent.research_evidence = old_binding  # type: ignore[attr-defined]
        else:
            parent.__dict__.pop("research_evidence", None)
    assert sys.modules.get(canonical) is old_module
    assert ("research_evidence" in parent.__dict__) is had_binding
    if had_binding:
        assert parent.__dict__["research_evidence"] is old_binding


def test_canonical_import_failure_restores_module_and_parent_binding(
    monkeypatch: pytest.MonkeyPatch,
    _fail_fast_external_io: dict[str, object],
) -> None:
    import gpt_researcher
    import gpt_researcher.workflows.academic_writing as parent

    canonical = "gpt_researcher.workflows.academic_writing.research_evidence"
    old_module = sys.modules.get(canonical)
    had_binding = "research_evidence" in parent.__dict__
    old_binding = parent.__dict__.get("research_evidence")
    real_import_module = importlib.import_module
    agent_module = _fail_fast_external_io["agent_module"]
    real_agent_researcher = _fail_fast_external_io["real_agent_researcher"]
    real_root_researcher = _fail_fast_external_io["real_root_researcher"]
    blocked = _fail_fast_external_io["blocked"]

    def failing_import(name: str, package: str | None = None):
        if name == canonical:
            raise ImportError("injected canonical import failure")
        return real_import_module(name, package)

    try:
        sys.modules.pop(canonical, None)
        parent.__dict__.pop("research_evidence", None)
        with monkeypatch.context() as patch:
            patch.setattr(agent_module, "GPTResearcher", real_agent_researcher)
            patch.setattr(gpt_researcher, "GPTResearcher", real_root_researcher)
            patch.setattr(importlib, "import_module", failing_import)
            with pytest.raises(ImportError) as caught:
                importlib.import_module(canonical)
            assert str(caught.value) == "injected canonical import failure"
            assert sys.modules["gpt_researcher.agent"] is agent_module
            assert inspect.getattr_static(
                agent_module, "GPTResearcher"
            ) is real_agent_researcher
            assert inspect.getattr_static(
                gpt_researcher, "GPTResearcher"
            ) is real_root_researcher
    finally:
        sys.modules.pop(canonical, None)
        if old_module is not None:
            sys.modules[canonical] = old_module
        if had_binding:
            parent.research_evidence = old_binding  # type: ignore[attr-defined]
        else:
            parent.__dict__.pop("research_evidence", None)
    assert sys.modules.get(canonical) is old_module
    assert ("research_evidence" in parent.__dict__) is had_binding
    if had_binding:
        assert parent.__dict__["research_evidence"] is old_binding
    assert inspect.getattr_static(agent_module, "GPTResearcher") is blocked
    assert inspect.getattr_static(gpt_researcher, "GPTResearcher") is blocked
    assert all(
        value is blocked
        for value in _resolve_external_entrypoints().values()
    )


@pytest.mark.asyncio
async def test_full_graph_success_has_exact_events_and_single_execution() -> None:
    researcher = _Researcher(context="complete")
    adapter, delegate, factory = _adapter([researcher])
    saver = InMemorySaver()
    state = await start_academic_workflow(
        _request(thread_id="thread-success"), adapter, checkpointer=saver
    )
    assert state.phase == "outline_ready"
    assert state.status == "running"
    assert _event_pairs(state) == _SUCCESS_EVENTS
    assert tuple(event.order for event in state.events) == tuple(range(1, 7))
    assert not any(event.event_type == "workflow_failed" for event in state.events)
    assert delegate.plan_calls == 1
    assert delegate.outline_calls == 1
    assert len(factory.calls) == 1
    assert researcher.calls == [
        "conduct",
        "context",
        "candidates",
        "audit",
        "sources",
        "visited",
    ]
    graph_module = importlib.import_module(
        "gpt_researcher.workflows.academic_writing.graph"
    )
    snapshot = await graph_module._build_graph(adapter, saver).aget_state(
        {"configurable": {"thread_id": "thread-success"}}
    )
    assert snapshot.next == ("outline_approval",)


@pytest.mark.asyncio
async def test_full_graph_raw_crash_resume_and_safe_checkpoint_metadata() -> None:
    first = _Researcher(conduct_error=RuntimeError(_RAW_SECRET))
    second = _Researcher(context="recovered")
    adapter, delegate, factory = _adapter([first, second])
    saver = InMemorySaver()
    execution_error = await _capture_exception(
        start_academic_workflow(_request(), adapter, checkpointer=saver)
    )
    assert type(execution_error) is ExecutionError
    assert str(execution_error) == "academic workflow execution failed"
    assert execution_error.__cause__ is None
    assert execution_error.__context__ is None
    assert first.calls == ["conduct"]
    first._conduct_error = None
    del first

    graph_module = importlib.import_module(
        "gpt_researcher.workflows.academic_writing.graph"
    )
    graph = graph_module._build_graph(adapter, saver)
    snapshot = await graph.aget_state({"configurable": {"thread_id": "thread-1"}})
    assert snapshot.next == ("research_evidence",)
    assert not _walk_reachable(snapshot.values, _RAW_SECRET)
    assert not _walk_reachable(snapshot.tasks, _RAW_SECRET)
    assert not _walk_reachable(snapshot.metadata, _RAW_SECRET)
    assert not _walk_reachable(execution_error, _RAW_SECRET)

    state = await resume_academic_workflow(
        AcademicWorkflowIdentity(
            workflow_id="workflow-1", thread_id="thread-1", run_id="run-1"
        ),
        adapter,
        checkpointer=saver,
    )
    assert state.status == "running"
    assert delegate.plan_calls == 1
    assert delegate.outline_calls == 1
    assert len(factory.calls) == 2
    assert second.calls[:2] == ["conduct", "context"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stage",
    ["factory", "conduct", "context", "candidates", "audit", "sources", "visited"],
)
async def test_full_graph_every_raw_crash_is_pending_safe_and_resumable(
    stage: str,
) -> None:
    raw_secret = f"RAW-CRASH-{stage}"
    raw_error = RuntimeError(raw_secret)
    recovered = _Researcher(context="recovered")
    delegate = _Delegate()

    if stage == "factory":
        factory_calls: list[tuple[AcademicWorkflowRequest, WorkflowTopicPlan]] = []
        error_box = {"value": raw_error}

        def factory(request: AcademicWorkflowRequest, topic_plan: WorkflowTopicPlan):
            factory_calls.append((request, topic_plan))
            if len(factory_calls) == 1:
                raise error_box.pop("value")
            return recovered

        first = None
    else:
        first = _Researcher(
            conduct_error=raw_error if stage == "conduct" else None,
            getter_errors={} if stage == "conduct" else {stage: raw_error},
        )
        sequence_factory = _Factory([first, recovered])
        factory = sequence_factory
        factory_calls = sequence_factory.calls

    adapter = GPTResearcherResearchEvidenceAdapter(
        delegate, researcher_factory=factory
    )
    saver = InMemorySaver()
    thread_id = f"thread-raw-{stage}"
    execution_error = await _capture_exception(
        start_academic_workflow(
            _request(thread_id=thread_id), adapter, checkpointer=saver
        )
    )
    assert type(execution_error) is ExecutionError
    assert str(execution_error) == "academic workflow execution failed"
    assert execution_error.__cause__ is None
    assert execution_error.__context__ is None
    if first is not None:
        first._conduct_error = None
        first._getter_errors.clear()

    graph_module = importlib.import_module(
        "gpt_researcher.workflows.academic_writing.graph"
    )
    snapshot = await graph_module._build_graph(adapter, saver).aget_state(
        {"configurable": {"thread_id": thread_id}}
    )
    assert snapshot.next == ("research_evidence",)
    assert _payload_event_pairs(snapshot.values) == (
        ("node_started", "topic_planner"),
        ("node_completed", "topic_planner"),
    )
    for surface in (
        execution_error,
        snapshot.values,
        snapshot.tasks,
        snapshot.metadata,
    ):
        assert not _walk_reachable(surface, raw_secret)

    state = await resume_academic_workflow(
        AcademicWorkflowIdentity(
            workflow_id="workflow-1", thread_id=thread_id, run_id="run-1"
        ),
        adapter,
        checkpointer=saver,
    )
    assert state.status == "running"
    assert _event_pairs(state) == _SUCCESS_EVENTS
    assert delegate.plan_calls == 1
    assert delegate.outline_calls == 1
    assert len(factory_calls) == 2
    assert recovered.calls == [
        "conduct",
        "context",
        "candidates",
        "audit",
        "sources",
        "visited",
    ]


@pytest.mark.asyncio
async def test_full_graph_expected_failure_and_outer_cancellation_resume() -> None:
    empty_researcher = _Researcher(context=" ")
    empty_adapter, empty_delegate, empty_factory = _adapter([empty_researcher])
    empty_saver = InMemorySaver()
    failed = await start_academic_workflow(
        _request(thread_id="thread-empty"),
        empty_adapter,
        checkpointer=empty_saver,
    )
    assert failed.status == "failed"
    assert failed.errors[0].code == "research_evidence_failed"
    assert _event_pairs(failed) == (
        ("node_started", "topic_planner"),
        ("node_completed", "topic_planner"),
        ("node_started", "research_evidence"),
        ("workflow_failed", "research_evidence"),
    )
    assert not any(event.event_type == "node_completed" and event.node_id == "research_evidence" for event in failed.events)
    assert not any(event.event_type == "workflow_completed" for event in failed.events)
    graph_module = importlib.import_module(
        "gpt_researcher.workflows.academic_writing.graph"
    )
    empty_snapshot = await graph_module._build_graph(empty_adapter, empty_saver).aget_state(
        {"configurable": {"thread_id": "thread-empty"}}
    )
    assert empty_snapshot.next == ()
    counts_before = (
        empty_delegate.plan_calls,
        empty_delegate.outline_calls,
        len(empty_factory.calls),
        tuple(empty_researcher.calls),
    )
    identity = AcademicWorkflowIdentity(
        workflow_id="workflow-1", thread_id="thread-empty", run_id="run-1"
    )
    for _ in range(2):
        with pytest.raises(ThreadProtocolError) as caught:
            await resume_academic_workflow(
                identity, empty_adapter, checkpointer=empty_saver
            )
        assert str(caught.value) == "academic workflow thread is not resumable"
    assert counts_before == (
        empty_delegate.plan_calls,
        empty_delegate.outline_calls,
        len(empty_factory.calls),
        tuple(empty_researcher.calls),
    )

    started = asyncio.Event()
    blocked = _Researcher(gate=started)
    recovered = _Researcher(context="recovered")
    adapter, delegate, factory = _adapter([blocked, recovered])
    saver = InMemorySaver()
    task = asyncio.create_task(
        start_academic_workflow(
            _request(thread_id="thread-cancel"), adapter, checkpointer=saver
        )
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    graph_module = importlib.import_module(
        "gpt_researcher.workflows.academic_writing.graph"
    )
    graph = graph_module._build_graph(adapter, saver)
    snapshot = await graph.aget_state(
        {"configurable": {"thread_id": "thread-cancel"}}
    )
    assert snapshot.next == ("research_evidence",)
    assert _payload_event_pairs(snapshot.values) == (
        ("node_started", "topic_planner"),
        ("node_completed", "topic_planner"),
    )
    state = await resume_academic_workflow(
        AcademicWorkflowIdentity(
            workflow_id="workflow-1", thread_id="thread-cancel", run_id="run-1"
        ),
        adapter,
        checkpointer=saver,
    )
    assert state.status == "running"
    assert _event_pairs(state) == _SUCCESS_EVENTS
    assert delegate.plan_calls == 1
    assert len(factory.calls) == 2
    assert recovered.calls == [
        "conduct",
        "context",
        "candidates",
        "audit",
        "sources",
        "visited",
    ]
