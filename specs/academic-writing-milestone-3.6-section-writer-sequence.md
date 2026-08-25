# Academic Writing Milestone 3.6: Off-Graph Sequential SectionWriter Orchestrator

Status: **Approved and frozen**

规范已批准并冻结，仅授权冻结边界内实施；实施验收项在完成并验证前保持未勾选。

## 1. Goal

Milestone 3.6 adds one injectable, off-graph orchestrator that consumes one
strict approved `AcademicWorkflowState`, calls the frozen Milestone 3.5 writer
once for each existing outline section in the outline's original tuple order,
and returns one exact built-in tuple of the existing strict DTO:

```text
AcademicWorkflowState(outline_approved/completed, decision=approve)
                              |
                              v
       GPTResearcherSectionWriterSequence.write_sections()
                              |
                              v
             tuple[WorkflowSectionDraft, ...]
```

The sequence contains between one and twelve drafts. It performs no review,
merge, report assembly, persistence, graph, checkpoint, or parallel work.

## 2. Baseline, precedence, and narrow supersession

Milestone 3.5 is the direct frozen production baseline:

```text
specs/academic-writing-milestone-3.5-section-writer-adapter.md
```

All unchanged Milestones 3.0-3.5 contracts remain normative. Milestone 3.6
narrowly supersedes only the 3.5 roadmap/non-goal statements that reserved a
future approved milestone for scheduling more than one section. It authorizes
one sequential coordinator over one through twelve existing outline members.

It does not change the implementation, public surface, prompt, response,
citation, failure, cancellation, import, or one-section-per-call behavior of
`GPTResearcherSectionWriterAdapter`. It does not supersede any state field,
DTO, graph node, phase, status, event, failure code, checkpoint, facade,
decision, or package-export contract. The approved state remains a completed
terminal graph state; 3.6 consumes it only off graph.

## 3. Exact future implementation boundary

After explicit Draft approval, implementation may add exactly:

```text
gpt_researcher/workflows/academic_writing/section_writer_sequence.py
tests/test_academic_writing_section_writer_sequence.py
```

No existing file may be modified. In particular, implementation must not
modify `state.py`, `section_writer.py`, `graph.py`, `nodes.py`, `adapters.py`,
any package initializer, any frozen specification, dependency, lock file,
legacy writer, backend, or frontend file. This Draft is the sole proposal
artifact and is not one of the two future implementation files.

## 4. Complete public surface and exact signatures

### 4.1 Module public surface

The complete public surface defined by `section_writer_sequence.py` is:

```python
__all__ = (
    "GPTResearcherSectionWriterSequence",
)
```

Every other definition introduced by the module has a leading underscore. No
package initializer imports or re-exports the module or its class.

### 4.2 Private writer Protocol

```python
class _SectionWriter(Protocol):
    async def write_section(
        self,
        state: AcademicWorkflowState,
        section_id: str,
    ) -> WorkflowSectionDraft: ...
```

This structural Protocol is deliberately private and exactly matches the
frozen 3.5 public method. It adds no preflight, batch, merge, retry, or review
method to the existing writer.

### 4.3 Private production factory

```python
def _create_production_section_writer() -> _SectionWriter:
    from .section_writer import GPTResearcherSectionWriterAdapter

    return GPTResearcherSectionWriterAdapter()
```

### 4.4 Public orchestrator

```python
class GPTResearcherSectionWriterSequence:
    def __init__(
        self,
        *,
        section_writer: _SectionWriter | None = None,
    ) -> None: ...

    async def write_sections(
        self,
        state: AcademicWorkflowState,
    ) -> tuple[WorkflowSectionDraft, ...]: ...
```

The constructor stores only the injected writer or a private sentinel meaning
"construct the production writer after preflight." It performs no production
import, writer construction, Config, completion, graph, file, environment, or
network work. The writer choice is stored on one private instance attribute and
is read at public-entry time only through the trusted static access procedure
in Section 6; ordinary dynamic `getattr()` is never used.

## 5. No state, DTO, graph, or event change

Milestone 3.6 adds no DTO. The output container is exact built-in `tuple`, not
a tuple subclass, list, model, iterator, async iterator, generator, mapping,
or partial-result wrapper. Every member has exact type `WorkflowSectionDraft`.

`AcademicWorkflowState`, `AcademicWorkflowGraphState`, `WorkflowSectionDraft`,
`NodeId`, `FailureCode`, phases, statuses, events, reachable shapes, graph
edges, facades, checkpoints, and package exports remain byte-for-byte
unchanged. No draft or sequence is written into state or a checkpoint.

## 6. Complete preflight before any writer work

Creating the coroutine performs no work. When first awaited, `write_sections()`
completes all of the following synchronously before any production import,
writer construction, `write_section()` call, client factory, or completion:

1. require `type(state) is AcademicWorkflowState`;
2. canonically dump the complete state with the 3.5 JSON formula, restore it
   through `AcademicWorkflowState.model_validate_json()`, and require exact
   recursive JSON types, exact DTO equality, and exact canonical bytes;
3. require `phase == "outline_approved"` and `status == "completed"`;
4. use strict restoration as the sole proof of `decision=approve`, decision
   identity/digest, and plan/evidence/outline reference consistency;
5. require the restored outline section count to be between 1 and 12 inclusive;
6. traverse the outline section tuple once without sorting or normalization;
7. require every section ID to have exact type `str`, be nonempty under
   `.strip()`, and be unique by exact code-point equality;
8. for every section in tuple order, perform the complete input-side 3.5
   Sections 6-8 acceptance algorithm, including report type/source, topic,
   query, language, questions, context projection, source admission, complete
   outline projection, target identity, canonical JSON, and 65,536 limit; and
9. retain only the canonical state bytes, exact approved outline ID, and exact
   built-in tuple of expected section-ID strings required by execution.

Step 8 is a local, side-effect-free mirror of the frozen 3.5 input algorithm.
It must not import or call a private 3.5 helper, construct a writer, call
`write_section()`, or weaken any 3.5 bound. A state is accepted only if every
target would pass the 3.5 input boundary. The same frozen 3.5 validation order
is used within each target; the outer target traversal uses outline order.

The complete preflight is all-or-nothing. A failure for the twelfth target has
the same zero-writer-call property as a failure for the first target.

The public entry control flow is additionally frozen. Its first operation uses
a private synchronous isolation helper. That helper first requires
`type(self) is GPTResearcherSectionWriterSequence`, then uses only
`object.__getattribute__(self, "__dict__")`, requires an exact built-in dict
whose sole exact-string key is `_section_writer_choice`, and reads that value
with `dict.__getitem__`. It never resolves the named attribute through
`self`, dynamic `getattr`, a descriptor, or a property. It returns only the
writer choice or a private marker. The public frame immediately deletes
`self` before state preflight begins.

State preflight runs in a separate synchronous isolation helper. That helper
never raises an ordinary business or contract exception through its boundary;
it returns either the successful pure-primitive execution plan from step 9 or
one fixed internal marker selecting the Section 8 error. On return, the public
frame deletes the original `state` in both success and failure paths.

For a writer-choice or preflight marker, the public frame deletes the writer
choice, state, `self`, plan candidate, and every temporary before calling a
safe synchronous finishing helper that has never received a sensitive object.
For success, the public frame transfers only the pure-primitive plan and writer
choice to a separate async finisher, deletes both local bindings, and awaits
only that finisher. The public frame therefore never suspends with `self`, the
original state, the writer choice, or the execution plan in its locals.

## 7. Reachable section-count boundaries

The existing `WorkflowOutline` validator requires a nonempty, contiguous,
uniquely identified section tuple but freezes no maximum. Therefore these are
distinct mechanical cases:

- one and twelve valid sections pass the sequence-count gate;
- thirteen valid sections reach strict `AcademicWorkflowState` restoration and
  fail the sequence-count gate with zero writer calls; and
- an empty outline cannot survive strict `WorkflowOutline` or state restoration.

The fixed 13-section vector uses orders `1..13`, IDs
`f"section:{order:06d}"`, unique nonblank titles/briefs, a recomputed canonical
outline digest, decision `approve`, and the existing approved event sequence.
It must round-trip as an exact `outline_approved/completed` state before the
sequence rejects it. No `model_construct()` bypass is used for this vector.

The empty-outline test deliberately creates a corrupted exact top-level state
with `model_construct()` and proves canonical strict snapshot failure plus zero
writer calls. It does not claim that an empty outline can reach the count gate,
and no unreachable empty-outline `ValueError` is added.

## 8. Fixed caller and contract errors

The exact top-level type error is:

```text
TypeError: academic section writer sequence state must be an exact AcademicWorkflowState
```

Reachable ordinary input rejections use exactly:

```text
ValueError: academic section writer sequence requires outline_approved/completed state
ValueError: academic section writer sequence requires between 1 and 12 outline sections
ValueError: academic section writer sequence input is not accepted by the single-section contract
```

The first applicable error wins in the Section 6 order. Invalid section IDs or
any 3.5 input-side rejection use the third `ValueError`. The synchronous
preflight helper represents these outcomes only with private identity markers;
it never raises them while holding the state. Strict snapshot failure, an
impossible post-restoration shape, any ordinary production import/writer
construction failure other than `asyncio.CancelledError`, any ordinary writer
exception, a non-awaitable writer return, or any invalid returned object uses
one private fixed error:

```python
class _SectionWriterSequenceError(RuntimeError):
    pass
```

Its sole text is:

```text
section writer sequence failed
```

The orchestrator never exposes or preserves an underlying 3.5 private error.
Every ordinary exception from the production or injected writer is converted
to this fixed safe error. No partial tuple, original exception, empty tuple,
fallback value, or legacy empty string is returned on failure.

For every fixed outward TypeError, ValueError, and RuntimeError, `__cause__` and
`__context__` are `None` and `__suppress_context__` is `False`. The exception
contains no dynamic section index, ID, content, state, prompt, writer, or raw
exception text.

## 9. Production and injected writer selection

Module import, constructor execution, and complete preflight do not import
`academic_writing.section_writer`. Only after all targets pass preflight:

- an injected writer is used directly and causes zero production-module
  import and zero production-writer construction; or
- the private production factory performs its local import exactly once and
  constructs exactly one `GPTResearcherSectionWriterAdapter`.

The selected writer object is reused for the sequence. For `N` accepted
sections, `writer.write_section()` is awaited exactly `N` times, once for each
expected ID. The frozen 3.5 adapter remains responsible for one fresh client
factory and one completion inside each production call.

If production import or construction raises an ordinary `Exception`, zero
`write_section()` calls occur and the fixed sequence error is raised after
isolation. `asyncio.CancelledError` at either stage is never included in that
classification and follows Section 13 instead.

## 10. Exact sequential algorithm

The independent async finisher receives the pure-primitive execution plan and
writer choice as its only arguments. It selects the injected writer or invokes
the production factory inside an isolation boundary; selection returns a
validated writer reference, a fixed marker, or propagates cancellation under
Section 13. No selection helper stores a writer or plan globally or in a
closure.

After successful writer selection, execution is exactly:

1. traverse the saved section-ID tuple from index zero to the end;
2. restore one fresh exact `AcademicWorkflowState` from the saved canonical
   state bytes immediately before the current writer operation;
3. create exactly one `writer.write_section(restored_state, expected_id)`
   coroutine, release the live restored state and local expected-ID binding,
   and await it before constructing the next operation;
4. after return, validate the result as Section 11 specifies;
5. canonically encode the validated draft, append only those immutable bytes
   to the private accumulator, and release the live draft before any next
   await; and
6. after every writer await has completed, restore all drafts from their bytes
   in a no-await finishing frame and return one exact tuple.

There is no task creation, `asyncio.gather`, `as_completed`, `TaskGroup`, `Send`,
subgraph, worker pool, speculative call, overlap, retry, fallback, repair,
review, merge, or inter-section prompt mutation. Call order and completion
order both equal the original outline section tuple order.

## 11. Immediate result validation

After each writer returns and before any later writer call, `result` remains
untrusted even when `type(result) is WorkflowSectionDraft`. Validation uses
exactly this trusted extraction algorithm:

1. require `type(result) is WorkflowSectionDraft` without reading any member;
2. call only `object.__getattribute__` to read exactly `__dict__`,
   `__pydantic_fields_set__`, `__pydantic_extra__`, and
   `__pydantic_private__` from `result`;
3. require the first value to have exact type `dict`, the second exact type
   `set`, and the final two to be exactly `None`;
4. require the exact-dict key tuple to be exactly
   `("outline_id", "section_id", "attempt", "content")`, after first
   requiring every key to have exact type `str`;
5. first require every exact-set member to have exact type `str`, then require
   the set to contain exactly those same four names;
6. read the four values only with trusted exact-dict operations; require exact
   `str` for `outline_id`, `section_id`, and `content`, and exact `int` equal to
   `1` for `attempt`, thereby rejecting both bool values;
7. copy only those four immutable primitives and delete `result` before any
   comparison, construction, serialization, or next await;
8. require the saved expected outline/section IDs also to have exact type
   `str`, then compare only the copied exact strings with those expected exact
   strings;
9. construct a new trusted `WorkflowSectionDraft` solely from the four copied
   primitives; and
10. perform the complete existing DTO content and canonical JSON restoration,
    recursive-type equality, model equality, and byte equality checks only on
    that newly constructed trusted DTO.

No path calls `model_dump`, `model_validate`, `model_copy`, equality, `repr`,
`str`, iteration, dynamic `getattr`, or any other instance method on the
writer-returned object. Iteration is permitted only after an internal container
has been proved to be an exact built-in `dict` or `set`, and key/member types
are proved exact before value equality.

This extraction surface is frozen to the locally verified Pydantic 2.13.4
shape. `BaseModel.__slots__` is exactly `("__dict__",
"__pydantic_fields_set__", "__pydantic_extra__", "__pydantic_private__")`.
A normal `WorkflowSectionDraft` exposes the four declared fields in its exact
`__dict__`, the same four names in an exact fields-set, and `None` for both
extra and private storage. A different runtime shape is a mandatory stop
condition, not permission to guess another internal surface.

A DTO subclass, instance-shadowed method, extra instance attribute, non-`None`
Pydantic extra/private state, hostile field value, string subclass, wrong
outline ID, prior/later/unknown section ID, corrupted exact instance, wrong
attempt, invalid content, mapping, list, `None`, or any other object immediately
becomes the fixed sequence error without executing hostile code. No later
writer is called. Valid returned content is never inspected semantically or
changed.

The final tuple is reconstructed only from individually validated canonical
draft bytes. Its length equals the outline section count, and its section-ID
tuple equals the saved expected-ID tuple exactly.

## 12. Snapshot, frame, closure, and traceback boundary

At public entry, the writer choice is statically extracted and `self` is
deleted before preflight. The isolated synchronous preflight frame never
receives the writer choice. After it returns, the public frame deletes the
caller's live state before interpreting its marker/plan. On failure it deletes
the writer choice and every temporary before invoking the safe finishing
helper. On success it transfers plan and choice to the independent async
finisher, deletes both, and then awaits. Thus public and preflight-error
tracebacks cannot reach `self`, the injected writer, the original state,
outline, section objects, or temporary projection through public locals.

Across awaits, frames introduced by the async finisher may retain only:

- canonical state bytes;
- the saved exact outline ID and exact section-ID tuple;
- the selected writer reference;
- the current writer coroutine after its arguments have been transferred; and
- canonical bytes for already validated drafts.

No frame or closure introduced by this module retains a live original state,
outline, outline section, decision, current draft, or collection of draft DTOs
across an await. No sensitive closure, callback, task, generator, or module
global is created.

Each ordinary writer/import/result failure is caught in an isolation frame.
Before that frame exits it releases the canonical state bytes, IDs, writer,
operation, current result, accumulated draft bytes, and raw exception. A later
finishing helper raises only the private identity marker, so the fixed outward
exception's arguments, attributes, cause/context, traceback locals, closures,
and module globals cannot reach those objects or any underlying writer
traceback.

The orchestrator controls only frames and closures introduced by this module.
A structurally injected writer is responsible for its own behavior while it is
running. Ordinary injected-writer failures are nevertheless isolated before a
fixed sequence error is raised. Cancellation retains its original traceback by
the bare-raise rule in Section 13; sequence-owned frame locals are deleted
before propagation, but 3.6 makes no claim about arbitrary injected-writer
locals.

## 13. Failure, cancellation, partial work, and retry

If call `N` raises any ordinary exception or returns an invalid result:

- calls `1..N-1` have completed and their external costs cannot be rolled back;
- call `N` is attempted exactly once;
- calls `N+1..end` are exactly zero;
- no tuple, partial tuple, draft list, checkpoint, event, or state update is
  returned or written; and
- the caller receives only `_SectionWriterSequenceError` with its fixed text.

An `asyncio.CancelledError` raised during the production writer's local import,
production writer construction/selection, injected writer selection, or call
`N`'s writer await always propagates the identical exception instance with its
exact original `args` through bare `raise`. It is never converted to the fixed
error, reconstructed, logged, or swallowed. Before propagation, every
sequence-owned frame deletes its state bytes, writer choice/reference,
execution plan, current operation/result, expected IDs, and accumulated draft
bytes. No partial tuple is returned and every later writer call is zero.
Costs from already completed calls are not rolled back; Provider cancellation
and billing are not guaranteed.

There is no checkpoint or idempotency key. Retrying `write_sections()` is a
completely new invocation beginning at the first outline section, including
when an earlier invocation failed or was cancelled after later sections had
already incurred external costs. Exactly-once execution, cost deduplication,
resume-from-N, and draft identity across invocations are not promised.

## 14. Import and side-effect isolation

At module scope, the production file imports only standard-library modules,
`Protocol`, and `AcademicWorkflowState`/`WorkflowSectionDraft` from `state.py`.
It does not import graph, nodes, adapters, checkpoint APIs, Config, LLM helpers,
legacy report generation, GPTResearcher, backend, frontend, or multi-agents.

The module owns no mutable global call state, cache, lock, task, queue,
semaphore, logger, handler, environment read, file operation, database,
socket, subprocess, or persistence behavior.

## 15. Test scope and behavior matrix

The new test file uses injected fakes only. Tests are grouped by behavior and
parameterized where a matrix shares one code path:

1. exact `__all__`, private Protocol, production factory, constructor, and
   public method signatures, without a symbol-existence-only test;
2. import/constructor/preflight isolation and injected-path zero production
   import/construction;
3. exact state type, strict snapshot corruption, approval shape, 3.5 input
   bounds, section ID, and zero-call preflight priority; at least one case in
   this existing matrix recursively proves its fixed exception cannot reach
   `self`, the injected writer, original state, traceback locals, closures, or
   exception chains;
4. reachable one-, twelve-, and thirteen-section vectors, including strict
   13-section state round trip and zero calls on rejection;
5. corrupted empty-outline snapshot rejection, explicitly not an unreachable
   post-restoration count branch;
6. exact outline-order calls, once-per-section behavior, exact tuple result,
   and state nonmutation;
7. invalid-result matrix covering non-DTO, DTO subclass, wrong outline/section
   ID, wrong attempt, invalid content, instance-shadowed `model_dump`, hostile
   extra attribute, hostile field value, string subclass, and zero later calls;
   hostile dynamic-call counters remain exactly zero;
8. first, middle, and last ordinary failure positions with fixed safe error,
   no partial return, exact call count, and sensitive traceback isolation;
9. one cancellation parameter matrix covering production local import,
   production construction/selection, injected selection, and first, middle,
   and last writer awaits; every case proves identical exception identity and
   args, bare propagation, no partial return, and zero later calls;
10. two complete invocations proving retry restarts at section one and repeats
    the entire call order; and
11. a bounded frame/closure probe proving sequence-owned frames retain no live
    input/snapshot/outline/draft collection across writer awaits.

Every test must map to a requirement above or an explicit implementation
acceptance item. Do not add historical initial-red tests, AST-only tests,
symbol-existence-only tests, full import registries, interpreter restoration,
duplicate 3.1-3.5 safety matrices, or unrelated graph/state regressions.

During implementation, run only the new test file until green. Final related
verification runs exactly once in this order:

```text
tests/test_academic_writing_section_writer_sequence.py
tests/test_academic_writing_section_writer.py
```

No full suite, graph suite, checkpoint suite, real service, or multiple file
order is required. A global-import mutation or order dependency is a stop
condition, not a reason to expand the matrix.

## 16. Non-goals

Milestone 3.6 does not implement:

- parallel writers, `Send`, subgraphs, tasks, `asyncio.gather`, or concurrency;
- citation review, claim support, correction, or traceability;
- ordered content merge, Markdown assembly, report body, final composition,
  introduction/conclusion, references, or `FinalEditor`;
- section rewrite, attempt 2+, comparison, fallback, repair, or regeneration;
- partial-result return, progress streaming, resume-from-N, checkpointing,
  persistence, idempotency, exactly-once, or billing guarantees;
- state/DTO/graph/node/event/facade/package-export changes;
- backend/frontend, REST, websocket, UI, authentication, authorization,
  database, ReportStore, export, or product mounting;
- legacy report/subtopic writer reuse or modification;
- new dependencies, Config settings, prompt families, or shared test helpers;
  or
- any modification to frozen Milestones 3.0-3.5 behavior.

## 17. Mandatory stop conditions

Implementation must stop for a revised, explicitly approved Draft if:

- either implementation file is insufficient or any existing/fifth file must
  change;
- the frozen 3.5 writer signature or behavior must change;
- a state field, DTO, graph, node, event, facade, checkpoint, package export,
  dependency, legacy path, backend, or frontend must change;
- thirteen sections cannot survive exact state canonical restoration;
- complete 3.5 input acceptance cannot be proved for every target before the
  first writer call;
- the production writer cannot remain a post-preflight local import;
- an injected writer path triggers the production import or construction;
- ordered execution requires a task, parallel primitive, merge, persistence,
  shared mutable state, or retry;
- exact tuple/result validation cannot occur before the next writer call;
- a fixed ordinary failure cannot isolate the original exception and all
  sequence-owned sensitive objects;
- the verified four-slot Pydantic extraction surface differs or hostile return
  validation would require invoking an untrusted instance operation;
- cancellation cannot use bare `raise` after deleting sequence-owned locals;
- an offline test requires Config, LLM, Provider, GPTResearcher, graph,
  checkpoint, network, subprocess, or file output; or
- the Draft conflicts internally or with an unchanged frozen contract.

No implementer may resolve a stop condition by weakening strict restoration,
calling a writer during preflight, importing a private 3.5 helper, returning
partial output, hiding state in an existing DTO, or silently expanding scope.

## 18. Draft approval checklist

- [ ] Status is Draft and implementation is not authorized.
- [ ] The exact two-added-file boundary is approved.
- [ ] Every existing production, test, specification, package, and dependency file remains unchanged.
- [ ] The narrow supersession authorizes only sequential scheduling of 1-12 existing sections.
- [ ] The one-name `__all__`, private writer Protocol, production factory, constructor, and public method signatures are exact.
- [ ] No state field, DTO, phase, status, event, node, graph, checkpoint, facade, or export changes.
- [ ] The output is an exact tuple of exact existing `WorkflowSectionDraft` instances.
- [ ] Coroutine creation, import, construction, and preflight side-effect boundaries are approved.
- [ ] Public entry statically extracts the writer choice, immediately deletes `self`, isolates preflight in a marker-only sync helper, and deletes state/choice before safe failure finishing.
- [ ] Exact state type and canonical strict snapshot restoration occur before all writer work.
- [ ] Strict restoration is the sole proof of approve, decision binding, digest, and artifact references.
- [ ] Section-count 1/12 success and reachable 13 rejection with zero calls are approved.
- [ ] Empty outline is tested only as strict snapshot corruption, not as a reachable count branch.
- [ ] Exact, nonblank, unique section IDs and original tuple order are approved.
- [ ] Complete 3.5 input acceptance for every target is proved before the first writer call.
- [ ] Preflight mirrors but neither imports nor calls private 3.5 helpers.
- [ ] Production writer import/construction occurs locally, once, and only after successful preflight.
- [ ] Injected writer execution causes zero production import/construction.
- [ ] Each expected section is called exactly once, sequentially, with no overlap or later call after failure.
- [ ] Immediate exact DTO, identity, attempt, content, and canonical-round-trip validation is approved.
- [ ] Untrusted exact DTOs are read only through the frozen four-slot Pydantic surface; copied primitives create a fresh trusted DTO and hostile instance operations never execute.
- [ ] Live state/outline/current-draft references do not cross sequence-owned await frames.
- [ ] Across finisher awaits, retained values are limited to canonical state/draft bytes, exact IDs, the selected writer, and the current transferred coroutine.
- [ ] The one private fixed error class/text and ordinary-exception conversion are approved.
- [ ] Fixed errors expose no dynamic value, original exception, writer traceback, partial output, or sensitive sequence object.
- [ ] Production import/construction, injected selection, and every writer-await cancellation preserve the original instance/args by bare raise, delete sequence-owned locals, and perform zero later calls.
- [ ] Failure/cancellation returns no partial tuple and cannot roll back prior external costs.
- [ ] Whole-sequence retry restarts at section one and may duplicate all prior costs.
- [ ] Review, merge, parallelism, checkpointing, persistence, and exactly-once remain excluded.
- [ ] The concise parameterized test matrix and single final related order are approved.
- [ ] Every non-goal and mandatory stop condition is approved.
- [x] This specification received explicit approval before implementation began.

## 19. Implementation acceptance checklist

- [ ] Only the exact two approved implementation files were added.
- [ ] No existing file, frozen specification, package initializer, dependency, or lock file changed.
- [ ] The production module exports exactly one approved name and every other definition is private.
- [ ] The private writer Protocol and every frozen factory/class/method signature are exact.
- [ ] Import, constructor, and rejected-input paths perform zero production-writer import or construction.
- [ ] The injected path performs zero production import, Config, LLM, Provider, graph, checkpoint, network, and file work.
- [ ] Exact input type and canonical strict state restoration precede every other operation.
- [ ] Entry extracts writer choice by trusted static access, deletes `self`, uses marker-only synchronous preflight, deletes original state on both paths, and raises preflight errors only after writer/state isolation.
- [ ] Approved phase/status and strict-restoration decision/reference proof are exact.
- [ ] One through twelve sections pass the count gate; thirteen restored sections reject before writer selection.
- [ ] The formula-built 13-section state completes exact DTO, recursive-type, and canonical-byte restoration.
- [ ] Corrupted empty outline fails at strict snapshot restoration with zero writer calls.
- [ ] Every section ID is exact, nonblank, unique, and retained in original tuple order.
- [ ] Every target passes the complete frozen 3.5 input acceptance algorithm before the first call.
- [ ] No private 3.5 helper is imported or called and no writer is used for preflight.
- [ ] Production local import/construction occurs once only after complete preflight.
- [ ] Call order equals outline order and every expected ID is called exactly once.
- [ ] There is no task creation, parallel primitive, retry, fallback, repair, merge, or review.
- [ ] Each returned value is validated before any next call for exact DTO type, IDs, attempt, content, and canonical round trip.
- [ ] Returned exact DTO internals match the verified exact dict/set/None/None surface; only copied exact primitives enter a fresh trusted DTO and hostile shadow/extra/field operations execute zero times.
- [ ] Wrong type, subclass, identity, order, attempt, or content raises the fixed error and prevents every later call.
- [ ] Successful output is an exact tuple whose length and ID order exactly match the approved outline.
- [ ] The public and sequence-owned suspended frames retain no live caller state, snapshot, outline, section, current draft, or draft DTO collection.
- [ ] Canonical state/draft byte projections are deleted before any fixed error is raised.
- [ ] First, middle, and last ordinary failures have exact call counts, no partial return, and one fixed safe error.
- [ ] The fixed error has exact text, null cause/context, and no original exception or sensitive-object reachability.
- [ ] Production import, production construction/selection, injected selection, and first/middle/last writer cancellations preserve the identical exception and args by bare raise and make zero later calls.
- [ ] A repeated whole invocation starts at section one and repeats the complete sequence without an exactly-once promise.
- [ ] Input state and every existing state/DTO/graph/event/checkpoint object remain unchanged.
- [ ] Tests are behavior-focused and contain no permanent initial-red, AST-only, symbol-only, duplicated historical guard, or unrelated regression matrix.
- [ ] The new focused tests pass offline with injected fakes only.
- [ ] The single final related two-file regression passes in the approved order.
- [ ] No test invokes a real Config, LLM, Provider, GPTResearcher, graph, checkpoint, network, subprocess, or persistent file output.
- [ ] `git diff --check` passes, staging is empty, and the worktree contains only the approved Draft before implementation.
- [ ] Implementation is not staged or committed before review.
