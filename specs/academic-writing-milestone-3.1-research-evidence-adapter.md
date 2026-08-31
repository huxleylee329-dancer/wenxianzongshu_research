# Academic Writing Milestone 3.1 — ResearchEvidence Adapter

Status: **Approved and frozen**

This specification has been reviewed, approved, and frozen. Implementation is
authorized strictly within the exact two-file boundary in Section 4. Any scope
expansion must stop immediately and receive a revised explicit approval before
work continues. Implementation-acceptance items remain unchecked until the
authorized implementation and verification are complete.

## 1. Goal

Milestone 3.1 adds the first real integration component to the default-disabled
academic-writing workflow from Milestone 3.0: a bounded ResearchEvidence
adapter that reuses the existing `GPTResearcher` research, candidate collection,
paper-screening, and audit behavior.

The adapter:

1. preserves the existing TopicPlanner and OutlineWriter through an injected
   delegate;
2. replaces only `collect_research_evidence()` with a `GPTResearcher`-backed
   implementation;
3. calls one fresh researcher once per adapter invocation;
4. immediately projects the completed run into the existing strict, immutable,
   bounded `WorkflowResearchEvidence` DTO; and
5. stores no researcher, Config, candidate, audit, raw content, Provider object,
   or other live value in LangGraph state or checkpoint data.

Milestone 3.1 does not mount this adapter into a product entry point and does
not alter the default legacy path.

## 2. Normative baseline

Milestone 3.0 remains normative and unchanged:

```text
specs/academic-writing-milestone-3.0-langgraph-skeleton.md
```

In particular, 3.1 does not change:

- `AcademicWorkflowRequest`;
- `AcademicWritingAdapter`;
- `WorkflowTopicPlan`;
- `WorkflowEvidenceSource`;
- `WorkflowResearchEvidence`;
- `AdapterFailure`;
- node, event, phase/status, exception, cancellation, checkpoint, or facade
  semantics;
- the LangGraph dependency ranges or API allowlist; or
- the default-disabled and legacy-isolation boundaries.

If any rule in this document would require changing a 3.0 contract, 3.0 takes
precedence and implementation must stop for specification revision.

## 3. Scope and non-goals

Milestone 3.1 implements only a real ResearchEvidence adapter and its completely
mocked tests.

It does not implement:

- a real TopicPlanner;
- a real OutlineWriter;
- OutlineApproval, `interrupt()`, or a user approval UI;
- SectionWriters or parallel section writing;
- CitationReviewer or FinalEditor;
- a durable saver;
- exactly-once external execution, idempotency keys, effect journals, or cost
  deduplication;
- backend, REST, WebSocket, frontend, or request-schema integration;
- ReportStore, file export, or artifact storage;
- changes to existing screening, audit, candidate collection, compression,
  Retriever, or GPTResearcher behavior; or
- changes to existing product defaults or legacy execution.

## 4. Exact two-file implementation boundary

Implementation may add exactly these two files:

```text
gpt_researcher/workflows/academic_writing/research_evidence.py
tests/test_academic_writing_research_evidence.py
```

No existing file may be modified, including:

```text
gpt_researcher/workflows/__init__.py
gpt_researcher/workflows/academic_writing/__init__.py
gpt_researcher/workflows/academic_writing/state.py
gpt_researcher/workflows/academic_writing/adapters.py
gpt_researcher/workflows/academic_writing/nodes.py
gpt_researcher/workflows/academic_writing/graph.py
gpt_researcher/agent.py
gpt_researcher/skills/researcher.py
gpt_researcher/screening/
gpt_researcher/config/
tests/test_academic_writing_workflow_state.py
tests/test_academic_writing_workflow_graph.py
specs/academic-writing-milestone-3.0-langgraph-skeleton.md
```

No dependency, lock, backend, frontend, or other specification file may
change. If a third implementation file is required, implementation stops and
requires a revised, explicitly approved specification.

## 5. Adapter and injected runtime protocols

### 5.1 Exact public surface and aggregate adapter

The complete public surface of `research_evidence.py` is frozen as:

```python
__all__ = (
    "GPTResearcherResearchEvidenceAdapter",
    "ResearcherFactory",
)
```

Only those two names are public. Every other name newly defined by the module,
including its handle Protocols, production factory, validation helpers,
markers, and contract exception, has a leading underscore. Neither existing
package `__init__.py` nor either existing package `__all__` may change.

The adapter structurally implements the existing `AcademicWritingAdapter`
Protocol with this exact code-level surface:

```python
class GPTResearcherResearchEvidenceAdapter:
    def __init__(
        self,
        delegate: AcademicWritingAdapter,
        *,
        researcher_factory: ResearcherFactory | None = None,
    ) -> None: ...

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

When `researcher_factory` is omitted, the adapter uses the private production
factory in the same module. Neither delegate nor factory enters a DTO, graph
channel, checkpoint payload, event, or error payload. Construction must not
create or cache a researcher; it retains only the delegate and selected
factory as live adapter dependencies.

### 5.2 Delegate methods

`plan_topic(request)`:

- awaits `delegate.plan_topic(request)` exactly once;
- returns the exact same object without copying, validation, wrapping, or
  conversion;
- does not call the researcher factory;
- does not create a researcher; and
- does not read screening, audit, candidate, source, or Config data.

`write_outline(request, topic_plan, evidence)`:

- awaits `delegate.write_outline(request, topic_plan, evidence)` exactly once;
- returns the exact same object without copying, validation, wrapping, or
  conversion;
- does not call the researcher factory; and
- does not create a researcher.

The existing nodes remain responsible for strict validation of delegated
returns.

### 5.3 Private researcher-handle Protocols

The exact private Protocols are:

```python
class _ResearcherConfigHandle(Protocol):
    language: str
    max_search_results_per_query: int


class _ResearcherHandle(Protocol):
    cfg: _ResearcherConfigHandle
    image_generator: object | None

    async def conduct_research(self) -> object: ...

    def get_research_context(self) -> object: ...

    def get_paper_candidates(self) -> object: ...

    def get_paper_screening_audit(self) -> object: ...

    def get_research_sources(self) -> object: ...

    def get_source_urls(self) -> object: ...
```

The `object` annotations are deliberate untrusted-runtime boundaries, not
permission to use `Any` or to place an opaque object in output/state. Exact
shape validation and projection follow every getter.

### 5.4 `ResearcherFactory`

`ResearcherFactory` is a Protocol with the exact synchronous call shape:

```python
class ResearcherFactory(Protocol):
    def __call__(
        self,
        request: AcademicWorkflowRequest,
        topic_plan: WorkflowTopicPlan,
    ) -> _ResearcherHandle: ...
```

It is synchronous because current `GPTResearcher` construction is synchronous.
Every `collect_research_evidence()` invocation calls the selected factory
exactly once. The returned handle must be a fresh researcher for that
invocation. The adapter performs no identity reuse, cache lookup, pooling, or
singleton construction.

### 5.5 `collect_research_evidence()` call count and isolation

For each invocation the method performs exactly:

1. one factory call;
2. one `await researcher.conduct_research()` call;
3. up to the five getter calls in Section 7, each at most once and only after
   all preceding stages succeed; and
4. one local projection attempt.

It implements no retry, timeout, fallback, second research pass, background
task, or alternate Provider path. A legally empty normalized context is the
only case that still reaches all five basic getter validations before the
business failure is returned. Every factory call must return a new researcher;
the caller must provide an isolated researcher for each collect invocation.

## 6. Production factory

### 6.1 Production factory signature and lazy import

The private factory name and signature are frozen as:

```python
def _create_production_researcher(
    request: AcademicWorkflowRequest,
    topic_plan: WorkflowTopicPlan,
) -> _ResearcherHandle: ...
```

The production factory performs local imports only when actually called. The
new module has no top-level import of `gpt_researcher.agent`; its AST must prove
this. An injected fake-factory path must never execute the local production
imports.

Python's first import of any `gpt_researcher` submodule executes the existing
root `gpt_researcher/__init__.py`, which already imports
`gpt_researcher.agent` and binds `GPTResearcher`. Milestone 3.1 does not change
or repeat that baseline behavior. The only new agent import site is the local
`from gpt_researcher.agent import GPTResearcher` inside the invoked production
factory, after the rejection sequence below. Tests using an injected factory
must not execute it, construct a real researcher, or call a real Provider.

### 6.2 Supported request scope

The production factory supports only:

```text
request.report_type == "research_report"
request.report_source == "web"
```

Both comparisons are exact and case-sensitive. The production factory's
mandatory rejection and import order is:

1. check `request.report_type`;
2. check `request.report_source`;
3. locally import the existing `Tone`;
4. map Tone by exact enum `.value`;
5. only then locally import `GPTResearcher`;
6. construct `GPTResearcher`; and
7. after successful construction, assign Config fields and
   `image_generator`.

The first two checks are exactly:

```python
if request.report_type != "research_report":
    raise ValueError(
        "academic research evidence requires report_type 'research_report'"
    )

if request.report_source != "web":
    raise ValueError(
        "academic research evidence requires report_source 'web'"
    )
```

Thus these rejections precede any `Tone`/`GPTResearcher` import, Config read,
object construction, environment read, or external call. Simultaneously
invalid fields expose only the report-type error. These exact `ValueError`s are
not normalized, defaulted, or converted to `AdapterFailure`.

### 6.3 Strict Tone mapping

The factory maps `request.tone` only by exact equality to an existing
`Tone` member value:

```python
tone_by_value = {member.value: member for member in Tone}
tone = tone_by_value.get(request.tone)
```

If no member value matches exactly, the factory raises, outside every active
exception handler:

```python
ValueError("academic research evidence requires a valid Tone value")
```

Matching is exact and case-sensitive against existing member `.value`; it does
not invoke enum coercion, case-fold, match a member name, accept an alias, or
fall back to `Tone.Objective`. This rejection occurs before the local
`GPTResearcher` import, Config access, construction, environment read, or
external call.

### 6.4 Constructor mapping

The production factory constructs exactly one `GPTResearcher` with:

```text
query          = topic_plan.research_topic
report_type    = request.report_type
report_source  = request.report_source
source_urls    = list(request.source_urls)
document_urls  = list(request.document_urls)
query_domains  = list(request.query_domains)
tone           = the strictly resolved Tone member
websocket      = None
verbose        = False
log_handler    = None
mcp_strategy   = "disabled"
```

It does not concatenate or otherwise use `topic_plan.research_questions` in
the query. Each tuple conversion creates a new list with a distinct identity;
none of the three list objects is reused.

It does not pass:

- `on_progress`;
- headers, cookies, API keys, credentials, or Provider configuration;
- `mcp_configs`, MCP connection data, or MCP callbacks;
- a fallback, retry policy, timeout, or callback;
- `workflow_id`, `thread_id`, `run_id`, checkpoint identity, RunnableConfig,
  or graph state; or
- arbitrary request-derived `**kwargs`.

After successful construction and before returning the researcher, the factory
performs these assignments (they are not constructor arguments):

```python
researcher.cfg.language = request.language
researcher.image_generator = None
```

If `request.max_search_results is not None`, it additionally performs:

```python
researcher.cfg.max_search_results_per_query = request.max_search_results
```

When the request value is `None`, the existing Config value is not read,
rewritten, copied, or defaulted by the factory.

`mcp_strategy="disabled"` is the sole MCP strategy setting. No MCP configuration
is injected. Existing internal cost accounting and logging may exist during a
real run, but the adapter adds no cost callback or log handler and copies no
cost or log object into its result.

The production factory returns the new researcher only as a live adapter-local
handle. Config and researcher objects never enter the returned DTO or
checkpoint.

## 7. Conduct and getter lifecycle

After factory construction, the adapter calls:

```python
await researcher.conduct_research()
```

exactly once. Its return value is not treated as evidence and is not stored.
After success, with no further `await` through final DTO construction, the
adapter executes this sole getter pipeline:

1. call `get_research_context()` once, then immediately validate its container,
   copy it, and normalize bounded context blocks;
2. after success, call `get_paper_candidates()` once, require an exact tuple,
   retain that tuple as the frozen outer snapshot, and validate every item;
3. after success, call `get_paper_screening_audit()` once inside only the
   exact audit-unavailable handler from Section 8; outside that handler,
   validate `None` or the exact frozen audit model;
4. after success, call `get_research_sources()` once, require an exact list,
   immediately execute `tuple(returned_list)`, and in that same synchronous
   interval validate/project each member to primitive temporary records;
5. after success, call `get_source_urls()` once, require an exact list,
   immediately copy it to a tuple, require exact-string members, then strip,
   filter, deduplicate, and sort into a primitive tuple;
6. if normalized context blocks are empty, return the sole `AdapterFailure`;
7. otherwise perform audit/source cross-reference and construct the DTO.

Any getter exception or invalid getter shape stops immediately and gives every
later getter zero calls. Conduct failure or cancellation gives all five getters
zero calls. The only special case is a valid context shape that normalizes
empty: all four later getters are still called and undergo basic shape
validation. Once all five basic validations succeed, empty context takes
priority and returns before audit/source cross-reference.

Candidate/audit values remain temporary inputs. Research-source outer lists,
dicts, and `raw_content` references are discarded after primitive projection.
No getter container is retained in the DTO, state, checkpoint, closure, global,
or adapter field.

## 8. Exact audit-unavailable boundary

The module imports the fixed constant from:

```python
from gpt_researcher.screening.audit import AUDIT_UNAVAILABLE_MESSAGE
```

Only this exact exception shape means that screening audit is normally
unavailable:

```python
type(exc) is RuntimeError
and exc.args == (AUDIT_UNAVAILABLE_MESSAGE,)
```

The `try/except` may wrap only the direct call:

```python
researcher.get_paper_screening_audit()
```

When and only when both conditions hold, the adapter sets its local audit value
to `None` and continues. Audit validation/mapping, all other getters, and
source projection execute outside that handler.

Every other failure propagates unchanged out of the adapter, including:

- another `RuntimeError` message;
- a `RuntimeError` subclass;
- a multi-argument `RuntimeError`;
- an audit getter exception of another type; or
- a malformed returned audit object.

The adapter does not use `str(exc)`, `repr(exc)`, substring matching,
`startswith`, regular expressions, case-folding, or other fuzzy recognition.

## 9. Context projection

### 9.1 Frozen constants

```text
CONTEXT_BLOCK_MAX_CHARS = 16384
CONTEXT_BLOCK_MAX_COUNT = 64
CONTEXT_TOTAL_MAX_CHARS = 262144
```

Length means Python `len()` over Unicode code points.

### 9.2 Exact accepted inputs

The context getter result is accepted only when:

```text
type(value) is str
```

or:

```text
type(value) is list
and type(item) is str for every item
```

The following are fixed adapter contract violations:

- tuple, iterator, generator, mapping, or bytes;
- a `str` subclass;
- a `list` subclass; or
- a list containing any non-exact-string member.

No value is stringified.

### 9.3 Normalization and slicing

Treat an exact string as one input element and a list as its existing ordered
elements. For each element in order:

```python
normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
```

Discard an empty normalized element. For every remaining element, maintain an
offset and repeat while text and both global budgets remain:

1. compute `aggregate_remaining = 262144 - aggregate_length`;
2. take the next prefix of at most
   `min(16384, aggregate_remaining)` code points;
3. advance the source offset by the unstripped slice length;
4. strip the slice;
5. discard it if empty, otherwise append it and add its stripped length to the
   aggregate; and
6. stop globally when block count reaches 64 or aggregate reaches 262144.

Excess suffixes are deterministically discarded. Projection adds no ellipsis,
separator, marker, hash, timestamp, truncation flag, metadata, or synthetic
text. It does not resplit by paragraph, sentence, token, Markdown, or semantic
structure.

### 9.4 Empty result

Only a successfully completed research call whose normalized context contains
no nonempty block returns the business failure:

```python
AdapterFailure(code="research_evidence_failed")
```

This is the only `AdapterFailure` code allowed from this method. Adapter-
detected type/shape errors, source/audit/candidate inconsistencies, and DTO
validation failures use the fixed contract exception in Section 13; they are
not business failures.

## 10. Source projection

### 10.1 Frozen constants

```text
SOURCE_MAX_COUNT = 200
SOURCE_TITLE_MAX_CHARS = 512
SOURCE_URL_MAX_CHARS = 4096
SOURCE_CANDIDATE_ID_MAX_CHARS = 256
```

### 10.2 Exact live-input shapes

Candidate getter output must be an exact tuple containing exact
`PaperCandidate` instances. Its finalized collector order is preserved.

When audit is available, it must be an exact `PaperScreeningAuditSnapshot`.
Audit absence is handled only by Section 8.

Research-source getter output must be an exact list containing exact dicts.
For each dict:

- `url` is required and must be an exact string;
- `title`, when present and not `None`, must be an exact string;
- all other fields are ignored except for the `raw_content` availability rule;
  and
- no ignored value is traversed, copied, rendered, or retained.

Visited getter output must be an exact list of exact strings. Any other outer
or member type is a fixed adapter contract violation.

### 10.3 Usable research-source URLs

A research-source dict is usable only in one of these cases:

1. it has a `raw_content` key whose value satisfies exactly
   `type(raw_content) is str and raw_content.strip() != ""`; or
2. it has no `raw_content` key, which is the URL-only marker.

When `raw_content` is absent, the exact URL-only marker remains usable. When it
is present but is `None`, not an exact string, or has `.strip() == ""`, discard
the record. Python `str.strip()` defines Unicode whitespace. Its result is used
only for this availability predicate: raw content is never saved, returned,
hashed, logged, measured, sliced, traversed further, or read again.

For each usable record, normalize URL and optional title as specified below.
Group usable records by normalized URL. The usable URL set is the sole
audit-absent proof that a candidate URL reached the research-source lifecycle.

### 10.4 Audit-present candidate sources

When audit exists, candidate-backed sources come only from:

```text
snapshot.routed_canonical_occurrence_refs
```

Preserve that frozen tuple order. Planning-only entries,
screening-included-but-not-routed entries, and excluded entries do not enter
this stage.

Build an occurrence index keyed by:

```text
(entry.web_pass_id, entry.occurrence_id)
```

Before traversal, explicitly require every `(web_pass_id, occurrence_id)` pair
to be unique within `routed_canonical_occurrence_refs` itself. A duplicate is
an immediate fixed contract violation; it may not be hidden by an audit model,
URL first-wins, dict overwrite, or later candidate matching. Occurrence-index
keys must also be unique. For every routed ref:

1. exactly one occurrence entry must resolve;
2. `entry.routed_to_evidence` must be exactly `True`;
3. `entry.planning_only` must be exactly `False`; and
4. at least one candidate in the finalized candidate tuple must match all
   three raw fields exactly:

   ```text
   candidate.candidate_id == entry.candidate_id
   candidate.title        == entry.title
   candidate.href         == entry.href
   ```

The audit entry's `candidate_id`, `title`, and `href` are the authoritative
academic citation identity. Matching compares those raw values without strip.
Zero exact matches is a fixed contract violation. One match is accepted;
multiple exact matches are accepted because all projected fields are
identical. Repeated collector occurrences do not generate repeated sources;
final URL first-wins still applies. The adapter does not choose by object
identity, source rank, retrieval query, Provider/completion order, repair an
audit, or retain a matched `PaperCandidate`; it uses only the three audit-entry
strings. Candidate body, abstract, metadata, and audit objects are never
returned.

### 10.5 Audit-absent candidate sources

When and only when audit is normally unavailable, traverse candidates in the
finalized snapshot order. A candidate may enter candidate-backed sources only
if its stripped URL is in the usable research-source URL set from Section 10.3.

Candidate snapshot membership plus visited-URL membership is insufficient:
planning-only candidates share the same collector, visited URLs contain no
planning flag, and prefetched/routed sources need not enter the visited set.

A visited-only URL never receives a candidate ID. Screening disabled, an empty
candidate tuple, no audit, or no candidate/source URL intersection are all
valid. Nonempty context with `sources=()` is a valid success.

### 10.6 Remaining research and visited sources

After candidate-backed records:

1. append remaining usable research-source URLs after sorting their normalized
   `(url, selected_title)` pairs lexicographically; and
2. append remaining visited URLs after stripping, validating, deduplicating,
   and sorting them lexicographically by URL.

Visited-only records use `candidate_id=None`. Output order never depends on set
or dict iteration, hash seed, task completion timing, object identity, or
Provider timing.

### 10.7 URL normalization

For every source candidate:

- the raw URL must be an exact string;
- strip it;
- discard the source if the result is blank;
- discard the source if length exceeds 4096; and
- never truncate or rewrite the URL.

When multiple records produce the same normalized URL, the first record in the
frozen priority/order traversal wins. Later records are discarded completely.

### 10.8 Title normalization

Choose title deterministically:

1. an audit/candidate title for a candidate-backed source;
2. otherwise the lexicographically smallest nonblank stripped title among all
   usable research-source records for that normalized URL; or
3. otherwise the normalized URL.

An absent or `None` research-source title contributes no candidate title. A
present non-string title is a fixed adapter contract violation.

Strip the selected title and retain its first 512 code points. The result must
be nonempty. No ellipsis or truncation metadata is appended.

### 10.9 Candidate-ID normalization

`None` is valid. A present candidate ID must be an exact string; otherwise the
input is a fixed adapter contract violation.

Strip a present string. Convert it to `None` when blank or longer than 256 code
points. Candidate identity is never truncated, hashed, rewritten, or replaced
with a synthetic value.

During final traversal, the first non-`None` normalized candidate ID wins.
Later sources with the same candidate ID remain as URL-distinct sources but
their candidate ID is converted to `None`.

### 10.10 Final deduplication, truncation, and identity assignment

The exact order is:

1. validate and normalize live inputs;
2. construct the frozen source-priority sequences;
3. traverse them in their frozen order;
4. apply URL first-wins deduplication;
5. apply nonempty candidate-ID first-wins deduplication;
6. retain only the first 200 resulting records; and
7. only then assign contiguous `order` and derived `source_id`.

The final identity rules are:

```text
order = 1..N
source_id = f"evidence-source:{order:06d}"
```

No ID or order uses UUID, time, `hash()`, a set's iteration order, completion
order, or object identity.

### 10.11 Snapshot and concurrency boundary

The candidate getter must return an exact tuple; that tuple is the frozen outer
snapshot and each item undergoes exact contract validation. Audit is either
`None` or the exact already-frozen snapshot model and is never copied into the
result. Research sources must be an exact list, immediately copied with
`tuple(returned_list)`, then synchronously validated/projected to primitives
without retaining the original list/dicts. Visited URLs must be an exact list,
immediately copied to a tuple, validated as exact strings, then normalized to a
primitive tuple.

From `conduct_research()` return through `WorkflowResearchEvidence`
construction there is no `await`. The following are explicit non-goals:

- another thread mutating the same researcher concurrently;
- concurrent `collect_research_evidence()` calls on one adapter instance;
- another thread mutating nested source dicts after a getter returns; and
- external code directly mutating researcher attributes.

The caller must provide isolation and every factory call must return a fresh
researcher instance.

## 11. Exact successful return

After all getters and projections succeed and context is nonempty, construct
exactly:

```python
WorkflowResearchEvidence(
    evidence_id="evidence:000001",
    topic_plan_id="topic-plan:000001",
    attempt=1,
    context_blocks=tuple(blocks),
    sources=tuple(sources),
)
```

No existing DTO changes and no additional fields, metadata, flags, summaries,
or opaque payloads are permitted.

## 12. Sensitive-data and checkpoint boundary

The returned value, graph state, events, errors, checkpoint values, and
checkpoint metadata must not contain or retain:

- a `GPTResearcher` instance or Config;
- a `PaperCandidate` object, body, or abstract;
- a `PaperScreeningAuditSnapshot`, entry, group, request, rationale, warning,
  or decision object;
- a live `research_sources` list or any original dict;
- `raw_content`, Provider responses, prompts, headers, keys, cookies,
  credentials, or callbacks;
- cost objects, logger or log-handler objects, exception text, traceback, or
  websocket;
- scraper, compressor, Retriever, LLM, MCP, Provider, database, file, task,
  thread, lock, or connection objects; or
- an arbitrary or opaque JSON blob.

Only bounded strings and the existing strict DTOs may reach graph state.
Existing runtime cost tracking and existing logging may occur within a real
researcher run, but the adapter does not add callbacks or copy that data into
the DTO or checkpoint.

## 13. Errors and cancellation

The module defines this private exception:

```python
class _ResearchEvidenceContractError(RuntimeError):
    pass
```

Its only permitted message is:

```text
research evidence adapter contract violation
```

### 13.1 Expected business failure

The only expected business failure is:

```text
conduct_research completed successfully
and all five getters completed under their contracts
and normalized context_blocks is empty
```

It returns exactly:

```python
AdapterFailure(code="research_evidence_failed")
```

### 13.2 Propagated runtime exceptions

These original exceptions leave the adapter unchanged:

- injected or production factory exceptions;
- `GPTResearcher` construction exceptions;
- normal exceptions from `conduct_research()`;
- exceptions thrown by any of the five getters;
- every non-exact audit-unavailable `RuntimeError`; and
- external cancellation.

The three exact scope/Tone `ValueError`s from Section 6 are also propagated and
remain independent caller errors, not adapter contract errors. None is
converted to `AdapterFailure`.

### 13.3 Adapter-detected contract failures

Every failure detected by adapter validation is converted to the fixed private
`_ResearchEvidenceContractError`, including:

- illegal getter containers/members or context members;
- malformed candidate, audit, research-source, or visited-source shapes;
- duplicate routed refs or occurrence compound keys;
- missing compound refs, contradictory route flags, or zero candidate match;
- audit/candidate/source cross-reference inconsistency;
- strict DTO construction/validation failure; and
- every other adapter-detected invariant.

The fixed exception never interpolates, formats, stringifies, or represents an
input. It retains no Pydantic validation details or original object and must
satisfy `__cause__ is None` and `__context__ is None`. If an internal validation
API throws, an isolation helper consumes it and returns only a private fixed
marker; after that helper frame has exited and outside every active `except`,
the caller raises the fixed contract error. No original exception, validation
input, traceback local, closure, attribute, global, checkpoint task, or metadata
may remain reachable. The contract error is not an `AdapterFailure`; the
existing node converts it to its fixed safe `ExecutionError`.

### 13.4 Cancellation

`asyncio.CancelledError` propagates out of the adapter unchanged. The adapter:

- does not catch it as `AdapterFailure`;
- does not wrap it;
- does not actively construct or raise it to express a business failure; and
- implements no cancellation cleanup that changes workflow state.

The existing 3.0 node/facade contract remains authoritative: external
cancellation does not create a business failure or pending-node event, and a
valid pending checkpoint may later resume.

### 13.5 No local retries

The adapter has no retry loop, timeout, fallback, alternate getter sequence,
or second research attempt. A single adapter invocation has one factory call
and one `conduct_research()` call even when the call fails.

## 14. Recovery and at-least-once semantics

Milestone 3.1 explicitly accepts at-least-once external execution:

- `conduct_research()` may already have invoked Providers, Retrievers, LLMs,
  scraping, compression, or other external effects;
- a raw crash or external cancellation before the evidence state transition is
  checkpointed leaves the last successful `topic_planned/running` checkpoint;
- `resume_academic_workflow()` re-executes the complete ResearchEvidence node;
- the resumed adapter call invokes the factory again;
- the factory returns a second fresh researcher;
- `conduct_research()` executes again; and
- searches, costs, and logs may therefore repeat.

The successful TopicPlanner checkpoint and its two events do not repeat.
ResearchEvidence and downstream work execute again from the pending node.

Milestone 3.1 provides no exactly-once guarantee, idempotency key, effect
journal, persisted evidence artifact, Provider-result cache, or cost
deduplication.

## 15. Import, mounting, and legacy isolation

`research_evidence.py` is not imported from either existing `__init__.py`, and
no `__all__` changes. A future caller must explicitly import and construct the
adapter.

Milestone 3.1 does not mount it into:

- backend or frontend code;
- current `GPTResearcher` or `ResearchConductor` construction;
- request schemas or `run_agent()`;
- REST or WebSocket routes;
- the private default graph factory; or
- any legacy, Basic Report, screening, audit, report, or export path.

The canonical import guarantee is differential. Python first imports
`gpt_researcher`; its existing root behavior imports `gpt_researcher.agent` and
binds `GPTResearcher`. The test first performs that canonical root import and
records the identity of `sys.modules["gpt_researcher.agent"]`, the identity of
`gpt_researcher.GPTResearcher`, the baseline `sys.modules` set, and the target
module's parent-package attribute existence/identity. It then saves/removes
only the target module's canonical `sys.modules` entry and parent binding,
imports the exact canonical name
`gpt_researcher.workflows.academic_writing.research_evidence`, and requires:

- the agent module identity is unchanged;
- the root `GPTResearcher` identity is unchanged;
- no second agent import occurred; and
- relative to baseline, the new module caused no new graph, saver, task,
  thread, lock, logger, environment, file, network, or subprocess side effect.

A `finally` block restores the target `sys.modules` entry and parent attribute
to their exact prior absence/presence and identity, including every failure
path. The new module's AST independently proves there is no top-level
`gpt_researcher.agent` import. The production factory's local import is the only
permitted new agent import site; an injected fake path must not execute it.

Beyond the pre-existing root-package baseline, importing the new module must
not:

- compile or invoke a graph;
- create a saver, researcher, Config, task, thread, lock, logger, or handler;
- read environment variables or files;
- open a socket or HTTP client;
- start a subprocess or external service; or
- perform a Provider, LLM, Retriever, scraper, compressor, MCP, WebSocket,
  database, or file-write operation.

The production factory's local import and live construction occur only after a
caller explicitly invokes the default factory at runtime and after all frozen
pre-import rejection checks pass.

## 16. Offline test matrix

`tests/test_academic_writing_research_evidence.py` uses only deterministic
fakes, injected factories, and `InMemorySaver`. It does not use a real
researcher or external service.

### 16.1 Delegate behavior

Tests prove:

- `plan_topic()` calls its delegate exactly once and returns the same object;
- `write_outline()` calls its delegate exactly once and returns the same
  object; and
- neither method calls the factory or creates/reads a researcher.

### 16.2 Production-factory mapping

Without importing or constructing the real `GPTResearcher`, tests install a
controlled fake at the production factory's lazy import boundary and prove:

- canonical import preserves the already-loaded agent module and root
  `GPTResearcher` identities and does not import agent a second time;
- the injected fake-factory path never executes the production factory's local
  imports;
- report-type rejection precedes source/Tone/agent/Config/construction work,
  source rejection precedes Tone/agent/Config/construction work, and invalid
  Tone precedes agent/Config/construction work;
- the three `ValueError` types/messages and simultaneous-invalid priority are
  byte-for-byte exact;
- each collect call constructs a fresh researcher;
- query equals `research_topic` exactly and excludes research questions;
- report type/source restrictions are exact;
- source/document/domain tuples become three identity-distinct new lists;
- Tone mapping accepts exact member values and rejects invalid values without
  Objective fallback;
- language and non-`None` max-results settings are applied to the fake Config;
- `None` leaves the existing max-results value unchanged;
- websocket, verbose, log handler, image generator, and MCP settings match
  Section 6; and
- headers, credentials, callbacks, checkpoint identity, and arbitrary kwargs
  are not passed.

### 16.3 Call count and order

Tests prove exactly one factory call, one `conduct_research()` call, zero local
retries, and the exact short-circuit order from Section 7. Every thrown getter
and every invalid returned shape gives all later getters zero calls. Conduct
failure/cancellation gives every getter zero calls. A valid-but-empty context
alone reaches one call and basic shape validation for each remaining getter,
then returns before cross-reference.

### 16.4 Context projection

Tests cover:

- exact string and exact `list[str]`;
- CRLF and bare-CR normalization to LF;
- outer and per-item strip and empty-item deletion;
- 16,384 and 16,385 code-point boundaries;
- 64-block and 262,144-character boundaries;
- deterministic prefix retention and suffix discard;
- non-ASCII code points;
- absence of ellipsis or truncation metadata;
- every rejected outer/subclass/member type as the fixed contract error; and
- empty normalized context as the sole allowed AdapterFailure.

### 16.5 Audit present

Tests cover:

- routed-ref order across one and multiple web passes;
- exclusion of planning-only, included-but-not-routed, and excluded entries;
- compound occurrence lookup and explicit duplicate routed-ref rejection;
- zero/one/multiple exact candidate matches by raw candidate ID/title/href,
  with zero rejected and one or multiple accepted;
- repeated identical candidates producing no duplicate source;
- missing and inconsistent mappings as fixed contract failures; and
- absence of audit snapshots, rationale, warnings, decisions, and other audit
  data in output and checkpoint state.

### 16.6 Audit absent

Tests cover:

- only exact `AUDIT_UNAVAILABLE_MESSAGE` being accepted;
- another RuntimeError, a RuntimeError subclass, and multi-argument RuntimeError
  propagating;
- candidates intersecting only with usable research-source URLs;
- planning-only candidates excluded when their URLs never entered usable
  research sources;
- visited-only URLs receiving `candidate_id=None`; and
- screening disabled, empty candidates, absent audit, and empty sources as
  valid cases when context is nonempty.

### 16.7 Research sources and visited URLs

Tests cover:

- exact `type(raw_content) is str and raw_content.strip() != ""` records as
  usable, including Unicode whitespace cases;
- every record without a `raw_content` key as a URL-only marker;
- `None`, blank, and non-string `raw_content` records being discarded;
- no returned or retained raw-content value;
- malformed containers/records/fields as fixed contract failures where
  specified;
- lexicographic `(url, title)` ordering of remaining research sources;
- sorted visited URLs;
- deterministic output across differently ordered visited inputs; and
- no dependence on hash seed, set/dict iteration, or completion timing.

### 16.8 Source limits and derived identity

Tests cover:

- blank URL, length 4096, and length 4097;
- title lengths 512 and 513;
- candidate-ID lengths 256 and 257;
- URL first-wins behavior across priorities;
- candidate-ID first-wins with later IDs converted to `None`;
- exact 200-source cap;
- truncation/deduplication before final identity allocation;
- contiguous order and exact source ID derivation; and
- successful nonempty context with `sources=()`.

### 16.9 Graph, failure, cancellation, and resume

Using the existing facades and `InMemorySaver`, tests prove:

- factory/conduct/getter runtime exceptions propagate from the adapter and
  become the existing fixed `ExecutionError` through the graph;
- all adapter-detected violations raise only the private fixed contract error,
  with no cause/context or original validation detail, and then become the
  existing fixed node `ExecutionError`;
- no sentinel, raw exception text, live object, candidate, audit, or raw content
  reaches state, events, task errors, or checkpoint metadata;
- raw crash leaves `snapshot.next == ("research_evidence",)`;
- resume invokes a second factory and receives a second fresh researcher;
- TopicPlanner does not repeat while ResearchEvidence and downstream work do;
- final successful event order remains the exact 3.0 seven-event sequence;
- expected AdapterFailure creates the existing non-resumable failed terminal
  state; and
- cancelling the outer facade task does not create business failure, preserves
  the pending checkpoint, propagates `CancelledError`, and permits successful
  resume.

### 16.10 Canonical import and fail-fast safety

Tests implement the exact canonical import differential from Section 15. They
restore the target `sys.modules` object and parent binding in `finally` on both
success and injected failure, preserve identities, and do not depend on test
order. They mechanically monitor compile/saver/researcher creation, tasks,
threads, locks, logger changes, environment reads, file access, network, and
subprocess activity. AST inspection covers the new production module's
top-level agent imports and environment/file/subprocess operations; source
substring checks are never the sole proof.

The test file defines exactly one qualified registry and no second names list:

```python
_EXTERNAL_ENTRYPOINTS: tuple[tuple[str, str], ...] = (
    ("gpt_researcher", "GPTResearcher"),
    ("gpt_researcher.agent", "GPTResearcher"),
    ("gpt_researcher.skills.researcher", "ResearchConductor"),
    ("gpt_researcher.utils.llm", "create_chat_completion"),
    ("gpt_researcher.actions.web_scraping", "scrape_urls"),
    ("gpt_researcher.actions.retriever", "get_retriever"),
    ("gpt_researcher.actions.retriever", "get_retrievers"),
    ("gpt_researcher.llm_provider.generic.base", "GenericLLMProvider"),
    ("gpt_researcher.llm_provider.generic.base", "_check_pkg"),
    ("gpt_researcher.context.retriever", "SearchAPIRetriever"),
    ("gpt_researcher.context.retriever", "SectionRetriever"),
    ("gpt_researcher.context.compression", "VectorstoreCompressor"),
    ("gpt_researcher.context.compression", "WrittenContentCompressor"),
    ("gpt_researcher.context.compression", "ContextualCompressionRetriever"),
    ("gpt_researcher.context.compression", "DocumentCompressorPipeline"),
    ("gpt_researcher.context.compression", "ContextCompressor"),
    ("gpt_researcher.llm_provider.image.image_generator", "ImageGeneratorProvider"),
    ("gpt_researcher.llm_provider.image.modelslab_image_generator", "ModelsLabImageGeneratorProvider"),
    ("gpt_researcher.skills.image_generator", "ImageGenerator"),
    ("gpt_researcher.scraper.arxiv.arxiv", "ArxivScraper"),
    ("gpt_researcher.scraper.beautiful_soup.beautiful_soup", "BeautifulSoupScraper"),
    ("gpt_researcher.scraper.browser.browser", "BrowserScraper"),
    ("gpt_researcher.scraper.browser.nodriver_scraper", "NoDriverScraper"),
    ("gpt_researcher.scraper.pymupdf.pymupdf", "PyMuPDFScraper"),
    ("gpt_researcher.scraper.web_base_loader.web_base_loader", "WebBaseLoaderScraper"),
    ("gpt_researcher.scraper.scraper", "Scraper"),
    ("gpt_researcher.scraper.browser.processing.scrape_skills", "ArxivRetriever"),
    ("gpt_researcher.retrievers.mcp.retriever", "MCPRetriever"),
    ("gpt_researcher.mcp.client", "MCPClientManager"),
    ("gpt_researcher.retrievers.utils", "check_pkg"),
    ("gpt_researcher.actions.utils", "stream_output"),
    ("gpt_researcher.actions.utils", "safe_send_json"),
    ("gpt_researcher.skills.researcher", "stream_output"),
    ("gpt_researcher.skills.image_generator", "stream_output"),
    ("gpt_researcher.retrievers.utils", "stream_output"),
)
```

This registry is the sole source for fixture patching, resolution tests, direct
call tests, and teardown. Setup imports each exact module and resolves its
attribute with `inspect.getattr_static(module, attribute_name)`; a missing
target immediately fails. There is no `hasattr` skip, fuzzy scan, unqualified
class-name scan, or scan of `sys.modules`. Each replacement is directly called
and must immediately raise one fixed fail-fast exception without relying on a
socket blocker. A `try/finally` restores every original object, after which
static resolution proves identity restoration.

The registry covers the currently reachable GPTResearcher/ResearchConductor,
LLM and image Provider, retriever factory/retrievers, scraper/compressor, MCP
client/retriever, dynamic package-install subprocess, and WebSocket wrapper
entrypoints. The second layer patches exact socket connect/create-connection
entrypoints; `requests.sessions.Session.request`; `urllib.request.urlopen`;
`httpx.Client.request`; `httpx.AsyncClient.request`;
`aiohttp.ClientSession._request`; `subprocess.Popen`, `run`, `call`,
`check_call`, and `check_output`; `asyncio.create_subprocess_exec` and
`create_subprocess_shell`; and write-capable `builtins.open`/`os.open` modes.
Windows asyncio internal loopback, ordinary asyncio scheduling/cancellation,
`InMemorySaver`, JSON, Pydantic, AST, `importlib.metadata`, and test-owned
read-only source/manifest access are explicitly allowed.

No test calls a real researcher or external service. Production-factory tests
inject/patch construction before invocation and never instantiate the real
class.

### 16.11 Safe recursive sentinel inspection

Security tests use one cycle-safe walker with `seen: set[int]` keyed by
identity. It may traverse exact built-in tuple/list/dict/set/frozenset,
traceback frame locals, and function closure cells. For every other object it
may use only `gc.get_referents()`; it must not call any arbitrary object method
or custom iterator.

The walker must never call an inspected object's `repr`, `str`, `getattr`,
`vars`, `__dict__`, property, or descriptor. Its AST test mechanically rejects
those calls. Hostile test objects make `__repr__`, `__str__`, dynamic
`__getattribute__`, and a property fail if invoked. The walker checks adapter
result DTOs, graph state, checkpoint values/tasks/metadata, exception traceback
frame locals, cause/context, and function closures. No stringification,
`contains`, shallow-only inspection, or source-code self-proof may substitute.
These reachability assertions apply to adapter-detected fixed contract errors
and the graph's fixed `ExecutionError`; deliberately propagated original
factory/conduct/getter exceptions are checked for identity propagation at the
adapter boundary and for sanitization only after the existing node boundary.

### 16.12 Execution orders and repository checks

All three commands below must pass independently with the repository Python
3.11 environment, `-B`, and `-p no:cacheprovider`:

```powershell
& '.venv\Scripts\python.exe' -B -m pytest -p no:cacheprovider `
  tests/test_academic_writing_research_evidence.py
```

```powershell
& '.venv\Scripts\python.exe' -B -m pytest -p no:cacheprovider `
  tests/test_academic_writing_workflow_state.py `
  tests/test_academic_writing_workflow_graph.py `
  tests/test_langgraph_dependency_baseline.py `
  tests/test_academic_writing_research_evidence.py
```

```powershell
& '.venv\Scripts\python.exe' -B -m pytest -p no:cacheprovider `
  tests/test_academic_writing_research_evidence.py `
  tests/test_academic_writing_workflow_state.py `
  tests/test_academic_writing_workflow_graph.py `
  tests/test_langgraph_dependency_baseline.py
```

Test counts may differ across commands, but no pass may depend on module,
fixture, patch, environment, or execution-order residue. Verification also
checks:

Implementation verification includes the new test file and the existing 3.0
state/graph/dependency tests under their established offline protections. It
also checks:

- the 3.0 frozen specification has no diff;
- no existing file changed;
- the worktree contains exactly the two approved additions;
- `git diff --check` passes; and
- the staging area remains empty.

No test installation, network access, real Provider execution, or repository
state mutation is permitted.

## 17. Draft approval checklist

- [ ] The Milestone 3.1 goal and non-goals are approved.
- [ ] The exact two-new-file and zero-existing-file-change boundary is approved.
- [ ] The exact two-name public surface and private helper Protocols are approved.
- [ ] The aggregate delegate/factory adapter signatures are approved.
- [ ] The canonical root-import baseline and production-factory lazy import boundary are approved.
- [ ] The exact report type/source/Tone rejection order, messages, and import timing are approved.
- [ ] The exact GPTResearcher constructor and Config mapping is approved.
- [ ] The one factory, one conduct, ordered getter short-circuit, and empty-context priority are approved.
- [ ] The exact audit-unavailable predicate is approved.
- [ ] The context type, normalization, count, item, and aggregate rules are approved.
- [ ] The audit-present compound-ref uniqueness and zero/one/multiple candidate-match rules are approved.
- [ ] The audit-absent planning-only exclusion rule is approved.
- [ ] The exact raw-content availability predicate and visited-URL semantics are approved.
- [ ] The live snapshot and explicit concurrency boundary is approved.
- [ ] The source priority, ordering, deduplication, limits, and IDs are approved.
- [ ] The sensitive-data and checkpoint exclusion boundary is approved.
- [ ] The fixed private contract exception and propagated-runtime distinction is approved.
- [ ] The external cancellation semantics are approved.
- [ ] The at-least-once recovery semantics and deferred exactly-once work are approved.
- [ ] The import/mounting/legacy isolation boundary is approved.
- [ ] The single qualified fail-fast registry and second-layer I/O blockers are approved.
- [ ] The cycle-safe hostile-object sentinel walker is approved.
- [ ] The three explicit pytest execution orders are approved.
- [x] This specification received explicit approval before implementation began.

## 18. Implementation acceptance checklist

- [ ] Only `research_evidence.py` and its one test file were added.
- [ ] No existing production, test, dependency, config, frontend, backend, or specification file changed.
- [ ] The module exports exactly the two approved public names; all helpers are private and package `__init__` files are unchanged.
- [ ] The adapter and factory signatures exactly match Section 5 and the existing aggregate Protocol remains unchanged.
- [ ] Topic planning and outline writing delegate exactly once and preserve object identity.
- [ ] Delegate-only methods never call the factory or create a researcher.
- [ ] Canonical import preserves the existing agent/GPTResearcher identities, does not re-import agent, and restores target module/bindings in `finally`.
- [ ] AST proves no top-level agent import; injected fake factory never executes the production local import.
- [ ] Canonical module import adds no graph, saver, researcher, task, thread, lock, logger, env, file, network, or subprocess side effect relative to baseline.
- [ ] Report type/source/Tone checks use exact priority, ValueError text, and pre-import/pre-construction rejection timing.
- [ ] Constructor lists are fresh and constructor/Config mappings match Section 6 exactly.
- [ ] Image generation is disabled only after construction, MCP is disabled exactly, and no callback or secret is passed.
- [ ] Every collect invocation uses one fresh researcher, one conduct call, and zero local retries.
- [ ] Getter order, immediate validation, later-getter zero-call short-circuit, and audit handler boundary are exact.
- [ ] A valid empty context still basic-validates all getters, then returns before cross-reference.
- [ ] Context projection obeys exact type, CRLF, strip, block, count, aggregate, and deterministic truncation rules.
- [ ] Empty normalized context returns only `research_evidence_failed`.
- [ ] Audit presence uses only routed canonical refs in frozen order.
- [ ] Routed compound refs are explicitly unique; duplicate refs raise the fixed contract error immediately.
- [ ] Raw candidate ID/title/href has zero rejected and one-or-more accepted matches without duplicate source generation.
- [ ] Exact audit-unavailable matching is used; all near-miss exceptions propagate.
- [ ] Audit-absent candidates intersect usable research-source URLs and visited-only URLs receive no candidate ID.
- [ ] Research-source raw-content uses the exact type/strip predicate and never retains, hashes, logs, or rereads content.
- [ ] Candidate, audit, research-source, and visited snapshots and the no-await projection interval match Section 10.11.
- [ ] URL/title/candidate-ID limits, first-wins rules, 200 cap, order, and source IDs are exact.
- [ ] Nonempty context with no source remains a valid successful DTO.
- [ ] The successful DTO contains exactly the existing fields and fixed IDs/attempt.
- [ ] No researcher, Config, candidate, body, abstract, audit, raw source, raw content, Provider data, secret, callback, cost, logger, exception, or live object enters state/checkpoint.
- [ ] Factory/conduct/getter runtime exceptions propagate unchanged from the adapter and retain existing raw-crash/ExecutionError semantics.
- [ ] Adapter-detected failures use only the fixed private contract error with cause/context `None` and no validation details.
- [ ] External cancellation propagates without a business failure or pending-node event.
- [ ] Raw crash resume creates a second researcher and demonstrates at-least-once conduct execution.
- [ ] TopicPlanner does not repeat and the final seven-event 3.0 sequence remains unchanged.
- [ ] AdapterFailure produces the existing non-resumable failed terminal state.
- [ ] The one qualified fail-fast registry resolves every target statically, patches direct calls, and restores every identity without skips or fuzzy scans.
- [ ] Socket/HTTP/subprocess/file-write second-layer blockers and the explicit internal allowlist are enforced.
- [ ] The safe recursive walker is cycle-safe, uses only approved traversal, passes hostile objects, and checks every frozen security surface.
- [ ] The walker's AST contains none of the prohibited dynamic/stringifying accesses.
- [ ] The 3.1-only, 3.0-then-3.1, and 3.1-then-3.0 commands all pass without order residue.
- [ ] The frozen 3.0 specification has no diff.
- [ ] The worktree contains exactly the two approved additions and the staging area is empty.
- [ ] `git diff --check` passes.
- [ ] No dependency was installed and no network, real LLM, Retriever, MCP, scraper, compressor, WebSocket, Provider, or external service was used.
- [ ] Implementation was not staged or committed before review.

## 19. Mandatory stop conditions

Draft approval does not waive these stop conditions. Implementation must stop
and request a revised approval if:

- the two-file boundary is insufficient;
- a 3.0 DTO, Protocol, node, graph, facade, event, error, or checkpoint contract
  must change;
- a current getter or constructor cannot satisfy the frozen design;
- implementation exposes an internal contradiction in this specification;
- a new dependency is required; or
- an existing runtime or product entry point must change.

No implementation may resolve one of these conditions by silently expanding
scope.
