# Semantic Scholar Reliability / Milestone 1.2 Specification

Status: **Approved and frozen**

This is a standalone reliability amendment for the existing Semantic Scholar retriever. It has been separately reviewed and approved, and implementation is authorized only within the file boundary defined by this specification. It does not alter the Approved and frozen `specs/academic-search-mvp.md`.

## Goal

Improve Semantic Scholar request reliability and API conformance while preserving the Academic Search result contract, metadata body, prefetched-content integration, and provider-failure isolation established by Milestones 1 and 1.1.

Milestone 1.2 is limited to:

- optional Semantic Scholar API-key authentication;
- correct endpoint and sort-parameter selection for the existing sort choices;
- safe handling of rate limits and other provider failures;
- fully mocked automated coverage for the new behavior; and
- one controlled real-request smoke test after automated tests pass.

## Background evidence

- A controlled real arXiv request has succeeded.
- A controlled anonymous Semantic Scholar request returned HTTP 429.
- The Semantic Scholar failure was safely isolated and did not crash the overall research task.
- The current Semantic Scholar implementation sends `sort=relevance` to `/paper/search`.
- The official `/paper/search` endpoint is the relevance-ranked search interface and does not accept a `sort` query parameter.
- Sorting is provided by `/paper/search/bulk`, where the sort value uses `field:order` format.

These observations identify a provider-availability concern and an API-conformance defect. They do not justify retries, new orchestration, or changes to other retrievers.

## Current implementation baseline

- `SemanticScholarSearch.__init__` accepts `relevance`, `citationCount`, and `publicationDate` through `VALID_SORT_CRITERIA`.
- `search()` currently uses `/graph/v1/paper/search` for all three choices and always includes the constructor value as the `sort` query parameter.
- Requests use an explicit 10-second timeout.
- Provider and response failures return `[]` rather than escaping into the research task.
- Accepted results contain exactly `title`, `href`, and `body`.
- `body` uses the shared frozen academic metadata format.
- `BODY_IS_PREFETCHED_CONTENT = True` integrates the returned body with the Milestone 1.1 prefetched-content bridge.

## API-key configuration

Milestone 1.2 introduces one optional environment variable:

```text
SEMANTIC_SCHOLAR_API_KEY
```

The behavior is frozen as follows:

- Normalize the environment value exactly as follows:

  ```python
  raw_key = os.getenv("SEMANTIC_SCHOLAR_API_KEY", "")
  normalized_key = raw_key.strip()
  ```

- Only when `normalized_key` is non-empty, send `x-api-key: <normalized_key>`.
- The transmitted header value must be the normalized value with surrounding whitespace removed; the original `raw_key` must never be sent.
- If the variable is missing, empty, or whitespace-only, omit the `x-api-key` header completely and retain anonymous request behavior.
- The API key must never appear in a URL, query parameter, log message, exception message, test snapshot, committed fixture, or Git-tracked value.
- `.env.example` may receive only an empty `SEMANTIC_SCHOLAR_API_KEY=` placeholder during implementation. No real or example secret value is permitted.
- Tests must use synthetic sentinel values and must prove those values do not appear in logs.

## Endpoint and sort routing

The existing Python constructor and `VALID_SORT_CRITERIA` remain unchanged. Request routing is frozen as:

| Constructor `sort` | Endpoint | Query parameter behavior |
| --- | --- | --- |
| `relevance` | `https://api.semanticscholar.org/graph/v1/paper/search` | Omit `sort` completely |
| `citationCount` | `https://api.semanticscholar.org/graph/v1/paper/search/bulk` | Send `sort=citationCount:desc` |
| `publicationDate` | `https://api.semanticscholar.org/graph/v1/paper/search/bulk` | Send `sort=publicationDate:desc` |

Additional routing rules:

- Query, field selection, result limit, timeout, and result normalization retain their existing per-request meanings.
- No client-side global sorting is introduced.
- No ranking or sorting is performed across providers.
- No fallback from one Semantic Scholar endpoint to another is added.

## Error handling and secret safety

- Keep the existing explicit 10-second request timeout.
- HTTP 400, 403, 429, and 5xx responses must not terminate the research task.
- Connection failures, timeouts, invalid JSON, invalid top-level response shapes, and individual malformed records retain safe failure isolation.
- Failures before any valid result is produced return `[]`; valid records already normalized before an individual-record failure may still be returned.
- An HTTP failure log may include the provider identity, safe failure category, and numeric HTTP status code.
- An HTTP status code may be read only from `response.status_code` or `exc.response.status_code`.
- A `requests.HTTPError` without an associated response must still return `[]` safely. Its status may be logged as `unknown`, and status extraction must not raise a secondary exception.
- HTTP failure logging must not call or interpolate `str(exc)` or `repr(exc)`.
- Logs and exception reporting must not include response bodies, request headers, the API key, or a serialized request containing the key.
- `response.text`, `response.content`, request headers, and serialized request objects must never be written to logs.
- Milestone 1.2 adds no automatic retry. In particular, HTTP 429 must return safely without issuing another provider request.

## Behavior that remains unchanged

- Every accepted result remains a dictionary with exactly `title`, `href`, and `body`.
- No `raw_content`, authentication data, status code, or other top-level result field is added.
- The frozen academic metadata body format and field order remain unchanged.
- `BODY_IS_PREFETCHED_CONTENT = True` remains unchanged.
- `ResearchConductor` and all other core research-flow code remain unchanged.
- Context compression, scraper management, retriever registration, and retriever selection remain unchanged.
- Prompts, frontend code, dependency files, and lock files remain unchanged.
- Milestone 1.2 does not add year filtering, cross-source deduplication, global ranking, cross-provider sorting, or a global result limit.
- arXiv behavior is outside this milestone.

## Implementation file boundary

Implementation may modify only:

- `gpt_researcher/retrievers/semantic_scholar/semantic_scholar.py`
- `tests/test_semantic_scholar_retriever.py`
- `tests/test_semantic_scholar_sort.py`
- `.env.example`

This specification-only turn may add only:

- `specs/semantic-scholar-milestone-1.2.md`

All other files are protected. If implementation cannot be completed within this boundary, work must stop and the Draft must be revised and approved before any additional file is changed.

## Automated test requirements

All automated network interactions must be fully mocked. Automated tests must not contact Semantic Scholar, arXiv, or any other real network service.

Tests must cover at least:

- Default `relevance` uses `/paper/search` and omits the `sort` query parameter entirely.
- `citationCount` uses `/paper/search/bulk` with `sort=citationCount:desc`.
- `publicationDate` uses `/paper/search/bulk` with `sort=publicationDate:desc`.
- A non-empty API key is sent through the `x-api-key` request header.
- A key with surrounding whitespace is sent only after normalization, and the original whitespace-bearing value is never sent.
- A missing API key sends no `x-api-key` header.
- An empty or whitespace-only API key sends no `x-api-key` header.
- HTTP 429 returns `[]`, does not propagate, and issues no retry request.
- Logs for HTTP failures do not contain the API key, request headers, or response body.
- HTTP 400, 403, and representative 5xx responses return safely.
- The existing regression test for `requests.HTTPError` without an associated response remains present and passing.
- Connection failure, timeout, invalid JSON, and invalid response-shape isolation remain covered.
- Existing title, URL, abstract, author, year, venue, DOI, metadata-body, result-limit, timeout, provider-order, and exact result-contract tests remain passing.
- Existing sort-constructor and `VALID_SORT_CRITERIA` behavior remains covered.

Test fixtures and assertions must use synthetic keys and synthetic response bodies. Neither may contain a real credential or require provider availability.

### Test environment isolation

- Automated tests must explicitly set, delete, and restore `SEMANTIC_SCHOLAR_API_KEY`; they must not inherit a real value from a developer machine, CI environment, parent process, or test caller.
- A missing-key test must explicitly remove the variable from the test environment. It must not merely assume the variable is absent.
- Test isolation must restore the caller's original environment after each test, including when an assertion or mocked request fails.
- A real inherited environment value must never enter requests mock call arguments, pytest assertion diffs, `caplog`, tracebacks, or snapshots.
- Tests using a synthetic sentinel key must additionally assert that the sentinel is absent from the request URL, query parameters, logs, and exception output.
- The only permitted location for a synthetic configured key is the mocked request's `x-api-key` header value being directly tested.

## Controlled real-request smoke test

After all automated tests pass, a reviewer or authorized implementer must perform one controlled real Semantic Scholar request outside pytest.

- The smoke test must not be added to automated test collection.
- It must use `SEMANTIC_SCHOLAR_API_KEY` supplied by the local environment.
- It must use the default `sort=relevance` constructor behavior.
- It must use `/paper/search` and omit the `sort` query parameter completely.
- It must send `limit=1`, retain the 10-second timeout, and perform no automatic retry.
- The API key must not be printed, logged, snapshotted, or committed.
- The smoke test must not print request headers or response bodies.
- Reporting may record the selected endpoint, authenticated mode, HTTP status, safe result count, and whether the failure boundary behaved correctly.
- Reporting must not include request headers, response bodies, or credentials.
- Successful smoke-test acceptance requires all of the following:
  - an HTTP 2xx response;
  - a valid top-level response structure;
  - at least one acceptable normalized result; and
  - a result dictionary containing exactly `title`, `href`, and `body`.
- An HTTP 429 in either anonymous or authenticated mode must return `[]` safely and may demonstrate correct failure isolation, but it does not satisfy or complete the Semantic Scholar real-success smoke-test acceptance item.
- `/paper/search/bulk` routing for `citationCount` and `publicationDate` remains mandatory in fully mocked automated tests. A controlled bulk-endpoint smoke test is optional and is not required for Milestone 1.2 acceptance.

## Official references

- [Semantic Scholar API documentation](https://api.semanticscholar.org/api-docs/)
- [Semantic Scholar Academic Graph API](https://www.semanticscholar.org/product/api)

## Acceptance checklist

- [x] This specification received explicit approval before implementation began.
- [ ] `SEMANTIC_SCHOLAR_API_KEY` is optional and `.env.example` contains only an empty placeholder.
- [ ] API-key normalization uses `raw_key = os.getenv("SEMANTIC_SCHOLAR_API_KEY", "")` followed by `normalized_key = raw_key.strip()`.
- [ ] A non-empty normalized key is sent only as `x-api-key`, and the raw whitespace-bearing value is never sent.
- [ ] API keys never enter URLs, parameters, logs, exceptions, snapshots, fixtures, assertion diffs, tracebacks, or Git.
- [ ] Missing, empty, and whitespace-only keys preserve anonymous mode without an `x-api-key` header.
- [ ] Automated tests explicitly isolate and restore `SEMANTIC_SCHOLAR_API_KEY`, including explicit deletion for missing-key cases.
- [ ] `relevance` uses `/paper/search` with no `sort` query parameter.
- [ ] `citationCount` uses `/paper/search/bulk` with `sort=citationCount:desc`.
- [ ] `publicationDate` uses `/paper/search/bulk` with `sort=publicationDate:desc`.
- [ ] The existing constructor and `VALID_SORT_CRITERIA` remain unchanged.
- [ ] The 10-second timeout remains in effect.
- [ ] HTTP 400, 403, 429, 5xx, connection failures, timeouts, invalid JSON, and invalid response shapes return safely without crashing the research task.
- [ ] HTTP 429 does not trigger an automatic retry.
- [ ] HTTP status is read only from `response.status_code` or `exc.response.status_code`; an `HTTPError` without a response safely returns `[]` with optional `unknown` status.
- [ ] Safe HTTP logs contain no exception string or repr, response body or content, request headers, serialized request, or key material.
- [ ] Accepted results still contain exactly `title`, `href`, and `body`.
- [ ] The metadata body format and `BODY_IS_PREFETCHED_CONTENT` remain unchanged.
- [ ] No `ResearchConductor`, core flow, prompt, frontend, registry, dependency, or lock-file change is introduced.
- [ ] No year filter, cross-source deduplication, global ranking, cross-provider sorting, or global result limit is introduced.
- [ ] All automated network tests are fully mocked and the existing Semantic Scholar regression coverage remains passing.
- [ ] A controlled authenticated `/paper/search` request with default relevance, no `sort`, `limit=1`, 10-second timeout, and no retry succeeds outside pytest with HTTP 2xx and at least one exact-contract result.
- [ ] A 429 is reported only as successful failure isolation and is not counted as a successful real-request smoke test.
- [ ] Only the approved implementation files are changed.
