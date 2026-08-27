import asyncio
import builtins
import pathlib
import socket
import urllib.request
from types import SimpleNamespace

import aiohttp
import httpx
import pytest
import requests

import gpt_researcher.agent as agent_module
from gpt_researcher.agent import GPTResearcher
from gpt_researcher.screening.audit import (
    AuditCollectorState,
    PaperScreeningAuditCollector,
    build_paper_screening_web_pass_audit,
)
from gpt_researcher.screening.decisions import ScreeningPolicy
from gpt_researcher.screening.rules import screen_paper_occurrences
from gpt_researcher.utils.enum import ReportSource, ReportType


@pytest.fixture(autouse=True)
def _no_external_access(monkeypatch):
    real_socket_connect = socket.socket.connect

    def blocked(*_args, **_kwargs):
        pytest.fail("real external access is forbidden")

    def guarded_socket_connect(sock, address):
        if (
            isinstance(address, tuple)
            and address
            and address[0] in {"127.0.0.1", "::1"}
        ):
            return real_socket_connect(sock, address)
        pytest.fail("real external socket access is forbidden")

    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket.socket, "connect", guarded_socket_connect)
    monkeypatch.setattr(requests.sessions.Session, "request", blocked)
    monkeypatch.setattr(urllib.request, "urlopen", blocked)
    monkeypatch.setattr(httpx.Client, "request", blocked)
    monkeypatch.setattr(httpx.AsyncClient, "request", blocked)
    monkeypatch.setattr(aiohttp.ClientSession, "_request", blocked)
    monkeypatch.setattr(agent_module, "create_chat_completion", blocked)
    monkeypatch.setattr(agent_module, "get_search_results", blocked)
    monkeypatch.setattr(agent_module.BrowserManager, "browse_urls", blocked)
    monkeypatch.setattr(
        agent_module.ContextManager,
        "get_similar_content_by_query",
        blocked,
    )
    monkeypatch.setattr(
        agent_module.ContextManager,
        "get_similar_content_by_query_with_vectorstore",
        blocked,
    )
    monkeypatch.setattr(
        agent_module.ResearchConductor,
        "_execute_mcp_research",
        blocked,
    )
    monkeypatch.setattr(asyncio, "create_subprocess_exec", blocked)
    monkeypatch.setattr(pathlib.Path, "write_text", blocked)
    monkeypatch.setattr(pathlib.Path, "write_bytes", blocked)


def _finalized_collector():
    collector = PaperScreeningAuditCollector(
        run_ordinal=1,
        policy=ScreeningPolicy(),
        topic_relevance_enabled=False,
    )
    collector.prepare()
    collector.commit()
    return collector


class _ReportGenerator:
    def __init__(self, result="ORIGINAL\n\n## References\n\n1. Ref"):
        self.result = result
        self.calls = 0

    async def write_report(self, **_kwargs):
        self.calls += 1
        return self.result


class _WebSocket:
    def __init__(self, error=None):
        self.error = error
        self.messages = []

    async def send_json(self, payload):
        self.messages.append(payload)
        if self.error is not None:
            raise self.error


class _EventReportGenerator:
    def __init__(self, *, result="ORIGINAL\n\n## References\n\n1. Ref", timeline=None):
        self.result = result
        self.timeline = timeline
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    async def write_report(self, **_kwargs):
        self.calls += 1
        if self.timeline is not None:
            self.timeline.append("llm_chunk")
        self.started.set()
        await self.release.wait()
        return self.result


class _TimelineWebSocket:
    def __init__(self, timeline, *, error=None, raw_websocket=object()):
        self.timeline = timeline
        self.error = error
        self.raw_websocket = raw_websocket
        self.messages = []
        self.calls = 0

    async def send_json(self, payload):
        self.calls += 1
        self.messages.append(payload)
        self.timeline.append("appendix")
        if self.error is not None:
            raise self.error


async def _start_paused_report(researcher, *, timeline=None):
    generator = _EventReportGenerator(timeline=timeline)
    researcher.report_generator = generator
    task = asyncio.create_task(researcher.write_report())
    await asyncio.wait_for(generator.started.wait(), timeout=1)
    return generator, task


def _bare_researcher(*, enabled="true", websocket=None):
    researcher = GPTResearcher.__new__(GPTResearcher)
    researcher.report_source = ReportSource.Web.value
    researcher.report_type = ReportType.ResearchReport.value
    researcher._paper_candidate_collector_borrower = False
    researcher._paper_candidate_run_active = False
    researcher._paper_screening_report_write_active = False
    researcher._paper_screening_audit_report_ready = True
    researcher._paper_screening_audit_collector = _finalized_collector()
    researcher._paper_screening_audit_run_ordinal = 1
    researcher.cfg = SimpleNamespace(
        paper_screening_audit_report_enabled=enabled,
        language="english",
    )
    researcher.report_generator = _ReportGenerator()
    researcher.websocket = websocket
    researcher.available_images = []
    researcher.context = ["context"]
    researcher._current_step = "general"

    async def no_log(*_args, **_kwargs):
        return None

    researcher._log_event = no_log
    return researcher


@pytest.mark.asyncio
async def test_enabled_report_captures_snapshot_appends_and_sends_once():
    websocket = _WebSocket()
    researcher = _bare_researcher(websocket=websocket)
    report = await researcher.write_report()
    appendix = websocket.messages[0]["output"]
    assert websocket.messages == [{"type": "report", "output": appendix}]
    assert report == "ORIGINAL\n\n## References\n\n1. Ref\n\n---\n\n" + appendix
    assert appendix.startswith("## Paper Screening Process\n\n")
    assert researcher._paper_screening_report_write_active is False
    assert researcher._paper_screening_audit_report_ready is True


@pytest.mark.asyncio
async def test_enabled_report_without_websocket_returns_appendix_without_send():
    researcher = _bare_researcher(websocket=None)
    report = await researcher.write_report()
    assert report.count("## Paper Screening Process") == 1
    assert report.startswith("ORIGINAL\n\n## References\n\n1. Ref\n\n---\n\n")


@pytest.mark.asyncio
async def test_disabled_path_is_byte_identical_and_does_not_call_getter(monkeypatch):
    researcher = _bare_researcher(enabled="false", websocket=_WebSocket())

    def forbidden():
        raise AssertionError("getter must not be called")

    researcher.get_paper_screening_audit = forbidden
    assert await researcher.write_report() == "ORIGINAL\n\n## References\n\n1. Ref"
    assert researcher.websocket.messages == []


@pytest.mark.asyncio
async def test_send_failure_propagates_and_preserves_retryable_state():
    failure = RuntimeError("transport failed")
    researcher = _bare_researcher(websocket=_WebSocket(failure))
    with pytest.raises(RuntimeError) as caught:
        await researcher.write_report()
    assert caught.value is failure
    assert len(researcher.websocket.messages) == 1
    assert researcher._paper_screening_report_write_active is False
    assert researcher._paper_screening_audit_report_ready is True
    assert researcher._paper_screening_audit_collector.state is AuditCollectorState.FINALIZED


@pytest.mark.asyncio
async def test_report_guards_are_synchronous_and_use_frozen_messages():
    researcher = _bare_researcher()
    researcher._paper_candidate_run_active = True
    coroutine = researcher.write_report()
    with pytest.raises(RuntimeError, match="^cannot write report while a research run is active$"):
        await coroutine
    researcher._paper_candidate_run_active = False
    researcher._paper_screening_report_write_active = True
    with pytest.raises(RuntimeError, match="^report writing is already active$"):
        await researcher.write_report()


@pytest.mark.asyncio
async def test_conduct_and_quick_reject_active_report_without_state_mutation():
    researcher = _bare_researcher()
    old_collector = researcher._paper_screening_audit_collector
    researcher._paper_screening_report_write_active = True
    for operation in (
        lambda: researcher.conduct_research(),
        lambda: researcher.quick_search("query"),
    ):
        with pytest.raises(RuntimeError, match="^cannot start research while report writing is active$"):
            await operation()
        assert researcher._paper_screening_audit_collector is old_collector
        assert researcher._paper_screening_audit_report_ready is True


@pytest.mark.asyncio
async def test_cancelled_report_clears_only_write_guard():
    cancellation = asyncio.CancelledError()
    researcher = _bare_researcher()

    async def cancel(**_kwargs):
        raise cancellation

    researcher.report_generator.write_report = cancel
    with pytest.raises(asyncio.CancelledError) as caught:
        await researcher.write_report()
    assert caught.value is cancellation
    assert researcher._paper_screening_report_write_active is False
    assert researcher._paper_screening_audit_report_ready is True


@pytest.mark.asyncio
async def test_invalid_enabled_config_fails_before_logging_or_report_generation():
    researcher = _bare_researcher(enabled="invalid")
    calls = []

    async def observed_log(*_args, **_kwargs):
        calls.append("log")

    researcher._log_event = observed_log
    with pytest.raises(
        ValueError,
        match="^PAPER_SCREENING_AUDIT_REPORT_ENABLED must be true or false$",
    ):
        await researcher.write_report()
    assert calls == []
    assert researcher.report_generator.calls == 0
    assert researcher._paper_screening_report_write_active is False


@pytest.mark.asyncio
async def test_failed_preconfiguration_gate_does_not_access_new_config():
    researcher = _bare_researcher()
    researcher._paper_screening_audit_report_ready = False

    class Config:
        language = "english"

        @property
        def paper_screening_audit_report_enabled(self):
            raise AssertionError("new config must not be read")

    researcher.cfg = Config()
    assert await researcher.write_report() == "ORIGINAL\n\n## References\n\n1. Ref"


@pytest.mark.asyncio
async def test_plan_captures_language_before_first_await_and_sequential_writes_work():
    researcher = _bare_researcher(websocket=_WebSocket())

    async def mutate_language(**_kwargs):
        researcher.cfg.language = "zh"
        return "REPORT"

    researcher.report_generator.write_report = mutate_language
    first = await researcher.write_report()
    assert first.endswith("- No Provider warnings.\n")
    assert "## Paper Screening Process" in first
    second = await researcher.write_report()
    assert "## 论文筛选过程" in second
    assert len(researcher.websocket.messages) == 2


@pytest.mark.asyncio
async def test_accepted_quick_invalidates_ready_without_clearing_audit():
    researcher = _bare_researcher()
    old_audit = researcher._paper_screening_audit_collector

    async def quick(*_args, **_kwargs):
        return "quick"

    researcher._quick_search_impl = quick
    assert await researcher.quick_search("query") == "quick"
    assert researcher._paper_screening_audit_report_ready is False
    assert researcher._paper_screening_audit_collector is old_audit


@pytest.mark.asyncio
async def test_formatter_failure_is_post_report_fatal_without_appendix_send(monkeypatch):
    researcher = _bare_researcher(websocket=_WebSocket())
    events = []

    async def log(_event_type, **kwargs):
        events.append(kwargs.get("step"))

    def fail(*_args):
        raise ValueError("formatter failed")

    researcher._log_event = log
    monkeypatch.setattr(
        "gpt_researcher.screening.audit_markdown.format_paper_screening_audit_markdown",
        fail,
    )
    with pytest.raises(ValueError, match="formatter failed"):
        await researcher.write_report()
    assert researcher.report_generator.calls == 1
    assert researcher.websocket.messages == []
    assert events == ["writing_report"]
    assert researcher._paper_screening_report_write_active is False
    assert researcher._paper_screening_audit_report_ready is True


@pytest.mark.asyncio
async def test_successful_eligible_conduct_publishes_ready_after_audit_commit():
    researcher = _bare_researcher()
    researcher._paper_screening_audit_report_ready = False

    class Conductor:
        _paper_screening_policy = ScreeningPolicy()
        _paper_topic_relevance_enabled = False

        def _bind_paper_screening_for_run(self):
            return None

        def _should_use_paper_screening(self):
            return True

    researcher.research_conductor = Conductor()

    async def conduct(*_args):
        collector = researcher._paper_screening_audit_collector
        pass_ref = collector.allocate_web_pass()
        collector.add_pass(
            build_paper_screening_web_pass_audit(
                web_pass_order=pass_ref.web_pass_order,
                web_pass_id=pass_ref.web_pass_id,
                policy=ScreeningPolicy(),
                topic_relevance_enabled=False,
                deterministic_result=screen_paper_occurrences((), ScreeningPolicy()),
                topic_result=None,
                request_metadata=(),
            )
        )
        return "context"

    researcher._conduct_research_impl = conduct
    assert await researcher.conduct_research() == "context"
    assert researcher._paper_screening_audit_collector.state is AuditCollectorState.FINALIZED
    assert researcher._paper_screening_audit_report_ready is True


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ("conduct", "quick", "write"))
async def test_event_paused_write_rejects_same_instance_overlap_without_mutation(
    operation,
):
    researcher = _bare_researcher()
    old_collector = researcher._paper_screening_audit_collector
    old_ordinal = researcher._paper_screening_audit_run_ordinal
    old_context = researcher.context
    generator, write_task = await _start_paused_report(researcher)
    assert researcher._paper_screening_report_write_active is True

    if operation == "conduct":
        pending = researcher.conduct_research()
        message = "cannot start research while report writing is active"
    elif operation == "quick":
        pending = researcher.quick_search("query")
        message = "cannot start research while report writing is active"
    else:
        pending = researcher.write_report()
        message = "report writing is already active"
    with pytest.raises(RuntimeError, match=f"^{message}$"):
        await pending

    assert researcher._paper_screening_audit_report_ready is True
    assert researcher._paper_screening_audit_collector is old_collector
    assert researcher._paper_screening_audit_run_ordinal == old_ordinal
    assert researcher.context is old_context
    generator.release.set()
    assert "## Paper Screening Process" in await write_task


@pytest.mark.asyncio
async def test_event_paused_writes_on_two_instances_are_isolated():
    first = _bare_researcher()
    second = _bare_researcher()
    first_generator, first_task = await _start_paused_report(first)
    second_generator, second_task = await _start_paused_report(second)
    assert first._paper_screening_report_write_active is True
    assert second._paper_screening_report_write_active is True
    first_generator.release.set()
    first_report = await first_task
    assert second._paper_screening_report_write_active is True
    second_generator.release.set()
    second_report = await second_task
    assert first_report == second_report
    assert first._paper_screening_report_write_active is False
    assert second._paper_screening_report_write_active is False


@pytest.mark.asyncio
async def test_plan_is_captured_before_await_and_never_rereads_run_state():
    researcher = _bare_researcher(websocket=_WebSocket())
    captured_collector = researcher._paper_screening_audit_collector
    getter_calls = 0

    def getter():
        nonlocal getter_calls
        getter_calls += 1
        return captured_collector.snapshot()

    researcher.get_paper_screening_audit = getter
    generator, task = await _start_paused_report(researcher)
    assert getter_calls == 1
    researcher._paper_screening_audit_report_ready = False
    researcher._paper_screening_audit_collector = None
    researcher._paper_screening_audit_run_ordinal = 999
    researcher.cfg.language = "zh"
    generator.release.set()
    report = await task
    assert getter_calls == 1
    assert "## Paper Screening Process" in report
    assert "## 论文筛选过程" not in report


@pytest.mark.asyncio
async def test_disabled_path_never_imports_formatter_or_calls_getter(monkeypatch):
    researcher = _bare_researcher(enabled="false")
    getter_calls = 0
    formatter_imports = []
    real_import = builtins.__import__

    def getter():
        nonlocal getter_calls
        getter_calls += 1
        raise AssertionError("disabled getter must not run")

    def watched_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "gpt_researcher.screening.audit_markdown" or (
            name == "audit_markdown" and "format_paper_screening_audit_markdown" in fromlist
        ):
            formatter_imports.append(name)
        return real_import(name, globals, locals, fromlist, level)

    researcher.get_paper_screening_audit = getter
    monkeypatch.setattr(builtins, "__import__", watched_import)
    assert await researcher.write_report() == "ORIGINAL\n\n## References\n\n1. Ref"
    assert getter_calls == 0
    assert formatter_imports == []


@pytest.mark.asyncio
async def test_formatter_runs_exactly_once_after_fake_llm_chunk(monkeypatch):
    researcher = _bare_researcher(websocket=_WebSocket())
    calls = []
    from gpt_researcher.screening import audit_markdown

    original = audit_markdown.format_paper_screening_audit_markdown

    def counted(snapshot, language):
        calls.append((snapshot, language))
        return original(snapshot, language)

    monkeypatch.setattr(audit_markdown, "format_paper_screening_audit_markdown", counted)
    generator, task = await _start_paused_report(researcher)
    assert calls == []
    generator.release.set()
    assert "## Paper Screening Process" in await task
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_formatter_post_stream_failure_preserves_chunks_and_retry_state(monkeypatch):
    timeline = []
    researcher = _bare_researcher(websocket=_TimelineWebSocket(timeline))
    formatter_calls = 0

    async def log(_event_type, **kwargs):
        timeline.append(kwargs["step"])

    def fail(*_args):
        nonlocal formatter_calls
        formatter_calls += 1
        raise ValueError("FORMATTER_SENTINEL")

    researcher._log_event = log
    monkeypatch.setattr(
        "gpt_researcher.screening.audit_markdown.format_paper_screening_audit_markdown",
        fail,
    )
    generator, task = await _start_paused_report(researcher, timeline=timeline)
    generator.release.set()
    with pytest.raises(ValueError, match="^FORMATTER_SENTINEL$"):
        await task
    assert formatter_calls == 1
    assert "llm_chunk" in timeline
    assert "report_completed" not in timeline
    assert researcher.websocket.messages == []
    assert researcher._paper_screening_report_write_active is False
    assert researcher._paper_screening_audit_report_ready is True
    assert researcher._paper_screening_audit_collector.state is AuditCollectorState.FINALIZED


@pytest.mark.asyncio
async def test_non_none_wrapper_with_no_raw_websocket_is_still_called_once():
    timeline = []
    websocket = _TimelineWebSocket(timeline, raw_websocket=None)
    researcher = _bare_researcher(websocket=websocket)
    report = await researcher.write_report()
    assert websocket.calls == 1
    assert websocket.messages[0]["output"] == report.split("\n\n---\n\n", 1)[1]


@pytest.mark.asyncio
async def test_post_send_transport_failure_is_at_most_once_and_retryable():
    failure = RuntimeError("TRANSPORT_JSON_SENTINEL")
    timeline = []
    websocket = _TimelineWebSocket(timeline, error=failure)
    researcher = _bare_researcher(websocket=websocket)
    with pytest.raises(RuntimeError) as caught:
        await researcher.write_report()
    assert caught.value is failure
    assert websocket.calls == 1
    assert len(websocket.messages) == 1
    assert researcher._paper_screening_report_write_active is False
    assert researcher._paper_screening_audit_report_ready is True


@pytest.mark.asyncio
async def test_send_cancellation_propagates_same_object_without_retry():
    cancellation = asyncio.CancelledError()
    timeline = []
    websocket = _TimelineWebSocket(timeline, error=cancellation)
    researcher = _bare_researcher(websocket=websocket)
    with pytest.raises(asyncio.CancelledError) as caught:
        await researcher.write_report()
    assert caught.value is cancellation
    assert websocket.calls == 1
    assert researcher._paper_screening_report_write_active is False
    assert researcher._paper_screening_audit_report_ready is True


@pytest.mark.asyncio
async def test_event_order_has_no_duplicate_full_report_chunk():
    timeline = []
    websocket = _TimelineWebSocket(timeline)
    researcher = _bare_researcher(websocket=websocket)

    async def log(_event_type, **kwargs):
        timeline.append(kwargs["step"])

    researcher._log_event = log
    generator, task = await _start_paused_report(researcher, timeline=timeline)
    generator.release.set()
    report = await task
    timeline.append("return")
    assert timeline == ["writing_report", "llm_chunk", "appendix", "report_completed", "return"]
    assert websocket.calls == 1
    assert websocket.messages == [
        {"type": "report", "output": report.split("\n\n---\n\n", 1)[1]}
    ]
    assert all(message["output"] != report for message in websocket.messages)


@pytest.mark.asyncio
async def test_fake_export_consumers_receive_the_exact_returned_report():
    researcher = _bare_researcher(websocket=None)
    report = await researcher.write_report()
    consumed = {}
    for name in ("markdown", "pdf", "docx"):
        consumed[name] = report
    assert consumed == {"markdown": report, "pdf": report, "docx": report}


class _RunBinding:
    _paper_screening_policy = ScreeningPolicy()
    _paper_topic_relevance_enabled = False

    def __init__(self, *, bind_error=None, enabled=False):
        self.bind_error = bind_error
        self.enabled = enabled

    def _bind_paper_screening_for_run(self):
        if self.bind_error is not None:
            raise self.bind_error

    def _should_use_paper_screening(self):
        return self.enabled


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode",
    (
        "disabled",
        "out_of_scope",
        "borrower",
        "binding_failure",
        "capability_failure",
        "ordinary_failure",
        "cancelled",
    ),
)
async def test_conduct_ready_state_matrix(mode):
    researcher = _bare_researcher()
    researcher.research_conductor = _RunBinding(
        bind_error=RuntimeError("binding") if mode == "binding_failure" else None,
        enabled=False,
    )
    if mode == "out_of_scope":
        researcher.report_source = ReportSource.Local.value
    if mode == "borrower":
        researcher._paper_candidate_collector_borrower = True

    async def conduct(*_args):
        if mode == "ordinary_failure":
            raise RuntimeError("ordinary")
        if mode == "cancelled":
            raise asyncio.CancelledError()
        return "context"

    researcher._conduct_research_impl = conduct
    if mode in {"binding_failure", "ordinary_failure"}:
        with pytest.raises(RuntimeError):
            await researcher.conduct_research()
    elif mode == "cancelled":
        with pytest.raises(asyncio.CancelledError):
            await researcher.conduct_research()
    else:
        assert await researcher.conduct_research() == "context"
    assert researcher._paper_screening_audit_report_ready is (mode == "borrower")


@pytest.mark.asyncio
async def test_report_transport_failure_can_be_retried_successfully():
    first_failure = RuntimeError("first send fails")

    class RetryWebSocket:
        def __init__(self):
            self.calls = 0
            self.messages = []

        async def send_json(self, payload):
            self.calls += 1
            self.messages.append(payload)
            if self.calls == 1:
                raise first_failure

    websocket = RetryWebSocket()
    researcher = _bare_researcher(websocket=websocket)
    with pytest.raises(RuntimeError) as caught:
        await researcher.write_report()
    assert caught.value is first_failure
    second = await researcher.write_report()
    assert "## Paper Screening Process" in second
    assert websocket.calls == 2
    assert researcher._paper_screening_audit_report_ready is True


@pytest.mark.asyncio
async def test_quick_preserves_snapshot_and_ordinal_while_invalidating_ready():
    researcher = _bare_researcher()
    collector = researcher._paper_screening_audit_collector
    ordinal = researcher._paper_screening_audit_run_ordinal

    async def quick(*_args, **_kwargs):
        return "quick"

    researcher._quick_search_impl = quick
    assert await researcher.quick_search("query") == "quick"
    assert researcher._paper_screening_audit_report_ready is False
    assert researcher._paper_screening_audit_collector is collector
    assert researcher._paper_screening_audit_run_ordinal == ordinal
