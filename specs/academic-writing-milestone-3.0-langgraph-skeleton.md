# Academic Writing Milestone 3.0 — LangGraph Skeleton

Status: **Approved and frozen**

This specification is approved and frozen and authorizes implementation only
within the boundaries defined below. The explicit approval record in Section 16
is checked; implementation acceptance items remain unchecked until the
implementation is completed and verified.

## 1. Goal and executable boundary

Milestone 3.0 aligns the core Python LangGraph dependencies to one verified 1.x
baseline and adds a default-disabled, isolated, offline-testable academic
writing workflow skeleton. It does not replace, intercept, or mount into the
current GPT Researcher request path.

The eventual workflow remains:

```text
TopicPlanner
→ ResearchEvidence
→ OutlineWriter
→ OutlineApproval
→ SectionWriters
→ CitationReviewer
→ FinalEditor
```

Milestone 3.0 implements only:

```text
START
→ topic_planner
→ research_evidence
→ outline_writer
→ END
```

The only successful terminal phase/status pair is
`outline_ready/completed`. Reaching LangGraph `END` means that a validated
outline exists; it does not mean that an outline was approved or a report was
written.

## 2. Frozen scope and legacy isolation

Milestone 3.0 implements only:

1. the dependency baseline in Section 3;
2. strict immutable workflow DTOs and one JSON-compatible graph-state envelope;
3. one aggregate asynchronous fake-injectable adapter protocol;
4. three thin graph nodes;
5. a private graph builder with an injected checkpointer;
6. public `start_academic_workflow()` and `resume_academic_workflow()` facades;
7. deterministic events, safe failure types, and thread identity guards;
8. application JSON round-trip tests separate from saver tests;
9. native in-memory checkpoint, crash, cancellation, and recovery tests; and
10. mechanical dependency-baseline and external-I/O fail-fast tests.

Milestone 3.0 does not implement:

- a real TopicPlanner, GPTResearcher adapter, EvidenceExtractor, or outline
  writer;
- `GPTResearcher`, `ResearchConductor`, Retriever, PaperScreener, scraper,
  compressor, MCP, LLM, Provider, or network calls;
- OutlineApproval, `interrupt()`, approval commands, or approval UI;
- SectionWriters, parallel fan-out/fan-in, or targeted rewrite;
- CitationReviewer, FinalEditor, final report, or citation membership;
- REST, WebSocket, `run_agent()`, request-schema, backend, frontend, or routing
  changes;
- `ReportStore`, artifact storage, report history, or file export;
- SQLite, Postgres, Redis, or another durable saver; or
- repair, refactor, import, compilation, or execution of the existing
  `multi_agents` graph.

The future request mode vocabulary remains:

```text
legacy
academic_langgraph
```

The future external default is exactly `legacy`. Milestone 3.0 does not add
that field to an existing request schema and does not mount either facade. The
new package can be reached only by an explicit internal import or test. When it
is not imported, all existing Basic Report, GPTResearcher, academic search,
screening, audit, report, transport, frontend, and export business code remains
value-for-value unchanged. Existing-file changes are limited to the four
dependency files in Section 4.

## 3. Dependency and normative LangGraph API baseline

### 3.1 Frozen dependency ranges

The only normative Python dependency baseline is:

```text
langgraph>=1.2.11,<2
langgraph-checkpoint>=4.2.0,<5
```

`langgraph-checkpoint` is a direct dependency because production annotations
use `BaseCheckpointSaver` and tests instantiate `InMemorySaver`. No lock file
is created. Milestone 3.0 does not install, upgrade, or downgrade the local
environment and does not add a SQLite, Postgres, or other persistent saver
package.

### 3.2 Normative API allowlist

The complete normative LangGraph/LangChain runtime API allowlist is:

```text
StateGraph
START
END
BaseCheckpointSaver
InMemorySaver
RunnableConfig
StateGraph.compile(checkpointer=...)
ainvoke
aget_state
```

Production imports are exactly:

```python
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
```

The dependency and graph tests additionally import exactly:

```python
from langgraph.checkpoint.memory import InMemorySaver
```

No other LangGraph class, function, method, serializer, streaming API,
interrupt API, store, cache, durability mode, subgraph API, or synchronous
state-read API is normative or permitted in 3.0 production or tests.

### 3.3 Read-only local probe

The repository-local Python 3.11 environment was probed with `-B`, without
network, installation, test execution, or file writes. It contained LangGraph
1.2.11 and `langgraph-checkpoint` 4.2.0.

A private one-node graph compiled with `InMemorySaver` produced:

```text
never-used thread: snapshot.created_at is None
checkpointed thread: snapshot.created_at is not None
```

Therefore the sole checkpoint-existence predicate is frozen as:

```python
checkpoint_exists = snapshot.created_at is not None
```

No other snapshot field participates in existence detection. In particular,
`values`, `next`, `config`, and `metadata` are not existence predicates.

### 3.4 Explicitly non-normative investigation record

Repository investigation may have observed additional aliases, streaming
methods, or synchronous state-read methods. Those observations are historical
and non-normative. They must not be imported, called, typed against, or tested
by the 3.0 implementation. Section 3.2 is the complete allowlist.

## 4. Exact 13-file boundary

Milestone 3.0 may modify exactly these four existing dependency files:

```text
pyproject.toml
requirements.txt
multi_agents/requirements.txt
setup.py
```

Milestone 3.0 may add exactly these nine files:

```text
gpt_researcher/workflows/__init__.py
gpt_researcher/workflows/academic_writing/__init__.py
gpt_researcher/workflows/academic_writing/state.py
gpt_researcher/workflows/academic_writing/adapters.py
gpt_researcher/workflows/academic_writing/nodes.py
gpt_researcher/workflows/academic_writing/graph.py
tests/test_academic_writing_workflow_state.py
tests/test_academic_writing_workflow_graph.py
tests/test_langgraph_dependency_baseline.py
```

No other file may be added, deleted, renamed, or modified. If implementation
needs a fourteenth file, it stops for specification revision and approval.

Dependency-file changes are exact:

- both Poetry and PEP 621 declarations in `pyproject.toml` use both Section 3.1
  ranges;
- root `requirements.txt` uses both Section 3.1 ranges;
- `multi_agents/requirements.txt` constrains `langgraph` to the Section 3.1
  range; it does not directly add checkpoint because that example has no live
  saver import; and
- `setup.py` stops filtering requirement names `langgraph` and
  `langgraph-checkpoint`, so its root-requirements path does not omit them.

## 5. Module, import, and side-effect boundary

Both new `__init__.py` files contain only a module docstring and:

```python
__all__ = ()
```

They do not import or re-export a DTO, facade, graph, adapter, exception, or
LangGraph type. Callers import DTOs from `state.py`, the adapter protocol from
`adapters.py`, and the two facades from `graph.py` explicitly.

The one-way production dependency direction is:

```text
state
  ↑
adapters
  ↑
nodes
  ↑
graph
```

Reverse imports and cycles are forbidden. The new modules do not import
backend, frontend, existing research/provider modules, or `multi_agents`.

Importing either new package introduces no graph compilation, saver, thread,
task, lock, logger, handler, environment read, file operation, database,
socket, subprocess, or service call. The current root `gpt_researcher` import
has pre-existing logger behavior; 3.0 requires only a differential guarantee:
after recording the root-package baseline, importing the new modules adds no
new side effect. It does not repair or remove existing logger handlers.

The private graph builder is not re-exported and is not a public thread
protocol. Only the two facade functions in Section 12 may invoke or inspect a
compiled graph.

## 6. Strict DTO foundation

Every domain DTO derives from one private Pydantic 2 base with exactly:

```python
ConfigDict(frozen=True, extra="forbid", strict=True)
```

The following rules apply mechanically:

- identifiers and required strings are strict `str`, stripped, and nonblank;
- validators never stringify another scalar type;
- `order` and `attempt` are strict positive integers; bool is rejected;
- DTO collections are tuples and contain no mutable list or dict;
- direct Python list input for a tuple field is rejected;
- JSON arrays are accepted as tuples only through `model_validate_json()`;
- duplicate IDs, duplicate ordered content, and broken cross-references are
  rejected;
- optional values use `None`, never blank-string sentinels;
- order is validated and never silently sorted or repaired; and
- no DTO contains a float, mapping blob, Path, set, bytes, datetime, Enum
  object, exception, callback, task, lock, file, connection, or live runtime
  object.

`frozen=True` is shallow, so the absence of mutable nested containers is a
required invariant rather than an assumption.

## 7. Identity and request contracts

### 7.1 `AcademicWorkflowIdentity`

```text
workflow_id: str
thread_id: str
run_id: str
```

All three fields are stripped nonblank, opaque, and case-sensitive. They are
supplied by the caller and are never generated, rewritten, hashed, or inferred.

### 7.2 `AcademicWorkflowRequest`

```text
workflow_mode: Literal["academic_langgraph"]
workflow_id: str
thread_id: str
run_id: str
query: str
report_type: str
report_source: str
tone: str
language: str
source_urls: tuple[str, ...]
document_urls: tuple[str, ...]
query_domains: tuple[str, ...]
max_search_results: StrictInt | None
```

Request identity exactly equals an `AcademicWorkflowIdentity`. Required text
is stripped and nonblank. URL/domain tuples preserve caller order and reject
duplicates. `max_search_results`, when present, is strictly positive. Headers,
cookies, credentials, Provider/MCP configuration, callbacks, Config, and
arbitrary kwargs are forbidden because the request enters checkpoint state.

### 7.3 Deterministic derived identities

```text
topic_plan_id = "topic-plan:000001"
evidence_id   = "evidence:000001"
outline_id    = "outline:000001"
section_id    = f"section:{order:06d}"
source_id     = f"evidence-source:{order:06d}"
event_id      = f"event:{order:06d}"
error_id      = f"error:{order:06d}"
```

No identity or observable order uses a random UUID, timestamp, `hash()`, set
iteration, mapping accident, process ID, Provider timing, or task completion
order.

## 8. Artifact and Evidence contracts

### 8.1 `WorkflowTopicPlan`

```text
topic_plan_id: Literal["topic-plan:000001"]
workflow_id: str
run_id: str
attempt: Literal[1]
research_topic: str
research_questions: tuple[str, ...]
```

Questions are nonempty, stripped, unique, and remain in adapter order.

### 8.2 `WorkflowEvidenceSource`

```text
source_id: str
order: StrictInt
title: str
url: str
candidate_id: str | None
```

The exact Evidence limits use Python `len()` over Unicode code points:

- `1 <= len(title) <= 512`;
- `1 <= len(url) <= 4096`;
- when present, `1 <= len(candidate_id) <= 256`;
- source count is at most 200;
- source order is contiguous from one;
- `source_id` exactly matches its order; and
- URL and non-`None` candidate IDs are unique in source order.

### 8.3 `WorkflowResearchEvidence`

```text
evidence_id: Literal["evidence:000001"]
topic_plan_id: Literal["topic-plan:000001"]
attempt: Literal[1]
context_blocks: tuple[str, ...]
sources: tuple[WorkflowEvidenceSource, ...]
```

The exact context limits are:

- `1 <= len(context_blocks) <= 64`;
- each stripped block has `1 <= len(block) <= 16384`;
- the sum of all block lengths is at most 262144; and
- `0 <= len(sources) <= 200`.

Evidence contains only bounded curated/compressed text and bounded citation
identity. It contains no complete `PaperCandidate`, abstract, raw body, audit
snapshot, Provider metadata/response, prompt, header, credential, exception
text, or opaque JSON blob. It is the fake-only 3.0 boundary, not a final
production Evidence schema. A real adapter, artifact retention, audit
reference, privacy, and schema migration remain Milestone 3.1 decisions.

### 8.4 `WorkflowOutlineSection` and `WorkflowOutline`

```text
WorkflowOutlineSection
  section_id: str
  order: StrictInt
  title: str
  brief: str

WorkflowOutline
  outline_id: Literal["outline:000001"]
  evidence_id: Literal["evidence:000001"]
  attempt: Literal[1]
  title: str
  sections: tuple[WorkflowOutlineSection, ...]
```

An outline has at least one section. Section order is contiguous from one,
IDs match order, IDs are unique, and stripped titles are unique. Future
parallel completion must merge by this preallocated order.

## 9. Failure, event, phase, and status contracts

### 9.1 `AdapterFailure`

`AdapterFailure` is a strict frozen return DTO, never an exception:

```text
code: Literal[
    "topic_planning_failed",
    "research_evidence_failed",
    "outline_writing_failed",
]
```

It contains no node ID, message, exception, repr, traceback, response, prompt,
credential, metadata, or payload. Each adapter method may return only its
node-corresponding code. A mismatched code is an invariant failure.

### 9.2 `WorkflowError`

```text
error_id: Literal["error:000001"]
order: Literal[1]
failed_node_id: Literal["topic_planner", "research_evidence", "outline_writer"]
attempt: Literal[1]
code: AdapterFailure.code
```

The code and failed node must correspond exactly. A business-failed state has
exactly one `WorkflowError`; running and completed states have none.

### 9.3 `WorkflowEvent`

```text
event_id: str
order: StrictInt
event_type: Literal[
    "node_started",
    "node_completed",
    "workflow_completed",
    "workflow_failed",
]
node_id: Literal["topic_planner", "research_evidence", "outline_writer"] | None
attempt: Literal[1]
```

Event order begins at one and is contiguous. `event_id` exactly matches order.
Node events require a node ID. `workflow_failed` requires the failed node ID.
`workflow_completed` requires `node_id=None`. Events contain no timestamp,
duration, message, payload, exception, or dynamic metadata.

### 9.4 `AcademicWorkflowState`

```text
schema_version: Literal["1"]
workflow_id: str
thread_id: str
run_id: str
phase: Literal[
    "initialized",
    "topic_planned",
    "evidence_collected",
    "outline_ready",
]
status: Literal["running", "completed", "failed"]
request: AcademicWorkflowRequest
topic_plan: WorkflowTopicPlan | None
research_evidence: WorkflowResearchEvidence | None
outline: WorkflowOutline | None
errors: tuple[WorkflowError, ...]
events: tuple[WorkflowEvent, ...]
```

State and request identity must match exactly. These are the only reachable
phase/status shapes:

| Phase | Status | Plan | Evidence | Outline | Errors | Event count |
| --- | --- | --- | --- | --- | ---: | ---: |
| `initialized` | `running` | absent | absent | absent | 0 | 0 |
| `topic_planned` | `running` | present | absent | absent | 0 | 2 |
| `evidence_collected` | `running` | present | present | absent | 0 | 4 |
| `outline_ready` | `completed` | present | present | present | 0 | 7 |
| `initialized` | `failed` | absent | absent | absent | 1 | 2 |
| `topic_planned` | `failed` | present | absent | absent | 1 | 4 |
| `evidence_collected` | `failed` | present | present | absent | 1 | 6 |

There is no `completed` phase and no `failed` phase. A raw execution crash,
invariant failure, or external cancellation creates no business terminal state
and leaves the last checkpoint in one of the three `running` shapes. Any other
artifact/error/event/phase/status combination is rejected as unreachable.

## 10. Four-layer JSON/checkpoint representation

The layers are strictly distinct:

1. frozen Pydantic domain DTOs;
2. one mutable LangGraph state mapping;
3. one JSON-compatible application checkpoint payload; and
4. canonical JSON restoration followed by strict DTO validation.

The graph state is exactly:

```python
class AcademicWorkflowGraphState(TypedDict):
    workflow: dict[str, JsonValue]
```

No Pydantic instance enters a graph channel. Tuples exist only in DTOs.
`model_dump(mode="json")` converts every DTO tuple to a payload list. The sole
application payload is:

```python
{"workflow": state.model_dump(mode="json")}
```

The only encoding/restoration boundary is:

```python
payload_bytes = json.dumps(
    graph_state["workflow"],
    ensure_ascii=False,
    allow_nan=False,
    separators=(",", ":"),
    sort_keys=True,
).encode("utf-8")

state = AcademicWorkflowState.model_validate_json(payload_bytes)
```

Direct `model_validate()` restoration, pickle, a second flattened form, custom
object encoding, and per-field graph channels are forbidden. Direct Python
list input to a strict tuple DTO remains invalid; a JSON array restores to a
tuple only through `model_validate_json()`.

The recursive JSON validator allows only `None`, bool, strict int, str, list,
and dict with string keys. It rejects float, tuple, set, bytes, Path, datetime,
Enum objects, exceptions, NaN, infinity, classes, functions, coroutines,
tasks, locks, queues, file handles, database connections, callbacks, and
arbitrary objects. `allow_nan=False` is a second defensive boundary.

Round-trip acceptance requires all four equalities:

1. recursive JSON value equality;
2. recursive Python type equality at every key, scalar, list element, and dict
   value (`type(before) is type(after)`);
3. strict `AcademicWorkflowState` model equality; and
4. canonical UTF-8 byte equality after restoring and re-dumping.

Every node and facade return validates through this boundary. Every node
returns exactly one complete replacement mapping:

```python
{"workflow": new_state.model_dump(mode="json")}
```

Application state never contains GPTResearcher, ResearchConductor, Retriever,
scraper, LLM/provider client, embeddings, Config, WebSocket, logger, callback,
collector, workspace, saver, or another live object.

## 11. Saver and adapter safety boundary

### 11.1 Saver is not application validation

`InMemorySaver` is used only as an injected LangGraph checkpointer in graph
tests. LangGraph's JsonPlusSerializer is not a JSON-only validator and is not
imported or used as normative API. It can serialize values outside the
application contract and therefore cannot prove JSON compatibility,
tuple/list behavior, strict DTO restoration, or absence of task exception
metadata.

Application JSON tests run independently of saver tests. Passing an
`InMemorySaver` round trip never substitutes for Section 10 validation. Saver
metadata is outside the application payload but is inspected by security tests
for safe exception text and sentinel absence.

### 11.2 Adapter protocol

```python
class AcademicWritingAdapter(Protocol):
    async def plan_topic(
        self,
        request: AcademicWorkflowRequest,
    ) -> WorkflowTopicPlan | AdapterFailure: ...

    async def collect_research_evidence(
        self,
        request: AcademicWorkflowRequest,
        topic_plan: WorkflowTopicPlan,
    ) -> WorkflowResearchEvidence | AdapterFailure: ...

    async def write_outline(
        self,
        request: AcademicWorkflowRequest,
        topic_plan: WorkflowTopicPlan,
        evidence: WorkflowResearchEvidence,
    ) -> WorkflowOutline | AdapterFailure: ...
```

Expected failure is represented only by `AdapterFailure`. An adapter must not
raise any exception, including `asyncio.CancelledError`, to express an expected
failure.

### 11.3 Fixed safe exceptions

`state.py` defines exactly:

```text
ExecutionError: "academic workflow execution failed"
InvariantError: "academic workflow invariant violation"
ThreadProtocolError: one fixed thread-protocol text from Section 12.2
```

Constructors accept no dynamic message, original exception, response, payload,
or arbitrary arguments.

An unknown adapter `Exception` becomes `ExecutionError`. State restoration,
config/state mismatch inside a node, illegal phase/status, wrong adapter
return type, response validation, duplicate/reference failure, and node logic
bugs become `InvariantError`. Neither becomes `AdapterFailure` or a business
`failed` state.

Conversion catches no exception object variable, never calls `str()` or
`repr()`, exits the `except` suite, and only then raises the fixed safe
exception. Raising a safe exception inside the handler, including merely using
`raise FixedError from None`, is forbidden because it retains `__context__`.

For both fixed exceptions tests require:

```text
__cause__ is None
__context__ is None
__suppress_context__ is False
```

Their traceback contains only the safe raise path, never the original
traceback. A secret sentinel from the raw exception must not appear in state,
events, the caller-visible exception type/`str`/`repr`, cause, context,
traceback text, or `repr(snapshot.tasks)`/checkpoint task error. No reference
to the raw exception is retained after conversion.

Both exceptions leave the last successful checkpoint unchanged. The snapshot
continues to name the failed pending node in `next`, so native recovery may be
attempted after the adapter or implementation is corrected. They never append
`WorkflowError`, `workflow_failed`, or another business event, and no
downstream node or adapter is called in the failed invocation.

## 12. Nodes, events, facades, and thread protocol

### 12.1 Deterministic node/event transitions

The logical node IDs are exactly:

```text
topic_planner
research_evidence
outline_writer
```

The initialized/running state supplied by `start_academic_workflow()` has an
empty `events` tuple. Its initial checkpoint must not contain
`topic_planner node_started`.

`node_started` is an audit event for a committed state transition, not real-time
telemetry. A node appends no event before or during its adapter call. After a
successful adapter result and all response/invariant validation complete, the
node atomically appends its own `node_started` and `node_completed`; the final
node also appends `workflow_completed` in that same committed update. No node
pre-appends the next node's `node_started`. Consequently the complete success
sequence is exactly:

```text
event:000001 topic_planner     node_started
event:000002 topic_planner     node_completed
event:000003 research_evidence node_started
event:000004 research_evidence node_completed
event:000005 outline_writer    node_started
event:000006 outline_writer    node_completed
event:000007 -                 workflow_completed
```

After a strict `AdapterFailure` return is validated, the node atomically appends
its own `node_started` followed by exactly one `workflow_failed`, appends no
`node_completed`, preserves the last successful phase, sets `status=failed`,
writes exactly one `WorkflowError`, and routes by a conditional edge to `END`.
Its terminal snapshot has `next == ()`, and `resume_academic_workflow()` rejects
it as not resumable:

```text
topic failure:
  event:000001 topic_planner node_started
  event:000002 topic_planner workflow_failed

evidence failure:
  event:000001 topic_planner     node_started
  event:000002 topic_planner     node_completed
  event:000003 research_evidence node_started
  event:000004 research_evidence workflow_failed

outline failure:
  event:000001 topic_planner     node_started
  event:000002 topic_planner     node_completed
  event:000003 research_evidence node_started
  event:000004 research_evidence node_completed
  event:000005 outline_writer    node_started
  event:000006 outline_writer    workflow_failed
```

A raw execution failure, invariant failure, or external cancellation appends
nothing. Its last checkpoint is the initialized state or the last successful
node transition and contains no `node_started` for the pending node. Resume
re-executes that pending node; only a validated success then atomically commits
that node's single `node_started` and `node_completed`, while a validated
`AdapterFailure` atomically commits its single `node_started` and
`workflow_failed`. Previously completed nodes and events never repeat.

All graph nodes are private closures. Before an adapter call a node restores
the state through Section 10, verifies the exact running predecessor shape,
and verifies runtime `configurable.thread_id` against state/request identity.
After an adapter return it validates the exact return union and every
cross-reference before emitting an update. No node uses silent phase-skip or
automatic retry logic.

### 12.2 Facade API and fixed thread errors

`graph.py` publicly exposes exactly these thread operations:

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
```

The private graph builder creates a fresh `StateGraph`, uses `START`/`END`,
installs the three closures and conditional edges, and calls exactly:

```python
builder.compile(checkpointer=checkpointer)
```

It returns the raw compiled graph only to the calling facade. It is prefixed
with `_`, is not re-exported, and is never returned by a public function.

Only the facades construct `RunnableConfig`, exactly as:

```python
{"configurable": {"thread_id": validated_identity.thread_id}}
```

No public config helper exists. Facades add no timestamp, `thread_ts`, tags,
metadata, callback, run ID, or other configurable value.

`ThreadProtocolError` has exactly four allowed fixed texts:

```text
academic workflow checkpoint does not exist
academic workflow identity does not match checkpoint
academic workflow thread already exists
academic workflow thread is not resumable
```

No dynamic identity or snapshot content enters these exceptions. Guard order
is frozen and is the only priority:

**Start**

1. strictly validate the request;
2. build graph/config and call `snapshot = await graph.aget_state(config)`;
3. if `snapshot.created_at is None`, build the initialized/running state with
   an empty `events` tuple and call
   `await graph.ainvoke(initial_state, config=config)`;
4. otherwise strictly restore the checkpoint state;
5. if workflow/thread/run identity differs, raise
   `academic workflow identity does not match checkpoint`;
6. otherwise raise `academic workflow thread already exists`.

**Resume**

1. strictly validate `AcademicWorkflowIdentity`;
2. build graph/config and call `snapshot = await graph.aget_state(config)`;
3. if `snapshot.created_at is None`, raise
   `academic workflow checkpoint does not exist`;
4. strictly restore the checkpoint state;
5. if workflow/thread/run identity differs, raise
   `academic workflow identity does not match checkpoint`;
6. if status is `completed` or `failed`, raise
   `academic workflow thread is not resumable`;
7. if `snapshot.next == ()`, raise
   `academic workflow thread is not resumable`;
8. validate that `snapshot.next` contains exactly the one node mechanically
   implied by the running phase; otherwise raise `InvariantError`;
9. only then call `await graph.ainvoke(None, config=config)`.

Thus a second start always rejects, an unused-thread resume always rejects,
identity mismatch precedes same-identity start/terminal errors, completed and
business-failed threads reject resume, and only a running checkpoint with one
valid nonempty `next` is resumable. Facades restore and return a strict DTO,
never the raw graph mapping.

### 12.3 External cancellation contract

Milestone 3.0 promises cancellation semantics only when the caller cancels the
outer task currently awaiting a facade's `ainvoke`. The graph test blocks a
fake adapter after entry but before it returns, calls `task.cancel()`, and
awaits the task. No event for that pending node has been committed.

The test asserts only that an `asyncio.CancelledError` is observed; it does not
assert exception object identity. No node or adapter may construct or raise
`CancelledError` as a normal failure. If a node receives a `CancelledError`
while `asyncio.current_task().cancelling() == 0`, that is an invariant violation
rather than supported cancellation. No LangGraph-specific cancellation class
is imported, caught, or equated with this external cancellation contract.

External cancellation does not create `status=failed`, `WorkflowError`,
`workflow_failed`, or a new application event. The last checkpoint remains the
initialized state or last successful running state; its events contain no
`node_started` for the pending node. Recovery is permitted only through
`resume_academic_workflow()` and its nonempty-`snapshot.next` guard.

## 13. Offline test matrix and fail-fast boundary

### 13.1 State and JSON tests

`tests/test_academic_writing_workflow_state.py` contains only DTO and
application JSON tests. It covers:

- strict/coercion, bool-as-int, extra-field, blank, length, count, duplicate,
  order, derived-ID, cross-reference, and unreachable phase/status rejection;
- Python tuple/list rejection and JSON-array-to-tuple restoration;
- all running, completed, and business-failed state/event shapes;
- recursive JSON value and Python type equality;
- strict model and canonical byte equality;
- exact encoding parameters and `model_validate_json()` restoration;
- rejection of float, NaN/infinity, tuple payloads, non-string keys, and every
  forbidden Python/live object; and
- Evidence per-field, per-item, collection-count, and aggregate-size limits.

It does not instantiate a saver. Therefore no saver serializer behavior can
make an application JSON test pass.

### 13.2 Graph, saver, recovery, security, and cancellation tests

`tests/test_academic_writing_workflow_graph.py` uses only deterministic fake
adapters and an injected `InMemorySaver`. It covers:

- fresh start with zero events, successful transition event counts two/four/seven,
  and exact seven-event success;
- strict `AdapterFailure` return at each node and exact failed shapes/events;
- wrong failure code and invalid adapter response as `InvariantError`;
- raw adapter exception as `ExecutionError`;
- exact fixed types/texts and absence of original cause/context/traceback and
  secret sentinel across caller, state, events, snapshot tasks, and metadata;
- crash checkpoint phase/status/completed-event prefix with no event for the
  pending node, exact pending `next`, and native resume that commits that node's
  started/completed events exactly once;
- all four thread error texts and their priority;
- unused, mismatched, second-start, terminal, empty-next, invalid-next, and
  different-thread cases;
- externally cancelled facade task, no business failure or pending-node event,
  and guarded resume that commits the pending node's events exactly once;
- independent saver threads and independent private graph builds; and
- differential import safety and proof that no existing legacy/external entry
  point is imported, compiled, or called.

Saver tests and Section 13.1 JSON tests remain separate test functions and
assertion paths.

### 13.3 Test-level external-I/O fail-fast

Before a fake graph test runs, an autouse fixture installs fail-fast sentinels
that raise `AssertionError` on:

- socket connect/connect_ex/create_connection and asyncio network connection;
- HTTP client send/request entry points if present;
- subprocess `Popen`, run/call/check helpers, and asyncio subprocess helpers;
- file writes through built-in open write/append/create/update modes,
  `Path.write_text`, `Path.write_bytes`, and write-capable `os.open` flags;
- real LLM/Provider, Retriever, MCP, scraper/compressor, GPTResearcher,
  ResearchConductor, WebSocket, ReportStore, and exporter constructors or call
  entry points if already imported.

The fixture is installed after test/module imports and allows read-only module
and metadata access. It explicitly does not patch or block asyncio scheduling,
task cancellation, `importlib.metadata`, `json.dumps/json.loads`, Pydantic,
LangGraph's in-process execution, or `InMemorySaver`. Tests use no temporary
artifact file and perform no real write.

No hash-seed subprocess test is required or permitted. Determinism is proven
by exact derived IDs, contiguous order, repeated in-process clean/recovered
payload equality, and the prohibition on unordered identity/order sources.
Thus the subprocess fail-fast rule has no conflicting exception.

### 13.4 Dependency-baseline mechanical test

`tests/test_langgraph_dependency_baseline.py` performs no installation and
uses `tomllib`, `ast`, `importlib.metadata`,
`packaging.requirements.Requirement`, and
`packaging.specifiers.SpecifierSet`. It must:

1. assert installed `langgraph` satisfies `>=1.2.11,<2`;
2. assert installed `langgraph-checkpoint` satisfies `>=4.2.0,<5`;
3. import exactly `StateGraph`, `START`, `END`, and `InMemorySaver` from the
   Section 3.2 paths;
4. parse, rather than substring-match, Poetry and PEP 621 declarations in
   `pyproject.toml` and assert both exact specifier sets;
5. parse root `requirements.txt` and assert both exact specifier sets;
6. parse `multi_agents/requirements.txt` and assert the exact LangGraph range;
7. parse `setup.py` with `ast` without executing it and assert no exact
   normalized exclusion entry can filter `langgraph` or
   `langgraph-checkpoint` from root requirements;
8. inspect all four Python dependency entry paths and assert no
   `langgraph-checkpoint-sqlite`, `langgraph-checkpoint-postgres`, or other
   persistent saver dependency; and
9. reject contains/substring-based version assertions.

This dependency test does not import `multi_agents.agent`, compile the old
graph, or contact a package index.

## 14. Existing `multi_agents` isolation record

The following pre-existing facts are explicitly recorded and not repaired:

- `EditorAgent` references missing `_route_draft_review` wiring;
- `ChiefEditorAgent` references missing `_route_fact_check` wiring;
- importing `multi_agents.agent` constructs and compiles a graph at import
  time;
- the orchestrator constructor creates a time-derived ID and output directory;
- its invocation config still contains the old `thread_ts` key, which is not
  the 1.2.11 checkpoint-selection contract; and
- its loose state and WebSocket/console human review are not 3.0 contracts.

The StateGraph primitives used by those files were locally importable and a
fully mocked build could compile only after process-local injection of the two
missing route methods. This is not a claim that `multi_agents` is complete,
compatible as an application, deployable, or runnable.

No 3.0 production module imports or reuses `multi_agents`. No 3.0 test imports
`multi_agents.agent`, instantiates its agents, compiles or invokes its graph,
or calls its LLM/MCP/provider paths. The dependency test reads only dependency
text/AST. The existing defects cannot block skeleton acceptance.

## 15. Deferred milestones

- **3.1:** a real bounded ResearchEvidence adapter reusing current research,
  screening, candidate, and audit logic, plus artifact/privacy decisions.
- **3.2:** real outline writing and native OutlineApproval interrupt semantics.
- **3.3:** bounded parallel SectionWriters with deterministic outline-order
  merge and targeted rewrites.
- **3.4:** CitationReviewer and evidence-to-citation traceability.
- **3.5:** FinalEditor, consistency review, and final report composition.
- **3.6:** authenticated request mounting, persistent saver, migrations,
  retention, REST/WebSocket lifecycle, and frontend approval UI.

Each requires a separate approved specification. `legacy` remains the external
default throughout rollout.

## 16. Draft approval checklist

- [ ] The Route A dependency baseline and exact normative API allowlist are approved.
- [ ] The `snapshot.created_at is not None` existence predicate is approved.
- [ ] The exact four-modified plus nine-new 13-file boundary is approved.
- [ ] The empty `__init__.py` exports and one-way import boundary are approved.
- [ ] The strict DTO, identity, phase/status, and deterministic event contracts are approved.
- [ ] The bounded fake-only Evidence schema is approved.
- [ ] The four-layer JSON boundary and four equality requirements are approved.
- [ ] The JsonPlusSerializer/non-validator saver boundary is approved.
- [ ] `AdapterFailure` as a strict return DTO is approved.
- [ ] The fixed `ExecutionError` and `InvariantError` security contract is approved.
- [ ] The start/resume facade and four thread-error priorities are approved.
- [ ] The external-task-only cancellation contract is approved.
- [ ] The dependency mechanical test and external-I/O fail-fast fixture are approved.
- [ ] The existing `multi_agents` record and explicit no-fix/no-import boundary are approved.
- [ ] The 3.0 non-goals and default-disabled legacy boundary are approved.
- [x] This specification received explicit approval before implementation began.

## 17. Implementation acceptance checklist

- [ ] Only the exact four approved dependency files changed and the exact nine approved files were added.
- [ ] Status remained Draft until explicit approval and no implementation exceeded this specification.
- [ ] Poetry, PEP 621, root requirements, multi-agent requirements, and setup filtering match Section 3.1.
- [ ] No lock, SQLite/Postgres saver dependency, installation, upgrade, or downgrade was added.
- [ ] The normative runtime uses only the Section 3.2 API allowlist.
- [ ] Both `__init__.py` files contain only a docstring and `__all__ = ()`.
- [ ] New imports compile no graph and create no saver, task, thread, file, logger, environment read, or external connection.
- [ ] Production imports follow the exact one-way direction and do not import backend, provider, or `multi_agents` modules.
- [ ] The raw compiled graph and RunnableConfig construction remain private to the two facades.
- [ ] `start_academic_workflow()` rejects every existing same or mismatched thread with the fixed priority.
- [ ] `resume_academic_workflow()` rejects missing, mismatched, terminal, empty-next, and invalid-next threads.
- [ ] Only a running checkpoint with the exact one-node nonempty `next` can call `ainvoke(None, ...)`.
- [ ] All DTOs are frozen, strict, extra-forbidden, noncoercing, and reject every illegal identity/order/reference/shape.
- [ ] Every Evidence string, item, collection, and aggregate length obeys Section 8.
- [ ] Evidence contains no complete candidate, abstract/body, audit, Provider data, prompt, credential, exception text, or opaque blob.
- [ ] No Pydantic model, tuple, exception, or live object enters the graph payload.
- [ ] Payload encoding and restoration use exactly Section 10 and never direct `model_validate()`.
- [ ] Recursive value/type, strict model, and canonical byte equalities all pass.
- [ ] JsonPlusSerializer and InMemorySaver are never treated as application JSON validators.
- [ ] Adapter expected failures are strict `AdapterFailure` returns and never exceptions.
- [ ] Each AdapterFailure preserves phase, sets failed status, emits one WorkflowError/workflow_failed, and reaches END.
- [ ] The only successful terminal state is outline_ready/completed with exactly seven ordered events.
- [ ] Raw execution and invariant failures use distinct fixed safe types and never produce business failure state/events.
- [ ] Safe exceptions retain no raw object, cause, context, original traceback, dynamic text, metadata, or sentinel.
- [ ] Raw crash leaves the last committed checkpoint without a pending-node event and resume commits that node's started/completed events exactly once.
- [ ] External facade-task cancellation alone receives the cancellation guarantee and produces no business failure or pending-node event.
- [ ] No test asserts CancelledError object identity or treats an adapter-raised cancellation as normal failure.
- [ ] State tests, saver/recovery tests, and dependency tests remain mechanically separate.
- [ ] Dependency tests use Requirement/SpecifierSet parsing and AST inspection, never contains matching or setup execution.
- [ ] Fail-fast sentinels block network, HTTP, subprocess, real writes, and real external/provider components.
- [ ] Fail-fast sentinels allow asyncio, importlib.metadata, Pydantic, JSON, LangGraph in-process execution, and InMemorySaver.
- [ ] No test imports, compiles, invokes, or repairs the existing multi_agents graph.
- [ ] No real GPTResearcher, ResearchConductor, Retriever, PaperScreener, compressor, LLM, Provider, MCP, scraper, WebSocket, database, ReportStore, exporter, network, subprocess, or file output was used.
- [ ] Existing Basic Report, GPTResearcher, screening, audit, report, REST, WebSocket, frontend, and export business code remains untouched.
