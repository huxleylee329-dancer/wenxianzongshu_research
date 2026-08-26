# Academic Writing Milestone 3.13: Off-Graph Academic Draft Composer

Status: **Approved and frozen**

规范已批准并冻结，仅授权冻结边界内实施；Implementation acceptance在实施完成并验证前保持未勾选。

## 1. Goal

Milestone 3.13 adds one asynchronous, sequential, off-graph composer over the
already frozen public Milestones 3.6 through 3.12:

```text
exact approved AcademicWorkflowState
                |
                v
       write_sections(state)
                |
                v
 gate_citation_evidence(state, drafts)
                |
                v
 review_citations(state, drafts, gate_result)
                |
                v
 gate_citation_review_disposition(gate_result, reviews)
                |
        +-------+-------+
        |               |
   non-ready          ready
        |               |
        |        merge_sections(state, drafts)
        |               |
        |        render_references(...)
        |               |
        +-------+-------+
                |
                v
      WorkflowAcademicDraftComposition
```

The composer preserves the complete review handoff when routing is
`needs_human_review` or `blocked`. Non-ready is a successful mechanical result,
not an execution error. Only `ready` invokes merge and reference rendering.

## 2. Frozen baselines and narrow supersession

The direct frozen baselines are:

```text
specs/academic-writing-milestone-3.6-section-writer-sequence.md
specs/academic-writing-milestone-3.7-section-merger.md
specs/academic-writing-milestone-3.8-evidence-provenance-index.md
specs/academic-writing-milestone-3.9-citation-evidence-gate.md
specs/academic-writing-milestone-3.10-citation-reviewer-adapter.md
specs/academic-writing-milestone-3.11-citation-review-disposition.md
specs/academic-writing-milestone-3.12-references-renderer.md
```

This milestone supersedes only their reservation of a future off-graph
composition layer. It changes no state, existing DTO, prompt, response,
allowlist, verdict, routing, merge, render, graph, checkpoint, event, facade,
package export, exception, cancellation, or persistence contract.

The composer invokes each public boundary; it does not copy or bypass a
private helper. The 3.10 reviewer must still rerun the public 3.9 Gate
internally. A result records the artifacts produced during one composer call,
but its serialized shape alone cannot attest that an artifact was produced by
the named implementation rather than fabricated with a legal structure.

## 3. Exact future implementation boundary

After explicit approval, implementation may add exactly:

```text
gpt_researcher/workflows/academic_writing/academic_draft_composer.py
tests/test_academic_writing_academic_draft_composer.py
```

No existing file may change. In particular, implementation must not modify
`state.py`, any Milestone 3.6-3.12 module or test, `graph.py`, nodes, events, a
facade, package initializer, frozen specification, dependency, lock file,
backend, frontend, checkpoint, or legacy path. If two added files are
insufficient, implementation stops for a revised approved specification.

## 4. Complete public surface and signatures

The complete public surface is:

```python
__all__ = (
    "WorkflowAcademicDraftComposition",
    "GPTResearcherAcademicDraftComposer",
)
```

Every other introduced name has a leading underscore. No package initializer
re-exports either public name.

The two private structural Protocols are exactly:

```python
class _SectionWriterSequence(Protocol):
    async def write_sections(
        self,
        state: AcademicWorkflowState,
    ) -> tuple[WorkflowSectionDraft, ...]: ...


class _CitationReviewer(Protocol):
    async def review_citations(
        self,
        state: AcademicWorkflowState,
        drafts: tuple[WorkflowSectionDraft, ...],
        gate_result: WorkflowCitationEvidenceGateResult,
    ) -> tuple[WorkflowSectionCitationReview, ...]: ...
```

The sole public executor is exactly:

```python
class GPTResearcherAcademicDraftComposer:
    def __init__(
        self,
        *,
        section_writer_sequence: _SectionWriterSequence | None = None,
        citation_reviewer: _CitationReviewer | None = None,
    ) -> None: ...

    async def compose(
        self,
        state: AcademicWorkflowState,
    ) -> WorkflowAcademicDraftComposition: ...
```

The constructor performs no import of either production implementation and no
Config, LLM, Provider, network, graph, file, or external work. A falsy injected
non-`None` value remains selected.

## 5. Strict six-field result DTO

### 5.1 Exact fields

```python
class WorkflowAcademicDraftComposition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    drafts: tuple[WorkflowSectionDraft, ...]
    gate_result: WorkflowCitationEvidenceGateResult
    reviews: tuple[WorkflowSectionCitationReview, ...]
    disposition: WorkflowCitationReviewDisposition
    merged_draft: WorkflowMergedDraft | None
    referenced_draft: WorkflowReferencedDraft | None
```

There is no seventh status, attempt, error, diagnostic, score, mutable mapping,
state snapshot, prompt, evidence block, client, or execution-log field.
Routing is read only from `disposition.disposition`.

### 5.2 Exact Python and JSON shapes

Direct Python validation accepts only an exact DTO or exact built-in `dict`
with exactly the six declared keys. `drafts` and `reviews` are exact built-in
tuples containing exact public DTO instances. The Gate and disposition are
exact public DTOs. The last two values are exact public DTOs or `None`.
Lists, tuple/model/string subclasses, iterators, generators, mappings other
than exact dict, coercions, missing keys, and extra keys reject.

JSON input is accepted only through `model_validate_json()`. JSON arrays are
copied to exact tuples by explicit mode-aware validators. Every nested artifact
is restored through its defining public type and must satisfy its complete
strict/frozen/JSON/canonical contract.

### 5.3 Binding and branch invariant

The DTO revalidates these pure-value bindings:

1. one through twelve drafts and reviews;
2. draft order and IDs equal Gate `section_ids` positionally;
3. every review outline ID, section ID, cited-ID tuple, and attempt equal the
   corresponding Gate values;
4. the disposition outline ID, section IDs, section count, aggregate label,
   and attempt bind to the Gate and reviews;
5. Gate, drafts, reviews, and disposition use exact attempt one at every
   artifact position; and
6. all outline IDs and ordered section-ID tuples agree.

After exact nested-DTO reconstruction, the Composition validator flattens
`gate_result.cited_source_ids_by_section` in section order and applies stable
first-wins to the exact source-ID strings. The resulting global unique plan
must contain one through sixty-four IDs. Zero or more than sixty-four rejects
for both ready and non-ready Composition branches. Every review cited-ID tuple
remains positionally equal to its Gate tuple, and a ready
`referenced_draft.reference_source_ids` remains exactly equal to this global
stable-first-wins plan.

This is a contract of `WorkflowAcademicDraftComposition` itself. A direct
Composition containing 200 globally unique IDs rejects even when its nested
Gate, reviews, disposition, and referenced artifact are each structurally
legal under their own standalone contracts. The one-through-sixty-four shape
matches what the real Composer can produce through the public provenance-backed
Gate; it does not prove that Gate, Reviewer, or Composer actually executed.

The DTO recomputes the frozen 3.11 verdict-to-section and aggregate routing
from the trusted review values and requires exact equality with disposition.
Issues remain validated under 3.10 but never alter routing.

If overall disposition is `ready`, every section disposition is `ready` and
both `merged_draft` and `referenced_draft` must be exact DTOs. Their outline,
section, attempt, merged-prefix, and reference-ID positional projections must
bind to the earlier artifacts under their public contracts.

If overall disposition is `needs_human_review` or `blocked`, both later fields
must be exactly `None`. The first four artifacts remain complete and readable.
One `None` and one DTO, a ready result with either `None`, or a non-ready result
with either later DTO is invalid.

The DTO can prove structural/value binding only. Without the input state it
cannot attest that Gate, Reviewer, Merger, Renderer, or Composer executed, nor
can it reconstruct source metadata or prove artifact origin.

### 5.4 Canonical representation

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
equality, and canonical-byte equality after strict JSON restoration. Branch
caps are frozen in Section 11.

## 6. Exact execution order

After synchronous exact-state preflight and collaborator selection, the sole
stage order is:

1. await exactly one `sequence.write_sections(state)`;
2. statically validate and reconstruct the returned exact draft tuple;
3. call public `gate_citation_evidence(state, drafts)` exactly once;
4. await exactly one `reviewer.review_citations(state, drafts, gate_result)`;
5. the reviewer's own public-Gate recomputation remains mandatory and is not
   replaced by step 3;
6. statically validate and reconstruct the returned exact review tuple;
7. call public `gate_citation_review_disposition(gate_result, reviews)` once;
8. if disposition is non-ready, construct and return the six-field DTO with
   both later fields `None`; otherwise
9. call public `merge_sections(state, drafts)` once;
10. call public `render_references(state, merged, gate_result, disposition)`
    once; and
11. construct, canonically restore, and return the ready Composition.

The composer performs no parallel work, `gather`, task creation, `Send`,
subgraph, iterator-driven section loop, retry, fallback, repair, speculative
merge, or speculative render. For either non-ready label, merge and render
call counts are exactly zero.

The composer never interprets review rationale or issues and never rewrites a
draft. `ready` remains only the frozen mechanical routing label. It does not
mean factually correct, evidence-complete, human-approved, publishable, or safe
for automatic release.

## 7. State and collaborator-result trust boundary

`compose()` first requires `type(state) is AcademicWorkflowState`, performs the
same canonical exact-state snapshot/restoration boundary used by 3.6, and
requires `outline_approved/completed`. Strict restoration remains the sole
proof of approve, digest, decision, event, and artifact-reference consistency.
The fresh trusted state is retained only because every later public stage
requires it.

An injected sequence or reviewer is a cost/test seam, not an authenticity
oracle. Its result remains untrusted even when its exact type matches. Drafts
use the frozen 3.6 exact four-slot extraction and fresh canonical
`WorkflowSectionDraft` reconstruction before step 3. Reviews use the frozen
3.10 seven-field exact surface, exact primitives/tuples, full verdict/issue/
rationale validation, and fresh canonical `WorkflowSectionCitationReview`
reconstruction before step 7.

No untrusted result method, equality, `repr`, dynamic attribute, property,
descriptor, custom iterator, serializer, or model-copy operation executes.
Only `object.__getattribute__`, exact built-in dict/set/tuple operations, and
exact primitive checks are allowed before reconstruction. The module does not
introduce a generic recursive safety walker or shared helper file.

Gate, disposition, merged, and referenced artifacts come only from their
direct public functions during `compose()`. The final DTO nevertheless
revalidates their exact public surfaces and pure-value bindings.

From entry until return or error, the caller must not mutate `state`, an
injected collaborator, or any nested internal mapping/container through
threads, tasks, callbacks, signals, `object.__setattr__`, `__dict__`, or slots.
Such mutation is illegal concurrent input and a non-goal. Normal async
scheduling alone is not mutation. The composer never mutates its inputs and
does not lock, detect, copy around, or recover from malicious mutation.

## 8. Injection and production isolation

The constructor stores only the two injected choices or private production
sentinels. The exact private default creators are conceptually:

```python
def _create_production_section_writer_sequence() -> _SectionWriterSequence:
    from .section_writer_sequence import GPTResearcherSectionWriterSequence

    return GPTResearcherSectionWriterSequence()


def _create_production_citation_reviewer() -> _CitationReviewer:
    from .citation_reviewer import GPTResearcherCitationReviewerAdapter

    return GPTResearcherCitationReviewerAdapter()
```

Each default is imported and constructed locally only when its stage is first
needed. The sequence is constructed once per `compose()` call before the write
await. The reviewer is constructed once only after Gate success and before the
review await. An injected choice causes zero corresponding production import
or construction. Non-ready still necessarily constructs/calls the reviewer,
but never constructs another component for merge or render.

Gate, disposition, merger, and renderer are direct private aliases of their
public functions. They have no injection, callback, monkeypatch, registry, or
constructor surface in production. The composer creates no mutable module-
global cache, last-artifact slot, client pool, writer pool, or registry.

## 9. Ordinary failure, partial work, and retry

The module defines exactly one private outward ordinary error:

```python
class _AcademicDraftComposerError(RuntimeError):
    pass
```

Its sole text is:

```text
academic draft composer failed
```

Every ordinary preflight, production import/construction, injected selection,
write, draft validation, Gate, review, review validation, disposition, merge,
render, DTO, canonicalization, or internal failure becomes that error. It has
no dynamic text, `__cause__ is None`, `__context__ is None`, and
`__suppress_context__ is False`.

Failure at stage N invokes every later stage zero times and returns no
Composition, partial tuple, default artifact, or empty result. Completed LLM
or writer cost cannot be rolled back. There is no checkpoint or idempotency
key. A caller retry is a complete new `compose()` invocation beginning with
Section 1 and may repeat every prior writer and reviewer charge.

Non-ready is never converted to this error. It returns the complete first four
artifacts with the two exact `None` values.

Before fixed conversion, composer-owned frames clear/delete the trusted state,
drafts, Gate, reviews, disposition, current/last artifact, collaborator
choices/references, operations, primitive plans, canonical bytes, mutable
lists, tuple copies, and closure aliases. The fixed-error helper receives only
a private marker. No prompt, client, Provider, writer-internal value, or raw
response is introduced directly by this module; the frozen 3.6/3.10 components
retain responsibility for their own lower frames.

The fixed outward traceback, locals, closures, chain, args, and attributes
cannot reach a complete input, completed artifact, partial Composition,
composer-owned collaborator alias, mutable accumulator, canonical payload, or
raw ordinary exception. No error is logged, printed, chained, or returned.

## 10. Cancellation and await-frame lifecycle

`asyncio.CancelledError` during production sequence/reviewer local import or
construction, injected collaborator selection, the writer-sequence await, or
the reviewer await propagates by bare `raise` as the identical instance with
identical `args`. It is not rebuilt, converted, logged, retried, or represented
as non-ready.

Before bare propagation, composer-owned frames clear/delete state, every
completed draft/Gate/review/disposition artifact, current/last aliases,
collaborator choice/reference, operation, primitive plan, mutable collection,
and any tuple copy. Later stage calls are zero and no partial Composition is
returned. Lower 3.6/3.10 frames retain their own frozen cancellation contracts;
the composer makes no claim about arbitrary hostile injected-collaborator
locals.

The public async entry statically extracts the two constructor choices and
deletes `self` before its first await. It may retain across an await only the
trusted state required by later public stages, already validated immutable
artifacts required by later stages, the current collaborator operation, and
the two constructor choices until each is consumed. Every unnecessary alias is
deleted before suspension.

The composer does not traverse section bodies. It needs no section iterator.
If an implementation uses a stage index, it must be exact `int` in an explicit
`while`; no `range`, `enumerate`, `zip`, generator, or other iterator may live
across an await. Successful return intentionally exposes all six output fields,
including complete drafts/reviews and, on ready, merged/referenced artifacts.
Failure-path non-reachability must not deny those legitimate outputs.

## 11. Exact joint canonical maxima

### 11.1 Why upstream standalone maxima do not add

The Composition maximum is not the sum of the standalone 3.7-3.12 maxima.
Non-ready never has merged/referenced artifacts. Ready must survive the 3.7
reconstruction of the target-specific 3.5 projected source allowlist, while
every draft must also fit the complete 3.10 per-section 65,536-code-point
review prompt. A real Composer ready path therefore cannot use the structurally
fabricated 200-reference 3.12 maximum vector.

For maximum UTF-8 bytes, one four-byte Unicode code point is the baseline
filler. A required 31-code-point ASCII marker replaces 31 such code points.
NUL is used only while its six-character JSON escape improves bytes without
violating the reviewer prompt. For `p` cited IDs in each 24,576-code-point
draft, the all-four-byte-character reviewer prompt length is:

```text
L0(p) = 24,672 + 88*p
k(p)  = floor((65,536 - L0(p)) / 5)
```

`k(p)` is the number of four-byte fillers replaced by NUL. Each replacement
adds five prompt code points and two canonical UTF-8 bytes. Remaining slack
below five stays unused because newline/quote/backslash replacements consume
prompt capacity while reducing UTF-8 bytes. The exact canonical size of one
draft is:

```text
D(p) = 98,390 - 93*p + 2*k(p)
```

### 11.2 Non-ready maximum

The maximum non-ready branch uses twelve sections, one shared cited source,
one minimal nonempty provenance block, one marker per maximum-length draft,
`k(1)=8,155`, twelve `uncertain` reviews with all three canonical issues and
2,048 NUL rationale code points, and twelve
`needs_human_review` routes. Each reviewer prompt is exactly 65,535 code
points. The component canonical sizes are:

```text
drafts tuple                         1,375,297
Gate result                                619
reviews tuple                          150,409
disposition                                575
two null later artifacts                     8
six-field object keys/punctuation            88
non-ready Composition maximum          1,526,996 UTF-8 bytes
```

For `1 <= p <= 64`, adding one cited ID replaces marker-sized four-byte body
content and reduces `k(p)` while adding only fixed ASCII Gate/review IDs. The
exact adjacent difference is always negative (either -948 or -924 bytes), so
`p=1` is the unique citation-count maximum. Adding a section is strictly
positive; the maxima for one through twelve sections end at 1,526,996.
`blocked` is 119 bytes smaller than the otherwise corresponding uncertain
vector, so it does not win.

### 11.3 Ready maximum

Ready must pass the 3.7/3.5 allowlist. For twelve sections and `p >= 1`
projected sources, use one-code-point query, language, question, context,
outline title, briefs, and distinct minimal section titles. Give each source a
256-four-byte-code-point projected title and one distinct four-byte URL. The
minimal 3.5 canonical message is:

```text
B_ready(p) = 1,003 + 316*p
title_sum(p) = 12 + 65,536 - B_ready(p)
             = 64,545 - 316*p
```

The public maximum uses `p=1`, title sum 64,229, maximum draft contents with
`k(1)=8,155`, twelve `supported` reviews with empty issues and 2,048-NUL
rationales, and one source whose full 512-code-point title is 256 four-byte
characters followed by 256 NULs. Its one-code-point URL is a distinct
four-byte character. The 3.5 prompt is exactly 65,536 and every 3.10 prompt is
65,535. Public Gate, Reviewer, Disposition, Merger, and Renderer contracts all
accept it. Exact results are:

```text
merged content                         359,223 code points
referenced content                     361,091 code points
drafts tuple                         1,375,297 UTF-8 bytes
Gate result                                619 UTF-8 bytes
reviews tuple                          149,485 UTF-8 bytes
ready disposition                          406 UTF-8 bytes
merged draft                         1,631,572 UTF-8 bytes
referenced draft                     1,634,527 UTF-8 bytes
six-field object keys/punctuation            88 UTF-8 bytes
ready Composition maximum            4,791,994 UTF-8 bytes
```

For `1 <= p <= 24`, deterministic enumeration of the formulas above and the
exact frozen bibliography template gives 4,791,994 at `p=1`; every increment
is negative (between -3,661 and -3,589 bytes). More source metadata consumes
3.5 title budget, and marker replacement costs across the three repeated body
surfaces outweigh added ID/bibliography bytes. Values for one through twelve
sections increase strictly and peak at twelve.

The same public-flow vector simultaneously reaches every number in this
section using injected fake collaborators and no external service. It makes no
claim that an LLM would naturally choose the maximizing response.

### 11.4 Composition caps and max-plus-one

Three boundaries are distinct:

1. each upstream artifact may satisfy its own standalone DTO contract;
2. a Composition is legal only when all Section 5 cross-field bindings, the
   global one-through-sixty-four unique-ID rule, its branch shape, and its
   Composition canonical cap hold; and
3. public `compose()` reachability additionally requires that one shared state
   and the exact stage sequence jointly produce every artifact.

After determining the legal branch from disposition and the two optional
fields, the DTO canonicalizes the complete six-field Composition by Section
5.4 and enforces its own cross-field UTF-8 byte cap:

```text
non-ready <= 1,526,996 UTF-8 bytes
ready     <= 4,791,994 UTF-8 bytes
```

The legal public-flow vectors in Sections 11.2 and 11.3 reach the respective
caps exactly, so each cap is also the maximum legal direct Composition size.
A direct 1,526,997-byte non-ready or 4,791,995-byte ready Composition mapping
rejects solely because it violates this Composition-level canonical cap. No
claim is made that cap-plus-one must violate an upstream prompt, source, draft,
review, merged, referenced, or other standalone artifact limit. The composer
public path must not fabricate max-plus-one.

## 12. Success reachability and information boundary

On success, the returned Composition intentionally retains every trusted
output artifact named by its six fields. The caller can read draft content,
review rationales, Gate citations, routing, and ready final text. Exact
primitive strings may retain an earlier primitive identity; no distinct-copy
promise exists.

The returned DTO must not reference the input state, composer, collaborator,
factory, Config, client, Provider, writer, prompt, evidence-block container,
raw response, mutable accumulator, execution plan, marker, canonical bytes,
frame, closure, or original exception. A module-level class/function and a
caller-held injected collaborator remain normally reachable; this is not a
whole-process erasure promise. No invocation creates or mutates a mutable
module-global retained reference.

## 13. Side effects and deferred FinalEditor

Aside from the existing writer and reviewer calls, the composer performs no
LLM, Retriever, Config, Provider, network, filesystem, subprocess, logging,
graph, checkpoint, database, persistence, legacy, backend, frontend, or
external work. Deterministic stages remain synchronous and direct.

FinalEditor remains deferred. Editing after references are appended can break
the exact inline-marker stream, bibliography binding, merged-prefix identity,
and Gate/disposition relationship. Editing before render changes reviewed
content and therefore requires a new Gate, Reviewer, and Disposition cycle.
Milestone 3.13 neither performs nor silently accepts either invalidation.

## 14. Compact offline test matrix

Only `tests/test_academic_writing_academic_draft_composer.py` is added. It uses
small local fake sequence/reviewer collaborators and public deterministic
functions. No real Config, LLM, Provider, network, filesystem, or service is
invoked.

The permanent suite covers:

1. exact two-name public surface, signatures, strict/frozen/JSON six-field DTO,
   and one real ready composition in a behavior test rather than symbol-only
   assertions;
2. ready execution order, exact call counts, complete positional binding, and
   readable six-artifact success;
3. `needs_human_review` and `blocked` normal returns with complete first four
   artifacts and exactly zero merge/render calls;
4. exact ready/non-ready branch invariants and nested-artifact near-misses in
   one parameterized DTO/binding matrix, including the global stable-first-wins
   64-ID boundary and direct 65-/200-ID rejection across both branches;
5. injected/default production isolation, lazy construction, and no mutable
   global residue without a copied import registry;
6. one stage-failure matrix spanning state preflight, production import/
   construction, sequence, draft validation, Gate, reviewer, review validation,
   disposition, merge, render, and result construction, with zero later calls;
7. one cancellation matrix spanning production import/construction,
   collaborator selection, writer await, and reviewer await, including first,
   middle, and last lower-stage cancellation inherited through fakes;
8. whole-call retry proving Section 1 restarts and earlier writer/reviewer cost
   may repeat;
9. one bounded module-specific alias/traceback inspection shared by the stage
   failure and cancellation matrices, proving no partial artifact or
   collaborator alias remains without a general walker;
10. the non-ready 1,526,996 and ready 4,791,994 reachable vectors, their exact
    upstream prompt/title arithmetic, whole-Composition cap selection, and
    direct-invalid max-plus-one solely at that cap; and
11. stable-input snapshots proving the composer never mutates state or injected
    collaborators.

Tests may reuse the behavioral patterns of the existing 3.6 fake sequence,
3.10 fake reviewer/client, and 3.7/3.9/3.11/3.12 local artifact builders, but
must not import private test helpers or duplicate their complete safety and
input matrices. Upstream correctness is exercised only where composition order,
binding, branching, cost, or joint bounds depend on it.

There is no permanent initial-red, module/API-missing, AST-only, symbol-only,
generic walker, complete import guard, multiple file-order, full-suite, or
unrelated historical test. Development runs only the new file. Final related
verification runs once with the new file plus 3.6, 3.9, 3.10, 3.11, 3.7, and
3.12 tests in dependency order. A demonstrated global-import mutation is a
stop condition, not permission for an order matrix.

## 15. Explicit non-goals

Milestone 3.13 does not implement:

- FinalEditor, section rewrite/regeneration, marker movement, citation repair,
  bibliography editing, claim extraction, or new semantic judgment;
- human-review UI, approval command, publication, export, backend, frontend,
  notification, or handoff persistence;
- graph, node, edge, phase/status, event, error code, facade, checkpoint,
  resume-from-stage, package export, or workflow-state mutation;
- parallelism, tasks, `gather`, Send, subgraph, streaming, progress callbacks,
  retry, fallback, repair, or attempt 2+;
- exactly-once writer/LLM execution, billing deduplication, idempotency, or
  rollback of completed cost;
- artifact authenticity, signatures, attestation, common-origin proof, factual
  correctness, evidence sufficiency, publication approval, or recovery from
  illegal call-duration mutation; or
- a new dependency, shared helper, modification of a frozen milestone, or
  direct use of a legacy report/finalization entry.

## 16. Mandatory stop conditions

Implementation must stop for a revised approved specification if:

- either approved file is insufficient or an existing/third file must change;
- any frozen public signature, DTO surface, prompt, result, error, cancellation,
  merge, render, or routing contract must change;
- Reviewer Gate recomputation would be bypassed or replaced;
- non-ready cannot return the complete first four artifacts without merge or
  render work;
- collaborator output cannot be statically reconstructed without executing an
  untrusted operation;
- deterministic stages require injection, a private helper, or copied
  implementation rather than their public functions;
- exact ordinary-failure isolation or same-instance cancellation cannot be
  preserved without returning partial artifacts;
- either 1,526,996 or 4,791,994 is false, not a true upper bound, or not
  simultaneously reachable by its stated public-flow vector;
- correctness requires FinalEditor, graph, checkpoint, persistence, parallel
  work, another external call, dependency, or package export; or
- this Draft conflicts internally with an unchanged frozen baseline.

No implementer may resolve a stop by weakening validation, treating non-ready
as an exception, treating ready as factual/publication approval, returning a
partial result, fabricating max-plus-one, caching a collaborator globally, or
expanding scope.

## 17. Draft approval checklist

- [ ] Status is Draft and implementation is not authorized.
- [ ] The exact two-added-file boundary and third-file stop condition are approved.
- [ ] Every existing production, test, specification, initializer, and dependency remains unchanged.
- [ ] The narrow supersession authorizes only off-graph sequential composition.
- [ ] The exact two-name `__all__`, private Protocols, constructor, and async method are approved.
- [ ] The strict/frozen/JSON six-field result DTO is approved.
- [ ] Exact Python/JSON dict, tuple, DTO, subclass, missing, and extra rules are approved.
- [ ] No redundant status or composer-attempt field is added.
- [ ] Complete draft/Gate/review/disposition positional binding is approved.
- [ ] The result DTO revalidates the frozen review-to-disposition routing.
- [ ] Ready requires exact merged and referenced DTOs.
- [ ] Non-ready requires both later fields to be exact `None`.
- [ ] Non-ready returns the complete first four artifacts as a normal result.
- [ ] The result shape alone makes no execution-origin or authenticity claim.
- [ ] Both branches freeze a global stable-first-wins Gate plan of one through sixty-four IDs and reject direct 200-ID Composition input without claiming provenance.
- [ ] The exact write, Gate, review, disposition, conditional merge, render order is approved.
- [ ] The Reviewer still reruns the public Gate internally.
- [ ] Both non-ready labels cause exactly zero merge/render calls.
- [ ] No parallel, speculative, retry, fallback, repair, graph, or checkpoint work exists.
- [ ] Exact approved-state snapshot/restoration precedes collaborator cost.
- [ ] Injected draft and review outputs receive trusted static reconstruction.
- [ ] No untrusted method, equality, repr, iterator, property, descriptor, or dynamic attribute executes.
- [ ] Only sequence and reviewer have injection surfaces.
- [ ] Default sequence and reviewer imports/constructions are local, delayed, and once per call.
- [ ] Injected paths perform zero corresponding production construction.
- [ ] Deterministic public functions are called directly and are not injectable.
- [ ] Call-duration external mutation is illegal input and the composer never mutates inputs.
- [ ] The one private fixed error type/text and null cause/context are approved.
- [ ] Any ordinary stage failure has zero later calls and no partial Composition.
- [ ] Non-ready never enters the ordinary-error path.
- [ ] Cancellation preserves identical instance/args by bare raise at every async boundary.
- [ ] Failure/cancellation clears all composer-owned artifacts and collaborator aliases.
- [ ] Prior external costs cannot roll back and whole-call retry restarts at Section 1.
- [ ] Await frames retain only state/artifacts/operation values required by a later stage.
- [ ] No section iterator or iterator-producing construct lives across await.
- [ ] Success intentionally exposes every output artifact and readable rationale/content.
- [ ] Success retains no input state, external component, mutable plan, or invocation-global residue.
- [ ] Standalone upstream maxima are not incorrectly summed.
- [ ] The reviewer-prompt formulas `L0(p)`, `k(p)`, and `D(p)` are approved.
- [ ] The non-ready maximum is exactly 1,526,996 UTF-8 bytes and is reachable.
- [ ] The ready title/source tradeoff and 64,229 title sum are approved.
- [ ] The ready maximum is exactly 4,791,994 UTF-8 bytes and is reachable.
- [ ] One cited ID uniquely maximizes both branches under the frozen formulas.
- [ ] Upstream-artifact legality, Composition legality, and public-flow reachability are distinct, and max-plus-one violates only the Composition cap.
- [ ] The compact fake-only behavior matrix and one dependency-order regression are approved.
- [ ] No initial-red, AST/symbol-only, generic walker, full suite, or order matrix is required.
- [ ] FinalEditor and every other explicit non-goal remain deferred.
- [ ] Every mandatory stop condition is approved.
- [x] This specification received explicit approval before implementation began.

## 18. Implementation acceptance checklist

- [ ] Only the exact two approved new files were added.
- [ ] No existing production, test, frozen specification, initializer, dependency, or lock file changed.
- [ ] `__all__` contains exactly the two approved names in order.
- [ ] Every private Protocol and public constructor/method signature is exact.
- [ ] The result DTO contains exactly the six approved fields.
- [ ] The DTO is strict, frozen, extra-forbid, and JSON-compatible.
- [ ] Python and JSON validation enforce exact dict, tuple, nested DTO, and null shapes.
- [ ] Lists, subclasses, iterators, coercions, missing keys, and extra keys reject.
- [ ] Drafts and reviews contain one through twelve exact trusted DTOs.
- [ ] Gate, draft, review, and disposition outline/section IDs bind positionally.
- [ ] Review citations equal Gate citations at every position.
- [ ] The global stable-first-wins Gate plan contains one through sixty-four IDs, binds ready references, and rejects 65-/200-ID direct Composition input in both branches.
- [ ] Every nested attempt is exact integer one under its frozen DTO.
- [ ] Disposition is recomputed from trusted verdicts and matches exactly.
- [ ] Ready has exact merged and referenced artifacts with complete binding.
- [ ] Needs-human-review has complete handoff artifacts and two exact nulls.
- [ ] Blocked has complete handoff artifacts and two exact nulls.
- [ ] Mixed null/DTO branch shapes reject.
- [ ] The DTO and docstrings make no authenticity, factual, or publication claim.
- [ ] Exact approved-state preflight occurs before production component construction.
- [ ] State snapshot restoration preserves the complete frozen approval contract.
- [ ] Composer constructor causes zero production import/construction or external work.
- [ ] Production sequence is locally imported/constructed exactly once when needed.
- [ ] Production reviewer is locally imported/constructed exactly once only after Gate success.
- [ ] Injected sequence and reviewer cause zero corresponding production imports/constructions.
- [ ] The sequence is awaited exactly once before every other artifact stage.
- [ ] Sequence output is statically extracted and freshly reconstructed before Gate.
- [ ] The public Gate is called exactly once by Composer.
- [ ] Reviewer is awaited exactly once with the trusted Composer Gate result.
- [ ] Reviewer-internal public Gate recomputation remains observable and unmodified.
- [ ] Reviewer output is statically extracted and freshly reconstructed before disposition.
- [ ] Public disposition is called exactly once after review validation.
- [ ] Non-ready makes zero merge and zero render calls.
- [ ] Ready calls public merger once and public renderer once in that order.
- [ ] No deterministic stage has an injection surface or private-helper dependency.
- [ ] No task, gather, parallelism, Send, subgraph, retry, fallback, or repair exists.
- [ ] No input object or nested mapping/container is mutated.
- [ ] Untrusted collaborator results execute no dynamic or hostile operation.
- [ ] Every ordinary stage failure raises only the fixed private error.
- [ ] Fixed error text is exact and cause/context are null.
- [ ] A final-stage ordinary failure returns no partial Composition or earlier artifact.
- [ ] Every ordinary failure makes zero later stage calls.
- [ ] Production import/construction and collaborator-await cancellation preserve instance/args.
- [ ] Cancellation returns no partial result and makes zero later calls.
- [ ] Composer-owned traceback frames retain no completed artifact, collaborator alias, or mutable accumulator after failure/cancellation.
- [ ] Whole-call retry restarts at Section 1 and may repeat writer/reviewer cost.
- [ ] Success returns all readable artifacts and retains no input state or external object.
- [ ] No invocation creates mutable module-global cache or last-object state.
- [ ] Async frames contain no range/enumerate/zip/generator iterator across await.
- [ ] The non-ready maximizing vector reaches exactly 1,526,996 canonical bytes.
- [ ] The ready maximizing vector reaches exactly 4,791,994 canonical bytes.
- [ ] The legal branch selects and enforces its cap over the complete six-field canonical Composition, and cap-plus-one rejects solely at that cap.
- [ ] The ready vector reaches 359,223 merged and 361,091 referenced content code points.
- [ ] The 3.5 and 3.10 maximizing prompts reach exactly 65,536 and 65,535 code points.
- [ ] Formula enumeration proves every added citation and every smaller section count is lower.
- [ ] Direct DTO max-plus-one rejects without a fabricated composer path.
- [ ] Tests use local fakes and map every retained case to a composition behavior or named risk.
- [ ] Tests contain no permanent initial-red, AST-only, symbol-only, generic walker/import guard, or multi-order matrix.
- [ ] Focused Composer tests pass offline without real Config, LLM, Provider, network, or files.
- [ ] The single dependency-order related regression passes without a full suite.
- [ ] Production changes no state, graph, event, facade, checkpoint, package export, or dependency.
- [ ] Production performs no additional external call beyond the frozen sequence/reviewer stages.
- [ ] `git diff --check` passes, staging is empty, and status contains only approved files.
- [ ] Implementation remains unstaged and uncommitted before review.
