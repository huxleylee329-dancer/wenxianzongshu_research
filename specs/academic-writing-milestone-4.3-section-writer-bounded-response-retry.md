# Academic Writing Milestone 4.3: SectionWriter Bounded Response Retry

Status: **Approved and frozen**

本规范尚未批准。批准前不得修改生产代码或测试代码。

## 1. Goal

Milestone 4.3 adds one bounded, adapter-local retry for a SectionWriter response
that reached the provider successfully but failed the existing strict response
pipeline. It addresses observed, paid-provider outputs such as blank content,
citation-array/marker-order disagreement, and bracket text outside an exact
`[[cite:<source_id>]]` marker.

The retry improves delivery reliability without weakening validation, repairing
model text, changing public APIs, or changing graph/checkpoint semantics.

## 2. Normative baseline and narrow supersession

All approved academic-writing specifications through Milestone 4.2 remain
normative.

This specification supersedes only the Milestone 3.5 statements that every
legal `write_section()` call performs exactly one factory call, constructs one
client, performs exactly one completion, and immediately terminates after the
first `_RESPONSE_FAILURE`.

All other Milestone 3.5 contracts remain unchanged, including:

- the public API, DTO, module `__all__`, state and graph boundaries;
- input restoration, prompt projection and all size/token limits;
- the strict response schema and full-response `model_validate_json()` path;
- content normalization and exact citation-marker validation;
- the three fixed private exception classes and fixed safe texts;
- cancellation identity/args preservation and sensitive-data cleanup; and
- zero fallback, repair, partial output, graph mutation or checkpoint mutation.

## 3. Exact implementation boundary

Implementation may modify exactly these two existing files:

```text
gpt_researcher/workflows/academic_writing/section_writer.py
tests/test_academic_writing_section_writer.py
```

No third file may be added or modified. In particular, no state, Composer,
Reviewer, node, graph, facade, package initializer, dependency, configuration,
backend or frontend file may change.

## 4. Public surface

There is no public-surface change. `section_writer.py.__all__`, every public DTO,
Protocol, constructor and method signature remain byte-for-byte unchanged.

## 5. Exact retry decision

For one legal `write_section(state, section_id)` call:

1. Input preparation and canonical user-message construction execute once.
2. The adapter creates a fresh client and performs the first completion.
3. The existing response pipeline validates the returned object unchanged.
4. If validation returns a valid `WorkflowSectionDraft`, return it immediately.
5. If and only if validation returns the exact private marker
   `_RESPONSE_FAILURE`, perform one retry as specified below.
6. Every other result follows the existing terminal behavior with zero retry.

The adapter must not retry:

- any input or preflight rejection;
- factory construction or completion execution failure;
- `_CONTRACT_FAILURE` or DTO round-trip contract failure;
- `asyncio.CancelledError` from either completion; or
- a valid first response.

## 6. Exact second attempt

The retry:

- calls the same selected `SectionWriterClientFactory` a second time;
- receives a second fresh client;
- makes exactly one second completion call;
- reuses the exact same canonical user-message string;
- uses the existing `_SYSTEM_MESSAGE` followed by the exact suffix below; and
- passes the second raw result through the same unchanged strict response
  pipeline.

Exact suffix, including its leading space:

```text
 Your previous response was invalid. Return a non-empty content string. The citations array must exactly equal the unique source IDs in first-marker order. Do not use [ or ] anywhere except inside an exact [[cite:<source_id>]] marker. Return only the required JSON object.
```

The retry instruction contains no first-response content, parser detail,
dynamic error text, source title, source URL, state field, exception, or other
sensitive value. There is no provider-specific branch.

If the second response is valid, return its independently constructed exact
`WorkflowSectionDraft`. The first response contributes no output data.

If the second response returns `_RESPONSE_FAILURE`, raise the existing
`_SectionWriterResponseError("section writer response invalid")` with null
cause and context. If the second factory/completion fails, preserve the existing
`_SectionWriterExecutionError("section writer execution failed")`; there is no
third call. If the second response produces `_CONTRACT_FAILURE`, preserve the
existing `_SectionWriterContractError("section writer contract invalid")`.

## 7. Bounds and cost semantics

Each section has a hard maximum of:

- two factory calls;
- two fresh client objects; and
- two completion calls.

There is exactly one possible retry. There is no loop, recursive call,
configurable retry count, sleep, delay, jitter, backoff, provider fallback, or
model fallback.

A 12-section `SectionWriterSequence` can therefore cause at most 24
SectionWriter completion calls. CitationReviewer behavior and costs remain
unchanged. A failed Composer retry still restarts from Section 1 under the
existing at-least-once graph contract and may repeat earlier costs.

## 8. Cancellation, isolation and cleanup

Cancellation from either completion propagates by bare raise as the same
exception instance with the same args. It performs no retry after cancellation.

Before every success, fixed failure or cancellation exits the adapter, local
references that can retain the input state, restored state, factory, either
client, canonical user message, either raw response, first parsed content, or a
partial draft must be released under the existing bounded reachability contract.
No raw response or partial artifact may appear in an exception, traceback-owned
local/container/closure, DTO, state, checkpoint, event, log or diagnostic.

Control state crossing an `await` must be exact bounded primitive state. No
iterator, generator, `range`, `enumerate`, `zip`, or response container may be
retained across an `await` to drive the retry.

## 9. Determinism boundary

The decision to retry is deterministic for a given first validation result, but
provider output and cost are not deterministic. This milestone does not claim
exactly-once billing, successful generation, factual correctness, evidence
sufficiency, publication readiness, or that a second completion differs from
the first.

## 10. Tests

Tests remain offline and use injected fakes only. Existing response cases are
updated according to the new two-attempt contract; no historical initial-red or
general-purpose security framework is added.

The test matrix must mechanically prove:

- valid first response: 1 factory/client/completion;
- first response failure then valid: 2 factory/client/completions and success;
- two response failures: 2 calls and the fixed response error;
- first response failure then execution failure: 2 calls and fixed execution
  error, with no third call;
- contract failure, execution failure and cancellation on attempt one: no retry;
- cancellation on attempt two: same instance/args and no third call;
- exact retry system message, exact unchanged user-message identity/value, and
  no first-response text in attempt two;
- fresh-client identity on attempt two;
- all existing strict parser near-miss classifications remain unchanged; and
- sensitive first/second responses, clients and partial artifacts are
  unreachable after every exit.

Focused SectionWriter tests must pass before one final academic-writing
regression run. No test may call a real provider, network, retriever, graph or
checkpoint.

## 11. Non-goals

This milestone does not add or change:

- response repair, JSON extraction, Markdown-fence stripping or parser
  relaxation;
- public retry configuration, telemetry or raw-response diagnostics;
- SectionWriterSequence resume, partial-section persistence or per-section
  checkpointing;
- Reviewer retry, Disposition rules, Composer API or graph behavior;
- provider/model fallback, rate-limit retry or transport retry;
- prompts for TopicPlanner, OutlineWriter or CitationReviewer; or
- FinalEditor, export, UI or human-review resolution.

## 12. Stop conditions

Implementation must stop for a revised and explicitly approved specification if
it requires:

- a third file;
- any public API, DTO, state, event, node, graph or checkpoint change;
- any weakening or repair of the existing strict response pipeline;
- retry after execution failure, contract failure or cancellation;
- more than one retry or any unbounded control flow;
- inclusion of the first raw response or dynamic failure data in attempt two;
- a Reviewer/Composer behavior change beyond the natural extra SectionWriter
  completion cost; or
- a real provider/network call in tests.

## 13. Draft approval checklist

- [ ] Status is Draft and implementation is not authorized.
- [ ] The exact two-file implementation boundary is approved.
- [ ] The narrow supersession of only the Milestone 3.5 single-completion rule is approved.
- [ ] All public APIs, DTOs, exports, state, graph, events and checkpoints remain unchanged.
- [ ] The existing strict response parser and all response gates remain unchanged.
- [ ] Retry occurs if and only if the first validation result is `_RESPONSE_FAILURE`.
- [ ] A valid first response performs exactly one factory/client/completion call.
- [ ] The retry uses the same factory, a fresh client and the identical canonical user message.
- [ ] The exact fixed retry suffix and absence of dynamic/first-response data are approved.
- [ ] The second response uses the same strict parser and contributes the sole successful output.
- [ ] Two response failures preserve the existing fixed response exception.
- [ ] Execution failure, contract failure and cancellation never trigger another attempt.
- [ ] The hard two-factory/two-client/two-completion limit is approved.
- [ ] No loop, backoff, fallback, repair or configurable retry is permitted.
- [ ] Bare cancellation and cleanup/reachability requirements are approved.
- [ ] The at-most-24 SectionWriter completion cost for 12 sections is accepted.
- [ ] All non-goals and stop conditions are approved.
- [ ] The offline focused and final regression strategy is approved.
- [x] This specification received explicit approval before implementation began.

## 14. Implementation acceptance checklist

- [ ] Only the exact two approved files changed.
- [ ] No public signature, DTO, export, state, graph, event or checkpoint changed.
- [ ] Input preparation and canonical user-message construction execute once.
- [ ] A valid first response causes exactly one factory/client/completion call.
- [ ] First `_RESPONSE_FAILURE` causes exactly one second factory and completion call.
- [ ] The second client is fresh and comes from the same selected factory.
- [ ] Both calls receive the identical canonical user-message value.
- [ ] The first system message remains exact.
- [ ] The second system message is `_SYSTEM_MESSAGE` plus the exact frozen suffix.
- [ ] The second message contains no raw first response or dynamic failure data.
- [ ] The unchanged strict response parser validates both results.
- [ ] A valid second result returns only the second independently built draft.
- [ ] Two response failures raise the exact fixed response error with null cause/context.
- [ ] First-attempt execution failure performs zero retry.
- [ ] Second-attempt execution failure performs zero third call and raises the fixed execution error.
- [ ] Contract failure on either attempt performs zero later completion.
- [ ] Cancellation on either attempt preserves exception identity and args by bare raise.
- [ ] No cancellation path starts another attempt.
- [ ] No path exceeds two factory, client or completion calls.
- [ ] No loop, recursion, sleep, backoff, provider/model fallback or repair was added.
- [ ] Strict parser near-miss behavior remains unchanged.
- [ ] First/second raw responses, clients, user message and partial drafts are unreachable after all exits.
- [ ] Existing no-state/no-graph/no-checkpoint mutation assertions remain green.
- [ ] Existing injected-fake isolation remains green.
- [ ] Focused SectionWriter tests pass offline.
- [ ] One final academic-writing regression run passes.
- [ ] No real provider, network, retriever, graph or checkpoint is used by the new tests.
- [ ] `git diff --check` passes and staging is empty.
- [ ] The worktree contains only the two approved implementation-file changes.
- [ ] Implementation remains unstaged and uncommitted before review.
