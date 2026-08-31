"""Deterministic Markdown presentation for finalized paper-screening audits."""

import ipaddress
import re
from enum import Enum
from urllib.parse import quote, urlsplit, urlunsplit

from .audit import (
    AuditExclusionReason,
    AuditRoutingStatus,
    PaperScreeningAuditSnapshot,
    ProviderWarningCategory,
)
from .decisions import PaperType, ScreeningReasonCode, UnknownValuePolicy
from .relevance import TopicRelevanceReasonCode, TopicRelevanceVerdict


PATH_SAFE = "/:@!$&'+,;=-.*~%"
QUERY_SAFE = "=&?/:@!$'+,;-.*~%"
FRAGMENT_SAFE = "/?:@!$&'+,;=-._~%"
_MARKDOWN_TEXT = frozenset("`*_{}[]()#+-.!|>")
_HEX = frozenset("0123456789abcdefABCDEF")
_IDNA_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def _text(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("display text must be a string")
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = value.replace("\\", "\\\\").replace("\n", "\\n")
    value = value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return "".join(("\\" + char) if char in _MARKDOWN_TEXT else char for char in value)


def _required(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("required display text must be a nonblank string")
    return _text(value)


def _optional(value: str | None) -> str:
    if value is None:
        return "N/A"
    return _required(value)


def _integer(value: int) -> str:
    if type(value) is not int:
        raise TypeError("display integer must be a strict integer")
    return f"{value:d}"


def _boolean(value: bool, language: str) -> str:
    if type(value) is not bool:
        raise TypeError("display boolean must be a strict boolean")
    if language == "zh":
        return "启用" if value else "禁用"
    return "Enabled" if value else "Disabled"


def _enum(value: Enum, expected: type[Enum]) -> str:
    if type(value) is not expected:
        raise TypeError("display enum has an unexpected type")
    raw = value.value
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("display enum value must be a nonblank string")
    return _text(raw)


def _enum_tuple(values: tuple[Enum, ...], expected: type[Enum]) -> str:
    if type(values) is not tuple or not values:
        raise ValueError("display enum tuple must be a nonempty tuple")
    return ", ".join(_enum(value, expected) for value in values)


def _normalize_percent_escapes(component: str) -> str:
    pieces: list[str] = []
    index = 0
    while index < len(component):
        char = component[index]
        if char != "%":
            pieces.append(char)
            index += 1
            continue
        if index + 2 >= len(component):
            raise ValueError("URL contains an incomplete percent escape")
        pair = component[index + 1 : index + 3]
        if any(char not in _HEX for char in pair):
            raise ValueError("URL contains an invalid percent escape")
        byte = int(pair, 16)
        if byte <= 0x1F or byte == 0x7F:
            raise ValueError("URL contains a percent-encoded control character")
        pieces.append("%" + pair.upper())
        index += 3
    return "".join(pieces)


def _paper_url(href: str, language: str) -> str:
    if not isinstance(href, str):
        raise TypeError("paper href must be a string")
    if any(ord(char) <= 0x1F or ord(char) == 0x7F for char in href):
        raise ValueError("paper href contains a control character")
    try:
        split = urlsplit(href)
    except Exception:
        raise ValueError("paper href cannot be parsed") from None
    scheme = split.scheme.casefold()
    if scheme not in {"http", "https"}:
        return _text(href)

    netloc = split.netloc
    if not netloc or "\\" in netloc:
        raise ValueError("HTTP URL authority is invalid")
    try:
        if split.username is not None or split.password is not None:
            raise ValueError("HTTP URL userinfo is forbidden")
        hostname = split.hostname
    except Exception:
        raise ValueError("HTTP URL host is invalid") from None
    if not hostname:
        raise ValueError("HTTP URL host is empty")

    bracketed = netloc.startswith("[")
    explicit_port = False
    if bracketed:
        close = netloc.find("]")
        if close < 0:
            raise ValueError("IPv6 authority is malformed")
        suffix = netloc[close + 1 :]
        if suffix:
            if not suffix.startswith(":") or suffix.count(":") != 1:
                raise ValueError("IPv6 authority suffix is malformed")
            port_text = suffix[1:]
            if not port_text or not port_text.isascii() or not port_text.isdecimal():
                raise ValueError("explicit port must contain ASCII digits")
            explicit_port = True
    else:
        colon_count = netloc.count(":")
        if colon_count > 1:
            raise ValueError("non-bracketed authority contains multiple colons")
        if colon_count == 1:
            port_text = netloc.rsplit(":", 1)[1]
            if not port_text or not port_text.isascii() or not port_text.isdecimal():
                raise ValueError("explicit port must contain ASCII digits")
            explicit_port = True

    normalized_port: str | None = None
    if explicit_port:
        try:
            port = split.port
        except Exception:
            raise ValueError("explicit port cannot be parsed") from None
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("explicit port is outside the allowed range")
        normalized_port = f"{port:d}"

    if bracketed:
        raw_host = netloc[1 : netloc.find("]")]
        if "%" in raw_host or raw_host.casefold().startswith("v") and "." in raw_host:
            raise ValueError("IPv6 zones and IPvFuture are forbidden")
        try:
            normalized_host = f"[{ipaddress.IPv6Address(raw_host).compressed.lower()}]"
        except Exception:
            raise ValueError("IPv6 host is invalid") from None
    elif hostname and all(char in "0123456789." for char in hostname):
        try:
            normalized_host = str(ipaddress.IPv4Address(hostname))
        except Exception:
            raise ValueError("IPv4 host is invalid") from None
    else:
        labels = hostname.split(".")
        if any(not label for label in labels):
            raise ValueError("IDNA host contains an empty label")
        encoded_labels: list[str] = []
        for label in labels:
            try:
                encoded_bytes = label.encode("idna")
                encoded = encoded_bytes.decode("ascii", errors="strict").lower()
            except Exception:
                raise ValueError("IDNA label is invalid") from None
            if not 1 <= len(encoded.encode("ascii", errors="strict")) <= 63:
                raise ValueError("IDNA label length is invalid")
            if _IDNA_LABEL.fullmatch(encoded) is None:
                raise ValueError("IDNA label syntax is invalid")
            encoded_labels.append(encoded)
        normalized_host = ".".join(encoded_labels)
        if len(normalized_host.encode("ascii")) > 253:
            raise ValueError("IDNA host is too long")

    authority = normalized_host
    if normalized_port is not None:
        authority += ":" + normalized_port
    path = quote(
        _normalize_percent_escapes(split.path),
        safe=PATH_SAFE,
        encoding="utf-8",
        errors="strict",
    )
    query = quote(
        _normalize_percent_escapes(split.query),
        safe=QUERY_SAFE,
        encoding="utf-8",
        errors="strict",
    )
    fragment = quote(
        _normalize_percent_escapes(split.fragment),
        safe=FRAGMENT_SAFE,
        encoding="utf-8",
        errors="strict",
    )
    normalized_url = urlunsplit((scheme, authority, path, query, fragment))
    label = "打开论文" if language == "zh" else "Open paper"
    return f"[{label}]({normalized_url})"


def _topic(value, expected, enabled: bool, language: str) -> str:
    if not enabled:
        return "未运行" if language == "zh" else "Not run"
    if value is None:
        return "N/A"
    return _enum(value, expected)


def _topic_rationale(value: str | None, enabled: bool, language: str) -> str:
    if not enabled:
        return "未运行" if language == "zh" else "Not run"
    return _optional(value)


def _paper_block(
    entry,
    group,
    order: int,
    language: str,
    *,
    excluded: bool,
    topic_enabled: bool,
) -> str:
    url = _paper_url(entry.href, language)
    if excluded:
        if type(entry.final_exclusion_reasons) is not tuple or not entry.final_exclusion_reasons:
            raise ValueError("excluded occurrence requires reasons")
        reasons = _enum_tuple(entry.final_exclusion_reasons, AuditExclusionReason)
        if language == "zh":
            lines = [
                f"#### 排除记录 {_integer(order)}",
                "",
                f"- 标题：{_required(entry.title)}",
                f"- 年份：{_optional(None) if entry.published_year is None else _integer(entry.published_year)}",
                f"- 论文类型：{_enum(entry.classified_type, PaperType)}",
                f"- 期刊或会议：{_optional(entry.venue)}",
                f"- DOI：{_optional(entry.doi)}",
                f"- 来源：{_required(entry.source)}",
                f"- 论文链接：{url}",
                f"- 是否为规范论文出现：{_boolean(entry.is_canonical, language)}",
                f"- 重复组 ID：{_required(entry.duplicate_group_id)}",
                f"- 最终排除原因：{reasons}",
                f"- 主题判断：{_topic(entry.topic_verdict, TopicRelevanceVerdict, topic_enabled, language)}",
                f"- 主题原因代码：{_topic(entry.topic_reason_code, TopicRelevanceReasonCode, topic_enabled, language)}",
                f"- 主题判断理由：{_topic_rationale(entry.topic_rationale, topic_enabled, language)}",
            ]
        else:
            lines = [
                f"#### Excluded Occurrence {_integer(order)}",
                "",
                f"- Title: {_required(entry.title)}",
                f"- Year: {_optional(None) if entry.published_year is None else _integer(entry.published_year)}",
                f"- Paper type: {_enum(entry.classified_type, PaperType)}",
                f"- Venue: {_optional(entry.venue)}",
                f"- DOI: {_optional(entry.doi)}",
                f"- Source: {_required(entry.source)}",
                f"- Paper URL: {url}",
                f"- Canonical occurrence: {_boolean(entry.is_canonical, language)}",
                f"- Duplicate group ID: {_required(entry.duplicate_group_id)}",
                f"- Final exclusion reasons: {reasons}",
                f"- Topic verdict: {_topic(entry.topic_verdict, TopicRelevanceVerdict, topic_enabled, language)}",
                f"- Topic reason code: {_topic(entry.topic_reason_code, TopicRelevanceReasonCode, topic_enabled, language)}",
                f"- Topic rationale: {_topic_rationale(entry.topic_rationale, topic_enabled, language)}",
            ]
        return "\n".join(lines)

    if group is None:
        raise ValueError("included paper must resolve to a group")
    if language == "zh":
        lines = [
            f"#### 论文记录 {_integer(order)}",
            "",
            f"- 标题：{_required(entry.title)}",
            f"- 年份：{_optional(None) if entry.published_year is None else _integer(entry.published_year)}",
            f"- 论文类型：{_enum(entry.classified_type, PaperType)}",
            f"- 期刊或会议：{_optional(entry.venue)}",
            f"- DOI：{_optional(entry.doi)}",
            f"- 来源：{_required(entry.source)}",
            f"- 论文链接：{url}",
            f"- 主题判断：{_topic(entry.topic_verdict, TopicRelevanceVerdict, topic_enabled, language)}",
            f"- 主题原因代码：{_topic(entry.topic_reason_code, TopicRelevanceReasonCode, topic_enabled, language)}",
            f"- 主题判断理由：{_topic_rationale(entry.topic_rationale, topic_enabled, language)}",
            f"- 筛选纳入：{_boolean(entry.screening_included, language)}",
            f"- 路由状态：{_enum(entry.routing_status, AuditRoutingStatus)}",
            f"- 路由请求数：{_integer(len(group.routed_request_ids))}",
            f"- 仅规划阶段出现：{_boolean(entry.planning_only, language)}",
        ]
    else:
        lines = [
            f"#### Paper Record {_integer(order)}",
            "",
            f"- Title: {_required(entry.title)}",
            f"- Year: {_optional(None) if entry.published_year is None else _integer(entry.published_year)}",
            f"- Paper type: {_enum(entry.classified_type, PaperType)}",
            f"- Venue: {_optional(entry.venue)}",
            f"- DOI: {_optional(entry.doi)}",
            f"- Source: {_required(entry.source)}",
            f"- Paper URL: {url}",
            f"- Topic verdict: {_topic(entry.topic_verdict, TopicRelevanceVerdict, topic_enabled, language)}",
            f"- Topic reason code: {_topic(entry.topic_reason_code, TopicRelevanceReasonCode, topic_enabled, language)}",
            f"- Topic rationale: {_topic_rationale(entry.topic_rationale, topic_enabled, language)}",
            f"- Screening included: {_boolean(entry.screening_included, language)}",
            f"- Routing status: {_enum(entry.routing_status, AuditRoutingStatus)}",
            f"- Routed request count: {_integer(len(group.routed_request_ids))}",
            f"- Planning-only occurrence: {_boolean(entry.planning_only, language)}",
        ]
    return "\n".join(lines)


def _warning_block(request, warning, order: int, language: str) -> str:
    if language == "zh":
        lines = [
            f"#### 数据源警告 {_integer(order)}",
            "",
            f"- Web 阶段 ID：{_required(request.web_pass_id)}",
            f"- 检索请求 ID：{_required(request.retrieval_request_id)}",
            f"- Retriever 序号：{_integer(warning.retriever_index)}",
            f"- 来源标识：{_required(warning.source_identifier)}",
            f"- 警告类别：{_enum(warning.category, ProviderWarningCategory)}",
        ]
    else:
        lines = [
            f"#### Provider Warning {_integer(order)}",
            "",
            f"- Web pass ID: {_required(request.web_pass_id)}",
            f"- Retrieval request ID: {_required(request.retrieval_request_id)}",
            f"- Retriever index: {_integer(warning.retriever_index)}",
            f"- Source identifier: {_required(warning.source_identifier)}",
            f"- Warning category: {_enum(warning.category, ProviderWarningCategory)}",
        ]
    return "\n".join(lines)


def format_paper_screening_audit_markdown(
    snapshot: PaperScreeningAuditSnapshot,
    language: str,
) -> str:
    """Render one validated audit snapshot with the frozen byte template."""
    if type(snapshot) is not PaperScreeningAuditSnapshot:
        raise TypeError("snapshot must be a PaperScreeningAuditSnapshot")
    if language not in {"en", "zh"}:
        raise ValueError("language must be exactly en or zh")

    occurrences = {}
    for entry in snapshot.occurrence_entries:
        key = (entry.web_pass_id, entry.occurrence_id)
        if key in occurrences:
            raise ValueError("duplicate occurrence compound identity")
        occurrences[key] = entry
    requests = {}
    for request in snapshot.request_audits:
        key = (request.web_pass_id, request.retrieval_request_id)
        if key in requests:
            raise ValueError("duplicate request compound identity")
        requests[key] = request
    groups = {}
    for group in snapshot.group_audits:
        key = (group.web_pass_id, group.group_id)
        if key in groups:
            raise ValueError("duplicate group compound identity")
        groups[key] = group

    routed_blocks = []
    planning_blocks = []
    for group in snapshot.group_audits:
        if not group.screening_included or group.canonical_occurrence_id is None:
            continue
        entry = occurrences.get((group.web_pass_id, group.canonical_occurrence_id))
        if entry is None or entry.duplicate_group_id != group.group_id:
            raise ValueError("canonical occurrence cannot be resolved")
        if group.routed_request_ids:
            routed_blocks.append(
                _paper_block(
                    entry,
                    group,
                    len(routed_blocks) + 1,
                    language,
                    excluded=False,
                    topic_enabled=snapshot.topic_relevance_enabled,
                )
            )
        elif group.planning_only_group:
            planning_blocks.append(
                _paper_block(
                    entry,
                    group,
                    len(planning_blocks) + 1,
                    language,
                    excluded=False,
                    topic_enabled=snapshot.topic_relevance_enabled,
                )
            )
        else:
            raise ValueError("included non-planning group must be routed")

    excluded_blocks = []
    for entry in snapshot.occurrence_entries:
        if not entry.screening_included:
            group = groups.get((entry.web_pass_id, entry.duplicate_group_id))
            if group is None:
                raise ValueError("excluded occurrence group cannot be resolved")
            excluded_blocks.append(
                _paper_block(
                    entry,
                    group,
                    len(excluded_blocks) + 1,
                    language,
                    excluded=True,
                    topic_enabled=snapshot.topic_relevance_enabled,
                )
            )

    warning_blocks = []
    for request in snapshot.request_audits:
        for warning in request.provider_warnings:
            warning_blocks.append(
                _warning_block(request, warning, len(warning_blocks) + 1, language)
            )

    policy = snapshot.policy
    summary = snapshot.summary
    minimum_year = "N/A" if policy.min_year is None else _integer(policy.min_year)
    maximum_year = "N/A" if policy.max_year is None else _integer(policy.max_year)
    if policy.allowed_paper_types is None:
        allowed_types = "全部论文类型" if language == "zh" else "All paper types"
    else:
        allowed_types = _enum_tuple(policy.allowed_paper_types, PaperType)
    candidate_state = (
        ("没有学术候选论文或论文出现记录。" if language == "zh" else "No academic candidates or occurrences.")
        if summary.total_occurrences == 0
        else ("已收集学术候选论文。" if language == "zh" else "Academic candidates were collected.")
    )
    routed = "\n\n".join(routed_blocks) or (
        "- 没有进入证据阶段的规范论文。" if language == "zh" else "- No routed canonical papers."
    )
    planning = "\n\n".join(planning_blocks) or (
        "- 没有仅规划阶段的规范论文。" if language == "zh" else "- No planning-only canonical papers."
    )
    excluded = "\n\n".join(excluded_blocks) or (
        "- 没有排除记录。" if language == "zh" else "- No excluded occurrences."
    )
    warnings = "\n\n".join(warning_blocks) or (
        "- 没有数据源警告。" if language == "zh" else "- No Provider warnings."
    )

    values = {
        "minimum": minimum_year,
        "maximum": maximum_year,
        "unknown_year": _boolean(policy.unknown_year is UnknownValuePolicy.INCLUDE, language),
        "types": allowed_types,
        "unknown_type": _boolean(policy.unknown_paper_type is UnknownValuePolicy.INCLUDE, language),
        "topic": _boolean(snapshot.topic_relevance_enabled, language),
    }
    summary_values = tuple(
        _integer(getattr(summary, name))
        for name in (
            "total_occurrences", "duplicate_occurrences",
            "deterministically_included_occurrences", "deterministically_excluded_occurrences",
            "excluded_by_year_occurrences", "excluded_by_type_occurrences",
            "excluded_as_duplicate_occurrences", "total_groups", "groups_with_canonical",
            "deterministically_included_groups", "topic_relevant_groups",
            "topic_irrelevant_groups", "topic_uncertain_groups", "screening_included_groups",
            "routed_groups", "planning_only_groups", "total_requests",
            "requests_without_academic_occurrences", "provider_warning_count",
        )
    )
    if language == "zh":
        labels = (
            "论文出现总数", "重复论文出现数", "确定性筛选纳入的论文出现数", "确定性筛选排除的论文出现数",
            "因年份规则排除的论文出现数", "因论文类型规则排除的论文出现数", "作为重复项排除的论文出现数",
            "论文组总数", "含规范论文的论文组数", "确定性筛选纳入的论文组数", "主题相关的论文组数",
            "主题不相关的论文组数", "主题相关性不确定的论文组数", "最终筛选纳入的论文组数",
            "已路由的论文组数", "仅规划阶段的论文组数", "检索请求总数", "无学术论文出现的检索请求数", "数据源警告数",
        )
        summary_lines = "\n".join(f"- {label}：{value}" for label, value in zip(labels, summary_values))
        result = (
            "## 论文筛选过程\n\n### 范围说明\n\n"
            "本附录记录论文筛选和证据路由结果。论文进入证据阶段并不表示其已在最终报告中被引用。当前系统无法可靠追踪大语言模型最终引用了哪些候选论文。\n\n"
            "### 筛选策略\n\n"
            f"- 最小年份：{values['minimum']}\n- 最大年份：{values['maximum']}\n"
            f"- 允许未知年份：{values['unknown_year']}\n- 允许的论文类型：{values['types']}\n"
            f"- 允许未知论文类型：{values['unknown_type']}\n- 启用主题相关性判断：{values['topic']}\n\n"
            f"### 汇总\n\n{summary_lines}\n- 学术候选状态：{candidate_state}\n\n"
            f"### 进入证据阶段的规范论文\n\n{routed}\n\n"
            f"### 仅规划阶段论文\n\n{planning}\n\n"
            f"### 排除记录\n\n{excluded}\n\n"
            f"### 数据源警告\n\n{warnings}\n"
        )
    else:
        labels = (
            "Total occurrences", "Duplicate occurrences", "Deterministically included occurrences",
            "Deterministically excluded occurrences", "Occurrences excluded by year rules",
            "Occurrences excluded by paper-type rules", "Occurrences excluded as duplicates",
            "Total groups", "Groups with a canonical paper", "Deterministically included groups",
            "Topic-relevant groups", "Topic-irrelevant groups", "Topic-uncertain groups",
            "Screening-included groups", "Routed groups", "Planning-only groups", "Total requests",
            "Requests without academic occurrences", "Provider warnings",
        )
        summary_lines = "\n".join(f"- {label}: {value}" for label, value in zip(labels, summary_values))
        result = (
            "## Paper Screening Process\n\n### Scope\n\n"
            "This appendix records paper-screening and evidence-routing results. A paper entering the evidence stage does not mean it was cited in the final report. The current system does not reliably track which candidate papers the LLM ultimately cited.\n\n"
            "### Policy Snapshot\n\n"
            f"- Minimum year: {values['minimum']}\n- Maximum year: {values['maximum']}\n"
            f"- Unknown-year policy enabled: {values['unknown_year']}\n- Allowed paper types: {values['types']}\n"
            f"- Unknown-paper-type policy enabled: {values['unknown_type']}\n- Topic relevance enabled: {values['topic']}\n\n"
            f"### Summary\n\n{summary_lines}\n- Academic candidate state: {candidate_state}\n\n"
            f"### Routed Canonical Papers\n\n{routed}\n\n"
            f"### Planning-only Canonical Papers\n\n{planning}\n\n"
            f"### Excluded Occurrences\n\n{excluded}\n\n"
            f"### Provider Warnings\n\n{warnings}\n"
        )
    if "\r" in result or not result.endswith("\n") or result.endswith("\n\n"):
        raise ValueError("formatter output violates the frozen newline contract")
    return result
