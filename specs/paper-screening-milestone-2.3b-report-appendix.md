# Paper Screening Milestone 2.3B — Report Appendix

Status: **Approved and frozen**

The IDNA hostname safety revision has completed read-only review and received
renewed explicit approval. Implementation may continue under this revised frozen
specification and remains strictly limited to the original seven-file boundary
in Section 14. If an eighth file is required, implementation must stop again and
receive a new explicit approval. This specification must not be modified during
subsequent implementation.

Milestone 2.3B adds presentation only. It does not change which papers are
screened, included, excluded, routed, compressed, or cited.

## 1. Normative foundations and current evidence

This specification builds on and must not weaken or reinterpret:

- `specs/paper-screening-milestone-2.0-candidate-model.md`;
- `specs/paper-screening-milestone-2.1-run-collection.md`;
- `specs/paper-screening-milestone-2.2a-deterministic-engine.md`;
- `specs/paper-screening-milestone-2.2b-basic-web-pipeline.md`;
- `specs/paper-screening-milestone-2.2c-topic-relevance-agent.md`; and
- `specs/paper-screening-milestone-2.3a-run-audit-snapshot.md`.

The existing report and audit architecture supplies these frozen facts:

- `BasicReport.run()` awaits `GPTResearcher.conduct_research()` and then awaits
  `GPTResearcher.write_report()`.
- A successful eligible owner `conduct_research()` publishes the immutable
  `PaperScreeningAuditSnapshot` before it returns.
- `ReportGenerator.write_report()` and `generate_report()` produce the LLM
  report body before `GPTResearcher.write_report()` returns it.
- For Basic/Web, the LLM prompt asks the LLM itself to write the References
  section. Basic Report does not subsequently call the deterministic
  `add_references()` helper.
- The Provider wrapper emits LLM report text through existing WebSocket
  messages with `type="report"` and `output=<chunk>`.
- Web clients accumulate those report chunks. They do not generally replace
  the visible report with the final `BasicReport.run()` return value.
- Markdown, PDF, and DOCX generation consume the final returned report string.
- Existing report-history persistence stores report text supplied through the
  current frontend/report APIs; Milestone 2.3B adds no separate structured
  audit persistence.

## 2. Frozen goal

When explicitly enabled for one eligible, successfully finalized Basic/Web
paper-screening run, append a deterministic Markdown representation of the
run's `PaperScreeningAuditSnapshot` to the complete final report.

The appendix must:

- be produced by a synchronous pure formatter;
- be based only on an already `FINALIZED` frozen snapshot;
- be appended after the complete LLM report, including its References;
- be returned in the same final Markdown string used by existing exports;
- be sent once as an additional existing-schema WebSocket report chunk when a
  WebSocket is present;
- make no LLM, Retriever, Provider, scraper, compressor, or MCP call;
- never enter an LLM prompt;
- leave the report body, References text, and citation numbering unchanged;
- be absent byte-for-byte when the feature is disabled or out of scope; and
- state explicitly that routing a paper into the evidence stage does not prove
  that the LLM cited it in the final report.

Milestone 2.3B does not infer or claim final-report citation membership.

## 3. Exact report-composition hook and pre-LLM plan

The sole integration point is `GPTResearcher.write_report()`. The method uses
one private frozen plan whose exact role is to bind one report invocation to
the audit that existed before report generation began:

```python
@dataclass(frozen=True)
class _PaperScreeningAuditAppendixPlan:
    snapshot: PaperScreeningAuditSnapshot
    language: Literal["zh", "en"]
```

The plan is private to `agent.py`, is not a public API, and stores only the
captured immutable snapshot and normalized appendix language. It stores no
collector, readiness flag, run ordinal, formatted output, report body, context,
images, configuration object, or mutable run state.

After the Section 4 report-write guard succeeds, and before the first `await`
or `_log_event()` in `write_report()`, synchronously:

1. evaluate the Section 5 gate in its frozen order;
2. when the gate is disabled or inapplicable, bind the plan to `None` without
   importing the formatter or calling the audit getter;
3. when enabled, call `get_paper_screening_audit()` exactly once, normalize the
   current language to exactly `zh` or `en` under Section 8, and construct the
   frozen plan; and
4. perform no formatting, I/O, logging, callback, or `await` while constructing
   the plan.

The report/research mutual-exclusion guard in Section 4 prevents an accepted
target-scope top-level owner `conduct_research()` or owner `quick_search()` on
the same instance from replacing controlled run state while the report write is
active. The immutable plan alone fixes the audit snapshot and appendix
language. It does not snapshot or change Writer inputs.

The exact asynchronous composition sequence is then frozen:

1. execute the existing report-start log and await the existing
   `self.report_generator.write_report(...)` call with existing Writer argument
   and attribute behavior unchanged;
2. receive the complete LLM-produced report string;
3. if the captured plan is `None`, do not import the formatter, call the audit
   getter, or emit an appendix WebSocket message;
4. if the plan exists, locally import the formatter and format only the plan's
   captured snapshot and normalized language;
5. format an appendix whose first bytes are the first heading bytes frozen in
   the selected Section 7 template; the separator is not part of `appendix`;
6. execute exactly one concatenation:

   ```python
   final_report = original_report + "\n\n---\n\n" + appendix
   ```

7. if and only if `self.websocket is not None`, directly execute exactly once:

   ```python
   await self.websocket.send_json({
       "type": "report",
       "output": appendix,
   })
   ```

   The `output` value must be the exact same heading-first `appendix` string
   used as the final operand in step 6. The separator is present only in the
   returned `final_report`, not in the WebSocket appendix message.
   Do not call `stream_output()`, `safe_send_json()`, or another helper that
   logs, rewrites, retries, or changes the schema;
8. execute the existing `report_completed` logging path using only its existing
   length/image metadata, never the appendix or snapshot text; and
9. return the completed report string.

After the first `await`, this invocation must not reread readiness, collector
state, the audit getter, configured language, or run ordinal. It must never
substitute a later run's snapshot or skip the captured snapshot because a later
state changed. The formatter import occurs only after report generation and
only when the already captured plan is non-`None`.

The full lifecycle from guard acquisition through plan construction, existing
logging, LLM report generation, optional formatting, optional direct WebSocket
send, completion logging, and return is enclosed by the Section 4
`try`/`finally`, so cancellation or failure always releases report-write state.

No formatter or audit data may be passed to `ReportGenerator`,
`generate_report()`, `create_chat_completion()`, a Prompt, or any LLM call. The
plan itself performs no formatting and does not modify the snapshot.

The formatter executes exactly once and only after the report LLM completes.
There is no formatter or URL preflight before the LLM and no second formatter
call after a preflight.

The appendix is after the LLM-generated References. It must not be inserted
before References or between report generation and References generation. Its
URLs therefore do not participate in existing reference generation or citation
numbering.

Do not modify `BasicReport`, backend server code, frontend code, Prompt code,
References helpers, `actions/utils.py`, or export code.

## 4. Same-instance report/research guard and readiness state

Add two private, instance-owned flags to `GPTResearcher`:

```python
_paper_screening_report_write_active: bool
_paper_screening_audit_report_ready: bool
```

The exact private spellings above are frozen. They are not public APIs and do
not modify the Milestone 2.3A getter or collector.

### 4.1 Report/research mutual exclusion

Initialize `_paper_screening_report_write_active` to exactly `False` for every
instance.

At `write_report()` entry, before plan construction, `_log_event()`, or any
other `await`:

1. if `_paper_candidate_run_active is True`, raise exactly:

   ```text
   cannot write report while a research run is active
   ```

2. if `_paper_screening_report_write_active is True`, raise exactly:

   ```text
   report writing is already active
   ```

3. otherwise set `_paper_screening_report_write_active=True` and execute the
   whole Section 3 lifecycle inside `try`/`finally`; and
4. in `finally`, set `_paper_screening_report_write_active=False`.

At entry to a top-level owner `conduct_research()` or owner `quick_search()`,
before `_begin_paper_candidate_run()` or any readiness, audit, collector,
active-run, context, image, or run-ordinal mutation, reject an active report
write with exactly:

```text
cannot start research while report writing is active
```

A rejected guard changes no readiness, collector, audit, ordinal, context,
image, candidate, or report-write state. The guard is per `GPTResearcher`
instance; different instances remain independent.

Sequential non-overlapping `write_report()` calls remain allowed. An original
`asyncio.CancelledError` propagates as the same object and `finally` releases
the report-write flag. The guard protects the target owner's report/audit
association; it does not consume readiness or the immutable snapshot.

This concurrency guarantee is limited to the same instance's target-scope
top-level Basic/Web owner run and `write_report()`. Borrowers do not create,
clear, or publish 2.3B readiness or plans and need not set
`_paper_candidate_run_active`; this milestone does not claim that
`write_report()` detects every borrower overlap. Borrower/report overlap keeps
its existing behavior and is a non-goal. Do not add a borrower guard or extend
the mechanism to Deep, Detailed, or subtopic orchestration.

Do not deep-copy or tuple-convert context or images and do not change the Writer
input contract. `context`, `available_images`, and `research_images` continue to
be read under existing Writer behavior. The guard guarantees only that a
target-scope owner conduct or Quick call cannot replace those fields through
the controlled entry points while report writing is active. External direct
attribute mutation and borrower concurrency are outside this milestone.

### 4.2 Run-matching readiness

Initialize `_paper_screening_audit_report_ready` to exactly `False` for every
`GPTResearcher` instance.

Lifecycle rules:

- An owner `conduct_research()` first passes the report-active guard and then
  executes the existing candidate overlap guard. Only after both guards
  succeed, set readiness to `False` before screening binding or external work.
- A failed report-active or candidate-overlap guard changes no readiness.
- Borrower `conduct_research()` neither reads nor changes readiness.
- An owner `quick_search()` first passes the report-active guard and then its
  existing overlap guard. Only after both guards succeed, set readiness to
  `False` before external work.
- A borrower Quick Search neither reads nor changes readiness.
- Quick Search must not clear or modify the Milestone 2.3A audit snapshot.
- Out-of-scope runs, screening-disabled runs, binding failures,
  candidate-capability failures, runs that create no audit collector, ordinary
  failures, and cancellation all leave the accepted current run's readiness
  `False`.
- Set readiness to `True` only after an exact Web/`research_report` owner run
  successfully commits its audit collector.
- Audit `commit()` and the assignment to `True` occur in the same synchronous,
  no-`await` completion interval.
- A new accepted owner conduct run immediately invalidates readiness for the
  previous run and never restores it after failure.
- A later accepted owner Quick Search invalidates report readiness without
  making the old audit getter unavailable.
- Consequently, `conduct_research() -> quick_search() -> write_report()` must
  not append the old conduct audit.
- `write_report()` does not consume or clear readiness. Multiple sequential
  report writes after the same successful eligible conduct run may each bind
  the same immutable snapshot into their independently generated report.
- Readiness is not class-level, global, or shared between instances.

This state is presentation readiness only. It does not affect candidate,
audit, Workspace, screening, routing, or Quick Search results.

## 5. Configuration and exact pre-LLM gate

Add the default-disabled string configuration:

```text
PAPER_SCREENING_AUDIT_REPORT_ENABLED=false
```

The existing `Config` loading mechanism materializes defaults, configuration
file values, and environment overrides. Milestone 2.3B does not modify
`gpt_researcher/config/config.py`.

Runtime parsing accepts only a string whose `strip().casefold()` value is
exactly `true` or `false`. Every other type or value raises `ValueError` with a
fixed configuration-specific message. Boolean objects are not accepted.

After the report-write guard succeeds and before the first `await`,
`GPTResearcher.write_report()` evaluates the gate in this exact order:

1. `report_source == "web"`;
2. `report_type == "research_report"`;
3. the current researcher is not a Milestone 2.1 collector borrower;
4. capture `_paper_screening_audit_report_ready` and require it to be exactly
   `True`;
5. capture the current audit collector reference and require its public state
   to be `FINALIZED`;
6. only now access and strictly parse the already materialized
   `cfg.paper_screening_audit_report_enabled` value;
7. if parsed `False`, bind plan `None`, do not import the formatter, do not call
   the getter, and preserve original report and WebSocket behavior
   byte-for-byte;
8. if parsed `True`, call the getter exactly once, require that it returns the
   captured collector's immutable snapshot, normalize and capture language,
   and construct the Section 3 plan; and
9. any true-path getter or association failure is fatal before report LLM work.

Before all first five conditions pass, runtime code must not access or parse
the new configuration attribute. This restriction concerns run-time property
access; it does not prohibit the existing `Config` constructor from
materializing its configured value.

Quick, Deep, Detailed, Hybrid, Local, subtopic, borrower, out-of-scope source,
and out-of-scope report-type paths do not use the switch. Once the plan is
captured, no post-LLM state recheck is permitted.

## 6. Pure formatter contract

Add a synchronous pure function in
`gpt_researcher/screening/audit_markdown.py` with the frozen public-in-module
interface:

```python
def format_paper_screening_audit_markdown(
    snapshot: PaperScreeningAuditSnapshot,
    language: str,
) -> str:
    ...
```

The function is not re-exported from `gpt_researcher.screening.__init__`.

Contract:

- `snapshot` must be exactly a valid `PaperScreeningAuditSnapshot` obtained
  from the finalized integration path. A wrong object or invalid compound
  relationship is fatal.
- Finalization provenance is enforced by the caller's state check and getter;
  the immutable snapshot model itself does not contain collector state.
- `language` must be exactly the normalized string `zh` or `en` captured by the
  pre-LLM plan. Every other value or type is fatal.
- The function performs no I/O, logging, environment read, network call,
  mutation, callback, or import of report/Provider code.
- It never modifies or reconstructs the snapshot.
- It uses only snapshot tuples and explicit frozen order fields. It must not
  rely on incidental dict or set iteration order.
- It outputs every applicable record. The MVP has no count truncation,
  pagination, hidden remainder, or sampling.
- Identical snapshot values and identical language values produce byte-for-byte
  identical output.

For lookup, construct only local ephemeral maps keyed by frozen compound
identity:

```text
(web_pass_id, occurrence_id)
(web_pass_id, retrieval_request_id)
(web_pass_id, group_id)
```

A missing, duplicate, cross-pass, or inconsistent lookup is fatal. Bare local
IDs must never be used as snapshot-wide keys.

## 7. Frozen appendix structure

Use fixed Markdown headings and bullet lists. Markdown tables and raw HTML are
forbidden.

### 7.0 Sole byte templates

All formatter output uses LF (`U+000A`) only. CR and CRLF are forbidden in
formatter-authored output. `appendix` has no leading whitespace or separator,
starts with its `##` heading, and ends with exactly one LF. Every Markdown
heading, including every `##`, `###`, and `####` heading, is followed by exactly
one empty line: the heading's terminating LF is followed by exactly one more LF,
so the bytes between the heading and the next nonempty line are exactly `\n\n`.
A heading must never be followed directly by a bullet or body text and must
never be followed by two or more empty lines. There is exactly one empty line
between top-level sections and between adjacent record blocks. Every bullet
begins with exactly `- `; every record heading begins with exactly `#### `;
indentation is forbidden.

In the templates below, `{{TOKEN}}` is specification metasyntax and is never
emitted. Each scalar token is replaced by its exact field-specific Section 9
value. `{{NORMALIZED_URL}}` is governed only by Section 9.3 and is never passed
through the Section 9.2 text sanitizer.
`{{ROUTED_BLOCKS}}`, `{{PLANNING_BLOCKS}}`, `{{EXCLUDED_BLOCKS}}`, and
`{{WARNING_BLOCKS}}` are replaced by either their exact empty-state line or by
record blocks joined with exactly one blank line. No implementation may add,
remove, reorder, translate, or punctuate another static byte.

The exact English appendix template is:

```text
## Paper Screening Process

### Scope

This appendix records paper-screening and evidence-routing results. A paper entering the evidence stage does not mean it was cited in the final report. The current system does not reliably track which candidate papers the LLM ultimately cited.

### Policy Snapshot

- Minimum year: {{MINIMUM_YEAR}}
- Maximum year: {{MAXIMUM_YEAR}}
- Unknown-year policy enabled: {{ALLOW_UNKNOWN_YEAR}}
- Allowed paper types: {{ALLOWED_PAPER_TYPES}}
- Unknown-paper-type policy enabled: {{ALLOW_UNKNOWN_TYPE}}
- Topic relevance enabled: {{TOPIC_RELEVANCE_ENABLED}}

### Summary

- Total occurrences: {{TOTAL_OCCURRENCES}}
- Duplicate occurrences: {{DUPLICATE_OCCURRENCES}}
- Deterministically included occurrences: {{DETERMINISTICALLY_INCLUDED_OCCURRENCES}}
- Deterministically excluded occurrences: {{DETERMINISTICALLY_EXCLUDED_OCCURRENCES}}
- Occurrences excluded by year rules: {{EXCLUDED_BY_YEAR_OCCURRENCES}}
- Occurrences excluded by paper-type rules: {{EXCLUDED_BY_TYPE_OCCURRENCES}}
- Occurrences excluded as duplicates: {{EXCLUDED_AS_DUPLICATE_OCCURRENCES}}
- Total groups: {{TOTAL_GROUPS}}
- Groups with a canonical paper: {{GROUPS_WITH_CANONICAL}}
- Deterministically included groups: {{DETERMINISTICALLY_INCLUDED_GROUPS}}
- Topic-relevant groups: {{TOPIC_RELEVANT_GROUPS}}
- Topic-irrelevant groups: {{TOPIC_IRRELEVANT_GROUPS}}
- Topic-uncertain groups: {{TOPIC_UNCERTAIN_GROUPS}}
- Screening-included groups: {{SCREENING_INCLUDED_GROUPS}}
- Routed groups: {{ROUTED_GROUPS}}
- Planning-only groups: {{PLANNING_ONLY_GROUPS}}
- Total requests: {{TOTAL_REQUESTS}}
- Requests without academic occurrences: {{REQUESTS_WITHOUT_ACADEMIC_OCCURRENCES}}
- Provider warnings: {{PROVIDER_WARNING_COUNT}}
- Academic candidate state: {{ACADEMIC_CANDIDATE_STATE}}

### Routed Canonical Papers

{{ROUTED_BLOCKS}}

### Planning-only Canonical Papers

{{PLANNING_BLOCKS}}

### Excluded Occurrences

{{EXCLUDED_BLOCKS}}

### Provider Warnings

{{WARNING_BLOCKS}}
```

The exact Chinese appendix template is:

```text
## 论文筛选过程

### 范围说明

本附录记录论文筛选和证据路由结果。论文进入证据阶段并不表示其已在最终报告中被引用。当前系统无法可靠追踪大语言模型最终引用了哪些候选论文。

### 筛选策略

- 最小年份：{{MINIMUM_YEAR}}
- 最大年份：{{MAXIMUM_YEAR}}
- 允许未知年份：{{ALLOW_UNKNOWN_YEAR}}
- 允许的论文类型：{{ALLOWED_PAPER_TYPES}}
- 允许未知论文类型：{{ALLOW_UNKNOWN_TYPE}}
- 启用主题相关性判断：{{TOPIC_RELEVANCE_ENABLED}}

### 汇总

- 论文出现总数：{{TOTAL_OCCURRENCES}}
- 重复论文出现数：{{DUPLICATE_OCCURRENCES}}
- 确定性筛选纳入的论文出现数：{{DETERMINISTICALLY_INCLUDED_OCCURRENCES}}
- 确定性筛选排除的论文出现数：{{DETERMINISTICALLY_EXCLUDED_OCCURRENCES}}
- 因年份规则排除的论文出现数：{{EXCLUDED_BY_YEAR_OCCURRENCES}}
- 因论文类型规则排除的论文出现数：{{EXCLUDED_BY_TYPE_OCCURRENCES}}
- 作为重复项排除的论文出现数：{{EXCLUDED_AS_DUPLICATE_OCCURRENCES}}
- 论文组总数：{{TOTAL_GROUPS}}
- 含规范论文的论文组数：{{GROUPS_WITH_CANONICAL}}
- 确定性筛选纳入的论文组数：{{DETERMINISTICALLY_INCLUDED_GROUPS}}
- 主题相关的论文组数：{{TOPIC_RELEVANT_GROUPS}}
- 主题不相关的论文组数：{{TOPIC_IRRELEVANT_GROUPS}}
- 主题相关性不确定的论文组数：{{TOPIC_UNCERTAIN_GROUPS}}
- 最终筛选纳入的论文组数：{{SCREENING_INCLUDED_GROUPS}}
- 已路由的论文组数：{{ROUTED_GROUPS}}
- 仅规划阶段的论文组数：{{PLANNING_ONLY_GROUPS}}
- 检索请求总数：{{TOTAL_REQUESTS}}
- 无学术论文出现的检索请求数：{{REQUESTS_WITHOUT_ACADEMIC_OCCURRENCES}}
- 数据源警告数：{{PROVIDER_WARNING_COUNT}}
- 学术候选状态：{{ACADEMIC_CANDIDATE_STATE}}

### 进入证据阶段的规范论文

{{ROUTED_BLOCKS}}

### 仅规划阶段论文

{{PLANNING_BLOCKS}}

### 排除记录

{{EXCLUDED_BLOCKS}}

### 数据源警告

{{WARNING_BLOCKS}}
```

Each English routed or planning record with a safe HTTP/HTTPS URL uses this
exact block:

```text
#### Paper Record {{RECORD_ORDER}}

- Title: {{TITLE}}
- Year: {{YEAR}}
- Paper type: {{PAPER_TYPE}}
- Venue: {{VENUE}}
- DOI: {{DOI}}
- Source: {{SOURCE}}
- Paper URL: [Open paper]({{NORMALIZED_URL}})
- Topic verdict: {{TOPIC_VERDICT}}
- Topic reason code: {{TOPIC_REASON_CODE}}
- Topic rationale: {{TOPIC_RATIONALE}}
- Screening included: {{SCREENING_INCLUDED}}
- Routing status: {{ROUTING_STATUS}}
- Routed request count: {{ROUTED_REQUEST_COUNT}}
- Planning-only occurrence: {{PLANNING_ONLY}}
```

Each Chinese routed or planning record with a safe HTTP/HTTPS URL uses this
exact block:

```text
#### 论文记录 {{RECORD_ORDER}}

- 标题：{{TITLE}}
- 年份：{{YEAR}}
- 论文类型：{{PAPER_TYPE}}
- 期刊或会议：{{VENUE}}
- DOI：{{DOI}}
- 来源：{{SOURCE}}
- 论文链接：[打开论文]({{NORMALIZED_URL}})
- 主题判断：{{TOPIC_VERDICT}}
- 主题原因代码：{{TOPIC_REASON_CODE}}
- 主题判断理由：{{TOPIC_RATIONALE}}
- 筛选纳入：{{SCREENING_INCLUDED}}
- 路由状态：{{ROUTING_STATUS}}
- 路由请求数：{{ROUTED_REQUEST_COUNT}}
- 仅规划阶段出现：{{PLANNING_ONLY}}
```

Each English excluded-occurrence record with a safe HTTP/HTTPS URL uses this
exact block:

```text
#### Excluded Occurrence {{RECORD_ORDER}}

- Title: {{TITLE}}
- Year: {{YEAR}}
- Paper type: {{PAPER_TYPE}}
- Venue: {{VENUE}}
- DOI: {{DOI}}
- Source: {{SOURCE}}
- Paper URL: [Open paper]({{NORMALIZED_URL}})
- Canonical occurrence: {{IS_CANONICAL}}
- Duplicate group ID: {{DUPLICATE_GROUP_ID}}
- Final exclusion reasons: {{FINAL_EXCLUSION_REASONS}}
- Topic verdict: {{TOPIC_VERDICT}}
- Topic reason code: {{TOPIC_REASON_CODE}}
- Topic rationale: {{TOPIC_RATIONALE}}
```

Each Chinese excluded-occurrence record with a safe HTTP/HTTPS URL uses this
exact block:

```text
#### 排除记录 {{RECORD_ORDER}}

- 标题：{{TITLE}}
- 年份：{{YEAR}}
- 论文类型：{{PAPER_TYPE}}
- 期刊或会议：{{VENUE}}
- DOI：{{DOI}}
- 来源：{{SOURCE}}
- 论文链接：[打开论文]({{NORMALIZED_URL}})
- 是否为规范论文出现：{{IS_CANONICAL}}
- 重复组 ID：{{DUPLICATE_GROUP_ID}}
- 最终排除原因：{{FINAL_EXCLUSION_REASONS}}
- 主题判断：{{TOPIC_VERDICT}}
- 主题原因代码：{{TOPIC_REASON_CODE}}
- 主题判断理由：{{TOPIC_RATIONALE}}
```

Each English Provider-warning record uses this exact block:

```text
#### Provider Warning {{RECORD_ORDER}}

- Web pass ID: {{WEB_PASS_ID}}
- Retrieval request ID: {{RETRIEVAL_REQUEST_ID}}
- Retriever index: {{RETRIEVER_INDEX}}
- Source identifier: {{SOURCE_IDENTIFIER}}
- Warning category: {{WARNING_CATEGORY}}
```

Each Chinese Provider-warning record uses this exact block:

```text
#### 数据源警告 {{RECORD_ORDER}}

- Web 阶段 ID：{{WEB_PASS_ID}}
- 检索请求 ID：{{RETRIEVAL_REQUEST_ID}}
- Retriever 序号：{{RETRIEVER_INDEX}}
- 来源标识：{{SOURCE_IDENTIFIER}}
- 警告类别：{{WARNING_CATEGORY}}
```

For a missing or non-HTTP/HTTPS scheme, replace only the safe-link line in the
applicable complete paper-record block with exactly one of these literal lines:

```text
- Paper URL: {{SANITIZED_HREF}}
```

```text
- 论文链接：{{SANITIZED_HREF}}
```

No other byte in the selected record block changes. `{{NORMALIZED_URL}}` occurs
exactly once in every safe-link record, and `{{SANITIZED_HREF}}` occurs exactly
once in every non-link record.

For two adjacent records, the previous record's final bullet is followed by
exactly two LF bytes, the next record starts immediately with `####`, and that
next heading is followed by exactly two LF bytes before its first bullet. Empty
sections retain their `###` heading, exactly one empty line, and then the exact
empty-state bullet.

The exact conditional static values are:

| Condition | English bytes | Chinese bytes |
|---|---|---|
| `total_occurrences == 0` | `No academic candidates or occurrences.` | `没有学术候选论文或论文出现记录。` |
| `total_occurrences > 0` | `Academic candidates were collected.` | `已收集学术候选论文。` |
| no routed record | `- No routed canonical papers.` | `- 没有进入证据阶段的规范论文。` |
| no planning-only record | `- No planning-only canonical papers.` | `- 没有仅规划阶段的规范论文。` |
| no excluded record | `- No excluded occurrences.` | `- 没有排除记录。` |
| no warning record | `- No Provider warnings.` | `- 没有数据源警告。` |
| topic fields when topic relevance was not run | `Not run` | `未运行` |
| boolean true | `Enabled` | `启用` |
| boolean false | `Disabled` | `禁用` |
| scalar `None` | `N/A` | `N/A` |
| `allowed_paper_types is None` | `All paper types` | `全部论文类型` |

The table above is normative template data, not a Markdown table emitted by the
formatter. Record-order values are one-based ASCII decimal integers within
their own section and are contiguous after section filtering. All enum values
are their exact `.value` bytes. The fixed link labels are exactly `Open paper`
and `打开论文`.

The first heading, all seven section headings, and their order are solely the
literal bytes in the selected complete template above. No alternate heading or
translation is permitted.

Dynamic paper titles or other untrusted values are never headings. Record
headings use exactly the fixed block headings in Section 7.0.

### 7.1 Scope statement

Emit the exact one-line Scope paragraph from the selected Section 7.0 template
without any additional sentence.

The appendix must not use the terms “final cited papers”, “references used”, or
any equivalent unsupported claim for routed or included candidates.

### 7.2 Policy snapshot

The policy section is emitted only through the six exact template lines in
Section 7.0.

Use only the exact conditional static values from Section 7.0 and preserve the
tuple order of allowed paper types.

### 7.3 Summary

Display every frozen `PaperScreeningAuditSummary` field, with localized labels
whose units say occurrence, group, request, or warning as applicable, in this
exact schema order:

1. `total_occurrences`;
2. `duplicate_occurrences`;
3. `deterministically_included_occurrences`;
4. `deterministically_excluded_occurrences`;
5. `excluded_by_year_occurrences`;
6. `excluded_by_type_occurrences`;
7. `excluded_as_duplicate_occurrences`;
8. `total_groups`;
9. `groups_with_canonical`;
10. `deterministically_included_groups`;
11. `topic_relevant_groups`;
12. `topic_irrelevant_groups`;
13. `topic_uncertain_groups`;
14. `screening_included_groups`;
15. `routed_groups`;
16. `planning_only_groups`;
17. `total_requests`;
18. `requests_without_academic_occurrences`; and
19. `provider_warning_count`.

Use the values already validated in `snapshot.summary`. Do not derive a new
summary that disagrees with the snapshot. In particular, year, type, and
duplicate exclusion counts can overlap and must not be added together or
presented as a total excluded count.

### 7.4 Routed canonical papers

Iterate `group_audits` in `group_order` and include each group whose canonical
is screening-included and whose `routed_request_ids` is nonempty. Resolve the
canonical occurrence through `(web_pass_id, canonical_occurrence_id)`.

The routed section is emitted only through the exact record block or exact empty
state in Section 7.0.

The item is one canonical group record even when the canonical is routed to
multiple requests. Do not repeat it once per route.

### 7.5 Planning-only canonical papers

Iterate `group_audits` in `group_order` and include each group that is
screening-included, has an empty routed-request tuple, and is a validated
planning-only group. Resolve it through the same compound key and emit the
exact planning record block from Section 7.0.

A screening-included non-planning group with no route violates the 2.3A
invariants and is fatal; it must not be silently moved into this section.

### 7.6 Excluded occurrences

Iterate `occurrence_entries` in `audit_order` and include every occurrence for
which `screening_included is False`.

The excluded section is emitted only through the exact record block or exact
empty state in Section 7.0.

This occurrence-level section preserves duplicate and multi-rule evidence.
Topic `uncertain` is not an exclusion reason and must never be presented as
one.

### 7.7 Provider warnings

Iterate requests in `request_order`, then each request's already validated
warning tuple order. The warning section is emitted only through the exact
record block or exact empty state in Section 7.0.

Do not display `retrieval_query`, exception text, Provider messages, response
data, headers, parameters, or candidate content. Warnings do not create a paper
record or exclusion reason.

### 7.8 Frozen empty states

The exact conditional static values in Section 7.0 cover:

- no academic candidates/occurrences;
- no routed canonical paper;
- no planning-only canonical paper;
- no excluded occurrence;
- no Provider warning; and
- topic relevance disabled.

An empty section remains present with its fixed empty-state text. Do not remove
or reorder sections based on content.

## 8. Language selection

During pre-LLM plan construction, require the configured language to be a
string and normalize it with `strip().casefold()`.

Chinese labels are used only for:

```text
chinese
zh
zh-cn
zh-hans
```

The plan stores exactly `zh` for those four values and exactly `en` for every
other string. The formatter receives only this normalized two-value mode. It
does not call an LLM, translation service, locale service, or environment
setting. Complete localization beyond this deterministic Chinese/English split
is deferred.

This selection changes only appendix labels. It does not reinterpret or alter
the configured language passed to the existing report LLM.

## 9. Markdown and URL safety

All dynamic values, including title, venue, DOI, source, identifier, reason,
rationale, and URL, are untrusted data.

### 9.1 Exact scalar conversion

The formatter may convert dynamic model values only through these frozen
type-specific rules before common escaping:

- `None` becomes the fixed localized `N/A` token; both language modes use the
  exact visible token `N/A`.
- A required string must already be a `str`; after its model-approved
  normalization, a blank value is fatal. Optional non-`None` strings use the
  same strict rule.
- A boolean is accepted only when `type(value) is bool` and becomes exactly
  `Enabled`/`Disabled` in English or `启用`/`禁用` in Chinese.
- An integer is accepted only when `type(value) is int` and is rendered as
  unsigned or signed ASCII decimal digits without locale separators.
- An Enum is accepted only as the expected frozen enum type; its `.value` must
  be a nonblank `str`, and that string is then escaped. The formatter never
  formats an Enum with `str()` or `repr()`.
- A tuple is accepted only where the frozen field schema expects a tuple. Its
  already ordered scalar members are converted independently and joined with
  exactly comma plus one ASCII space: `, `. Lists, sets, mappings, generators,
  and arbitrary iterables are fatal.
- `allowed_paper_types is None` uses `All paper types` / `全部论文类型`;
  an actual allowed-types tuple must be nonempty and uses the fixed tuple join.
- An excluded occurrence's reason tuple must be nonempty and uses the fixed
  tuple join. An empty reason tuple is fatal under the 2.3A invariant.
- Empty warning, routed-paper, planning-only-paper, and excluded-occurrence
  tuples are represented only by their Section 7.8 fixed localized empty-state
  sentence, never by generic tuple stringification.

No formatter path may call generic `str()`, `repr()`, `dict()`, or JSON
serialization on a model, tuple, list, set, mapping, exception, or unknown
object to obtain display text.

### 9.2 Dynamic text escaping

After the exact scalar conversion above, apply one common deterministic
escaping function to every dynamic display-text value except
`{{NORMALIZED_URL}}`, which is handled only by Section 9.3, in exactly this
order:

1. require a `str`;
2. replace every CRLF pair with one LF;
3. replace every remaining CR with one LF;
4. replace every original backslash (`0x5C`) with two backslashes;
5. replace every LF with exactly the two ASCII bytes `0x5C 0x6E`, producing a
   visible literal `\n` and never a real record boundary;
6. replace `&` with `&amp;`, then `<` with `&lt;`, then `>` with `&gt;`;
7. in one left-to-right pass, prefix a backslash to every literal character in
   this complete frozen Markdown set:

   ```text
   ` * _ { } [ ] ( ) # + - . ! | >
   ```

8. do not reprocess any backslash or entity introduced by a prior step;
9. preserve every other Unicode code point and meaningful non-newline
   whitespace; and
10. never strip, truncate, summarize, or silently discard valid display data.

The `>` member remains part of the frozen Markdown set even though a raw `>`
from the input was already converted to `&gt;`; introduced entity characters are
not processed a second time. Formatter-authored fixed Markdown syntax is never
passed through this dynamic-value sanitizer.

Dynamic values are emitted only after a fixed label inside a bullet. They are
never used as a heading or link label. The formatter emits no raw HTML.

DOI is escaped plain text only. It is never concatenated into a URL.

### 9.3 Paper URL handling

The link label is exactly the fixed localized `Open paper` / `打开论文`; it never
contains the paper title or another dynamic value.

Classify and format `href` through this sole ordered algorithm, using only
`urllib.parse`, `urllib.parse.quote`, `ipaddress`, and Python's built-in IDNA
codec and performing no I/O:

1. Require `href` to be exactly a `str`. Before parsing, reject any actual
   character from `U+0000` through `U+001F` or `U+007F`, including CR and LF,
   as fatal.
2. Call `urllib.parse.urlsplit(href)`. Every exception is fatal.
3. Normalize `split.scheme` with `casefold()`.
4. If the scheme is missing or is not exactly `http` or `https`, do not further
   validate authority, host, or port; create no Markdown link; and emit only
   the original `href` after the Section 9.2 text sanitizer. `javascript`,
   `data`, and `file` never produce links.
5. For `http`/`https`, validate in this exact order:
   - `split.netloc` is nonempty;
   - `split.netloc` contains no backslash;
   - `split.username is None` and `split.password is None`;
   - `split.hostname` can be read and is nonempty.
   Any failed condition or property-access exception is fatal.
6. Determine explicit-port presence only from the raw `split.netloc` grammar:
   - a bracketed IPv6 netloc is allowed only as `[host]` or `[host]:port_text`;
   - when `]` is the final character, no explicit port exists;
   - when the text after `]` begins with `:`, an explicit port exists,
     `port_text` is everything after that colon, and `port_text` must be nonempty
     and contain only ASCII digits;
   - `[host]:` is fatal, and every other structure after `]` is fatal;
   - for a non-bracketed netloc, no `:` means no explicit port;
   - for a non-bracketed netloc, exactly one `:` means an explicit port,
     `port_text` is everything after that colon, and `port_text` must be nonempty
     and contain only ASCII digits;
   - `host:` is fatal; and
   - more than one `:` in a non-bracketed netloc is fatal and cannot fall back
     to the IDNA branch.
7. When an explicit port exists, read `split.port`; every parsing or property
   exception is fatal. Require `1 <= split.port <= 65535` and serialize the port
   only as `str(split.port)`, thereby removing all original leading zeroes. Port
   0 and every value above 65535 are fatal. Preserve the presence of a legal
   explicit default port: HTTP `:80` remains `:80` and HTTPS `:443` remains
   `:443`. When no explicit port exists, add no default port and emit no trailing
   colon. The frozen examples are:

   ```text
   :00080 -> :80
   :001 -> :1
   :0 -> fatal
   :65535 -> :65535
   :65536 -> fatal
   host: -> fatal
   [::1]: -> fatal
   ```

8. Select exactly one host branch in this priority order:
   - if the authority's host is enclosed in square brackets, use only the IPv6
     branch;
   - otherwise, if the host contains only ASCII digits and `.`, use only the
     IPv4 branch; and
   - otherwise use only the IDNA-domain branch.
9. In the IPv6 branch:
   - the input host must be enclosed in square brackets;
   - reject every zone identifier, including `%zone` and `%25zone`;
   - reject IPvFuture;
   - parse only with `ipaddress.IPv6Address`;
   - any parse failure is fatal;
   - render `compressed.lower()`; and
   - enclose the normalized result in exactly one pair of square brackets in
     the reconstructed authority.
   A non-bracketed IPv6 address is fatal and cannot fall back to IDNA.
10. In the IPv4 branch, parse only with `ipaddress.IPv4Address`. A digits-and-dot
   host that does not parse is fatal and cannot fall back to IDNA. Render the
   normalized decimal address returned by `IPv4Address`.
11. In the IDNA-domain branch:
    - split the host on `.`;
    - reject every empty label, including a leading dot, trailing dot, or
      consecutive dots;
    - encode every label with Python's built-in IDNA codec;
    - decode the encoded bytes with strict ASCII decoding;
    - any encoding or strict ASCII decoding failure is fatal and cannot fall
      back to the Unicode host;
    - lowercase every encoded ASCII label;
    - require every encoded label byte length to be from 1 through 63;
    - after lowercasing and length validation, require the complete encoded label
      to match this exact ASCII regular expression:

      ```python
      r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
      ```

    - a label that does not match is fatal and cannot be percent-encoded,
      sanitized, emitted as a link target, or fall back to the original Unicode
      label; and
    - require the complete dot-joined ASCII host to be at most 253 bytes.
12. Independently scan path, query, and fragment. Every `%` must be followed by
    exactly two hexadecimal characters or the URL is fatal. Normalize the two
    hexadecimal characters to uppercase. Reject `%00` through `%1F` and `%7F`,
    case-insensitively. Preserve every other valid `%HH` escape without
    double-encoding it.
13. Use these exact safe-string constants; the displayed backslash-free source
    values are normative:

    ```python
    PATH_SAFE = "/:@!$&'+,;=-.*~%"
    QUERY_SAFE = "=&?/:@!$'+,;-.*~%"
    FRAGMENT_SAFE = "/?:@!$&'+,;=-._~%"
    ```

14. Call `urllib.parse.quote(component, safe=..., encoding="utf-8",
    errors="strict")` separately for path, query, and fragment with their exact
    matching constant.
15. Reconstruct `normalized_url` only with `urllib.parse.urlunsplit()` from the
    normalized scheme, normalized authority, encoded path, encoded query, and
    encoded fragment.
16. Emit exactly `[Open paper]({normalized_url})` in English or exactly
    `[打开论文]({normalized_url})` in Chinese, as already written literally in
    every safe-link record template in Section 7.0. The brackets, parentheses,
    and label are formatter-authored fixed bytes. Emit no whitespace around the
    URL, angle brackets, title attribute, bare URL, reference-style link, HTML
    `<a>` element, or backtick link. Never use a paper title as the label.
    `normalized_url` is not passed through the Section 9.2 text sanitizer and
    occurs exactly once in the emitted link.

A malformed supported `http`/`https` URL is always fatal and never degrades to
plain text. An absent or unsupported scheme always follows step 4 and emits the
sanitized original `href` at the same record-field position, without a Markdown
link. The formatter never performs a DNS lookup, HTTP request, or other network
validation.

### 9.4 Data minimization

The appendix must not contain or log:

- `retrieval_query` (it remains permitted inside the 2.3A snapshot and getter,
  but this appendix does not display or otherwise copy it);
- candidate abstract or formatted body;
- raw LLM or Provider response, JSON wrapper, or original result object;
- LLM system/user prompt or research-topic payload;
- request parameters, request headers, or serialized request;
- API keys, environment values, or credentials;
- exception objects, exception text, repr, or traceback; or
- ordinary Web, scraped, MCP, or compressed content.

The only LLM-derived text permitted is the already validated, stripped,
at-most-500-character topic rationale present in the finalized snapshot. It is
escaped like every other dynamic value. The formatter neither logs its input
nor its output.

## 10. WebSocket, return value, export, and persistence behavior

- The heading-first `appendix` used as the final operand of the Section 3
  concatenation and the WebSocket `report` message's `output` value are the
  exact same string value. The return-only separator is not part of that value.
- Emit at most one appendix message for each `write_report()` invocation.
- “A WebSocket is present” means exactly `self.websocket is not None`. Do not
  inspect a wrapper's internal raw-WebSocket field.
- When `self.websocket is None`, do not call `send_json`; the returned
  `final_report` still contains the appendix.
- When `self.websocket is not None`, send only through the exact direct call frozen
  in Section 3. Do not use `stream_output()` because it actively logs `output`,
  and do not modify `gpt_researcher/actions/utils.py`.
- A `CustomLogsHandler` object is called even when its internal raw WebSocket is
  `None`, so its existing transport-JSON behavior remains active.
- Event order is: all LLM `report` chunks; the one appendix `report` chunk;
  existing `report_completed` event; `write_report()` return; then existing
  backend file/path handling. No later full-report `report` chunk is added.
- With no WebSocket, append and return the same completed report without
  attempting a send.
- Do not add, rename, or remove a WebSocket field or message type.
- The existing Markdown, PDF, and DOCX paths receive the completed returned
  report string without exporter changes.
- Existing report history may persist the appendix as part of ordinary final
  report text. This is not a new structured snapshot field, audit database, or
  audit serialization API.
- `CustomLogsHandler.send_json()` may store the direct WebSocket product message
  in its existing transport JSON/content record. This is approved product
  transport/persistence, not formatter diagnostic logging and not a new
  structured audit snapshot.
- The formatter must not itself persist, log, stream, or serialize its input or
  output outside its returned string.

The complete awaited `send_json()` call is one at-most-once side effect. Any
exception from any part of that await is fatal, including an underlying
WebSocket send failure, a later transport-JSON read/write failure, a wrapper or
callback failure, and `asyncio.CancelledError`. Re-raise the original exception
unchanged: do not wrap, replace, suppress, downgrade, or retry it. The method
must not return the locally constructed `final_report` as a success.

If the underlying client received the appendix and a later transport-JSON write
fails, `write_report()` still fails. The client may already have received the
appendix exactly once; this accepted at-most-once partial side effect is not
rolled back and must never trigger a second appendix send. The report-write
guard clears in `finally`, while readiness and the finalized audit remain
available for a later independent `write_report()` retry.

The safety boundary distinguishes diagnostic logs from approved product
output:

- Forbidden diagnostic behavior includes any new logger call containing a
  snapshot, appendix, rationale, query, dynamic paper field, raw response, or
  formatter output, and any exception log newly containing such data.
- Approved product output includes the one WebSocket report chunk, the returned
  final report string, existing `ReportStore`/history storage of that report,
  existing CustomLogsHandler transport JSON, and existing Markdown/PDF/DOCX
  exports.
- When enabled, an escaped validated topic rationale becomes intentional report
  text and may consequently be present in those approved report products. This
  is the explicit 2.3B presentation behavior and supersedes any claim that the
  rationale is never persisted.
- A raw LLM response or JSON wrapper, prompt, raw abstract/body, Provider
  response/request/header, API key, exception text, and retrieval query remain
  forbidden from the appendix, diagnostics, and any new persistence path.

Because the report body is already streamed while the LLM generates it, a
later getter, formatter, or appendix-send failure cannot retract body chunks
already seen by a client. Such a failure prevents successful completion and
normal export-path publication but does not erase already transmitted text.

## 11. Failure and cancellation semantics

The following are fatal and cause `write_report()` to fail:

- a report write starts while a research run is active;
- a second overlapping report write starts on the same instance;
- readiness and configuration are both true but the getter is unavailable;
- the collector state and getter result disagree;
- the formatter receives the wrong snapshot type or an invalid snapshot;
- a compound occurrence, request, or group lookup is missing, duplicated,
  cross-pass, or inconsistent;
- an unexpected dynamic value type is encountered;
- a supported-scheme URL is malformed or cannot be safely normalized;
- Markdown safety formatting fails; or
- any part of the complete additional `send_json()` await fails.

Do not silently omit a field, record, section, or appendix after an enabled
invariant failure. Do not return the unmodified report as a fallback.

Formatter and URL validation occur only after the report LLM has completed and
the formatter is called exactly once. If either fails after LLM report chunks
have already been sent:

- previously sent LLM chunks are not rolled back;
- no appendix chunk is sent;
- no successful `final_report` is returned;
- no successful `report_completed` event is sent;
- the formatter is not called again;
- report-write active state clears in `finally`; and
- readiness and the finalized audit remain available for a later independent
  retry.

Every original exception from formatter, URL handling, WebSocket, transport
JSON, or the surrounding report operation propagates unchanged, and
report-write active state is cleared in `finally`. In particular, an original
`asyncio.CancelledError` remains the same object. Existing ordinary disconnect
behavior is preserved; Milestone 2.3B adds no retry or alternate channel.

The audit snapshot was finalized by the earlier successful research run. A
later report-generation, getter, formatter, WebSocket, or cancellation failure:

- does not abort, clear, replace, mutate, or roll back that snapshot;
- does not clear the readiness flag merely because `write_report()` failed;
  and
- does not alter candidate, Workspace, screening, or routing state.

## 12. Compatibility requirements

When the appendix feature is disabled or the gate is out of scope:

- the returned report is byte-for-byte unchanged;
- the number, order, fields, and content of WebSocket messages are unchanged;
- existing report generation, fallback, logging, cost, image, References, and
  export behavior is unchanged;
- no formatter module is imported at runtime;
- the audit getter is not called;
- no new configuration property is accessed before the frozen gate; and
- no new external or model call occurs.

For a target-scope top-level owner, the same-instance report/research guards
apply regardless of whether the appendix configuration ultimately parses to
false. Guard rejection is synchronous, occurs before controlled run-state
mutation or external work, and does not change the value-equivalent normal
non-overlapping old path. Borrower overlap and external direct attribute
mutation retain existing behavior and are outside this guarantee.

When enabled:

- the original report is an exact prefix of the completed report;
- References and citation text inside that prefix are byte-for-byte unchanged;
- exactly one separator and appendix are added per newly generated report
  string;
- repeated `write_report()` calls independently receive exactly one appendix
  based on the same immutable snapshot; and
- no existing `{title, href, body}`, `BODY_IS_PREFETCHED_CONTENT`, candidate,
  collector, screening, route, or ContextCompressor contract changes.

## 13. Test-first implementation requirements

Implementation must be test-first:

1. add isolated formatter and integration tests before business code;
2. run them against the old implementation and record failures caused only by
   the missing 2.3B formatter, configuration, readiness state, or report hook;
3. do not classify dependency, import, fixture, or mock errors as expected red;
4. implement the smallest behavior inside Section 14; and
5. run the full relevant candidate, audit, screening, report, WebSocket, and
   export regression set.

All Provider, Retriever, LLM, HTTP, socket, scraper, compressor, MCP, and
external export boundaries must be mocked or fail-fast. No real service call
is permitted.

## 14. Frozen seven-file implementation boundary

After explicit approval, implementation may add only:

- `gpt_researcher/screening/audit_markdown.py`;
- `tests/test_paper_screening_audit_markdown.py`; and
- `tests/test_paper_screening_audit_report_pipeline.py`.

It may modify only:

- `.env.example`;
- `gpt_researcher/config/variables/base.py`;
- `gpt_researcher/config/variables/default.py`; and
- `gpt_researcher/agent.py`.

Do not modify:

- any approved specification;
- `BasicReport`;
- backend server, report store, or WebSocket manager code;
- frontend code;
- `gpt_researcher/skills/writer.py`;
- `gpt_researcher/actions/report_generation.py`;
- Prompt or PromptFamily code;
- `gpt_researcher/actions/markdown_processing.py`;
- Markdown, PDF, or DOCX exporters;
- `gpt_researcher/screening/audit.py`;
- `gpt_researcher/screening/__init__.py`;
- candidate, collection, deterministic, Workspace, or topic-relevance code;
- Provider or Retriever code;
- dependencies or lock files; or
- any other file.

A requirement for an eighth file must stop implementation and trigger a
revised Draft, review, and explicit approval.

## 15. Fully mocked test matrix

The approved implementation suite must cover at least the following.

### 15.1 Readiness lifecycle

- report writing rejects an active research run before its first `await` with
  the exact frozen message and changes no state;
- overlapping `write_report()` calls fail synchronously with the exact frozen
  message, while sequential report writes succeed;
- owner conduct and owner Quick reject an active report write before candidate,
  readiness, audit, ordinal, context, or image mutation;
- report/research guard rejection leaves all prior state unchanged;
- report failure and the same-object `CancelledError` both clear report-write
  active state in `finally`;
- report/research mutual exclusion is per instance and does not serialize two
  independent researchers;
- every new researcher initializes readiness to `False`;
- owner conduct clears readiness only after the overlap guard succeeds;
- a failed overlap does not replace readiness;
- eligible successful conduct sets readiness to `True` only after audit commit;
- commit and readiness publication contain no intervening `await`;
- disabled, out-of-scope, binding-failed, capability-failed, failed, and
  cancelled conduct runs leave readiness `False`;
- a new failed conduct run does not expose the old appendix;
- accepted owner Quick Search sets readiness `False` without clearing the old
  finalized audit snapshot;
- a failed Quick overlap does not replace readiness;
- `conduct -> quick -> write_report` does not append an old audit;
- borrower conduct and borrower Quick do not alter readiness;
- repeated `write_report()` calls do not consume readiness;
- two instances share no readiness state; and
- consecutive runs do not reuse the previous run's presentation eligibility.

### 15.2 Gate and compatibility

- plan construction occurs after guard acquisition and before the first await
  or `_log_event()`;
- a controlled report LLM pause followed by an attempted same-instance
  target-scope owner Quick or conduct run proves the run is rejected and the
  report retains its originally captured audit snapshot and language;
- the test does not assert that context or images were copied, tuple-converted,
  or stored in the plan: Writer inputs continue through their existing object
  and access contract;
- plan `None` stores no snapshot and imports no formatter;
- an enabled plan stores only the immutable snapshot and normalized `zh`/`en`
  language, never collector/readiness/ordinal/mutable state;
- enabled construction calls the getter exactly once before LLM generation and
  the post-LLM path never rereads getter, readiness, collector, language, or
  run ordinal;
- exact Web/`research_report`/owner/ready/finalized gating;
- every failed preconfiguration gate uses a property sentinel to prove the new
  configuration is not accessed;
- malformed configuration is ignored out of scope but fails strictly after all
  preceding gates pass;
- `false` does not import the formatter, call the getter, send an appendix, or
  change the report or WebSocket stream;
- `true` calls the finalized getter exactly once;
- `true` with an unavailable getter is fatal;
- Quick, Deep, Detailed, Hybrid, Local, subtopic, and borrower behavior is
  value-equivalent, without claiming that the new guard detects borrower/report
  overlap;
- borrower/report overlap and external direct mutation of context or images
  retain existing behavior and are outside the concurrency guarantee;
- default-disabled report, References, logging, costs, images, fallback, and
  WebSocket behavior are value-equivalent; and
- no extra LLM, Provider, Retriever, scraper, compressor, MCP, or network call
  occurs.

### 15.3 Formatter structure and data

- exact scalar conversion for `None`, required strings, exact booleans, strict
  integers, expected Enums, and strict tuples;
- bool/int/Enum conversion never uses generic stringification and lists, sets,
  mappings, arbitrary iterables, and unknown objects fail;
- tuple members retain order and join with exactly `, `;
- every field-specific empty tuple uses its frozen localized empty state and an
  excluded occurrence with empty reasons fails;
- golden-byte equality for the complete English and Chinese Section 7.0
  templates, including every heading, scope sentence, label, punctuation mark,
  ASCII space, list marker, indentation byte, LF, blank line, record block,
  empty-state line, boolean token, `N/A` token, and final trailing-LF rule;
- a complete golden-byte appendix containing at least two adjacent records
  proves `\n\n` after every heading, exactly two LF bytes between the previous
  record's final bullet and the next `####`, and exactly two LF bytes between
  that `####` heading and its first bullet;
- exact byte construction of
  `original_report + "\n\n---\n\n" + appendix`, with the heading-first appendix
  containing no separator of its own;
- `chinese`, `zh`, `zh-cn`, and `zh-hans` select Chinese after normalization;
- every other language selects English;
- policy fields and tuple order;
- all nineteen summary fields and their explicit units;
- overlapping exclusion counts are not added into an invented total;
- routed canonical groups appear once in `group_order`;
- a canonical routed to multiple requests appears once with the correct route
  count;
- planning-only canonical groups are separate and explicitly described as not
  routed to evidence;
- excluded occurrences appear in `audit_order` with complete reason tuples;
- duplicate noncanonical, deterministic exclusion, and topic-irrelevant
  canonical records are represented accurately;
- topic uncertain is shown but never becomes an exclusion reason;
- validated rationales are displayed and escaped;
- warnings follow request, Retriever-index, and category order and expose only
  approved safe fields;
- no candidates, no routed canonical, no planning-only canonical, no excluded
  occurrence, no warning, and topic-disabled empty states;
- every applicable record is emitted without truncation or pagination;
- compound IDs that collide across passes resolve correctly; and
- missing or inconsistent compound references fail rather than silently omit
  data.

### 15.4 Markdown, URL, and secret safety

- every character in the complete frozen Markdown escape set, including braces,
  period, pipe, and blockquote marker;
- exact CRLF-to-LF, remaining-CR-to-LF, original-backslash doubling, and LF-to-
  bytes-`0x5C 0x6E` order cannot create a new record;
- raw HTML tags and entities are neutralized without double-processing
  introduced entities or escape backslashes;
- dynamic titles and rationales cannot create headings, rows, links, or HTML;
- title is never used as a link label;
- valid HTTP and HTTPS DNS, IPv4, and bracketed IPv6 links use exactly
  `[Open paper]({normalized_url})` or `[打开论文]({normalized_url})`;
- strict IDNA label normalization, lowercase host, host/label length limits, and
  explicit valid port preservation;
- exact path/query/fragment safe constants and UTF-8 percent encoding;
- valid existing percent escapes are preserved with uppercase hex while a
  malformed percent escape is fatal;
- only path, query, and fragment component characters are percent-encoded under
  their respective frozen safe sets; this percent-encoding rule never applies
  to the scheme, authority, hostname, or port;
- an HTTP/HTTPS hostname must first and exclusively satisfy its IPv4, IPv6, or
  IDNA hostname branch. In an IDNA hostname, whitespace, parentheses, square
  brackets, percent signs, underscores, plus signs, backticks, and every other
  character that fails the frozen LDH regular expression are fatal;
- a failing hostname must not be repaired through `quote()`, percent-encoding,
  sanitization, character deletion, Unicode fallback, or plain-text downgrade;
  the same character classes in path, query, or fragment remain subject to the
  exact percent-encoding rules of their respective frozen safe sets;
- `javascript:`, `file:`, `data:`, missing-scheme, and other unsupported URLs
  are non-clickable escaped text;
- malformed supported URLs, invalid hosts, userinfo, control characters, and
  malformed ports fail closed;
- DOI stays plain text and cannot inject a URL;
- `retrieval_query`, raw abstract, body, Provider/LLM response, prompt, header,
  exception, traceback, and API-key sentinels are absent;
- formatter input and output are not logged;
- the original snapshot is unchanged; and
- identical input produces byte-for-byte identical Markdown.

The URL golden-byte cases must individually cover:

- a valid and an invalid IPv4 address;
- bracketed IPv6, unbracketed IPv6, `%zone`, `%25zone`, and IPvFuture;
- a Unicode IDNA domain, an empty label, an overlong label, and an overlong
  complete host;
- an authority backslash and userinfo;
- explicit ports `0`, `1`, `65535`, `65536`, and a nonnumeric port;
- `:00080` to `:80`, `:001` to `:1`, preserved explicit HTTP `:80` and HTTPS
  `:443`, absent-port omission, `host:`, and `[::1]:`;
- `%00`, `%0A`, `%1F`, `%7F`, `%`, `%A`, and `%GG`;
- normalization of every valid percent escape to uppercase without double
  encoding;
- the exact output of `PATH_SAFE`, `QUERY_SAFE`, and `FRAGMENT_SAFE`; and
- non-clickable sanitized rendering of `javascript:`, `data:`, and `file:`
  values.

The URL golden-byte suite must also prove the exact Chinese and English inline
link bytes, exact one-time occurrence of `normalized_url`, no title attribute,
no angle brackets, no bare/reference-style/HTML/backtick link, no second text-
sanitizer pass over `normalized_url`, and exact plain-text output for a missing
or non-HTTP/HTTPS scheme.

The following HTTP/HTTPS hostname cases each require a direct
`pytest.raises(...)` assertion or a semantically equivalent strict fatal
assertion around the formatter call:

- `https://foo)bar/path`;
- `https://foo(bar/path`;
- `https://foo bar/path`;
- `https://foo%20bar/path`;
- `https://foo_bar/path`;
- `https://foo+bar/path`;
- a non-IPv6 hostname containing `[`;
- a non-IPv6 hostname containing `]`;
- a hostname containing a backtick;
- a label with a leading hyphen;
- a label with a trailing hyphen;
- an empty label;
- a 64-byte encoded label; and
- a 254-byte complete encoded hostname.

Every fatal-hostname test must prove that the formatter raises, returns no
Markdown value, and generates neither `[Open paper](...)` nor
`[打开论文](...)`. A `contains` assertion, code inspection, or an indirect failure
in another URL component cannot substitute for the strict fatal assertion.

Separate success tests must use complete, exact Markdown-link byte equality for:

- a label with a legal internal hyphen;
- a single-letter label;
- a single-digit label;
- a legal Unicode IDNA hostname normalized to the fixed ASCII hostname
  `xn--fsqu00a.xn--0zwm56d`;
- a legal `xn--...` punycode label;
- a 63-byte encoded label; and
- a 253-byte complete encoded hostname.

Hostname fail-closed tests and path/query/fragment percent-encoding tests are
two independent test categories. Neither category may stand in for the other.

### 15.5 Report, WebSocket, history, and export integration

- the original report is preserved as an exact prefix;
- the return value equals exactly
  `original_report + "\n\n---\n\n" + appendix`;
- existing References text and citation numbering are byte-for-byte unchanged;
- an LLM body already containing the exact static appendix heading bytes does
  not cause content-based deduplication or suppress the one approved appendix;
- repeated report generation produces one appendix in each independent return
  value, not cumulative mutation of a prior string;
- the WebSocket `output` equals the exact heading-first appendix suffix after
  the return value's separator, byte-for-byte, and excludes the separator;
- the exact direct `self.websocket.send_json()` call occurs once and neither
  `stream_output()` nor `safe_send_json()` is called;
- event order is all fake LLM report chunks, appendix chunk,
  `report_completed`, return, then fake file/path handling, with no full-report
  duplicate chunk;
- `self.websocket is None` performs no `send_json` call and still returns the
  completed report;
- a fake WebSocket succeeds with exactly one direct call;
- a wrapper whose internal raw WebSocket is `None` is still called exactly once
  so its existing transport-JSON behavior executes;
- underlying WebSocket failure, post-send transport-JSON failure, wrapper or
  callback failure, and the original `CancelledError` each propagate unchanged,
  produce no successful return, clear the report-write guard, retain readiness
  and the finalized audit for retry, and make at most one `send_json` call;
- when the client receives the appendix before transport-JSON failure, the
  accepted at-most-once side effect is not retried or duplicated;
- formatter and URL failure tests prove the formatter runs once only after all
  fake LLM chunks, with no preflight or retry, no appendix chunk, no successful
  `report_completed` event, no successful return, guard cleanup, and retained
  readiness/audit;
- getter, formatter, or WebSocket failure leaves the finalized audit readable;
- existing Markdown, PDF, and DOCX functions are mocked and each receives the
  same complete string;
- existing frontend/report-history and CustomLogsHandler transport semantics
  may receive the appendix as ordinary product report text without a schema
  change;
- diagnostic logger sentinels prove snapshot, appendix, rationale, raw response,
  prompt, query, abstract/body, exception, and secret content is not newly
  logged;
- escaped validated rationale is present in the approved report/WebSocket/
  persistence/export products, while raw response, abstract/body, prompt, and
  key sentinels remain absent; and
- all external boundaries are mocked or fail-fast.

## 16. Explicit non-goals

Milestone 2.3B does not implement or modify:

- a structured audit field in an API response;
- a WebSocket schema or message-type change;
- a frontend audit panel, filter, or control;
- citation-to-candidate or `cited_in_final_report` tracking;
- composite scoring or ranking;
- Crossref or retraction checks;
- a new LLM call or LLM translation;
- Retriever or Provider behavior;
- screening, deduplication, topic decisions, routing, or compression;
- a structured audit file, database table, object-store record, or JSON audit
  persistence;
- complete multilingual localization;
- output truncation, pagination, or interactive expansion;
- report-template or Prompt changes;
- export-format redesign; or
- dependency changes.

The ordinary final report text and existing report artifacts may contain the
appendix. That expected report-text behavior is distinct from persisting the
structured audit snapshot as a new data product.

## 17. Acceptance checklist

- [x] This specification received explicit approval before implementation began.
- [ ] Only the frozen seven implementation files changed.
- [ ] The feature is default-disabled through `PAPER_SCREENING_AUDIT_REPORT_ENABLED=false`.
- [ ] Runtime configuration is not accessed until all five preconfiguration gates pass.
- [ ] Invalid configuration is rejected only in the eligible ready path.
- [ ] Same-instance target-scope top-level owner report/research overlap is rejected before any state mutation or await with the exact frozen messages.
- [ ] Borrower/report overlap and external direct context/image mutation retain existing behavior and are not claimed to be detected by the new guard.
- [ ] The plan does not copy, deep-copy, tuple-convert, or store context or images, and the existing Writer input contract remains unchanged.
- [ ] Report-write active state is always released in finally, including same-object cancellation propagation.
- [ ] The immutable appendix plan is captured after the guard and before the first await or log event.
- [ ] An enabled plan captures exactly one getter snapshot and normalized language and stores no mutable run state.
- [ ] The post-LLM path never rereads readiness, collector, getter, language, or run ordinal.
- [ ] The exact append/send integration point is after complete LLM report generation and before the existing completion log and return.
- [ ] The final value is exactly `original_report + "\n\n---\n\n" + appendix`, and the heading-first appendix contains no separator of its own.
- [ ] No audit data enters an LLM prompt and no new LLM call occurs.
- [ ] Disabled and out-of-scope report and WebSocket behavior is byte-for-byte unchanged.
- [ ] The private readiness flag follows every frozen conduct, Quick, overlap, borrower, failure, cancellation, and consecutive-run transition.
- [ ] Quick invalidates appendix readiness without clearing the finalized 2.3A snapshot.
- [ ] `conduct_research -> quick_search -> write_report` cannot append a stale audit.
- [ ] Audit commit and readiness publication have no intervening await.
- [ ] Repeated report writes may reuse one finalized snapshot but append exactly once per independently generated report.
- [ ] The formatter is synchronous, pure, deterministic, and not re-exported from `screening.__init__`.
- [ ] The formatter accepts only a valid finalized-path `PaperScreeningAuditSnapshot` and normalized `zh`/`en` language.
- [ ] Every snapshot-wide lookup uses compound Web-pass identity.
- [ ] Missing, duplicate, cross-pass, or inconsistent lookup data is fatal.
- [ ] The complete English and Chinese appendix templates are golden-byte frozen, including `\n\n` after every heading, at least two adjacent records, all scope text, labels, punctuation, spaces, list markers, forbidden indentation, LF-only line endings, exact inter-record spacing, empty states, boolean text, `N/A`, and trailing-LF behavior.
- [ ] The appendix uses the frozen headings and bullet lists without Markdown tables or raw HTML.
- [ ] The scope statement does not claim routed papers were cited in the final report.
- [ ] Policy fields appear in frozen order.
- [ ] All nineteen summary fields appear with explicit units.
- [ ] Overlapping reason counts are not summed into a total exclusion count.
- [ ] Routed canonical groups appear once in group order.
- [ ] Planning-only canonical groups are separate and explicitly marked as not routed to evidence.
- [ ] Every excluded occurrence appears in audit order with complete frozen reasons.
- [ ] Topic uncertain is never represented as an exclusion reason.
- [ ] Only validated topic rationale is displayed, after common escaping.
- [ ] Provider warnings expose only pass/request ID, Retriever index, safe source identity, and category.
- [ ] Retrieval queries are not displayed.
- [ ] Every frozen empty state is present when applicable.
- [ ] No output record is truncated or paginated.
- [ ] Chinese selection uses exactly the four frozen normalized tokens and every other language falls back to English.
- [ ] Dynamic scalars use only the frozen strict type conversions and tuple empty-state rules without generic stringification.
- [ ] All dynamic text follows the frozen newline, backslash, HTML, and Markdown escaping sequence.
- [ ] Dynamic data cannot create a heading, list item, table row, link label, or raw HTML.
- [ ] DOI remains escaped plain text and is never converted to a URL.
- [ ] Only validated HTTP/HTTPS targets become exactly `[Open paper]({normalized_url})` or `[打开论文]({normalized_url})`, with the URL occurring once and receiving no text-sanitizer pass.
- [ ] Unsupported URL schemes are non-clickable and malformed supported URLs fail closed.
- [ ] URL userinfo, controls, malformed ports, malformed percent escapes, and Markdown target injection are rejected or encoded as frozen.
- [ ] DNS/IDNA, IPv4, IPv6, explicit-port grammar and decimal reconstruction, percent-escape, UTF-8, and component-safe normalization follows the sole frozen algorithm.
- [ ] URL golden tests cover valid/invalid IPv4; bracketed/unbracketed IPv6; zone identifiers; IPvFuture; authority backslash; userinfo; ports 0/1/65535/65536, leading-zero normalization, explicit default-port preservation, absent ports, empty ports, and nonnumeric ports; every frozen malformed/control percent escape; uppercase percent normalization; all three exact safe sets; exact inline-link bytes; and plain-text javascript/data/file rendering.
- [ ] Every frozen dangerous hostname, including punctuation, whitespace, percent, underscore, plus, brackets, backtick, leading/trailing hyphen, empty label, 64-byte label, and 254-byte host cases, has a direct strict fatal automated test.
- [ ] Legal internal-hyphen, single-letter, single-digit, Unicode IDNA, punycode, 63-byte-label, and 253-byte-host cases have direct complete exact-link success tests.
- [ ] Hostname fail-closed tests are independent from and cannot be replaced by path/query/fragment percent-encoding tests.
- [ ] No hostname safety acceptance may be claimed only through code review, `contains` assertions, or indirect tests.
- [ ] Abstract, body, retrieval query, raw response, prompt, header, exception, traceback, and secret data are absent.
- [ ] The formatter logs neither input nor output and does not modify the snapshot.
- [ ] Identical inputs produce byte-for-byte identical output.
- [ ] The WebSocket `output` is the exact heading-first appendix suffix used by the return value and excludes the return-only separator.
- [ ] The appendix WebSocket chunk uses one direct `send_json` call and no logging/rewriting helper.
- [ ] `self.websocket is None` performs no send, while any non-`None` wrapper receives exactly one direct `send_json` call even when its internal raw WebSocket is `None`.
- [ ] WebSocket, post-send transport-JSON, wrapper, callback, and cancellation failures propagate unchanged, never return success, never retry, clear the guard, and preserve readiness and finalized audit for a later retry.
- [ ] A transport failure after client receipt is an accepted at-most-once partial side effect and never triggers a second appendix send.
- [ ] The appendix chunk is sent once after all LLM chunks and before `report_completed`, return, and downstream path handling, with no duplicate full-report chunk.
- [ ] No WebSocket schema, backend, frontend, or exporter change is required.
- [ ] Markdown, PDF, and DOCX consumers receive the same completed report string.
- [ ] Existing References and citation numbering are byte-for-byte unchanged.
- [ ] Getter, formatter, URL, WebSocket, and complete transport invariant failures are fatal when enabled.
- [ ] Formatter and URL validation run exactly once after LLM completion with no preflight; failure leaves prior LLM chunks in place but sends no appendix or successful completion event, returns no success, clears the guard, and preserves readiness/audit.
- [ ] An original `CancelledError` propagates unchanged.
- [ ] A report or appendix failure never rolls back or mutates the finalized audit snapshot.
- [ ] Existing report-text persistence may contain the appendix without creating structured audit persistence.
- [ ] Validated escaped rationale may enter approved report products, while formatter diagnostics and raw sensitive source data remain prohibited.
- [ ] Quick, Deep, Detailed, Hybrid, Local, subtopic, and borrower flows remain unchanged.
- [ ] Candidate, audit, Workspace, screening, routing, three-key result, and prefetched-body contracts remain unchanged.
- [ ] No new network, Provider, Retriever, LLM, scraper, compressor, MCP, or export-service call is introduced.
- [ ] All automated tests are fully mocked/fake and fail fast on real external access.
- [ ] No dependency, Prompt, frontend, backend, audit-model, Retriever, Provider, or export file changed.
- [ ] No real network, Provider, LLM, Retriever, scraper, compressor, MCP, or external export operation was run during verification.
