# Academic Writing Milestone 4.2: Academic Composer Graph and Checkpoint Wiring

Status: **Approved and frozen**

This specification is a draft. It authorizes no implementation. Every approval
and implementation checkbox remains unchecked until explicit approval and the
later implementation evidence exist.

## 1. Goal and exact boundary

Milestone 4.2 is the last required graph-wiring milestone in the current
academic-writing phase. It connects the already frozen Milestone 3.13 Academic
Draft Composer and Milestone 4.1 persistent outcome contract to the existing
LangGraph workflow.

The only new execution path is:

```text
approve
  -> outline_approved/running, outcome=None
  -> academic_draft_composer
  -> draft_ready/completed or review_required/completed
  -> END
```

This milestone adds no product capability beyond that wiring. It does not add
a human-review resolution command, FinalEditor, front matter, UI, export,
publication, persistence backend, capacity policy, or exactly-once execution.

## 2. Frozen baselines and narrow supersession

The following approved contracts remain authoritative:

- Milestone 3.13 owns Composer inputs, execution order, cost behavior, output
  DTOs, ready/non-ready routing, and exception/cancellation behavior.
- Milestone 4.1 owns PersistentState, both outcome DTOs, phase/status/outcome
  reachability, event shapes, legacy restoration, and persistent
  serialization.
- Milestones 3.0 and 3.4 continue to own the earlier graph, approval interrupt,
  rejection, identity, digest, and checkpoint protocols except where this
  specification explicitly supersedes approval success routing.

This specification supersedes only these old graph facts:

1. an approve decision no longer commits `outline_approved/completed` as the
   new workflow result;
2. an approve decision no longer emits `workflow_completed` in the approval
   node;
3. `outline_approval` no longer has an unconditional edge to `END`;
4. the public graph facades restore and return the 4.1 PersistentState;
5. `resume_academic_workflow()` additionally recognizes the pending Composer
   node.

The legacy `outline_approved/completed` shape remains valid only as an already
completed historical checkpoint. It is not rewritten or upgraded.

## 3. Allowed module loading and delayed production construction

Python module loading and production object construction are separate
contracts.

The graph and facade may load these deterministic modules before the Composer
node executes:

- `workflow_outcome.py`;
- `academic_draft_composer.py`.

That loading is permitted because PersistentState discrimination, Pydantic
schema construction, and public type identity require it. The Composer module
may therefore already exist in `sys.modules`, and code may read the public
`GPTResearcherAcademicDraftComposer` and
`WorkflowAcademicDraftComposition` identities.

In this milestone, **delayed** means delayed production-object construction and
delayed cost-bearing execution, not delayed Python module loading.

Before the graph actually enters `academic_draft_composer`, it must not:

- instantiate `GPTResearcherAcademicDraftComposer`;
- select or invoke a production Composer factory;
- construct a SectionWriterSequence or CitationReviewer;
- construct Config, Provider, or an LLM client;
- call Writer, Reviewer, Gate, Disposition, Merger, or Renderer;
- perform network or LLM work or incur model cost;
- place a composer, factory, client, or other live production object in state
  or a checkpoint.

Tests verify constructor, factory, and cost-bearing call counts. They do not
use a fragile `sys.modules` or import-guard assertion.

## 4. Exact public facade contract

No new public facade is added. The existing facades become:

```python
async def start_academic_workflow(
    request: AcademicWorkflowRequest,
    adapter: AcademicWritingAdapter,
    *,
    checkpointer: BaseCheckpointSaver,
) -> AcademicWorkflowPersistentState: ...

async def resume_academic_workflow(
    identity: AcademicWorkflowIdentity,
    adapter: AcademicWritingAdapter,
    *,
    checkpointer: BaseCheckpointSaver,
    composer: _AcademicDraftComposer | None = None,
) -> AcademicWorkflowPersistentState: ...

async def submit_academic_outline_decision(
    command: AcademicOutlineDecisionCommand,
    adapter: AcademicWritingAdapter,
    *,
    checkpointer: BaseCheckpointSaver,
    composer: _AcademicDraftComposer | None = None,
) -> AcademicWorkflowPersistentState: ...
```

`_AcademicDraftComposer` is a private Protocol exposing only:

```python
async def compose(
    self,
    state: AcademicWorkflowState,
) -> WorkflowAcademicDraftComposition: ...
```

It is not added to a public `__all__` and does not modify
`AcademicWritingAdapter`.

`start_academic_workflow()` has no composer parameter because a new start ends
at the approval interrupt and cannot execute the Composer. The composer
argument is accepted only by the two facades that can execute or retry the
Composer node.

Passing `composer=None` selects the production default only inside the
Composer node. Passing a non-null composer selects only that injected object
for that single facade call. The object is neither cached globally nor stored
in graph state or checkpoint data.

Every `resume_academic_workflow()` and
`submit_academic_outline_decision()` call owns one private, one-shot Composer
dependency slot. The facade places either the injected composer or a private
production sentinel in that slot and then immediately deletes every direct
parameter and local alias to that choice. The slot is not a module global and
is never placed in RunnableConfig, state, checkpoint values, tasks, metadata,
or pending writes.

## 5. Exact graph topology

The graph retains the single frozen channel:

```text
{"workflow": <JSON-compatible mapping>}
```

The approved topology is:

```text
START
  -> topic_planner
  -> research_evidence
  -> outline_writer
  -> outline_approval
       reject  -> END
       approve -> academic_draft_composer -> END
```

The first three conditional routes remain unchanged. `outline_approval` gains
one deterministic conditional router:

- exact `outline_rejected/completed` routes to `END`;
- exact `outline_approved/running` with `outcome=None` routes to
  `academic_draft_composer`;
- every other value fails closed with the existing fixed invariant error.

The Composer node has one unconditional edge to `END`. It may return only a
valid 4.1 terminal PersistentState. It never routes back to approval and never
creates a graph interrupt.

Every graph `ainvoke` that can enter or replay to
`academic_draft_composer` explicitly passes:

```python
durability="sync"
```

This includes approve execution through the submit facade, restoration from
`outline_approved/running` through the resume facade, and every internal call
path capable of replaying the Composer task. The implementation does not rely
on LangGraph's default durability. Calls that cannot reach the Composer retain
their existing behavior, but they must not provide an alternate internal path
that reaches the node without exact synchronous durability.

With synchronous durability, LangGraph must await successful return from the
approved/running checkpoint `aput` before starting the Composer task. Composer
constructor, factory, Composer, Writer, and Reviewer call counts remain zero
while that `aput` is blocked. The terminal Composer update uses the same exact
synchronous durability, so a successful facade return occurs only after the
terminal checkpoint `aput` has completed.

## 6. Approval and rejection transitions

The approval interrupt payload, command, identity binding, outline digest,
actor assertion, retry priority, and rejection semantics remain unchanged.

For `decision="approve"`, the approval node atomically returns one persistent
state update with:

- `phase="outline_approved"`;
- `status="running"`;
- `outcome=None`;
- the exact approval decision record;
- no errors;
- events 1 through 8, ending with:

```text
7  node_started    outline_approval
8  node_completed  outline_approval
```

It does not append `workflow_completed`.

For `decision="reject"`, the node retains the historical terminal transition:

```text
outline_rejected/completed, outcome=None
7  node_started      outline_approval
8  node_completed    outline_approval
9  workflow_rejected -
```

Reject never selects, constructs, or calls a production or injected Composer.

## 7. Composer node execution view

The Composer node accepts only the exact persisted predecessor:

```text
outline_approved/running
status=running
outcome=None
next=academic_draft_composer
```

It restores that predecessor through the Milestone 4.1 persistent helper and
checks runtime thread identity before any Composer construction or call.

Milestone 3.13 requires an exact `AcademicWorkflowState` in the historical
`outline_approved/completed` shape. The node therefore constructs a fresh,
in-memory-only execution view:

- exact type `AcademicWorkflowState`, not the PersistentState subclass;
- the same trusted request, topic plan, evidence, outline, and approval record;
- `phase="outline_approved"` and `status="completed"`;
- the historical nine-event approved shape, including its compatibility-only
  `workflow_completed` event;
- no outcome field.

That compatibility event is not a new observable workflow completion. The
execution view and all of its bytes are forbidden from graph output,
checkpoint values, tasks, metadata, pending writes, and module-global state.

The selected Composer's public `compose(execution_view)` method is awaited
exactly once. The node accepts only an exact, valid
`WorkflowAcademicDraftComposition`. It does not rerun or replace the
Composer's internal Gate validation.

The compiled node closure captures only the one-shot dependency slot; it never
captures the injected composer directly. The node consumes the slot exactly
once, copies the selected choice into a local only for the active invocation,
and immediately empties the slot. A second consume attempt is a fixed invariant
failure. A production sentinel causes construction of the default
`GPTResearcherAcademicDraftComposer` only after consumption inside the node;
an injected value never enters that construction branch.

## 8. Exact terminal projection

The full Composition exists only in memory. The node projects exactly one 4.1
outcome.

### 8.1 Ready

If `composition.disposition.disposition == "ready"`, both merged and referenced
drafts must have the exact ready shape frozen by 3.13. The checkpoint stores:

```python
WorkflowDraftReadyOutcome(
    outcome_type="draft_ready",
    referenced_draft=composition.referenced_draft,
)
```

The terminal state is `draft_ready/completed`.

### 8.2 Non-ready

If the disposition is exactly `blocked` or `needs_human_review`, merged and
referenced drafts must both be `None`. The checkpoint stores:

```python
WorkflowReviewRequiredOutcome(
    outcome_type="review_required",
    drafts=composition.drafts,
    gate_result=composition.gate_result,
    reviews=composition.reviews,
)
```

The terminal state is `review_required/completed`. Disposition is not stored;
Milestone 4.1 deterministically recomputes it during restoration.

Both projections are validated by constructing and canonically serializing a
fresh `AcademicWorkflowPersistentState`. No checkpoint may contain a complete
Composition, Disposition DTO, MergedDraft, Handoff, injected composer,
production composer, factory, client, or temporary execution view.

## 9. Exact successful events and atomic state update

Both successful terminal branches have this exact event suffix:

```text
7   node_started    outline_approval
8   node_completed  outline_approval
9   node_started    academic_draft_composer
10  node_completed  academic_draft_composer
11  workflow_completed -
```

The terminal phase, completed status, minimal outcome, Composer events, and
`workflow_completed` are returned by one Composer-node state update and are
written as one checkpoint channel value. No intermediate state containing a
terminal phase without its outcome or completion events is valid or visible.

`workflow_completed` is emitted only by a successful Composer node for new
approve executions. The approval node never emits it. Historical approved
checkpoints retain their already stored old event.

## 10. Persistent restoration, legacy checkpoints, and guards

The graph facades use the Milestone 4.1 PersistentState restore and dump
helpers as the authority for facade-visible and post-approval states. Earlier
nodes may continue using the unchanged base helpers before the persistent
approved/running checkpoint is created.

The schema version remains exactly `"1"`.

- A legacy payload missing `outcome` restores as `outcome=None`; its first new
  dump may add `outcome:null` under the 4.1 one-way canonical exception.
- A complete new payload must retain recursive JSON value/type, model, and
  canonical-byte equality.
- A legacy `outline_approved/completed` checkpoint is terminal. Start rejects
  it as an existing thread; resume rejects it as not resumable; a repeated
  approval is already committed. Composer calls are zero.
- A new `outline_approved/running` checkpoint is resumable only when the real
  compiled snapshot has exactly `next=("academic_draft_composer",)`.
- `draft_ready/completed`, `review_required/completed`, and
  `outline_rejected/completed` are terminal. Resume, repeated approval, and
  raw replay do not execute Composer.
- A running state with empty, extra, wrong, or reordered pending nodes fails
  closed before graph execution.

No dedicated composition-resume facade is added. The existing
`resume_academic_workflow()` is the sole public retry path.

## 11. Ordinary failure and cancellation

If Composer selection, construction, invocation, or validation raises an
ordinary exception, the node exposes the existing fixed `ExecutionError`
contract with no dynamic text, cause, context, or partial outcome. A malformed
Composer return exposes the existing fixed `InvariantError` contract.

A genuine task `asyncio.CancelledError` is re-raised bare as the same instance
with the same args. It is not translated into an ordinary error.

For an exception or cancellation before the Composer node returns its state
update:

- no terminal phase, outcome, Composer completion event, or
  `workflow_completed` is written;
- the last successfully committed workflow checkpoint is
  `outline_approved/running, outcome=None`;
- all stages after the failing operation have zero calls;
- retry through `resume_academic_workflow()` invokes the entire Composer again
  from Section 1;
- Writer and Reviewer work already performed may be charged again.

Before returning a successful node update, converting an ordinary failure, or
barely re-raising cancellation, the implementation deletes the selected
composer, choice, client, result, execution view, and every direct alias. The
dependency slot is empty in all three paths. The facade additionally uses a
`finally` boundary to empty the slot again and release the compiled graph,
node closure, and related local references before it returns or propagates an
error.

A successful PersistentState return cannot reach the composer, slot, or
compiled graph. On ordinary failure and cancellation, the complete public
traceback, frame locals, closure cells, and one-level expansion of exact
tuple/list/dict locals cannot reach the injected composer identity. These
checks use identity only; they do not execute custom equality, repr, dynamic
attributes, or untrusted iterators.

No FailureCode or new WorkflowEvent literal is introduced. No failed business
state is persisted for a Composer execution exception.

Cancellation or failure before the approved/running checkpoint itself becomes
visible remains governed by the existing approval commit/retry protocol; it is
not misclassified as a Composer retry.

## 12. Checkpointer write uncertainty

This milestone does not claim transactional rollback from arbitrary
checkpointers. In particular, a checkpointer may commit bytes and then raise to
its caller.

Synchronous durability establishes ordering and waits for each `aput`; it does
not remove the uncertainty of a saver that commits and then raises.

After any checkpointer exception, later behavior is determined only by the
last canonical checkpoint that can actually be read:

1. if `draft_ready/completed` or `review_required/completed` is visible, the
   Composer must not run again;
2. if `outline_approved/running` is visible with the exact Composer next-node
   shape, resume runs the complete Composer again;
3. if the checkpointer cannot provide a readable canonical checkpoint, the
   infrastructure failure propagates and no graph-level recovery claim is
   made.

The implementation must not synthesize rollback, infer commit status from the
exception alone, or promise exactly-once effects. Tests cover both a saver that
raises before delegation and one that delegates successfully and then raises.

## 13. At-least-once and no-rerun mechanical proof

At-least-once behavior is proven using the real compiled graph and a recording
checkpointer:

- block the injected Writer or Reviewer after the approved/running checkpoint
  is readable;
- observe exact `outcome=None`, event prefix 1..8, and
  `next=("academic_draft_composer",)`;
- fail or cancel the operation and observe no terminal checkpoint;
- call the existing resume facade with a fresh injected Composer;
- observe Writer starting again at Section 1 and Reviewer work repeating when
  its earlier attempt had already occurred;
- observe exactly one final minimal outcome.

No-rerun behavior is proven independently for ready, review-required, reject,
and legacy approved terminals. Constructor, factory, Composer, Writer, and
Reviewer counters all remain zero when terminal guards reject resume, repeated
approval, or replay.

These tests establish at-least-once execution, not exactly-once LLM calls or
exactly-once billing.

## 14. Dependency lifetime and mutation boundary

An injected composer is an ephemeral dependency for one facade call. It may be
retained by the caller but not by workflow state, checkpoint data, mutable
module-global caches, or later facade calls.

When `composer is not None`, the node selects only that injected instance and
the production Composer constructor count is zero. When `composer is None`, a
fresh production Composer is constructed exactly once and only after the node
has restored and validated the approved/running predecessor.

Retry is a new facade call. The caller must pass its injected composer again;
otherwise that retry uses the production default. No dependency identity is
recovered from a checkpoint.

The one-shot slot has exactly three states within one facade call:

```text
loaded(injected or production sentinel)
  -> consumed and empty
  -> finally-confirmed empty
```

It cannot be refilled or shared between facade calls. The compiled graph and
node closure may reach only that slot, never the original injected composer
after consumption.

Inputs and injected dependencies must not be mutated externally during one
call. Bypassing frozen models or concurrently mutating their internal mappings
is illegal input and remains a non-goal. The graph itself does not mutate the
caller's request, identity, command, adapter, composer, Composition, or nested
artifact objects.

## 15. Exact implementation boundary

Implementation is limited to exactly four existing files.

Production:

```text
gpt_researcher/workflows/academic_writing/nodes.py
gpt_researcher/workflows/academic_writing/graph.py
```

Tests:

```text
tests/test_academic_writing_workflow_graph.py
tests/test_academic_writing_outline_approval.py
```

`workflow_outcome.py`, `academic_draft_composer.py`, `state.py`, adapters,
package initializers, every other production module, every other test, and all
frozen specifications remain unchanged.

A need for a fifth implementation file stops implementation and requires a new
impact audit and explicit reapproval.

## 16. Focused implementation test matrix

Tests use the real compiled StateGraph, `InMemorySaver` or a narrowly derived
recording saver, real `StateSnapshot`/`CheckpointTuple` surfaces, and injected
fake Composer dependencies that return strict frozen Compositions.

The permanent matrix contains only:

1. one ready lifecycle proving approval running checkpoint, exact execution
   view, one Composer call, minimal ready outcome, exact events, and END;
2. one parameterized non-ready lifecycle covering `blocked` and
   `needs_human_review`, minimal review outcome, deterministic restoration,
   and END;
3. rejection and legacy-approved terminal guards with zero Composer-related
   construction or calls;
4. exact facade return types and injected/default construction isolation;
5. ordinary failure and malformed-return classification with no partial
   outcome and zero later calls;
6. real cancellation identity/args and the unchanged approved/running
   checkpoint;
7. retry from Section 1 with the smallest vector that proves repeated Writer
   and Reviewer cost;
8. ready/review terminal resume, repeated approval, and replay guards with
   zero Composer calls;
9. synchronous durability using a controlled saver that blocks the
   approved/running `aput`, proves exact `durability="sync"` at `ainvoke`,
   observes zero Composer calls while blocked, and observes Composer start only
   after successful release;
10. terminal synchronous durability plus pre-write and post-write saver
    failures, followed by behavior selected from the last readable canonical
    checkpoint;
11. raw checkpoint assertions that reject stored Composition, Disposition,
    MergedDraft, Handoff, execution view, composer, factory, or client values;
12. legacy missing-outcome restoration and first-dump null canonicalization;
13. pending-node near misses: empty, extra, wrong, or reordered nodes;
14. the existing success/failure/cancellation reachability matrix extended
    with injected composer identity, empty-slot assertions, and traceback,
    frame, closure, and one-level exact-container identity checks.

Items 9 and 14 extend existing checkpoint and reachability tests. They add no
new test function, parameter case, generic walker, or safety framework.

The existing approval lifecycle matrices are revised rather than duplicated.
No test recreates the internal 3.5 through 3.14 parameter matrices.

Permanent tests must not include initial-red history, AST-only or symbol-only
checks, a new generic security walker, duplicate import guards, a full-suite
run, or multiple execution orders. Python module presence in `sys.modules` is
not a test target.

## 17. Explicitly deferred work

The following remain deferred with no implied milestone number:

- human-review resolution commands or resume decisions;
- FinalEditor or automatic rewriting;
- front matter generation;
- human-review UI or other frontend work;
- file export, persistence products, publication, or deployment;
- backend capacity limits, transaction protocols, leases, idempotency keys,
  or distributed locks;
- exactly-once LLM, Writer, Reviewer, billing, or checkpoint semantics;
- any additional graph outcome, branch, facade, or product router.

## 18. Stop conditions

Implementation must stop if any of these occurs:

1. a fifth file must change;
2. Milestone 3.13 or 4.1 public API or frozen behavior must change;
3. state.py, workflow_outcome.py, academic_draft_composer.py, an adapter, or a
   package initializer must change;
4. the real graph cannot expose a committed approved/running checkpoint before
   Composer execution;
5. the real graph cannot atomically write terminal phase, outcome, and events
   in one node state update;
6. old approved/completed checkpoints cannot remain terminal;
7. terminal guards cannot prevent a second Composer execution;
8. a universal rollback or exactly-once promise is required;
9. injected and production Composer construction cannot remain isolated;
10. human-review resolution or another deferred product feature becomes
    necessary for the two terminal outcomes.

## 19. Draft approval checklist

- [ ] The milestone is limited to Composer graph/checkpoint wiring.
- [ ] The exact approve-to-Composer-to-terminal topology is approved.
- [ ] Reject remains a direct historical terminal path.
- [ ] No dedicated composition facade is added.
- [ ] Start keeps its parameters and all three facades return PersistentState.
- [ ] Composer injection is separate from AcademicWritingAdapter.
- [ ] Only resume and submit accept the optional composer dependency.
- [ ] Deterministic module loading is distinguished from production construction.
- [ ] Module presence in sys.modules is explicitly not a delayed-construction failure.
- [ ] Production Composer construction is delayed until actual node execution.
- [ ] Every Composer-capable ainvoke uses exact synchronous durability.
- [ ] Approved/running aput completion strictly precedes every Composer call.
- [ ] Terminal aput completion strictly precedes successful facade return.
- [ ] Approval persists running/null outcome and emits no workflow completion.
- [ ] The in-memory approved/completed execution view is never persisted.
- [ ] Public Composer is called exactly once per node attempt.
- [ ] Ready stores only the referenced-draft outcome.
- [ ] Non-ready stores only drafts, Gate, and reviews.
- [ ] Composition, Disposition, merged draft, and Handoff are not persisted.
- [ ] Successful terminal phase, outcome, events, and workflow completion are atomic.
- [ ] Legacy approved/completed remains terminal and receives no outcome.
- [ ] New approved/running is the only Composer-resumable state.
- [ ] New ready and review-required outcomes cannot rerun Composer.
- [ ] Ordinary error and malformed-return classifications are approved.
- [ ] Genuine cancellation remains bare and identity-preserving.
- [ ] Composer failure before return leaves the last committed running checkpoint.
- [ ] Retry is explicitly at-least-once and may repeat Writer/Reviewer cost.
- [ ] Arbitrary checkpointer rollback and exactly-once are not promised.
- [ ] Readable terminal versus running checkpoint uniquely controls retry behavior.
- [ ] Injected dependency lifetime and retry reinjection are explicit.
- [ ] The one-shot slot and success/failure/cancellation cleanup contract is approved.
- [ ] Traceback and closure surfaces cannot retain the injected composer.
- [ ] No FailureCode or event literal is added.
- [ ] The exact four-file implementation boundary is approved.
- [ ] The focused real-graph test matrix is approved.
- [ ] Deferred product and backend work remains outside scope.
- [x] This specification received explicit approval before implementation began.

## 20. Implementation acceptance checklist

- [ ] `nodes.py` adds the Composer node without changing off-graph Composer code.
- [ ] `graph.py` replaces the approval-to-END edge with the frozen conditional route.
- [ ] The Composer node has one edge to END and no interrupt or loop.
- [ ] Start has no composer argument and returns exact PersistentState.
- [ ] Resume and submit accept only the frozen optional composer parameter.
- [ ] Every Composer-capable graph invocation passes exact durability sync.
- [ ] All facade-visible restores use the 4.1 PersistentState authority.
- [ ] Earlier nodes retain the base helpers only before approved/running persistence.
- [ ] Legacy missing outcome restores to None and first new dump writes null.
- [ ] Complete new checkpoint restoration preserves canonical equality.
- [ ] Approval success writes exactly approved/running with outcome None.
- [ ] Approval success events stop at node_completed outline_approval.
- [ ] Reject behavior, events, END routing, and Composer zero-call guard remain exact.
- [ ] Approved/running is committed before Composer begins cost-bearing work.
- [ ] A blocked approved/running aput holds all Composer-related call counts at zero.
- [ ] Runtime thread identity and predecessor shape are checked before construction.
- [ ] The execution view is an exact base AcademicWorkflowState approved/completed value.
- [ ] The execution view is absent from every checkpoint surface and mutable global.
- [ ] Injected Composer is selected without constructing the production Composer.
- [ ] Default production Composer is constructed once only inside the node.
- [ ] The compiled node closure captures only its per-call one-shot slot.
- [ ] The slot is consumed once, immediately emptied, and cannot be refilled.
- [ ] No pre-node Writer, Reviewer, Gate, Disposition, Merger, Renderer, or client call occurs.
- [ ] Each node attempt invokes public compose exactly once.
- [ ] An exact strict Composition is required before projection.
- [ ] Ready projects only the exact WorkflowDraftReadyOutcome.
- [ ] Non-ready projects only the exact WorkflowReviewRequiredOutcome.
- [ ] ReviewRequired restoration recomputes Disposition through the 4.1 contract.
- [ ] Terminal state stores no Composition, Disposition, MergedDraft, or Handoff.
- [ ] Both terminal branches have the exact eleven-event sequence.
- [ ] workflow_completed is emitted only in the successful Composer update.
- [ ] Terminal phase, outcome, Composer events, and completion are one checkpoint update.
- [ ] Ordinary Composer errors expose fixed ExecutionError with no partial outcome.
- [ ] Invalid Composer results expose fixed InvariantError with no partial outcome.
- [ ] Genuine CancelledError propagates as the same instance and args.
- [ ] Success, ordinary failure, and cancellation all leave the dependency slot empty.
- [ ] Facade finally cleanup releases the compiled graph, node closure, and dependency aliases.
- [ ] Return values and public traceback/frame/closure surfaces cannot reach the injected composer.
- [ ] Failure and cancellation before return leave approved/running as the last committed state.
- [ ] Resume retries Composer from Section 1 and proves possible repeated cost.
- [ ] A fresh facade call must re-supply an injected composer.
- [ ] Ready, review-required, reject, and legacy terminal guards execute zero Composer calls.
- [ ] Repeated approval after commit executes zero Composer calls.
- [ ] Pre-write saver failure leaves running visible and permits retry.
- [ ] Post-write saver failure with visible terminal prevents retry.
- [ ] Synchronous terminal aput completes before a successful facade return.
- [ ] Unreadable saver failure propagates without a recovery claim.
- [ ] Pending-node empty, extra, wrong, and reordered shapes fail before execution.
- [ ] Constructor, factory, Writer, Reviewer, and cost counters prove delayed construction.
- [ ] Tests do not assert module absence from sys.modules.
- [ ] Existing reachability tests cover composer identity using only identity-safe one-level expansion.
- [ ] Tests use the real compiled graph and inspect canonical snapshot/CheckpointTuple data.
- [ ] Existing approval matrices are updated without duplicate behavior cases.
- [ ] No initial-red, AST-only, symbol-only, duplicate walker/import guard, or multi-order test exists.
- [ ] Milestone 3.13 and 4.1 files and APIs have zero diff.
- [ ] Every file outside the exact four-file boundary has zero diff.
- [ ] Syntax validation, trailing-whitespace checks, and Markdown fence checks pass.
- [ ] `git diff --check` passes, staging is empty, and the worktree contains only this Draft before approval.
