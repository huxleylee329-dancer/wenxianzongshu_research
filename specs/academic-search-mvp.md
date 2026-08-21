# Academic Search MVP Specification

Status: **Approved and frozen**  
Milestone covered: **Milestone 1**, with a deferred outline for **Milestone 2**  
Configuration: `RETRIEVER=arxiv,semantic_scholar`

This specification is the implementation boundary for the Academic Search MVP. Any change to the scope, output contract, source behavior, or protected files must be approved by updating this specification before implementation.

## Goal

Improve the existing arXiv and Semantic Scholar retrievers so that GPT Researcher can use scholarly abstracts with consistent citation metadata while continuing to use the existing retriever pipeline.

The MVP must:

- use the existing comma-separated `RETRIEVER` configuration;
- preserve the existing `{title, href, body}` retriever result contract;
- preserve the current research and context-compression flow;
- include authors, publication year, DOI, venue where available, and source identity in `body`;
- tolerate an individual academic provider failure without crashing the research task; and
- be testable without real network access.

## Current behavior

- `RETRIEVER=arxiv,semantic_scholar` resolves to the existing `ArxivSearch` and `SemanticScholarSearch` classes.
- The planning search uses the first configured retriever. With the approved order, that retriever is arXiv.
- Subsequent research queries iterate over all configured retrievers.
- Each retriever currently returns dictionaries containing `title`, `href`, and `body`.
- Prefetched search content is passed downstream primarily as URL plus raw body content. Metadata that exists only as additional top-level result keys is not guaranteed to survive into `ContextCompressor`.
- arXiv currently returns the paper title, PDF URL, and abstract, but does not preserve authors, year, DOI, or source in the returned content.
- Semantic Scholar currently requests some metadata but returns only title, an open-access PDF URL, and abstract. It filters out papers without an open-access PDF and does not request DOI-bearing `externalIds`.
- Search-result limits retain their existing per-query, per-retriever meaning through `MAX_SEARCH_RESULTS_PER_QUERY`.

## Scope

Milestone 1 includes only:

- improving the existing arXiv retriever;
- improving the existing Semantic Scholar retriever;
- adding a small academic utility module for DOI normalization and body formatting;
- preserving metadata inside `body` so it reaches the existing context-compression and report pipeline;
- adding provider-specific logging, retry/error handling, and a Semantic Scholar HTTP timeout;
- skipping results that do not contain a usable abstract; and
- adding fully mocked unit tests for both retrievers and the shared formatting behavior.

No new search mode or separate academic-search orchestration layer is introduced.

## Non-goals

Milestone 1 does not include:

- `SEARCH_MODE`, `ACADEMIC_SOURCES`, `AcademicSearchService`, or another search architecture;
- changes to `agent.py`, `researcher.py`, the retriever registry, frontend, report prompts, or dependency files;
- PDF download, PDF parsing, or full-text retrieval;
- year-range filtering;
- DOI-based or normalized-title cross-source deduplication;
- cross-source ranking;
- a global result limit across retrievers;
- changes to the meaning of `MAX_SEARCH_RESULTS_PER_QUERY`;
- Crossref calls or Crossref-derived metadata;
- title-based fuzzy metadata matching; or
- changing the fact that the first configured retriever is used during planning.

## Retriever output contract

Every accepted result must remain a dictionary with exactly the established required keys:

```python
{
    "title": str,
    "href": str,
    "body": str,
}
```

Contract rules:

- `title` is the provider-returned paper title after surrounding whitespace is removed.
- `href` is a human-readable paper landing or abstract page, not a PDF URL.
- `body` contains the normalized academic metadata block followed by the abstract.
- No new top-level metadata keys are required or consumed by the pipeline in Milestone 1.
- A result with an empty or whitespace-only title, URL, or abstract is skipped.
- Provider order and provider-local result order are preserved.
- `max_results` keeps its existing per-retriever behavior.

## Metadata body format

`gpt_researcher/retrievers/academic_utils.py` owns DOI normalization and construction of the metadata block. Both academic retrievers must use the same formatter.

The body format and field order are frozen as:

```text
Title: <title>
Authors: <author 1>; <author 2>
Year: <four-digit year or N/A>
Venue: <venue or N/A>
DOI: <normalized DOI or N/A>
Source: <arXiv or Semantic Scholar>

Abstract:
<abstract text>
```

Formatting rules:

- Authors are joined with `; ` and retain provider order.
- Missing optional metadata is represented by `N/A` so that the field layout remains stable.
- Abstract whitespace may be normalized, but the substantive text must not be summarized or rewritten.
- The DOI value is stored as a bare canonical identifier such as `10.1234/example`, not as a resolver URL.
- DOI normalization removes surrounding whitespace and a case-insensitive `doi:`, `https://doi.org/`, `http://doi.org/`, `https://dx.doi.org/`, or `http://dx.doi.org/` prefix, then lowercases the identifier.
- An absent or unusable DOI is formatted as `N/A`; DOI lookup is not performed in Milestone 1.
- The formatter is a pure function and performs no network access or logging.

## arXiv behavior

The existing `arxiv` library remains the API client.

The arXiv retriever must:

- construct an `arxiv.Client` with explicit retry configuration;
- continue to honor the supplied query and `max_results` value;
- use `result.entry_id` as `href`;
- obtain authors from `result.authors` in provider order;
- obtain the year from `result.published`;
- obtain DOI from `result.doi` and normalize it through `academic_utils`;
- use `arXiv` as the source and venue values;
- format metadata and `result.summary` into `body` through the shared formatter;
- skip results without a usable abstract; and
- catch provider, transport, parsing, and unexpected result-processing failures at the retriever boundary, log a provider-specific message, and return safe partial results or an empty list.

Milestone 1 does not replace the arXiv client with a custom Atom HTTP/XML implementation. Because the installed `arxiv.Client` does not expose a public request-timeout option, this milestone does not promise a hard retriever-level timeout for arXiv. Retry configuration must remain bounded.

## Semantic Scholar behavior

The Semantic Scholar retriever must:

- query the existing paper-search API;
- request at least `title`, `abstract`, `url`, `authors`, `year`, `venue`, and `externalIds`;
- remove the current `isOpenAccess` and `openAccessPdf` acceptance requirement;
- retain every otherwise valid paper with a usable abstract, regardless of open-access status;
- use the API paper-page `url` as `href`;
- obtain DOI only from `externalIds.DOI` and normalize it through `academic_utils`;
- preserve provider author order;
- use the returned year and venue without Crossref enrichment;
- use `Semantic Scholar` as the source value;
- format all metadata and the abstract into `body` through the shared formatter;
- send an explicit bounded HTTP timeout with the request;
- treat non-success HTTP responses, timeouts, connection failures, invalid JSON, and invalid response shapes as provider failures; and
- log provider-specific failures and return safe partial results or an empty list rather than propagating the provider failure into the research task.

## Error-handling rules

- A provider failure must not terminate the overall research task.
- A failure before any valid result is produced returns `[]`.
- A malformed individual record is skipped; already normalized valid records may still be returned.
- Error logs must identify the provider and failure category and must not include secrets or full response bodies.
- Expected network/provider failures must not print directly to standard output; they use the project's logging facilities.
- Semantic Scholar requests use an explicit bounded timeout.
- arXiv uses bounded client retries but, for this milestone, has no guaranteed hard request timeout.
- No fallback to another provider is implemented inside either retriever. The existing multi-retriever flow remains responsible for trying configured providers.
- Missing optional metadata is not an error.
- Missing or blank abstract is grounds to skip the result.

## Files to change

Milestone 1 implementation is limited to the following files.

Modify:

- `gpt_researcher/retrievers/arxiv/arxiv.py`
- `gpt_researcher/retrievers/semantic_scholar/semantic_scholar.py`

Add:

- `gpt_researcher/retrievers/academic_utils.py`
- `tests/test_arxiv_retriever.py`
- `tests/test_semantic_scholar_retriever.py`
- `specs/academic-search-mvp.md`

The following are explicitly protected from Milestone 1 changes:

- `gpt_researcher/agent.py`
- `gpt_researcher/skills/researcher.py`
- retriever registry and selection code
- frontend code
- report prompts
- dependency and lock files

If implementation proves impossible within this file boundary, work must stop and this specification must be reconsidered before another file is changed.

## Test cases

All network interactions must be mocked. Unit tests must not depend on live arXiv or Semantic Scholar availability.

### Academic utilities

- Formats every metadata field in the frozen order.
- Joins multiple authors with `; `.
- Represents missing year, venue, DOI, or authors as `N/A`.
- Preserves the abstract's substantive text.
- Normalizes bare DOI values and supported DOI prefixes.
- Normalizes DOI casing and surrounding whitespace.
- Returns the agreed missing value for an absent or unusable DOI.

### arXiv retriever

- Maps a complete mocked arXiv result to exactly `title`, `href`, and `body`.
- Uses `entry_id`, not `pdf_url`, as `href`.
- Includes authors, published year, normalized DOI, `Venue: arXiv`, and `Source: arXiv` in `body`.
- Handles missing DOI and other optional metadata.
- Skips a result with a missing or blank abstract.
- Preserves valid results when a later individual record is malformed.
- Passes the query, sort option, and result limit correctly.
- Configures bounded client retries.
- Returns `[]` and logs an arXiv-specific error when the mocked client fails before producing results.
- Performs no real network request.

### Semantic Scholar retriever

- Requests `externalIds`, `authors`, `year`, and `venue` together with required content fields.
- Sends an explicit timeout.
- Accepts a paper with an abstract even when it is not open access and has no PDF.
- Uses the API paper-page `url`, not `openAccessPdf`, as `href`.
- Maps authors, year, venue, DOI, and source into the frozen body format.
- Handles missing DOI, venue, year, or authors.
- Skips a result with a missing or blank abstract.
- Preserves valid results when a later individual record is malformed.
- Returns `[]` and logs a Semantic Scholar-specific error for timeout, connection failure, non-success status, invalid JSON, or invalid top-level response shape.
- Honors `max_results` without introducing a cross-provider total limit.
- Performs no real network request.

## Acceptance checklist

- [ ] `RETRIEVER=arxiv,semantic_scholar` continues to resolve through the existing registry.
- [ ] No new global search mode, service, or parallel retrieval architecture exists.
- [ ] Both retrievers return only the required `title`, `href`, and `body` contract.
- [ ] Both retrievers use the shared `academic_utils.py` formatter.
- [ ] Metadata reaches downstream context as part of `body`.
- [ ] arXiv links point to `entry_id` abstract pages.
- [ ] arXiv authors, published year, DOI, venue, and source are represented in `body`.
- [ ] arXiv retries are bounded and failures are logged without escaping the retriever boundary.
- [ ] The absence of an arXiv hard timeout is documented and not misrepresented.
- [ ] Semantic Scholar no longer filters by open-access PDF availability.
- [ ] Semantic Scholar uses paper-page URLs and keeps records with usable abstracts.
- [ ] Semantic Scholar requests and emits authors, year, venue, DOI, and source.
- [ ] Semantic Scholar requests have a bounded timeout.
- [ ] Results without usable abstracts are skipped.
- [ ] A provider failure returns safe partial results or `[]` and does not crash the overall task.
- [ ] Unit tests mock all network calls and pass without internet access.
- [ ] `MAX_SEARCH_RESULTS_PER_QUERY` retains its existing semantics.
- [ ] No year filtering, cross-source deduplication, global ranking, or global result limit is introduced.
- [ ] No protected file or dependency file is modified.

## Deferred Milestone 2

Crossref enrichment is deferred and is not part of Milestone 1 implementation or tests.

The frozen starting constraints for Milestone 2 are:

- Crossref enrichment is disabled by default.
- Crossref acts as a metadata enricher for existing academic results, not as a primary abstract, content, full-text, or PDF retriever.
- The first version queries Crossref only by an already available normalized DOI.
- Title-based exact, fuzzy, or bibliographic matching is not implemented.
- Crossref may verify or fill authors, publication year, journal/container title, DOI metadata, and reference metadata.
- Original-provider DOI values have precedence.
- A conflicting Crossref DOI is not substituted; the conflict is logged as a warning.
- Crossref failure leaves the original provider result unchanged.
- Crossref does not download or parse full text.
- Crossref must not require a new global search mode or a parallel retrieval pipeline.

Milestone 2 requires its own implementation approval, precise configuration design, request limits, caching policy, mocked test plan, and file list before code changes begin.
