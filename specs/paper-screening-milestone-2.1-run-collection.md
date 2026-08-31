# Paper Screening Milestone 2.1 — Run-wide Candidate Collection

Status: **Approved and frozen**

This specification completed separate review and received explicit approval
for implementation. Implementation is authorized only within the frozen file
boundary and technical design defined here. Any need to change that boundary or
design requires implementation to stop and the specification to be revised,
reviewed, and explicitly approved again.

Milestone 2.1 builds only the run-wide collection boundary on top of the
approved Milestone 2.0 `PaperCandidate` foundation. It does not enable paper
screening or change report content.

## 1. Background and evidence

Milestone 2.0 introduced immutable structured academic candidates while
preserving the exact external Retriever result contract. The current code has
the following relevant properties:

- `gpt_researcher/screening/models.py` defines the frozen, strict
  `PaperCandidate` model and its exact `to_retriever_result()` projection.
- `gpt_researcher/retrievers/arxiv/arxiv.py` and
  `gpt_researcher/retrievers/semantic_scholar/semantic_scholar.py` each expose
  `search_candidates()`; their existing `search()` methods call it once and
  project each candidate to `{title, href, body}`.
- `gpt_researcher/actions/query_processing.py::get_search_results()` creates a
  Retriever instance and currently invokes `search()` in `asyncio.to_thread()`.
  This path is used by research planning, Quick Search, and Deep Research
  planning.
- `gpt_researcher/skills/researcher.py::ResearchConductor._search_relevant_source_urls()`
  independently creates Retriever instances and invokes `search()` in
  `asyncio.to_thread()` for each normal research sub-query.
- `ResearchConductor._get_context_by_web_search()` runs sub-queries concurrently.
  Each sub-query proceeds to scraping/prefetched content and
  `ContextManager.get_similar_content_by_query()` as soon as that sub-query is
  ready. There is no current run-wide barrier before `ContextCompressor`.
- Quick Search can query one Retriever or query all configured Retrievers
  concurrently. Its all-Retriever path de-duplicates only its outward result
  list by URL.
- Deep Research creates concurrent nested `GPTResearcher` instances.
- Detailed Report creates an initial `GPTResearcher` and separate subtopic
  `GPTResearcher` instances.

The frozen Milestone 2.0 specification remains authoritative for candidate
model behavior:

- `specs/paper-screening-milestone-2.0-candidate-model.md`

The implementation evidence reviewed for this Draft is:

- `gpt_researcher/screening/models.py`
- `gpt_researcher/agent.py`
- `gpt_researcher/actions/query_processing.py`
- `gpt_researcher/skills/researcher.py`
- `gpt_researcher/skills/deep_research.py`
- `backend/report_type/detailed_report/detailed_report.py`
- `gpt_researcher/retrievers/arxiv/arxiv.py`
- `gpt_researcher/retrievers/semantic_scholar/semantic_scholar.py`
- existing Academic Search, PaperCandidate, Quick Search, Deep Research, and
  ResearchConductor tests

## 2. Frozen goal

Milestone 2.1 collects every `PaperCandidate` already returned by an academic
Retriever during one top-level research run and exposes an immutable snapshot
only after that run succeeds.

Milestone 2.1 only collects candidates. It does not execute or introduce:

- DOI or title de-duplication;
- year filtering;
- journal, conference, or preprint filtering;
- retraction checks;
- Crossref requests;
- LLM topic decisions;
- citation, year, or relevance scoring;
- exclusion-reason generation;
- audit-file output;
- Prompt or report changes;
- run-wide filtering before `ContextCompressor`; or
- a two-stage research-flow refactor.

Milestone 2.1 provides a post-run candidate snapshot. It does not claim that
all candidates have been globally assembled before `ContextCompressor`, and it
must not change the current report context or report output. A future Milestone
2.2 that allows screening decisions to control context must separately specify,
review, and approve a two-stage retrieve-then-compress flow.

## 3. Top-level run scope

For Milestone 2.1, a top-level run includes all academic Retriever requests
actually issued by any of these paths:

- normal Basic Research;
- Quick Search in single-Retriever mode;
- Quick Search in all-Retriever mode;
- the top-level Deep Research instance and every nested `GPTResearcher` created
  by that Deep Research run;
- Detailed Report initial research and every subtopic researcher created by
  that Detailed Report run;
- academic searches issued by Hybrid mode; and
- academic Retriever requests issued during research-planning stages.

Planning candidates are included because those papers were actually retrieved
and used as evidence while planning the run. Milestone 2.1 does not judge their
relevance.

Candidates produced by Hybrid passes, repeated planning requests, repeated
queries, or repeated Provider results must remain separate entries. Milestone
2.1 must not de-duplicate them, even when every `PaperCandidate` field is
identical.

The candidate run boundary is the top-level retrieval operation, not report
serialization:

- a directly owned `GPTResearcher.conduct_research()` run finalizes after that
  method succeeds;
- a directly owned `GPTResearcher.quick_search()` run finalizes only after the
  whole Quick Search call, including an optional aggregate summary, succeeds;
- a top-level Deep Research `GPTResearcher.conduct_research()` includes all
  nested Deep Research branches and finalizes when the top-level call succeeds;
- a `DetailedReport` run is owned by the outer `DetailedReport` orchestrator and
  includes its initial and all subtopic research. It finalizes when
  `DetailedReport.run()` succeeds.

Report writing performed later by a normal Basic Research caller does not
reopen or modify an already finalized candidate snapshot.

## 4. `PaperCandidateCollector`

Implementation will add an internal instance-scoped
`PaperCandidateCollector`. It must accept only `PaperCandidate` objects and
must not use module-global or class-level mutable candidate storage.

The collector API must provide at least:

```python
add_batch(candidates)
finalize()
abort()
snapshot() -> tuple[PaperCandidate, ...]
```

### 4.1 States and transitions

The frozen states are:

- `OPEN`: a newly started run can accept candidate batches;
- `FINALIZED`: the successful run has an immutable deterministic snapshot;
- `ABORTED`: the top-level run was cancelled or escaped with an unhandled
  failure, so its incomplete candidates are not consumable.

The frozen transitions and guards are:

| Current state | Operation | Result |
| --- | --- | --- |
| `OPEN` | `add_batch()` | Batch is validated and appended |
| `OPEN` | `finalize()` | Deterministic snapshot is stored; state becomes `FINALIZED` |
| `OPEN` | `abort()` | Mutable candidates are cleared; state becomes `ABORTED` |
| `FINALIZED` | `snapshot()` | Returns the immutable finalized snapshot |
| `OPEN` | `snapshot()` | Fails; an in-progress run must not look complete |
| `ABORTED` | `snapshot()` | Fails; partial candidates must not be exposed |
| `FINALIZED` or `ABORTED` | `add_batch()` | Fails without changing state |
| `FINALIZED` or `ABORTED` | `finalize()` or `abort()` | Fails without changing state |

Invalid state operations must fail deterministically and must not log candidate
content or mutate the stored snapshot.

`add_batch()` must validate the complete input batch before modifying internal
state. Any non-`PaperCandidate` item rejects that batch rather than leaving a
partially appended batch.

`snapshot()` must return a new immutable tuple or an equivalently safe frozen
tuple view. It must never expose the collector's internal mutable list.

The collector necessarily retains the approved in-memory `PaperCandidate`
objects, which already include `body` and `abstract`. It must not separately
log, persist, duplicate into an audit record, or serialize for external output
candidate text, raw Provider payloads, exception content, API keys, request
headers, responses, requests, or tracebacks. The transient stable serialization
used only as an in-memory sort key is not persisted or logged.

### 4.2 Recoverable failures

An existing recoverable failure of one Provider request, one source record, or
one sub-query must not abort the collector. Such a failure contributes an empty
or partial batch while candidates from other successful requests remain.

Only cancellation of the owning top-level run or an unhandled exception that
escapes that top-level run aborts the collector.

### 4.3 Thread boundary

The collector must not be passed as an explicit argument to the synchronous
worker callable submitted to `asyncio.to_thread()`, and that callable's closure
must not capture the collector.

This restriction applies to the worker's executable code path, not to the
entire reachable object graph. The existing `MCPRetriever` may hold a
`researcher` reference that indirectly reaches the collector. That existing
indirect reference is permitted. Neither an MCP worker nor any other Provider
worker may read, call, or modify any collector API.

The synchronous worker returns materialized Retriever results and candidates
to the awaiting coroutine. Only event-loop orchestration may call
`add_batch()`, `finalize()`, or `abort()`: `add_batch()` occurs only after a
successful `await asyncio.to_thread(...)` return, `finalize()` occurs only after
the owning run succeeds, and `abort()` occurs in owner-side cancellation or
unhandled-failure handling.

If the await is cancelled, its continuation must not call `add_batch()`. The
owning event-loop orchestration aborts the collector. A blocking worker may
continue running after cancellation, but there is no worker code path that
calls a collector API, so it cannot perform a late collector write.

Tests must record the executing thread for every collector method call and
prove that `add_batch()`, `finalize()`, and `abort()` are never invoked by the
Provider worker thread. Tests must not require an `MCPRetriever` object graph to
contain no indirect collector reference.

## 5. Ownership, borrowing, and isolation

Collector sharing must use explicit dependency injection. The implementation
must not use `ContextVar`, a module-global variable, class-global candidate
storage, or a Retriever `last_candidates` side channel.

Frozen ownership rules:

- a normal top-level `GPTResearcher.conduct_research()` creates and owns the
  collector for that run;
- a top-level `GPTResearcher.quick_search()` creates and owns the collector for
  that Quick Search run;
- a top-level Deep Research `GPTResearcher` owns the collector, and every
  nested Deep Research `GPTResearcher` explicitly borrows the same collector;
- the outer `DetailedReport` orchestrator owns its collector and explicitly
  injects the same collector into its initial researcher and all subtopic
  researchers;
- a borrower may add batches through the shared execution path but must not
  start, replace, finalize, abort, or clear the collector;
- only the owner starts, finalizes, or aborts its top-level run;
- a successful snapshot remains readable until that owner begins another run;
- beginning another run creates a fresh collector state and does not append to
  the previous run; and
- different top-level tasks and different independently owned `GPTResearcher`
  instances must have completely isolated collectors.

### 5.1 Frozen `GPTResearcher` binding mechanism

There is one canonical private binding mechanism:

```python
GPTResearcher._bind_paper_candidate_collector(
    collector: PaperCandidateCollector | None,
    *,
    owner: bool,
) -> None
```

The `GPTResearcher` constructor accepts these private keyword arguments:

```python
_paper_candidate_collector: PaperCandidateCollector | None = None
_paper_candidate_collector_owner: bool = False
```

The constructor must route an injected collector through
`_bind_paper_candidate_collector()`; it must not implement a second assignment
path. The frozen semantics are:

- when no collector is injected, the instance has no active collector binding;
  when that instance begins a top-level `conduct_research()` or
  `quick_search()`, it creates a new collector and binds it with `owner=True`;
- when a collector is injected, `_paper_candidate_collector_owner` must be
  `False`, and the instance binds it with `owner=False` as a borrower;
- external or nested callers must not inject a collector with owner set to
  `True`; ownership is established only by the top-level begin path or by the
  outer `DetailedReport` owner;
- `owner=False` borrowers may add candidates through the unified execution
  path and may read a finalized snapshot, but they must not begin, replace,
  finalize, abort, or clear the collector;
- Deep Research constructs every nested researcher with the top-level
  collector injected and `_paper_candidate_collector_owner=False`; and
- Detailed Report binds its already-created initial researcher and constructs
  every subtopic researcher with the same collector and borrower ownership.

The same private binding method may be called by `DetailedReport` to replace or
invalidate the binding on its already-existing initial researcher. No direct
assignment to the researcher's private collector attributes is permitted.
Ownership must not be inferred from concrete Retriever or report class names.

The same `GPTResearcher` instance does not support two overlapping top-level
`conduct_research()`/`quick_search()` runs. Detection must occur before an
existing open collector is replaced or cleared, and the second call must fail
fast. Milestone 2.1 does not attempt to make the other existing mutable
`GPTResearcher` fields safe for overlapping use.

### 5.2 Frozen `DetailedReport` lifecycle

`DetailedReport` is the sole owner of the collector spanning its initial and
subtopic research. Its `run()` method must obey this exact lifecycle:

1. Before replacing a collector or mutating run state, check the outer
   run-active flag. If the same `DetailedReport` instance already has an active
   `run()`, fail fast.
2. Mark the outer run active and invalidate the initial researcher's prior
   collector binding through `_bind_paper_candidate_collector(None,
   owner=False)` so a failure during rebinding cannot expose an old snapshot as
   the new run's result.
3. Create a new `OPEN` collector, make it the outer report's current collector,
   and bind it to the already-existing `self.gpt_researcher` with
   `_bind_paper_candidate_collector(new_collector, owner=False)` before initial
   research begins.
4. Construct every subtopic researcher with that same collector injected and
   `_paper_candidate_collector_owner=False`.
5. The initial and subtopic researchers remain borrowers. They cannot finalize,
   abort, clear, or replace the collector.
6. After the complete `DetailedReport.run()` succeeds, the outer owner calls
   `finalize()` exactly once.
7. On `asyncio.CancelledError`, the outer owner calls `abort()` and re-raises
   the cancellation.
8. On any otherwise unhandled `Exception`, the outer owner calls `abort()` and
   re-raises the exception.
9. A `finally` block clears the outer run-active flag.

The rebinding sequence before the first await must be treated as one outer
begin operation. If failure occurs after the initial researcher exists but
before the new binding is complete, the new collector, if created, is aborted;
the initial researcher remains unbound or bound to that aborted collector. It
must not expose the previous run's snapshot or any partial new snapshot.

A second non-overlapping `DetailedReport.run()` creates a new collector and
must not contain candidates from the first run. The outer report's read-only
getter reads only the current run's `FINALIZED` collector. After outer
finalization, `self.gpt_researcher.get_paper_candidates()` reads that same
complete initial-plus-subtopic snapshot.

## 6. Unified single-request Retriever execution

The unified async execution helper will be placed in:

```text
gpt_researcher/actions/query_processing.py
```

This location is frozen because both existing execution entries can depend on
it without introducing a reverse dependency from `screening.collection` into
core orchestration code.

Both of these entries must use the helper:

- `get_search_results()`; and
- `ResearchConductor._search_relevant_source_urls()`.

The helper must preserve the existing optional `max_results` behavior: when no
explicit maximum is supplied, it invokes the selected Retriever method without
inventing a different maximum; when supplied, it passes the approved value.

### 6.1 Candidate-capable Retriever

If the Retriever instance has a callable `search_candidates` attribute:

1. the worker thread calls `search_candidates(...)` exactly once;
2. it does not call `search()`;
3. inside that same worker invocation, it immediately consumes the returned
   iterable exactly once and materializes it as a tuple;
4. the worker validates every materialized item as a `PaperCandidate` and
   returns `tuple[PaperCandidate, ...]`; it must not return a lazy iterator or
   generator across the thread boundary;
5. after `asyncio.to_thread()` returns, the event-loop coroutine atomically adds
   that same tuple to the active collector, when one is present;
6. only after the batch has been added, the event-loop coroutine iterates that
   same tuple to call each candidate's `to_retriever_result()` exactly once for
   projection; and
7. the existing caller receives only those projected result dictionaries.

The original candidate iterable must never be consumed twice. If materializing
or validating the iterable fails, no partial batch is added and no partial
projection is returned. Iterating the already materialized tuple once for
validation and once for projection is permitted; the Provider-returned iterable
itself is consumed only once.

Capability detection must be generic. It must not hard-code `ArxivSearch`,
`SemanticScholarSearch`, source strings, module names, or Retriever registry
names.

### 6.2 Ordinary Retriever

If the instance has no callable `search_candidates`:

1. the worker calls its existing `search(...)` exactly once;
2. no candidate batch is added; and
3. its existing results and failure behavior remain unchanged.

### 6.3 Prohibited execution patterns

The implementation must not:

- call `search()` and then call `search_candidates()`;
- make a second Provider request for collection;
- add a fourth field to Retriever result dictionaries;
- pass the collector as an explicit synchronous-worker argument;
- capture the collector in the callable submitted to `asyncio.to_thread()`;
- call a collector API from MCP or any other Provider worker code;
- return a lazy candidate iterator or generator across the thread boundary;
- consume the Provider-returned candidate iterable more than once;
- modify Retriever registration or selection;
- let Quick Search's outward URL de-duplication remove collector entries; or
- convert ordinary Retriever snippets into `PaperCandidate` objects.

Academic `search()` remains available for external direct callers and continues
to return exact three-key results. Milestone 2.1 changes only the internal
orchestrated execution path so it can retain the candidate objects from the
same single Provider request.

## 7. External compatibility and prefetched content

Every academic Retriever result exposed outside the collector remains exactly:

```python
{
    "title": ...,
    "href": ...,
    "body": ...,
}
```

No `PaperCandidate`, collector identifier, run state, source rank, or Provider
metadata may be added as a fourth result field.

The current `BODY_IS_PREFETCHED_CONTENT` bridge and its priority remain
unchanged:

- existing non-empty `raw_content` keeps its current priority;
- a Retriever whose class declares `BODY_IS_PREFETCHED_CONTENT = True` still
  has a non-blank `body` copied unchanged to prefetched `raw_content`;
- the corresponding academic landing-page URL is not sent to the scraper;
- ordinary Retriever body snippets still follow the ordinary page-fetch path;
  and
- `ContextCompressor`, the scraper manager, and report generation remain
  unchanged.

## 8. Deterministic immutable snapshot

Milestone 2.1 does not add or retain planning ordinals, branch ordinals,
completion timestamps, or coroutine-completion order.

At `finalize()`, every collected candidate, including duplicates, is sorted by
this exact ascending key:

1. `retrieval_query`;
2. `source`;
3. `source_rank`;
4. `candidate_id`; and
5. the stable complete serialization of the `PaperCandidate`.

The stable complete serialization must:

- traverse fields in the declared `PaperCandidate` model-field order;
- use Pydantic JSON-mode values or an equivalent deterministic representation;
- encode datetimes using one deterministic ISO-8601 representation;
- preserve tuple item order;
- avoid `Python hash()`;
- avoid object `repr()`, memory addresses, process identifiers, completion
  times, or other runtime-specific data;
- not modify `PaperCandidate`; and
- be used only as a deterministic in-memory tie-breaker.

Sorting must not perform de-duplication. Completely identical candidates remain
present with their original multiplicity. Because identical values are
observationally indistinguishable, their relative in-memory identity order is
not part of the contract.

## 9. Read-only access

`GPTResearcher` will provide:

```python
get_paper_candidates() -> tuple[PaperCandidate, ...]
```

Frozen behavior:

- after a successful owned or borrowed run has been finalized by its owner,
  the method returns the `FINALIZED` snapshot;
- it returns a new tuple or an equivalently safe immutable tuple snapshot;
- callers cannot mutate the collector through this method;
- while the run is `OPEN`, it fails rather than pretending partial data is a
  complete result;
- after `ABORTED`, it fails rather than returning partial candidates;
- it does not automatically add candidates to reports, API responses,
  WebSocket messages, research logs, JSON logs, or audit files; and
- Milestone 2.1 does not invoke a screening service through this interface.

The initial `GPTResearcher` held by `DetailedReport` borrows the same collector
owned by the outer `DetailedReport`. After the outer owner finalizes, that
researcher's `get_paper_candidates()` reads the complete initial-plus-subtopic
snapshot. `DetailedReport` must also provide an internal read-only delegation
or equivalent owner-side access so its orchestration and tests do not reach
into private collector storage.

## 10. `PaperCandidate` boundary

Milestone 2.1 must not modify:

- `gpt_researcher/screening/models.py`;
- `PaperCandidate` or `ExternalIdentifier` fields or validators;
- the frozen candidate identity algorithm;
- `source_rank` semantics;
- candidate body formatting; or
- Provider field mappings.

`PaperCandidate.source` remains limited to `arxiv` and `semantic_scholar`.
Adding a third academic Provider requires a separate model-expansion
specification and is not part of Milestone 2.1.

The collector must not append Crossref data, inferred fields, secrets, headers,
raw responses, requests, exceptions, or tracebacks to `PaperCandidate`.

## 11. Frozen dependency direction

The permitted import direction is:

```text
screening.models
      ↑
screening.collection
      ↑
actions.query_processing
      ↑
skills.researcher / skills.deep_research / agent

retrievers.arxiv --------------------→ screening.models
retrievers.semantic_scholar ---------→ screening.models

backend DetailedReport → public gpt_researcher package + screening.collection
```

In words:

- `screening.collection` may import `screening.models`;
- the academic Retrievers may continue importing `screening.models`;
- `actions.query_processing` may import `screening.collection` and
  `screening.models` as needed by the unified execution helper;
- `ResearchConductor` may import and call the helper from
  `actions.query_processing`;
- `agent`, Deep Research, and Detailed Report may receive or create collectors
  for run ownership and injection; and
- `screening.collection` must not import `GPTResearcher`, `ResearchConductor`,
  Deep Research, report classes, Retriever classes, Provider clients, or
  network modules.

This preserves a one-way dependency from orchestration to a pure screening
collection primitive. Placing the execution helper in `screening.collection`
is prohibited because it would mix the state model with Retriever execution
and tempt reverse core imports. Placing it in `agent.py` is also prohibited
because `ResearchConductor` is already imported by `agent.py` and a reverse
import would create a cycle.

## 12. Frozen implementation file boundary

After this Draft is explicitly approved, implementation may add only:

- `gpt_researcher/screening/collection.py`
- `tests/test_paper_candidate_collection.py`
- `tests/test_paper_candidate_collection_pipeline.py`

Implementation may modify only:

- `gpt_researcher/screening/__init__.py`
- `gpt_researcher/agent.py`
- `gpt_researcher/actions/query_processing.py`
- `gpt_researcher/skills/researcher.py`
- `gpt_researcher/skills/deep_research.py`
- `backend/report_type/detailed_report/detailed_report.py`

No implementation change is required or permitted in:

- `gpt_researcher/screening/models.py`
- either academic Retriever;
- `ContextCompressor` or context-management code;
- the scraper manager;
- Retriever registration or selection;
- Prompt code;
- frontend code;
- report templates;
- Provider API configuration;
- dependency or lock files; or
- `.env.example`.

The approved implementation may update this specification's status in a
separate approval-only change. Any implementation need outside the listed file
boundary requires work to stop and this Draft to be revised and re-approved.

## 13. Fully mocked test matrix

Implementation tests must cover all of the following without real network,
real LLM, real scraper, or real `ContextCompressor` calls:

### 13.1 Collector unit behavior

- only `PaperCandidate` instances are accepted;
- a mixed invalid batch is rejected atomically;
- `add_batch()`, `finalize()`, `abort()`, and `snapshot()` obey the frozen state
  table;
- writes after `FINALIZED` are rejected;
- writes and reads after `ABORTED` do not expose partial candidates;
- `snapshot()` returns a tuple and does not expose internal mutable state;
- equal candidate batches added in different completion orders produce the
  same snapshot order;
- deterministic ordering covers datetime and nested external identifiers;
- `Python hash()` and object `repr()` are not used as sort keys; and
- completely identical duplicate candidates retain their full multiplicity.

### 13.2 Single-request execution

- a candidate-capable fake Retriever calls `search_candidates()` exactly once
  and never calls `search()`;
- an ordinary fake Retriever calls `search()` exactly once and does not enter
  the candidate path;
- an explicit `max_results` is forwarded unchanged;
- an omitted `max_results` preserves the Retriever method's existing default;
- candidate projection returns exact `{title, href, body}` dictionaries;
- the collector is neither an explicit worker argument nor captured by the
  callable submitted to `asyncio.to_thread()`;
- an MCP-style fake Retriever may indirectly reach the collector through its
  `researcher` object graph, but its worker code never reads or calls the
  collector;
- collector method instrumentation records thread identities and proves that
  `add_batch()`, `finalize()`, and `abort()` execute only in event-loop
  orchestration, never in the Provider worker;
- a fake candidate Retriever returning a generator has that generator consumed
  exactly once inside the worker, returned as `tuple[PaperCandidate, ...]`, and
  produces complete collector and three-key projection results;
- no lazy candidate iterator or generator is consumed after crossing back to
  the event loop;
- arXiv, Semantic Scholar, and a capability-compatible fake academic Retriever
  can all be collected without hard-coded class names; and
- one Retriever failure does not clear batches already returned by other
  requests.

### 13.3 Pipeline and concurrency

- multiple sub-queries completing in different orders still produce a complete
  deterministic candidate snapshot;
- planning-stage academic requests are included;
- Hybrid duplicate candidates are retained unchanged;
- Quick Search single-Retriever mode collects candidates;
- Quick Search all-Retriever mode collects all candidates;
- Quick Search outward URL de-duplication does not remove internal candidates;
- every nested Deep Research researcher receives the identical collector;
- Detailed Report initial and all subtopic researchers receive the identical
  collector owned by the outer report;
- a successful Detailed Report exposes the complete combined snapshot;
- `DetailedReport.run()` checks overlap before replacing collector or run
  state;
- a second overlapping `DetailedReport.run()` fails fast without disturbing the
  active collector;
- each non-overlapping Detailed Report run creates a new `OPEN` collector and
  binds it to the existing initial researcher before research begins;
- the Detailed Report owner alone finalizes on success and aborts on
  `asyncio.CancelledError` or an unhandled `Exception`, then clears its
  run-active flag in `finally`;
- a second non-overlapping Detailed Report run contains no first-run candidates;
- Detailed Report and its initial researcher read the same complete finalized
  snapshot;
- a failure between invalidating the old initial-researcher binding and
  completing the new binding exposes neither the old snapshot nor a partial new
  snapshot;
- consecutive runs on one owner do not mix old candidates into the new run;
- overlapping top-level calls on one `GPTResearcher` fail fast before replacing
  the active collector;
- two independent top-level instances running concurrently never exchange
  candidates;
- top-level cancellation changes the collector to `ABORTED`;
- completion of a cancelled `to_thread()` worker cannot perform a late write;
- a recoverable Provider, record, or sub-query failure does not abort the whole
  run;
- `BODY_IS_PREFETCHED_CONTENT` still copies academic body unchanged into
  prefetched `raw_content`;
- ordinary non-academic Retriever behavior is unchanged; and
- no candidate is automatically emitted through reports, API payloads,
  WebSocket messages, or logs.

### 13.4 Isolation and regression

- all Retriever calls are fully mocked;
- all LLM, scraper, and `ContextCompressor` calls used by pipeline tests are
  fully mocked;
- tests install a fail-fast guard against accidental real network access;
- tests do not depend on host credentials or environment variables;
- existing Quick Search, Deep Research, Detailed Report-adjacent,
  ResearchConductor, Academic Search, and PaperCandidate behavior remains
  unchanged; and
- the existing 156-test Academic Search/PaperCandidate regression set continues
  to pass before newly added Milestone 2.1 tests are counted.

## 14. Explicit non-goals

Milestone 2.1 does not:

- alter the current report lifecycle or report text;
- expose candidates through HTTP or WebSocket APIs;
- persist candidates or create an audit artifact;
- introduce filtering, ranking, scoring, de-duplication, or exclusion reasons;
- change `PaperCandidate`;
- change either Provider request;
- add a Provider request;
- add support for a third academic Provider;
- change `BODY_IS_PREFETCHED_CONTENT`;
- change Retriever selection;
- change Prompt, frontend, report templates, dependencies, or environment
  configuration; or
- build the future global pre-compression screening barrier.

## 15. Deferred work and review questions

The following remain deliberately outside implementation and must not be
silently resolved by expanding Milestone 2.1:

- Milestone 2.2 must define the approved two-stage retrieve-then-screen-then-
  compress flow before screening can control report context.
- A third academic Provider requires a separate expansion of the frozen
  `PaperCandidate.source` type.
- Persistent audit output, screening decisions, Crossref, LLM assessment, and
  scoring require later approved milestones.
- The private constructor arguments and canonical binding method named in
  Section 5.1 are frozen. Other private storage attribute and exception class
  names remain implementation details, but they may not create a second binding
  mechanism or weaken state, ownership, overlap, or snapshot semantics.
- Detailed Report collector rebinding for every call to the same outer object
  must use the Section 5.1 binding method, remain inside the frozen file
  boundary, and obey the Section 5.2 lifecycle without global state.

No circular-import or file-boundary blocker was found during Draft preparation.
If implementation discovers one, work must stop and this Draft must be revised
and approved rather than silently enlarging the boundary.

## 16. Acceptance checklist

Approval:

- [x] This specification received explicit approval before implementation
  began.

Collector and lifecycle:

- [ ] `PaperCandidateCollector` accepts only `PaperCandidate` batches.
- [ ] `OPEN`, `FINALIZED`, and `ABORTED` transitions match this specification.
- [ ] Only finalized runs expose immutable tuple snapshots.
- [ ] Aborted runs clear and hide partial candidates.
- [ ] Finalized and aborted collectors reject further writes.
- [ ] Recoverable Provider, record, and sub-query failures preserve other
  candidates.
- [ ] Top-level cancellation and unhandled failure abort the owned collector.
- [ ] A cancelled worker cannot write after its awaiter is cancelled.
- [ ] Collector method thread records prove that Provider workers never call
  `add_batch()`, `finalize()`, or `abort()`.

Ownership and isolation:

- [ ] Basic Research and Quick Search own isolated run collectors.
- [ ] `GPTResearcher` uses the single frozen constructor-and-binding mechanism
  and correctly distinguishes an unbound future owner from an injected
  `owner=False` borrower.
- [ ] Deep Research nested researchers borrow the top-level collector.
- [ ] Detailed Report owns and shares one collector with initial and subtopic
  researchers.
- [ ] Borrowers cannot perform owner lifecycle transitions.
- [ ] Detailed Report detects overlapping `run()` calls before replacing state.
- [ ] Every non-overlapping Detailed Report run creates and binds a fresh
  collector before initial research.
- [ ] Detailed Report alone finalizes successful runs and aborts cancelled or
  unhandled failed runs, re-raises, and clears run-active state in `finally`.
- [ ] Detailed Report rebinding failures expose neither an old nor partial new
  snapshot.
- [ ] Detailed Report and its initial researcher read the same finalized
  initial-plus-subtopic snapshot.
- [ ] Consecutive runs do not mix candidates.
- [ ] Overlapping runs on one `GPTResearcher` fail fast.
- [ ] Independent concurrent top-level tasks do not leak candidates.

Execution and compatibility:

- [ ] Both existing Retriever execution entries use the unified helper.
- [ ] Candidate-capable Retrievers make one `search_candidates()` call and no
  `search()` call in the orchestrated path.
- [ ] Candidate iterables are consumed exactly once in the worker, validated,
  and returned as `tuple[PaperCandidate, ...]` rather than lazy iterators.
- [ ] The event loop atomically adds the materialized tuple before projecting
  that same tuple to exact three-key results.
- [ ] A generator-returning fake Retriever proves complete one-pass collection
  and projection.
- [ ] Ordinary Retrievers make one existing `search()` call and remain
  unchanged.
- [ ] An MCP worker may indirectly hold a collector through `self.researcher`
  but never reads, invokes, or mutates collector APIs.
- [ ] Collection causes no second Provider request.
- [ ] Capability detection does not hard-code academic Retriever names.
- [ ] Outward academic results remain exact `{title, href, body}` dictionaries.
- [ ] Quick Search outward URL de-duplication does not remove internal
  candidates.
- [ ] Planning and Hybrid academic requests are collected.
- [ ] Duplicate candidates are retained.
- [ ] `BODY_IS_PREFETCHED_CONTENT` behavior and priority remain unchanged.
- [ ] No report, Prompt, frontend, registration, Provider, dependency, or
  environment behavior changes.

Snapshot and read interface:

- [ ] Final snapshot ordering uses the frozen stable content key.
- [ ] Snapshot ordering is independent of concurrency completion order.
- [ ] Stable serialization is deterministic and contains no runtime identity.
- [ ] `GPTResearcher.get_paper_candidates()` exposes only finalized snapshots.
- [ ] Detailed Report can read the complete initial-plus-subtopic snapshot.
- [ ] Candidates are not automatically logged, persisted, reported, or sent by
  API/WebSocket.

Testing and scope:

- [ ] Both new fully mocked test files cover the complete frozen matrix.
- [ ] Tests cannot make real Retriever, LLM, scraper, or compression requests.
- [ ] Existing 156 Academic Search/PaperCandidate tests continue to pass.
- [ ] No file outside the frozen implementation boundary changed.
- [ ] Milestone 2.1 enables no screening, de-duplication, Crossref, LLM
  assessment, scoring, audit output, or core two-stage flow.
