# Semantic Scholar Journal Filter / Milestone 1.3 Specification

Status: **Approved and frozen**

This is a standalone Approved and frozen amendment for the existing Semantic Scholar retriever. It has completed review and received explicit implementation approval while preserving the Approved and frozen Academic Search MVP and Semantic Scholar Reliability specifications. Subsequent implementation is authorized only within the frozen three-file boundary defined below. If implementation requires changing any other file, work must stop immediately and this specification must be revised, reviewed, and explicitly approved again. A real-network smoke test remains unauthorized until an API key is approved and the user provides separate explicit authorization.

## Goal

Add an optional Semantic Scholar journal whitelist so users can restrict a search to these publication venues:

- TGARS;
- JSTARS;
- TAES;
- Remote Sensing; and
- Journal of Radars / 雷达学报.

The configuration accepts only frozen, stable tokens. It does not accept arbitrary journal names, introduce a new retriever, or change the Academic Search result and integration contracts.

`SEMANTIC_SCHOLAR_JOURNALS` filters only requests made by the Semantic Scholar retriever. It does not filter arXiv or any other retriever. When `RETRIEVER=arxiv,semantic_scholar`, a report may therefore still contain arXiv results that do not belong to the configured journal whitelist. A user who requires a strict target-journal-only report must use this runtime configuration:

```text
RETRIEVER=semantic_scholar
```

This is configuration guidance, not a change to retriever registration, selection, orchestration, or this milestone's implementation file boundary.

## Background and motivation

- Milestones 1 and 1.1 established normalized Semantic Scholar results, the exact `title`/`href`/`body` contract, and the prefetched-content bridge.
- Milestone 1.2 established optional API-key authentication, API-conformant relevance and bulk endpoint routing, a 10-second timeout, no automatic retries, and safe provider-failure isolation.
- The current Semantic Scholar implementation searches across all venues because it sends no `venue` request parameter.
- Remote-sensing and radar research often needs a repeatable way to constrain discovery to a small reviewed set of journals.
- Free-form journal configuration would make spelling, aliases, casing, and future compatibility unstable. Milestone 1.3 therefore exposes a fixed token vocabulary and owns its exact mapping to Semantic Scholar venue values.
- Semantic Scholar venue coverage, especially for Journal of Radars and its Chinese aliases, cannot be guaranteed by local mapping alone. Coverage must be measured later with a controlled real-request smoke test after API-key approval and explicit network authorization.

## Relationship to existing frozen specifications

This Draft is additive and does not modify:

- `specs/academic-search-mvp.md`; or
- `specs/semantic-scholar-milestone-1.2.md`.

Milestone 1.2 remains authoritative for API-key normalization, endpoint and sort routing, HTTP safety, timeouts, retry policy, result normalization, and the controlled-request security posture. If a proposed Milestone 1.3 implementation would require changing those frozen behaviors or exceeding this specification's file boundary, implementation must stop and the specification must be revised and explicitly re-approved first.

## Configuration contract

Milestone 1.3 introduces one optional environment variable:

```text
SEMANTIC_SCHOLAR_JOURNALS=
```

The value is an ASCII-comma-separated list of stable tokens. It is not a comma-separated list of arbitrary publication names.

Examples:

```text
SEMANTIC_SCHOLAR_JOURNALS=tgars
SEMANTIC_SCHOLAR_JOURNALS=tgars,jstars,remote_sensing
SEMANTIC_SCHOLAR_JOURNALS=journal_of_radars
```

Configuration rules:

- If the variable is missing, empty, or whitespace-only, omit the `venue` request parameter and preserve the current unrestricted-venue behavior.
- Tokens are case-insensitive after normalization, but only the frozen tokens in this specification are accepted.
- Journal display names and aliases are implementation-owned mapping values, not accepted user input.
- `.env.example` may receive only the empty placeholder `SEMANTIC_SCHOLAR_JOURNALS=` during an approved implementation.
- No default journal whitelist is introduced.
- `SEMANTIC_SCHOLAR_API_KEY` remains independently optional and retains its Milestone 1.2 behavior.
- The whitelist applies only inside `SemanticScholarSearch`; it does not post-filter results from arXiv or another retriever.
- With `RETRIEVER=arxiv,semantic_scholar`, out-of-whitelist arXiv results may still reach the report through the unchanged multi-retriever flow.
- Strict journal-only operation requires `RETRIEVER=semantic_scholar`. This is an existing runtime configuration choice and does not authorize registry, selection, or orchestration changes.

## Frozen token mapping

The mapping and alias order are frozen as follows:

| Stable token | Semantic Scholar `venue` value or values |
| --- | --- |
| `tgars` | `IEEE Transactions on Geoscience and Remote Sensing` |
| `jstars` | `IEEE Journal of Selected Topics in Applied Earth Observations and Remote Sensing` |
| `taes` | `IEEE Transactions on Aerospace and Electronic Systems` |
| `remote_sensing` | `Remote Sensing` |
| `journal_of_radars` | `Journal of Radars`, `雷达学报`, `雷达学报(中英文)` |

`journal_of_radars` must expand to all three candidate venue aliases in the listed order. The aliases account for known English and Chinese naming forms; they do not assert that Semantic Scholar currently indexes every form or returns complete coverage.

## Parsing and expansion algorithm

The implementation behavior is frozen in this order:

1. Read the value with `os.getenv("SEMANTIC_SCHOLAR_JOURNALS", "")`.
2. If the raw value is missing, empty, or whitespace-only, treat journal filtering as unconfigured. Do not send `venue`.
3. Otherwise, split the raw value on the English comma `,` only.
4. Normalize every item with `strip()` followed by `lower()`.
5. Ignore normalized empty items.
6. De-duplicate normalized tokens while preserving the first occurrence order.
7. Classify each remaining token against the fixed mapping table. Do not interpret an unknown token as a journal name or venue alias.
8. Expand valid tokens in first-occurrence token order. For a token with aliases, retain the frozen alias order.
9. De-duplicate expanded venue values while preserving their first occurrence order.
10. Join expanded venue values with a literal comma and send the resulting string as the single Semantic Scholar `venue` query parameter.

Examples of parsing behavior:

| Raw configuration | Normalized tokens | Result |
| --- | --- | --- |
| missing, `""`, or `"   "` | none | Unrestricted request; omit `venue` |
| `" TGARS, jstars, TGARS "` | `tgars`, `jstars` | Two venue values in that order |
| `"remote_sensing,,taes,"` | `remote_sensing`, `taes` | Empty items ignored |
| `"journal_of_radars"` | `journal_of_radars` | Three frozen candidate aliases |
| `"tgars,unsupported"` | one valid, one invalid | Send TGARS venue only and emit a safe warning |
| `"unsupported"` | one invalid | Return `[]`; do not send a request |
| `",,,"` | no non-empty tokens | Return `[]`; do not send a request because the raw configuration was non-blank |

## Fail-closed decision

An explicitly non-blank configuration expresses a request to restrict results. Silently falling back to an unrestricted search would violate that request and could admit papers outside the intended journals.

The decision is therefore frozen as follows:

- Mixed valid and invalid tokens use only the expanded values from valid tokens.
- Mixed configuration emits one safe warning identifying the count of distinct unsupported normalized tokens, without including their raw values.
- Invalid tokens must never be copied into a URL, request parameter, exception message, or log record.
- If the raw configuration is non-blank but expansion produces no valid venue value, return `[]` before calling `requests.get`.
- The all-invalid path emits a safe configuration warning, but the warning contains neither the raw environment value nor any invalid token value.
- A non-blank value containing only commas and whitespace is an explicitly configured but unusable whitelist and also fails closed.
- Configuration errors remain isolated inside the retriever and must not crash the overall research task.

This fail-closed rule applies only to an explicitly non-blank `SEMANTIC_SCHOLAR_JOURNALS` value. Missing, empty, and whitespace-only values intentionally retain the existing unrestricted behavior.

## Semantic Scholar request behavior

The expanded whitelist must use the official Semantic Scholar `venue` request parameter. The same expanded `venue` string is supplied to both endpoint families:

| Constructor `sort` | Endpoint | Sort parameter | Venue behavior |
| --- | --- | --- | --- |
| `relevance` | `https://api.semanticscholar.org/graph/v1/paper/search` | Omit `sort` | Send expanded `venue` when configured |
| `citationCount` | `https://api.semanticscholar.org/graph/v1/paper/search/bulk` | `citationCount:desc` | Send the same expanded `venue` |
| `publicationDate` | `https://api.semanticscholar.org/graph/v1/paper/search/bulk` | `publicationDate:desc` | Send the same expanded `venue` |

The implementation must pass `venue` through the request parameter dictionary and allow `requests` to perform URL encoding. It must not manually concatenate venue values into the request URL.

### Request examples

For `SEMANTIC_SCHOLAR_JOURNALS=tgars,remote_sensing` and the default relevance sort, the conceptual request is:

```python
requests.get(
    "https://api.semanticscholar.org/graph/v1/paper/search",
    params={
        "query": query,
        "limit": max_results,
        "fields": "title,abstract,url,authors,year,venue,externalIds",
        "venue": (
            "IEEE Transactions on Geoscience and Remote Sensing,"
            "Remote Sensing"
        ),
    },
    timeout=10,
)
```

The relevance request contains no `sort` parameter.

For `SEMANTIC_SCHOLAR_JOURNALS=journal_of_radars` and `sort="citationCount"`, the conceptual request is:

```python
requests.get(
    "https://api.semanticscholar.org/graph/v1/paper/search/bulk",
    params={
        "query": query,
        "limit": max_results,
        "fields": "title,abstract,url,authors,year,venue,externalIds",
        "sort": "citationCount:desc",
        "venue": "Journal of Radars,雷达学报,雷达学报(中英文)",
    },
    timeout=10,
)
```

When a normalized API key is configured, Milestone 1.2 adds it only through the `x-api-key` header. Journal filtering must not change or duplicate that logic.

## Behavior that remains unchanged

- `SEMANTIC_SCHOLAR_API_KEY` remains optional and keeps its exact Milestone 1.2 normalization and header behavior.
- The existing constructor parameters and `VALID_SORT_CRITERIA` remain unchanged.
- `relevance`, `citationCount`, and `publicationDate` retain their current endpoint and sort mappings.
- Requests retain the explicit 10-second timeout.
- No automatic retry is added; each search performs at most one provider request.
- `BODY_IS_PREFETCHED_CONTENT = True` remains unchanged.
- Every accepted result remains a dictionary with exactly `title`, `href`, and `body`.
- No `raw_content`, venue-filter metadata, token, or other top-level result field is added.
- The frozen metadata body field order and formatting remain unchanged. The venue printed in a result body continues to come from the provider response.
- A Semantic Scholar failure remains isolated and does not terminate the overall research task.
- `ResearchConductor`, context compression, scraper management, retriever registration, and retriever selection remain unchanged.
- Journal filtering remains local to the Semantic Scholar retriever. arXiv and every other retriever retain their existing unfiltered behavior.
- The existing multi-retriever orchestration remains unchanged, so `RETRIEVER=arxiv,semantic_scholar` may contribute arXiv results outside the Semantic Scholar journal whitelist.
- Users requiring a strict target-journal-only report must select only Semantic Scholar with `RETRIEVER=semantic_scholar`; documenting this runtime choice does not expand the implementation boundary.
- API keys must never appear in URLs, parameters, logs, exception output, assertion diffs, snapshots, fixtures, or Git.

## Safety and logging requirements

- `SEMANTIC_SCHOLAR_API_KEY` may enter only the normalized `x-api-key` request header defined by Milestone 1.2.
- The journal configuration and API key must be isolated independently in automated tests so neither inherits a developer, CI, parent-process, or test-caller value.
- Unsupported tokens are not secrets, but their raw text must not be logged or transmitted. Safe warnings report only a provider-specific configuration category and count.
- Safe warnings must not include the full `SEMANTIC_SCHOLAR_JOURNALS` value, valid or invalid token values, expanded request parameters, URLs containing encoded parameters, request headers, or serialized request objects.
- Existing HTTP handling must continue to avoid exception strings and representations, response text and content, request headers, and serialized request data.
- HTTP status codes may continue to be read and logged only under the safe Milestone 1.2 rules.
- The all-invalid fail-closed decision must occur before request construction can expose configuration through a mock call, traceback, or provider request.
- No test or implementation artifact may contain a real API key.

## Implementation file boundary

Implementation may modify only:

- `.env.example`
- `gpt_researcher/retrievers/semantic_scholar/semantic_scholar.py`
- `tests/test_semantic_scholar_venues.py`

The approved implementation may add only this placeholder to `.env.example`:

```text
SEMANTIC_SCHOLAR_JOURNALS=
```

This specification is now frozen. Implementation must not modify it or exceed the boundary above. Any required change to the specification or file boundary must be separately revised, reviewed, and explicitly approved before implementation continues.

All other files remain protected, including:

- `.env`;
- `specs/academic-search-mvp.md`;
- `specs/semantic-scholar-milestone-1.2.md`;
- `gpt_researcher/retrievers/academic_utils.py`;
- the arXiv retriever;
- `ResearchConductor` and all other core research-flow code;
- retriever registry and selection code;
- prompts and frontend code;
- dependency and lock files; and
- all existing tests outside the new venue test file.

If implementation cannot be completed within the three-file boundary, work must stop. This Draft must then be revised, reviewed, and explicitly approved before another file is changed.

## Fully mocked automated test matrix

Every `requests.get` interaction must be fully mocked. Automated tests must not contact Semantic Scholar, arXiv, a publisher, Crossref, or any other network service.

| Area | Required coverage |
| --- | --- |
| Environment isolation | Explicitly delete, set, and automatically restore `SEMANTIC_SCHOLAR_JOURNALS`; independently delete, set, and restore `SEMANTIC_SCHOLAR_API_KEY`; never inherit host values |
| Unconfigured behavior | Missing, empty, and whitespace-only journal values omit `venue` and preserve the current request path |
| Normalization | Case-insensitive tokens, surrounding whitespace, empty comma items, duplicates, and first-occurrence order |
| Fixed mapping | Exact assertion for every frozen token and all three ordered `journal_of_radars` aliases |
| Relevance endpoint | `/paper/search` receives the expanded `venue` parameter and no `sort` parameter |
| Bulk endpoints | Both `citationCount` and `publicationDate` use `/paper/search/bulk`, retain their `:desc` sort value, and receive the same expanded `venue` value as relevance |
| Mixed configuration | Valid venues are sent, unsupported tokens are omitted, and one safe count-only warning is recorded |
| All invalid | Returns `[]`, emits a safe warning, and proves `requests.get` was not called |
| Separator-only value | A non-blank comma/whitespace-only value fails closed without a request |
| Invalid-token safety | A synthetic unsupported sentinel is absent from URL, params, headers, logs, exception output, traceback text, and assertion/snapshot output |
| API-key regression | Missing/blank keys remain anonymous; a normalized synthetic key appears only in `x-api-key`; the key remains absent from URL, params, logs, and exceptions |
| HTTP regression | Safe 400, 403, 429, representative 5xx, timeout, connection, invalid-JSON, invalid-shape, and response-less `HTTPError` behavior remains intact |
| Request count | Valid configuration produces one request; 429 and other failures produce no retry; all-invalid configuration produces zero requests |
| Timeout | Every provider request keeps `timeout=10` |
| Result regression | Accepted results still contain exactly `title`, `href`, and `body`; metadata body formatting and prefetched-content capability remain unchanged |

Test requirements:

- Use only synthetic journal tokens, synthetic API keys, synthetic payloads, and mocked responses.
- Missing-variable tests must explicitly remove the corresponding variable rather than assume it is absent.
- Environment changes must be restored even when an assertion or mocked request fails.
- Tests for an unsupported sentinel must assert that the sentinel never enters the mocked request URL or any request parameter.
- Tests must not encode a real network fallback or depend on test execution order.
- Existing API-key header, safe HTTP error, 10-second timeout, single-request, sort-routing, metadata, and exact-contract behavior must not regress.

## Controlled real-request smoke-test plan

No real request is authorized by this Draft or by the later implementation turn. A controlled smoke test may occur only after all of these preconditions are satisfied:

- the specification is approved and frozen;
- the approved implementation and fully mocked automated tests pass review;
- `SEMANTIC_SCHOLAR_API_KEY` is approved and supplied through the user's local environment;
- the user explicitly authorizes real Semantic Scholar network access for a separate turn; and
- the operator confirms that no terminal command will print the key, request headers, or complete environment variables.

When authorized:

- Test each stable token with at most one small-result request.
- Run requests serially and respect the API key's documented rate limit.
- Use no automatic retry.
- Do not print or record the API key, request headers, complete environment values, response bodies, or serialized requests.
- Record only the token under test, endpoint mode, safe HTTP status, normalized result count, returned provider `venue` values, whether a DOI is present, and the two independent result statuses defined below.
- The final smoke-test report must list transport/routing status and venue coverage status separately for every tested token. Neither status may be inferred from or substituted for the other.

### Smoke-test result dimensions

#### 1. Transport/routing success

Transport/routing is successful only when all of the following are true:

- the provider returns HTTP 2xx;
- the response has a valid top-level structure;
- the frozen `venue` parameter for the token is sent to the expected relevance or bulk endpoint with the correct sort behavior;
- no exception escapes the retriever failure boundary; and
- no API key, request header, complete environment value, response body, or other sensitive information is exposed.

A zero-result response may still satisfy transport/routing success when all of these conditions hold. This dimension establishes that the request reached the correct route with the correct journal configuration; it does not establish provider coverage.

#### 2. Venue coverage success

Venue coverage is successful only when both of the following are true:

- at least one normalized result satisfies the exact `title`, `href`, and `body` contract; and
- the returned provider `venue` can be attributed to one of the frozen candidate journal names for the token under test.

For any token, zero results alone cannot prove an implementation defect. Record zero results as `coverage inconclusive` when the available evidence cannot establish provider coverage, or as a `provider coverage gap` when the provider demonstrably lacks usable results for the tested mapping. `journal_of_radars` remains a specially identified high-risk coverage gap because its English and Chinese aliases may have incomplete Semantic Scholar indexing.

Milestone 1.3 automated code acceptance must not depend on a real provider returning any result. Fully mocked tests establish parsing, routing, request parameters, fail-closed behavior, safety, and result normalization independently of live provider coverage.

HTTP 429, 401, and 403 are not successful smoke outcomes for either dimension. They retain the stopping behavior below and may be reported only as safely isolated provider or authorization failures.

### Smoke-test stopping conditions

Stop the remaining real requests immediately if any of the following occurs:

- HTTP 429, so the test does not continue applying rate pressure;
- HTTP 401 or 403, indicating an authentication or authorization issue;
- repeated provider/server failure or an unexpected response shape;
- any exception escapes the retriever's failure boundary;
- any request would exceed the one-request-per-token limit;
- any evidence suggests that a key, request header, complete environment value, or response body could be printed or recorded; or
- the user's authorization or approved API-key availability is withdrawn.

Do not broaden the smoke test to bulk harvesting, retries, alternate services, or publisher scraping.

## Explicit non-goals

Milestone 1.3 does not implement:

- an IEEE Xplore retriever;
- an MDPI retriever;
- a Journal of Radars / 雷达学报 website scraper;
- Crossref queries or enrichment;
- full-text or PDF downloading and parsing;
- a frontend journal selector;
- prompt changes;
- arbitrary journal-name configuration;
- cross-source deduplication;
- global ranking or cross-provider sorting;
- year filtering;
- a global result limit;
- new retries, fallback endpoints, caching, or orchestration;
- dependency or lock-file changes;
- changes to any retriever other than Semantic Scholar;
- filtering, suppressing, or post-processing arXiv or other retriever results;
- guaranteeing that `RETRIEVER=arxiv,semantic_scholar` produces a report containing only target journals; or
- changes to retriever registration, selection, or orchestration to enforce report-wide journal filtering.

Using `RETRIEVER=semantic_scholar` for strict target-journal-only operation is runtime configuration guidance only and is not implementation scope for Milestone 1.3.

## Official reference

- [Semantic Scholar API documentation](https://api.semanticscholar.org/api-docs/)

## Deferred coverage adapters

If controlled smoke testing shows incomplete or absent coverage for a frozen venue, a later independently specified milestone may evaluate:

- Crossref as a metadata or discovery adapter under an explicit query, rate-limit, matching, and conflict policy;
- IEEE Xplore or publisher APIs for IEEE journal coverage;
- MDPI's supported interfaces for Remote Sensing coverage; or
- the official Journal of Radars / 雷达学报 site or publisher interface.

These are recommendations for future investigation only. They are not fallback paths, implementation authorization, or acceptance requirements for Milestone 1.3.

## Acceptance checklist

- [x] This specification received explicit approval before implementation began.
- [ ] `SEMANTIC_SCHOLAR_JOURNALS` is optional and `.env.example` contains only an empty placeholder.
- [ ] `SEMANTIC_SCHOLAR_JOURNALS` filters only Semantic Scholar and does not filter arXiv or another retriever.
- [ ] Documentation states that `RETRIEVER=arxiv,semantic_scholar` may still contribute out-of-whitelist arXiv results.
- [ ] Documentation directs strict target-journal-only operation to the existing `RETRIEVER=semantic_scholar` runtime configuration without changing registration, selection, orchestration, or the implementation boundary.
- [ ] Missing, empty, and whitespace-only configuration omits `venue` and preserves unrestricted journal behavior.
- [ ] Parsing uses English-comma splitting, `strip()`, `lower()`, empty-item removal, and order-preserving de-duplication.
- [ ] Only the five frozen tokens are accepted; arbitrary journal names are rejected.
- [ ] Every token expands to exactly the frozen venue value or ordered aliases.
- [ ] `journal_of_radars` expands to `Journal of Radars`, `雷达学报`, and `雷达学报(中英文)`.
- [ ] Expanded venue values are de-duplicated in order and joined with commas into one `venue` parameter.
- [ ] Relevance and both bulk sort paths receive the same configured `venue` value.
- [ ] Relevance continues to omit `sort`; bulk sorts retain their exact `:desc` mappings.
- [ ] Mixed valid and invalid configuration sends only valid expanded venues and emits a safe count-only warning.
- [ ] Non-blank configuration with no valid venue fails closed with `[]` and no network request.
- [ ] Separator-only non-blank configuration also fails closed.
- [ ] Unsupported token values never enter URLs, params, headers, logs, exception output, tracebacks, snapshots, or assertion output.
- [ ] Automated tests isolate and restore both journal and API-key environment variables and never inherit host values.
- [ ] Every automated `requests.get` call is mocked and no automated test accesses a real network.
- [ ] API-key normalization and header behavior remain unchanged and key material stays secret.
- [ ] Endpoint routing, 10-second timeout, one-request/no-retry behavior, and safe HTTP failure isolation do not regress.
- [ ] `BODY_IS_PREFETCHED_CONTENT` remains unchanged.
- [ ] Results remain exactly `title`, `href`, and `body`, and metadata body formatting remains unchanged.
- [ ] No core flow, registry, prompt, frontend, dependency, lock file, or protected test is modified.
- [ ] Only the approved Milestone 1.3 implementation files are changed.
- [ ] Automated acceptance does not require the real provider to return a result for any token.
- [ ] A separately authorized controlled smoke test reports transport/routing status and venue coverage status independently for every token without printing secrets or response bodies.
- [ ] Transport/routing success requires HTTP 2xx, a valid top-level structure, the correct `venue` route and parameters, and no escaping exception or sensitive-data exposure; zero results may still satisfy this dimension.
- [ ] Venue coverage success requires at least one exact-contract result whose provider venue belongs to a frozen candidate name for the tested token.
- [ ] Zero results for any token are recorded as coverage inconclusive or a provider coverage gap and are not treated alone as proof of a code defect.
- [ ] HTTP 429, 401, and 403 are not counted as successful real-request smoke results and retain their stopping behavior.
- [ ] Any Journal of Radars result gap is marked as a high-risk coverage gap without misclassifying zero provider coverage as an implementation failure.
