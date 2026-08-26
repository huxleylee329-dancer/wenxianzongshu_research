# Academic Writing Milestone 3.12: Off-Graph Deterministic References Renderer

Status: **Approved and frozen**

规范已批准并冻结，仅授权冻结边界内实施；Implementation acceptance在实施完成并验证前保持未勾选。

## 1. Goal and exact boundary

Milestone 3.12 adds one synchronous, deterministic, off-graph renderer after
the frozen 3.7 merger, 3.9 citation-evidence Gate, and 3.11 disposition Gate:

```text
exact approved AcademicWorkflowState
  + exact WorkflowMergedDraft
  + exact WorkflowCitationEvidenceGateResult
  + exact WorkflowCitationReviewDisposition
                         |
                         v
                render_references()
                         |
                         v
              WorkflowReferencedDraft
```

The renderer preserves the supplied merged content code point for code point
and appends one deterministic Markdown References block. It does not replace
inline markers, edit prose, infer claims, or invoke another workflow stage.

The renderer accepts only an exact `ready` disposition. Here `ready` means only
that the supplied, structurally valid 3.10 review opinions mechanically routed
to `ready` under 3.11. It is not factual correctness, evidence sufficiency,
human approval, publication approval, or proof that any Gate, reviewer,
merger, or LLM was actually executed.

## 2. Frozen baselines and narrow supersession

The direct frozen baselines are:

```text
specs/academic-writing-milestone-3.7-section-merger.md
specs/academic-writing-milestone-3.8-evidence-provenance-index.md
specs/academic-writing-milestone-3.9-citation-evidence-gate.md
specs/academic-writing-milestone-3.11-citation-review-disposition.md
```

This milestone supersedes only their reservation of a future deterministic
references renderer. It changes no existing state, DTO, phase, status, node,
event, facade, graph, checkpoint, adapter, writer, merger, Gate, reviewer,
disposition, prompt, exception, or persistence contract.

The renderer does not use the legacy
`gpt_researcher.actions.markdown_processing.add_references`. That function
accepts an unordered URL set, interpolates raw Markdown, catches and prints
dynamic errors, and has no source-ID/title/Gate/disposition binding. Those
contracts are incompatible with this milestone.

## 3. Exact future implementation boundary

After explicit approval, implementation may add exactly:

```text
gpt_researcher/workflows/academic_writing/references_renderer.py
tests/test_academic_writing_references_renderer.py
```

No existing file may change. In particular, implementation must not modify
`state.py`, `graph.py`, `nodes.py`, events, a facade, a package initializer,
`section_merger.py`, `citation_evidence_gate.py`,
`citation_review_disposition.py`, any existing test, frozen specification,
dependency, lock file, backend, frontend, checkpoint, or legacy entry point.

The production module may import standard-library facilities, Pydantic, and
the exact public signature DTOs from their defining modules. Complete static
state restoration may additionally import these state DTOs under private
aliases and no others:

```python
from gpt_researcher.workflows.academic_writing.state import (
    AcademicWorkflowRequest as _AcademicWorkflowRequest,
    AcademicWorkflowState,
    WorkflowEvidenceProvenance as _WorkflowEvidenceProvenance,
    WorkflowEvidenceSource as _WorkflowEvidenceSource,
    WorkflowEvent as _WorkflowEvent,
    WorkflowOutline as _WorkflowOutline,
    WorkflowOutlineDecisionRecord as _WorkflowOutlineDecisionRecord,
    WorkflowOutlineSection as _WorkflowOutlineSection,
    WorkflowResearchEvidence as _WorkflowResearchEvidence,
    WorkflowTopicPlan as _WorkflowTopicPlan,
)
from gpt_researcher.workflows.academic_writing.section_merger import (
    WorkflowMergedDraft,
)
from gpt_researcher.workflows.academic_writing.citation_evidence_gate import (
    WorkflowCitationEvidenceGateResult,
)
from gpt_researcher.workflows.academic_writing.citation_review_disposition import (
    WorkflowCitationReviewDisposition,
)
```

No private helper from an earlier milestone, annotation or `model_fields`
reflection, `typing` reflection, dynamic import, or `Any` may supply a type or
behavior. If the two added files are insufficient, implementation stops for a
revised, explicitly approved specification.

## 4. Complete public surface

The complete public surface is exactly:

```python
__all__ = (
    "WorkflowReferencedDraft",
    "render_references",
)
```

Every introduced helper, marker, error, constant, literal alias, and extraction
type has a leading underscore. No package initializer re-exports either name.

The sole operation is exactly:

```python
def render_references(
    state: AcademicWorkflowState,
    merged_draft: WorkflowMergedDraft,
    gate_result: WorkflowCitationEvidenceGateResult,
    disposition: WorkflowCitationReviewDisposition,
) -> WorkflowReferencedDraft: ...
```

It is an ordinary synchronous function. There is no class constructor,
Protocol, callback, iterator/generator API, async function, task, thread,
subprocess, client, factory, or external component.

## 5. Strict module-local output DTO

### 5.1 Exact fields

```python
class WorkflowReferencedDraft(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    outline_id: Literal["outline:000001"]
    section_ids: tuple[str, ...]
    reference_source_ids: tuple[str, ...]
    attempt: Literal[1]
    content: str
```

There are exactly five fields. The DTO contains no review, verdict, issue,
rationale, evidence block, score, diagnostic, mutable mapping, timestamp, or
raw input model.

### 5.2 Exact Python and JSON shapes

Direct Python validation accepts only the exact DTO instance or an exact
built-in `dict` with exactly the five declared keys. Python collections are
exact built-in tuples. JSON input is accepted only by `model_validate_json()`;
JSON arrays are copied to exact tuples by mode-aware validation.

Every scalar and collection member has exact built-in type. Lists on the
Python path, tuple subclasses, string subclasses, bool attempts, floats,
coercible values, bytes, iterators, generators, null, missing fields, and extra
fields reject.

The DTO enforces:

1. one through twelve ordered section IDs, where
   `section_ids[index] == f"section:{index + 1:06d}"`;
2. one through 200 unique `reference_source_ids`;
3. every reference ID equals `f"evidence-source:{order:06d}"` for an integer
   order from one through 200;
4. exact integer `attempt == 1`, rejecting `bool`;
5. exact nonblank content of at most 5,901,246 Python code points; and
6. canonical DTO JSON of at most 8,596,240 UTF-8 bytes.

Only `render_references()` binds the section and reference tuples to all four
supplied artifacts and proves the exact output template.

### 5.3 Canonical representation

The sole canonical representation is:

```python
json.dumps(
    result.model_dump(mode="json"),
    ensure_ascii=False,
    allow_nan=False,
    sort_keys=True,
    separators=(",", ":"),
).encode("utf-8")
```

Construction requires strict JSON restoration, exact recursive JSON
value/type equality, exact model equality, and exact canonical-byte equality.
The public function returns only the restored exact DTO.

## 6. Exact input restoration and binding

### 6.1 No operation on untrusted instances

All four inputs remain untrusted even when their exact class matches. Before a
fresh trusted DTO is restored, no path calls an input instance method,
equality, `repr`, `str`, dynamic `getattr`, property, descriptor, custom
iterator, serializer, validator, or model-copy operation.

The only input-object reads use `object.__getattribute__` for the frozen
Pydantic 2.13.4 slots:

```text
__dict__
__pydantic_fields_set__
__pydantic_extra__
__pydantic_private__
```

The namespace and field set must be exact built-in `dict` and `set`; extra and
private must be `None`. Keys and field-set members are exact strings. Exact
tuples use only trusted built-in length/index operations. Primitive values are
type-checked before comparison. Instance methods are permitted only on newly
constructed, canonically restored trusted DTOs.

### 6.2 Complete approved-state restoration

`type(state) is AcademicWorkflowState` is required. Static extraction uses the
unchanged exact field whitelist:

```text
schema_version, workflow_id, thread_id, run_id, phase, status, request,
topic_plan, research_evidence, outline, outline_decision, errors, events
```

The nine imported nested DTO types use their exact frozen field order and
surfaces from `state.py`. The resulting pure JSON-domain mapping is canonically
encoded and restored through the public `AcademicWorkflowState` type. Exact
recursive type/value, model, and canonical-byte equality are mandatory.

`WorkflowResearchEvidence` has one narrowly inherited 3.8 surface exception.
Its exact built-in `__pydantic_fields_set__` may have only one of these two
shapes:

1. the current complete set `{evidence_id, topic_plan_id, attempt,
   context_blocks, sources, provenance}`; or
2. exactly `{evidence_id, topic_plan_id, attempt, context_blocks, sources}`.

For the second shape, the exact built-in `__dict__` must nevertheless contain
the `"provenance"` key, and its value must have `type(value) is tuple` and equal
`()`. The complete shape permits either an exact empty tuple or a contract-valid
nonempty provenance tuple. Both legal empty-provenance shapes continue through
the renderer identically. The renderer neither infers nor records checkpoint
history from the field set. A null or wrongly typed provenance, a missing
`__dict__["provenance"]` value, any other missing field, or any extra field,
field-set member, extra state, or private state fails fixedly. This restoration
rule does not require any cited source to be provenance-backed and therefore
does not narrow the legal 200-ID input surface.

The trusted state must be exactly `phase="outline_approved"`,
`status="completed"`, with an exact `outline_decision.decision == "approve"`.
Strict restoration therefore also proves the existing identity, artifact,
outline digest, event, empty-error, source order, source-ID, source-URL
uniqueness, and provenance-binding contracts without inventing duplicate
business gates.

The renderer copies only the approved outline ID, ordered section IDs, and the
ordered source projection `(source_id, title, url)`. `sources` may contain zero
through 200 entries, but later Gate membership requires at least one referenced
source. Existing title and URL bounds remain 512 and 4,096 code points.

The existing strict state contract already rejects duplicate exact normalized
URLs. Rechecking URL uniqueness while restoring the pure state mapping is an
inherited invariant proof, not a new renderer restriction, deduplication rule,
or first-wins algorithm.

### 6.3 Merged draft

`type(merged_draft) is WorkflowMergedDraft` is required with this exact field
surface and declaration order:

```text
outline_id, section_ids, attempt, content
```

The extractor requires exact primitives, one through twelve unique section
IDs, exact integer one, nonblank content, and the existing 359,538-code-point
content bound. It creates a pure mapping, restores through the public merged
DTO, and requires exact recursive/model/canonical equality.

This proves only DTO shape and value. It cannot prove that `merge_sections()`
was called or recover the original section drafts.

### 6.4 Citation Gate result

`type(gate_result) is WorkflowCitationEvidenceGateResult` is required with the
exact four-field surface:

```text
outline_id, section_ids, cited_source_ids_by_section, attempt
```

The full frozen 3.9 DTO contract is revalidated: one through twelve sections,
one through 64 unique IDs per section, exact ID grammar from one through 200,
outer positional length, exact integer one, and the 19,519-byte canonical
bound. Restoration uses only a pure mapping and the public Gate result type.

The Gate outline ID and ordered section IDs must exactly equal both the trusted
state outline and merged draft. Every cited ID must occur exactly once in the
trusted state's source table. The renderer does not require a cited ID to have
provenance because a structurally valid Gate DTO and 3.11 artifact do not prove
that `gate_citation_evidence()` actually executed. Adding such a requirement
would silently impose a new global-64 gate.

### 6.5 Disposition

`type(disposition) is WorkflowCitationReviewDisposition` is required with the
exact five-field surface:

```text
outline_id, section_ids, section_dispositions, disposition, attempt
```

The complete frozen 3.11 DTO contract and 575-byte canonical bound are
revalidated through a pure mapping and the public disposition type. Outline ID,
ordered section IDs, and positional lengths must exactly bind the state outline,
merged draft, Gate, and disposition.

The sole state-side attempt value is `trusted_state.outline.attempt`.
`trusted_state.outline.attempt`, `merged_draft.attempt`, `gate_result.attempt`,
and `disposition.attempt` must each have `type(value) is int` and value exactly
one. `AcademicWorkflowState` has no top-level attempt field. The returned
`WorkflowReferencedDraft.attempt` is independently fixed to exact integer one.

The overall disposition must be exact `"ready"`, and every positional section
disposition must be exact `"ready"`. No default, coercion, sparse value,
upgrade, override, or repair exists.

## 7. Honest proof and non-proof boundary

Given stable inputs, the renderer mechanically establishes only that:

1. each artifact has its exact trusted structural form;
2. outline ID, ordered section IDs, and tuple positions bind across the state
   outline, merged draft, Gate, and disposition, while their four explicitly
   named attempt values satisfy Section 6.5;
3. every Gate ID names one final state source;
4. the supplied disposition is structurally `ready` at every position;
5. the global marker sequence satisfies Section 9; and
6. the returned content is the exact preserved merged content plus the exact
   bibliography projection.

It does not establish and must not claim:

- that 3.7, 3.9, 3.10, or 3.11 actually ran;
- that the artifacts share a real execution origin;
- that a structurally legal Gate, review, disposition, or merged draft is
  authentic rather than directly fabricated;
- that a marker belongs to the Gate section at the same position;
- claim support, factual truth, evidence sufficiency/completeness,
  contradiction status, citation placement correctness, or provenance
  completeness; or
- that `ready` means human approval, factual correctness, safe publication,
  automatic merge/export eligibility, or permission to release.

A directly constructed, exact, mutually bound four-artifact set is valid input
and receives the same deterministic rendering as producer-created artifacts.
One limitation test makes this explicit without simulating provenance.

## 8. Citation scale and global numbering

The frozen scale has two distinct layers:

```text
per-section cited-ID occurrences       <= 64
aggregate occurrences                  <= 12 * 64 = 768
final state evidence sources           <= 200
global unique cited source IDs         <= 200
```

A real 3.9 execution can expose at most 64 global unique IDs because every
citation must have one of at most 64 provenance entries. The Gate DTO itself,
however, permits source orders one through 200, and 3.11 explicitly does not
prove Gate execution origin. Therefore the renderer must accept a structurally
legal 200-ID `ready` artifact and must not impose a global-64 limit.

The sole global order is:

1. traverse Gate section tuples in `section_ids` order;
2. traverse each inner tuple in its existing first-marker order;
3. append an ID only on its first appearance across the whole traversal; and
4. assign bibliography numbers one through `len(reference_source_ids)` in that
   stable global first-wins order.

No source-order sort, URL sort, title sort, set iteration, provenance order,
alphabetization, or numeric source-ID reorder is permitted.

The reachable 200-ID vector uses citation tuple sizes:

```text
(64, 64, 63, 1, 1, 1, 1, 1, 1, 1, 1, 1)
```

The tuples contain source IDs 1 through 200 in order. They total 200
occurrences, remain below the 768 aggregate bound, and bind to twelve exact
`ready` section dispositions.

## 9. Global marker validation boundary

The renderer never replaces or renumbers an inline marker. The sole recognized
marker syntax is:

```text
[[cite:<source_id>]]
```

The scan is left-to-right over the complete original
`merged_draft.content`. When the exact prefix `[[cite:` occurs, it must have a
following exact `]]`; its interior must be one exact nonempty source ID with no
`[` or `]`, must use the order-1-through-200 grammar, and must name a trusted
state source. Only a token beginning with the exact string `[[cite:` enters
citation parsing. A malformed exact-prefix token that is unclosed, has an empty
or invalid ID, is nested, or has a tail other than exactly the terminating two
`]` characters fails fixedly; an adjacent third `]` or adjacent `[` is an
illegal tail. All text not beginning with that exact prefix is opaque, remains
code point for code point, is not a marker, and is not malformed marker text.
This includes `[cite:evidence-source:000001]`,
`[[CITE:evidence-source:000001]]`, and
` [[ cite:evidence-source:000001]]`.

The scanner builds the left-to-right stable first-occurrence sequence of all
recognized IDs. It must equal, code point for code point and in exact order,
the Gate's globally flattened stable-first-wins sequence. This proves every
Gate ID appears globally and no extra exact marker ID appears. Repeated exact
markers are allowed and retain their source's first bibliography number.

The scan occurs before the bibliography is appended. It does not apply the
3.5/3.7 literal-`://` body rule to the complete merged string because outline
titles are opaque and the appended URLs intentionally may contain that
substring.

`WorkflowMergedDraft` stores only one flat string, while 3.7 permits opaque
Markdown headings in section bodies. Section boundaries therefore cannot be
recovered reliably. The renderer cannot and does not compare a marker with a
specific Gate section tuple. A globally matching but section-redistributed
marker stream may pass, and this limitation is required in prose, tests, and
checklists.

Every marker remains byte/code-point identical in returned content. The
bibliography number is discoverable through the returned ordered
`reference_source_ids`; it is not substituted into the prose.

## 10. Exact bibliography template and escaping

The returned content begins with the original merged content unchanged and
then appends exactly this 17-code-point prefix:

```python
"\n\n## References\n\n"
```

Each reference is one four-space-indented Markdown code line with this exact
template:

```python
f"    [{number}] source_id={source_json} title={title_json} url={url_json}"
```

The values are exactly:

```python
source_json = json.dumps(source_id, ensure_ascii=False)
title_json = json.dumps(title, ensure_ascii=False)
url_json = json.dumps(url, ensure_ascii=False)
```

Lines follow global first-wins order, are joined by exactly one LF, and the
last line has no trailing LF. There is no blank line between entries and no
text after the last URL JSON string.

Because each entry is an indented code line, Markdown punctuation, brackets,
parentheses, backticks, emphasis characters, HTML-like text, and URL schemes
are opaque code text. JSON string encoding escapes quotes, backslashes, CR, LF,
tabs, NULs, and other JSON control characters. No value is interpolated into a
Markdown link destination, HTML attribute, heading, list label, or raw URL
line. Unicode is preserved under `ensure_ascii=False`; length is measured in
Python code points before UTF-8 canonical encoding.

The source metadata is neither normalized again nor silently rewritten. Exact
state restoration already supplies stripped, bounded titles and URLs and
rejects duplicate URLs. The renderer neither deduplicates by URL/title nor
backs one source with another source's metadata.

## 11. Mechanical length bounds

### 11.1 Bibliography and rendered content

For a maximum source ID, `json.dumps(source_id, ensure_ascii=False)` is 24 code
points. A 512-code-point title made of NULs encodes to `512 * 6 + 2 = 3,074`.
A 4,096-code-point URL made of NULs encodes to
`4,096 * 6 + 2 = 24,578`.

For reference number `n`, the exact line length is:

```text
4 spaces + "[" + digits(n) + "] "
+ "source_id=" + 24
+ " title=" + 3,074
+ " url=" + 24,578
= 27,705 + digits(n)
```

For 200 entries:

```text
sum(digits(1..200))                     = 492
sum(reference lines)                    = 200 * 27,705 + 492
                                         = 5,541,492
199 inter-line LF characters            = 199
References prefix                        = 17
bibliography maximum                     = 5,541,708 code points
rendered content maximum                 = 359,538 + 5,541,708
                                         = 5,901,246 code points
```

### 11.2 Canonical DTO maximum

The earlier audit value 8,595,876 assumed 82 characters of genuine 3.7
heading/template structure in the maximum merged input. That assumption is not
part of the `WorkflowMergedDraft` DTO and would falsely claim producer origin.
An exact structurally legal merged DTO may instead contain exactly 200
31-character markers followed or surrounded by NUL code points up to its
359,538-character limit.

The true public-input maximum is therefore:

```text
200 marker characters                 = 200 * 31 = 6,200
remaining merged NULs                 = 359,538 - 6,200 = 353,338
outer-JSON merged interior            = 353,338 * 6 + 6,200
                                      = 2,126,228 bytes

outer-JSON encoded reference lines    = 200 * 32,319 + 492
                                      = 6,464,292 bytes
199 encoded LF separators             = 199 * 2 = 398 bytes
encoded line block                    = 6,464,690 bytes
encoded References prefix             = 21 bytes
content JSON value including quotes   = 2 + 2,126,228 + 21 + 6,464,690
                                      = 8,590,941 bytes

reference_source_ids JSON array       = 2 + 200 * 24 + 199 = 5,001 bytes
section_ids JSON array                = 205 bytes
remaining keys, values and punctuation= 93 bytes
canonical DTO maximum                 = 8,590,941 + 5,001 + 205 + 93
                                      = 8,596,240 UTF-8 bytes
```

All bytes in the maximizing canonical representation are ASCII after JSON
escaping, so the canonical byte and code-point counts coincide.

### 11.3 Simultaneous reachability and max-plus-one

The maximum is simultaneously reachable with legal public inputs:

1. an exact approved state has 200 ordered sources;
2. every title contains 512 NUL code points;
3. every URL contains 4,096 control code points, begins and ends with NUL, and
   uses a distinct internal `U+0001` position so all 200 URLs remain unique
   without reducing six-character JSON escaping;
4. the Gate uses the twelve citation tuple sizes in Section 8 and IDs 1..200;
5. the exact disposition has twelve `ready` entries and overall `ready`;
6. the exact merged DTO has 359,538 code points, exactly one marker for each ID
   in Gate-global order, and NULs in every other position; and
7. all outline/section IDs bind exactly, and the state-outline, merged, Gate,
   and disposition attempts are each exact integer one.

This vector deliberately proves structural reachability, not real execution of
3.7/3.9/3.10/3.11.

Appending one ASCII `X` to the maximum rendered content creates a direct DTO
mapping with 5,901,247 content code points and 8,596,241 canonical bytes. It is
the sole required max-plus-one vector and must reject during direct DTO
validation. No public renderer path fabricates max-plus-one.

Any direct DTO mapping whose content is within the code-point cap but whose
canonical representation exceeds the byte cap also rejects.

## 12. Exact control flow and information safety

The public function freezes this order:

1. statically extract and canonically restore the exact approved state;
2. statically extract and restore the exact merged draft;
3. statically extract and restore the exact Gate result;
4. statically extract and restore the exact disposition;
5. bind outline IDs, section IDs, positions, the four attempts named in Section
   6.5, source membership, and all-`ready` routing;
6. flatten Gate citations and compute the global first-wins source tuple;
7. scan the original merged content and require the exact global sequence;
8. reduce all live inputs to one pure primitive render plan and delete every
   complex input reference;
9. build the bibliography and final content from exact primitives;
10. validate both output limits and perform the complete output canonical
    round trip; and
11. clear/delete mutable accumulators and temporary DTO/bytes before returning
    the restored exact result.

The module defines exactly one private outward error:

```python
class _ReferencesRendererError(RuntimeError):
    pass
```

Its sole text is:

```text
references renderer failed
```

Every ordinary input, surface, binding, marker, source, routing, template,
length, DTO, canonicalization, or internal invariant failure becomes that one
error. It contains no dynamic ID, title, URL, content, validation detail,
representation, or raw exception text. It has `__cause__ is None`,
`__context__ is None`, and `__suppress_context__ is False`.

Sensitive helpers catch ordinary `Exception` without stringifying it and
return only a pure primitive plan or private identity marker. Before the safe
error helper raises, every frame holding a live input DTO, state/source model,
title, URL, merged content, marker, partial bibliography/final content,
canonical payload, mutable accumulator, or raw exception has exited or cleared
and deleted the reference. There is no logging, printing, chaining, retained
debug value, or partial output.

On success, the returned DTO intentionally contains the complete preserved
merged content and the escaped title/URL projections. It must not reference any
complex input model, mutable input mapping/container, temporary plan,
accumulator, canonical bytes, or raw exception. Exact primitive string identity
may be reused; no identity-distinct copy promise exists.

From entry until return or error, all input objects and nested Pydantic
containers must remain externally unchanged. Mutation through `__dict__`,
`object.__setattr__`, slots, threads, callbacks, or signals is illegal input and
an explicit non-goal. The renderer itself never mutates any input and does not
detect, lock, copy around, or recover from malicious concurrent mutation.

## 13. Execution, retry, and side effects

The operation is synchronous and has no await or cancellation point; it freezes
no artificial `asyncio.CancelledError` behavior. It performs no LLM, Retriever,
Config, Provider, GPTResearcher, network, filesystem, environment, subprocess,
graph, checkpoint, persistence, database, logging, legacy, or external-service
operation.

There is no retry, fallback, repair, partial result, cache, registry, or mutable
module-global call state. Repeating a call with the same stable inputs returns
the same canonical result or a fresh fixed error with the same type and text,
at zero external cost.

## 14. Compact offline test matrix

Only `tests/test_academic_writing_references_renderer.py` is added. Tests use
local exact DTO fixtures and a small number of parameterized behavior matrices.
They perform no external work and do not invoke the legacy renderer.

The permanent suite covers:

1. exact two-name `__all__`, sole synchronous signature, strict/frozen/JSON
   output DTO, one real rendering success, nonmutation, and deterministic repeat
   in one behavior test rather than a symbol-only test;
2. compact DTO Python/JSON exactness for mapping, tuple/list/subclass, bool,
   null, missing/extra, literal, count, unique/order, content, and canonical
   limits;
3. one input/binding matrix for exact state/merged/Gate/disposition surfaces,
   both legal research-evidence field-set shapes, illegal provenance surfaces,
   approval, IDs, positions, the four named attempts, source membership,
   all-`ready`, missing, extra, reordered, repeated, and malformed values;
4. one citation-scale matrix for 64 per section, 65 rejection, 768 aggregate,
   and cross-section duplicates; it contains no independent 200-ID row;
5. one global-marker matrix for exact-prefix syntax, malformed exact-prefix
   tokens (unclosed/empty/invalid/nested/illegal-tail), unknown IDs, repeated
   markers, extra/missing/order mismatch, opaque near-miss text, and global-only
   validation;
6. one explicit redistribution vector that passes globally while proving the
   renderer cannot establish section ownership;
7. one structurally fabricated but mutually bound four-artifact vector that
   passes without an execution/authenticity claim;
8. one JSON-escaping/template golden matrix covering Markdown controls,
   parentheses, quotes, backslashes, CR/LF, tabs, NUL, Unicode, exact spaces,
   separators, prefix, no trailing LF, and byte-for-byte preservation of the
   original merged prefix;
9. one boundary test is the sole 200-ID success vector: it simultaneously
   proves 200 global unique IDs, at most 64 per section, at most 768 aggregate,
   public rendering success, bibliography 5,541,708, content 5,901,246, and
   canonical 8,596,240, and rejects only the direct invalid max-plus-one vector;
10. hostile exact input variants extend the input-error matrix and prove zero
    untrusted method/equality/repr/iterator/property/descriptor calls;
11. one bounded module-specific traceback/locals/closure inspection proves the
    fixed error, null cause/context, no partial result, and no retained input,
    metadata, content, accumulator, canonical payload, or raw exception without
    a reusable historical walker; and
12. strict state restoration proves duplicate URL rejection is inherited and
    the renderer performs no URL deduplication or first-wins substitution.

There is no permanent initial-red, module-missing, API-missing, AST-only,
symbol-only, copied historical walker/import guard, import-registry framework,
multiple file ordering, real service, or unrelated regression. Parameterized
rows are retained only when they map to a distinct acceptance item or concrete
behavior risk.

During implementation, run only the new test file until green. Final related
verification runs exactly once with the new test plus the existing 3.7, 3.9,
and 3.11 test files. No full academic-writing suite or order permutation is
required; any global-import dependency is a stop condition.

## 15. Explicit non-goals

Milestone 3.12 does not implement:

- replacement of `[[cite:id]]` with numeric markers, footnotes, links, or
  author-year citations;
- section-marker ownership proof or recovery of flat merged-text boundaries;
- claim verification, factual correction, evidence sufficiency, contradiction
  proof, citation placement correction, or provenance completion;
- review/Gate/disposition/merger authenticity, signatures, attestation, or
  common-execution proof;
- draft or section rewrite/regeneration, marker movement, source replacement,
  citation repair, bibliography inference, or metadata lookup;
- workflow composition/orchestration, FinalEditor, human-review UI, publishing,
  export, backend, frontend, graph, checkpoint, persistence, or package export;
- LLM, Retriever, scraper, network, external service, legacy renderer, retry,
  fallback, parallelism, Send, subgraph, streaming, or progress callback; or
- modification of any frozen milestone, new dependency, shared helper, or
  recovery from illegal call-duration mutation.

## 16. Mandatory stop conditions

Implementation stops for a revised approved specification if:

- either approved implementation file is insufficient or any existing/third
  file must change;
- any exact public DTO type/surface differs from the frozen contracts;
- complete approved-state restoration requires a private helper, reflection,
  dynamic import, `Any`, or operation on an untrusted instance;
- the renderer would need original section drafts or recovered section
  boundaries to make a claimed guarantee;
- a global-64 limit, URL/title sorting, URL deduplication, marker replacement,
  or provenance requirement is needed;
- the exact template or the 200-ID stable order is ambiguous;
- any of 5,541,708, 5,901,246, or 8,596,240 is false or not simultaneously
  reachable from the frozen public input contracts;
- fixed-error isolation cannot release inputs, metadata, content, partial
  output, canonical payloads, and raw ordinary exceptions; or
- correctness requires LLM, Retriever, network, graph, checkpoint, legacy,
  persistence, or another external operation.

No implementer may resolve a stop by weakening exact validation, claiming
artifact authenticity or section ownership, treating `ready` as factual or
publication approval, truncating metadata, dropping a cited source, changing
the template, silently narrowing to 64 global IDs, or expanding scope.

## 17. Draft approval checklist

- [ ] The synchronous deterministic off-graph References Renderer goal is approved.
- [ ] The exact two-added-file boundary and third-file stop condition are approved.
- [ ] Every existing state, DTO, graph, event, facade, checkpoint and export remains unchanged.
- [ ] The narrow supersession of only future deterministic references rendering is approved.
- [ ] The exact two-name `__all__` and sole synchronous signature are approved.
- [ ] The five-field strict/frozen/JSON output DTO is approved.
- [ ] Exact Python/JSON mapping, tuple, primitive, subclass and bool rules are approved.
- [ ] Section IDs, reference IDs, attempt, content and canonical limits are approved.
- [ ] All four exact public inputs are mandatory.
- [ ] Complete approved-state static restoration and inherited URL uniqueness are approved.
- [ ] Research evidence permits only the complete field set or the exact provenance-omitted field set.
- [ ] Provenance omission requires an exact empty tuple value; both legal empty shapes continue without origin inference.
- [ ] Provenance surface corruption fails fixedly without introducing a provenance-backed citation gate.
- [ ] Merged, Gate and disposition static restoration uses only public DTO types.
- [ ] Exact outline, section and position binding across all inputs is approved.
- [ ] State-outline, merged, Gate and disposition attempts are each exact integer one; state has no top-level attempt.
- [ ] Overall and every section disposition must be exact ready.
- [ ] Ready is explicitly only mechanical routing, not factual or publication approval.
- [ ] Structurally fabricated artifacts and absent common-origin proof are approved limitations.
- [ ] One through 64 IDs per section and 768 aggregate occurrences are approved.
- [ ] The global unique-ID maximum is 200, not 64.
- [ ] The legal 200-ID ready vector must be accepted.
- [ ] Gate section-order flattening and global stable first-wins are approved.
- [ ] No source, URL or title sorting may replace Gate order.
- [ ] Inline citation markers remain unchanged.
- [ ] The exact-prefix global marker parser and opaque non-prefix text rule are approved.
- [ ] Only malformed exact-prefix tokens fail; non-prefix citation-like text remains opaque.
- [ ] Global marker order must equal the Gate global first-wins order.
- [ ] Section ownership cannot be recovered or claimed.
- [ ] The exact 17-character References prefix is approved.
- [ ] The four-space single-line reference template is approved.
- [ ] Source ID, title and URL JSON string projections are approved.
- [ ] Exact LF joining and no trailing LF are approved.
- [ ] Markdown controls, parentheses, CR/LF, backslashes, quotes and control characters remain opaque.
- [ ] Legacy add_references is prohibited.
- [ ] Duplicate URL rejection is inherited, not a new renderer gate.
- [ ] The bibliography maximum is exactly 5,541,708 code points.
- [ ] The rendered-content maximum is exactly 5,901,246 code points.
- [ ] The corrected canonical DTO maximum is exactly 8,596,240 UTF-8 bytes.
- [ ] The prior 8,595,876 assumption is explicitly superseded by structural reachability.
- [ ] The maximum vector is simultaneously reachable without an origin claim.
- [ ] Max-plus-one is tested only by direct invalid DTO construction.
- [ ] The trusted static Pydantic surface and no-untrusted-operation rule are approved.
- [ ] Inputs reduce to a pure primitive plan before rendering or fixed failure.
- [ ] The one private fixed error type/text and no-partial-result rule are approved.
- [ ] Fixed errors contain no dynamic data and have null cause/context.
- [ ] Input, metadata, content, accumulator and raw-exception reachability limits are approved.
- [ ] The function never mutates inputs; malicious concurrent mutation is a non-goal.
- [ ] Synchronous execution freezes no artificial cancellation contract.
- [ ] No LLM, Retriever, Config, Provider, network, filesystem or external work exists.
- [ ] Retry is caller-controlled, deterministic and has zero external cost.
- [ ] The compact parameterized test matrix contains no prohibited low-value framework.
- [ ] All explicit non-goals and mandatory stop conditions are approved.
- [x] This specification received explicit approval before implementation began.

## 18. Implementation acceptance checklist

- [ ] Only the exact two approved new files were added.
- [ ] No existing production, test, specification, initializer, dependency or lock file changed.
- [ ] `__all__` contains exactly the two approved names in order.
- [ ] The sole public function has the exact synchronous four-input signature.
- [ ] WorkflowReferencedDraft has exactly the five approved fields.
- [ ] The DTO is strict, frozen, extra-forbid and JSON-compatible.
- [ ] Python and JSON exactness rejects wrong mappings, containers, subclasses and coercions.
- [ ] Bool, null, missing fields and extra fields reject.
- [ ] Section IDs are exact ordered one-through-twelve identities.
- [ ] Reference source IDs are exact, unique and bounded one through 200.
- [ ] Attempt is exact integer one and content is nonblank and bounded.
- [ ] DTO canonical validation enforces the exact byte limit.
- [ ] State input is exact and uses the complete trusted static surface.
- [ ] Canonical state restoration proves outline-approved/completed/approve.
- [ ] Research evidence fields-set is exact complete or exact provenance-omitted only.
- [ ] A provenance-omitted surface has an exact empty tuple value; both legal empty shapes proceed identically.
- [ ] Null, wrong, missing or extra provenance state fails without creating a provenance citation gate.
- [ ] State source order, IDs, title/URL bounds and URL uniqueness are inherited exactly.
- [ ] Merged input is exact with the trusted four-field surface and 359,538 bound.
- [ ] Gate input is exact with the trusted four-field surface and full 3.9 DTO validation.
- [ ] Disposition input is exact with the trusted five-field surface and full 3.11 DTO validation.
- [ ] No input instance method, equality, repr, iterator, property or descriptor executes.
- [ ] Outline IDs match exactly across all four inputs.
- [ ] Section IDs and positions match exactly across all four inputs.
- [ ] State outline, merged, Gate and disposition attempts are each exact integer one.
- [ ] Overall and every section disposition are exact ready.
- [ ] Every Gate ID names one exact final state source.
- [ ] No provenance requirement or global-64 gate was introduced.
- [ ] Per-section 64, aggregate 768 and global unique 200 bounds are exact.
- [ ] The structurally legal 200-ID ready vector succeeds.
- [ ] Gate flattening and global stable first-wins produce the exact reference tuple.
- [ ] Marker scan recognizes only the frozen exact prefix syntax.
- [ ] Unclosed, empty, invalid, nested, illegal-tail and unknown exact-prefix tokens fail fixedly.
- [ ] Non-prefix citation-like text is opaque, preserved and absent from the marker sequence.
- [ ] Repeated markers retain first order; extra, missing and reordered global IDs fail.
- [ ] A globally matching section-redistributed vector passes without ownership claim.
- [ ] A legal fabricated artifact set passes without authenticity claim.
- [ ] The original merged content is preserved code point for code point.
- [ ] The exact prefix, line template, spaces, labels and LF rules are reproduced.
- [ ] Source ID, title and URL use the exact frozen JSON string projections.
- [ ] Markdown controls, parentheses, Unicode, CR/LF, quotes, backslashes and NUL are safe opaque text.
- [ ] No URL/title deduplication, sorting, truncation, link interpolation or metadata substitution occurs.
- [ ] The output reference_source_ids exactly exposes bibliography-number order.
- [ ] The output contains no review, verdict, issue, rationale, evidence block or mutable mapping.
- [ ] Canonical restoration returns one exact trusted result DTO.
- [ ] Bibliography 5,541,708 and content 5,901,246 are reached exactly.
- [ ] Canonical 8,596,240 bytes is reached exactly by one simultaneous legal vector.
- [ ] The simultaneous maximum vector is the sole 200-ID success case and also proves per-section and aggregate bounds.
- [ ] Direct-invalid 8,596,241/max-content-plus-one rejects; no public path is fabricated.
- [ ] Any late failure returns no partial bibliography or default DTO.
- [ ] The fixed private error has exact type/text and null cause/context.
- [ ] Fixed-error frames retain no complete input, metadata, content, partial result or raw exception.
- [ ] Hostile dynamic-access counters remain zero in the shared error matrix.
- [ ] The function mutates no input object or nested mapping/container.
- [ ] Repeated stable calls are deterministic and perform zero external work.
- [ ] Production is synchronous and creates no cancellation machinery or async object.
- [ ] Production performs no LLM, Config, Provider, Retriever, network, filesystem or legacy call.
- [ ] Production invokes no composer, FinalEditor, UI, publish, graph or persistence path.
- [ ] Tests contain no initial-red, AST-only, symbol-only, copied walker/import guard or multi-order matrix.
- [ ] Every retained test maps to an acceptance item or concrete behavior risk.
- [ ] The focused new test file passes offline.
- [ ] The single approved 3.7+3.9+3.11+3.12 related regression passes.
- [ ] `git diff --check` passes and staging remains empty.
- [ ] Status shows only the approved implementation files after implementation.
- [ ] No network, dependency installation, staging or commit occurred.
