# Academic Writing Milestone 3.4 — Academic Outline Approval Checkpoint Protocol

Status: **Approved and frozen**

This specification is approved and frozen and authorizes implementation only
within the boundaries defined below. The explicit approval record is checked;
implementation acceptance items remain unchecked until implementation is
completed and verified.

## 1. Goal

Milestone 3.4 adds one checkpoint-native decision boundary after a committed
academic outline:

```text
WorkflowTopicPlan
+ WorkflowResearchEvidence
+ WorkflowOutline
        ↓
outline_approval interrupt checkpoint
        ↓
approve → outline_approved/completed
reject  → outline_rejected/completed
```

The workflow must not enter body generation while an outline is unapproved.
The pause is represented by LangGraph checkpoint state, not by an in-process
event, UI flag, websocket, or process-local object. A decision is accepted only
when a strict command is bound to the same workflow, thread, run, outline ID,
and canonical outline revision.

Approve is a successful terminal result. Reject is a non-failure terminal
result. Wrong identity, stale revision, invalid command, repeated decision, an
ordinary-resume attempt, or an unexplained checkpoint shape produces zero graph
execution, zero new events, and zero application-state change.

The graph-operating public surface is exactly
`start_academic_workflow()`, `resume_academic_workflow()`, and
`submit_academic_outline_decision()`. No fourth facade, adapter, backend,
frontend, or direct caller may invoke or inspect the private compiled graph.

## 2. Normative precedence and supersession

The following table is exhaustive. There is no general "newer document wins"
rule.

| Earlier frozen contract | Earlier rule | Milestone 3.4 replacement |
| --- | --- | --- |
| 3.0 Section 2 and Section 15 roadmap | Milestone 3.4 was named CitationReviewer | 3.3's most recently approved boundary controls numbering: 3.4 is outline approval; CitationReviewer remains later work |
| 3.0 Sections 7–8 | `outline_ready/completed` with seven events is the sole successful terminal shape | `outline_ready/running` with six events is the approval pause; approve and reject are the two completed terminal shapes |
| 3.0 Section 11 | `outline_writer` routes to `END` and emits `workflow_completed` | successful outline writing routes to `outline_approval`; only an approved decision emits `workflow_completed` |
| 3.0 Section 12.2 start facade | the raw `ainvoke` result is restored directly | after invocation, the facade always calls `aget_state()` and restores the checkpoint application state |
| 3.0 Section 12.2 resume facade | every running state with empty `next` is non-resumable and every valid nonempty `next` is technically resumed | ordinary resume rejects both approval availability shapes and never constructs a decision command |
| 3.0 Section 3.2 production API allowlist | no interrupt API | production adds only `interrupt` and `Command(resume=...)`; every other production prohibition remains |
| 3.0 Section 3.2 production-and-test API allowlist | saver-tuple inspection is not allowed in production or tests | approved 3.4 tests narrowly add only `InMemorySaver.aget_tuple()` for the inspection purposes in this specification's Section 5; this test-only exception never enters the production allowlist |
| 3.0 Section 5 module, graph, facade, and import boundary | only `start_academic_workflow()` and `resume_academic_workflow()` may invoke or inspect the private compiled graph | exactly `start_academic_workflow()`, `resume_academic_workflow()`, and `submit_academic_outline_decision()` may invoke or inspect it; the graph remains private, only these controlled facades construct `RunnableConfig`, only the decision facade constructs `Command(resume=...)`, package initializers add no export, and every other Section 5 import and side-effect rule remains unchanged |
| 3.0 Section 7 `AcademicWorkflowIdentity` and `AcademicWorkflowRequest` | both DTOs carry `workflow_id`, `thread_id`, and `run_id`; request identity exactly equals `AcademicWorkflowIdentity` | 3.4 adds only a 256-code-point cap and strict string validation to those three fields; their equality relationship and guard priority remain unchanged |
| 3.0 Section 8.1 `WorkflowTopicPlan` | the plan carries `workflow_id` and `run_id`, and does not carry `thread_id` | 3.4 adds only the same 256-code-point cap and strict string validation to the two existing plan identity fields; it does not add `thread_id` or change any plan-to-request identity relationship |
| 3.0 Section 9.4 `AcademicWorkflowState` | state carries `workflow_id`, `thread_id`, and `run_id`, and state identity must exactly match request identity | 3.4 adds only the same 256-code-point cap and strict string validation to those three state fields; restoration equality and guard priority remain unchanged |
| 3.1–3.3 graph success goldens | outline success ends with event 7 and `next == ()` | full-graph goldens stop with six events at a normal approval pause |
| 3.3 Section 20 | approval, pause, and modification are deferred to 3.4 | this specification implements pause plus approve/reject only; editing remains deferred |

The historical 3.0 text is not edited. All 3.0–3.3 requirements not listed in
this table remain normative, including strict DTO restoration, single-channel
application state, identity rules, safe fixed exceptions, injected
checkpointers, offline tests, and external-I/O fail-fast behavior.

## 3. Scope

### 3.1 In scope

- `outline_ready/running` checkpoint pause;
- `outline_approved/completed` successful terminal state;
- `outline_rejected/completed` non-failure terminal state;
- strict decision command and minimal decision record DTOs;
- canonical `WorkflowOutline` revision digest;
- native `interrupt()` and `Command(resume=...)`;
- one private approval node and graph edge;
- one public dedicated decision facade;
- pause-aware start and ordinary-resume guards;
- complete event, snapshot, cancellation, replay, and safety goldens;
- `InMemorySaver`-only offline tests and mechanical updates to old goldens.

### 3.2 Non-goals

Milestone 3.4 does not implement SectionWriter, body generation, parallel
writing, CitationReviewer, FinalEditor, same-thread outline regeneration,
outline attempt 2+, free-text edits, approve-with-edits, reject loops,
backend/frontend, REST, websocket transport, product authentication or
authorization, persistent savers, databases, ReportStore, exports,
notifications, email, external messages, `Send`, subgraphs, streaming, stores,
or changes to any 3.1–3.3 adapter production implementation.

Reject-to-regenerate requires a new run and thread created outside 3.4. The
milestone neither creates that run nor exposes a helper that does so.

## 4. Local LangGraph 1.2.11 mechanical baseline

The design is frozen to local LangGraph 1.2.11 behavior. The investigation was
pure-memory, used `StateGraph` plus `InMemorySaver`, and performed no network,
provider, Config, retriever, MCP, file-write, or pytest operation.

The local signatures are:

```python
interrupt(value: Any) -> Any

Command(
    *,
    graph: str | None = None,
    update: Any | None = None,
    resume: dict[str, Any] | Any | None = None,
    goto: Send | Sequence[Send | N] | N = (),
) -> None
```

The broad installed annotations are an observation, not permission to use
`Any`, `Send`, `goto`, `graph`, or `update` in 3.4 production code. Production
uses only `Command(resume=<strict JSON mapping>)` and `interrupt(<strict JSON
mapping>)`.

### 4.1 Stable snapshot shape A: normal pause

After the first approval-node entry:

```text
values.phase                     = outline_ready
values.status                    = running
values.outline_decision          = None
next                             = ("outline_approval",)
tasks count                      = 1
task.name                        = outline_approval
task.path                        = ("__pregel_pull", "outline_approval")
task.error                       = None
task.interrupts count            = 1
task.interrupts[0].value         = exact safe interrupt payload
task.result                      = None
task.state                       = None
metadata                         = {"source": "loop", "step": 3, "parents": {}}
created_at                       = non-None ISO string
config checkpoint ID            = nonempty generated string
parent_config checkpoint ID     = nonempty generated string
```

The probe's isolated one-node graph reported metadata step `0`; the production
four-node graph must mechanically golden its corresponding step as `3`. IDs,
timestamps, interrupt IDs, and checkpoint version suffixes are generated and
are checked by type, relationship, and nonblankness rather than literal value.

The `CheckpointTuple` has the same config, metadata, parent config, and
checkpoint channel values as the snapshot. Its pending writes contain exactly
one `__interrupt__` write for the approval task. The checkpoint channel values
contain the single workflow channel in production, and no decision record.

The raw pause `ainvoke` result contains `__interrupt__`. It is never passed to
`restore_workflow_state()` and is never returned by a public facade.

### 4.2 Stable snapshot shape B: decision pre-commit ordinary failure

When a validated resume reaches the approval node and a normal exception occurs
before the terminal state update is committed:

```text
values.phase                     = outline_ready
values.status                    = running
values.outline_decision          = None
next                             = ()
tasks count                      = 1
task.name                        = outline_approval
task.path                        = ("__pregel_pull", "outline_approval")
task.error                       = one exact serialized task-error string marker
task.interrupts count            = 1
task.interrupts[0].value         = the original exact safe interrupt payload
task.result                      = exact empty dict
task.state                       = None
metadata                         = unchanged pause metadata
created_at/config/parent_config  = unchanged pause checkpoint values
```

The two allowed exact serialized task-error string markers are:

```text
_OutlineApproveCommitError('academic outline decision commit failed')
_OutlineRejectCommitError('academic outline decision commit failed')
```

Both exception classes and their constructors are private. Their identical
fixed message contains no dynamic value. LangGraph 1.2.11 with JsonPlus exposes
the failure through `StateSnapshot.tasks` as an exact built-in `str`, not as an
exception object. The facade recognizes only the complete serialized string;
it does not recover the first decision from saver internals or add a LangGraph
API.

The `CheckpointTuple.pending_writes` mechanically contains, in order:

1. the existing task `__interrupt__` write;
2. the null-task-ID `__resume__` write containing the bounded validated resume
   mapping;
3. the approval-task `__resume__` write containing a one-element list of that
   mapping;
4. the approval-task `__error__` write containing the exact safe error string.

LangGraph therefore retains the first resume value. A later different decision
does not replace it: an approve crash followed by a raw reject command completes
as approve, and the reverse completes as reject. The dedicated facade must
reject a changed decision before `ainvoke`.

### 4.3 Stable Command-resume cancellation shape

When the caller cancels while the resumed approval node is blocked after
`interrupt()` has returned but before it returns an update, the cancellation
snapshot has the same complete `values`, `next`, one task, task ID/name/path,
`error=None`, one original interrupt, `result=None`, `state=None`, metadata,
created-at, config, and parent-config shape as A. The `CheckpointTuple` has the
same checkpoint and metadata; pending writes contain the original
`__interrupt__` write followed by one null-task-ID `__resume__` write holding
the bounded validated mapping. There is no task-level resume, error, result, or
application-channel write. The caller observes `CancelledError`; the original
message is its first arg.

This is mechanically distinguishable from B because `next` is the approval
node, task error is `None`, and task result is `None`. It is eligible as shape A
and a later newly validated decision is used.

### 4.4 Stable successful terminal shape

After a successful retry or first successful decision:

```text
values.phase                     = outline_approved or outline_rejected
values.status                    = completed
values.outline_decision          = exact committed record
next                             = ()
tasks                            = ()
metadata.source                 = loop
metadata step                    = pause step + 1
created_at                       = non-None
config                           = new terminal checkpoint config
parent_config                    = the pause checkpoint config
CheckpointTuple.pending_writes   = ()
```

The checkpoint channel contains the terminal application state. It contains no
second copy of the outline.

### 4.5 Command identity and retry result

The same `Command` instance and a newly constructed value-equal `Command` both
successfully replay after shape B. Python object identity is not stored or
compared. A newly constructed command with the opposite decision is ignored by
raw LangGraph replay in favor of the first pending resume value. Consequently,
this specification selects retry semantic **B**:

> A pre-commit failure has not committed an application decision, but its
> checkpoint mechanically retains the first decision. Only the same decision
> may be retried. A new, strictly validated Command is constructed every time.

The original bounded actor assertion in the first retained resume payload is
the assertion committed on successful replay. A retry actor assertion is still
strictly validated but does not replace that original assertion. Actor
assertions are caller statements, not authentication evidence.

Shape B's `aget_state()` surface does not expose the first actor assertion.
Production code therefore never reads, recovers, or compares that actor, and it
never calls `aget_tuple()` or inspects pending writes for actor, decision, or
retry eligibility. Shape-B eligibility does not compare old and new actors.
LangGraph retains and replays the complete first validated resume payload.

The two actor-retry vectors are independently normative:

- approve by actor A reaches the approve commit marker; a later strictly valid
  approve by actor B may drive replay, but the terminal decision is approve and
  the decision record actor is exactly A; B is absent from the record;
- reject by actor A reaches the reject commit marker; a later strictly valid
  reject by actor B may drive replay, but the terminal decision is reject and
  the decision record actor is exactly A; B is absent from the record.

The retry actor is validated as the bounded caller assertion for that facade
call and is not written to the terminal record. A different retry decision is
still rejected from the serialized decision marker before `ainvoke`. The core
protocol intentionally accepts that a later caller authorized by the product
layer may drive the retained first decision to completion, but cannot rewrite
its actor or decision. It does not claim that an actor assertion is
authentication, authorization, or proof that only the same actor may retry.
The future product mount must authenticate and authorize every caller before it
invokes the dedicated facade.

### 4.6 Raw invocations that public facades must prevent

- `ainvoke(None)` at shape A returns the same pause.
- `ainvoke(None)` at shape B replays the retained decision and can commit it.
- an invalid raw resume at shape A creates an unrecognized fixed invariant task
  error and `next == ()`;
- a raw command at a terminal checkpoint returns the old terminal state but can
  add a pending `__resume__` write.

Therefore ordinary resume must reject both A and B, and a terminal dedicated
decision must be rejected before graph invocation. Raw compiled graphs remain
private.

## 5. LangGraph API contract

Production adds exactly:

```python
from langgraph.types import Command, interrupt
```

Existing 3.0 production APIs remain allowed. `interrupt` and
`Command(resume=...)` are the only production additions. Production state,
node, graph, and facade code must not use any of:

```text
InMemorySaver.aget_tuple()
get_tuple()
aget_tuple()
get_state()
update_state()
Send
RetryPolicy
store
subgraph
streaming
```

Production must not read a `CheckpointTuple` or pending writes to recover a
decision, actor, or retry eligibility. Existing prohibitions on `astream` and
new durability APIs also remain.

Approved 3.4-related tests have exactly one narrow additional inspection API:

```text
InMemorySaver.aget_tuple()
```

This is an explicit test-only supersession of 3.0 Section 3.2's unified
production-and-test allowlist. Only the new approval test and the approved
related tests may use it, and only to read the real `CheckpointTuple`, inspect
checkpoint data, metadata, and pending writes, verify interrupt/resume/error
write ordering, prove sensitive objects unreachable, and demonstrate that a
raw terminal `Command` may add a pending resume write that the public facade
must prevent. No test helper providing this access may be imported by
production. Synchronous `get_tuple()`, every other saver API, `get_state()`,
`update_state()`, and every other new LangGraph API remain forbidden. This does
not impose the inspection contract on a persistent saver, does not make
`InMemorySaver` a JSON-only validator, and cannot become part of a production
recovery algorithm.

The exact execution sequence is:

1. outline writer atomically commits the outline and six-event pause state;
2. graph enters `outline_approval`;
3. its first execution calls `interrupt(safe_payload)` before any other effect;
4. the public start facade calls `aget_state()` and restores the checkpoint;
5. the dedicated facade calls `aget_state()` and validates shape A or B;
6. it constructs a new `Command(resume=validated_payload)`;
7. after invocation it calls `aget_state()` again and restores the terminal
   state.

Exactly `start_academic_workflow()`, `resume_academic_workflow()`, and
`submit_academic_outline_decision()` may call `ainvoke()` or `aget_state()` and
construct or use the exact `RunnableConfig`:

```python
{"configurable": {"thread_id": validated_identity.thread_id}}
```

Only `submit_academic_outline_decision()` constructs
`Command(resume=...)`; `resume_academic_workflow()` never constructs a
`Command`. The raw compiled graph remains private, is returned only internally
to one of these three facades, and is not exported by a package initializer.
The existing one-way module import direction and import-side-effect contract
remain unchanged.

The approval node performs no adapter, provider, LLM, network, file, clock,
logging, billing, or other state-external action before or after interrupt.

## 6. Strict DTO contract

All new models inherit the existing frozen, strict, extra-forbid model base.
All are frozen. JSON and Python validation are both strict.

```python
OutlineDecision: TypeAlias = Literal["approve", "reject"]

class AcademicOutlineDecisionCommand(_StrictWorkflowModel):
    schema_version: Literal["1"]
    workflow_id: str
    thread_id: str
    run_id: str
    outline_id: Literal["outline:000001"]
    outline_digest: str
    decision: OutlineDecision
    actor_assertion: str

class WorkflowOutlineDecisionRecord(_StrictWorkflowModel):
    decision_id: Literal["outline-decision:000001"]
    schema_version: Literal["1"]
    workflow_id: str
    thread_id: str
    run_id: str
    outline_id: Literal["outline:000001"]
    outline_digest: str
    decision: OutlineDecision
    actor_assertion: str
    attempt: FixedOne
```

Limits are:

```text
workflow_id/thread_id/run_id  1..256 Python code points after existing strip
outline_digest                exactly 64 lowercase ASCII hexadecimal chars
actor_assertion               1..256 Python code points after strip
canonical command JSON        at most 2048 Python code points
resume mapping                exactly the seven command fields
```

The same 256-code-point identity cap is added to the existing `workflow_id`,
`thread_id`, and `run_id` fields of `AcademicWorkflowRequest`,
`AcademicWorkflowIdentity`, and `AcademicWorkflowState`, and to the existing
`workflow_id` and `run_id` fields of `WorkflowTopicPlan`. TopicPlan has no
`thread_id`, and 3.4 does not add one. The approval command is a different DTO:
its own `thread_id` binds the command to the existing request, config, state,
and facade identity protocol and does not add a field to TopicPlan. These caps
are checked at initial request validation, before any graph invocation. They
are the sole identity-length supersessions listed in Section 2 and prevent a
paused checkpoint for which no bounded approval command can be constructed.

`actor_assertion` rejects CR, LF, NUL, and all Unicode category `Cc`
characters. It permits no separate reason, comment, display name, edit,
metadata, or opaque object. It is only a bounded caller assertion and is not an
authentication or authorization proof. Product authentication and
authorization are deferred.

Bool is rejected anywhere a fixed integer is expected. Tuples are required by
Python DTO fields that declare tuples; JSON arrays are accepted only through
model JSON validation. String subclasses, dict subclasses, extra keys, wrong
containers, coercions, NaN, Infinity, bytes, and arbitrary objects reject.

The command canonical form uses:

```python
json.dumps(
    command.model_dump(mode="json"),
    ensure_ascii=False,
    allow_nan=False,
    sort_keys=True,
    separators=(",", ":"),
)
```

No public command builder is added. Callers construct the strict DTO.

## 7. Application state contract

The following state-level changes are frozen:

```python
NodeId = Literal[
    "topic_planner",
    "research_evidence",
    "outline_writer",
    "outline_approval",
]

AcademicWorkflowState.phase = Literal[
    "initialized",
    "topic_planned",
    "evidence_collected",
    "outline_ready",
    "outline_approved",
    "outline_rejected",
]

AcademicWorkflowState.outline_decision = (
    WorkflowOutlineDecisionRecord | None
)
```

The exact reachable combinations are:

| Phase/status | Plan | Evidence | Outline | Decision | Errors | Events |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| initialized/running | no | no | no | no | 0 | 0 |
| topic_planned/running | yes | no | no | no | 0 | 2 |
| evidence_collected/running | yes | yes | no | no | 0 | 4 |
| outline_ready/running | yes | yes | yes | no | 0 | 6 |
| outline_approved/completed | yes | yes | yes | approve | 0 | 9 |
| outline_rejected/completed | yes | yes | yes | reject | 0 | 9 |
| initialized/failed | no | no | no | no | 1 topic error | 2 |
| topic_planned/failed | yes | no | no | no | 1 evidence error | 4 |
| evidence_collected/failed | yes | yes | no | no | 1 outline error | 6 |

`outline_ready/completed` is invalid after 3.4. A rejected state is not failed,
has no `WorkflowError`, and cannot be technically resumed. Decision record
identity, outline ID, and digest must match state and recomputed outline digest.
No failure code is added for reject or approval technical exceptions.

## 8. Canonical outline revision digest

The only algorithm is:

```python
def _canonical_outline_bytes(outline: WorkflowOutline) -> bytes:
    # exact WorkflowOutline required
    payload = outline.model_dump(mode="json")
    validate_json_value(payload)
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    restored = WorkflowOutline.model_validate_json(encoded)
    # exact model/value/type and canonical-byte round-trip equality required
    return encoded

def _outline_digest(outline: WorkflowOutline) -> str:
    return hashlib.sha256(_canonical_outline_bytes(outline)).hexdigest()
```

Input must be exact `WorkflowOutline`. The helper performs the same isolated
canonical JSON round trip used by the workflow boundary. The digest is exactly
64 lowercase hexadecimal characters. The facade recomputes it from the outline
stored in checkpoint state and compares command digest with
`hmac.compare_digest()`. The client does not define the algorithm. No second
outline body is stored.

Tests freeze a complete Unicode golden, Python/JSON round trips, every field,
section order, and single-code-point changes. Any outline mutation changes the
digest.

The fixed Unicode golden outline has title `研究 Ω` and three sections
`引言/范围 α`, `分析/证据 β`, and `结论/综合 γ`, with the deterministic IDs,
orders, evidence ID, and attempt defined by 3.3. Its canonical encoding is 313
code points and 345 UTF-8 bytes, and its SHA-256 digest is exactly:

```text
6ec33d8656eb25d09990657737af1438d23cb5cc8749f2679e3f1ccc1dabff3a
```

## 9. Interrupt and resume payloads

The exact interrupt value is:

```json
{
  "allowed_decisions": ["approve", "reject"],
  "outline_digest": "<64 lowercase hex>",
  "outline_id": "outline:000001",
  "run_id": "<run>",
  "schema_version": "1",
  "thread_id": "<thread>",
  "workflow_id": "<workflow>"
}
```

The mapping has exactly these seven keys, uses exact built-in JSON types, and
its canonical JSON is at most 2048 code points. The resume mapping is the exact
`AcademicOutlineDecisionCommand.model_dump(mode="json")` mapping and is also at
most 2048 code points. Both are revalidated inside the approval helper.

Neither payload contains the outline, evidence, prompt, raw response, Config,
client, provider, exception, credentials, edit text, reason, comment, callback,
websocket, graph, saver, `RunnableConfig`, or opaque object.

## 10. Graph and node semantics

The only graph is:

```text
START
→ topic_planner
→ research_evidence
→ outline_writer
→ outline_approval
→ approved END
  or rejected END
```

Successful outline writing commits the outline, `outline_ready/running`, no
decision record, and its node-started/node-completed events. It does not emit
`workflow_completed`. The resulting checkpoint has
`next == ("outline_approval",)`.

Approval-node first entry performs strict predecessor restoration and then
immediately calls `interrupt()`. It does not pre-commit a node-started event.
Pause commits no approval event or failure event.

After resume, a private isolation helper validates the complete resume DTO,
schema version, workflow/thread/run identities, outline ID, outline digest,
decision, actor assertion, exact mapping shape, and predecessor. Only after all
of those checks succeed may execution enter the decision-specific commit stage
that can produce an approve or reject serialized task-error marker. It builds
the complete terminal DTO and graph-state update in isolation. Only a
successful update atomically appends approval-started, approval-completed, and
terminal workflow event.

Invalid, empty, or overlong actors; invalid decisions; identity, outline-ID, or
digest mismatches; wrong schema; missing, extra, or wrongly typed payload
fields; validation-input failures; and nonapproval internal failures never
produce an eligible approve or reject marker. They fail before the
decision-specific commit stage and cannot become shape B.

An ordinary exception before the helper has a validated decision becomes a
fixed `InvariantError` shape that is not eligible for decision retry. An
ordinary exception after validation becomes the decision-specific private
fixed commit error. The sensitive helper frame exits before the fixed exception
is raised. Cause and context are `None`; no original exception or validation
object is retained. True external `CancelledError` uses bare `raise`.

## 11. Event goldens

Pause has exactly:

```text
1  topic_planner      node_started
2  topic_planner      node_completed
3  research_evidence  node_started
4  research_evidence  node_completed
5  outline_writer     node_started
6  outline_writer     node_completed
```

Approve appends exactly:

```text
7  outline_approval   node_started
8  outline_approval   node_completed
9  -                  workflow_completed
```

Reject appends exactly:

```text
7  outline_approval   node_started
8  outline_approval   node_completed
9  -                  workflow_rejected
```

`WorkflowEvent.event_type` adds `workflow_rejected`. Both terminal workflow
event types require `node_id=None`; node events require their exact node. Raw
exceptions, invalid commands, and the normal interrupt commit no pending
approval event. Before a terminal update becomes visible, cancellation commits
no approval-started, approval-completed, or terminal event. After a terminal
update is visible, its approval and terminal events are committed and are not
removed or reinterpreted because the caller observes cancellation. Replays
never duplicate the first six events.

## 12. Public facades

The exact public signatures are:

```python
async def start_academic_workflow(
    request: AcademicWorkflowRequest,
    adapter: AcademicWritingAdapter,
    *,
    checkpointer: BaseCheckpointSaver,
) -> AcademicWorkflowState: ...

async def resume_academic_workflow(
    identity: AcademicWorkflowIdentity,
    adapter: AcademicWritingAdapter,
    *,
    checkpointer: BaseCheckpointSaver,
) -> AcademicWorkflowState: ...

async def submit_academic_outline_decision(
    command: AcademicOutlineDecisionCommand,
    adapter: AcademicWritingAdapter,
    *,
    checkpointer: BaseCheckpointSaver,
) -> AcademicWorkflowState: ...
```

These are the only three graph-operating public facades. No fourth entry point
exists. No facade returns the graph, config, interrupt, task, snapshot, or
checkpoint tuple. The raw graph builder remains private, and callers import the
facades explicitly from `graph.py`; package initializers do not re-export them.

### 12.1 Start

Start preserves the 3.0 request, existence, and identity priority. After its
single `ainvoke`, it ignores the raw result, calls `aget_state`, strictly
restores `snapshot.values`, and returns either a business failure or the normal
approval pause. It verifies the pause's exact shape A before returning it.

### 12.2 Ordinary technical resume

Ordinary resume preserves 3.0 validation, missing-checkpoint, identity, and
terminal priority. Before `ainvoke(None)`, it recognizes both exact approval
shapes A and B and raises:

```text
academic outline approval decision is required
```

It never constructs `Command`. An `outline_ready/running` checkpoint that is
neither A nor B raises fixed `InvariantError`. Existing upstream technical
failure checkpoints continue to use `ainvoke(None)`.

### 12.3 Dedicated decision facade priority

The only guard order is:

1. strictly validate the command; invalid input raises
   `academic outline decision command is invalid`;
2. build graph/config and call `aget_state`;
3. missing checkpoint raises `academic workflow checkpoint does not exist`;
4. strictly restore application state; restoration failure is `InvariantError`;
5. workflow/thread/run mismatch raises
   `academic workflow identity does not match checkpoint`;
6. an existing decision, approved/rejected phase, completed status, or failed
   status raises `academic outline decision has already been committed`;
7. missing outline or non-ready/running state raises
   `academic outline decision is not available`;
8. outline ID mismatch raises
   `academic outline identity does not match checkpoint`;
9. recomputed/checkpoint/command digest mismatch raises
   `academic outline revision does not match checkpoint`;
10. classify the snapshot as exact shape A or B; any other shape raises fixed
    `InvariantError`;
11. for A, validate the sole interrupt payload byte-for-byte by canonical JSON;
12. for B, map the exact serialized task-error string marker to the retained
    decision without reading or comparing the first actor; a different command
    decision raises
    `academic outline retry decision does not match failed attempt`;
13. construct a new `Command(resume=validated_mapping)` and call `ainvoke` once;
14. call `aget_state` once more, restore and validate the exact terminal state.

All errors above are fixed private-table selections in
`OutlineDecisionProtocolError`; they contain no dynamic value. Every rejection
before step 13 has zero `ainvoke`, zero events, and zero checkpoint mutation.
Factory/graph construction itself must not execute a node.

## 13. Approval availability predicates

Exactly two shapes accept a dedicated decision.

### 13.1 Shape A

All of these are required:

- exact `outline_ready/running`, outline present, decision absent, no errors,
  exact six events;
- `next == ("outline_approval",)`;
- exactly one task with a nonblank exact-string ID;
- exact name/path, `error is None`, `result is None`, `state is None`;
- exactly one interrupt;
- interrupt value exactly matches schema, state identities, outline ID, and
  recomputed digest.

### 13.2 Shape B

All of these are required:

- the same exact application state as A;
- `next == ()`;
- exactly one task with the same exact name/path;
- `state is None`, `result == {}` by exact built-in type and value;
- exactly one matching interrupt;
- `type(task.error) is str` and its complete value equals exactly one of the two
  safe serialized markers;
- the marker's retained decision equals the command decision;
- there is no terminal event or decision record.

`next == ()` alone never means terminal or retriable. Completed, business
failed, approved, rejected, decided, missing-outline, wrong-task, wrong-path,
wrong-count, empty-error, nonapproval-error, wrong-result, wrong-interrupt, or
unexplained empty-next states are never approval availability shapes.

## 14. Error and sensitive-data boundary

`task.error` is LangGraph checkpoint metadata, not application state. It is not
assumed JSON-safe or automatically redacted. Shape B requires
`type(task.error) is str` and complete value equality with exactly one Section
4.2 marker:

```text
_OutlineApproveCommitError('academic outline decision commit failed')
_OutlineRejectCommitError('academic outline decision commit failed')
```

This is the **exact serialized task-error string marker** produced by local
LangGraph 1.2.11 with JsonPlus. Classification does not use `isinstance`,
contains, prefix, suffix, regular expression, exception-type matching, `repr`,
or dynamic attribute access; a `str` subclass rejects. Version or
serializer drift that changes the complete string fails closed. Malformed,
unknown, or other-node task errors reject.

The decision-specific marker can be reached only after every field of the first
resume payload has passed the complete validation in Section 10. It therefore
identifies a retained validated decision, but exposes neither the retained
actor nor the payload to production facade code.

The approval helper never includes actor text, outline content, the resume
mapping, validation input, or original exception text in an exception. The two
safe serialized markers and common fixed message are the only stored error
data.
Cause and context of fixed outward exceptions are `None`.

Checkpoint application state stores only the existing request, plan, evidence,
outline, events, errors, and final minimal decision record. It never stores a
prompt, raw model/provider response, Config, client, callback, websocket,
credentials, exception object/text, edit/comment/reason, graph, or opaque blob.
LangGraph may retain the bounded validated resume mapping in pending writes
after a technical failure; this is explicitly inspected and is not described
as application state.

Tests inspect `StateSnapshot.values/tasks/metadata`, interrupts, task results,
`CheckpointTuple`, checkpoint metadata, and pending writes. An AST-equivalent
safe walker verifies forbidden sentinels are unreachable from application
state, fixed exceptions, traceback locals/closures, and all checkpoint
surfaces. Hostile `repr`, `str`, property, descriptor, iterator, and attribute
objects must not execute during error classification.

## 15. Cancellation semantics

Client, node, and facade code use bare `raise` for true outer-task
`CancelledError`. They do not construct, wrap, or convert cancellation.
LangGraph 1.2.11 may append an internal task object to cancellation args during
a blocked checkpoint commit; 3.4 guarantees that the caller's original message
remains the first arg, not that the framework preserves an identical args tuple
or exception identity.

The four probed cases are frozen separately:

1. **Before interrupt.** The last checkpoint is outline-ready/running with
   `next=("outline_approval",)`, one approval task, zero interrupts, no error,
   no result, and no pending writes. It is neither A nor B and the dedicated
   facade safely rejects it.
2. **After interrupt commit and facade return.** Cancelling an already finished
   caller task returns `False` and leaves exact shape A unchanged.
3. **During Command execution before the terminal update becomes visible.**
   Cancellation propagates; the checkpoint returns to shape A. Pending writes
   may include the bounded global resume write. No approval-started,
   approval-completed, or terminal event is visible, and no business failed or
   rejected state is created. A newly validated approve or reject may be
   submitted; the new decision is used because no technical-error replay was
   retained.
4. **After the terminal update is visible while checkpoint put or caller return
   is interrupted.** Snapshot values expose the terminal phase, completed
   status, decision record, corresponding approval events, and terminal event.
   During a blocked put they also expose `next==()` and one task with the
   terminal result plus pending channel writes; after completion tasks are
   empty. In both cases the decision is committed even if the caller observes
   `CancelledError`. The facade rejects a second decision before graph
   execution. It never removes or rolls back the record or events, reclassifies
   the checkpoint as A or B, or permits a second approval attempt.

Before a terminal update becomes visible, cancellation is handled only by the
frozen pause/retry or separately frozen cancellation shape and does not create
a business failed/rejected state. After a terminal update is visible, the
committed record and events control. Any unrecognized cancellation shape is
safely rejected rather than guessed.

## 16. Checkpoint, replay, and at-least-once semantics

Checkpoint existence remains `snapshot.created_at is not None`. Approval is in
the same workflow, thread, and run. `snapshot.next` is necessary but not
sufficient for normal pause and is not a terminal predicate.

Shape B is at-least-once for the uncommitted approval-node attempt. The facade
revalidates state, digest, identity, schema, actor assertion, and decision on
every call and always constructs a new Command. It never compares or recovers a
Python Command object. It also never recovers or compares the first actor: the
new actor is independently validated for the facade call, while LangGraph
replays the retained first actor into the record. Only the retained decision
may be retried. Upstream plan, evidence, outline, and their six events are
exactly once with respect to approval replay.

No exactly-once claim is made for an uncommitted node attempt, transport,
in-memory invocation, or framework bookkeeping. The approval node has no
external side effect, so replay is safe. Once the terminal update is visible,
every further decision rejects before graph execution, including when the
caller observed cancellation after that visibility point.

## 17. Complete local probe matrix

The implementation tests must reproduce, without production external calls:

- full shape A snapshot and tuple golden;
- full shape B snapshot and tuple golden;
- successful retry terminal snapshot and tuple golden;
- Command-resume cancellation snapshot and tuple golden;
- same Command instance retry succeeds;
- new value-equal Command retry succeeds;
- approve-crash then raw reject replays approve;
- reject-crash then raw approve replays reject;
- facade rejects both changed-decision attempts before raw invocation;
- invalid resume at normal pause becomes a noneligible invariant task error in
  the private raw-graph probe;
- invalid facade command at A or B makes zero graph call;
- raw `ainvoke(None)` behavior at A and B is documented and facade-blocked;
- raw terminal Command behavior is documented and facade-blocked;
- cancellation before interrupt, after pause, before terminal-update
  visibility, and after terminal-update visibility;
- each exact serialized task-error string marker excludes actor, outline, raw
  payload text, raw exception, and validation input.

Generated IDs/timestamps/version suffixes use structural assertions; all
application values, names, paths, errors, interrupts, results, metadata source
and step, config relationships, pending-write channels/order, and event content
use exact assertions.

## 18. Test-first and offline matrix

Implementation begins with failing tests and no production edit. Tests use
fakes, `InMemorySaver`, static AST, and fail-fast guards only. They never call a
real adapter dependency, LLM, Config, Provider, Retriever, MCP, network,
subprocess, or file write.

Direct tests cover:

1. all DTO Python and JSON strict cases and every bound;
2. digest byte golden, round trip, Unicode, order, and mutations;
3. interrupt and resume payload complete goldens;
4. start returning strict pause state rather than raw interrupt mapping;
5. six-event pause, nine-event approve, and nine-event reject;
6. all state legal/illegal combinations including removal of
   outline-ready/completed;
7. exact graph node/edge production API allowlist and the sole test-only
   `InMemorySaver.aget_tuple()` supersession;
8. exact A and B predicates plus every near miss;
9. ordinary resume rejection of A and B;
10. dedicated facade error priority and fixed texts;
11. zero `ainvoke`, events, and state mutation on every rejection;
12. same-decision retry, actor A-to-B replay, and changed-decision rejection;
13. terminal repeated approve/reject combinations;
14. all four cancellation cases and first-arg retention;
15. ordinary/raw crash before commit and at-least-once replay;
16. no repeated upstream calls or events;
17. snapshot, task, interrupt, tuple, metadata, and pending-write safety using
    only the test-only `InMemorySaver.aget_tuple()` inspection API;
18. safe traceback and hostile-object checks;
19. canonical import success/failure and complete state restoration;
20. external file/network/HTTP/subprocess fail-fast before target resolution;
21. 3.0–3.3 updated full-graph goldens;
22. at least three execution orders spanning 3.0–3.4;
23. frozen specs, package initializers, dependency files, and nine-file scope;
24. forbidden production and test LangGraph API and product-mount static
    checks;
25. cancellation before and after terminal-update visibility, including exact
    record/event persistence and zero second-decision execution.

The actor-replay matrix is not collapsed into a generic retry assertion. It
contains these direct, independently asserted cases:

1. approve by actor A reaches an approve pre-commit crash;
2. approve by actor B submits a same-decision retry;
3. the approve terminal record actor is exactly A;
4. actor B is unreachable from that record;
5. reject by actor A reaches a reject pre-commit crash;
6. reject by actor B submits a same-decision retry;
7. the reject terminal record actor is exactly A;
8. actor B is unreachable from that record;
9. shape-B eligibility performs no retry-actor comparison;
10. the retry actor still passes the complete strict DTO and length matrix;
11. an invalid first actor cannot create an approve marker;
12. an invalid first actor cannot create a reject marker;
13. neither marker is reachable before the entire first payload validates;
14. a raw changed decision would replay the first decision, while the public
    facade rejects it before `ainvoke` from the exact marker;
15. the final record is constructed only from the retained first payload.

The Section 5 facade boundary is also tested by eight independent mechanical
assertions rather than one broad surface check:

1. only `start_academic_workflow()`, `resume_academic_workflow()`, and
   `submit_academic_outline_decision()` call or inspect the private compiled
   graph;
2. only those same three named facades construct or use `RunnableConfig`;
3. only `submit_academic_outline_decision()` constructs
   `Command(resume=...)`;
4. `resume_academic_workflow()` constructs no `Command`;
5. no fourth graph-calling entry point exists;
6. the raw compiled graph is not public or returned to a caller;
7. both package `__init__.py` files remain unchanged and add no export;
8. `WorkflowTopicPlan` fields remain exactly the frozen 3.0 fields, including
   `workflow_id` and `run_id` and excluding `thread_id`.

Existing 3.1–3.3 safe construction, qualified registry, ordered file allowlist,
canonical import difference, safe walker, and cleanup contracts are not
weakened. Test helpers are not imported from old test modules unless an existing
production helper is the subject under test.

## 19. Compatibility and dependencies

Python remains 3.11. Existing LangGraph ranges remain unchanged. Local 1.2.11
is the mechanical baseline. No dependency, lock file, persistent saver, or
serializer is added. `InMemorySaver` remains a test saver and is not treated as
a JSON validator or durable store.

## 20. Exact implementation boundary

Future implementation may modify exactly these eight existing files:

```text
gpt_researcher/workflows/academic_writing/state.py
gpt_researcher/workflows/academic_writing/nodes.py
gpt_researcher/workflows/academic_writing/graph.py
tests/test_academic_writing_workflow_state.py
tests/test_academic_writing_workflow_graph.py
tests/test_academic_writing_topic_planner.py
tests/test_academic_writing_research_evidence.py
tests/test_academic_writing_outline_writer.py
```

It may add exactly:

```text
tests/test_academic_writing_outline_approval.py
```

The implementation boundary is eight modified plus one added file, nine total.
It explicitly excludes:

```text
gpt_researcher/workflows/academic_writing/adapters.py
gpt_researcher/workflows/academic_writing/topic_planner.py
gpt_researcher/workflows/academic_writing/research_evidence.py
gpt_researcher/workflows/academic_writing/outline_writer.py
gpt_researcher/workflows/__init__.py
gpt_researcher/workflows/academic_writing/__init__.py
gpt_researcher/agent.py
backend/**
frontend/**
requirements.txt
pyproject.toml
multi_agents/**
```

No package initializer export is required. If these nine files cannot implement
the contract, work stops and the specification returns to review.

## 21. Stop conditions

Draft or implementation must stop if it discovers that:

- the nine-file boundary is insufficient;
- an adapter production file, backend/frontend, package initializer, or
  dependency must change;
- local interrupt/Command behavior no longer matches Sections 4 and 15;
- A, B, terminal, and nonapproval damage cannot be mechanically distinguished;
- same-decision retry cannot be enforced before graph invocation;
- interrupt payload cannot remain bounded and safe;
- event updates cannot be atomic;
- the protocol would have to treat the actor assertion itself as product
  authentication or authorization rather than requiring the future product
  mount to authenticate and authorize before facade entry;
- a required offline test is unreachable.

## 22. Draft approval checklist

- [ ] Status is Draft and the document does not authorize implementation.
- [ ] The supersession table is exhaustive and 3.0 historical text remains unchanged.
- [ ] The 3.0 Section 5 two-facade limit is narrowly superseded by exactly the three named facades.
- [ ] Every other Section 5 module, private-graph, RunnableConfig, import, and side-effect rule remains effective.
- [ ] The goal is limited to checkpoint-native approve/reject.
- [ ] SectionWriter, edit, regeneration, UI, transport, and durable storage remain excluded.
- [ ] LangGraph 1.2.11 signatures and the two production-only API additions are approved.
- [ ] The sole test-only InMemorySaver.aget_tuple supersession and its inspection boundary are approved.
- [ ] Normal pause snapshot shape A is approved.
- [ ] Pre-commit technical-failure snapshot shape B is approved.
- [ ] Terminal snapshot shape is approved.
- [ ] CheckpointTuple and pending-write observations are approved.
- [ ] Same-decision retry semantic B is approved.
- [ ] Approve actor-A to actor-B replay preserves actor A and is approved.
- [ ] Reject actor-A to actor-B replay preserves actor A and is approved.
- [ ] Shape-B eligibility ignores the retry actor while each retry actor is strictly validated.
- [ ] Decision-specific markers are reachable only after complete first-payload validation.
- [ ] Command object identity is explicitly non-normative.
- [ ] Raw None, invalid, and terminal invocation observations are approved.
- [ ] Decision and actor assertion semantics are approved.
- [ ] Command DTO fields, strictness, and limits are approved.
- [ ] Decision record fields, strictness, and limits are approved.
- [ ] Actor assertion is explicitly not authentication or authorization.
- [ ] State phases, status, artifacts, errors, and decision combinations are approved.
- [ ] Canonical outline digest bytes and comparison are approved.
- [ ] Interrupt and resume payloads and limits are approved.
- [ ] Approval node atomicity and no-side-effect rule are approved.
- [ ] Pause event golden is approved.
- [ ] Approve event golden is approved.
- [ ] Reject event golden and workflow_rejected are approved.
- [ ] Public facade signatures are approved.
- [ ] Start post-invoke aget_state behavior is approved.
- [ ] Ordinary resume rejection of shapes A and B is approved.
- [ ] Dedicated facade guard order and fixed texts are approved.
- [ ] Shape A predicate is approved.
- [ ] Shape B predicate and exact serialized task-error string markers are approved.
- [ ] Unknown empty-next and task-error shapes fail safely.
- [ ] Task-error and pending-write safety boundaries are approved.
- [ ] Fixed exception cause, context, traceback, and hostile-object rules are approved.
- [ ] Four distinct cancellation shapes are approved.
- [ ] Cancellation semantics are split before and after terminal-update visibility.
- [ ] Cancellation first-arg rather than identity guarantee is approved.
- [ ] At-least-once approval and upstream nonrepetition are approved.
- [ ] Complete local probe matrix is approved.
- [ ] Offline test matrix and old-golden updates are approved.
- [ ] Python, dependency, and InMemorySaver constraints are approved.
- [ ] Exact eight-modified/one-added file boundary is approved.
- [ ] All explicit exclusions are approved.
- [ ] Stop conditions are approved.
- [x] This specification received explicit approval before implementation began.

## 23. Implementation acceptance checklist

- [ ] Only the exact nine implementation files changed.
- [ ] No frozen specification or excluded file changed.
- [ ] Initial red was recorded before production edits.
- [ ] New DTO signatures, fields, strictness, and frozen models match exactly.
- [ ] All DTO Python strict cases pass.
- [ ] All DTO JSON strict cases pass.
- [ ] Identity, actor, digest, and payload bounds pass at both edges.
- [ ] Canonical outline byte golden matches exactly.
- [ ] Digest Unicode, round-trip, order, field, and one-character matrices pass.
- [ ] Digest comparison uses hmac.compare_digest.
- [ ] Interrupt payload complete golden matches exactly.
- [ ] Resume payload complete golden matches exactly.
- [ ] Forbidden data is absent from both payloads.
- [ ] State legal-shape matrix passes.
- [ ] State illegal-shape matrix rejects all old and mixed combinations.
- [ ] outline_ready/completed is rejected.
- [ ] Pause has exactly six events.
- [ ] Approve has exactly nine events.
- [ ] Reject has exactly nine events and no WorkflowError.
- [ ] Outline writer no longer commits workflow_completed.
- [ ] Graph has exactly the four frozen nodes and edges.
- [ ] Only start_academic_workflow, resume_academic_workflow, and submit_academic_outline_decision call or inspect the private compiled graph.
- [ ] Only those three named facades construct or use RunnableConfig.
- [ ] Only submit_academic_outline_decision constructs Command(resume=...).
- [ ] resume_academic_workflow constructs no Command.
- [ ] No fourth graph-calling entry point exists.
- [ ] The raw compiled graph is private and is never returned or exported.
- [ ] Both package initializers remain unchanged and add no facade export.
- [ ] WorkflowTopicPlan retains workflow_id and run_id and has no thread_id.
- [ ] Approval first entry performs interrupt before any other effect.
- [ ] Approval node makes zero adapter, I/O, clock, log, or billing calls.
- [ ] Start never restores or returns raw __interrupt__ data.
- [ ] Start returns a strict shape-A application state.
- [ ] Ordinary resume rejects exact shape A before ainvoke.
- [ ] Ordinary resume rejects exact shape B before ainvoke.
- [ ] Existing upstream technical resume still works.
- [ ] Dedicated facade public signature matches exactly.
- [ ] Dedicated facade guard priority and every fixed text match exactly.
- [ ] Every pre-invoke rejection has zero graph execution and mutation.
- [ ] Exact shape A is accepted.
- [ ] Every shape-A near miss rejects.
- [ ] Exact shape B is accepted for the retained decision.
- [ ] Every shape-B near miss rejects.
- [ ] Changed decision at B rejects before ainvoke.
- [ ] Approve actor-A crash followed by actor-B retry commits approve with actor A only.
- [ ] Reject actor-A crash followed by actor-B retry commits reject with actor A only.
- [ ] Shape-B eligibility performs no old/new actor comparison and the retry actor still validates strictly.
- [ ] Invalid first actors and every other prevalidation failure create no eligible decision marker.
- [ ] Both decision markers become reachable only after the complete first payload validates.
- [ ] Same Command instance replay probe passes.
- [ ] New value-equal Command replay probe passes.
- [ ] Raw changed-decision replay observation matches LangGraph 1.2.11.
- [ ] Terminal repeated decisions reject before ainvoke.
- [ ] Approval task errors require exact built-in str type and complete serialized-marker equality.
- [ ] Unknown task errors never confer retry eligibility.
- [ ] Fixed outward errors have None cause and context.
- [ ] Traceback and closure walker finds no forbidden sentinel.
- [ ] StateSnapshot full shape-A golden passes.
- [ ] StateSnapshot full shape-B golden passes.
- [ ] StateSnapshot terminal golden passes.
- [ ] Test-only InMemorySaver.aget_tuple is the sole added saver inspection API and its tuple/pending-write goldens pass.
- [ ] Cancellation before interrupt matches the frozen noneligible shape.
- [ ] Cancellation after pause leaves shape A unchanged.
- [ ] Cancellation during resume propagates and leaves shape A.
- [ ] Cancellation before terminal-update visibility commits no approval or terminal event.
- [ ] Cancellation after terminal-update visibility preserves the record and events and never permits a second decision.
- [ ] Cancellation preserves the caller message as first arg.
- [ ] Cancellation alone creates no business failure, and replay creates no duplicate events.
- [ ] At-least-once approval replay passes.
- [ ] Topic, evidence, outline calls and six upstream events do not repeat.
- [ ] Sensitive-data walker covers values, tasks, metadata, tuples, and pending writes.
- [ ] Hostile repr/str/property/descriptor/iterator cases remain fail-fast.
- [ ] Canonical import success and loader-after-failure restore complete state.
- [ ] File, HTTP, socket, and subprocess targets fail before external execution.
- [ ] Production and test-only LangGraph API static allowlist checks both pass.
- [ ] No real Config, LLM, Provider, Retriever, MCP, network, or subprocess ran.
- [ ] 3.0–3.4 tests pass alone and in every frozen order.
- [ ] Frozen specs, package initializers, and dependency files have zero diff.
- [ ] AST and static checks pass for every changed production file.
- [ ] Trailing whitespace and git diff --check pass.
- [ ] Staging remains empty and no commit was created.

Implementation acceptance remains entirely unchecked until implementation and
all mechanical verification complete.
