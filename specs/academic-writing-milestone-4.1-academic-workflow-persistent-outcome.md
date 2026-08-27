# Academic Writing Milestone 4.1: Academic Workflow Persistent Outcome Contract

Status: **Approved and frozen**

This specification is a draft. It authorizes no implementation. Every approval
and implementation checkbox remains unchecked until explicit approval and the
later implementation evidence exist.

## 1. Goal and exact boundary

Milestone 4.1 defines the smallest checkpoint-safe state and outcome contract
needed by a later graph integration milestone. It does not connect the
Milestone 3.13 Composer to LangGraph and does not change the graph's current
execution path.

The future graph can persist exactly two new successful terminal outcomes:

```text
draft_ready/completed
    -> one bounded referenced-draft projection

review_required/completed
    -> drafts + Gate values + review values
       (Disposition is deterministically recomputed on restoration)
```

The checkpoint never stores a complete `WorkflowAcademicDraftComposition`, a
`WorkflowMergedDraft`, a `WorkflowCitationReviewDisposition`, or a
`WorkflowAcademicReviewHandoff`. This prevents duplicate persistence of the
same drafts, merged text, referenced text, and review material.

Milestone 4.1 also defines the nonterminal `outline_approved/running` state
that a later Composer node will consume. The current graph continues to commit
the legacy `outline_approved/completed` state until a separately approved 4.2
milestone changes the graph.

## 2. Frozen baselines and narrow supersession

The direct frozen baselines are:

```text
specs/academic-writing-milestone-3.0-langgraph-skeleton.md
specs/academic-writing-milestone-3.4-outline-approval-checkpoint.md
specs/academic-writing-milestone-3.7-section-merger.md
specs/academic-writing-milestone-3.9-citation-evidence-gate.md
specs/academic-writing-milestone-3.10-citation-reviewer-adapter.md
specs/academic-writing-milestone-3.11-citation-review-disposition.md
specs/academic-writing-milestone-3.12-references-renderer.md
specs/academic-writing-milestone-3.13-academic-draft-composer.md
specs/academic-writing-milestone-3.14-academic-review-handoff.md
specs/academic-writing-milestone-4.0-fixed-report-profiles.md
```

This milestone narrowly supersedes only:

1. the 3.0/3.4 `NodeId` set by adding `academic_draft_composer`;
2. the 3.0/3.4 phase/status reachability table and event goldens by adding the
   three new shapes in Section 7;
3. the 3.4 rule that a newly approved outline must immediately append
   `workflow_completed`, but only as a contract made available for 4.2; and
4. schema-1 canonical equality for the one legacy shape that omits the new
   `outcome` field and is then dumped once with `outcome:null`.

All existing request, topic, evidence, outline, approval, off-graph artifact,
LLM, citation, merge, reference, handoff, and fixed-profile contracts remain
unchanged unless this specification says otherwise.

## 3. Exact topology and import direction

`AcademicWorkflowState` does not gain an `outcome` field. `state.py` only adds
the new phases, the new node ID, and their state/event reachability rules.

The new module is:

```text
gpt_researcher/workflows/academic_writing/workflow_outcome.py
```

It imports `AcademicWorkflowState`, `WorkflowSectionDraft`, and the required
public artifact DTOs. It defines the two outcome DTOs and the persistent-state
subclass. `state.py` must not import `workflow_outcome.py`.

The frozen import DAG is:

```text
state.py -> report_profiles.py

artifact modules -> state.py

workflow_outcome.py
    -> state.py
    -> citation_evidence_gate.py
    -> citation_reviewer.py
    -> citation_review_disposition.py
    -> references_renderer.py
    -> academic_draft_composer.py

future nodes.py/graph.py -> workflow_outcome.py
```

No import points back from any listed dependency to `workflow_outcome.py`.
There is no dynamic import, annotation lookup, model-field reflection, package
initializer export, or second `{workflow, outcome}` channel/wrapper.

The existing LangGraph channel remains exactly `{"workflow": <mapping>}`. The
mapping is the complete persistent-state JSON object and contains `outcome` as
one field.

## 4. Exact public surface and DTOs

`workflow_outcome.py.__all__` contains exactly:

```python
__all__ = (
    "WorkflowDraftReadyOutcome",
    "WorkflowReviewRequiredOutcome",
    "WorkflowOutcome",
    "AcademicWorkflowPersistentState",
)
```

The strict DTOs are:

```python
class WorkflowDraftReadyOutcome:
    outcome_type: Literal["draft_ready"]
    referenced_draft: WorkflowReferencedDraft


class WorkflowReviewRequiredOutcome:
    outcome_type: Literal["review_required"]
    drafts: tuple[WorkflowSectionDraft, ...]
    gate_result: WorkflowCitationEvidenceGateResult
    reviews: tuple[WorkflowSectionCitationReview, ...]


WorkflowOutcome = Annotated[
    WorkflowDraftReadyOutcome | WorkflowReviewRequiredOutcome,
    Field(discriminator="outcome_type"),
]


class AcademicWorkflowPersistentState(AcademicWorkflowState):
    outcome: WorkflowOutcome | None = None
```

Both outcome DTOs and the subclass use strict, frozen, extra-forbid Pydantic
configuration and are JSON-compatible. `outcome_type`, every primitive, every
tuple, every nested DTO, and integer one must retain the exact type required by
its frozen upstream contract. Boolean coercion, string subclasses, list input
in Python mode, unknown discriminator values, missing/extra fields, and wrong
nested DTO types are rejected. JSON arrays restore to the frozen tuple forms.

The union discriminator is the exact `outcome_type` field. Union trial order,
duck typing, or exception-dependent fallback is forbidden.

## 5. Persistent state restoration and canonical form

Relative to the complete inherited `AcademicWorkflowState` field set, the
only accepted persistent-state input shapes are:

1. the complete inherited field set plus `outcome`; or
2. exactly the complete inherited field set, with only `outcome` absent.

No inherited field may be missing and no other field may be present. Static
Python extraction validates the exact model/mapping, exact built-in
`__dict__`, fields-set, extra/private state, and all exact-string keys/members
before equality, membership, subscription, or hashing-dependent construction.
It does not invoke instance methods, equality, `repr`, descriptors, dynamic
attributes, string-subclass code, or untrusted iterators.

Missing `outcome` restores to exact `None`. Explicit JSON `null` also restores
to exact `None`; every other explicit wrong type is rejected. The restoration
algorithm does not infer that an object came from a historical checkpoint.
It recognizes only the mechanical field shape.

Private module helpers provide the sole future graph serialization boundary:

```text
AcademicWorkflowPersistentState
    -> exact model_dump(mode="json")
    -> validate strict JSON domain
    -> {"workflow": complete persistent mapping}

{"workflow": complete or legacy mapping}
    -> canonical JSON
    -> AcademicWorkflowPersistentState.model_validate_json(...)
    -> exact recursive value/type and canonical equality
```

The existing base-only `workflow_to_graph_state()` and
`restore_workflow_state()` remain unchanged and are not authoritative for new
4.1 phases. Future 4.2 approval/Composer/terminal paths must use the private
persistent helpers. Earlier graph nodes may continue using the base helpers
before an approved-running persistent checkpoint exists.

## 6. Schema version and legacy checkpoint behavior

`schema_version` remains exactly `"1"`.

A legacy checkpoint contains an old reachable phase/status/artifact/event shape
and omits `outcome`. It restores directly as
`AcademicWorkflowPersistentState(outcome=None)`. Its first new dump adds only:

```json
"outcome": null
```

This is the sole 4.1 schema-1 one-way canonicalization exception. The old bytes
need not equal the first new dump. After that dump, complete new payloads must
retain recursive JSON value/type equality, model equality, and canonical-byte
equality across restoration.

Legacy `outline_approved/completed` remains a completed historical thread. It
is never reclassified as `outline_approved/running`, never automatically runs
the Composer, and never receives a synthesized outcome.

## 7. Exact phase, status, decision, event, and outcome matrix

`NodeId` adds exactly:

```text
academic_draft_composer
```

No `FailureCode` and no `WorkflowEvent.event_type` literal is added.
`workflow_completed` continues to use `node_id=None`. Composer execution
exceptions and cancellation do not create a new business failure code; a later
4.2 run leaves the last committed `outline_approved/running` checkpoint and
may retry the whole Composer at least once.

The existing six-event outline pause prefix is followed by these new event
shapes:

```text
outline_approved/running
  7  node_started       outline_approval
  8  node_completed     outline_approval

draft_ready/completed or review_required/completed
  7  node_started       outline_approval
  8  node_completed     outline_approval
  9  node_started       academic_draft_composer
 10  node_completed     academic_draft_composer
 11  workflow_completed -
```

The exact persistent reachability matrix is:

| State shape | Outcome | Result |
|---|---|---|
| Any old reachable 3.0/3.4 shape | missing or `None` | valid |
| Any old reachable shape | either non-null outcome | reject |
| `outline_approved/running` | `None` | valid |
| `outline_approved/running` | non-null | reject |
| `draft_ready/completed` | matching `WorkflowDraftReadyOutcome` | valid |
| `review_required/completed` | matching `WorkflowReviewRequiredOutcome` | valid |
| Either new completed phase | missing, `None`, or wrong branch | reject |
| Either new phase with `running`/`failed`, except approved-running above | reject |
| Any other phase/status/event/decision/artifact combination | reject |

Every approved-running or new completed state has the approved outline decision
and all existing topic/evidence/outline artifacts. Errors are empty. The
decision identity and digest continue to match the outline. The base state
validator checks state/event reachability; the persistent subclass alone is
the authority for the phase/outcome relationship.

## 8. Draft-ready outcome contract

The referenced draft is restored through the complete public Milestone 3.12
DTO contract. In addition:

1. its outline ID equals the persistent state's outline ID;
2. its section IDs equal the approved outline IDs in exact order;
3. its attempt is exact integer one;
4. `reference_source_ids` is an exact tuple containing one through 64 exact,
   unique, nonempty source IDs;
5. every ID exists in the state's evidence sources; and
6. no merged draft, Gate, reviews, Disposition, Composition, or Handoff is
   present in this outcome.

Draft-ready restoration does not scan or parse the referenced content, rerun
the Renderer, or mechanically re-prove marker-derived stable-first-wins order.
The 64-ID rule freezes only the bounded shape expected from a real Milestone
3.13 Composer result. A future 4.2 may write only the ready output returned by
that Composer into this outcome, but 4.1 cannot prove that the Composer, Gate,
Reviewer, or Renderer actually ran. It does not prove claims are supported or
that the draft is factually correct or publishable. A structurally valid
fabricated `WorkflowReferencedDraft` can pass and cannot be distinguished.

## 9. Review-required restoration and validation

The unique restoration order is:

1. statically restore every exact draft, Gate field, and review field;
2. validate one through 12 drafts and the complete positional outline/section,
   citation, attempt, order, and uniqueness bindings;
3. flatten `gate_result.cited_source_ids_by_section` in section order and apply
   stable first-wins;
4. require one through 64 global unique cited IDs;
5. call public `gate_citation_review_disposition(gate_result, reviews)` exactly
   once;
6. require the result to be exactly `blocked` or `needs_human_review`;
7. reconstruct a fresh trusted `WorkflowAcademicDraftComposition` with the
   restored drafts, Gate, reviews, recomputed Disposition,
   `merged_draft=None`, and `referenced_draft=None`;
8. require the complete Milestone 3.13 non-ready binding, normalization, and
   1,526,996-byte Composition cap; and
9. discard the recomputed Disposition and temporary Composition before the
   persistent state is returned.

No Disposition, Composition, merged draft, referenced draft, or Handoff is
saved in the review-required outcome. Restoration never reruns the Section
Writer, Citation Reviewer, Retriever, LLM, or network work.

This branch proves only that the stored pure values form a valid non-ready
Composition shape and deterministically route to human review. It does not
prove Reviewer execution, review correctness, claim support, evidence
sufficiency, absence of contradiction, common external provenance, or human
resolution.

## 10. Exact bounded outcome increments

The complete `AcademicWorkflowPersistentState` has no finite natural canonical
maximum. It inherits unbounded request, topic/question, and freeform-outline
surfaces from `AcademicWorkflowState`. Milestone 4.1 does not add limits to
those fields, does not impose a global checkpoint cap, and does not add an
artificial PersistentState cap-plus-one test. Backend capacity policy remains
deferred.

Only the newly stored outcome increment is bounded.

### 10.1 Draft-ready natural maximum

The maximum canonical `WorkflowReferencedDraft` is 8,596,240 UTF-8 bytes.
The exact outcome wrapper adds 50 bytes:

```text
{"outcome_type":"draft_ready","referenced_draft":<DTO>}

wrapper excluding nested DTO = 50
8,596,240 + 50 = 8,596,290 UTF-8 bytes
```

A legal 12-section, 64-reference DTO reaches the nested cap. Its referenced
draft has a 1,901-byte fixed portion. The content JSON contribution is:

```text
1,432,389 NUL code points * 6 bytes = 8,594,334
1 four-byte Unicode code point          =         4
1 ASCII code point                      =         1
total content contribution              = 8,594,339
1,901 + 8,594,339                       = 8,596,240
```

The outcome then reaches exactly 8,596,290 bytes. This is the natural strict
DTO maximum, not a claim that the same vector maximizes an entire real Composer
call or a complete persistent state.

### 10.2 Review-required natural maximum

For the corresponding trusted non-ready Composition:

```text
canonical empty Composition skeleton                         104 bytes
four variable placeholders at 2 bytes each                  - 8 bytes
Composition fixed bytes                                      96 bytes

Composition = components + Disposition + 96 fixed bytes
Outcome     = components + 70 fixed bytes
Outcome     = Composition - Disposition - 26
```

At 12 sections the smallest legal non-ready Disposition is the all-`blocked`
432-byte DTO. The 3.13 cap is reachable simultaneously:

```text
drafts array                         1,337,616
Gate                                    19,519
reviews array                           169,333
all-blocked Disposition                     432
Composition fixed bytes                     96
                                      ---------
valid non-ready Composition          1,526,996

drafts array                         1,337,616
Gate                                    19,519
reviews array                           169,333
review-outcome fixed bytes                  70
                                      ---------
WorkflowReviewRequiredOutcome        1,526,538
```

The Gate uses 12 sections with 64 cited IDs per section and a global
stable-first-wins set of 64 IDs. Each of the 12 blocked reviews reaches its
14,110-byte DTO limit. Draft content is distributed within the existing
24,576-code-point per-section limits to make the complete Composition exactly
1,526,996 bytes.

These two maxima are verified only with legal successful construction vectors.
There is no wrapper cap validator and no wrapper-only max-plus-one test because
the nested DTO and Composition contracts already establish the bounds.

For a non-null outcome, adding the field to an otherwise identical canonical
base-state object contributes:

```text
1 comma + len('"outcome":') + outcome bytes = 11 + outcome bytes
```

Since the base-state term is unbounded, this formula does not create a finite
complete-PersistentState maximum.

## 11. Failure, safety, and mutation boundary

All outcome and persistent-state validation failures use one fixed private safe
contract error with fixed text, no dynamic values, no partial state, and
`cause/context=None`. Before raising, validation releases the input state,
outcome, drafts, Gate, reviews, recomputed Disposition/Composition, referenced
draft, canonical bytes, and temporary collections. Fixed-error traceback frame
locals and closures must not retain full draft text, referenced content, review
rationale, or live input models.

Raw Pydantic validation remains internal to the fixed conversion boundary.
Successful restoration may return the validated nested artifact values held by
the new persistent DTO. No mutable module-global cache, last-result registry,
I/O, network, Config, Provider, Retriever, LLM, async task, or cancellation
contract is introduced.

Inputs and their nested Pydantic mappings must not be mutated externally during
one validation call. Bypassing frozen models through `object.__setattr__`,
`__dict__`, nested containers, another thread, callback, or signal handler is
illegal concurrent input and a non-goal. The implementation itself never
modifies an input.

## 12. Exact future implementation boundary

Implementation is limited to exactly four files:

```text
modify gpt_researcher/workflows/academic_writing/state.py
add    gpt_researcher/workflows/academic_writing/workflow_outcome.py
modify tests/test_academic_writing_workflow_state.py
add    tests/test_academic_writing_workflow_outcome.py
```

No package initializer, graph, node, facade, adapter, Composer, Gate, Reviewer,
Disposition, Merger, Renderer, Handoff, dependency, or other test file may be
modified. A fifth-file requirement is a stop condition.

## 13. Focused implementation test matrix

The two test files use small parameterized behavior matrices covering:

1. strict/frozen/JSON behavior and exact discriminated-union restoration;
2. legacy missing outcome, explicit null, first-dump canonicalization, and new
   canonical equality;
3. every row of the phase/status/event/decision/outcome matrix;
4. the new Composer node ID and unchanged event/FailureCode literals;
5. draft-ready outline/section/attempt/source binding and the 1..64 rule;
6. review-required positional binding, stable first-wins global 1..64, one
   Disposition call, non-ready result, and trusted Composition reconstruction;
7. wrong branches, null new terminals, malformed nested values, 65 global IDs,
   and old phases carrying an outcome;
8. exact 8,596,290 and 1,526,538 successful maximum vectors;
9. the inherited unbounded-state fact without introducing a huge allocation or
   artificial global-cap test;
10. hostile mapping keys, fields-set members, string subclasses, and instance
    shadows with zero dynamic calls;
11. fixed-error non-reachability using the existing local safety-test pattern;
    and
12. unchanged input values and no external calls.

There is no permanent initial-red, symbol-existence test, AST-only test,
generic import guard, repeated walker, separate security framework, full-suite
requirement, multiple file-order regression, wrapper max-plus-one case, or
test that repeats all upstream artifact matrices.

## 14. Explicitly deferred work

Milestone 4.1 does not implement:

- Composer graph execution or approval-edge changes;
- the ephemeral approved/completed execution view needed by the frozen 3.13
  Composer input contract;
- at-least-once crash/resume execution and repeated LLM-cost tests;
- human-review resolution, graph interrupt, or resumption command;
- exactly-once LLM semantics;
- checkpoint backend capacity policy or a PersistentState global cap;
- FrontMatter, FinalEditor, rewriting, publishing, export, persistence backend,
  UI, frontend, or API mounting.

A future 4.2 requires its own read-only impact audit before a file boundary is
frozen. In particular, the existing outline-approval tests directly freeze the
legacy approve terminal state and may require revision when approval stops
committing `outline_approved/completed`. Milestone 4.1 freezes no 4.2 file
count, implementation boundary, or stop condition.

## 15. Stop conditions

Implementation must stop before changes if any of the following is found:

1. `state.py` must import `workflow_outcome.py`;
2. the outcome requires a second graph channel or `{workflow, outcome}` wrapper;
3. a fifth implementation file is required;
4. the legacy missing-outcome checkpoint cannot restore with schema version 1;
5. either outcome maximum is not reachable by its stated legal vector;
6. ReviewRequired restoration cannot reuse the public Disposition Gate exactly
   once and the existing non-ready Composition contract;
7. the implementation needs a global PersistentState cap or new limits on old
   state fields; or
8. 3.13 or another frozen off-graph API must change.

## 16. Draft approval checklist

- [ ] The goal is limited to a persistent state/outcome contract and does not connect the graph.
- [ ] `AcademicWorkflowState` gains phases and a node ID but no outcome field.
- [ ] The persistent subclass adds only the defaulted outcome field.
- [ ] The exact four-member public surface is approved.
- [ ] The two exact outcome DTO field sets are approved.
- [ ] The discriminated union uses only exact `outcome_type`.
- [ ] The one-way import DAG and absence of a wrapper/channel are approved.
- [ ] The schema version remains exactly `"1"`.
- [ ] Missing outcome restores to `None` and first dump writes null.
- [ ] Legacy approved/completed remains a historical terminal state.
- [ ] Approved/running has eight events and no outcome.
- [ ] Both new completed phases have eleven events and matching outcomes.
- [ ] NodeId adds only `academic_draft_composer`.
- [ ] Event types and FailureCode remain unchanged.
- [ ] Draft-ready stores only one referenced draft projection.
- [ ] Review-required stores only drafts, Gate values, and review values.
- [ ] Review-required recomputes Disposition exactly once and stores none of its temporary values.
- [ ] The proof limitations and fabricated-artifact limitation are explicit.
- [ ] DraftReadyOutcome naturally reaches exactly 8,596,290 bytes.
- [ ] ReviewRequiredOutcome naturally reaches exactly 1,526,538 bytes.
- [ ] The full PersistentState is explicitly and intentionally unbounded.
- [ ] No global state/checkpoint cap or artificial cap-plus-one contract is introduced.
- [ ] Fixed safe errors and the external-mutation non-goal are approved.
- [ ] The exact four-file implementation boundary is approved.
- [ ] Graph integration, exactly-once behavior, backend capacity, and product extensions remain deferred.
- [x] This specification received explicit approval before implementation began.

## 17. Implementation acceptance checklist

- [ ] `state.py` adds only the frozen phase and NodeId contract changes.
- [ ] `state.py` does not import `workflow_outcome.py`.
- [ ] `workflow_outcome.py` follows the frozen acyclic import DAG.
- [ ] `__all__` contains exactly the four approved members.
- [ ] Both outcome DTOs are strict, frozen, extra-forbid, and JSON-compatible.
- [ ] Python tuple, exact primitive, strict integer, and string-subclass boundaries pass.
- [ ] JSON arrays restore to exact tuples without weakening Python-mode strictness.
- [ ] The exact discriminator rejects missing, unknown, subclass, and wrong-branch values.
- [ ] The persistent subclass inherits the complete state contract and adds only outcome.
- [ ] Persistent fields-set accepts only complete or exactly outcome-missing shape.
- [ ] Missing outcome restores to exact None and dumps as JSON null.
- [ ] Legacy first-dump bytes differ only by the approved defaulted field.
- [ ] Complete new payload restoration preserves recursive, model, and canonical equality.
- [ ] Old reachable states accept only null outcome.
- [ ] Legacy outline-approved/completed remains terminal and is not upgraded.
- [ ] Outline-approved/running accepts only null outcome and the exact eight events.
- [ ] Draft-ready/completed accepts only the matching ready outcome and eleven events.
- [ ] Review-required/completed accepts only the matching review outcome and eleven events.
- [ ] Every wrong phase/status/outcome combination fails with the fixed error.
- [ ] Decision identity, digest, artifacts, errors, and event ordering remain closed.
- [ ] `academic_draft_composer` is the only new NodeId.
- [ ] WorkflowEvent event types and FailureCode have no additions.
- [ ] Ready referenced outline, ordered sections, attempt, source membership, uniqueness, and exact tuple preservation bind mechanically.
- [ ] Ready rejects zero, 65, duplicate, and unknown reference IDs.
- [ ] Review drafts, Gate, and reviews are statically restored through their exact contracts.
- [ ] Review positional outline/section/citation/attempt bindings are complete.
- [ ] Review global citation flattening is stable first-wins and accepts only 1..64 IDs.
- [ ] Public Disposition Gate is called exactly once during review restoration.
- [ ] Ready Disposition is rejected from ReviewRequired restoration.
- [ ] Blocked and needs-human-review Dispositions both restore successfully.
- [ ] A fresh non-ready Composition enforces all inherited binding and the 1,526,996-byte cap.
- [ ] Disposition, Composition, merged draft, referenced draft, and Handoff are absent from stored review outcome.
- [ ] The 8,596,290-byte ready maximum succeeds with 64 IDs.
- [ ] The 1,526,538-byte review maximum succeeds with 12 sections and 64 global IDs.
- [ ] No wrapper cap validator or wrapper-only max-plus-one test exists.
- [ ] Tests mechanically document the inherited lack of a finite PersistentState maximum.
- [ ] No old request/topic/question/freeform bound changes.
- [ ] Private persistent dump/restore uses the sole existing workflow channel.
- [ ] The old base helpers remain unchanged and are not used as authority for new terminal checkpoints.
- [ ] Hostile keys, fields-set members, string subclasses, and shadows execute zero dynamic code.
- [ ] Fixed failures have constant text, no cause/context, and no partial persistent state.
- [ ] Failure traceback/locals/closures do not retain sensitive nested artifacts or canonical payloads.
- [ ] Successful restoration returns complete trusted values and does not mutate input.
- [ ] No LLM, Retriever, Config, Provider, network, file, async, or mutable-global behavior is added.
- [ ] Tests contain no initial-red, AST-only, symbol-only, duplicate walker/import guard, or multi-order regression.
- [ ] The two focused test files cover every acceptance item without copying upstream suites.
- [ ] Syntax/AST validation passes for the two production files.
- [ ] The frozen specification and every file outside the four-file boundary have zero diff.
- [ ] Trailing whitespace is zero and Markdown fences are balanced.
- [ ] `git diff --check` passes, the staging area is empty, and the worktree contains only the specification before approval.
