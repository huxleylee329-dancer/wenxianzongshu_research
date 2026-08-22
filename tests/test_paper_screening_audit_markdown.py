import socket
import pathlib
import asyncio
import urllib.request

import aiohttp
import httpx
import pytest
import requests

from gpt_researcher.screening.audit import (
    PaperScreeningAuditCollector,
    PaperScreeningProviderWarning,
    PaperScreeningRequestMetadata,
    ProviderWarningCategory,
    build_paper_screening_web_pass_audit,
)
from gpt_researcher.screening.audit_markdown import (
    format_paper_screening_audit_markdown,
)
from gpt_researcher.screening.decisions import (
    CandidateOccurrence,
    RetrievalRequestRoute,
    ScreeningPolicy,
)
from gpt_researcher.screening.models import PaperCandidate
from gpt_researcher.screening.relevance import (
    TopicRelevanceDecision,
    TopicRelevanceReasonCode,
    TopicRelevanceVerdict,
    TopicScreeningResult,
)
from gpt_researcher.screening.rules import screen_paper_occurrences


@pytest.fixture(autouse=True)
def _no_external_access(monkeypatch):
    def blocked(*_args, **_kwargs):
        pytest.fail("real external access is forbidden")

    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(requests.sessions.Session, "request", blocked)
    monkeypatch.setattr(urllib.request, "urlopen", blocked)
    monkeypatch.setattr(httpx.Client, "request", blocked)
    monkeypatch.setattr(httpx.AsyncClient, "request", blocked)
    monkeypatch.setattr(aiohttp.ClientSession, "_request", blocked)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", blocked)
    monkeypatch.setattr(pathlib.Path, "write_text", blocked)
    monkeypatch.setattr(pathlib.Path, "write_bytes", blocked)


def _candidate(
    candidate_id,
    *,
    href="https://example.org/paper",
    year=2024,
    rank=1,
    doi="10.1/example",
    title=None,
    venue="Venue",
    query="query",
):
    return PaperCandidate(
        candidate_id=candidate_id,
        source="arxiv",
        source_record_id=candidate_id,
        retrieval_query=query,
        source_rank=rank,
        title=title or f"Title {candidate_id}",
        href=href,
        body="BODY_MUST_NOT_APPEAR",
        abstract="ABSTRACT_MUST_NOT_APPEAR",
        authors=(),
        published_year=year,
        published_at=None,
        updated_at=None,
        venue=venue,
        publication_venue_id=None,
        publication_venue_name=None,
        publication_venue_type=None,
        publication_venue_alternate_names=(),
        doi=doi,
        external_ids=(),
        citation_count=None,
        publication_types=(),
        categories=(),
        journal_reference=None,
    )


def _snapshot(*, href=None, count=1):
    policy = ScreeningPolicy()
    occurrences = ()
    metadata = ()
    if href is not None:
        occurrences = tuple(
            CandidateOccurrence(
                occurrence_id=f"occ:{index}",
                retrieval_request_id="evidence:000001",
                planning_only=False,
                candidate=_candidate(
                    f"paper-{index}",
                    href=href,
                    rank=index,
                    doi=f"10.1/example-{index}",
                ),
            )
            for index in range(1, count + 1)
        )
        metadata = (
            PaperScreeningRequestMetadata(
                retrieval_request_id="evidence:000001",
                planning_only=False,
                retrieval_query="query",
                provider_warnings=(),
            ),
        )
    result = screen_paper_occurrences(occurrences, policy)
    collector = PaperScreeningAuditCollector(
        run_ordinal=1,
        policy=policy,
        topic_relevance_enabled=False,
    )
    pass_ref = collector.allocate_web_pass()
    collector.add_pass(
        build_paper_screening_web_pass_audit(
            web_pass_order=pass_ref.web_pass_order,
            web_pass_id=pass_ref.web_pass_id,
            policy=policy,
            topic_relevance_enabled=False,
            deterministic_result=result,
            topic_result=None,
            request_metadata=metadata,
        )
    )
    collector.prepare()
    collector.commit()
    return collector.snapshot()


def _composite_snapshot():
    occurrences = (
        CandidateOccurrence(
            occurrence_id="occ:planning",
            retrieval_request_id="planning:000001",
            planning_only=True,
            candidate=_candidate(
                "planning",
                href="https://papers.example/planning",
                doi="10.1/planning",
                rank=1,
                title="Planning Paper",
                venue=None,
                query="ROOT_QUERY_MUST_NOT_APPEAR",
            ),
        ),
        CandidateOccurrence(
            occurrence_id="occ:relevant",
            retrieval_request_id="evidence:000001",
            planning_only=False,
            candidate=_candidate(
                "relevant",
                href="https://papers.example/relevant",
                doi="10.1/relevant",
                rank=2,
                title="Relevant Paper",
                venue="Journal A",
                query="EVIDENCE_QUERY_MUST_NOT_APPEAR",
            ),
        ),
        CandidateOccurrence(
            occurrence_id="occ:uncertain",
            retrieval_request_id="evidence:000001",
            planning_only=False,
            candidate=_candidate(
                "uncertain",
                href="https://papers.example/uncertain",
                doi="10.1/uncertain",
                rank=3,
                title="Uncertain Paper",
                venue="Journal B",
            ),
        ),
        CandidateOccurrence(
            occurrence_id="occ:irrelevant",
            retrieval_request_id="evidence:000001",
            planning_only=False,
            candidate=_candidate(
                "irrelevant",
                href="https://papers.example/irrelevant",
                doi="10.1/irrelevant",
                rank=4,
                title="Irrelevant Paper",
                venue="Journal C",
            ),
        ),
    )
    policy = ScreeningPolicy(min_year=2020)
    deterministic = screen_paper_occurrences(occurrences, policy)
    verdicts = {
        "planning": (
            TopicRelevanceVerdict.RELEVANT,
            TopicRelevanceReasonCode.SUPPORTING_CONTEXT,
            "Planning rationale.",
            61,
        ),
        "relevant": (
            TopicRelevanceVerdict.RELEVANT,
            TopicRelevanceReasonCode.DIRECT_TOPIC_MATCH,
            "Relevant rationale.",
            95,
        ),
        "uncertain": (
            TopicRelevanceVerdict.UNCERTAIN,
            TopicRelevanceReasonCode.LLM_FAILURE,
            "Uncertain rationale.",
            0,
        ),
        "irrelevant": (
            TopicRelevanceVerdict.IRRELEVANT,
            TopicRelevanceReasonCode.OUT_OF_SCOPE,
            "Irrelevant rationale.",
            90,
        ),
    }
    occurrence_by_id = {item.occurrence_id: item for item in occurrences}
    decisions = []
    irrelevant_occurrence_ids = set()
    for group in deterministic.duplicate_groups:
        if group.canonical_occurrence_id is None:
            continue
        occurrence = occurrence_by_id[group.canonical_occurrence_id]
        verdict, reason, rationale, confidence = verdicts[
            occurrence.candidate.candidate_id
        ]
        decisions.append(
            TopicRelevanceDecision(
                decision_order=len(decisions) + 1,
                duplicate_group_id=group.group_id,
                canonical_occurrence_id=occurrence.occurrence_id,
                canonical_candidate_id=occurrence.candidate.candidate_id,
                verdict=verdict,
                reason_code=reason,
                rationale=rationale,
                confidence=confidence,
            )
        )
        if verdict is TopicRelevanceVerdict.IRRELEVANT:
            irrelevant_occurrence_ids.add(occurrence.occurrence_id)
    topic = TopicScreeningResult(
        deterministic_result=deterministic,
        relevance_decisions=tuple(decisions),
        effective_routes=tuple(
            RetrievalRequestRoute(
                retrieval_request_id=route.retrieval_request_id,
                canonical_occurrence_ids=tuple(
                    occurrence_id
                    for occurrence_id in route.canonical_occurrence_ids
                    if occurrence_id not in irrelevant_occurrence_ids
                ),
            )
            for route in deterministic.routes
        ),
    )
    warning = PaperScreeningProviderWarning(
        retriever_index=2,
        source_identifier="tests.FakeRetriever",
        category=ProviderWarningCategory.CONTRACT,
    )
    request_metadata = (
        PaperScreeningRequestMetadata(
            retrieval_request_id="planning:000001",
            planning_only=True,
            retrieval_query="ROOT_QUERY_MUST_NOT_APPEAR",
            provider_warnings=(),
        ),
        PaperScreeningRequestMetadata(
            retrieval_request_id="evidence:000001",
            planning_only=False,
            retrieval_query="EVIDENCE_QUERY_MUST_NOT_APPEAR",
            provider_warnings=(warning,),
        ),
    )
    collector = PaperScreeningAuditCollector(
        run_ordinal=3,
        policy=policy,
        topic_relevance_enabled=True,
    )
    pass_ref = collector.allocate_web_pass()
    collector.add_pass(
        build_paper_screening_web_pass_audit(
            web_pass_order=pass_ref.web_pass_order,
            web_pass_id=pass_ref.web_pass_id,
            policy=policy,
            topic_relevance_enabled=True,
            deterministic_result=deterministic,
            topic_result=topic,
            request_metadata=request_metadata,
        )
    )
    collector.prepare()
    collector.commit()
    return collector.snapshot()


EXPECTED_COMPOSITE_EN = r"""## Paper Screening Process

### Scope

This appendix records paper-screening and evidence-routing results. A paper entering the evidence stage does not mean it was cited in the final report. The current system does not reliably track which candidate papers the LLM ultimately cited.

### Policy Snapshot

- Minimum year: 2020
- Maximum year: N/A
- Unknown-year policy enabled: Enabled
- Allowed paper types: All paper types
- Unknown-paper-type policy enabled: Enabled
- Topic relevance enabled: Enabled

### Summary

- Total occurrences: 4
- Duplicate occurrences: 0
- Deterministically included occurrences: 4
- Deterministically excluded occurrences: 0
- Occurrences excluded by year rules: 0
- Occurrences excluded by paper-type rules: 0
- Occurrences excluded as duplicates: 0
- Total groups: 4
- Groups with a canonical paper: 4
- Deterministically included groups: 4
- Topic-relevant groups: 2
- Topic-irrelevant groups: 1
- Topic-uncertain groups: 1
- Screening-included groups: 3
- Routed groups: 2
- Planning-only groups: 1
- Total requests: 2
- Requests without academic occurrences: 0
- Provider warnings: 1
- Academic candidate state: Academic candidates were collected.

### Routed Canonical Papers

#### Paper Record 1

- Title: Relevant Paper
- Year: 2024
- Paper type: preprint
- Venue: Journal A
- DOI: 10\.1/relevant
- Source: arxiv
- Paper URL: [Open paper](https://papers.example/relevant)
- Topic verdict: relevant
- Topic reason code: direct\_topic\_match
- Topic rationale: Relevant rationale\.
- Screening included: Enabled
- Routing status: routed
- Routed request count: 1
- Planning-only occurrence: Disabled

#### Paper Record 2

- Title: Uncertain Paper
- Year: 2024
- Paper type: preprint
- Venue: Journal B
- DOI: 10\.1/uncertain
- Source: arxiv
- Paper URL: [Open paper](https://papers.example/uncertain)
- Topic verdict: uncertain
- Topic reason code: llm\_failure
- Topic rationale: Uncertain rationale\.
- Screening included: Enabled
- Routing status: routed
- Routed request count: 1
- Planning-only occurrence: Disabled

### Planning-only Canonical Papers

#### Paper Record 1

- Title: Planning Paper
- Year: 2024
- Paper type: preprint
- Venue: N/A
- DOI: 10\.1/planning
- Source: arxiv
- Paper URL: [Open paper](https://papers.example/planning)
- Topic verdict: relevant
- Topic reason code: supporting\_context
- Topic rationale: Planning rationale\.
- Screening included: Enabled
- Routing status: planning\_only
- Routed request count: 0
- Planning-only occurrence: Enabled

### Excluded Occurrences

#### Excluded Occurrence 1

- Title: Irrelevant Paper
- Year: 2024
- Paper type: preprint
- Venue: Journal C
- DOI: 10\.1/irrelevant
- Source: arxiv
- Paper URL: [Open paper](https://papers.example/irrelevant)
- Canonical occurrence: Enabled
- Duplicate group ID: group:sha256:d1bb50d3b1aadca7fe7a87513cfb20430325db3740d32176e1608c6102732fe9
- Final exclusion reasons: topic\_irrelevant
- Topic verdict: irrelevant
- Topic reason code: out\_of\_scope
- Topic rationale: Irrelevant rationale\.

### Provider Warnings

#### Provider Warning 1

- Web pass ID: web\-pass:000001
- Retrieval request ID: evidence:000001
- Retriever index: 2
- Source identifier: tests\.FakeRetriever
- Warning category: contract
"""


EXPECTED_COMPOSITE_ZH = r"""## 论文筛选过程

### 范围说明

本附录记录论文筛选和证据路由结果。论文进入证据阶段并不表示其已在最终报告中被引用。当前系统无法可靠追踪大语言模型最终引用了哪些候选论文。

### 筛选策略

- 最小年份：2020
- 最大年份：N/A
- 允许未知年份：启用
- 允许的论文类型：全部论文类型
- 允许未知论文类型：启用
- 启用主题相关性判断：启用

### 汇总

- 论文出现总数：4
- 重复论文出现数：0
- 确定性筛选纳入的论文出现数：4
- 确定性筛选排除的论文出现数：0
- 因年份规则排除的论文出现数：0
- 因论文类型规则排除的论文出现数：0
- 作为重复项排除的论文出现数：0
- 论文组总数：4
- 含规范论文的论文组数：4
- 确定性筛选纳入的论文组数：4
- 主题相关的论文组数：2
- 主题不相关的论文组数：1
- 主题相关性不确定的论文组数：1
- 最终筛选纳入的论文组数：3
- 已路由的论文组数：2
- 仅规划阶段的论文组数：1
- 检索请求总数：2
- 无学术论文出现的检索请求数：0
- 数据源警告数：1
- 学术候选状态：已收集学术候选论文。

### 进入证据阶段的规范论文

#### 论文记录 1

- 标题：Relevant Paper
- 年份：2024
- 论文类型：preprint
- 期刊或会议：Journal A
- DOI：10\.1/relevant
- 来源：arxiv
- 论文链接：[打开论文](https://papers.example/relevant)
- 主题判断：relevant
- 主题原因代码：direct\_topic\_match
- 主题判断理由：Relevant rationale\.
- 筛选纳入：启用
- 路由状态：routed
- 路由请求数：1
- 仅规划阶段出现：禁用

#### 论文记录 2

- 标题：Uncertain Paper
- 年份：2024
- 论文类型：preprint
- 期刊或会议：Journal B
- DOI：10\.1/uncertain
- 来源：arxiv
- 论文链接：[打开论文](https://papers.example/uncertain)
- 主题判断：uncertain
- 主题原因代码：llm\_failure
- 主题判断理由：Uncertain rationale\.
- 筛选纳入：启用
- 路由状态：routed
- 路由请求数：1
- 仅规划阶段出现：禁用

### 仅规划阶段论文

#### 论文记录 1

- 标题：Planning Paper
- 年份：2024
- 论文类型：preprint
- 期刊或会议：N/A
- DOI：10\.1/planning
- 来源：arxiv
- 论文链接：[打开论文](https://papers.example/planning)
- 主题判断：relevant
- 主题原因代码：supporting\_context
- 主题判断理由：Planning rationale\.
- 筛选纳入：启用
- 路由状态：planning\_only
- 路由请求数：0
- 仅规划阶段出现：启用

### 排除记录

#### 排除记录 1

- 标题：Irrelevant Paper
- 年份：2024
- 论文类型：preprint
- 期刊或会议：Journal C
- DOI：10\.1/irrelevant
- 来源：arxiv
- 论文链接：[打开论文](https://papers.example/irrelevant)
- 是否为规范论文出现：启用
- 重复组 ID：group:sha256:d1bb50d3b1aadca7fe7a87513cfb20430325db3740d32176e1608c6102732fe9
- 最终排除原因：topic\_irrelevant
- 主题判断：irrelevant
- 主题原因代码：out\_of\_scope
- 主题判断理由：Irrelevant rationale\.

### 数据源警告

#### 数据源警告 1

- Web 阶段 ID：web\-pass:000001
- 检索请求 ID：evidence:000001
- Retriever 序号：2
- 来源标识：tests\.FakeRetriever
- 警告类别：contract
"""


def test_nonempty_composite_english_appendix_is_exact_golden_bytes():
    actual = format_paper_screening_audit_markdown(_composite_snapshot(), "en")
    assert actual == EXPECTED_COMPOSITE_EN


def test_nonempty_composite_chinese_appendix_is_exact_golden_bytes():
    actual = format_paper_screening_audit_markdown(_composite_snapshot(), "zh")
    assert actual == EXPECTED_COMPOSITE_ZH
    for forbidden in (
        "ROOT_QUERY_MUST_NOT_APPEAR",
        "EVIDENCE_QUERY_MUST_NOT_APPEAR",
        "BODY_MUST_NOT_APPEAR",
        "ABSTRACT_MUST_NOT_APPEAR",
    ):
        assert forbidden not in actual


EXPECTED_NON_HTTP_EN = r"""## Paper Screening Process

### Scope

This appendix records paper-screening and evidence-routing results. A paper entering the evidence stage does not mean it was cited in the final report. The current system does not reliably track which candidate papers the LLM ultimately cited.

### Policy Snapshot

- Minimum year: N/A
- Maximum year: N/A
- Unknown-year policy enabled: Enabled
- Allowed paper types: All paper types
- Unknown-paper-type policy enabled: Enabled
- Topic relevance enabled: Disabled

### Summary

- Total occurrences: 1
- Duplicate occurrences: 0
- Deterministically included occurrences: 1
- Deterministically excluded occurrences: 0
- Occurrences excluded by year rules: 0
- Occurrences excluded by paper-type rules: 0
- Occurrences excluded as duplicates: 0
- Total groups: 1
- Groups with a canonical paper: 1
- Deterministically included groups: 1
- Topic-relevant groups: 0
- Topic-irrelevant groups: 0
- Topic-uncertain groups: 0
- Screening-included groups: 1
- Routed groups: 1
- Planning-only groups: 0
- Total requests: 1
- Requests without academic occurrences: 0
- Provider warnings: 0
- Academic candidate state: Academic candidates were collected.

### Routed Canonical Papers

#### Paper Record 1

- Title: Title paper\-1
- Year: 2024
- Paper type: preprint
- Venue: Venue
- DOI: 10\.1/example\-1
- Source: arxiv
- Paper URL: data:text/plain,&lt;x&gt;
- Topic verdict: Not run
- Topic reason code: Not run
- Topic rationale: Not run
- Screening included: Enabled
- Routing status: routed
- Routed request count: 1
- Planning-only occurrence: Disabled

### Planning-only Canonical Papers

- No planning-only canonical papers.

### Excluded Occurrences

- No excluded occurrences.

### Provider Warnings

- No Provider warnings.
"""


EXPECTED_NON_HTTP_ZH = r"""## 论文筛选过程

### 范围说明

本附录记录论文筛选和证据路由结果。论文进入证据阶段并不表示其已在最终报告中被引用。当前系统无法可靠追踪大语言模型最终引用了哪些候选论文。

### 筛选策略

- 最小年份：N/A
- 最大年份：N/A
- 允许未知年份：启用
- 允许的论文类型：全部论文类型
- 允许未知论文类型：启用
- 启用主题相关性判断：禁用

### 汇总

- 论文出现总数：1
- 重复论文出现数：0
- 确定性筛选纳入的论文出现数：1
- 确定性筛选排除的论文出现数：0
- 因年份规则排除的论文出现数：0
- 因论文类型规则排除的论文出现数：0
- 作为重复项排除的论文出现数：0
- 论文组总数：1
- 含规范论文的论文组数：1
- 确定性筛选纳入的论文组数：1
- 主题相关的论文组数：0
- 主题不相关的论文组数：0
- 主题相关性不确定的论文组数：0
- 最终筛选纳入的论文组数：1
- 已路由的论文组数：1
- 仅规划阶段的论文组数：0
- 检索请求总数：1
- 无学术论文出现的检索请求数：0
- 数据源警告数：0
- 学术候选状态：已收集学术候选论文。

### 进入证据阶段的规范论文

#### 论文记录 1

- 标题：Title paper\-1
- 年份：2024
- 论文类型：preprint
- 期刊或会议：Venue
- DOI：10\.1/example\-1
- 来源：arxiv
- 论文链接：data:text/plain,&lt;x&gt;
- 主题判断：未运行
- 主题原因代码：未运行
- 主题判断理由：未运行
- 筛选纳入：启用
- 路由状态：routed
- 路由请求数：1
- 仅规划阶段出现：禁用

### 仅规划阶段论文

- 没有仅规划阶段的规范论文。

### 排除记录

- 没有排除记录。

### 数据源警告

- 没有数据源警告。
"""


@pytest.mark.parametrize(
    "href",
    (
        "https://host/\x00",
        "https://host/\x1f",
        "https://host/\x7f",
        "https://host\\evil/path",
        "https://2001:db8::1/path",
        "https://[::1%zone]/path",
        "https://[::1%25zone]/path",
        "https://[v1.fe80]/path",
        "https://host/%00",
        "https://host/%1F",
        "https://host/%7F",
        "https://host/%",
        "https://host/%A",
        "https://host/%GG",
    ),
)
def test_frozen_url_failure_matrix_is_fatal(href):
    with pytest.raises((TypeError, ValueError)):
        format_paper_screening_audit_markdown(_snapshot(href=href), "en")


@pytest.mark.parametrize(
    ("href", "expected_line"),
    (
        ("https://host:1/path", "- Paper URL: [Open paper](https://host:1/path)"),
        (
            "https://host:65535/path",
            "- Paper URL: [Open paper](https://host:65535/path)",
        ),
        (
            "https://host//:@!$&'+,;=-.*~%2f",
            "- Paper URL: [Open paper](https://host//:@!$&'+,;=-.*~%2F)",
        ),
        (
            "https://host/path?=&?/:@!$'+,;-.*~%2f",
            "- Paper URL: [Open paper](https://host/path?=&?/:@!$'+,;-.*~%2F)",
        ),
        (
            "https://host/path#/?::@!$&'+,;=-._~%2f",
            "- Paper URL: [Open paper](https://host/path#/?::@!$&'+,;=-._~%2F)",
        ),
        (
            "https://host/path%2fpart?x=%aa#f=%ef",
            "- Paper URL: [Open paper](https://host/path%2Fpart?x=%AA#f=%EF)",
        ),
        ("example.org/a(b)", r"- Paper URL: example\.org/a\(b\)"),
        ("data:text/plain,<x>", "- Paper URL: data:text/plain,&lt;x&gt;"),
        ("file:///tmp/a b", "- Paper URL: file:///tmp/a b"),
    ),
)
def test_frozen_url_success_and_plain_text_matrix_has_exact_bytes(href, expected_line):
    actual = format_paper_screening_audit_markdown(_snapshot(href=href), "en")
    lines = tuple(line for line in actual.splitlines() if line.startswith("- Paper URL:"))
    assert lines == (expected_line,)


def test_non_http_english_appendix_is_complete_exact_golden():
    actual = format_paper_screening_audit_markdown(
        _snapshot(href="data:text/plain,<x>"), "en"
    )
    assert actual == EXPECTED_NON_HTTP_EN


def test_non_http_chinese_appendix_is_complete_exact_golden():
    actual = format_paper_screening_audit_markdown(
        _snapshot(href="data:text/plain,<x>"), "zh"
    )
    assert actual == EXPECTED_NON_HTTP_ZH


def test_empty_english_appendix_is_exact_golden_bytes():
    expected = """## Paper Screening Process

### Scope

This appendix records paper-screening and evidence-routing results. A paper entering the evidence stage does not mean it was cited in the final report. The current system does not reliably track which candidate papers the LLM ultimately cited.

### Policy Snapshot

- Minimum year: N/A
- Maximum year: N/A
- Unknown-year policy enabled: Enabled
- Allowed paper types: All paper types
- Unknown-paper-type policy enabled: Enabled
- Topic relevance enabled: Disabled

### Summary

- Total occurrences: 0
- Duplicate occurrences: 0
- Deterministically included occurrences: 0
- Deterministically excluded occurrences: 0
- Occurrences excluded by year rules: 0
- Occurrences excluded by paper-type rules: 0
- Occurrences excluded as duplicates: 0
- Total groups: 0
- Groups with a canonical paper: 0
- Deterministically included groups: 0
- Topic-relevant groups: 0
- Topic-irrelevant groups: 0
- Topic-uncertain groups: 0
- Screening-included groups: 0
- Routed groups: 0
- Planning-only groups: 0
- Total requests: 0
- Requests without academic occurrences: 0
- Provider warnings: 0
- Academic candidate state: No academic candidates or occurrences.

### Routed Canonical Papers

- No routed canonical papers.

### Planning-only Canonical Papers

- No planning-only canonical papers.

### Excluded Occurrences

- No excluded occurrences.

### Provider Warnings

- No Provider warnings.
"""
    assert format_paper_screening_audit_markdown(_snapshot(), "en") == expected


def test_chinese_appendix_uses_frozen_headings_and_empty_states():
    result = format_paper_screening_audit_markdown(_snapshot(), "zh")
    assert result.startswith("## 论文筛选过程\n\n### 范围说明\n\n")
    assert "### 进入证据阶段的规范论文\n\n- 没有进入证据阶段的规范论文。" in result
    assert "### 数据源警告\n\n- 没有数据源警告。" in result
    assert result.endswith("\n") and "\r" not in result


@pytest.mark.parametrize(
    ("href", "expected"),
    (
        ("https://Example.COM:00080/a b?x=%2f#z", "[Open paper](https://example.com:80/a%20b?x=%2F#z)"),
        ("https://[2001:0db8::1]:001/a", "[Open paper](https://[2001:db8::1]:1/a)"),
        ("https://例子.测试/a", "[Open paper](https://xn--fsqu00a.xn--0zwm56d/a)"),
        ("javascript:alert(1)", r"javascript:alert\(1\)"),
    ),
)
def test_url_golden_bytes(href, expected):
    result = format_paper_screening_audit_markdown(_snapshot(href=href), "en")
    line = next(line for line in result.splitlines() if line.startswith("- Paper URL:"))
    assert line == f"- Paper URL: {expected}"


@pytest.mark.parametrize(
    "href",
    (
        "https://host:",
        "https://[::1]:",
        "https://host:0/",
        "https://host:65536/",
        "https://host:not-a-port/",
        "https://1.2.3.999/",
        "https://[::1%25zone]/",
        "https://host/%0A",
        "https://host/%GG",
        "https://user:pass@host/",
    ),
)
def test_malformed_supported_urls_are_fatal(href):
    with pytest.raises((TypeError, ValueError)):
        format_paper_screening_audit_markdown(_snapshot(href=href), "en")


def test_formatter_is_strict_and_never_emits_candidate_body_or_abstract():
    with pytest.raises((TypeError, ValueError)):
        format_paper_screening_audit_markdown(_snapshot(), "english")
    result = format_paper_screening_audit_markdown(_snapshot(href="https://host/"), "en")
    assert "BODY_MUST_NOT_APPEAR" not in result
    assert "ABSTRACT_MUST_NOT_APPEAR" not in result


def test_adjacent_records_have_the_frozen_heading_and_block_spacing():
    result = format_paper_screening_audit_markdown(
        _snapshot(href="https://host/paper", count=2), "en"
    )
    first_end = "- Planning-only occurrence: Disabled"
    assert (
        first_end
        + "\n\n#### Paper Record 2\n\n- Title: Title paper\\-2"
    ) in result
    assert "#### Paper Record 1\n- Title:" not in result
    assert "#### Paper Record 2\n- Title:" not in result


@pytest.mark.parametrize(
    ("href", "expected_url"),
    (
        ("http://host:80/", "http://host:80/"),
        ("https://host:443/", "https://host:443/"),
        ("https://host:001/", "https://host:1/"),
        ("https://host/a_b?x=a&y=b#c_d", "https://host/a_b?x=a&y=b#c_d"),
    ),
)
def test_explicit_ports_and_safe_sets_have_one_normalized_link(href, expected_url):
    result = format_paper_screening_audit_markdown(_snapshot(href=href), "en")
    expected = f"[Open paper]({expected_url})"
    assert result.count(expected) == 1
    assert "title=" not in result


@pytest.mark.parametrize("language", ("en", "zh"))
@pytest.mark.parametrize(
    "href",
    (
        "https://foo)bar/path",
        "https://foo(bar/path",
        "https://foo bar/path",
        "https://foo%20bar/path",
        "https://foo_bar/path",
        "https://foo+bar/path",
        "https://foo[bar/path",
        "https://foo]bar/path",
        "https://foo`bar/path",
        "https://-foo.example/path",
        "https://foo-.example/path",
        "https://foo..example/path",
        f"https://{'a' * 64}.example/path",
        f"https://{'a' * 63}.{'b' * 63}.{'c' * 63}.{'d' * 62}/path",
    ),
)
def test_dangerous_http_hostnames_fail_without_markdown_or_link(href, language):
    rendered = None
    with pytest.raises((TypeError, ValueError)):
        rendered = format_paper_screening_audit_markdown(
            _snapshot(href=href),
            language,
        )

    assert rendered is None
    assert "[Open paper](" not in (rendered or "")
    assert "[打开论文](" not in (rendered or "")


@pytest.mark.parametrize(
    ("href", "normalized_url"),
    (
        ("https://foo-bar.example/path", "https://foo-bar.example/path"),
        ("https://a/path", "https://a/path"),
        ("https://7.example/path", "https://7.example/path"),
        (
            "https://例子.测试/path",
            "https://xn--fsqu00a.xn--0zwm56d/path",
        ),
        (
            "https://xn--fsqu00a.xn--0zwm56d/path",
            "https://xn--fsqu00a.xn--0zwm56d/path",
        ),
        (
            f"https://{'a' * 63}.example/path",
            f"https://{'a' * 63}.example/path",
        ),
        (
            f"https://{'a' * 63}.{'b' * 63}.{'c' * 63}.{'d' * 61}/path",
            f"https://{'a' * 63}.{'b' * 63}.{'c' * 63}.{'d' * 61}/path",
        ),
    ),
)
@pytest.mark.parametrize(
    ("language", "field_prefix", "link_label"),
    (
        ("en", "- Paper URL: ", "Open paper"),
        ("zh", "- 论文链接：", "打开论文"),
    ),
)
def test_valid_idna_hostname_boundaries_have_exact_markdown_link_bytes(
    href,
    normalized_url,
    language,
    field_prefix,
    link_label,
):
    result = format_paper_screening_audit_markdown(_snapshot(href=href), language)
    line = next(line for line in result.splitlines() if line.startswith(field_prefix))
    assert line == f"{field_prefix}[{link_label}]({normalized_url})"
