# Paper Screening Milestone 2.2B — Basic/Web Two-Stage Pipeline

Status: **Approved and frozen**

This standalone specification has completed review and received explicit
implementation approval for the Basic/Web integration of the Approved and
frozen Milestone 2.2A deterministic screening engine. Implementation is
authorized only within the technical design and complete frozen eight-file
boundary defined here. Any need to modify a ninth file, a protected file, or
any other frozen boundary or behavior requires implementation to stop and this
specification to be revised, reviewed, and explicitly approved again.

Milestone 2.2B is limited to a run/web-pass-scoped, two-stage Basic/Web
research pipeline. It collects structured academic occurrences before context
compression, invokes the frozen Milestone 2.2A engine exactly once, and routes
only retained canonical academic bodies into the existing context path. It
does not alter the external Retriever contract or enable screening in any
other research mode.

## 1. Normative foundations

This Draft builds on, and must not weaken or reinterpret:

- `specs/paper-screening-milestone-2.0-candidate-model.md`;
- `specs/paper-screening-milestone-2.1-run-collection.md`; and
- `specs/paper-screening-milestone-2.2a-deterministic-engine.md`.

The Milestone 2.1 `PaperCandidateCollector` lifecycle and the Milestone 2.2A
models, policy semantics, grouping, classification, canonical selection,
decision evidence, deterministic ordering, and route semantics remain frozen.

The current Basic/Web pipeline reaches `ContextCompressor` independently for
each concurrent sub-query. The existing post-run collector snapshot therefore
cannot provide a pre-compression global barrier. Milestone 2.2B introduces a
separate `ScreeningWorkspace`; it does not reuse, inspect, finalize early, or
modify `PaperCandidateCollector`.

## 2. Frozen goal

When screening is enabled for an in-scope Basic/Web run, the web pass is split
into three ordered stages:

1. **Phase A** completes Planning and all evidence Retriever calls, preserves
   ordinary Retriever inputs, and collects academic `CandidateOccurrence`
   values without calling the scraper or context compressor.
2. **Barrier** invokes `screen_paper_occurrences()` exactly once over Planning
   and all evidence occurrences and resolves the frozen per-request routes.
3. **Phase B** sends each evidence request's routed canonical academic bodies,
   together with that request's ordinary web inputs, through the existing
   scraper and context-compression behavior.

This milestone only controls which already-retrieved academic bodies may enter
context. It introduces no second Provider request and no new Provider.

## 3. Exact entry scope

The two-stage path is eligible only when both conditions hold:

- `report_source == ReportSource.Web.value`; and
- `report_type` is exactly one of:
  - `ReportType.ResearchReport.value`;
  - `ReportType.ResourceReport.value`;
  - `ReportType.OutlineReport.value`; or
  - `ReportType.CustomReport.value`.

Eligibility is determined in the shared `GPTResearcher` Basic/Web core call
chain. The WebSocket `BasicReport` wrapper class name is not a capability or
scope test. Direct Python and CLI callers that use the same eligible
`GPTResearcher` source/type combination receive the same behavior.

The following remain unchanged and outside Milestone 2.2B:

- Quick Search;
- Hybrid;
- Local, Azure, LangChain Documents, and LangChain Vector Store sources;
- Deep Research;
- Detailed Report;
- Subtopic Report, whether nested or requested directly;
- multi-agent research; and
- the MCP result/output protocol.

When explicit `source_urls` are supplied with
`complement_source_urls=True`, only Retriever results produced by the
supplementary eligible Web pass are screened. Explicit source URLs retain the
existing scrape and compression behavior.

## 4. Runtime configuration contract

The following configuration keys are frozen:

```dotenv
PAPER_SCREENING_ENABLED=false
PAPER_SCREENING_MIN_YEAR=
PAPER_SCREENING_MAX_YEAR=
PAPER_SCREENING_UNKNOWN_YEAR=include
PAPER_SCREENING_ALLOWED_TYPES=
PAPER_SCREENING_UNKNOWN_TYPE=include
```

These keys are added to the project Config schema and defaults. The Config
layer preserves their raw values; the screening-specific parser performs the
strict normalization below.

### 4.1 Enabled flag

- Normalize with `strip().casefold()`.
- Accept only the exact normalized tokens `true` and `false`.
- The default is `false`.
- Every other value, including an empty value when explicitly supplied, is a
  configuration error.

When disabled, the existing Web path is used exactly:

- no `ScreeningWorkspace` is created;
- no screening request or occurrence ID is allocated;
- `screen_paper_occurrences()` is not called;
- no screening-specific prepared batch is created; and
- Provider, scraper, compressor, context, and outward results retain their
  existing call order and behavior.

### 4.2 Year bounds

- Apply `strip()`.
- Empty `PAPER_SCREENING_MIN_YEAR` or `PAPER_SCREENING_MAX_YEAR` maps to
  `None`.
- A nonempty value must consist only of decimal ASCII digits and must normalize
  to an integer in the inclusive range `1000` through `9999`.
- Signs, decimal points, exponent notation, booleans, and surrounding content
  are invalid.
- `min_year > max_year` is invalid.

### 4.3 Unknown-value policies

Both unknown-policy values use `strip().casefold()` and accept only:

- `include`; or
- `exclude`.

They map directly to the frozen Milestone 2.2A `UnknownValuePolicy` values.

### 4.4 Allowed paper types

`PAPER_SCREENING_ALLOWED_TYPES` is parsed as follows:

1. Preserve whether the raw stripped configuration was empty.
2. Split a nonempty value on the English comma `,`.
3. Apply `strip().casefold()` to every item.
4. Ignore empty items.
5. Preserve the first appearance order of valid nonempty items.
6. Reject duplicate normalized tokens rather than silently de-duplicating.

An empty stripped configuration maps to `allowed_paper_types=None`. A nonempty
configuration that contains only commas and whitespace is invalid.

The only accepted tokens are:

- `journal`;
- `conference`;
- `preprint`;
- `review`; and
- `book_chapter`.

The token `unknown` is not allowed in this list. Unknown classification is
controlled only by `PAPER_SCREENING_UNKNOWN_TYPE`.

### 4.5 Parse timing and ownership

`ResearchConductor.__init__` is the single screening runtime-configuration
binding point. It runs after project Config and Retriever class selection are
available and before `GPTResearcher` issues a research Provider, LLM, scraper,
or compressor request.

The binding order is frozen:

1. First evaluate only the exact `report_source` and `report_type` eligibility
   predicate in Section 3.
2. If the source/type combination is out of scope, do not read or parse
   `PAPER_SCREENING_ENABLED` or any other screening configuration, do not
   construct a `ScreeningPolicy`, and do not execute the candidate-capability
   gate.
3. For an eligible Basic/Web combination, strictly parse
   `PAPER_SCREENING_ENABLED`.
4. If enabled is false, record only the eligible-but-disabled state, do not
   parse the remaining policy configuration, and use the exact old path.
5. If enabled is true, parse and validate every remaining screening key once
   and construct one frozen Milestone 2.2A `ScreeningPolicy`.
6. Only after the complete enabled policy is valid may the conductor evaluate
   the candidate-capability gate.

Consequently, an invalid screening environment must have no initialization or
runtime effect on Quick, Hybrid, Local, Azure, LangChain, Deep, Detailed,
Subtopic, or other out-of-scope modes. Conversely, invalid enabled policy for
an eligible Basic/Web run is fatal even if no configured Retriever is
candidate-capable.

- If enabled is false, no `ScreeningPolicy` is built.
- If enabled is true, the complete policy is validated before capability
  discovery or any external activity.
- Any invalid enabled configuration fails fast during conductor construction,
  before Planning, agent selection, MCP, Provider, LLM, scraper, or compressor
  activity.
- Every eligible web-pass Workspace owned by that conductor receives the same
  frozen policy object.
- Provider workers, `ScreeningWorkspace`, and the 2.2A engine do not read
  environment variables or project Config.

This binding uses only the frozen implementation files. It does not require a
change to `agent.py` or `config.py`.

## 5. Candidate-capability gate

Only after exact entry eligibility, strict enabled parsing, and complete
enabled-policy validation, the conductor checks configured Retriever classes
for the generic callable `search_candidates` capability. Out-of-scope and
eligible-but-disabled runs do not perform this gate.

- The check must not hard-code `ArxivSearch`, `SemanticScholarSearch`, source
  strings, or class-name fragments.
- MCP is not treated as academic merely because it may indirectly reference a
  researcher.
- If no configured Retriever class is candidate-capable, then even when
  `PAPER_SCREENING_ENABLED=true`, the entire web pass uses the existing Web
  path.
- In that case no Workspace is created, no screening ID is allocated, and the
  2.2A engine is not called.
- Ordinary Retriever concurrency, MCP handling, scraping, compression,
  source tracking, and output remain unchanged.

Complete configuration validation still occurs at the frozen constructor
binding point before this gate. The no-academic gate controls pipeline
selection, not whether invalid enabled configuration may be ignored.

## 6. ScreeningWorkspace

Add a run/web-pass-scoped `ScreeningWorkspace` in
`gpt_researcher/screening/workspace.py`.

Its states are:

```text
OPEN -> SCREENED -> FINALIZED
OPEN -> ABORTED
SCREENED -> ABORTED
```

### 6.1 State behavior

- `OPEN` accepts request identity/planning metadata and academic occurrences.
- The Workspace accepts only one Planning request identity and zero or more
  evidence request identities.
- Each request identity has one consistent `planning_only` value.
- Screening may be invoked exactly once and only from `OPEN`.
- Screening atomically snapshots all occurrences as a tuple and calls the
  frozen engine once with the Workspace's frozen policy.
- Successful screening stores the complete `ScreeningResult` and changes state
  to `SCREENED`.
- A screening exception stores no partial result, aborts the Workspace, and
  re-raises the original exception.
- Only `SCREENED` permits reading the `ScreeningResult`, routes, and resolved
  canonical candidates.
- Successful completion of all Phase B requests changes `SCREENED` to
  `FINALIZED`.
- Cancellation or an unhandled pipeline failure while `OPEN` or `SCREENED`
  changes the Workspace to `ABORTED` and clears only its own screening state.
- Invalid repeated or out-of-order `screen`, `finalize`, `abort`, add, or read
  operations fail rather than silently succeeding.
- Finalized or aborted workspaces reject further writes and transitions.

Every eligible web pass creates a fresh Workspace. Consecutive passes cannot
share request metadata, occurrences, routes, or a `ScreeningResult`.

The Workspace stores only:

- the frozen `ScreeningPolicy`;
- retrieval-request identity and its strict `planning_only` metadata;
- the immutable `CandidateOccurrence` tuple and occurrence lookup;
- after screening, the complete `ScreeningResult`;
- the frozen routes; and
- canonical occurrence/candidate lookup required to resolve those routes.

The Workspace must not store or own:

- ordinary URLs;
- ordinary prefetched `raw_content`;
- MCP context or MCP cache;
- scraped data;
- compressed context;
- `visited_urls`; or
- `research_sources`.

### 6.2 Separation from the 2.1 Collector

The Workspace:

- does not subclass or wrap `PaperCandidateCollector`;
- does not read Collector private state or storage;
- does not call Collector `finalize()`, `abort()`, or `snapshot()`;
- does not change Collector state or ownership;
- does not expose candidates through the existing Collector getter; and
- does not alter the 2.1 top-level owner/borrower lifecycle.

The event-loop orchestration writes the same already-materialized candidate
tuple separately to the active Collector and the Workspace.

### 6.3 Existing mutable run state

This milestone does not promise, add, or change cleanup semantics for existing:

- `visited_urls`;
- `research_sources`; or
- MCP result caches.

Those existing behaviors must not be used as Workspace identity or storage.
Workspace `abort()` clears only Workspace-owned screening metadata,
occurrences, lookup state, routes, and result state. It does not clear, mutate,
replace, or roll back an ordinary prepared batch, MCP cache, `visited_urls`, or
`research_sources`.

### 6.4 PreparedEvidenceRequest ownership

`gpt_researcher/skills/researcher.py` defines one private
`PreparedEvidenceRequest`. It is an implementation-private value and is not
exported through a package `__init__`, public API, GPTResearcher getter,
WebSocket, report, or audit surface.

It contains at least:

- `retrieval_request_id`;
- `query`;
- ordinary URLs awaiting existing visited-URL handling and scraping;
- ordinary prefetched `raw_content` under the existing protocol;
- academic occurrence IDs associated with this evidence request; and
- a snapshot of MCP context in its existing protocol.

Phase A returns one `PreparedEvidenceRequest` per evidence request. Phase B
consumes those returned values. `asyncio.gather()` results are retained in the
same preallocated request order used to create the coroutines; orchestration
must not append prepared requests in task-completion order.

The Workspace and a `PreparedEvidenceRequest` relate only through stable
`retrieval_request_id` and `occurrence_id` values. The Workspace never stores
the prepared value or its ordinary/MCP payload.

## 7. Stable request identity

All request identities are allocated before evidence coroutines are created.

The Planning request ID is exactly:

```text
planning:000001
```

After Planning produces the final ordered `sub_queries` list, including any
existing original-query append behavior, evidence request IDs are assigned by
one-based position:

```text
evidence:000001
evidence:000002
...
```

Duplicate query strings remain different retrieval requests and receive
different request IDs. Query text is evidence, not identity.

IDs must not depend on coroutine completion, Provider latency, append timing,
runtime object identity, or Python hash order.

## 8. Stable occurrence identity

Every academic candidate returned by one Retriever request becomes exactly one
`CandidateOccurrence`.

The occurrence digest input is a JSON array in this exact order:

```json
[
  "retrieval_request_id",
  1,
  1,
  "candidate_id"
]
```

The four actual values are:

1. normalized `retrieval_request_id`;
2. the Retriever's one-based position in the configured Retriever class list;
3. the candidate's one-based position in that Provider's materialized tuple;
4. the exact normalized `PaperCandidate.candidate_id`.

Serialization is frozen as:

```python
json.dumps(
    values,
    ensure_ascii=False,
    separators=(",", ":"),
)
```

The UTF-8 bytes of that string are hashed with SHA-256. The ID is:

```text
occurrence:sha256:<64 lowercase hexadecimal characters>
```

Completion order, Workspace append order, time, `hash()`, object address,
locale, and timezone must not affect the result.

For Planning occurrences `planning_only=True`; for evidence occurrences it is
`False`.

## 9. Unified Retriever execution batch

`gpt_researcher/actions/query_processing.py` gains an internal frozen batch
result containing at least:

- `projected_results`;
- `candidates: tuple[PaperCandidate, ...]`; and
- `candidate_capable: bool`.

The internal helper must preserve the Milestone 2.1 thread boundary:

- a candidate-capable Retriever calls `search_candidates()` exactly once and
  never calls `search()` for that request;
- an ordinary Retriever calls `search()` exactly once;
- a returned candidate iterator or generator is materialized exactly once,
  inside the synchronous worker;
- the complete tuple is validated before it becomes visible to event-loop
  orchestration;
- three-key projections are generated from that same tuple;
- no candidate iterable is consumed twice;
- the worker receives or captures neither Collector nor Workspace;
- the worker calls no Collector or Workspace API; and
- cancellation of the awaiting coroutine prevents a later worker return from
  writing either destination.

Existing `execute_retriever_search()` retains its current signature, outward
list return behavior, exact ordinary result behavior, Collector behavior, and
three-key academic projection. It may delegate to the new internal helper, but
existing callers do not receive the internal batch object.

The enabled two-stage orchestration uses the internal batch once, on the event
loop, to add the same candidate tuple to both the active 2.1 Collector and the
current Workspace. It must not issue a second Provider request.

## 10. Phase A — Planning

Planning retains the current first-Retriever-only rule.

- It uses `planning:000001` and `planning_only=True`.
- If the first Retriever is candidate-capable, its candidate tuple is added to
  the active Collector and Workspace after the worker returns.
- Its exact `{title, href, body}` projections continue to be passed to
  `plan_research_outline()`.
- Planning results do not become evidence context and are not sent to the
  scraper or compressor.
- A normal Provider exception from the Planning Retriever is isolated as an
  empty Planning evidence list; outline generation continues with that empty
  list.
- `CancelledError`, invalid runtime configuration, Workspace invariant errors,
  and other orchestration-fatal errors are not converted to empty evidence.

Planning outline generation remains necessary and may use the existing LLM.
The prohibition on LLM calls during Phase A applies only to context compression
or new screening decisions; it does not remove the existing planner LLM.

## 11. Phase A — evidence requests

After Planning, the conductor freezes the final ordered sub-query list,
allocates every evidence request ID, and only then creates concurrent request
coroutines.

For each evidence request:

- configured Retrievers execute in their existing configured order;
- different evidence requests may execute concurrently;
- every academic occurrence uses `planning_only=False`;
- ordinary URLs are preserved in the private `PreparedEvidenceRequest` for
  later scraping;
- ordinary prefetched `raw_content` is preserved in that prepared value for
  Phase B;
- academic candidates and occurrence IDs are added to the Workspace while only
  their IDs are referenced by the prepared value;
- MCP context is snapshotted in the prepared value under its existing protocol
  for later combination;
- no academic landing page is scheduled for scraping;
- BrowserManager is not called;
- ContextManager and ContextCompressor are not called;
- no context is produced yet; and
- no Provider request is repeated.

A normal failure from one Retriever contributes an empty batch for that
Retriever. Other configured Retrievers in that request and all other evidence
requests continue. The failure must not erase candidates or ordinary results
already returned by successful peers.

Every Phase A evidence coroutine returns its private
`PreparedEvidenceRequest`. The result list from `asyncio.gather()` is consumed
in the preassigned evidence-request order. Neither the conductor nor the
Workspace may reorder those values by completion time or append them from
per-task callbacks.

Phase A must retain the current distinctions:

- `raw_content` is prefetched ordinary full content only when the existing
  nonacademic threshold and rules accept it;
- ordinary `body` remains a search summary and does not become prefetched full
  content; and
- candidate-capable academic body is withheld for route resolution rather than
  immediately entering prefetched content.

## 12. Global pre-compression barrier

The barrier runs only after every evidence Phase A coroutine has completed or
contributed its isolated empty result.

It must:

- pass one tuple containing the Planning and every evidence occurrence to
  `screen_paper_occurrences()`;
- pass the Workspace's one frozen `ScreeningPolicy`;
- call the engine exactly once;
- retain the complete `ScreeningResult` in the SCREENED Workspace;
- require the Planning route to be empty;
- exclude groups with no canonical from Phase B;
- exclude noncanonical duplicate occurrences from direct routing;
- route a canonical to a non-Planning request only when that request actually
  retrieved a non-Planning member of the same duplicate group;
- allow the routed canonical occurrence itself to originate from Planning or
  another request when the frozen 2.2A route permits it; and
- ensure a canonical occurrence appears at most once in one request route.

An engine, model, identity, cross-reference, or Workspace invariant exception
is fatal and fail closed:

- abort the Workspace;
- preserve and re-raise the original exception; and
- never fall back to unscreened academic data.

The enabled path must not place this barrier inside a broad exception handler
that converts screening errors into an empty or unscreened successful result.

## 13. Phase B — routed context

For each evidence request, resolve its route against the Workspace occurrence
map using the request and occurrence IDs carried by its private
`PreparedEvidenceRequest`. Each routed canonical candidate becomes exactly:

```python
{
    "url": candidate.href,
    "raw_content": candidate.body,
}
```

Academic body handling is frozen:

- pass `body` exactly as stored;
- do not strip, normalize, summarize, or rewrite it;
- do not apply the ordinary 100-character full-page threshold;
- do not add its landing URL to the scraper input;
- do not include an excluded academic URL in `research_sources`; and
- call existing `add_research_sources()` only for academic sources actually
  routed into Phase B.

Ordinary behavior is frozen:

- ordinary URLs use the existing visited-URL de-duplication;
- the existing random shuffle remains in place;
- BrowserManager receives the remaining ordinary URLs;
- ordinary prefetched `raw_content` keeps the existing greater-than-100
  threshold and priority;
- ordinary `body` remains a snippet and does not bypass scraping; and
- ordinary source tracking remains unchanged.

After ordinary scraping, merge ordinary scraped/prefetched input with that
request's routed academic content and call the existing
`ContextManager.get_similar_content_by_query()`. Existing MCP context is then
combined through the current MCP protocol.

The same canonical appears no more than once in one evidence request, but may
appear independently in multiple evidence requests whose frozen routes contain
it.

## 14. Failure, cancellation, and completion

### 14.1 Recoverable failures

- An exception raised directly by one Provider `search()` or
  `search_candidates()` call is isolated to an empty batch for only that
  Retriever.
- An exception raised while materializing that Provider's returned iterator or
  generator is isolated to an empty batch for only that Retriever.
- A failure validating the Provider batch return contract, including a
  candidate-capable batch containing a non-`PaperCandidate` value, is isolated
  to an empty batch for only that Retriever.
- A normal Planning Provider failure supplies empty search evidence to the
  planner.
- A normal Phase B compressor exception makes only that evidence request's
  context empty; other requests continue under the current isolation behavior.

Recoverable failures do not cause a second request and do not remove successful
peer evidence. Provider isolation applies only to the three Provider boundary
categories above; it must not wrap identity, Workspace, screening, route, or
pipeline orchestration.

Provider-isolation logging may record only:

- a fixed safe Retriever/source identifier selected by orchestration; and
- a fixed safe error-category token distinguishing call, materialization, or
  batch-contract failure.

It must not log `str(exc)`, `repr(exc)`, response body or content, request or
response headers, candidate title/body/abstract, API key, environment value,
URL parameters containing secrets, or a serialized request/response.

### 14.2 Fatal failures

The following are fatal:

- invalid enabled screening configuration;
- request-ID or occurrence-ID generation, collision, or uniqueness failure;
- any Workspace add, state-transition, or invariant failure;
- any exception from `screen_paper_occurrences()`;
- any `ScreeningResult`, route, canonical, or occurrence lookup/validation
  failure;
- any Phase A, Barrier, or Phase B orchestration invariant failure; and
- any other unhandled pipeline exception.

Fatal failures abort the Workspace and re-raise the original exception. They
must not be logged as successful screening and must not expose partial routed
academic content. None may be converted to an empty Provider batch.

### 14.3 Cancellation

`asyncio.CancelledError` is always re-raised unchanged.

- If a Workspace exists in OPEN or SCREENED state, it is aborted.
- Provider call, iterator-materialization, and batch-contract isolation must
  explicitly allow `CancelledError` to escape at every layer.
- No subsequent Phase A add, barrier call, Phase B call, or finalize occurs.
- A synchronous worker that finishes after its awaiting task was cancelled has
  no code path that can write the Workspace or Collector.

### 14.4 Success and repeated passes

- Phase B completes for all evidence requests before Workspace finalization.
- A successful pass changes SCREENED to FINALIZED exactly once.
- A later eligible web pass creates an entirely new OPEN Workspace.
- A new pass contains no prior request metadata, occurrence, route, or result.
- Existing Milestone 2.1 Collector ownership, finalization, abort, and snapshot
  timing remain unchanged.

Milestone 2.2B adds no public `ScreeningResult` getter. Controlled audit or API
exposure requires separate Milestone 2.4 approval.

## 15. Compatibility requirements

The following remain exact:

- external academic Retriever results have only `title`, `href`, and `body`;
- no fourth top-level result key is added;
- `search()` and Quick Search compatibility remain unchanged;
- `BODY_IS_PREFETCHED_CONTENT` remains unchanged;
- disabled and no-academic runs use the exact existing bridge behavior;
- Semantic Scholar API-key handling, journal filtering, endpoint/sort routing,
  timeout, no-retry behavior, and safe logging remain unchanged;
- arXiv request and bounded retry behavior remain unchanged;
- ordinary Retriever output and scraping remain unchanged;
- ContextManager, ContextCompressor, and BrowserManager remain unchanged;
- report generation consumes only the resulting context and is not modified;
  and
- Prompt, frontend, API, WebSocket, registration, and selection logic remain
  unchanged.

## 16. Strict implementation file boundary

After explicit approval, implementation may add only:

- `gpt_researcher/screening/workspace.py`;
- `tests/test_paper_screening_workspace.py`; and
- `tests/test_paper_screening_basic_web_pipeline.py`.

Implementation may modify only:

- `.env.example`;
- `gpt_researcher/config/variables/base.py`;
- `gpt_researcher/config/variables/default.py`;
- `gpt_researcher/actions/query_processing.py`; and
- `gpt_researcher/skills/researcher.py`.

These are the complete eight implementation files. Apart from a separately
approved status-only update to this specification, implementation must not
modify any other file.

Protected files and areas include:

- `gpt_researcher/agent.py`;
- `gpt_researcher/screening/models.py`;
- `gpt_researcher/screening/collection.py`;
- `gpt_researcher/screening/decisions.py`;
- `gpt_researcher/screening/rules.py`;
- all Retriever and Provider implementations;
- ContextManager, ContextCompressor, and BrowserManager;
- Prompt, frontend, report templates, API, and WebSocket code;
- dependency and lock files; and
- every Approved and frozen specification.

If implementation requires any ninth file or any protected method, work must
stop. This Draft must be revised, reviewed, and explicitly approved again.

## 17. Fully mocked test matrix

All integration tests must install fail-fast guards against real Provider,
socket, HTTP, LLM, scraper, and compressor calls except explicitly injected
fakes. Tests must isolate and restore all screening and academic Provider
environment variables.

### 17.1 Configuration

- out-of-scope source/type combinations do not read or parse any screening
  configuration, even when screening environment values are invalid;
- default disabled behavior;
- exact true/false normalization and rejection of every other token;
- empty and valid min/max years;
- invalid numeric syntax, range, and min-greater-than-max;
- exact include/exclude policies;
- allowed-type split, strip, casefold, ignored empty items, and stable order;
- empty allowed types mapping to None;
- comma-only invalid input;
- duplicate and unsupported type rejection;
- `unknown` rejection;
- eligible Web plus enabled=true invalid policy fails before any fake Provider,
  LLM, scraper, or compressor call;
- entry scope is checked before enabled parsing, enabled is checked before full
  policy parsing, and full policy parsing precedes capability discovery;
- disabled creates no policy or Workspace; and
- the same frozen policy object is used for the whole web pass.

### 17.2 Workspace and identity

- every legal state transition;
- illegal repeated or out-of-order operations;
- atomic screen success and failure;
- abort clears and hides only Workspace-owned screening state;
- ordinary URLs, ordinary prefetched content, MCP data, scraped/compressed
  context, visited URLs, and research sources never enter Workspace storage;
- abort does not mutate private prepared batches, MCP cache, visited URLs, or
  research sources;
- finalized/aborted workspaces reject writes;
- fresh state for consecutive passes;
- Planning and evidence request numbering;
- repeated query text receives distinct IDs;
- occurrence SHA-256 uses the exact JSON and UTF-8 algorithm;
- input order and delayed completion do not affect identities;
- duplicate occurrence IDs fail before partial screening; and
- no runtime identity, clock, locale, timezone, or hash seed affects output.

### 17.3 Unified execution

- candidate Retriever calls `search_candidates()` once and not `search()`;
- ordinary Retriever calls `search()` once;
- generator materialization occurs once in the worker;
- candidate validation is atomic;
- Provider call exceptions are isolated to their Retriever batch;
- Provider iterator/generator materialization exceptions are isolated to their
  Retriever batch;
- Provider batch-contract validation failures are isolated to their Retriever
  batch;
- request/occurrence identity and Workspace failures are not Provider-isolated;
- `CancelledError` is never captured by Provider isolation;
- candidate tuple and exact three-key projections come from one result;
- Collector and Workspace receive the same tuple object after thread return;
- worker has no Collector or Workspace parameter or closure capture;
- collector/workspace method thread records show event-loop-only calls;
- cancellation prevents late writes; and
- existing `execute_retriever_search()` signature and return behavior remain
  unchanged.

### 17.4 Phase A

- Planning uses only the first Retriever;
- Planning occurrences use `planning_only=True`;
- Planning projections reach the planner unchanged;
- Planning Provider failure supplies empty evidence and outline continues;
- Planning output never enters evidence context directly;
- all final evidence requests are numbered before task creation;
- evidence requests run concurrently while Retrievers within one request retain
  configured order;
- gathered `PreparedEvidenceRequest` values retain preallocated request order
  even when fake tasks finish in reverse or arbitrary order;
- evidence occurrences use `planning_only=False`;
- private prepared values preserve ordinary URL, ordinary prefetched content,
  academic occurrence IDs, and MCP context separately from Workspace;
- one Provider failure preserves successful peers;
- Phase A makes no BrowserManager, ContextManager, or ContextCompressor call;
  and
- no Provider receives a second request.

### 17.5 Barrier and routing

- barrier occurs only after every Phase A evidence task finishes;
- `screen_paper_occurrences()` is called exactly once;
- the input includes Planning and every evidence occurrence;
- Planning route is empty;
- excluded and no-canonical groups do not enter Phase B;
- noncanonical duplicate bodies do not enter Phase B;
- a request routes only groups it actually retrieved;
- an evidence request may route a Planning or peer canonical only when it has a
  non-Planning member in that group;
- one route contains no repeated canonical;
- one canonical may appear in multiple qualifying request routes;
- screening failure aborts and re-raises without unscreened fallback; and
- occurrence-ID generation/collision, Workspace, ScreeningResult, route, and
  canonical failures are fatal and never become empty Provider batches; and
- broad request-error isolation does not swallow screening or orchestration
  invariant errors.

### 17.6 Phase B

- routed academic body is passed byte-for-byte as the Python string value;
- routed academic landing pages never reach BrowserManager;
- excluded academic URLs are not added to research sources;
- ordinary URLs retain visited-URL filtering and scraper behavior;
- ordinary `raw_content` retains the greater-than-100 threshold;
- ordinary `body` still requires webpage scraping;
- per-request compressor input contains only that request's routed academics
  plus its ordinary inputs;
- each canonical occurs at most once per request;
- ContextManager is called only after the global barrier;
- MCP context combination remains unchanged;
- one compressor exception empties only that request;
- cancellation aborts and propagates; and
- successful completion finalizes the Workspace once.

### 17.7 Bypass and entry guards

- default disabled mode follows the exact old call path;
- explicitly disabled mode follows the exact old call path;
- enabled mode with no candidate-capable Retriever follows the exact old call
  path;
- bypass paths create no Workspace, IDs, or engine call;
- eligible Research, Resource, Outline, and Custom Web reports use two stages;
- Quick, Hybrid, Local, Azure, LangChain, Deep, Detailed, Subtopic, and
  multi-agent paths remain unchanged;
- explicit source URLs remain unchanged while only the complement Web pass is
  screened; and
- capability detection never names arXiv or Semantic Scholar.

### 17.8 Regression and security

- academic projection remains exact `{title, href, body}`;
- `BODY_IS_PREFETCHED_CONTENT` disabled-path behavior remains exact;
- PaperCandidateCollector API, state, ownership, and ordering remain exact;
- Semantic Scholar API key and journal environment isolation remains exact;
- Semantic Scholar relevance/bulk routing, timeout, no retry, 429 handling, and
  safe logging remain exact;
- arXiv timeout/retry and partial-result isolation remain exact;
- synthetic secrets never enter URL, params, logs, traceback, snapshots, or
  screening identifiers; and
- Provider isolation logs contain only fixed source identifiers and fixed error
  category tokens; a synthetic sentinel does not enter logs through exception
  strings, repr, response content, headers, candidate content, or serialized
  requests; and
- no real Provider, network, LLM, scraper, or compressor is invoked.

## 18. Explicit non-goals

Milestone 2.2B does not implement or change:

- Quick Search screening;
- Hybrid, Deep Research, Detailed Report, or Subtopic Report screening;
- Crossref lookup or retraction checking;
- LLM topic relevance or LLM exclusion reasons;
- composite scoring;
- JSON, file, or database audit persistence;
- Prompt, frontend, report, API, or WebSocket display;
- a public `ScreeningResult` getter;
- a third academic Provider;
- PDF or full-text retrieval;
- Retriever registration, selection, or Provider requests;
- existing MCP output protocol;
- cleanup semantics for MCP cache, visited URLs, or research sources; or
- dependency or lock-file changes.

Those concerns require separately reviewed milestones, including the already
identified 2.2C, 2.3, and 2.4 boundaries.

## 19. Acceptance checklist

Approval:

- [x] This specification received explicit approval before implementation
  began.

Configuration and scope:

- [ ] Every configuration key, default, normalization, token, range, and error
  behavior matches Section 4.
- [ ] Enabled configuration is parsed once and fails fast before any Provider,
  LLM, scraper, or compressor request.
- [ ] Exact entry scope is checked before reading any screening configuration,
  so invalid screening environment has no effect on out-of-scope modes.
- [ ] Eligible disabled runs stop after strict enabled parsing; eligible enabled
  runs validate the complete policy before capability discovery.
- [ ] Only the four frozen Basic/Web report types are eligible.
- [ ] Disabled and no-academic paths exactly bypass Workspace, IDs, and engine.
- [ ] Quick, Hybrid, Local, Azure, LangChain, Deep, Detailed, Subtopic, and
  multi-agent behavior remains unchanged.
- [ ] Explicit source URLs remain unchanged and only their complement Web pass
  is screened.

Workspace and identity:

- [ ] Workspace states and every legal and illegal transition are exact.
- [ ] Every web pass owns a fresh isolated Workspace and the same frozen policy
  for that pass.
- [ ] Workspace stores only policy, request/planning metadata, occurrences,
  ScreeningResult, routes, and canonical lookup.
- [ ] Ordinary and MCP prepared data never enters Workspace, and Workspace
  abort changes only Workspace-owned screening state.
- [ ] Workspace neither reads nor changes PaperCandidateCollector state.
- [ ] Request IDs are allocated before concurrency and remain stable for
  repeated query text.
- [ ] Occurrence IDs use the exact frozen JSON/SHA-256 algorithm.
- [ ] Identities and output do not depend on completion order or runtime state.

Execution and Phase A:

- [ ] The internal frozen Retriever batch contains one-pass projections,
  candidates, and capability state.
- [ ] Existing `execute_retriever_search()` signature and return behavior are
  unchanged.
- [ ] Candidate generators are materialized once in the worker and the same
  tuple reaches Collector and Workspace on the event loop.
- [ ] No worker captures, reads, or writes Collector or Workspace.
- [ ] Planning uses only the first Retriever, marks occurrences as Planning,
  and supplies exact projections to the planner.
- [ ] Planning Provider failure continues with empty evidence.
- [ ] All evidence Phase A tasks complete without scraper or compression calls.
- [ ] Private `PreparedEvidenceRequest` values carry ordinary/MCP inputs outside
  Workspace and remain in preallocated request order after `asyncio.gather()`.
- [ ] Provider partial failure preserves every successful peer result.
- [ ] Only Provider call, materialization, and batch-contract errors are
  isolated; identity, Workspace, screening, route, and orchestration errors are
  fatal.
- [ ] Provider isolation never catches `CancelledError` and safe logs reveal no
  exception text, response/request data, candidate content, header, or key.
- [ ] No second Provider request occurs.

Barrier and Phase B:

- [ ] The engine is called exactly once after all Phase A evidence requests.
- [ ] Planning routes are empty and every normal route obeys frozen 2.2A group
  evidence constraints.
- [ ] Excluded, no-canonical, noncanonical duplicate, and planning-only-only
  results do not enter context.
- [ ] Screening failure aborts, re-raises, and never falls back to unscreened
  data.
- [ ] Each request compresses only its routed canonical academic bodies and
  preserved ordinary inputs.
- [ ] Academic body is unchanged and academic landing pages are not scraped.
- [ ] Excluded academic URLs are not recorded as research sources.
- [ ] Ordinary URL, raw-content, body, scraper, compressor, and MCP behavior is
  unchanged.
- [ ] Compressor failure remains request-local and cancellation always aborts
  and propagates.
- [ ] Successful Phase B finalizes the Workspace once.

Compatibility, tests, and boundary:

- [ ] Academic outward results remain exact `{title, href, body}` dictionaries.
- [ ] `BODY_IS_PREFETCHED_CONTENT` and the 2.1 Collector contract remain exact.
- [ ] API key, journal filtering, routing, timeout, no-retry, and safe-log
  regressions pass.
- [ ] Both new fully mocked test files cover the complete frozen matrix.
- [ ] Fail-fast test guards prevent real Provider, network, LLM, scraper, and
  compressor calls.
- [ ] Only the frozen eight implementation files change.
- [ ] No implementation starts while this specification remains Draft.
- [ ] No dependency is installed, no real-network test is run, and no file is
  staged or committed as part of this Draft.
