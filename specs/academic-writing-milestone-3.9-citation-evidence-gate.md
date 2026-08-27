# Academic Writing Milestone 3.9: Off-Graph Deterministic Citation Evidence Gate

Status: **Approved and frozen**

规范已批准并冻结，仅授权冻结边界内实施；Implementation acceptance在实施完成并验证前保持未勾选。

## 1. Goal and exact proof boundary

Milestone 3.9 adds one synchronous, deterministic, off-graph gate between the
frozen Milestone 3.6 sequence and the frozen Milestone 3.7 merger:

```text
AcademicWorkflowState(outline_approved/completed, decision=approve)
        + exact tuple[WorkflowSectionDraft, ...]
                              |
                              v
                 gate_citation_evidence()
                              |
                              v
          WorkflowCitationEvidenceGateResult
                              |
                              v
                caller may invoke merge_sections()
```

The gate proves exactly and only:

1. every approved outline section has one structurally trusted draft in the
   exact approved section order;
2. every draft contains at least one mechanically valid citation marker;
3. every cited `source_id` exists in the restored state's final evidence
   sources;
4. every cited `source_id` has one corresponding provenance entry; and
5. that entry contains at least one exact, nonblank bounded evidence block.

The gate does not prove or claim that a sentence or claim is supported by the
block, that evidence is complete or sufficient, that evidence is consistent,
that a marker is placed beside the right prose, that a draft came from the 3.5
writer, or that the source was admitted to that writer's target-specific
prompt. It performs no claim extraction, entailment, contradiction, semantic
similarity, confidence, correction, or natural-language attribution review.

`WorkflowEvidenceProvenance` is a deterministic source-to-bounded-text link,
not a semantic truth or claim-support judgment. Absence of a provenance entry
means unavailable/unrecorded under 3.8, not unsupported or false.

## 2. Normative baseline and narrow supersession

The direct frozen baselines are:

```text
specs/academic-writing-milestone-3.5-section-writer-adapter.md
specs/academic-writing-milestone-3.6-section-writer-sequence.md
specs/academic-writing-milestone-3.8-evidence-provenance-index.md
```

Milestone 3.9 supersedes only their roadmap/non-goal reservation of a future
deterministic citation/provenance gate. Every existing state, DTO, writer,
sequence, merger, graph, event, checkpoint, facade, package export, prompt,
allowlist, failure, cancellation, and legacy behavior remains unchanged.

In particular, 3.5 still accepts marker-free content, 3.6 still returns it,
and 3.7 retains its own syntax/membership validation. A new caller explicitly
choosing this gate receives the stricter all-sections-cited requirement. That
composition does not retroactively alter any frozen producer or merger.

## 3. Exact future implementation boundary

After explicit approval, implementation may add exactly:

```text
gpt_researcher/workflows/academic_writing/citation_evidence_gate.py
tests/test_academic_writing_citation_evidence_gate.py
```

No existing file may change. In particular, implementation must not modify
`state.py`, `section_writer.py`, `section_writer_sequence.py`,
`section_merger.py`, `graph.py`, `nodes.py`, `events`, `adapters.py`, a package
initializer, a frozen specification, an existing test, a dependency, lock
file, backend, frontend, facade, checkpoint, or legacy entry point.

The production module may import only standard-library facilities, Pydantic,
and the following exact types from
`gpt_researcher.workflows.academic_writing.state`. The two signature types keep
their original names; the nine nested DTOs use these exact private aliases:

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
    WorkflowSectionDraft,
    WorkflowTopicPlan as _WorkflowTopicPlan,
)
```

These imports are ordinary eager imports of the already-loaded DTO-definition
module and must perform no compilation, I/O, network, external-component, or
registry work. Type acquisition through annotations, `model_fields`, `typing`
reflection, dynamic import, or `Any` is forbidden. Private aliases do not add
to `__all__` or the public surface. The module does not import either writer,
the sequence, the merger, Config, an LLM helper, GPTResearcher, Retriever,
Provider, or legacy report code.

If these two added files are insufficient, implementation must stop for a new
approved specification.

## 4. Complete public surface and synchronous signature

The complete module public surface is exactly:

```python
__all__ = (
    "WorkflowCitationEvidenceGateResult",
    "gate_citation_evidence",
)
```

Every other introduced definition has a leading underscore. No package
initializer re-exports either name.

The sole operation is exactly:

```python
def gate_citation_evidence(
    state: AcademicWorkflowState,
    drafts: tuple[WorkflowSectionDraft, ...],
) -> WorkflowCitationEvidenceGateResult: ...
```

It is an ordinary synchronous function. There is no class, constructor,
Protocol, callback, iterator, generator, async function, task, thread,
subprocess, context manager, or injected external component.

## 5. Strict module-local result DTO

### 5.1 Exact fields

```python
class WorkflowCitationEvidenceGateResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    outline_id: Literal["outline:000001"]
    section_ids: tuple[str, ...]
    cited_source_ids_by_section: tuple[tuple[str, ...], ...]
    attempt: Literal[1]
```

No state field or existing DTO is added or changed. The positional binding is
exact: `cited_source_ids_by_section[index]` belongs to
`section_ids[index]`. Every inner tuple is nonempty and is the stable unique
source-ID sequence from that section's marker order.

### 5.2 Exact Python and JSON shapes

Direct Python construction accepts only the DTO's exact instance or an exact
built-in `dict` containing exactly the four fields. `outline_id` and every ID
must have exact type `str`; `attempt` must have exact type `int` equal to `1`,
rejecting bool and every other value. `section_ids`, the outer citation
container, and every inner citation container must be exact built-in tuples.
Tuple/list/string/mapping subclasses, lists in Python mode, iterators,
generators, bytes, null, bool, floats, coercible values, missing fields, and
extra fields reject.

JSON input is accepted only through `model_validate_json()`. JSON arrays are
copied to exact tuples through explicit mode-aware validators. JSON booleans,
null, wrong containers, wrong members, missing fields, and extra fields reject.

The DTO requires:

- one through twelve section IDs;
- `section_ids[index] == f"section:{index + 1:06d}"`;
- the outer citation tuple length equals the section-ID tuple length;
- one through 64 cited IDs per section;
- exact uniqueness within each inner tuple; and
- every cited ID equals `f"evidence-source:{order:06d}"` for an integer order
  from 1 through 200.

The DTO alone validates shape and bounds. Only `gate_citation_evidence()` binds
those IDs to one approved state, one draft tuple, and nonempty provenance.

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

Construction requires exact recursive JSON value/type equality, exact model
equality, and exact canonical-byte equality after strict JSON restoration.

## 6. Exact mechanical output bounds

The section bound is the existing 3.6 operational outline bound: one through
twelve. The evidence-source bound remains 200. The tighter successful citation
bound is 64 per section because 3.8 permits at most 64 provenance entries and
every output citation must name one of them. Therefore:

```text
per-section unique cited IDs <= 64 <= 200 sources
aggregate cited-ID occurrences <= 12 * 64 = 768
```

All output strings have fixed ASCII identities: outline ID 14 code points,
section ID 14, and evidence-source ID 22. For 12 sections each citing all 64
provenance IDs, each quoted source ID occupies 24 canonical code points, each
inner array occupies `2 + (64 * 24) + 63 = 1,601`, the outer citation array
occupies `2 + (12 * 1,601) + 11 = 19,225`, and the section-ID array occupies
`2 + (12 * 16) + 11 = 205`. Fixed scalar values, keys, colons, commas, and
outer braces contribute 89 more code points. The exact maximum is therefore:

```text
19,225 + 205 + 89 = 19,519 canonical code points = 19,519 UTF-8 bytes
```

The vector is reachable at this public gate: use a strict approved state with
12 sections, 64 final sources, 64 one-block provenance entries, and 12 exact
drafts whose content lists those 64 markers in order. Each body is only 1,984
code points. This vector does not claim the drafts were actually generated by
3.5; that non-provenance is an explicit scope boundary.

19,520 is unreachable from legal DTO or successful gate output because all
string identities and container counts are closed above. It is a derived
invariant, not a required artificial public-path max-plus-one test.

## 7. Trusted static extraction surface

### 7.1 No operation on untrusted instances

`state`, every nested state model, every draft, and every provenance value are
untrusted even when their exact type matches. Before trust is established, no
path calls an instance method, equality, `repr`, `str`, dynamic `getattr`,
property, descriptor, custom iterator, or model serializer on them.

The only object reads use `object.__getattribute__` for the Pydantic 2.13.4
slots verified by Milestone 3.6 and reverified at implementation time:

```text
__dict__
__pydantic_fields_set__
__pydantic_extra__
__pydantic_private__
```

The first two must be exact built-in `dict`/`set`; extra and private must be
`None`. Exact built-in tuples are read only by `tuple.__len__` and
`tuple.__getitem__`; exact dictionaries and sets use only trusted built-in
operations after key/member types are proved exact. Exact primitive types are
proved before comparison or built-in string operations.

### 7.2 Complete state whitelist

The module freezes one private exact-type/ordered-field table for the complete
candidate approved-state tree. `AcademicWorkflowState` is the top-level
signature type; the other rows name the exact private aliases frozen in
Section 3:

```text
AcademicWorkflowState:
  schema_version, workflow_id, thread_id, run_id, phase, status, request,
  topic_plan, research_evidence, outline, outline_decision, errors, events
AcademicWorkflowRequest:
  workflow_mode, workflow_id, thread_id, run_id, query, report_type,
  report_source, tone, language, source_urls, document_urls, query_domains,
  max_search_results
WorkflowTopicPlan:
  topic_plan_id, workflow_id, run_id, attempt, research_topic,
  research_questions
WorkflowResearchEvidence:
  evidence_id, topic_plan_id, attempt, context_blocks, sources, provenance
WorkflowEvidenceSource:
  source_id, order, title, url, candidate_id
WorkflowEvidenceProvenance:
  source_id, evidence_blocks
WorkflowOutline:
  outline_id, evidence_id, attempt, title, sections
WorkflowOutlineSection:
  section_id, order, title, brief
WorkflowOutlineDecisionRecord:
  decision_id, schema_version, workflow_id, thread_id, run_id, outline_id,
  outline_digest, decision, actor_assertion, attempt
WorkflowEvent:
  event_id, order, event_type, node_id, attempt
```

Every `__dict__` key tuple must equal its row in order, after every key is
proved exact `str`. Candidate approved state requires `errors` to be an exact
empty tuple, so `WorkflowError` is unreachable and is deliberately absent from
the nine-type nested DTO import whitelist. Every other fields-set must be the
exact set of its row, with exactly one mechanical exception for
`WorkflowResearchEvidence`:

- `__dict__` always has the exact ordered row above, including a
  `provenance` value whose type is exact `tuple`;
- `__pydantic_fields_set__` may be either the exact full row set or the exact
  row set minus `provenance`;
- when the fields-set omits `provenance`, the exact tuple value must be empty;
  a nonempty value is a corrupt-object fixed failure; and
- a full fields-set with an empty tuple and an omitted-provenance fields-set
  with an empty tuple reach the same empty-provenance fixed failure.

The gate does not infer, record, or claim how either mechanical shape was
created. Milestone 3.8 owns old-checkpoint compatibility; this gate identifies
no historical origin.

The extractor recursively proves every declared model, exact tuple, exact
primitive, optional `None`, and integer type, and emits only a fresh strict
JSON primitive tree. It never follows undeclared data. That tree is canonically
encoded; all live input references are deleted; and only then is a fresh
trusted `AcademicWorkflowState` restored and subjected to its normal strict
model and canonical equality checks. Instance methods may be used only on
fresh trusted objects created from those canonical bytes.

### 7.3 Two-stage exact draft extraction

After `type(drafts) is tuple`, members are accessed by index. Each member must
have exact type `WorkflowSectionDraft` and the frozen four-slot surface:

```text
__dict__ exact keys: outline_id, section_id, attempt, content
fields-set exact members: outline_id, section_id, attempt, content
extra: None
private: None
```

Draft reading has exactly two phases.

**Stage A -- metadata only.** The extractor verifies the exact type, slots,
exact `dict`/`set`, exact four keys/fields-set, and `None` extra/private state.
It reads only `outline_id`, `section_id`, and `attempt`; the first two must be
exact strings and the last must be exact integer one, rejecting `bool`. It
proves that the exact `content` key exists but does not fetch, copy, compare,
validate, stringify, represent, or otherwise touch its value. It completes
draft count, outline-ID, section-ID, order, uniqueness, and attempt binding and
saves only a pure exact-primitive metadata plan. Every member local is deleted;
the exact input tuple remains only so Stage B can index it later.

State sources and provenance are then statically validated and projected. If
provenance is empty under either allowed fields-set shape, the public path
deletes the trusted state, original `drafts`, metadata plan, and every
temporary, then invokes a no-input safe helper that raises the fixed error.
No draft `content` value has been read and the marker parser has not run.

**Stage B -- content only.** This phase is reachable only after nonempty
provenance has passed. Each indexed member is again required to have the exact
type and frozen four-slot surface. Only then does trusted `dict.__getitem__`
read `content` exactly once. Its type must be exact `str`; a canonical JSON
string encode/decode using `ensure_ascii=False` creates a fresh exact-string
copy, which must be nonblank, at most 24,576 Python code points, and unchanged
by trusted `str.strip`. The sole frozen choice is direct construction of a pure
marker-scan plan from the Stage-A metadata and copied content strings; Stage B
does not reconstruct `WorkflowSectionDraft`. Each live member is deleted, and
the original tuple is deleted before marker parsing. A subclass, shadowed
method, extra/private state, hostile value, string subclass, bool attempt,
invalid content, or malformed internal surface fails without dynamic code
execution.

## 8. Exact preflight and guard order

Validation and the two draft-read phases occur in exactly this order:

1. require and statically extract an exact `AcademicWorkflowState`;
2. canonically restore the complete trusted state;
3. require `phase == "outline_approved"` and `status == "completed"`;
4. use strict restoration as the sole proof of `decision=approve`, decision
   identity/digest, and artifact references;
5. require `type(drafts) is tuple`;
6. require the outline and draft counts to match and be between 1 and 12;
7. run Stage A on every draft without reading any `content` value;
8. require exact outline ID, section ID, tuple order, uniqueness, and attempt
   binding against the approved outline, producing the pure metadata plan;
9. validate and project final source IDs and provenance IDs from the trusted
   evidence, including exact association and at least one nonblank exact block
   per present entry; an empty provenance tuple under either mechanical
   fields-set shape fails here after state, `drafts`, and the metadata plan are
   deleted; then
10. run Stage B, recheck each exact draft surface, read and copy each exact
    `content`, delete every member and `drafts`, and produce the sole direct
    pure marker-scan plan; and
11. scan copied content in section order.

Before step 10 there has been no read, copy, comparison, representation, or
validation of any draft `content`, and no marker-parser call. Before step 11,
the state snapshot, evidence blocks, source/provenance DTOs, and original draft
tuple/members have been released. The scan plan contains only the exact
approved outline ID, exact section IDs, fresh exact content strings, the exact
final source-ID tuple, and exact provenance-ID tuple.

## 9. Exact citation algorithm

For each trusted exact content string in section order:

1. reject the literal substring `://`;
2. scan left to right for the literal prefix `[[cite:` and the next literal
   suffix `]]`;
3. reject a missing suffix, empty value, nested bracket text, or any `[`/`]`
   outside a successfully parsed marker;
4. require the extracted value to have exact type `str` and equal one final
   state source ID code point for code point;
5. require the same ID to occur in the provenance-ID tuple whose validated
   entry contains at least one nonblank exact block;
6. append the first occurrence to the current section's output tuple and ignore
   later repetitions for tuple construction only; and
7. reject the section if its stable unique tuple is empty.

No marker or body character is normalized, inserted, removed, repaired,
reordered, or rewritten. `www` text, bare domains, DOI text, email-like text,
and unmarked natural-language attribution remain unrecognized. Cross-section
reuse of one source is allowed; stable uniqueness is per section.

## 10. Explicit 3.5 allowlist limitation

The gate does not reconstruct or claim the target-specific 3.5 projected
source allowlist. It does not import a private 3.5/3.6 helper and does not copy
a third version of the 3.5 query/context/outline/source/prompt algorithm.

Consequently, a marker may pass this gate when its exact ID exists in final
state sources and nonempty provenance but was excluded from a particular 3.5
prompt by the 24-source or 65,536-message cutoff. This is intentional and must
be visible in documentation and tests. It does not prove 3.5 generation or
weaken the 3.5 writer/3.7 merger, both of which retain their own allowlists.

One frozen test uses a provenance-backed source 25: the gate accepts its marker
although the public 3.5 count cutoff would exclude it. No 3.5 private helper,
prompt reconstruction, golden duplication, or assertion that it was writer
generated is permitted.

## 11. Deterministic output construction

After all sections pass, construct exactly:

```python
WorkflowCitationEvidenceGateResult(
    outline_id=approved_outline_id,
    section_ids=approved_section_ids,
    cited_source_ids_by_section=stable_citations_by_section,
    attempt=1,
)
```

The gate performs the complete canonical round trip from Section 5.3. It
returns only the restored exact DTO. It does not return evidence blocks,
content, marker positions, booleans, scores, reasons, diagnostics, partial
results, merged text, URLs, titles, claims, or corrections.

## 12. Fixed failure and information safety

The module defines exactly one private error:

```python
class _CitationEvidenceGateError(RuntimeError):
    pass
```

Its sole text is:

```text
citation evidence gate failed
```

Every ordinary type, snapshot, approval, draft, identity, order, source,
provenance, marker, coverage, DTO, canonicalization, or internal failure becomes
that one error. It contains no dynamic section, source, marker, content,
evidence block, validation detail, or underlying exception text.

All sensitive work occurs in isolated synchronous helpers that catch ordinary
`Exception` without stringifying it and return only a pure primitive plan,
canonical result bytes, or a private identity marker. Before a marker reaches
the finishing helper, frames holding `state`, `drafts`, any original or trusted
model, content, marker substring, evidence block, source/provenance collection,
accumulated ID list, primitive plan, result DTO, canonical bytes, or ordinary
exception have exited and released those values.

The sole error finisher has never received a sensitive value. The outward
error has `__cause__ is None`, `__context__ is None`, and
`__suppress_context__ is False`. Its args, attributes, traceback locals,
closure cells, and module globals cannot reach complete inputs, draft content,
evidence blocks, partial output, or raw ordinary exceptions. No error is
logged, printed, chained with `from`, preserved, or returned.

There is no partial result. A failure in the final section returns neither the
earlier section tuples nor an empty/default DTO.

## 13. Synchronous execution and cancellation

The complete operation is synchronous and contains no await, coroutine, task,
callback, thread, process, generator, external component, or interruption
point controlled by this module. It therefore freezes no artificial
`asyncio.CancelledError` contract. Cancellation of an enclosing async caller
cannot be observed by Python while this synchronous call is executing.

`KeyboardInterrupt`, `SystemExit`, and other `BaseException` behavior is not a
Milestone 3.9 contract. No ordinary failure handler catches `BaseException`.

## 14. Side effects and non-goals

Import and execution perform no Config, LLM, Retriever, Provider, GPTResearcher,
legacy report, network, filesystem, environment, subprocess, graph,
checkpoint, database, persistence, logging, or external-service work.

Milestone 3.9 does not implement:

- CitationReviewer, claim extraction, entailment, contradiction, evidence
  sufficiency, confidence, citation placement review, correction, or rewrite;
- natural-language attribution recognition or broader URL detection;
- 3.5 prompt/allowlist reconstruction or proof of writer provenance;
- draft generation, regeneration, retry, fallback, repair, or attempt 2+;
- merge, references, bibliography, citation renumbering, FinalEditor, report
  composition, export, or persistence;
- graph, node, phase, status, event, error code, facade, checkpoint, package
  export, backend, frontend, or product integration;
- parallelism, Send, subgraphs, tasks, streaming, progress, idempotency, or
  exactly-once guarantees; or
- a new dependency, shared helper, or modification of Milestones 3.5-3.8.

Semantic claim-support review remains a future LLM/NLI/human-review milestone.
Even such a reviewer may report a bounded judgment; it must not claim formal
proof or ground truth merely because an LLM returned a verdict.

## 15. Compact offline test matrix

The sole new test file uses strict local fixtures and parameterized behavior
matrices:

1. public `__all__`, exact function signature, DTO behavior, and a real gate
   success are checked together, not by symbol-existence-only tests;
2. direct Python/JSON strict, frozen, extra-forbid, tuple/list/subclass, exact
   integer-one, ID grammar, 1/12 section, 1/64 citation, positional-length,
   uniqueness, canonical equality, and 19,519 maximum behavior share compact
   DTO/boundary matrices;
3. exact state, approval, all nine privately aliased nested DTO types, and
   nonmutation failures share one preflight matrix; the cases exercise real
   nested exact-type validation rather than symbol or AST inspection;
4. draft tuple/count/order/identity/attempt Stage-A failures, the two allowed
   provenance fields-set shapes, and omitted-fields-set/nonempty corruption
   share the existing priority matrix. Full-set empty and omitted-set empty
   provenance produce the same fixed failure. A hostile/corrupt `content`
   sentinel is present in those empty-provenance cases; its dynamic counters,
   the instrumented Stage-B content-read count, and marker-parser count all
   remain zero;
5. source absent, provenance absent, empty/corrupt block, and valid linked block
   behavior share one source/provenance matrix;
6. marker-free, valid, repeated, stable-order, malformed, unknown, stray
   bracket, and literal `://` behavior share one marker matrix;
7. the source-25 vector proves the explicit absence of a 3.5 projected-
   allowlist claim without importing or copying its algorithm;
8. the reachable 12-by-64 vector produces exactly 768 cited-ID occurrences and
   19,519 canonical bytes; 19,520 receives no artificial public-path test;
9. corrupted exact state/draft/provenance values with hostile shadow, property,
   descriptor, iterator, equality, `repr`, content, and string-subclass
   sentinels extend the existing error matrix and record zero dynamic calls;
   nonempty provenance also proves Stage B uses the direct marker plan and no
   trusted-draft reconstruction; and
10. one targeted traceback/locals/closure inspection proves fixed error safety,
    no partial result, and no retained input/content/evidence block without a
    reusable historical walker.

The revisions in items 3, 4, and 9 extend those existing parameter matrices;
they add no test function, import guard, or safety framework.

Tests perform no LLM, Config, Provider, Retriever, GPTResearcher, network,
legacy, merger, graph, checkpoint, subprocess, persistence, or filesystem
operation. They contain no permanent initial-red, AST-only, symbol-only,
import-registry, duplicate safety framework, multiple file order, or unrelated
historical regression. Every test maps to a behavior above or an implementation
acceptance item.

During implementation, run only the new test file until green. Final related
verification runs exactly once with the new test plus the existing 3.8 state,
3.6 sequence, and 3.5 section-writer tests. No full academic-writing suite or
multiple ordering is required; a global-import dependency is a stop condition.

## 16. Mandatory stop conditions

Implementation must stop for a revised approved specification if:

- either approved implementation file is insufficient or any existing/third
  file must change;
- state, an existing DTO, graph, event, facade, checkpoint, package export,
  writer, sequence, merger, dependency, or legacy path must change;
- exact static extraction requires invoking an untrusted instance operation;
- any of the exact nine nested DTO names/private aliases is unavailable or
  type acquisition would require reflection, dynamic import, or `Any`;
- the verified Pydantic four-slot shape differs from 2.13.4 or cannot be used
  without guessing another surface;
- complete strict state restoration cannot precede draft content scanning;
- empty provenance cannot fail before any draft-content read or marker-parser
  call;
- source/provenance association cannot be proved without evidence inference;
- correctness would require reconstructing a third 3.5 allowlist algorithm;
- the 768 aggregate or 19,519 canonical maximum is false or unreachable;
- fixed error isolation cannot release input, content, evidence blocks, partial
  IDs, DTO bytes, and raw ordinary exceptions;
- implementation requires async work, cancellation machinery, external calls,
  a new dependency, shared helper, or persistent state; or
- this Draft conflicts internally with an unchanged frozen contract.

No implementer may resolve a stop by weakening validation, treating empty
provenance as verification, inferring support, returning partial output,
hiding data in an existing DTO, importing a private helper, or expanding scope.

## 17. Draft approval checklist

- [ ] The deterministic off-graph citation-evidence gate goal is approved.
- [ ] The exact two-added-file boundary and third-file stop condition are approved.
- [ ] Every existing file, DTO, state shape, graph, event, facade and export remains unchanged.
- [ ] The gate is placed after `write_sections` and before caller-invoked `merge_sections`.
- [ ] The exact two-name `__all__` and sole synchronous function signature are approved.
- [ ] The strict/frozen/JSON result DTO and exact four fields are approved.
- [ ] Positional section/citation tuple binding and stable per-section uniqueness are approved.
- [ ] Exact Python/JSON containers, primitives, attempt one and subclass rejection are approved.
- [ ] One-through-twelve section and one-through-64 per-section citation bounds are approved.
- [ ] The 768 aggregate cited-ID bound is approved.
- [ ] The reachable 19,519 canonical-byte/code-point maximum is approved.
- [ ] No artificial unreachable 19,520 public-path test is required.
- [ ] The gate's five exact mechanical proofs are approved.
- [ ] Every claim-support, sufficiency, consistency and placement conclusion is explicitly excluded.
- [ ] Draft provenance and complete provenance text are explicitly not claimed.
- [ ] Complete static state extraction uses only the frozen four-slot Pydantic surface.
- [ ] The exact nine nested DTO private aliases, field whitelist and empty-errors rule are approved.
- [ ] Annotation/model-fields/typing reflection, dynamic import and `Any` type acquisition are forbidden.
- [ ] The two provenance fields-set shapes are mechanical only and never identify historical origin.
- [ ] Untrusted models execute no method, equality, repr, property, descriptor or iterator code.
- [ ] Stage A reads only exact draft metadata and proves the `content` key without reading its value.
- [ ] Stage B alone reads exact content and builds the sole direct marker plan without draft reconstruction.
- [ ] Strict approved-state restoration remains the sole decision/reference proof.
- [ ] The complete preflight and guard order are approved.
- [ ] Either empty-provenance shape fails before any draft-content read or marker-parser call.
- [ ] Source and provenance structure is reduced to pure ID projections before scanning.
- [ ] Exact 3.5 marker syntax, malformed/bracket and literal `://` behavior are approved.
- [ ] Every section must contain at least one qualifying marker.
- [ ] Every marker must name both a final source and a nonempty provenance entry.
- [ ] Repeated markers and stable first-appearance order are approved.
- [ ] Natural-language attribution and broader URL recognition remain excluded.
- [ ] The gate intentionally does not reconstruct or prove the 3.5 projected allowlist.
- [ ] A provenance-backed source excluded from a 3.5 prompt may still pass this gate.
- [ ] The source-25 limitation vector is approved without a third allowlist copy.
- [ ] Output contains only outline/section/citation IDs and exact attempt one.
- [ ] The one private fixed error class/text and no-partial-result rule are approved.
- [ ] Fixed errors contain no dynamic data and have null cause/context.
- [ ] Input, content, evidence blocks, partial IDs and raw exceptions are unreachable from fixed errors.
- [ ] Synchronous execution freezes no artificial cancellation semantics.
- [ ] No LLM, Retriever, Config, Provider, network, legacy or external call exists.
- [ ] The compact parameterized test strategy contains no prohibited low-value framework.
- [ ] Every non-goal and mandatory stop condition is approved.
- [x] This specification received explicit approval before implementation began.

## 18. Implementation acceptance checklist

- [ ] Only the exact two approved files were added.
- [ ] No existing production, test, specification, initializer, dependency or lock file changed.
- [ ] The module exports exactly the two approved names in order.
- [ ] The public function and result DTO signatures/fields are exact.
- [ ] The DTO is strict, frozen, extra-forbid and JSON-compatible.
- [ ] Python and JSON inputs enforce exact mapping, tuple, nested tuple, string and integer types.
- [ ] Bool, coercion, subclasses, missing fields and extra fields reject.
- [ ] DTO section IDs, cited source IDs, counts, uniqueness and positional lengths are exact.
- [ ] DTO canonical recursive-type, model and byte equality pass.
- [ ] The exact reachable DTO/gate maximum is 19,519 bytes and 768 cited occurrences.
- [ ] Exact top-level state type and complete static extraction precede every other input operation.
- [ ] Every nested state model uses the frozen exact type/field/four-slot whitelist.
- [ ] The exact nine nested DTOs are acquired only through the frozen private direct-import aliases.
- [ ] No annotation, model-fields, typing reflection, dynamic import or `Any` supplies a DTO type.
- [ ] Both provenance fields-set shapes require an exact tuple; omission permits only the empty tuple.
- [ ] Full-set empty and omitted-set empty provenance have the same fixed failure and no origin claim.
- [ ] No untrusted instance method, equality, repr, iterator, property or descriptor executes.
- [ ] Canonical bytes restore one fresh exact approved state before draft scanning.
- [ ] Strict restoration proves approve, digest and artifact references without duplicate gates.
- [ ] Drafts must be an exact tuple matching the 1-12 approved outline count.
- [ ] Stage A validates metadata identity/order/attempt while never reading any content value.
- [ ] Outline ID, section IDs, order, uniqueness and attempt bind exactly.
- [ ] Empty provenance deletes state, drafts and metadata before a safe fixed failure.
- [ ] Hostile content plus empty provenance records zero dynamic, Stage-B read and marker-parser calls.
- [ ] Stage B rereads the static surface, copies exact content and builds only the direct marker plan.
- [ ] Final sources and provenance entries are structurally valid and correctly associated.
- [ ] Every qualifying provenance entry has at least one exact nonblank bounded block.
- [ ] Evidence blocks and live state objects are released before content scanning.
- [ ] Exact marker parsing matches 3.5 syntax and bracket/`://` rules.
- [ ] Marker-free content and every malformed marker fail without repair.
- [ ] Unknown source and source-without-provenance markers fail.
- [ ] Each section has at least one citation and repeated IDs deduplicate stably.
- [ ] Cross-section citation reuse is allowed and output position matches section position.
- [ ] The gate imports/copies no 3.5 allowlist helper or algorithm.
- [ ] The source-25 test visibly proves the limited non-allowlist claim.
- [ ] The result contains no content, blocks, URLs, claims, scores, diagnostics or partial values.
- [ ] Any final-section failure returns no earlier section result.
- [ ] The fixed private error has exact type/text and null cause/context.
- [ ] Fixed-error traceback/locals/closures retain no full input, content, block, partial ID or raw exception.
- [ ] Hostile dynamic-access counters remain zero in the existing error matrix.
- [ ] Production is synchronous and creates no coroutine, task, thread, callback or cancellation machinery.
- [ ] Production performs no LLM, Retriever, Config, Provider, network, graph, checkpoint or legacy call.
- [ ] No state, draft, provenance, outline, event or checkpoint input is mutated.
- [ ] Tests contain no initial-red, AST-only, symbol-only, duplicate walker/import guard or multi-order matrix.
- [ ] Every test maps to an acceptance item or concrete behavior risk.
- [ ] The focused new tests pass offline.
- [ ] The single approved related regression passes without full-suite or order permutations.
- [ ] `git diff --check` passes and staging remains empty.
- [ ] The worktree contains only the approved Draft before implementation.
- [ ] No network, dependency installation, staging or commit occurred.
