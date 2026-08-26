# Academic Writing Milestone 3.11: Off-Graph Deterministic Citation Review Disposition Gate

Status: **Approved and frozen**

规范已批准并冻结，仅授权冻结边界内实施；Implementation acceptance在实施完成并验证前保持未勾选。

## 1. Goal and exact routing boundary

Milestone 3.11 adds one synchronous, deterministic, off-graph routing gate after
the frozen 3.10 reviewer and before any caller invokes the frozen 3.7 merger:

```text
exact WorkflowCitationEvidenceGateResult
  + exact tuple[WorkflowSectionCitationReview, ...]
                         |
                         v
        gate_citation_review_disposition()
                         |
                         v
          WorkflowCitationReviewDisposition
```

The result says only how this call routes the supplied, structurally validated
review opinions. It does not approve facts, claims, citations, publication, or
automatic release. In particular:

- `ready` means every supplied section review has the exact verdict
  `supported`;
- `needs_human_review` means no supplied section review is `unsupported` and at
  least one is `uncertain`; and
- `blocked` means at least one supplied section review is `unsupported`.

These are workflow labels derived from bounded LLM opinions. They are not
formal proofs, ground-truth labels, human approval, publication approval, or
claims that evidence is complete, sufficient, consistent, contradiction-free,
or correctly placed.

## 2. Normative baseline and narrow supersession

The direct frozen baselines are:

```text
specs/academic-writing-milestone-3.9-citation-evidence-gate.md
specs/academic-writing-milestone-3.10-citation-reviewer-adapter.md
```

Milestone 3.11 supersedes only the reservation of a future deterministic
post-review routing decision. It does not change either producer, any existing
DTO, the meaning of a verdict or issue, the 3.9 mechanical proof boundary, the
3.10 model-opinion boundary, or any state, graph, event, facade, checkpoint,
writer, merger, prompt, response, exception, cancellation, or legacy contract.

The 3.10 statement that its adapter makes no approval decision remains true:
3.10 only creates reviews. This separate 3.11 function derives a routing label,
not human or factual approval. A caller may use `ready` as one prerequisite for
a later action, but 3.11 neither invokes that action nor declares the manuscript
publishable.

## 3. Exact future implementation boundary

After explicit approval, implementation may add exactly:

```text
gpt_researcher/workflows/academic_writing/citation_review_disposition.py
tests/test_academic_writing_citation_review_disposition.py
```

No existing file may change. In particular, implementation must not modify
`state.py`, `graph.py`, `nodes.py`, `events`, `adapters.py`, a facade, a package
initializer, `citation_evidence_gate.py`, `citation_reviewer.py`,
`section_merger.py`, any existing test, frozen specification, dependency, lock
file, backend, frontend, checkpoint, or legacy entry point.

The production module may import standard-library facilities, Pydantic, and
exactly these two public input DTO types at module scope:

```python
from gpt_researcher.workflows.academic_writing.citation_evidence_gate import (
    WorkflowCitationEvidenceGateResult,
)
from gpt_researcher.workflows.academic_writing.citation_reviewer import (
    WorkflowSectionCitationReview,
)
```

Those imports are required by the public signature and trusted exact-type
checks but are not members of this module's `__all__`. The imports execute no
Config, LLM, Provider, Retriever, network, filesystem, graph, checkpoint, or
legacy work. No private 3.9/3.10 helper, annotation reflection, `model_fields`,
typing reflection, dynamic import, or `Any` may supply a type or behavior.

If these two added files are insufficient, implementation stops for a revised,
explicitly approved specification.

## 4. Complete public surface and synchronous signature

The complete public surface is exactly:

```python
__all__ = (
    "WorkflowCitationReviewDisposition",
    "gate_citation_review_disposition",
)
```

Every introduced helper, marker, error, literal alias, constant, and extraction
type has a leading underscore. No package initializer re-exports either name.

The sole operation is exactly:

```python
def gate_citation_review_disposition(
    gate_result: WorkflowCitationEvidenceGateResult,
    reviews: tuple[WorkflowSectionCitationReview, ...],
) -> WorkflowCitationReviewDisposition: ...
```

It is an ordinary synchronous function. There is no class constructor,
Protocol, callback, iterator/generator API, async function, task, thread,
subprocess, injected component, or external client.

## 5. Strict module-local output DTO

### 5.1 Exact fields and types

```python
_Disposition = Literal["ready", "needs_human_review", "blocked"]


class WorkflowCitationReviewDisposition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    outline_id: Literal["outline:000001"]
    section_ids: tuple[str, ...]
    section_dispositions: tuple[_Disposition, ...]
    disposition: _Disposition
    attempt: Literal[1]
```

There are exactly five fields. The DTO contains no verdict, issue, rationale,
citation ID, score, confidence, review text, evidence, content, diagnostic,
timestamp, or mutable mapping.

### 5.2 Exact Python and JSON shapes

Direct Python construction accepts only the exact DTO instance or an exact
built-in `dict` with exactly the five declared keys. Every scalar has exact
built-in type. Python containers are exact built-in tuples; lists, subclasses,
iterators, generators, bytes, null, bool attempts, floats, coercible values,
missing fields, and extra fields reject.

JSON input is accepted only through `model_validate_json()`. JSON arrays are
explicitly copied to exact tuples by mode-aware validation. JSON booleans,
null, wrong containers or members, missing fields, and extra fields reject.

The exact value rules are:

1. `section_ids` has one through twelve members;
2. `section_ids[index] == f"section:{index + 1:06d}"`;
3. `section_dispositions` is nonempty and has exactly the same length as
   `section_ids`;
4. every section disposition is one exact allowed literal;
5. overall `disposition` equals the unique aggregate rule in Section 6; and
6. `attempt` has exact type `int`, equals `1`, and rejects `bool`.

The DTO validates its own positional and aggregate invariants. Only the public
function binds those values to one supplied Gate result and review tuple.

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

Construction requires strict JSON restoration, exact recursive JSON value/type
equality, exact model equality, and exact canonical-byte equality. The public
function returns only the restored exact DTO.

## 6. Unique routing and priority algorithm

The sole section mapping is:

```text
supported   -> ready
uncertain   -> needs_human_review
unsupported -> blocked
```

The sole ascending aggregate priority is:

```text
ready < needs_human_review < blocked
```

The function traverses reviews in Gate section order, appends exactly one
mapped disposition per review, and chooses the greatest encountered priority.
Equivalently: any `unsupported` makes the overall result `blocked`; otherwise
any `uncertain` makes it `needs_human_review`; otherwise it is `ready`.

Issues never override, upgrade, or downgrade the verdict. They are fully
revalidated as the unique subsequence of the frozen 3.10 order:

```text
insufficient_evidence
possible_contradiction
citation_placement_unclear
```

They participate only in 3.10 verdict/issue coherence validation and are then
dropped. Rationale is validated and dropped; its words are never parsed or used
for routing. Input tuple order is preserved exactly in `section_ids` and
`section_dispositions`.

## 7. Exact proof and non-proof boundary

Given stable inputs, the function mechanically establishes only that:

1. both inputs have the exact trusted structural forms frozen below;
2. every Gate section has exactly one positionally corresponding review;
3. each review's outline ID, section ID, cited source IDs, and attempt bind to
   the Gate projection;
4. each review still satisfies the complete frozen 3.10 DTO contract; and
5. the returned labels are the exact deterministic mapping in Section 6.

It cannot establish and must not claim:

- that the Gate was actually produced by calling 3.9;
- that a review was actually produced by the 3.10 adapter, an LLM, or any
  particular model/provider/call;
- that a structurally legal review is authentic rather than fabricated;
- that `supported`, `unsupported`, or `uncertain` is factually correct;
- claim support, factual truth, evidence sufficiency/completeness, absence or
  presence of contradiction, citation placement correctness, or provenance
  completeness; or
- that `ready` means approved, factually correct, safe to publish, or eligible
  for automatic merge/export.

A deliberately constructed, exact, fully coherent review tuple that matches an
exact Gate artifact is valid input and receives the same mechanical routing as
an adapter-produced tuple. One required limitation test makes this visible; it
does not simulate or assert LLM provenance.

## 8. Trusted static extraction surface

### 8.1 No operation on untrusted instances

Both DTO inputs remain untrusted even when their exact class matches. Before a
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

`__dict__` and `__pydantic_fields_set__` must be exact built-in `dict` and
`set`; extra and private must be `None`. Keys and field-set members must be
exact strings. Exact tuples use only `tuple.__len__` and `tuple.__getitem__`;
exact dicts/sets use trusted built-in operations only after member types are
proved. Primitive values are type-checked before comparison.

### 8.2 Exact Gate surface and trusted restoration

The exact Gate surface is:

```text
__dict__ keys in declaration order:
  outline_id, section_ids, cited_source_ids_by_section, attempt
fields-set exact members:
  outline_id, section_ids, cited_source_ids_by_section, attempt
extra: None
private: None
```

The extractor copies the exact outline string, exact section-ID tuple, exact
nested citation tuples, and exact integer attempt to a pure primitive mapping.
It enforces all 3.9 DTO counts, fixed ID grammar, uniqueness, positional length,
and attempt rules. That mapping is canonically encoded, restored through the
public `WorkflowCitationEvidenceGateResult` type, dumped, and compared for
exact recursive type/value and canonical-byte equality. Instance methods are
used only on this newly restored trusted DTO. The live supplied Gate object and
all extraction temporaries are then deleted.

This restoration proves shape and values only. It does not call
`gate_citation_evidence()` and cannot prove the artifact's execution origin.

### 8.3 Exact review surface and trusted restoration

Every member accessed by exact tuple index must have exact type
`WorkflowSectionCitationReview` and this exact surface:

```text
__dict__ keys in declaration order:
  outline_id, section_id, cited_source_ids, verdict, issues, rationale, attempt
fields-set exact members:
  outline_id, section_id, cited_source_ids, verdict, issues, rationale, attempt
extra: None
private: None
```

The extractor copies only exact primitive strings, exact string tuples, and the
exact integer attempt into a pure mapping. It re-enforces the complete public
3.10 DTO contract: section/source ID grammar and bounds, cited-ID uniqueness,
the three verdict literals, unique canonical issue order, verdict/issue
coherence, exact nonblank rationale of at most 2,048 code points, and exact
integer one. The pure mapping is canonically encoded, restored through the
public `WorkflowSectionCitationReview` type, dumped, and compared for exact
recursive type/value and canonical-byte equality.

The function then compares only trusted exact primitive projections against
the Gate values. It never compares original model instances. After deriving
the current section disposition, it deletes the current live-review local,
trusted restored review, rationale, cited IDs, issues, canonical bytes,
mapping, and every other single-review alias before indexing the next member.
The original exact `reviews` tuple necessarily remains the sole input owner of
all original members while the synchronous extraction helper still needs later
positions; it is deleted as one unit when that helper returns its pure plan or
failure marker and before either result construction or the fixed-error helper.

Validated primitive strings may retain an existing primitive identity; this
contract does not promise identity-distinct strings. No returned object can
reach an original complex input model, input mapping, rationale, issue tuple,
or cited-ID tuple.

## 9. Exact guard and positional-binding order

All input validation completes before result DTO construction, in this order:

1. require `type(gate_result) is WorkflowCitationEvidenceGateResult`;
2. statically extract, validate, canonically restore, and reduce the Gate to the
   trusted pure projection `(outline_id, section_ids, citations, attempt)`;
3. require `type(reviews) is tuple`;
4. require review count equals Gate section count and is between one and twelve;
5. for each Gate index, require exact review type and static surface;
6. fully revalidate and canonically restore the current review;
7. require review `outline_id` equals Gate `outline_id`;
8. require review `section_id` equals Gate `section_ids[index]`;
9. require review `cited_source_ids` equals
   Gate `cited_source_ids_by_section[index]` code point for code point and in
   exact order;
10. require review `attempt` and Gate `attempt` are exact integer one;
11. map only the validated verdict, append one section disposition, and delete
    all current review aliases; and
12. after every position succeeds, compute the overall priority and construct
    the exact output DTO.

Missing, extra, duplicate, reordered, wrong-outline, wrong-section,
wrong-citation, malformed-opinion, or corrupt reviews fail at their first
applicable guard. There is no default, skipped review, sparse result, fallback,
repair, inferred citation tuple, or partial success. Duplicate section IDs are
already impossible in a trusted Gate projection, and a repeated review is
rejected by its positional mismatch rather than deduplicated.

## 10. Call-duration mutation boundary

From entry until return or error, `gate_result`, `reviews`, and every nested
Pydantic internal mapping/container reachable from them must not be modified by
another thread, callback, signal handler, or caller through
`object.__setattr__`, `__dict__`, a Pydantic slot, or an underlying container.
Such bypass of frozen-model semantics is illegal concurrent input and an
explicit non-goal.

The function does not detect, lock, serialize, copy around, recover from, or
promise consistency under malicious call-duration mutation. Because execution
is synchronous, there is no task scheduling or await inside the operation. The
function itself never mutates either input or any nested input value. Stable-
input tests verify nonmutation; no thread/signal/race test is required.

## 11. Deterministic output construction

After complete validation, construct exactly:

```python
WorkflowCitationReviewDisposition(
    outline_id=trusted_gate_outline_id,
    section_ids=trusted_gate_section_ids,
    section_dispositions=tuple(mapped_section_dispositions),
    disposition=highest_mapped_disposition,
    attempt=1,
)
```

The function performs the complete canonical round trip from Section 5.3 and
returns only the restored exact DTO. Before return it clears and deletes the
mutable accumulation list, primitive plan, canonical bytes, temporary DTO, and
every other construction alias. The returned DTO may contain the exact input
primitive section strings; it never contains or references a Gate/review model,
review rationale/issues/citations, temporary mapping, mutable collection, or
raw exception.

## 12. Exact canonical output bounds

All output strings are fixed ASCII identities. The longest legal disposition
is `needs_human_review`, 18 code points. For twelve sections all mapped to that
label:

```text
section_ids array
  = 2 + (12 * 16 quoted-ID characters) + 11 commas
  = 205

section_dispositions array
  = 2 + (12 * 20 quoted-disposition characters) + 11 commas
  = 253

fixed keys, outline/attempt/overall values, punctuation and outer braces
  = 117

maximum = 205 + 253 + 117
        = 575 canonical code points
        = 575 UTF-8 bytes
```

The maximum is reachable through the public function: use one exact Gate
result with twelve ordered sections and any legal nonempty cited-ID tuple per
section, plus twelve exact coherent reviews whose verdict is `uncertain`, whose
issues contain any legal nonempty canonical subset, and whose IDs/citations
match the Gate. Every section and the overall result are
`needs_human_review`.

For general `n`, the two arrays have lengths `1 + 17n` and `1 + 21n`; with the
117 non-array characters the exact all-`needs_human_review` length is
`119 + 38n`, reaching 575 at `n = 12`.

576 is unreachable from a legal DTO or public result because all string
literals and the twelve-section count are closed. The sole direct-construction
max-plus-one vector changes only the overall mapping value to the invalid exact
string `needs_human_reviewX`; its canonical mapping is 576 characters and DTO
construction rejects it. No public function path fabricates 576.

## 13. Fixed failure and information safety

The module defines exactly one private outward error:

```python
class _CitationReviewDispositionError(RuntimeError):
    pass
```

Its sole text is:

```text
citation review disposition failed
```

Every ordinary input type, internal surface, Gate, review, count, binding,
verdict, issue, rationale, attempt, DTO, canonicalization, or invariant failure
becomes this one error. It contains no dynamic outline, section, citation,
verdict, issue, rationale, validation detail, object representation, or raw
exception text. It has `__cause__ is None`, `__context__ is None`, and
`__suppress_context__ is False`.

Sensitive work occurs in isolated synchronous helpers that catch ordinary
`Exception` without stringifying it and return only a pure primitive success
plan or private identity marker. Before failure reaches the no-input error
helper, every frame holding `gate_result`, `reviews`, a live or trusted DTO,
rationale, issue/citation tuple, canonical bytes, primitive plan, accumulated
disposition, or raw exception has exited or explicitly deleted and cleared it.

The fixed-error traceback, locals, closure cells, exception chain, args, and
attributes cannot reach the complete inputs, any rationale, partial output,
mutable accumulator, canonical payload, or raw ordinary exception. No error is
logged, printed, chained with `from`, preserved, or returned. There is no
partial result: failure at the final review returns neither earlier section
dispositions nor an empty/default DTO.

`KeyboardInterrupt`, `SystemExit`, and other `BaseException` behavior is not a
3.11 contract. Ordinary handlers catch `Exception`, not `BaseException`.

## 14. Synchronous execution, retry, and side effects

The complete operation is synchronous and has no await, cancellation point,
coroutine, task, callback, thread, process, generator, client, factory, or
external component. It therefore freezes no artificial
`asyncio.CancelledError` behavior.

Import and execution perform no LLM, Config, Provider, Retriever,
GPTResearcher, network, filesystem, environment, subprocess, graph,
checkpoint, persistence, database, logging, merger, references, or legacy
work. No external cost is incurred. Repeating a call with the same stable exact
inputs deterministically returns the same canonical result or raises a fresh
fixed error with the same type and text; no retry occurs inside the module.

## 15. Explicit non-goals

Milestone 3.11 does not implement:

- factual verification, formal claim support, entailment, contradiction proof,
  evidence sufficiency/completeness, confidence calibration, or source lookup;
- review authenticity, signatures, attestation, provenance of Gate/reviewer
  execution, model/provider identity, or anti-forgery guarantees;
- rationale interpretation, issue reclassification, score aggregation, policy
  override, human approval, or publication approval;
- draft correction, section rewrite/regeneration, marker movement, source
  replacement, citation repair, or attempt 2+;
- merge invocation, merged-text inspection, references/bibliography rendering,
  citation renumbering, FinalEditor, report composition, export, or persistence;
- graph, phase/status, node, event, error code, facade, checkpoint, package
  export, backend, frontend, or product integration;
- LLM, Retriever, scraper, network evidence, external service, retry, fallback,
  repair, parallelism, Send, subgraph, streaming, or progress callbacks; or
- recovery from illegal call-duration mutation, a new dependency, shared
  helper, or modification of any frozen milestone.

## 16. Compact offline test matrix

Only `tests/test_academic_writing_citation_review_disposition.py` is added.
Tests use local exact DTO fixtures and a small number of parameterized behavior
matrices. They perform no LLM, Config, Provider, Retriever, network, filesystem,
legacy, merger, graph, checkpoint, subprocess, persistence, or external work.

The permanent suite covers:

1. exact two-name `__all__`, sole synchronous signature, strict/frozen/JSON DTO
   behavior, and one real routing success together rather than a symbol-only
   test;
2. exact Python/JSON mapping, tuple/list/subclass, literal, bool, missing/extra,
   1/12 section, positional length, aggregate consistency, canonical equality,
   reachable 575, and direct-invalid 576 behavior in compact DTO matrices;
3. one verdict-routing matrix covering `supported`, `uncertain`, and
   `unsupported`, plus mixed-section rows proving
   `blocked > needs_human_review > ready`;
4. the same routing matrix varies every legal coherent issue pattern and proves
   issues do not upgrade or downgrade the verdict-derived result;
5. one binding/error matrix covers wrong input types, missing/extra/repeated or
   reordered reviews, wrong outline/section/citation/attempt, malformed verdict,
   issue order/coherence, blank/2,049 rationale, and final-position failure with
   no partial result;
6. one legal directly constructed review vector passes and explicitly proves
   that structure/binding cannot establish LLM or adapter provenance;
7. hostile exact Gate/review instances with shadowed methods, extra/private
   state, hostile field values, string subclasses, equality, `repr`, property,
   descriptor, and iterator sentinels extend the existing error matrix and
   record zero dynamic calls;
8. stable-input snapshots before/after prove the function mutates no input;
9. one bounded module-specific traceback/locals/closure inspection proves fixed
   error text, null cause/context, no rationale/input/partial-result reachability,
   and no retained raw exception without a reusable historical walker; and
10. repeated calls prove deterministic result/error and zero external work.

Tests contain no permanent initial-red, AST-only, symbol-only, copied historical
walker/import guard, import-registry framework, multiple file ordering, real
service, or unrelated regression. Every test maps to a behavior above or an
implementation-acceptance item.

During implementation, run only the new test file until green. Final related
verification runs exactly once with the new test plus the existing 3.9 and 3.10
test files. No full academic-writing suite or order permutation is required;
any global-import dependency is a stop condition.

## 17. Mandatory stop conditions

Implementation must stop for a revised approved specification if:

- either approved implementation file is insufficient or an existing/third
  file must change;
- the exact Gate or review DTO type/surface differs from the frozen contracts;
- full 3.9 Gate DTO or 3.10 review DTO validation cannot be reproduced using
  their public types without a private helper or untrusted instance operation;
- positional section/citation binding cannot be completed before output DTO
  construction;
- routing requires rationale interpretation, issue override, LLM work, source
  lookup, merger invocation, state/graph mutation, persistence, or inference;
- the reachable canonical maximum is not exactly 575 or the 576 direct-invalid
  vector is false;
- fixed failure isolation cannot release complete inputs, rationale, partial
  dispositions, canonical payloads, and raw ordinary exceptions; or
- this Draft conflicts internally with an unchanged frozen 3.9/3.10 contract.

No implementer may resolve a stop by weakening exact validation, accepting a
partial review tuple, treating `ready` as fact/publication approval, claiming
review provenance, changing the priority rule, hiding data in an existing DTO,
or expanding scope.

## 18. Draft approval checklist

- [ ] The synchronous deterministic off-graph routing goal is approved.
- [ ] The exact two-added-file boundary and third-file stop condition are approved.
- [ ] Every existing state, DTO, graph, event, facade, checkpoint and export remains unchanged.
- [ ] The narrow supersession of only future deterministic routing is approved.
- [ ] The exact two-name `__all__` and sole synchronous signature are approved.
- [ ] The five-field strict/frozen/JSON output DTO is approved.
- [ ] Exact Python/JSON mapping, tuple, primitive, subclass and bool rules are approved.
- [ ] Section IDs, section dispositions, positional lengths and attempt one are approved.
- [ ] The exact three section mappings are approved.
- [ ] The aggregate priority `blocked > needs_human_review > ready` is approved.
- [ ] Issues never override, upgrade or downgrade the verdict-derived route.
- [ ] Full 3.10 issue order/coherence and rationale validation remain mandatory.
- [ ] Issues and rationale do not enter the output DTO.
- [ ] The routing-only meaning of all three labels is approved.
- [ ] `ready` is explicitly not factual, human or publication approval.
- [ ] Gate execution and review/LLM authenticity are explicitly not proved.
- [ ] A structurally legal fabricated review is an accepted scope limitation.
- [ ] The exact module-scope public input-type imports are approved.
- [ ] No private helper, reflection, dynamic import or `Any` is permitted.
- [ ] Both input DTOs use only the frozen trusted static Pydantic surface.
- [ ] Exact dict/set, extra/private, tuple and primitive checks are approved.
- [ ] Gate pure projection and public-type canonical restoration are approved.
- [ ] Every review receives complete public 3.10 DTO revalidation.
- [ ] No untrusted method, equality, repr, iterator, property or descriptor executes.
- [ ] Every Gate section requires exactly one review.
- [ ] Outline, section, citation, order and attempt binding are exact.
- [ ] Missing, extra, repeated and reordered reviews fail without repair.
- [ ] All validation completes before output DTO construction.
- [ ] Exact traversal order and per-review alias deletion are approved.
- [ ] External call-duration mutation is illegal input and an explicit non-goal.
- [ ] The function itself never mutates inputs; no race test is required.
- [ ] The canonical encoder and complete DTO round trip are approved.
- [ ] The reachable 575-byte/code-point maximum is approved.
- [ ] 576 is tested only by direct invalid DTO construction.
- [ ] The one private fixed error type/text and no-partial-result rule are approved.
- [ ] Fixed errors have null cause/context and contain no dynamic data.
- [ ] Inputs, rationale, partial dispositions and raw exceptions are unreachable from fixed errors.
- [ ] Synchronous execution freezes no artificial cancellation semantics.
- [ ] Retry is caller-controlled, deterministic and has zero external cost.
- [ ] No LLM, Retriever, network, merger, references or external work exists.
- [ ] The compact test matrix contains no prohibited low-value framework.
- [ ] Every explicit non-goal and mandatory stop condition is approved.
- [x] This specification received explicit approval before implementation began.

## 19. Implementation acceptance checklist

- [ ] Only the exact two approved new files were added.
- [ ] No existing production, test, specification, initializer, dependency or lock file changed.
- [ ] `__all__` contains exactly the two approved names in order.
- [ ] The sole public function has the exact synchronous signature.
- [ ] WorkflowCitationReviewDisposition has exactly the five approved fields.
- [ ] The DTO is strict, frozen, extra-forbid and JSON-compatible.
- [ ] Python and JSON exactness rejects wrong mappings, containers, subclasses and coercions.
- [ ] Bool, null, missing fields and extra fields reject.
- [ ] Section IDs are exact ordered one-through-twelve identities.
- [ ] Section-disposition count and position exactly match section IDs.
- [ ] Attempt is exact integer one and overall disposition is internally consistent.
- [ ] Supported maps only to ready.
- [ ] Uncertain maps only to needs_human_review.
- [ ] Unsupported maps only to blocked.
- [ ] Aggregate priority is exactly blocked over needs_human_review over ready.
- [ ] Legal issues retain 3.10 order/coherence but never affect routing.
- [ ] Rationale is fully validated but never parsed or returned.
- [ ] Gate input is an exact DTO with the exact four-slot surface.
- [ ] Gate projection enforces full 3.9 DTO shape, bounds and canonical restoration.
- [ ] Reviews input is an exact tuple with exactly one member per Gate section.
- [ ] Every review is an exact DTO with the exact seven-field surface.
- [ ] Every review enforces full 3.10 DTO shape, bounds, coherence and canonical restoration.
- [ ] No input instance method, equality, repr, iterator, descriptor or hostile code executes.
- [ ] Outline IDs match exactly at every position.
- [ ] Section IDs and review order match exactly at every position.
- [ ] Cited source-ID tuples match the Gate exactly at every position.
- [ ] Gate and review attempts are exact integer one.
- [ ] Missing, extra, repeated, reordered or malformed reviews fail fixedly.
- [ ] A legal fabricated review passes without any authenticity claim.
- [ ] The function mutates no input model, internal mapping, tuple or primitive.
- [ ] All inputs are validated before result construction.
- [ ] Every current review/rationale local alias is deleted before the next position; only the original input tuple remains until helper exit.
- [ ] The output contains no verdict, issue, rationale, citation, evidence or content.
- [ ] Canonical restoration returns one exact trusted result DTO.
- [ ] The reachable 12-section maximum is exactly 575 bytes/code points.
- [ ] The direct-invalid 576 mapping rejects; no impossible public path is fabricated.
- [ ] Any final-position failure returns no earlier or default disposition result.
- [ ] The fixed private error has exact type/text and null cause/context.
- [ ] Fixed-error frames retain no complete input, rationale, partial result or raw exception.
- [ ] Hostile dynamic-access counters remain zero in the shared error matrix.
- [ ] Repeated stable calls are deterministic and perform zero external work.
- [ ] Production is synchronous and creates no cancellation machinery or async object.
- [ ] Production performs no LLM, Config, Provider, Retriever, network or legacy call.
- [ ] Production invokes neither merger nor references/finalization code.
- [ ] Tests contain no initial-red, AST-only, symbol-only, copied walker/import guard or multi-order matrix.
- [ ] Every retained test maps to an acceptance item or concrete behavior risk.
- [ ] The focused new test file passes offline.
- [ ] The single approved 3.9+3.10+3.11 related regression passes.
- [ ] `git diff --check` passes and staging remains empty.
- [ ] Status shows only the approved Draft before implementation.
- [ ] No network, dependency installation, staging or commit occurred.
