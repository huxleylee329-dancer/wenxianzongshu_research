# Academic Writing Milestone 4.0: Fixed Report Profile Contract

Status: **Approved and frozen**

规范已批准并冻结，仅授权在精确十九文件边界内继续实施；Implementation acceptance在实施完成并验证前保持未勾选。

## 1. Goal and terminal planning boundary

Milestone 4.0 adds an explicit, deterministic fixed-report-profile contract to
the existing Academic Writing request, outline, checkpoint, OutlineWriter, and
approval-digest surfaces. It is the project's last planned feature milestone.

The caller, never the query or an LLM, selects exactly one of six fixed report
modes and the sole fixed-profile locale. Code supplies the ordered section
roles and Chinese titles. The LLM supplies only the overall report title and
one brief for every catalog role.

Fixed profiles contain numbered body sections only. Abstract and keywords are
not outline sections. This avoids weakening the Milestone 3.9 requirement that
every outline section contain at least one provenance-backed citation and
preserves every Milestone 3.9-3.14 nonempty-citation contract. References remain
the deterministic Milestone 3.12 suffix.

This milestone does not design a Milestone 4.1 or any FrontMatter Composer,
FinalEditor, graph outcome, human-review resolution command, export,
persistence, backend, frontend, or UI.

## 2. Frozen baselines and narrow supersession

The directly superseded clauses are:

1. Milestone 3.0's exact `AcademicWorkflowRequest`, `WorkflowOutline`, and
   `WorkflowOutlineSection` field sets;
2. Milestone 3.3's single freeform OutlineWriter prompt and response contract,
   which becomes an unchanged freeform path plus one fixed-profile path;
3. Milestone 3.4's outline approval digest algorithm; and
4. Milestone 3.0's four-layer canonical-byte equality only for the exact
   schema-1 legacy fields-set shapes enumerated in Section 7.2.

The narrow legacy exception permits one deterministic restoration and dump that
adds only the missing defaulted fields. Complete new payloads retain recursive
value equality, recursive exact-type equality, strict model equality, and
canonical UTF-8 byte equality.

All other Milestone 3.0-3.14 contracts remain frozen. In particular, this
milestone does not alter evidence, provenance, SectionWriter, citation marker,
Gate, Reviewer, Disposition, merge, reference rendering, Composer, Handoff,
phase, status, event, node, checkpoint topology, external-call, or safety
semantics.

## 3. Exact future implementation boundary

Implementation may touch exactly these nineteen files after renewed explicit
approval. The production boundary remains the original seven files: one added
module and six modified modules. The test boundary expands from six to twelve
files. The existing thirteen-file partial implementation is retained as a
read-only recovery point until renewed approval.

Add:

```text
gpt_researcher/workflows/academic_writing/report_profiles.py
```

Modify production:

```text
gpt_researcher/workflows/academic_writing/state.py
gpt_researcher/workflows/academic_writing/outline_writer.py
gpt_researcher/workflows/academic_writing/graph.py
gpt_researcher/workflows/academic_writing/citation_evidence_gate.py
gpt_researcher/workflows/academic_writing/references_renderer.py
gpt_researcher/workflows/academic_writing/academic_review_handoff.py
```

Modify tests:

```text
tests/test_academic_writing_workflow_state.py
tests/test_academic_writing_outline_writer.py
tests/test_academic_writing_workflow_graph.py
tests/test_academic_writing_citation_evidence_gate.py
tests/test_academic_writing_references_renderer.py
tests/test_academic_writing_academic_review_handoff.py
tests/test_academic_writing_academic_draft_composer.py
tests/test_academic_writing_citation_reviewer.py
tests/test_academic_writing_outline_approval.py
tests/test_academic_writing_section_writer_sequence.py
tests/test_academic_writing_section_writer.py
tests/test_academic_writing_section_merger.py
```

No twentieth file is permitted. No additional production file is permitted.
In particular, implementation must not modify TopicPlanner, ResearchEvidence,
CitationReviewer, CitationReviewDisposition, or AcademicDraftComposer production
code, package initializers, dependencies, graph nodes, adapters, events, or any
frozen specification.

The three modified strict consumers are the only production modules whose
frozen exact request/outline/section surface tables require extension. The
unchanged 3.10, 3.11, and 3.13 consumers continue through their existing public
and canonical restoration contracts. The newly in-scope 3.10 and 3.13 tests may
change only their approved-state digest fixtures as specified in Section 13;
their production behavior and all other assertions remain unchanged.

## 4. Exact mode, locale, and DTO fields

`report_profiles.py` defines the typing alias:

```python
ReportMode = Literal[
    "freeform",
    "stem_literature_review",
    "technical_route_survey",
    "method_comparison",
    "equipment_material_selection",
    "proposal_research_status",
    "systematic_literature_review",
]
```

The exact added DTO fields are:

```python
AcademicWorkflowRequest.report_mode: ReportMode = "freeform"
AcademicWorkflowRequest.report_locale: Literal["zh-CN"] | None = None

WorkflowOutline.report_mode: ReportMode = "freeform"
WorkflowOutline.report_locale: Literal["zh-CN"] | None = None

WorkflowOutlineSection.section_role: str = "freeform"
```

`AcademicWorkflowState` gains no top-level mode or locale field. The request is
the input selection; the outline is the approved artifact projection.

For every newly supplied value, Python validation requires exact `str`; string
subclasses reject before equality, membership, stripping, or normalization.
`report_mode` and `report_locale` are case-sensitive and whitespace-sensitive.
Unknown text, `null` mode, a locale other than exact `"zh-CN"`, and variants
such as `"zh-cn"`, `"ZH-CN"`, or `" zh-CN "` reject where a fixed profile is
required. No fuzzy locale check is permitted.

`language` remains the existing stripped, nonblank, at-most-128-code-point
free-text request for prose generation. It is not a profile locale enum and is
never used to infer the mode or catalog. `report_locale` alone selects catalog
labels. Freeform legacy state retains the existing arbitrary `language`
contract.

## 5. Immutable profile catalog

The catalog is one module-owned tuple of immutable primitive tuples, in the
exact order below. It is not a mutable dictionary, registry, plugin surface,
configuration value, environment value, prompt result, or LLM output. Private
lookup traverses only the trusted tuple. No package initializer re-exports it,
and no second-language catalog exists.

Every role has exact type `str`, matches:

```text
[a-z][a-z0-9]*(?:_[a-z0-9]+)*
```

and has 1 through 48 code points. A role is reused across profiles only where
the section function is intentionally shared. Titles may be profile-specific.

### 5.1 `stem_literature_review` — 8 sections

```text
research_background                  研究背景
literature_search_method              文献检索方法
technical_routes                      主要技术路线
experimental_methods_and_metrics      实验方法与评价指标
results_comparison                    研究结果对比
existing_problems                     现有问题
future_research                       未来研究方向
conclusion                            结论
```

### 5.2 `technical_route_survey` — 9 sections

```text
requirements_and_scope                需求与边界
literature_search_method              资料检索方法
technical_routes                      技术路线分类
principles_and_process                核心原理与流程
performance_maturity_cost_comparison  性能成熟度与成本对比
application_scenarios                 适用场景
risks_and_challenges                  风险与难点
recommended_route                     推荐路线
conclusion                            结论
```

### 5.3 `method_comparison` — 9 sections

```text
problem_definition                    问题定义
comparison_framework                  比较框架
candidate_methods                     候选方法
experimental_conditions_and_data      实验条件与数据
performance_comparison                性能对比
robustness_and_scalability            鲁棒性与扩展性
cost_and_engineering_complexity       成本与工程复杂度
selection_guidance                    适用条件与选型建议
conclusion                            结论
```

### 5.4 `equipment_material_selection` — 10 sections

```text
requirements_and_constraints          需求与约束
candidate_options                     候选方案
parameters_and_material_properties    关键参数与材料性能
testing_and_evidence                  测试与证据
compatibility_and_reliability         兼容性与可靠性
cost_and_supply_risk                  成本与供应风险
safety_environment_compliance         安全环保与合规
decision_matrix                       决策矩阵
recommended_solution                  推荐方案
conclusion                            结论
```

### 5.5 `proposal_research_status` — 9 sections

```text
research_background_and_significance  研究背景与意义
literature_search_method              检索范围与方法
domestic_research_status              国内研究现状
international_research_status         国外研究现状
technical_routes                      主要学派或技术路线
existing_problems                     现有不足
proposed_problem                      拟解决问题
research_content_and_innovation       研究内容与创新点
conclusion                            结论
```

### 5.6 `systematic_literature_review` — 10 sections

```text
research_questions_and_protocol       研究问题与协议
databases_and_search_strategy         数据库与检索式
eligibility_criteria                  纳入排除标准
quality_assessment                    质量评价
study_selection_process               文献筛选流程
data_extraction_and_synthesis         数据提取与综合
results                               结果
bias_and_limitations                  偏倚与局限
discussion                            讨论
conclusion                            结论
```

No catalog includes abstract, keywords, or references. A freeform section must
have role exactly `"freeform"`. A fixed-profile section must never have that
role. Unknown, duplicate, missing, additional, or reordered fixed roles reject.

## 6. DTO and state binding

### 6.1 Request and outline local invariants

For request and outline DTOs independently:

- `report_mode == "freeform"` requires `report_locale is None`;
- any fixed mode requires `report_locale == "zh-CN"`;
- a freeform outline requires every section role to equal `"freeform"`; and
- a fixed outline requires its section count and ordered `(section_role,
  title)` projection to equal its catalog entry exactly.

Section role validation does not strip or normalize. Exact `"freeform"` or an
exact catalog role is retained code point for code point. Existing contiguous
order, deterministic `section_id`, title uniqueness, title/brief normalization,
and all existing bounds continue to apply.

### 6.2 State cross-artifact invariants

Whenever `state.outline` exists, the `AcademicWorkflowState` validator requires:

```text
state.request.report_mode == state.outline.report_mode
state.request.report_locale == state.outline.report_locale
```

It then repeats the freeform or fixed catalog validation as a cross-artifact
invariant. An outline cannot be approved, restored, passed to a strict consumer,
or used for a digest if this binding fails. Earlier states with no outline are
valid after request-local validation.

No mode or locale is copied into TopicPlan, ResearchEvidence, top-level state,
events, errors, decisions, drafts, reviews, merged output, referenced output,
Composition, or Handoff.

## 7. New-start and legacy checkpoint rules

### 7.1 New workflow start

Before `_safe_input()` or any canonical dump/rebuild, `start_academic_workflow`
must statically inspect the exact input `AcademicWorkflowRequest` Pydantic
surface. The exact internal dict and fields-set must first be established as
trusted built-in containers whose keys/members are exact strings. The original
fields-set must explicitly contain both `report_mode` and `report_locale`.

Only after copying the two exact primitive values may normal canonical request
validation run. For a thread whose `snapshot.created_at is None`, start requires
one of the six fixed modes and exact locale `"zh-CN"`. Missing/defaulted mode,
explicit `"freeform"`, missing/defaulted locale, explicit `None`, unknown text,
string subclasses, case variants, and whitespace variants fail before graph
invocation and before any adapter call.

The static flags are used only for the new-thread branch. An already existing
thread retains the frozen identity/exists error ordering. The facade never
infers mode or locale from `query`, `language`, `report_type`, titles, URLs, or
checkpoint content.

### 7.2 Legacy restore and resume

Schema version remains exact `"1"`. On schema-1 restoration:

- missing request `report_mode` becomes exact `"freeform"`;
- missing request `report_locale` becomes `None`;
- missing outline `report_mode` becomes exact `"freeform"`;
- missing outline `report_locale` becomes `None`; and
- every missing section `section_role` becomes exact `"freeform"`.

Relative to each post-4.0 complete field set, these are the only accepted
Pydantic fields-set shapes:

```text
AcademicWorkflowRequest
  A. complete
  B. complete minus report_mode
  C. complete minus report_locale
  D. complete minus report_mode and report_locale

WorkflowOutline
  A. complete
  B. complete minus report_mode
  C. complete minus report_locale
  D. complete minus report_mode and report_locale

WorkflowOutlineSection
  A. complete
  B. complete minus section_role
```

No old field may be missing and no extra field or fields-set member is allowed.
For every omitted new field, the exact internal `__dict__` must nevertheless
contain its exact default value after Pydantic restoration: `"freeform"` for
mode or role and `None` for locale. Complete shapes with `null` mode, wrong
types, unknown values, string subclasses, or corrupt values reject.

The request, outline, and every section may independently have any one of its
listed legal shapes. After defaults are restored, all request-local,
outline-local, request/outline mode and locale, and catalog role/title/count/
order invariants run normally. A fixed mode with a missing locale therefore
restores `None` and rejects rather than becoming a legacy fixed profile.

Fields-set shape is a mechanical compatibility surface only. It is never used
to infer, record, or claim that a payload actually came from a historical
checkpoint.

Explicit `null` report mode, wrong types, unknown strings, and corrupt partial
fixed shapes reject. Defaults distinguish omission from an explicit invalid
value; no fields-set is interpreted as historical provenance beyond applying
this mechanical missing-field compatibility rule.

`resume_academic_workflow` accepts an identity, not a request. It strictly
restores the checkpoint and therefore continues an old freeform running thread.
It does not reapply the new-start explicit-selection gate. The first dump after
restoring a legacy payload writes the five classes of defaulted fields above.
This is a one-way canonicalization; no migration record or schema bump is made.

For a complete new payload, all new fields must be present and the existing
four-layer canonical equality remains exact. A payload may not use omission to
create a fixed profile.

## 8. OutlineWriter dual path

### 8.1 Freeform compatibility

For a restored freeform request, the existing Milestone 3.3 system message,
user-message bytes, and LLM response JSON schema remain exactly the old 3.3
contract. Existing title/section normalization, 3-12 section count, bounds,
factory/client/completion counts, errors, cancellation, and safety behavior
also remain unchanged. Freeform output writes `report_mode="freeform"`,
`report_locale=None`, and `section_role="freeform"` through DTO defaults.

No fixed-profile catalog key is added to the freeform prompt. This preserves
existing system/user prompt goldens and response-schema behavior.

The complete serialized bytes of `AcademicWorkflowRequest`, `WorkflowOutline`,
`WorkflowOutlineSection`, `AcademicWorkflowState`, and checkpoint `values` are
not required to equal their pre-4.0 bytes. Restoring a legal legacy fields-set
shape and dumping it for the first time adds the defaulted `report_mode`,
`report_locale`, and `section_role` keys. That change is the sole schema-1
one-way canonicalization exception approved here. Every subsequent complete
new canonical payload again satisfies all four equality layers. No general
claim that freeform DTO or state bytes are unchanged may override this
exception.

### 8.2 Fixed-profile prompt

The fixed user message is the canonical JSON encoding of exactly:

```text
context_blocks
evidence_sources
language
report_locale
report_mode
research_questions
root_topic
section_profile
```

`section_profile` is an array in catalog order. Every item has exactly:

```json
{"role":"<exact catalog role>","title":"<exact zh-CN catalog title>"}
```

Encoding remains:

```python
json.dumps(
    payload,
    ensure_ascii=False,
    allow_nan=False,
    separators=(",", ":"),
    sort_keys=True,
)
```

All existing query, language, question, context, source, title-truncation, and
65,536-code-point projection rules apply before any factory call. Mode, locale,
roles, and titles are trusted catalog data, not LLM instructions or evidence.

The fixed system instruction uniquely requires one JSON object with exact keys
`"sections"` and `"title"`. Each section object has exact keys `"brief"` and
`"role"`. It expressly forbids section titles, IDs, order, citations,
references, markdown, prose, and extra keys.

### 8.3 Fixed response and construction

The fixed response schema is:

```json
{
  "sections": [
    {"brief": "<brief>", "role": "<exact catalog role>"}
  ],
  "title": "<overall title>"
}
```

JSON object key order is irrelevant. Array order is normative. Raw response
must have exact type `str`, be nonblank, and contain at most 24,576 code points.
Unknown, missing, or extra keys and wrong types fail without repair.

The adapter requires response roles to equal the complete catalog role tuple
positionally. Missing, extra, duplicate, renamed, freeform, unknown, or
reordered roles fail. Roles are never stripped, case-folded, normalized, or
inferred. The LLM cannot return a section title.

After validation, code constructs sections in catalog order:

```text
section_id  = f"section:{order:06d}"
order       = 1..N
section_role= catalog role
title       = catalog zh-CN title
brief       = normalized LLM brief
```

The outline copies exact request mode and locale. There is one factory, one
fresh client, and one completion. There is no retry, fallback, repair, role
inference, alternate title, runtime catalog, or freeform fallback.

TopicPlanner and ResearchEvidence do not inspect, prompt with, branch on, or
copy report mode, locale, role, or profile title.

## 9. Brief, prompt, and response boundaries

### 9.1 Brief aggregate

Fixed profiles have 8, 9, or 10 sections. Every brief remains exact nonblank
text of at most 1,024 code points. The inherited aggregate maximum is 8,192
code points. The maximum equipment vector is exactly:

```text
7 * 1,024 + 1,022 + 1 + 1 = 8,192
```

This clause supersedes any proposed statement that twelve 1,024-code-point
briefs are simultaneously reachable. They are not: the inherited aggregate
cap rejects 12,288. The freeform 12-section count remains legal, but it also
remains subject to the same 8,192 aggregate cap.

### 9.2 Reachable fixed prompt boundary

Use `equipment_material_selection`, its exact ten-entry catalog, no evidence
sources, `language = "a" * 128`, one research question `"q" * 512`, and exact
`query == research_topic == NUL * 4,096`. Use three context blocks:

```text
block 1 = NUL * 4,096
block 2 = NUL * 1,371 + "a" * 2,725
block 3 = "a" * 4,091
```

The block lengths are 4,096, 4,096, and 4,091; aggregate context length is
12,283. Under the exact canonical formula in Section 8.2, the user message is
exactly 65,536 Python code points. Every earlier item and aggregate guard passes,
so the adapter makes exactly one factory and completion call.

Appending one ASCII `"a"` to block 3 makes its length 4,092 and aggregate
12,284, both still legal, while the canonical user message becomes exactly
65,537 code points. That input rejects before factory selection; factory,
client, and completion counts are zero. No source cutoff or illegal earlier
field is used to manufacture either boundary.

### 9.3 Raw response boundary

A contract-valid fixed response is below the raw cap. Appending only legal JSON
whitespace after the complete object reaches exactly 24,576 code points and
still parses successfully. One further whitespace code point produces 24,577
and rejects before JSON parsing. Invalid JSON is not a boundary vector.

## 10. Approval digest

### 10.1 Legacy/freeform algorithm

For `report_mode == "freeform"`, construct the exact legacy Milestone 3.4
outline projection. It contains only:

```text
outline_id, evidence_id, attempt, title, sections
```

and each section contains only:

```text
section_id, order, title, brief
```

The projection entirely excludes `report_mode`, `report_locale`, and every
`section_role`. Encode it with the existing canonical JSON parameters and
compute `SHA256(legacy_canonical_outline_bytes)`. Consequently an old approved
checkpoint's digest remains identical byte for byte and hex digit for hex
digit after default restoration. The legacy projection is permitted only for a
valid freeform/None/freeform-role outline.

### 10.2 Fixed-profile algorithm

For a fixed profile, encode the complete new `WorkflowOutline` JSON projection,
including `report_mode`, `report_locale`, every ordered `section_role`, and
every catalog title, with the existing canonical JSON parameters. Then compute:

```python
hashlib.sha256(
    b"academic-fixed-profile-v1\0" + canonical_outline_bytes
).hexdigest()
```

The domain prefix prevents a fixed outline from colliding with the legacy
digest domain. Locale is included because it determines title catalog identity.
Mode, locale, role, title, count, or order mutation changes canonical bytes and
therefore must fail decision binding.

### 10.3 Sole production digest function

The private production function `_outline_digest(...)` is the sole allowed
digest producer and verifier for both freeform and fixed branches. Its internal
branch selects Section 10.1 or 10.2; no caller may reproduce the legacy
projection, fixed projection, domain prefix, canonical encoding, or SHA call.

The same function object must be used for:

- approval interrupt/command payload creation;
- approval decision-record creation;
- state and checkpoint restoration validation;
- approval retry validation;
- resume restoration and validation; and
- every expected-value recomputation before a digest comparison.

`nodes.py` continues importing and calling this existing private function and
requires no source modification. `state.py`, `nodes.py`, and `graph.py` must not
contain a second digest implementation. The existing graph test file patches
the one function identity with one counting spy across the relevant imported
references and mechanically proves that approval creation, restoration,
retry, and resume use that function and no duplicate digest path.

### 10.4 Complete stem golden

The golden outline has overall title `"示例综述"`, fixed mode
`"stem_literature_review"`, locale `"zh-CN"`, the exact eight catalog rows in
Section 5.1, and brief `"目标" + str(order)` for orders 1 through 8. Its exact
canonical JSON is:

```json
{"attempt":1,"evidence_id":"evidence:000001","outline_id":"outline:000001","report_locale":"zh-CN","report_mode":"stem_literature_review","sections":[{"brief":"目标1","order":1,"section_id":"section:000001","section_role":"research_background","title":"研究背景"},{"brief":"目标2","order":2,"section_id":"section:000002","section_role":"literature_search_method","title":"文献检索方法"},{"brief":"目标3","order":3,"section_id":"section:000003","section_role":"technical_routes","title":"主要技术路线"},{"brief":"目标4","order":4,"section_id":"section:000004","section_role":"experimental_methods_and_metrics","title":"实验方法与评价指标"},{"brief":"目标5","order":5,"section_id":"section:000005","section_role":"results_comparison","title":"研究结果对比"},{"brief":"目标6","order":6,"section_id":"section:000006","section_role":"existing_problems","title":"现有问题"},{"brief":"目标7","order":7,"section_id":"section:000007","section_role":"future_research","title":"未来研究方向"},{"brief":"目标8","order":8,"section_id":"section:000008","section_role":"conclusion","title":"结论"}],"title":"示例综述"}
```

Its canonical UTF-8 length is 1,166 bytes. With the exact domain prefix, the
full expected digest is:

```text
a1f9d02e75f912abfb458c82b0f27f3ead7d9d8ad7d23e32d91b28c43adbbd1b
```

Goldens mutate mode, locale, one role, one catalog title, and two section
positions independently; every mutation must differ from the golden digest and
must be rejected by fixed-profile validation or approval binding.

## 11. Strict-consumer compatibility

`citation_evidence_gate.py`, `references_renderer.py`, and
`academic_review_handoff.py` extend their exact trusted request, outline, and
section field tables only where those objects are already inspected. They
accept exactly the request/outline/section fields-set shapes enumerated in
Section 7.2, restore omitted values to their exact defaults, validate exact
primitive mode/locale/role values, and enforce the complete catalog binding
before using the restored state. They do not introduce a new citation,
provenance, rendering, Handoff, or security rule.

The three modules must accept complete fixed-profile fields and all exact
complete/legacy-compatible fields-set variants enumerated for each DTO in
Section 7.2. Their existing behavior and outputs remain otherwise identical.
Defaults do not authorize a fixed profile with missing fields.

CitationReviewer, CitationReviewDisposition, and AcademicDraftComposer require
no source change: their public Gate boundary or canonical state restoration
receives a state already validated under this contract. Focused regression
must prove all six Milestone 3.9-3.14 consumers accept the new valid state shape
without weakening their existing behavior.

## 12. Error, safety, and mutation boundaries

All existing fixed safe error types, texts, cause/context rules, raw execution
classification, cancellation propagation, and sensitive-reference lifecycle
remain unchanged. New validation failure is classified at the existing owning
DTO, adapter, facade, or invariant boundary; no dynamic mode, locale, role,
title, query, prompt, response, or checkpoint text enters a fixed error.

Static pre-canonical start inspection must validate exact built-in containers
and exact-string keys/members before equality, membership, or subscription. It
must not execute an input method, dynamic attribute, equality, repr, iterator,
descriptor, instance shadow, or string-subclass behavior.

Catalog and input objects are read-only. Implementation creates no mutable
module-global cache, registry, runtime profile override, filesystem artifact,
network request, Provider call, Config call, or additional LLM call. External
mutation of frozen-model internals during a call remains illegal concurrent
input and is not detected, locked, repaired, or retried.

## 13. Focused test contract

Permanent tests are limited to behavior and real regression risks:

1. one parameterized golden matrix contains all six profiles and checks exact
   mode, locale, role/title tuple, count, order, and deterministic IDs;
2. one legacy matrix covers the exact 4/4/2 request, outline, and section
   fields-set shapes, rejection of every missing-old/extra shape, schema 1,
   resume, one-way dump, new-payload equality, and unchanged freeform
   system/user prompt plus response-schema behavior;
3. one new-start matrix covers explicit fixed mode/locale and missing,
   freeform, `None`, unknown, subclass, case, whitespace, and no-inference
   failures before graph/adapter calls;
4. one role matrix covers missing, extra, duplicate, renamed, freeform,
   unknown, and reordered fixed response/state roles;
5. one digest matrix covers unchanged legacy digest projection/result, the
   complete fixed golden, domain separation, mode/locale/role/title/order
   mutations, and one-spy call evidence for every approval/retry/resume path;
6. existing boundary tests are extended for the 8,192 aggregate vector, exact
   65,536/65,537 fixed prompts, and exact 24,576/24,577 raw responses; and
7. focused consumer regression proves Gate, Reviewer, Disposition, Renderer,
   Composer, and Handoff accept valid default/fixed fields while retaining all
   prior citation and output behavior;
8. the approved-state digest fixtures in
   `test_academic_writing_academic_draft_composer.py`,
   `test_academic_writing_citation_reviewer.py`,
   `test_academic_writing_section_writer_sequence.py`,
   `test_academic_writing_section_writer.py`, and
   `test_academic_writing_section_merger.py` use the production
   `_outline_digest` contract or the same already-exposed production test entry.
   They must not hash a complete `outline.model_dump()`, copy either canonical
   projection, call SHA directly, or change the tested production behavior; and
9. `test_academic_writing_outline_approval.py` updates its nineteen existing
   new-start call groups through the smallest shared parameterization so each
   start supplies one explicit fixed mode, exact `zh-CN`, and a catalog-conformant
   outline. Its checkpoint assertions include the real canonical 4.0 default
   fields, while its legacy-resume coverage continues to omit and restore
   `freeform`/`None`/`freeform` without confusing resume with new start.

Tests use injected fake clients and existing fixtures. They perform no real
Config, LLM, Provider, Retriever, network, filesystem, backend, frontend, or
external-service work.

No permanent test may be an initial-red/module-missing test, AST-only or
symbol-only assertion, full-suite requirement, multi-order run, duplicate
walker/import guard, per-profile test function, repeated Milestone 3.0-3.14
safety framework, or new generic security framework. A stronger parameterized
case replaces weaker duplicates rather than increasing test count.

## 14. Non-goals

- abstract or keyword generation and any FrontMatter Composer;
- weakening or role-special-casing the per-section citation Gate;
- FinalEditor, automatic rewrite, citation repair, or claim-level judgment;
- graph outcome, Composer graph wiring, checkpoint outcome DTO, or new node;
- human-review resolution command, interrupt, approval UI, or product flow;
- export, file I/O, persistence, publication, backend, frontend, or UI;
- a second locale, translated title catalog, locale negotiation, or fallback;
- query/language inference, runtime catalog configuration, plugin profiles, or
  user-defined fixed profiles;
- production changes to TopicPlanner, ResearchEvidence, 3.10, 3.11, or 3.13; and
- any later planned feature milestone.

## 15. Stop conditions

Implementation stops and requires a new explicitly approved specification if:

- the complete stem digest differs from
  `a1f9d02e75f912abfb458c82b0f27f3ead7d9d8ad7d23e32d91b28c43adbbd1b`
  for an unexplained reason;
- the exact 65,536 and 65,537 vectors do not both pass every earlier field,
  item, count, and aggregate condition stated here;
- any twentieth file is required;
- TopicPlanner, ResearchEvidence, CitationReviewer,
  CitationReviewDisposition, or AcademicDraftComposer production code must
  change;
- a legacy freeform approval digest cannot remain byte-for-byte identical;
- exact `"zh-CN"` cannot close the fixed catalog/start/state binding; or
- implementing the catalog would weaken a Milestone 3.9-3.14 citation contract.

## 16. Draft approval

- [x] This specification received explicit approval before implementation began.
- [ ] Milestone 4.0 is the final planned feature milestone and has no implied follow-up milestone.
- [ ] Front matter choice B is approved: fixed profiles contain numbered body sections only.
- [ ] Abstract and keywords remain outside the outline and FrontMatter Composer remains a non-goal.
- [ ] References remain exclusively the Milestone 3.12 suffix.
- [ ] No Milestone 3.9-3.14 nonempty-citation contract changes.
- [ ] The seven exact ReportMode literals and six explicit fixed choices are approved.
- [ ] Request mode and locale defaults are approved solely for legacy restoration.
- [ ] Outline mode and locale defaults and section-role default are approved solely for compatibility.
- [ ] AcademicWorkflowState gains no top-level mode or locale.
- [ ] Exact `zh-CN` is the sole fixed-profile locale and no fuzzy locale matching exists.
- [ ] Existing free-text language remains separate from report locale.
- [ ] The immutable primitive-tuple catalog and no-public-registry boundary are approved.
- [ ] The exact stem role/title order and count eight are approved.
- [ ] The exact technical-route role/title order and count nine are approved.
- [ ] The exact method-comparison role/title order and count nine are approved.
- [ ] The exact equipment/material role/title order and count ten are approved.
- [ ] The exact proposal-status role/title order and count nine are approved.
- [ ] The exact systematic-review role/title order and count ten are approved.
- [ ] Role syntax, 1-48 length, reuse, freeform-only, and fixed-role rules are approved.
- [ ] Request/outline mode and locale cross-binding is approved.
- [ ] Fixed outline role/title/count/order equality with the catalog is approved.
- [ ] New start requires caller-explicit fixed mode and exact locale before canonical rebuild.
- [ ] Missing/default/freeform/None/unknown/subclass/case/whitespace start failures are approved.
- [ ] Query and language inference are forbidden.
- [ ] Resume accepts old freeform threads without reapplying the new-start gate.
- [ ] Schema version one, the exact 4/4/2 fields-set shapes, and one-way canonicalization are approved.
- [ ] Complete new payloads retain all four canonical equalities.
- [ ] Freeform system/user prompt bytes, response schema, and legacy approval digest alone retain their old byte contracts.
- [ ] Fixed prompt contains the exact eight keys and canonical encoding parameters.
- [ ] The LLM returns only overall title and exact ordered role/brief rows.
- [ ] Code alone supplies section titles, order, and deterministic IDs.
- [ ] Missing, extra, duplicate, renamed, freeform, unknown, or reordered fixed roles fail.
- [ ] TopicPlanner and ResearchEvidence remain mode-unaware.
- [ ] The 8,192 aggregate brief vector supersedes the impossible twelve-times-1,024 claim.
- [ ] The exact 65,536 success and 65,537 pre-factory failure vectors are approved.
- [ ] Exact 24,576 legal-whitespace success and 24,577 failure are approved.
- [ ] Legacy/freeform digest projection excludes all three new field classes.
- [ ] Both digest branches and every approval/retry/resume path use the sole `_outline_digest` function identity.
- [ ] Locale, mode, ordered roles, and catalog titles participate in fixed digest binding.
- [ ] The complete stem canonical JSON, 1,166 bytes, and full digest are approved.
- [ ] Supersession is limited to the exact 3.0, 3.3, 3.4, and legacy-canonical clauses.
- [ ] The exact nineteen-file implementation boundary is approved.
- [ ] No twentieth file, additional production file, dependency, initializer, frozen-spec, or unrelated test may change.
- [ ] The focused parameter matrices and explicit test exclusions are approved.
- [ ] All listed non-goals and stop conditions are approved.

## 17. Implementation acceptance

- [ ] Only the nineteen approved files changed, with exactly one new production module and no additional production file.
- [ ] `ReportMode` accepts only the seven exact literals and rejects subclasses/coercions.
- [ ] Request, outline, and section accept exactly the frozen 4/4/2 fields-set shapes and restore omitted values exactly.
- [ ] Explicit null mode, wrong locale, wrong type, unknown value, and corrupt shapes reject.
- [ ] Section roles reject subclasses, whitespace variants, unknown values, and invalid syntax.
- [ ] The catalog is the exact immutable primitive tuple frozen in Section 5.
- [ ] All six catalog golden rows, counts, roles, titles, and orders match exactly.
- [ ] Freeform sections all use exact `freeform`; fixed sections never do.
- [ ] Fixed outlines reject missing, extra, duplicate, renamed, unknown, and reordered roles.
- [ ] Fixed titles are catalog values and cannot be supplied or overridden by the LLM.
- [ ] State binds request and outline mode and locale exactly whenever outline exists.
- [ ] AcademicWorkflowState has no added top-level mode or locale field.
- [ ] New-start static inspection precedes canonical reconstruction and executes no hostile behavior.
- [ ] New threads accept only explicitly supplied fixed mode plus exact `zh-CN`.
- [ ] All rejected new-start variants make zero graph and adapter calls.
- [ ] Existing-thread identity and exists error priority remains unchanged.
- [ ] Legacy resume restores missing fields and does not invoke the new-start selection gate.
- [ ] Legacy first dump adds only the frozen default fields under schema version one.
- [ ] Complete new payloads pass recursive value/type, model, and canonical-byte equality.
- [ ] Freeform system/user prompt bytes, response schema, projection semantics, calls, failures, and cancellation remain unchanged.
- [ ] Fixed user payload has exactly the eight frozen keys and canonical JSON encoding.
- [ ] Fixed response has exactly title plus ordered role/brief rows and no title override.
- [ ] Fixed role comparison is exact and positional with no normalization or inference.
- [ ] Fixed construction writes catalog title/role and deterministic section ID for every row.
- [ ] Each fixed request uses one factory, one fresh client, and one completion only.
- [ ] No retry, fallback, repair, runtime catalog, or freeform fallback exists.
- [ ] Every brief is nonblank, at most 1,024 code points, and aggregate at most 8,192.
- [ ] The equipment vector reaches exactly 8,192 as 7*1,024+1,022+1+1.
- [ ] The exact equipment prompt vector is 65,536 code points and calls once.
- [ ] Appending one legal ASCII context character yields 65,537 and zero factory calls.
- [ ] Both prompt vectors pass every earlier count, item, and aggregate guard.
- [ ] A valid fixed JSON response plus whitespace reaches exactly 24,576 and succeeds.
- [ ] The corresponding 24,577 raw response rejects without repair.
- [ ] Freeform digest bytes and hex remain identical for old approved checkpoints.
- [ ] All six scope-expansion test files use the sole `_outline_digest` contract for old digest fixtures; no complete `outline.model_dump()` digest, copied projection, or direct SHA remains.
- [ ] Outline-approval new-start tests explicitly use a fixed mode plus exact `zh-CN` and assert the canonical 4.0 checkpoint shape, while missing-field legacy resume remains compatible.
- [ ] Fixed digest uses `academic-fixed-profile-v1` followed by one NUL domain separator through `_outline_digest` only.
- [ ] The complete stem golden produces exactly 1,166 canonical UTF-8 bytes.
- [ ] The complete stem digest equals `a1f9d02e75f912abfb458c82b0f27f3ead7d9d8ad7d23e32d91b28c43adbbd1b`.
- [ ] Mode, locale, role, title, and order mutations all alter or invalidate the fixed digest.
- [ ] Fixed and freeform outlines cannot share a digest domain.
- [ ] CitationEvidenceGate accepts and validates the extended strict state surface.
- [ ] ReferencesRenderer accepts and validates the extended strict state surface.
- [ ] AcademicReviewHandoff accepts and validates the extended strict state surface.
- [ ] All six 3.9-3.14 consumers retain their existing citation and output behavior.
- [ ] No front-matter section, special citation exemption, or references outline row exists.
- [ ] TopicPlanner and ResearchEvidence neither read nor copy mode, locale, role, or profile.
- [ ] No mutable profile cache, registry, runtime override, extra I/O, or external call exists.
- [ ] Existing error, cancellation, sensitive-reference, and mutation boundaries remain unchanged.
- [ ] Focused tests use shared parameter matrices and injected fakes only.
- [ ] No initial-red, AST/symbol-only, full-suite, multi-order, duplicate guard, or new safety framework remains.
- [ ] No per-profile duplicate test function or weaker redundant parameter remains.
- [ ] Implementation acceptance checkboxes remained unchecked until implementation and verification completed.
- [ ] Syntax checks, focused tests, relevant regression, trailing-whitespace check, fence check, and `git diff --check` pass.
- [ ] The staging area is empty and no commit was created by implementation.
