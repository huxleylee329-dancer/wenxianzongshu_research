# Academic Writing Milestone 3.14: Off-Graph Academic Review Handoff

Status: **Approved and frozen**

规范已批准并冻结，仅授权冻结边界内实施；Implementation acceptance在实施完成并验证前保持未勾选。

## 1. Goal and exact boundary

Milestone 3.14 adds one synchronous, deterministic, off-graph builder for a
human-review handoff. It accepts an exact approved workflow state and an exact
non-ready Milestone 3.13 composition, reruns the two public deterministic gates,
and projects only the cited source metadata and bounded provenance already held
by the state.

The handoff is an information package, not a new review. It performs no section
writing, LLM review, claim assessment, merge, reference rendering, graph work,
checkpoint work, persistence, export, or user-interface work.

```text
exact approved AcademicWorkflowState
                 +
exact non-ready WorkflowAcademicDraftComposition
                 |
                 v
 gate_citation_evidence(state, composition.drafts)
                 |
                 v
 compare recomputed Gate with the embedded Gate projection
                 |
                 v
 gate_citation_review_disposition(recomputed_gate, composition.reviews)
                 |
                 v
 compare recomputed Disposition with the embedded Disposition projection
                 |
                 v
 project cited source/provenance objects in global first-wins order
                 |
                 v
 WorkflowAcademicReviewHandoff
```

## 2. Frozen baselines and narrow supersession

The direct frozen baselines are:

```text
specs/academic-writing-milestone-3.8-evidence-provenance-index.md
specs/academic-writing-milestone-3.9-citation-evidence-gate.md
specs/academic-writing-milestone-3.10-citation-reviewer-adapter.md
specs/academic-writing-milestone-3.11-citation-review-disposition.md
specs/academic-writing-milestone-3.12-references-renderer.md
specs/academic-writing-milestone-3.13-academic-draft-composer.md
```

This milestone supersedes only their reservation of a future deterministic
human-review handoff. It changes no existing DTO, state, source/provenance,
Gate, Reviewer, Disposition, Composer, merge, render, graph, checkpoint, event,
facade, package-export, error, persistence, or external-service contract.

The builder calls the public 3.9 Gate and public 3.11 Disposition functions. It
does not import or copy either module's private helper. It never reruns the 3.10
Reviewer, SectionWriter, SectionWriterSequence, merger, or renderer.

## 3. Exact future implementation boundary

After explicit approval, implementation may add exactly:

```text
gpt_researcher/workflows/academic_writing/academic_review_handoff.py
tests/test_academic_writing_academic_review_handoff.py
```

No existing file may change. In particular, implementation must not modify
`state.py`, any Milestone 3.8-3.13 module or test, `graph.py`, nodes, events, a
facade, package initializer, frozen specification, dependency, lock file,
backend, frontend, checkpoint, persistence, export, or legacy path. If these
two added files are insufficient, implementation stops for a revised approved
specification.

## 4. Complete public surface

The complete public surface is:

```python
__all__ = (
    "WorkflowAcademicReviewHandoff",
    "build_academic_review_handoff",
)


class WorkflowAcademicReviewHandoff(BaseModel):
    composition: WorkflowAcademicDraftComposition
    cited_sources: tuple[WorkflowEvidenceSource, ...]
    cited_provenance: tuple[WorkflowEvidenceProvenance, ...]


def build_academic_review_handoff(
    state: AcademicWorkflowState,
    composition: WorkflowAcademicDraftComposition,
) -> WorkflowAcademicReviewHandoff: ...
```

There is no public Protocol, client, factory, builder class, overload, Union,
status enum, helper, serializer, exception, or alias. All implementation names
other than the two `__all__` members are private.

The function is synchronous. It has no cancellation contract and must not be
declared `async` or return an awaitable.

## 5. Strict three-field output DTO

### 5.1 Exact model and containers

`WorkflowAcademicReviewHandoff` is a Pydantic model with
`frozen=True`, `extra="forbid"`, and `strict=True`. It has exactly the three
fields shown in Section 4 and no status, attempt, schema version, audit field,
mapping, raw object, or redundant copy.

Python validation requires:

- an exact built-in `dict` when a mapping is supplied;
- exactly the three exact-string keys, with no missing or extra key;
- `type(composition) is WorkflowAcademicDraftComposition`;
- exact built-in tuples for `cited_sources` and `cited_provenance`;
- one through sixty-four exact `WorkflowEvidenceSource` members;
- one through sixty-four exact `WorkflowEvidenceProvenance` members; and
- equal tuple lengths, unique source IDs, and positionally identical source IDs.

JSON validation accepts exact JSON objects and arrays and normalizes the two
arrays to tuples. `null`, a Python list in Python mode, tuple/list subclasses,
model subclasses, bools, coercions, iterators, mappings other than exact dicts,
missing fields, and extra fields reject.

The DTO is JSON-compatible: Python tuple fields dump as JSON arrays, and a
canonical dump round-trips to the same model value. Identity is not serialized;
the builder-only identity guarantee in Section 5.3 does not apply to a JSON
round-trip.

### 5.2 Non-ready and cross-field shape

The embedded composition must be a valid non-ready 3.13 shape:

- `composition.disposition.disposition` is exactly
  `"needs_human_review"` or `"blocked"`;
- every value in `composition.disposition.section_dispositions` is a frozen
  3.11 literal and their frozen overall priority yields that overall value;
- `composition.merged_draft is None` and
  `composition.referenced_draft is None`;
- composition drafts, Gate, reviews, and Disposition contain one through
  twelve positionally bound sections; and
- the Gate's section-wise citations flatten to one through sixty-four global
  unique IDs under stable first-wins order.

An exact `ready` composition always rejects. Mixed later-artifact shapes reject.
The builder never converts ready into a handoff and never treats non-ready as an
execution failure.

The output source and provenance IDs must exactly equal the composition Gate's
global stable-first-wins citation plan. Each ID occurs exactly once in each
tuple, and the two tuples are positionally aligned with each other and that
plan.

### 5.3 Identity contract

On successful public builder return:

- `result.composition is composition` is true;
- every `result.cited_sources[i]` is the exact input-state source instance
  selected for the corresponding citation ID;
- every `result.cited_provenance[i]` is the exact input-state provenance
  instance selected for that ID; and
- the result retains no root state, uncited source/provenance object, temporary
  projection, recomputed Gate, or recomputed Disposition.

The result is intentionally allowed to retain the three output fields and all
of their nested output values. A success-path safety assertion must not claim
that these documented output identities or their readable metadata/evidence
blocks are unreachable.

## 6. Static trust boundary

### 6.1 No operation on an untrusted instance

Before any field comparison, membership, or subscription, every inspected
Pydantic instance must have the exact expected type and the frozen internal
surface:

- `object.__getattribute__(value, "__dict__")` is an exact built-in `dict`;
- `object.__getattribute__(value, "__pydantic_fields_set__")` is an exact
  built-in `set`;
- `object.__getattribute__(value, "__pydantic_extra__") is None`; and
- `object.__getattribute__(value, "__pydantic_private__") is None`.

Before equality, membership, subscription, `dict_keys`/set comparison, or a
new set/dict whose behavior depends on members, exact built-in iteration must
first prove that every mapping key and every fields-set member has
`type(member) is str`. A non-exact member fails immediately without executing
its equality, hash beyond the already-existing container operation, repr,
property, descriptor, dynamic attribute, or custom iterator.

After that proof, extraction may use only `object.__getattribute__`, exact
built-in `dict`/`set`/`tuple` operations, and exact primitive values. It must not
call an input model's `model_dump`, `model_validate`, `model_copy`, equality,
repr, iterator, property, descriptor, annotation, `model_fields`, dynamic
attribute, or any other instance method.

All exact DTO types required for static inspection may be imported directly
under private aliases from their owning frozen modules. No `Any`, annotation
reflection, typing reflection, dynamic import, or private upstream helper is
permitted. Those imports perform no Config, provider, I/O, network, or external
work and add no public surface.

### 6.2 Approved state and 3.8 provenance surface

The state must have exact type `AcademicWorkflowState` and the complete frozen
state surface. Static restoration must prove at least:

- `schema_version == "1"`;
- `phase == "outline_approved"` and `status == "completed"`;
- exact request/topic-plan/evidence/outline/decision/event/error DTO surfaces;
- the decision is exactly `"approve"`;
- state/request/topic-plan/evidence/outline identities are bound exactly;
- state outline attempt and every relevant nested attempt are exact `int` one;
- outline section count, order, IDs, and uniqueness satisfy the frozen state
  contract; and
- sources, provenance, orders, unique URLs, source IDs, block counts, and
  aggregate evidence characters satisfy the frozen 3.8 contract.

For `WorkflowResearchEvidence.__pydantic_fields_set__`, exactly two shapes are
allowed:

```text
A. {evidence_id, topic_plan_id, attempt, context_blocks, sources, provenance}
B. {evidence_id, topic_plan_id, attempt, context_blocks, sources}
```

For shape B, the exact internal `__dict__` must nevertheless contain the key
`"provenance"`, and its value must have exact type `tuple` and equal `()`.
Shape A may contain legal empty or non-empty provenance. Neither fields-set
shape is interpreted, recorded, or exposed as checkpoint history or legacy
origin.

Both legal empty-provenance shapes proceed to the deterministic Gate call and
then fail the handoff because no cited source can satisfy the frozen Gate. A
missing internal value, `null`, wrong container, non-empty provenance under
shape B, another missing fields-set member, or an extra fields-set member is a
fixed contract failure.

The Handoff adds no provenance-backed rule beyond the public Gate. Successful
construction necessarily has a non-empty provenance entry with at least one
non-empty bounded block for every cited ID because the recomputed Gate requires
it.

### 6.3 Composition surface

The exact input composition and all nested drafts, Gate result, reviews, and
Disposition are statically extracted under their frozen fields and types.
Extraction must revalidate:

- exact tuple sizes and exact nested DTO types;
- exact outline ID shared by state outline, drafts, Gate, reviews, and
  Disposition;
- exact section IDs, section count, and section order shared by state outline
  and all composition artifacts;
- exact per-section Gate citation tuples and review citation tuples;
- every relevant `attempt` as `type(value) is int and value == 1`;
- exact review verdict, issue subset/order, nonblank rationale, and rationale
  length;
- exact review-to-section positional binding;
- exact verdict-to-section-Disposition routing and overall priority;
- both later composition fields as exact `None`; and
- the global stable-first-wins citation count of one through sixty-four.

This static work creates a pure primitive plan and trusted exact tuples needed
by the public Gate and Disposition calls. It does not execute an untrusted
operation or rebuild the Composition that will be returned.

## 7. Exact deterministic algorithm

The public builder freezes this order:

1. Statically validate and project the exact approved state and exact non-ready
   composition without calling an input instance method.
2. Preserve the original composition reference solely for the eventual success
   field; prepare the trusted exact draft tuple from the verified composition
   surface.
3. Call the public `gate_citation_evidence(state, trusted_drafts)` exactly once.
4. Statically extract the exact recomputed Gate result and compare its complete
   pure-primitive projection with the complete pure-primitive projection of
   `composition.gate_result`.
5. On any Gate failure or mismatch, perform fixed safe failure; do not call
   Disposition and do not project output tuples.
6. Call the public
   `gate_citation_review_disposition(recomputed_gate, trusted_reviews)` exactly
   once.
7. Statically extract the exact recomputed Disposition and compare its complete
   pure-primitive projection with the complete pure-primitive projection of
   `composition.disposition`.
8. Require the recomputed result to be exactly `"needs_human_review"` or
   `"blocked"`; ready fails before any output projection.
9. Flatten the recomputed Gate citation tuples in approved section order and
   apply stable first-wins deduplication. Require one through sixty-four IDs.
10. From the already validated input state's source and provenance tuples,
    select exactly one object per planned ID. Preserve the plan order and the
    exact selected object identities. No loser, uncited item, fallback, URL
    match, title match, or textual-similarity match may contribute.
11. Construct one `WorkflowAcademicReviewHandoff` containing the original input
    composition identity and the two exact projected tuples.

Gate and Disposition comparisons operate only on exact trusted primitives and
exact built-in tuples. Pydantic/model equality is forbidden. There is no retry,
fallback, repair, partial Handoff, alternate route, or second deterministic
call.

## 8. Honest proof and non-proof boundary

A successful Handoff proves only that, during one externally unmodified call:

- the supplied state/drafts passed the public deterministic 3.9 Gate;
- the supplied reviews and recomputed Gate passed the public deterministic 3.11
  Disposition;
- the embedded Gate and Disposition pure-value projections matched those
  recomputed results;
- the routing result was non-ready;
- each cited ID had corresponding frozen source metadata and at least one
  non-empty bounded provenance block; and
- the two projected tuples follow the recomputed global citation order.

It does not prove that:

- the Reviewer, Composer, Gate, or Disposition was historically executed;
- a review was produced by an LLM or is authentic/correct;
- a claim is true or supported by an evidence block;
- evidence is sufficient, complete, current, non-contradictory, or correctly
  placed;
- provenance text is the full source text or has a trusted external origin;
- artifacts share a trustworthy historical origin merely because their values
  bind structurally; or
- the handoff is factually approved, safe to publish, or a completed human
  review.

Structurally legal fabricated reviews remain indistinguishable from equally
shaped real Reviewer outputs. `needs_human_review` and `blocked` are mechanical
routing labels, not factual judgments.

## 9. Concurrency, mutation, and lifecycle

From entry until return or exception, the caller must not mutate state,
composition, or any nested Pydantic mapping/slot/container through
`object.__setattr__`, `__dict__`, a backing container, another thread, task,
callback, or signal handler. Such mutation is illegal concurrent input and an
explicit non-goal. The builder does not detect, lock, copy races, or recover
from them. Normal synchronous execution is not itself a concurrent mutation.

The builder never modifies an input object, nested mapping, tuple, DTO, source,
provenance entry, block, or composition artifact.

On success, the returned Handoff intentionally owns the documented output
references. No mutable module-global cache, registry, last-state, last-result,
last-source, last-provenance, or other invocation residue may be created.

On failure, before the fixed exception is raised, implementation must clear and
delete every builder-owned reference to the state, composition, trusted drafts,
embedded/recomputed Gate, reviews, embedded/recomputed Disposition, content,
rationale, evidence blocks, citation plan, projected tuple/list, current/last
source or provenance item, and any temporary result. No closure or retained
iterator may keep such an alias.

## 10. Fixed failure contract

Every input, contract, public Gate, public Disposition, comparison, projection,
or construction `Exception` is converted to one private exception type with one
fixed static message. The message contains no dynamic ID, title, URL, content,
rationale, block, exception text, or repr. The fixed exception has
`__cause__ is None` and `__context__ is None`.

The frame that raises the fixed exception holds no state, composition, nested
artifact, source, provenance, block, plan, or partial Handoff. Live-input work
is isolated in helpers that return either a successful primitive/output plan or
a fixed internal marker. After all sensitive references are deleted, a
non-sensitive helper raises the fixed exception.

The production traceback, frame locals, closure cells, exception chain, and
mutable module globals must not make a failed call's complete state,
composition, draft content, rationale, evidence block, projected collection,
or partial output reachable. Tests may use a narrowly scoped identity check on
the existing traceback path; this milestone must not introduce a reusable
walker or safety framework.

`KeyboardInterrupt`, `SystemExit`, process termination, and concurrent hostile
mutation are outside the ordinary `Exception` contract. Because the function
is synchronous and performs no await, this milestone defines no artificial
`CancelledError` behavior.

## 11. Canonical representation and natural maximum

### 11.1 Canonical JSON

Canonical JSON for mechanical size calculations is:

```python
json.dumps(
    value,
    ensure_ascii=False,
    allow_nan=False,
    sort_keys=True,
    separators=(",", ":"),
).encode("utf-8")
```

The Handoff does **not** define or execute an independent canonical-byte cap
validator. Its accepted size is bounded naturally by the already frozen nested
DTO contracts, the one-through-sixty-four projection count, exact cross-field
binding, and its fixed three-field object shape.

### 11.2 Exact 53-byte outer overhead

Sorted canonical field order is exactly:

```json
{"cited_provenance":...,"cited_sources":...,"composition":...}
```

Embedding each nested canonical JSON value changes none of that nested value's
bytes. The exact outer overhead is:

```text
"cited_provenance":  = 19 bytes
"cited_sources":     = 16 bytes
"composition":       = 14 bytes
outer { } + 2 commas  =  4 bytes
                         --------
total                 = 53 bytes
```

The previous 41-byte figure is not valid and is not part of this contract.

### 11.3 Joint natural maximum

Under the frozen nested contracts, the three values have these exact reachable
canonical maxima:

```text
non-ready WorkflowAcademicDraftComposition = 1,526,996 bytes
cited_sources exact tuple                   = 1,873,400 bytes
cited_provenance exact tuple                = 1,576,833 bytes
outer three-field object overhead           =        53 bytes
                                               -------------
natural Handoff maximum                     = 4,977,282 bytes
```

The same legal public-builder input reaches all three component maxima at once:

- twelve approved sections and a valid non-ready Composition at its frozen
  1,526,996-byte maximum;
- every section cites the same sixty-four source IDs in Gate order, giving 768
  aggregate citation appearances and sixty-four global first-wins IDs;
- twelve maximum-length drafts and twelve maximum-length review rationales use
  the frozen non-ready maximizing construction;
- the approved state contains those same sixty-four sources with each frozen
  metadata field at its legal canonical-byte maximum while retaining unique
  source IDs, orders, URLs, and candidate IDs;
- those same IDs have sixty-four provenance entries with exactly sixty-four
  total blocks and 262,144 total Python code points, using legal block lengths
  and maximum JSON escaping; and
- public Gate and Disposition recomputation succeeds and yields the embedded
  non-ready values.

The public builder must successfully construct this exact 4,977,282-byte
Handoff. Measurements distinguish Python code points from UTF-8 bytes and use
the canonical parameters above.

### 11.4 No Handoff-only max-plus-one contract

Because 4,977,282 is the sum of simultaneously reachable maxima for all three
fields plus fixed overhead, any 4,977,283-byte three-field output necessarily
violates at least one existing nested-artifact or nested-field contract. There
is no vector that violates only a Handoff byte cap.

Therefore:

- `WorkflowAcademicReviewHandoff` performs no second whole-object byte-cap
  check;
- the public builder has no 4,977,283 rejection branch of its own;
- tests retain the 4,977,282 public success vector;
- there is no Handoff-only max-plus-one test; and
- nested-artifact max-plus-one behavior remains owned by the corresponding
  frozen upstream tests and is not copied into Milestone 3.14.

The natural maximum is a derived invariant and acceptance measurement, not a
new serialization limit layered over the nested DTOs.

## 12. Side effects and execution environment

The module is synchronous, deterministic, and off graph. It performs no LLM,
Retriever, Config, Provider, network, filesystem, process, database, checkpoint,
legacy `add_references`, backend, frontend, or other external call. It creates
no task, thread, coroutine, retry, fallback, repair, cache, telemetry payload,
or persistence record.

Its only calls outside private local helpers are the public deterministic 3.9
Gate, the public deterministic 3.11 Disposition, Pydantic validation needed for
the new result DTO, and canonical JSON operations used by tests/measurement,
not by an independent production cap validator.

## 13. Compact offline test matrix

Implementation adds tests only to the one new test file. Fakes are unnecessary
for LLM/network work because none exists. Tests must use compact parameterized
behavior matrices and map every retained case to this milestone's behavior or a
named regression risk.

The permanent matrix covers:

1. `needs_human_review` and `blocked` successful construction, exact
   composition identity, exact projected source/provenance identities, order,
   uniqueness, JSON arrays, frozen behavior, and input non-mutation.
2. Exact ready rejection and mixed/non-ready shape corruption in one invalid
   composition matrix.
3. Public Gate recomputation exactly once, complete pure-projection agreement,
   and Gate failure/mismatch before Disposition or output projection.
4. Public Disposition recomputation exactly once, complete pure-projection
   agreement, non-ready routing, and Disposition failure/mismatch before output
   construction.
5. Positionally bound state outline, drafts, Gate, reviews, Disposition,
   citations, attempts, and one-through-sixty-four global IDs in one binding
   matrix.
6. Both legal 3.8 provenance fields-set shapes, with both empty shapes reaching
   the same Gate failure; null, corrupt missing-shape non-empty value, missing
   internal value, extra fields-set member, and wrong container in an existing
   invalid-surface matrix.
7. Stable global first-wins projection with repeated cross-section citations,
   exact source/provenance selection, uncited-item exclusion, and no URL/title/
   similarity fallback.
8. Hostile exact-type internal surfaces with non-exact mapping keys,
   fields-set members, string subclasses, descriptors, shadowed methods, or
   custom iterators; every hostile dynamic-call counter remains zero.
9. Fixed ordinary failures at the state, Gate, Disposition, projection, and
   result-construction stages; exact call prefixes, zero later calls, null
   cause/context, no partial Handoff, and the existing narrow identity-based
   sensitive-reachability assertion.
10. One joint public-builder vector reaching exactly 4,977,282 canonical UTF-8
    bytes, including exact 1,526,996 / 1,873,400 / 1,576,833 component sizes and
    exact 53-byte outer overhead.

The matrix deliberately contains no 4,977,283 Handoff-only rejection case. It
does not reproduce any nested DTO's max-plus-one matrix.

Tests must not retain a module-missing or symbol-missing initial red, AST-only or
symbol-only assertion, generic traceback walker, copied import guard, complete
3.8-3.13 safety framework, full-suite requirement, multiple execution-order
matrix, real service call, or independent test for a behavior already fully
covered by a stronger case.

Focused 3.14 tests run first during implementation. After green, only one
directly related 3.8/3.9/3.11/3.13/3.14 regression run is required. A full
academic-writing suite or multiple file orders are not required unless a real
global-state contamination is first demonstrated and reported.

## 14. Explicit non-goals

Milestone 3.14 does not add or perform:

- FinalEditor or any automatic section/draft rewrite;
- claim-level truth, support, contradiction, sufficiency, or placement judgment;
- another CitationReviewer, SectionWriter, Retriever, or LLM call;
- merge, reference rendering, citation-marker rewriting, or bibliography work;
- a ready/publication handoff or automatic publication decision;
- human-review UI, review mutation, approval command, or reviewer assignment;
- graph, subgraph, Send, checkpoint, phase, event, state, facade, or routing work;
- file export, persistence, backend, frontend, network, database, or product I/O;
- package-root export, dependency, schema-version, or checkpoint migration; or
- concurrency control, locking, hostile mutation detection, retry, fallback,
  repair, cache, telemetry, or audit-log creation.

## 15. Mandatory stop conditions

Implementation must stop for a revised approved specification if:

- either approved new file is insufficient or any existing/third file must
  change;
- any frozen state, provenance, Gate, Review, Disposition, Composition, graph,
  checkpoint, package-export, or exception contract must change;
- exact static extraction requires executing an untrusted method, equality,
  repr, descriptor, dynamic attribute, or custom iterator;
- the public Gate or public Disposition cannot be called exactly as frozen;
- the original composition identity or selected input-state source/provenance
  identities cannot be preserved on successful return;
- a ready composition can reach successful output, or a non-ready composition
  requires Reviewer/LLM/SectionWriter work;
- 53 bytes is not the exact fixed outer overhead;
- 4,977,282 is not the natural maximum or the three component maxima cannot be
  reached simultaneously by one legal public-builder input;
- correctness appears to require an independent Handoff cap or a fabricated
  Handoff-only 4,977,283 test;
- fixed ordinary-error isolation cannot be achieved without retaining a partial
  Handoff or sensitive live input; or
- correctness requires FinalEditor, graph, checkpoint, persistence, export,
  external service, dependency, or another non-goal.

No implementer may resolve a stop by weakening validation, copying a private
helper, adding a third file, adding an independent canonical cap, inventing an
unreachable max-plus-one vector, treating non-ready as factual judgment, or
expanding scope.

## 16. Draft approval checklist

- [ ] Status is Draft and implementation is not authorized.
- [ ] The exact two-added-file boundary and third-file stop condition are approved.
- [ ] No existing production, test, specification, initializer, or dependency may change.
- [ ] The narrow supersession authorizes only an off-graph deterministic human-review handoff.
- [ ] The exact two-name `__all__` and synchronous public signature are approved.
- [ ] The strict/frozen/JSON three-field DTO and absence of redundant fields are approved.
- [ ] Exact Python dict/tuple/DTO and exact JSON object/array rules are approved.
- [ ] The output composition must retain the exact input composition identity.
- [ ] Output source/provenance items retain the exact selected input-state identities.
- [ ] Only non-ready needs-human-review or blocked Composition shapes are accepted.
- [ ] Ready and mixed later-artifact shapes are fixed failures.
- [ ] The output tuples contain one through sixty-four unique, positionally aligned IDs.
- [ ] The exact approved-state and complete nested-artifact static surfaces are approved.
- [ ] Mapping keys and fields-set members are proven exact strings before trusted operations.
- [ ] No untrusted method, equality, repr, iterator, descriptor, reflection, or dynamic attribute executes.
- [ ] The two allowed provenance fields-set shapes and exact empty default rule are approved.
- [ ] Fields-set shape is not used to infer or expose checkpoint history.
- [ ] Both empty-provenance shapes reach the same deterministic Gate failure.
- [ ] The complete state/composition outline, section, citation, attempt, and order binding is approved.
- [ ] Public Gate recomputation occurs once before public Disposition recomputation.
- [ ] Embedded and recomputed Gate values compare only by complete pure-primitive projection.
- [ ] Embedded and recomputed Disposition values compare only by complete pure-primitive projection.
- [ ] Reviewer, SectionWriter, merger, renderer, and external services are never rerun.
- [ ] Global citation order is section-order flattening followed by stable first-wins.
- [ ] Source and provenance projection uses only exact ID lookup with no fallback.
- [ ] The proof boundary makes no review-authenticity, factual, evidentiary, or publication claim.
- [ ] Structurally legal fabricated reviews remain explicitly indistinguishable.
- [ ] External call-duration mutation is illegal input and the builder never mutates inputs.
- [ ] Success may expose documented output identities but retains no root state or temporary plan.
- [ ] Ordinary failures use one private fixed error with null cause/context and no partial Handoff.
- [ ] Failure cleanup removes all sensitive input, artifact, content, evidence, and projection aliases.
- [ ] No artificial cancellation contract is added to the synchronous function.
- [ ] Canonical JSON parameters and UTF-8/code-point distinction are approved.
- [ ] The outer canonical field order is cited_provenance, cited_sources, composition.
- [ ] The exact outer overhead is 19 + 16 + 14 + 4 = 53 bytes.
- [ ] The three component maxima are 1,526,996, 1,873,400, and 1,576,833 bytes.
- [ ] One legal public-builder vector reaches all three component maxima simultaneously.
- [ ] The natural complete-Handoff maximum is exactly 4,977,282 UTF-8 bytes.
- [ ] WorkflowAcademicReviewHandoff has no independent canonical-cap validator.
- [ ] No Handoff-only 4,977,283 vector or test exists because one cannot be mechanically isolated.
- [ ] Nested max-plus-one ownership remains in the frozen upstream tests.
- [ ] The compact offline behavior matrix contains no duplicated upstream suite or safety framework.
- [ ] No initial-red, AST/symbol-only, full-suite, or multi-order requirement is introduced.
- [ ] FinalEditor, UI, graph/checkpoint, export, persistence, and all other non-goals remain deferred.
- [ ] Every mandatory stop condition is approved.
- [x] This specification received explicit approval before implementation began.

## 17. Implementation acceptance checklist

- [ ] Only the exact two approved new files were added.
- [ ] No existing production, test, frozen specification, initializer, dependency, or lock file changed.
- [ ] `__all__` contains exactly the two approved names in order.
- [ ] The public builder is synchronous and has the exact approved signature.
- [ ] The output DTO contains exactly composition, cited_sources, and cited_provenance.
- [ ] The DTO is strict, frozen, extra-forbid, and JSON-compatible.
- [ ] Python validation enforces exact dict, tuple, nested DTO, member, and key types.
- [ ] JSON validation accepts exact arrays and round-trips to tuple fields.
- [ ] Lists in Python mode, subclasses, coercions, iterators, missing fields, and extra fields reject.
- [ ] Direct DTO validation requires one through sixty-four unique aligned source/provenance IDs.
- [ ] Successful public output preserves the exact input composition identity.
- [ ] Successful public output preserves each selected input-state source/provenance identity.
- [ ] Ready, mixed, missing, extra, and corrupt Composition shapes reject.
- [ ] Needs-human-review and blocked each return a complete Handoff normally.
- [ ] All nested outline IDs, section IDs, citations, attempts, counts, and order bind exactly.
- [ ] Every relevant attempt is exact integer one and bool rejects.
- [ ] Exact Pydantic internal dict/set/extra/private surfaces are validated statically.
- [ ] Every mapping key and fields-set member is exact str before equality, membership, subscription, or set comparison.
- [ ] Hostile members and shadows execute zero equality, repr, descriptor, iterator, or dynamic-attribute code.
- [ ] Both frozen WorkflowResearchEvidence fields-set shapes are accepted as surfaces.
- [ ] Missing-provenance shape requires an exact internal empty tuple and rejects every competing shape.
- [ ] Empty provenance under either legal fields-set shape fails through the Gate before output projection.
- [ ] No fields-set shape is labeled or recorded as legacy origin.
- [ ] Public Gate is called exactly once with the trusted draft tuple.
- [ ] Gate failure or pure-projection mismatch makes zero Disposition/output-projection calls.
- [ ] Recomputed and embedded Gate projections compare completely without model equality.
- [ ] Public Disposition is called exactly once with recomputed Gate and trusted reviews.
- [ ] Disposition failure or pure-projection mismatch makes zero output-construction calls.
- [ ] Recomputed and embedded Disposition projections compare completely without model equality.
- [ ] Reviewer, writer, merger, renderer, LLM, Retriever, Config, Provider, network, and files are never called.
- [ ] Global cited IDs use exact section order and stable first-wins deduplication.
- [ ] Projected source/provenance IDs equal that plan positionally and occur once each.
- [ ] Uncited and duplicate-loser objects are absent and no URL/title/text fallback exists.
- [ ] The Handoff and docstrings make no authenticity, factual, support, sufficiency, or publication claim.
- [ ] The builder does not mutate any input object or nested container.
- [ ] Success retains no root state, uncited object, recomputed artifact, mutable plan, or module-global residue.
- [ ] Every ordinary failure raises only the fixed private exception and fixed text.
- [ ] Fixed error cause/context are null and no dynamic data enters the error.
- [ ] Every failure returns no partial Handoff and makes zero later calls.
- [ ] Failure traceback/locals/closures retain no state, composition, content, rationale, evidence block, or projected collection.
- [ ] The exact canonical outer overhead is mechanically measured as 53 bytes.
- [ ] The cited_sources tuple reaches exactly 1,873,400 canonical UTF-8 bytes.
- [ ] The cited_provenance tuple reaches exactly 1,576,833 canonical UTF-8 bytes.
- [ ] The same legal input uses a 1,526,996-byte non-ready Composition.
- [ ] One public-builder success reaches exactly 4,977,282 canonical UTF-8 bytes.
- [ ] The joint vector simultaneously has twelve sections, sixty-four global IDs, 768 appearances, maximum drafts/reviews, and 64 blocks/262,144 evidence code points.
- [ ] Production contains no independent complete-Handoff canonical-cap validator.
- [ ] Tests contain no 4,977,283 Handoff-only rejection case.
- [ ] Nested-artifact max-plus-one tests are not copied into the 3.14 suite.
- [ ] Tests use compact parameterized matrices and every retained case maps to a 3.14 behavior or named risk.
- [ ] Tests contain no permanent initial-red, AST-only, symbol-only, generic walker/import guard, or multi-order matrix.
- [ ] Focused tests and one directly related regression pass offline without real external services.
- [ ] Production changes no state, graph, event, facade, checkpoint, package export, persistence, or dependency.
- [ ] Tail whitespace, syntax, `git diff --check`, staging, and the exact two-file implementation boundary pass final verification.
