"""Bounded GPTResearcher evidence adapter for the academic-writing workflow."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, TypeAlias

from gpt_researcher.screening.audit import (
    AUDIT_UNAVAILABLE_MESSAGE,
    PaperScreeningAuditEntry,
    PaperScreeningAuditSnapshot,
    PaperScreeningOccurrenceRef,
)
from gpt_researcher.screening.models import PaperCandidate

from .adapters import AcademicWritingAdapter
from .state import (
    AcademicWorkflowRequest,
    AdapterFailure,
    WorkflowEvidenceProvenance,
    WorkflowEvidenceSource,
    WorkflowOutline,
    WorkflowResearchEvidence,
    WorkflowTopicPlan,
)


__all__ = (
    "GPTResearcherResearchEvidenceAdapter",
    "ResearcherFactory",
)


_CONTEXT_BLOCK_MAX_CHARS = 16384
_CONTEXT_BLOCK_MAX_COUNT = 64
_CONTEXT_TOTAL_MAX_CHARS = 262144
_SOURCE_MAX_COUNT = 200
_SOURCE_TITLE_MAX_CHARS = 512
_SOURCE_URL_MAX_CHARS = 4096
_SOURCE_CANDIDATE_ID_MAX_CHARS = 256
_PROVENANCE_BLOCK_MAX_CHARS = 16384
_PROVENANCE_BLOCK_MAX_COUNT = 64
_PROVENANCE_TOTAL_MAX_CHARS = 262144
_CONTRACT_FAILURE = object()


_ResearchSourceRecord: TypeAlias = tuple[
    str, str | None, dict[str, object], bool
]
_SourcePlanRecord: TypeAlias = tuple[
    str, str, str | None, str, object | None
]


class _ResearcherConfigHandle(Protocol):
    language: str
    max_search_results_per_query: int


class _ResearcherHandle(Protocol):
    cfg: _ResearcherConfigHandle
    image_generator: object | None

    async def conduct_research(self) -> object: ...

    def get_research_context(self) -> object: ...

    def get_paper_candidates(self) -> object: ...

    def get_paper_screening_audit(self) -> object: ...

    def get_research_sources(self) -> object: ...

    def get_source_urls(self) -> object: ...


class ResearcherFactory(Protocol):
    def __call__(
        self,
        request: AcademicWorkflowRequest,
        topic_plan: WorkflowTopicPlan,
    ) -> _ResearcherHandle: ...


class _ResearchEvidenceContractError(RuntimeError):
    pass


def _create_production_researcher(
    request: AcademicWorkflowRequest,
    topic_plan: WorkflowTopicPlan,
) -> _ResearcherHandle:
    if request.report_type != "research_report":
        raise ValueError(
            "academic research evidence requires report_type 'research_report'"
        )
    if request.report_source != "web":
        raise ValueError(
            "academic research evidence requires report_source 'web'"
        )

    from gpt_researcher.utils.enum import Tone

    tone_by_value = {member.value: member for member in Tone}
    tone = tone_by_value.get(request.tone)
    if tone is None:
        raise ValueError("academic research evidence requires a valid Tone value")

    from gpt_researcher.agent import GPTResearcher

    researcher = GPTResearcher(
        query=topic_plan.research_topic,
        report_type=request.report_type,
        report_source=request.report_source,
        source_urls=list(request.source_urls),
        document_urls=list(request.document_urls),
        query_domains=list(request.query_domains),
        tone=tone,
        websocket=None,
        verbose=False,
        log_handler=None,
        mcp_strategy="disabled",
    )
    researcher.cfg.language = request.language
    researcher.image_generator = None
    if request.max_search_results is not None:
        researcher.cfg.max_search_results_per_query = request.max_search_results
    return researcher


def _safe_contract_call(call: Callable[[], object]) -> object:
    failed = False
    result: object | None = None
    try:
        result = call()
    except Exception:
        failed = True
    if failed:
        return _CONTRACT_FAILURE
    return result


def _normalize_context(value: object) -> tuple[str, ...]:
    if type(value) is str:
        items = (value,)
    elif type(value) is list:
        copied = tuple(value)
        if any(type(item) is not str for item in copied):
            raise TypeError("context members must be exact strings")
        items = copied
    else:
        raise TypeError("context must be an exact string or list")

    blocks: list[str] = []
    aggregate_length = 0
    for text in items:
        normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not normalized:
            continue
        offset = 0
        while (
            offset < len(normalized)
            and len(blocks) < _CONTEXT_BLOCK_MAX_COUNT
            and aggregate_length < _CONTEXT_TOTAL_MAX_CHARS
        ):
            aggregate_remaining = _CONTEXT_TOTAL_MAX_CHARS - aggregate_length
            window_length = min(_CONTEXT_BLOCK_MAX_CHARS, aggregate_remaining)
            raw_window = normalized[offset : offset + window_length]
            offset += len(raw_window)
            block = raw_window.strip()
            if block:
                blocks.append(block)
                aggregate_length += len(block)
        if (
            len(blocks) >= _CONTEXT_BLOCK_MAX_COUNT
            or aggregate_length >= _CONTEXT_TOTAL_MAX_CHARS
        ):
            break
    return tuple(blocks)


def _snapshot_candidates(value: object) -> tuple[PaperCandidate, ...]:
    if type(value) is not tuple:
        raise TypeError("candidates must be an exact tuple")
    for candidate in value:
        if type(candidate) is not PaperCandidate:
            raise TypeError("candidate members must be exact PaperCandidate values")
        if (
            type(candidate.candidate_id) is not str
            or type(candidate.title) is not str
            or type(candidate.href) is not str
        ):
            raise TypeError("candidate identity fields must be exact strings")
    return value


def _validate_audit(value: object) -> PaperScreeningAuditSnapshot | None:
    if value is None:
        return None
    if type(value) is not PaperScreeningAuditSnapshot:
        raise TypeError("audit must be the exact frozen snapshot")
    if type(value.routed_canonical_occurrence_refs) is not tuple:
        raise TypeError("routed references must be an exact tuple")
    if type(value.occurrence_entries) is not tuple:
        raise TypeError("audit occurrences must be an exact tuple")
    return value


def _normalize_url(value: str) -> str | None:
    normalized = value.strip()
    if not normalized or len(normalized) > _SOURCE_URL_MAX_CHARS:
        return None
    return normalized


def _snapshot_research_sources(value: object) -> tuple[_ResearchSourceRecord, ...]:
    if type(value) is not list:
        raise TypeError("research sources must be an exact list")
    outer_snapshot = tuple(value)
    projected: list[_ResearchSourceRecord] = []
    for item in outer_snapshot:
        if type(item) is not dict:
            raise TypeError("research source members must be exact dicts")
        if "url" not in item or type(item["url"]) is not str:
            raise TypeError("research source URL must be an exact string")
        title_value = item.get("title")
        if title_value is not None and type(title_value) is not str:
            raise TypeError("research source title must be an exact string")
        if "raw_content" in item:
            raw_content = item["raw_content"]
            usable = type(raw_content) is str and raw_content.strip() != ""
            has_raw_content = usable
        else:
            usable = True
            has_raw_content = False
        if not usable:
            continue
        url = _normalize_url(item["url"])
        if url is None:
            continue
        title = None
        if type(title_value) is str:
            normalized_title = title_value.strip()
            title = normalized_title or None
        projected.append((url, title, item, has_raw_content))
    return tuple(projected)


def _snapshot_visited_urls(value: object) -> tuple[str, ...]:
    if type(value) is not list:
        raise TypeError("visited URLs must be an exact list")
    outer_snapshot = tuple(value)
    if any(type(item) is not str for item in outer_snapshot):
        raise TypeError("visited URL members must be exact strings")
    normalized = (
        url
        for item in outer_snapshot
        if (url := _normalize_url(item)) is not None
    )
    return tuple(sorted(set(normalized)))


def _research_titles(
    research_sources: tuple[_ResearchSourceRecord, ...]
) -> dict[str, str | None]:
    titles_by_url: dict[str, list[str]] = {}
    for url, title, _, _ in research_sources:
        titles_by_url.setdefault(url, [])
        if title is not None:
            titles_by_url[url].append(title)
    return {
        url: min(titles) if titles else None
        for url, titles in titles_by_url.items()
    }


def _audit_candidate_records(
    audit: PaperScreeningAuditSnapshot,
    candidates: tuple[PaperCandidate, ...],
) -> tuple[_SourcePlanRecord, ...]:
    routed_refs = audit.routed_canonical_occurrence_refs
    seen_refs: set[tuple[str, str]] = set()
    for ref in routed_refs:
        if type(ref) is not PaperScreeningOccurrenceRef:
            raise TypeError("routed reference members must be exact")
        if type(ref.web_pass_id) is not str or type(ref.occurrence_id) is not str:
            raise TypeError("routed reference fields must be exact strings")
        key = (ref.web_pass_id, ref.occurrence_id)
        if key in seen_refs:
            raise ValueError("routed reference identities must be unique")
        seen_refs.add(key)

    occurrence_by_key: dict[tuple[str, str], PaperScreeningAuditEntry] = {}
    for entry in audit.occurrence_entries:
        if type(entry) is not PaperScreeningAuditEntry:
            raise TypeError("audit occurrence members must be exact")
        if (
            type(entry.web_pass_id) is not str
            or type(entry.occurrence_id) is not str
            or type(entry.candidate_id) is not str
            or type(entry.title) is not str
            or type(entry.href) is not str
        ):
            raise TypeError("audit occurrence fields must be exact strings")
        key = (entry.web_pass_id, entry.occurrence_id)
        if key in occurrence_by_key:
            raise ValueError("audit occurrence identities must be unique")
        occurrence_by_key[key] = entry

    records: list[_SourcePlanRecord] = []
    for ref in routed_refs:
        entry = occurrence_by_key.get((ref.web_pass_id, ref.occurrence_id))
        if (
            entry is None
            or entry.routed_to_evidence is not True
            or entry.planning_only is not False
        ):
            raise ValueError("routed reference must resolve to routed evidence")
        winning_candidate: PaperCandidate | None = None
        for candidate in candidates:
            if (
                candidate.candidate_id == entry.candidate_id
                and candidate.title == entry.title
                and candidate.href == entry.href
            ):
                winning_candidate = candidate
                break
        if winning_candidate is None:
            raise ValueError("routed audit candidate must resolve")
        records.append(
            (
                entry.href,
                entry.title,
                entry.candidate_id,
                "candidate",
                winning_candidate,
            )
        )
    return tuple(records)


def _candidate_records_without_audit(
    candidates: tuple[PaperCandidate, ...],
    usable_urls: set[str],
) -> tuple[_SourcePlanRecord, ...]:
    records: list[_SourcePlanRecord] = []
    for candidate in candidates:
        url = _normalize_url(candidate.href)
        if url is not None and url in usable_urls:
            records.append(
                (
                    candidate.href,
                    candidate.title,
                    candidate.candidate_id,
                    "candidate",
                    candidate,
                )
            )
    return tuple(records)


def _build_source_plan(
    candidates: tuple[PaperCandidate, ...],
    audit: PaperScreeningAuditSnapshot | None,
    research_sources: tuple[_ResearchSourceRecord, ...],
    visited_urls: tuple[str, ...],
) -> tuple[_SourcePlanRecord, ...]:
    selected_titles = _research_titles(research_sources)
    usable_urls = set(selected_titles)
    if audit is None:
        candidate_records = _candidate_records_without_audit(candidates, usable_urls)
    else:
        candidate_records = _audit_candidate_records(audit, candidates)

    winning_research_records: dict[str, _ResearchSourceRecord] = {}
    for record in research_sources:
        winning_research_records.setdefault(record[0], record)
    research_records: tuple[_SourcePlanRecord, ...] = tuple(
        (
            url,
            title if title is not None else url,
            None,
            "research" if winning_research_records[url][3] else "none",
            winning_research_records[url] if winning_research_records[url][3] else None,
        )
        for url, title in sorted(selected_titles.items(), key=lambda item: (item[0], item[1] or ""))
    )
    visited_records: tuple[_SourcePlanRecord, ...] = tuple(
        (url, url, None, "none", None) for url in visited_urls
    )
    prioritized = candidate_records + research_records + visited_records

    normalized_records: list[_SourcePlanRecord] = []
    seen_urls: set[str] = set()
    seen_candidate_ids: set[str] = set()
    for raw_url, raw_title, raw_candidate_id, locator_kind, locator in prioritized:
        if type(raw_url) is not str or type(raw_title) is not str:
            raise TypeError("projected source fields must be exact strings")
        url = _normalize_url(raw_url)
        if url is None or url in seen_urls:
            continue
        title = raw_title.strip()
        if not title:
            title = selected_titles.get(url) or url
        title = title[:_SOURCE_TITLE_MAX_CHARS]
        candidate_id = raw_candidate_id
        if candidate_id is not None:
            if type(candidate_id) is not str:
                raise TypeError("candidate id must be an exact string")
            candidate_id = candidate_id.strip()
            if (
                not candidate_id
                or len(candidate_id) > _SOURCE_CANDIDATE_ID_MAX_CHARS
                or candidate_id in seen_candidate_ids
            ):
                candidate_id = None
        seen_urls.add(url)
        if candidate_id is not None:
            seen_candidate_ids.add(candidate_id)
        normalized_records.append(
            (url, title, candidate_id, locator_kind, locator)
        )

    return tuple(normalized_records[:_SOURCE_MAX_COUNT])


def _sources_from_plan(
    plan: tuple[_SourcePlanRecord, ...],
) -> tuple[WorkflowEvidenceSource, ...]:
    return tuple(
        WorkflowEvidenceSource(
            source_id=f"evidence-source:{order:06d}",
            order=order,
            title=title,
            url=url,
            candidate_id=candidate_id,
        )
        for order, (url, title, candidate_id, _, _) in enumerate(plan, start=1)
    )


def _read_candidate_body(candidate: object) -> str:
    if type(candidate) is not PaperCandidate:
        raise TypeError("candidate provenance locator must be exact")
    storage = object.__getattribute__(candidate, "__dict__")
    if type(storage) is not dict or "body" not in storage:
        raise TypeError("candidate body storage is invalid")
    body = storage["body"]
    if type(body) is not str:
        raise TypeError("candidate body must be an exact string")
    return body


def _read_research_content(record: object) -> str:
    if type(record) is not tuple or len(record) != 4:
        raise TypeError("research provenance locator must be exact")
    raw_source = record[2]
    has_raw_content = record[3]
    if type(raw_source) is not dict or has_raw_content is not True:
        raise TypeError("research provenance locator is invalid")
    if "raw_content" not in raw_source:
        raise TypeError("research provenance content is missing")
    raw_content = raw_source["raw_content"]
    if type(raw_content) is not str:
        raise TypeError("research provenance content must be exact")
    return raw_content


def _retainable_raw_bounds(text: str) -> tuple[int, int]:
    start = 0
    end = len(text)
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    if end <= start:
        return start, start
    return start, end - 1


def _project_text_blocks(
    text: str,
    block_count: int,
    character_count: int,
) -> tuple[tuple[str, ...], int, int]:
    if type(text) is not str:
        raise TypeError("provenance text must be exact")
    raw_index, retainable_end = _retainable_raw_bounds(text)
    blocks: list[str] = []
    while (
        block_count < _PROVENANCE_BLOCK_MAX_COUNT
        and character_count < _PROVENANCE_TOTAL_MAX_CHARS
        and raw_index < retainable_end
    ):
        window_limit = min(
            _PROVENANCE_BLOCK_MAX_CHARS,
            _PROVENANCE_TOTAL_MAX_CHARS - character_count,
        )
        characters: list[str] = []
        while raw_index < retainable_end and len(characters) < window_limit:
            character = text[raw_index]
            if character == "\r":
                raw_index += 1
                if raw_index < retainable_end and text[raw_index] == "\n":
                    raw_index += 1
                characters.append("\n")
            else:
                raw_index += 1
                characters.append(character)
        block = "".join(characters).strip()
        if block:
            if type(block) is not str:
                raise TypeError("projected provenance block must be exact")
            blocks.append(block)
            block_count += 1
            character_count += len(block)
    return tuple(blocks), block_count, character_count


def _project_provenance(
    sources: tuple[WorkflowEvidenceSource, ...],
    plan: tuple[_SourcePlanRecord, ...],
) -> tuple[WorkflowEvidenceProvenance, ...]:
    if len(sources) != len(plan):
        raise ValueError("source provenance plan is inconsistent")
    entries: list[WorkflowEvidenceProvenance] = []
    block_count = 0
    character_count = 0
    index = 0
    while (
        block_count < _PROVENANCE_BLOCK_MAX_COUNT
        and character_count < _PROVENANCE_TOTAL_MAX_CHARS
        and index < len(plan)
    ):
        source = sources[index]
        record = plan[index]
        locator_kind = record[3]
        locator = record[4]
        if locator_kind == "candidate":
            text = _read_candidate_body(locator)
        elif locator_kind == "research":
            text = _read_research_content(locator)
        elif locator_kind == "none" and locator is None:
            index += 1
            continue
        else:
            raise ValueError("source provenance locator is invalid")
        blocks, block_count, character_count = _project_text_blocks(
            text, block_count, character_count
        )
        if blocks:
            entries.append(
                WorkflowEvidenceProvenance(
                    source_id=source.source_id,
                    evidence_blocks=blocks,
                )
            )
        index += 1
    return tuple(entries)


def _build_evidence(
    topic_plan: WorkflowTopicPlan,
    context_blocks: tuple[str, ...],
    candidates: tuple[PaperCandidate, ...],
    audit: PaperScreeningAuditSnapshot | None,
    research_sources: tuple[_ResearchSourceRecord, ...],
    visited_urls: tuple[str, ...],
) -> WorkflowResearchEvidence:
    plan = _build_source_plan(candidates, audit, research_sources, visited_urls)
    sources = _sources_from_plan(plan)
    provenance = _project_provenance(sources, plan)
    return WorkflowResearchEvidence(
        evidence_id="evidence:000001",
        topic_plan_id=topic_plan.topic_plan_id,
        attempt=1,
        context_blocks=context_blocks,
        sources=sources,
        provenance=provenance,
    )


async def _collect_with_researcher(
    factory: ResearcherFactory,
    request: AcademicWorkflowRequest,
    topic_plan: WorkflowTopicPlan,
) -> object:
    researcher = factory(request, topic_plan)
    await researcher.conduct_research()

    raw_context = researcher.get_research_context()
    context_blocks = _safe_contract_call(lambda: _normalize_context(raw_context))
    if context_blocks is _CONTRACT_FAILURE:
        return _CONTRACT_FAILURE

    raw_candidates = researcher.get_paper_candidates()
    candidates = _safe_contract_call(lambda: _snapshot_candidates(raw_candidates))
    if candidates is _CONTRACT_FAILURE:
        return _CONTRACT_FAILURE

    try:
        raw_audit = researcher.get_paper_screening_audit()
    except RuntimeError as error:
        if type(error) is RuntimeError and error.args == (AUDIT_UNAVAILABLE_MESSAGE,):
            raw_audit = None
        else:
            raise
    audit = _safe_contract_call(lambda: _validate_audit(raw_audit))
    if audit is _CONTRACT_FAILURE:
        return _CONTRACT_FAILURE

    raw_sources = researcher.get_research_sources()
    research_sources = _safe_contract_call(
        lambda: _snapshot_research_sources(raw_sources)
    )
    if research_sources is _CONTRACT_FAILURE:
        return _CONTRACT_FAILURE

    raw_visited = researcher.get_source_urls()
    visited_urls = _safe_contract_call(lambda: _snapshot_visited_urls(raw_visited))
    if visited_urls is _CONTRACT_FAILURE:
        return _CONTRACT_FAILURE

    if context_blocks == ():
        return AdapterFailure(code="research_evidence_failed")

    evidence = _safe_contract_call(
        lambda: _build_evidence(
            topic_plan,
            context_blocks,  # type: ignore[arg-type]
            candidates,  # type: ignore[arg-type]
            audit,  # type: ignore[arg-type]
            research_sources,  # type: ignore[arg-type]
            visited_urls,  # type: ignore[arg-type]
        )
    )
    return evidence


def _raise_contract_error() -> None:
    raise _ResearchEvidenceContractError(
        "research evidence adapter contract violation"
    )


class GPTResearcherResearchEvidenceAdapter:
    def __init__(
        self,
        delegate: AcademicWritingAdapter,
        *,
        researcher_factory: ResearcherFactory | None = None,
    ) -> None:
        self._delegate = delegate
        self._researcher_factory = (
            _create_production_researcher
            if researcher_factory is None
            else researcher_factory
        )

    async def plan_topic(
        self,
        request: AcademicWorkflowRequest,
    ) -> WorkflowTopicPlan | AdapterFailure:
        return await self._delegate.plan_topic(request)

    async def collect_research_evidence(
        self,
        request: AcademicWorkflowRequest,
        topic_plan: WorkflowTopicPlan,
    ) -> WorkflowResearchEvidence | AdapterFailure:
        outcome = await _collect_with_researcher(
            self._researcher_factory, request, topic_plan
        )
        if outcome is _CONTRACT_FAILURE:
            _raise_contract_error()
        return outcome  # type: ignore[return-value]

    async def write_outline(
        self,
        request: AcademicWorkflowRequest,
        topic_plan: WorkflowTopicPlan,
        evidence: WorkflowResearchEvidence,
    ) -> WorkflowOutline | AdapterFailure:
        return await self._delegate.write_outline(request, topic_plan, evidence)
