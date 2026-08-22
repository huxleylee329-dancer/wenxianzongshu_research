# Paper Screening Milestone 2.3A — Run Audit Snapshot

Status: **Approved and frozen**

This revised standalone specification has completed review and received
explicit implementation approval. Implementation is strictly limited to the
frozen five-file boundary in Section 14. Any change to a model, lifecycle,
publication protocol, getter, ordering rule, count formula, security boundary,
or file scope requires implementation to stop immediately, this specification
to return to Draft, and the revision to complete a new review and explicit
approval.

Milestone 2.3A adds a strict, immutable, in-memory audit snapshot for one
successful top-level Basic/Web paper-screening run. It does not change paper
selection, routing, report generation, or any Provider behavior.

## 1. Normative foundations and current evidence

This specification builds on and must not weaken or reinterpret:

- `specs/paper-screening-milestone-2.0-candidate-model.md`;
- `specs/paper-screening-milestone-2.1-run-collection.md`;
- `specs/paper-screening-milestone-2.2a-deterministic-engine.md`;
- `specs/paper-screening-milestone-2.2b-basic-web-pipeline.md`; and
- `specs/paper-screening-milestone-2.2c-topic-relevance-agent.md`.

The current architecture provides the evidence required by this design:

- Milestone 2.0 supplies strict immutable `PaperCandidate` values while
  preserving the exact `{title, href, body}` Retriever projection.
- Milestone 2.1 owns run-scoped `CandidateOccurrence` collection and the
  top-level owner/borrower lifecycle.
- Milestone 2.2A supplies immutable deterministic decisions, duplicate groups,
  routes, fixed reason codes, and stable ordering.
- Milestone 2.2B supplies the eligible Basic/Web Phase A, global deterministic
  barrier, and Phase B pipeline, including requests with no academic
  occurrences and recoverable Provider warnings.
- Milestone 2.2C optionally supplies immutable topic decisions and effective
  routes between the deterministic barrier and Phase B.
- `conduct_research()` is the top-level run boundary. `write_report()` is a
  later operation and is not part of the screening lifecycle.

## 2. Frozen goal

For one top-level Basic/Web `research_report` run in which deterministic paper
screening is actually enabled, produce one strict, immutable, run-end
`PaperScreeningAuditSnapshot` that can be read synchronously through:

```python
GPTResearcher.get_paper_screening_audit()
```

The snapshot must explain, without replaying the pipeline:

- which academic occurrences and duplicate groups were screened;
- the deterministic and optional topic decisions that applied;
- which canonical papers remained screening-included;
- which canonicals were routed to which evidence requests;
- which requests had no academic occurrences or no route;
- which safe Provider-batch warning categories occurred; and
- aggregate counts whose units are explicit.

The snapshot is an inspection surface only. It must not affect screening,
routing, compression, or report output.

## 3. Exact scope and lifecycle endpoint

Milestone 2.3A applies only when all of the following are true:

1. the run is the exact Web source supported by Milestone 2.2B;
2. the report type is exactly `research_report`;
3. the current `GPTResearcher` is the Milestone 2.1
   `PaperCandidateCollector` owner; and
4. deterministic paper screening is actually enabled after the frozen 2.2B
   binding and capability gates.

Source and report type alone are insufficient to establish ownership.

The following never create, receive, or write an audit collector:

- Quick Search;
- Deep Research;
- Detailed Report, including its initial Web/`research_report` borrower;
- Hybrid and Local flows;
- subtopic researchers;
- any other borrower; and
- any source/report-type combination outside the exact Basic/Web scope.

The audit lifecycle ends when `conduct_research()` succeeds. It does not wait
for `write_report()`. A later `write_report()` failure cannot roll back an
already finalized audit. The snapshot does not track and must not claim which
papers were cited in a final report.

## 4. `PaperScreeningAuditCollector` lifecycle

Add an internal run-owned collector with exactly these states:

```text
OPEN -> FINALIZED
OPEN -> ABORTED
```

There are no transitions out of `FINALIZED` or `ABORTED`.

Rules:

- Reuse the existing Milestone 2.1 paper-candidate run-overlap guard. Do not add
  a second overlap guard.
- In the owner path of `GPTResearcher.conduct_research()`, first call the
  existing `_begin_paper_candidate_run()`.
- After that method's overlap guard succeeds, a `conduct_research()`-specific
  path must immediately clear the previous run's audit reference. This clearing
  must occur before screening binding.
- A failed overlap attempt must fail before the old reference is cleared or
  replaced.
- The old snapshot remains unavailable even when the new run has screening
  disabled, screening binding fails, the candidate-capability gate does not
  pass, or no audit collector is ultimately created. Failure or cancellation
  must not restore it.
- Create an `OPEN` audit collector only after screening binding succeeds and
  exact Web source, exact `research_report`, candidate ownership, and actual
  deterministic-screening enablement are all confirmed. Assign and increment
  `run_ordinal` only at this point.
- Every non-overlapping eligible run creates a new collector. No entries,
  warnings, pass identifiers, pending snapshot, or finalized snapshot may be
  reused across runs.
- Borrowers neither clear, receive, create, nor mutate the collector.
- `quick_search()` never clears, creates, replaces, or writes audit state.
- Audit clearing must not be added to the shared
  `_begin_paper_candidate_run()`, because that method is also used by Quick
  Search.
- `OPEN` and `ABORTED` collectors expose no snapshot.
- `add_pass()` accepts only a completely validated immutable pass audit and
  atomically appends it while `OPEN`.
- Any invalid state, duplicate pass identity, or pass invariant failure is
  fatal and must occur before partial collector mutation.
- `abort()` clears any private pending snapshot and makes all audit data
  unavailable through the getter.

## 5. Run and Web-pass identity

Each time an audit collector is created, assign a strict positive integer
`run_ordinal`.

- The ordinal is monotonically increasing within one `GPTResearcher` instance.
- A failed or cancelled run consumes its ordinal; ordinals are never rolled
  back or reused.
- Runs that never create an audit collector consume no audit ordinal.
- Concurrent `GPTResearcher` instances do not share counters or audit state.

Each screened Web pass receives a deterministic preallocated identifier:

```text
web-pass:000001
web-pass:000002
```

Allocation follows planned pass order and never completion time. Milestone
2.3A currently has one Basic/Web pass, but the identifier is present from the
first schema version. An occurrence's audit identity is the composite:

```text
(web_pass_id, occurrence_id)
```

The existing occurrence-ID algorithm is unchanged.

Add a strict immutable compound reference:

```python
class PaperScreeningOccurrenceRef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    web_pass_id: str
    occurrence_id: str
```

Both fields are stripped and reject a blank normalized value. Across passes,
all occurrence lookups use `(web_pass_id, occurrence_id)`, all request lookups
use `(web_pass_id, retrieval_request_id)`, and all group lookups use
`(web_pass_id, group_id)`. A bare local identifier is never a snapshot-wide
map key.

Add a strict immutable Web-pass reference:

```python
class PaperScreeningWebPassRef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    web_pass_order: Annotated[int, Field(gt=0)]
    web_pass_id: str
```

The snapshot stores:

```python
web_passes: tuple[PaperScreeningWebPassRef, ...]
```

`web_pass_order` is one-based and contiguous across the snapshot. Pass IDs are
unique after normalization and follow their preallocated order.

## 6. Pass audit construction order

For each screened Web pass, perform these steps in order:

1. complete Phase A;
2. call `workspace.screen()`;
3. run the topic-relevance barrier if enabled;
4. complete the Phase B gather;
5. finish every potentially failing Phase B result transformation, including
   context filtering and joining, and save the final context return value;
6. while all pass-local evidence remains available, build and fully validate
   an immutable pass audit from:
   - the deterministic `ScreeningResult`;
   - the `TopicScreeningResult`, or `None` when topic relevance is disabled;
   - request identity and query metadata;
   - deterministic and effective routes; and
   - safe Provider warning records;
7. atomically add the validated pass audit to the `OPEN` audit collector;
8. finalize the `ScreeningWorkspace` using its existing state machine; and
9. directly return the already constructed context value.

No potentially failing business conversion may run after Workspace
finalization. Writing the audit after Workspace finalization is forbidden,
because a subsequent audit failure would no longer be able to abort a screened
Workspace through its existing state machine.

Audit construction or collector insertion failure is a fatal invariant error.
The Workspace and top-level owner run abort through their existing lifecycle,
the original exception propagates, and no partial audit is exposed.

The existing recoverable per-request Phase B compressor failure remains a
compression outcome. It must not be invented as a paper-screening exclusion
reason or Provider warning.

## 7. Candidate/audit two-phase publication

At successful owner-run completion, freeze this exact synchronous sequence:

1. the `OPEN` audit collector performs `prepare()`:
   - construct, sort, and validate the final immutable snapshot;
   - store it as a private pending snapshot; and
   - do not publish it;
   - keep the public collector state exactly `OPEN`; and
   - continue to make the getter fail;
2. finalize the existing candidate collector;
3. call audit `commit()`:
   - publish only the already validated pending snapshot;
   - assign that snapshot, clear the pending reference, and switch to
     `FINALIZED`;
   - perform no duplicate state check that intentionally raises;
   - perform only non-failing synchronous state assignment; and
   - do not construct a model, sort, convert a tuple, validate an invariant,
     allocate a new model, execute callbacks, log, or call external code; and
4. clear the active-run state through the existing owner lifecycle.

There must be no `await` between `prepare()`, candidate finalization, and audit
`commit()`.

If audit `prepare()` or candidate finalization fails:

- independently attempt to abort the audit collector if it is still `OPEN`;
- independently attempt to abort the candidate collector if it is still
  `OPEN` according to its existing lifecycle;
- never allow a cleanup or abort exception to replace the original exception;
- clear the active-run state; and
- re-raise the original exception unchanged.

Active-run cleanup is guaranteed by `finally`. An original
`asyncio.CancelledError` is re-raised as the same object. Both collectors'
valid-`OPEN` abort operations must be designed as non-failing synchronous
clears; defensive cleanup still preserves the primary exception if an abort is
unexpectedly unable to complete.

The sequence must never expose one collector as `FINALIZED` while the other
remains `OPEN`. `commit()` is deliberately constrained to be incapable of a
new validation, ordering, allocation, or application-level failure.
The private pending snapshot is not a public `PREPARED` state; the only public
states remain `OPEN`, `FINALIZED`, and `ABORTED`.

## 8. Strict model contract

Every audit model uses Pydantic 2 with:

```python
ConfigDict(
    frozen=True,
    extra="forbid",
    strict=True,
)
```

Every collection field is a strict tuple. A list must not be implicitly
converted. Required identifiers and strings are stripped and reject an empty
normalized value. Strict integer fields reject `bool` and string numerals.
Caller-supplied derived values are validated against their source evidence.

### 8.1 Supporting enums and warning model

`AuditRoutingStatus` has exactly:

```text
excluded
routed
planning_only
```

`AuditExclusionReason` reuses the exact existing deterministic exclusion reason
codes and contains exactly:

```text
duplicate_of_canonical
year_below_min
year_above_max
year_unknown
type_not_allowed
type_unknown
topic_irrelevant
```

It must not contain `included` or introduce a synonym for an existing
duplicate, year, or paper-type reason.

`ProviderWarningCategory` has exactly:

```text
call
materialization
contract
```

`PaperScreeningProviderWarning` contains at least and only the following safe
warning data:

- `retriever_index`: a strict positive integer;
- `source_identifier`: a strict nonblank string; and
- `category`: one fixed `ProviderWarningCategory`.

Derive `source_identifier` using this sole algorithm:

1. set `retriever_type = type(retriever)`;
2. read `retriever_type.__module__` and `retriever_type.__qualname__`;
3. only when both values are strings and both remain nonblank after `strip()`,
   set:

   ```python
   source_identifier = f"{module.strip()}.{qualname.strip()}"
   ```

4. otherwise use exactly `unknown_retriever`; and
5. if reading either class metadata attribute fails, use exactly
   `unknown_retriever`.

Identity derivation must never call or read:

- `str(retriever)` or `repr(retriever)`;
- `str()` or `repr()` on an exception;
- the instance `__dict__`;
- an API key, Provider response, request header, query, prompt, or
  user-configured free-text name.

Retriever indexing is frozen:

- the Planning request's Retriever index is exactly 1;
- an evidence request uses the Retriever's one-based position in the original
  configured/enumerated Retriever order;
- task completion order, success/failure filtering, or an empty result cannot
  change that index; and
- call, materialization, and contract warnings for the same Retriever use the
  same index and source identifier.

Warning ownership is frozen:

- a Planning Retriever warning belongs only to its Planning RequestAudit;
- an evidence Retriever warning belongs only to its originating evidence
  RequestAudit;
- a warning cannot move to another request; and
- a warning creates no paper Entry, Group, or exclusion reason.

Within one request, warnings are ordered by `retriever_index`, then by this
fixed category order:

```text
call
materialization
contract
```

Across the snapshot, warning order is `web_pass_order`, `request_order`,
`retriever_index`, then the same fixed category order.

It never contains an exception, response, request, header, candidate data, or
free-form provider message.

### 8.2 `PaperScreeningAuditEntry`

Each occurrence produces exactly one entry containing at least:

- `audit_order`;
- `web_pass_id`;
- `occurrence_id`;
- `candidate_id`;
- `title`;
- `href`;
- `doi`;
- `source`;
- `source_rank`;
- `published_year`;
- `venue`;
- `classified_type`;
- `duplicate_group_id`;
- `duplicate_key_kind`;
- `canonical_occurrence_id`;
- `is_canonical`;
- `planning_only`;
- `originating_request_id`;
- `routed_request_ids: tuple[str, ...]`;
- `deterministic_included`;
- `deterministic_primary_reason`;
- `deterministic_matched_rules`;
- `topic_verdict | None`;
- `topic_reason_code | None`;
- `topic_rationale | None`;
- `topic_confidence | None`;
- `screening_included`;
- `routed_to_evidence`;
- `routing_status`; and
- `final_exclusion_reasons`.

Invariants:

- `originating_request_id` is the occurrence's one and only retrieval request.
- The originating request resolves within the entry's `web_pass_id`, and the
  entry occurrence appears exactly once in that request's `occurrence_ids`.
- A canonical may have multiple `routed_request_ids`.
- Routed request IDs are derived only from effective-route membership, ordered
  by request audit order, and deduplicated without reordering.
- Every routed request resolves within the entry's `web_pass_id`.
- Only a canonical entry may have nonempty `routed_request_ids`; every
  non-canonical entry has exactly `()`.
- Topic fields are either all `None`, or together mirror one complete valid
  topic decision for the group's canonical.
- `routed_to_evidence` is true exactly when `routed_request_ids` is non-empty.
- `routing_status` is derived from screening inclusion, planning-only status,
  and effective-route membership; callers cannot invent it.
- `audit_order` is strict, positive, snapshot-global, continuous, and preserves
  deterministic occurrence/decision order within each pass.
- The entry never stores `abstract`, `body`, complete Provider response,
  prompt, request metadata beyond the listed safe fields, or any secret.
- Topic rationale is available only through the explicit audit getter; it is
  not logged or persisted by this milestone.

### 8.3 `DuplicateGroupAudit`

Each deterministic duplicate group produces exactly one group audit containing
at least:

- `group_order`;
- `web_pass_id`;
- `group_id`;
- `key_kind`;
- `member_occurrence_ids`;
- `reference_occurrence_id`;
- `canonical_occurrence_id`;
- `canonical_candidate_id`;
- `duplicate_count`;
- `deterministic_included`;
- the complete topic decision, or `None`;
- `screening_included`;
- `routed_request_ids`; and
- `planning_only_group`.

Invariants:

- `group_order` is strict, positive, snapshot-global, and continuous;
- member IDs exactly mirror the deterministic group and contain no normalized
  duplicates;
- every member, reference, and canonical occurrence resolves within the group
  audit's `web_pass_id` using compound lookup keys;
- `duplicate_count == len(member_occurrence_ids) - 1`;
- `planning_only_group` is true only when every member occurrence is planning;
- group, canonical occurrence, candidate, deterministic decision, and topic
  decision IDs must mirror their source objects exactly;
- a topic decision's canonical ID resolves only together with this group
  audit's `web_pass_id` and cannot resolve against another pass;
- routed request IDs use the same derivation and ordering as occurrence audit
  entries; and
- callers cannot supply inconsistent derived values.

### 8.4 `PaperScreeningRequestAudit`

Every Planning and evidence request produces exactly one request audit,
including a request that produced no academic occurrence. It contains at
least:

- `request_order`;
- `web_pass_id`;
- `retrieval_request_id`;
- `planning_only`;
- `retrieval_query`;
- `occurrence_ids`;
- `deterministic_route_present`;
- `deterministic_canonical_occurrence_ids`;
- `effective_route_present`;
- `effective_canonical_occurrence_ids`; and
- `provider_warnings`.

Invariants:

- `request_order` is strict, positive, snapshot-global, and continuous;
- Planning is first; evidence requests follow their preallocated order.
- Occurrence IDs exactly mirror Phase A request membership in deterministic
  occurrence order.
- Route-present fields distinguish an existing route with `()` from a route
  that does not exist.
- When no academic occurrence exists, the audit still exists, no occurrence or
  group is fabricated, both route-present fields are false, and both canonical
  tuples are empty.
- Topic disabled means effective-route fields exactly mirror deterministic
  route presence and contents.
- Topic enabled means effective-route fields exactly mirror
  `TopicScreeningResult.effective_routes`; a missing deterministic route
  remains missing and is not fabricated.
- Provider warnings follow retriever index and fixed-category order and contain
  only the safe warning model above.

### 8.5 `PaperScreeningAuditSummary`

Summary field names state their count unit. It contains at least:

Occurrence counts:

- `total_occurrences`;
- `duplicate_occurrences`;
- `deterministically_included_occurrences`;
- `deterministically_excluded_occurrences`;
- `excluded_by_year_occurrences`;
- `excluded_by_type_occurrences`; and
- `excluded_as_duplicate_occurrences`.

Group counts:

- `total_groups`;
- `groups_with_canonical`;
- `deterministically_included_groups`;
- `topic_relevant_groups`;
- `topic_irrelevant_groups`;
- `topic_uncertain_groups`;
- `screening_included_groups`;
- `routed_groups`; and
- `planning_only_groups`.

Request/warning counts:

- `total_requests`;
- `requests_without_academic_occurrences`; and
- `provider_warning_count`.

All counts are strict non-negative integers derived from validated pass audit
data, never caller-authored totals. The exact formulas are:

- `total_occurrences == len(occurrence_entries)`;
- `duplicate_occurrences == sum(group.duplicate_count for group in
  group_audits)`;
- `deterministically_included_occurrences` is the number of entries whose
  `deterministic_included` is true;
- `deterministically_excluded_occurrences` is the number of entries whose
  `deterministic_included` is false;
- `excluded_by_year_occurrences` is the number of distinct entries whose
  deterministic matched rules contain `year_below_min`, `year_above_max`, or
  `year_unknown`;
- `excluded_by_type_occurrences` is the number of distinct entries whose
  deterministic matched rules contain `type_not_allowed` or `type_unknown`;
- `excluded_as_duplicate_occurrences` is the number of entries whose
  deterministic matched rules contain `duplicate_of_canonical`;
- `total_groups == len(group_audits)`;
- `groups_with_canonical` is the number of groups whose
  `canonical_occurrence_id` is not `None`;
- `deterministically_included_groups == groups_with_canonical`;
- `topic_relevant_groups`, `topic_irrelevant_groups`, and
  `topic_uncertain_groups` count groups with a topic decision of the respective
  verdict;
- `screening_included_groups` counts groups whose `screening_included` is true;
- `routed_groups` counts groups whose canonical occurs in at least one
  effective evidence route;
- `planning_only_groups` counts groups for which every member occurrence is a
  Planning occurrence;
- `total_requests == len(request_audits)`;
- `requests_without_academic_occurrences` counts request audits whose
  `occurrence_ids == ()`; and
- `provider_warning_count` is the sum of all request-audit warning tuple
  lengths.

`duplicate_occurrences` and `excluded_as_duplicate_occurrences` are not
required to be equal. Year, type, duplicate, planning, and inclusion counts may
overlap. The three topic verdict counts are mutually exclusive only among
groups with a topic decision. Planning-only and screening-included may overlap.
No invariant may assume all summary fields can be added to obtain a total. The
summary model validator must recompute every formula from entries, groups, and
requests and reject forged totals.

### 8.6 Pass audit and final snapshot

An internal immutable `PaperScreeningWebPassAudit` is the atomic `add_pass()`
unit. It contains the pass identity, validated policy/topic mode, request
audits, group audits, occurrence entries, included canonical references, and
routed canonical references for exactly one pass. The collector derives the final snapshot
from complete pass units only.

`PaperScreeningAuditSnapshot` contains at least:

- `schema_version: Literal["1"]`;
- `run_ordinal`;
- `web_passes: tuple[PaperScreeningWebPassRef, ...]`;
- `policy: ScreeningPolicy`;
- `topic_relevance_enabled`;
- `summary`;
- `request_audits`;
- `group_audits`;
- `occurrence_entries`;
- `screening_included_canonical_occurrence_refs`; and
- `routed_canonical_occurrence_refs`.

Both top-level canonical fields are strict tuples of
`PaperScreeningOccurrenceRef`; the snapshot must not expose a bare
snapshot-wide occurrence-ID tuple. Included and routed references follow group
order filtered by their respective derived status.

The snapshot does not retain the complete `ScreeningResult`,
`TopicScreeningResult`, `PaperCandidate`, `CandidateOccurrence`, Workspace, or
Provider object graph. Every final tuple follows the ordering in Section 11.
All cross-model validators use compound occurrence, request, and group keys;
they must reject a reference that resolves only by colliding with a local ID in
another pass.

## 9. Screening inclusion and routing semantics

Use the field name `screening_included`. The names `finally_included`,
`included_in_report`, and `cited` are forbidden.

Derive inclusion exactly as follows:

- a deterministically excluded decision: `screening_included=false`;
- a non-canonical duplicate: `screening_included=false`;
- a deterministic canonical with topic relevance disabled: `true`;
- a canonical with topic verdict `relevant`: `true`;
- a canonical with topic verdict `uncertain`: `true`;
- a canonical with topic verdict `irrelevant`: `false`; and
- a planning-only canonical may be `true`, but it is not routed to evidence.

Routing rules:

- ordinary Web and MCP material produces no paper audit entry;
- one canonical routed to multiple requests remains one paper/group result and
  records every effective request ID;
- an existing empty route and an absent route remain distinguishable at the
  request-audit level.

Derive `routing_status` using this sole ordered algorithm:

1. If `screening_included is False`:
   - `routing_status=excluded`;
   - `routed_request_ids == ()`; and
   - `routed_to_evidence is False`.
2. Otherwise, if `screening_included is True` and `routed_request_ids` is
   nonempty:
   - `routing_status=routed`;
   - `routed_to_evidence is True`; and
   - every routed request resolves within the same `web_pass_id`.
3. Otherwise, only when all of the following are true:
   - `screening_included is True`;
   - `routed_request_ids == ()`; and
   - the occurrence's `planning_only is True`;

   derive:

   - `routing_status=planning_only`; and
   - `routed_to_evidence is False`.
4. Every other combination is an invariant-validation failure.

Additional invariants:

- `routed_to_evidence == (routing_status == routed)`;
- every non-canonical occurrence has `screening_included=false` and status
  `excluded`;
- every deterministically excluded occurrence has status `excluded`;
- every topic-irrelevant canonical has status `excluded`;
- a relevant or uncertain evidence canonical has status `routed`;
- a Planning occurrence whose canonical is actually used by an evidence route
  has status `routed`, not `planning_only`;
- only a screening-included, unrouted occurrence whose own `planning_only` is
  true may use status `planning_only`;
- `planning_only` is a routing status, never an exclusion reason;
- status `excluded` requires a nonempty mechanically derived
  `final_exclusion_reasons` tuple; and
- statuses `routed` and `planning_only` require
  `final_exclusion_reasons == ()`.

No `cited_in_final_report` field or equivalent inference may be added.

## 10. Final exclusion reasons

`deterministic_primary_reason` and `deterministic_matched_rules` remain separate
and preserve the exact 2.2A evidence.

`final_exclusion_reasons` is derived as follows:

1. for a deterministically excluded decision, map every deterministic
   `matched_rules` exclusion code into `AuditExclusionReason`, preserving its
   original order;
2. for a deterministically included canonical whose topic verdict is
   `irrelevant`, use only `topic_irrelevant`;
3. topic verdict `relevant` or `uncertain` adds no exclusion reason;
4. preserve first occurrence while removing any duplicate, without sorting;
   and
5. never add a duplicate synonym or any code outside the exact enum.

Additional constraints:

- no normalized duplicate may appear;
- `included` is not an exclusion reason and cannot be mapped;
- `uncertain` is never an exclusion reason;
- Provider failure is never a paper exclusion reason;
- planning-only is a routing status, not an exclusion reason; and
- the tuple contains only reasons that genuinely caused
  `screening_included=false`.

## 11. Deterministic ordering

All audit order is derived from frozen pipeline order:

- `web_pass_order`: snapshot-global, one-based, contiguous preallocated pass
  order;
- `request_order`: snapshot-global, one-based, contiguous; Planning first
  within each pass, then that pass's preallocated evidence order;
- `group_order`: snapshot-global, one-based, contiguous, preserving each pass's
  `ScreeningResult.duplicate_groups` order;
- `audit_order`: snapshot-global, one-based, contiguous, preserving each pass's
  deterministic occurrence/decision order;
- included canonical references: group order filtered by
  `screening_included`;
- routed canonical references: group order filtered by effective evidence
  membership;
- routed request IDs: request audit order with first occurrence retained;
- warnings: pass order, request order, retriever index, then fixed category
  order.

Ordering must not depend on task-completion order, timestamps, object identity,
set/dict incidental order, or Python `hash()`.

Passes are concatenated by `web_pass_order`; public order values never restart
or repeat at a pass boundary. If an internal pass audit needs local positions,
the private names must be `pass_request_order`, `pass_group_order`, and
`pass_occurrence_order`. Final `prepare()` converts those private local
positions into public snapshot-global orders and validates every continuous
range.

## 12. Public getter

Add one synchronous read-only method:

```python
GPTResearcher.get_paper_screening_audit()
```

Behavior:

- before an eligible successful run: raise `RuntimeError` with the fixed message
  below;
- screening disabled or run out of scope: raise the same `RuntimeError`;
- collector `OPEN`: raise the same `RuntimeError`;
- collector `FINALIZED`: return the immutable snapshot;
- collector `ABORTED`: raise the same `RuntimeError`;
- after consecutive successful runs: return only the newest snapshot;
- immediately after a new owner `conduct_research()` run passes the overlap
  guard: the old snapshot is no longer visible;
- if that new run fails or is cancelled: do not restore the old snapshot; and
- never expose a pending or partial snapshot.

The getter does not serialize or automatically publish the snapshot to a
report, API, WebSocket, log, or file.

Every unavailable state uses exactly:

```text
paper screening audit is available only after successful finalization
```

## 13. Security and data minimization

The audit may contain identifiers, title, URL, DOI, venue, retrieval query,
fixed classification and reason codes, fixed safe warning categories, and the
already approved short topic rationale.

It must not directly copy, retain, or log:

- `PaperCandidate.abstract`, formatted academic `body`, or an original
  candidate/result object;
- a full raw LLM response, its JSON wrapper, a Provider response, or an original
  Provider result object;
- Provider request, parameters, headers, or serialized request;
- API keys, environment values, or LLM credentials;
- an LLM system/user prompt, Provider request, or research-topic payload;
- exception objects, `str(exc)`, `repr(exc)`, traceback text, or response body;
- ordinary Web page content, scraped content, MCP context, or compressed
  context; or
- a complete retained source object graph from earlier milestones.

Provider warnings are produced at the existing recoverable Provider boundary
as fixed source/category data. They do not alter the failure-isolation behavior
and do not require another Provider request. Topic rationale may be returned by
the explicit getter but is not written to logs or persistence. The only
permitted LLM-derived text is the `TopicRelevanceDecision.rationale` that has
already passed Milestone 2.2C validation, been stripped, and been limited to at
most 500 Unicode characters. Because that validated rationale may summarize or
quote source material, this specification does not claim that its text can
never overlap text in the abstract; the guarantee is that the audit builder
does not directly copy the raw abstract/body or retain the raw response.

`PaperScreeningRequestAudit.retrieval_query` is explicitly permitted and is not
an LLM prompt for this security rule. It must not be added to logs or
persistence by this milestone.

Milestone 2.3A adds no network, Retriever, LLM, scraper, compressor, or MCP
call. It adds no dependency and reads no new environment variable.

## 14. Frozen five-file implementation boundary

After explicit approval, implementation may add:

- `gpt_researcher/screening/audit.py`;
- `tests/test_paper_screening_audit.py`; and
- `tests/test_paper_screening_audit_pipeline.py`.

It may modify only:

- `gpt_researcher/agent.py`; and
- `gpt_researcher/skills/researcher.py`.

Do not modify `gpt_researcher/screening/__init__.py`; implementation uses direct
module imports. The following remain protected:

- every approved specification;
- candidate, collection, deterministic, Workspace, and topic-relevance models
  and engines;
- all Retrievers and Provider request implementations;
- ContextManager, ContextCompressor, BrowserManager, scraper, and MCP code;
- configuration and `.env.example`;
- Prompt, frontend, report templates, API, and WebSocket code;
- dependency and lock files; and
- every other test and business file.

Any requirement for a sixth implementation file must stop implementation and
trigger a revised Draft and new approval.

## 15. Fully mocked test matrix

All tests use synthetic candidates and fake/mocked pipeline dependencies.
Network, socket, Provider, Retriever, LLM, scraper, compressor, and MCP access
must be fail-fast. The implementation suite must cover at least:

### Model strictness and invariants

- every model is strict, frozen, and extra-forbid;
- tuple fields reject lists;
- strict integers reject booleans and string numerals;
- required normalized identifiers reject blank values;
- derived fields reject caller-forged values;
- occurrence `originating_request_id` is exactly one scalar ID;
- one canonical can have multiple ordered, deduplicated
  `routed_request_ids`;
- occurrence and Web-pass reference models reject blank values, extra fields,
  mutation, and list coercion;
- top-level canonical tuples contain compound occurrence references rather than
  bare IDs;
- cross-pass collisions in occurrence, request, and group local IDs resolve
  only with their `web_pass_id` and never cross-link;
- originating and routed request references resolve within the entry's pass;
- non-canonical entries reject nonempty routed request tuples;
- topic fields are all `None` or a complete valid decision;
- `duplicate_count` is exactly members minus one;
- Planning-only and mixed Planning/evidence groups are distinguished; and
- the snapshot retains no prohibited object graph or sensitive field.

### Requests, routes, warnings, and ordering

- Planning request precedes evidence requests;
- evidence request order follows preallocation rather than completion order;
- Web-pass order and snapshot-global request, group, and audit orders are
  one-based and contiguous;
- public orders do not restart across two synthetic Web passes;
- a request with no academic occurrence still has a request audit;
- no-occurrence requests fabricate no occurrence, group, or route;
- an existing route with `()` differs from an absent route;
- topic-disabled effective route data mirrors deterministic route data;
- topic relevant, irrelevant, and uncertain decisions map correctly;
- a route filtered to `()` remains present;
- canonical membership across multiple routes is retained once per request;
- warning identity uses the complete stripped `module.qualname` when both class
  metadata fields are valid;
- an empty module or qualname uses `unknown_retriever`;
- failure while reading class metadata uses `unknown_retriever`;
- warning identity derivation never calls instance `str()`/`repr()` or reads its
  `__dict__`;
- Planning Retriever index is 1 and evidence indices preserve the original
  one-based configured order;
- reverse concurrent completion cannot change warning indices or order;
- Planning and evidence warnings remain attached to their respective request
  and never cross requests;
- warnings sort by pass, request, retriever index, and exact category order
  `call < materialization < contract`;
- Provider call, materialization, and contract failures produce only safe fixed
  warnings;
- warnings produce no paper Entry, Group, or exclusion reason;
- warning/log output excludes a synthetic exception, raw response wrapper,
  request header, API key, LLM prompt, raw abstract/body sentinel, and
  traceback;
- a synthetic exception or API-key sentinel cannot enter source identity, any
  audit model, or logs;
- a validated rationale sentinel remains visible only through the explicit
  getter, while raw response-wrapper and directly copied abstract/body
  sentinels are absent;
- input/task/hash-seed variation cannot change the frozen snapshot order.

### Inclusion, exclusion, and summary

- deterministic exclusion, non-canonical duplicate, topic relevant,
  irrelevant, uncertain, and topic-disabled inclusion rules;
- Planning-only canonical may be included but is not routed;
- deterministic exclusion, duplicate non-canonical, and topic-irrelevant
  canonical all derive status `excluded`;
- relevant and uncertain evidence canonicals derive status `routed`;
- an unrouted Planning-only canonical derives status `planning_only`;
- a Planning occurrence whose canonical is used by evidence derives status
  `routed`;
- screening-included non-Planning without a route is invalid;
- screening-excluded with a nonempty route is invalid;
- a `routed_to_evidence`/status mismatch is invalid;
- legacy values `not_routed` and `screening_excluded` are rejected;
- final reasons preserve deterministic order and append only
  `topic_irrelevant` when applicable;
- the exact exclusion enum rejects `included` and every unapproved synonym;
- uncertain, Provider failure, and planning-only never become exclusion reasons;
- every occurrence, group, request, warning, and route count has the specified
  unit;
- all occurrence, group, and request summary fields are recomputed and exact;
- `duplicate_occurrences` uses summed group duplicate counts while
  `excluded_as_duplicate_occurrences` uses the matched duplicate rule, and the
  test proves they need not be equal;
- `deterministically_included_groups` exactly equals
  `groups_with_canonical`;
- overlapping year/type/duplicate counts are tested and never assumed additive;
- topic verdict counts are mutually exclusive only inside decided groups; and
- included and routed canonical ID tuples follow group order.

### Collector and owner lifecycle

- initial getter failure;
- disabled and out-of-scope getter failure;
- every unavailable getter state uses the exact frozen RuntimeError message;
- `OPEN`, `FINALIZED`, and `ABORTED` getter behavior;
- run ordinals are positive and monotonic per `GPTResearcher` instance;
- failed runs consume, but do not publish or reuse, an ordinal;
- deterministic preallocated Web-pass IDs;
- pass insertion is atomic and rejects invalid state or duplicate identity;
- audit construction occurs only after Phase B completes;
- Phase B per-request compressor failure is not an exclusion reason;
- audit prepare, candidate finalize, and audit commit have no intervening
  `await`;
- audit commit performs only non-failing pending-snapshot publication;
- `prepare()` leaves the public state `OPEN`, keeps pending private, and leaves
  the getter unavailable;
- candidate-finalize failure leaves no public audit;
- audit-prepare failure does not publish the candidate snapshot early;
- audit build/add failure aborts Workspace and owner run;
- Workspace finalization occurs after audit insertion, and no potentially
  failing context conversion follows it;
- abort cleanup failures cannot replace the original exception and active state
  is cleared in `finally`;
- cancellation preserves the original `CancelledError` and exposes no audit;
- consecutive success returns only the latest snapshot;
- a new run clears the old snapshot only after the overlap guard passes;
- new-run failure never restores the old snapshot;
- disabled, binding-failed, and capability-gate-failed owner
  `conduct_research()` runs also hide the old snapshot without creating a new
  collector;
- overlap fails before clearing or replacing old state;
- Quick Search neither clears nor creates nor replaces audit state;
- borrowers receive no collector and expose no audit;
- Detailed Report's initial Web/`research_report` borrower exposes no audit;
- two concurrent `GPTResearcher` owners share no ordinal, collector, pending
  snapshot, or finalized snapshot; and
- a later `write_report()` failure does not roll back the finalized snapshot.

### Pipeline compatibility

- exact eligible Basic/Web owner gating;
- disabled deterministic screening creates no audit collector;
- topic disabled and enabled runs both publish accurate snapshots;
- no second Retriever, paper Provider, LLM, scraper, compressor, or MCP call is
  introduced by audit construction;
- existing candidate collector, Workspace, deterministic engine, topic barrier,
  effective-route consumption, and Phase B behavior remain unchanged;
- exact `{title, href, body}` Retriever result contract remains unchanged;
- `BODY_IS_PREFETCHED_CONTENT` behavior remains unchanged;
- ordinary URL, ordinary prefetched content, MCP content, research sources,
  and visited URLs remain outside the audit and behave identically;
- Quick, Deep, Detailed, Hybrid, Local, and subtopic flows remain unchanged;
- report content and return values are byte/value equivalent with audit enabled;
- API and WebSocket output are unchanged; and
- all tests execute without real network, LLM, Retriever, scraper, compressor,
  or MCP access.

## 16. Explicit non-goals

Milestone 2.3A does not implement or modify:

- final-report content, citation selection, or report templates;
- API, WebSocket, or frontend exposure;
- file, database, object-store, or JSON audit persistence;
- `cited_in_final_report` tracking or any claim of actual citation;
- Crossref or retraction checks;
- citation-count or composite scoring;
- a new LLM, Retriever, Provider, or second Provider request;
- Quick, Deep, Detailed, Hybrid, Local, or subtopic audit scope;
- screening, deduplication, topic-decision, or routing behavior;
- Provider API configuration, retry, timeout, or security rules;
- Prompt changes; or
- dependencies and lock files.

Persistent/exported audit artifacts, report/API presentation, cited-paper
tracking, Crossref/retraction evidence, and scoring require separately scoped
and approved future milestones.

## 17. Acceptance checklist

- [x] This specification received explicit approval before implementation began.
- [ ] Only the frozen five implementation files changed.
- [ ] All audit models are Pydantic 2 strict, frozen, and extra-forbid.
- [ ] All collection fields are strict tuples and reject list coercion.
- [ ] The audit applies only to eligible top-level Basic/Web owner runs with deterministic screening enabled.
- [ ] Quick, Deep, Detailed, Hybrid, Local, subtopic, and borrower flows create no audit.
- [ ] A Detailed initial Web/`research_report` borrower creates no audit.
- [ ] The existing overlap guard rejects overlap before old audit state is cleared.
- [ ] Every accepted owner `conduct_research()` run immediately makes the old snapshot unavailable before screening binding.
- [ ] Disabled, binding-failed, capability-gate-failed, and no-collector owner runs never restore the old snapshot.
- [ ] Quick Search does not clear, create, replace, or write audit state.
- [ ] Run ordinals are per-instance, positive, monotonic, and consumed by failed runs.
- [ ] Web-pass IDs are preallocated and independent of completion timing.
- [ ] Occurrence audit identity is `(web_pass_id, occurrence_id)` without changing occurrence IDs.
- [ ] Snapshot-wide canonical collections use strict compound `PaperScreeningOccurrenceRef` values and no bare IDs.
- [ ] Web-pass, request, group, and audit orders are snapshot-global, one-based, and contiguous.
- [ ] Cross-model occurrence, request, and group references resolve with compound pass-local keys.
- [ ] Pass audit construction occurs after Phase B and before Workspace finalization.
- [ ] Audit construction and insertion are atomic and expose no partial data.
- [ ] Audit prepare, candidate finalize, and audit commit execute synchronously without an intervening await.
- [ ] Audit commit performs only non-failing publication of a validated pending snapshot.
- [ ] Audit prepare leaves the public state OPEN and the pending snapshot unavailable.
- [ ] Audit-prepare or candidate-finalize failure publishes neither partial audit nor mismatched collector state.
- [ ] Abort cleanup cannot replace the original exception, and active state always clears in finally.
- [ ] Every occurrence has one scalar `originating_request_id`.
- [ ] A canonical records all effective routed request IDs in deterministic order.
- [ ] Originating and routed requests resolve within the entry's Web pass, and non-canonical entries have no routed requests.
- [ ] Requests with no academic occurrences still have request audits and no fabricated route.
- [ ] Existing-empty and absent routes remain distinguishable.
- [ ] Provider warnings use strict one-based indices, the sole trusted `module.qualname` algorithm with `unknown_retriever` fallback, and only call/materialization/contract categories.
- [ ] Planning index is 1, evidence indices preserve original configuration order, and completion order cannot change them.
- [ ] Planning and evidence warnings remain attached to their originating request in the frozen category order.
- [ ] Warning identity never calls instance str/repr, reads instance data, or includes exception text, secrets, requests, queries, prompts, or responses.
- [ ] Provider warnings create no Entry, Group, or exclusion reason.
- [ ] No raw exception, response wrapper, Provider request/header, LLM prompt, directly copied abstract/body, or secret enters the audit or logs.
- [ ] Only a validated, stripped, at-most-500-character topic rationale may be returned as LLM-derived text by the getter.
- [ ] Retrieval query is allowed in RequestAudit but is not newly logged or persisted.
- [ ] Topic fields are all absent or mirror one complete valid topic decision.
- [ ] Topic disabled, relevant, irrelevant, and uncertain inclusion semantics are exact.
- [ ] Planning-only canonicals can be included but are never routed to evidence.
- [ ] Routing status permits only excluded, routed, and planning_only and follows the frozen ordered derivation.
- [ ] Excluded entries have no routes and nonempty final reasons; routed/planning-only entries have empty final reasons.
- [ ] A Planning occurrence routed through evidence uses routed rather than planning_only.
- [ ] Invalid route/status combinations and both removed legacy enum values are rejected.
- [ ] Final exclusion reasons use only the exact seven-code enum, preserve deterministic matched-rule order, and append only `topic_irrelevant` when applicable.
- [ ] Provider failure, topic uncertain, and planning-only are not exclusion reasons.
- [ ] Every Summary field is recomputed with its frozen formula, forged values fail, and overlapping counts are not treated as additive.
- [ ] Request, group, occurrence, canonical, route, and warning order is deterministic.
- [ ] The snapshot retains no full ScreeningResult, TopicScreeningResult, PaperCandidate, or Provider object graph.
- [ ] The getter exposes only the latest finalized immutable snapshot.
- [ ] OPEN, ABORTED, disabled, out-of-scope, and never-run getter calls use the exact frozen RuntimeError message.
- [ ] A failed new run never restores an older snapshot.
- [ ] Candidate and audit collectors complete or abort coherently.
- [ ] A later `write_report()` failure does not roll back a successful run audit.
- [ ] The audit does not claim which papers were cited in the final report.
- [ ] Existing screening, routing, Phase B, three-key result, and prefetched-body behavior remain unchanged.
- [ ] Report, API, WebSocket, and frontend output remain unchanged.
- [ ] No new network, Provider, Retriever, LLM, scraper, compressor, or MCP call is introduced.
- [ ] All automated tests are fully mocked/fake and fail fast on real external access.
- [ ] No dependency, configuration, Prompt, frontend, or persistence file changed.
- [ ] No real network, Provider, LLM, scraper, compressor, or MCP operation was run during implementation verification.
