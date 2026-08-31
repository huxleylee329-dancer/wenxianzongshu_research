# Academic Search MVP Specification

Status: **Approved and frozen**  
Milestone covered: **Milestone 1 + approved Milestone 1.1 integration amendment**, with a deferred outline for **Milestone 2**<br>
Configuration: `RETRIEVER=arxiv,semantic_scholar`

This specification is the implementation boundary for the Academic Search MVP. Any change to the scope, output contract, source behavior, or protected files must be approved by updating this specification before implementation.

Milestone 1.1 is approved as a design and file-boundary amendment. Its business implementation has not begun as part of this specification-only revision.

## Goal

Improve the existing arXiv and Semantic Scholar retrievers so that GPT Researcher can use scholarly abstracts with consistent citation metadata while continuing to use the existing retriever pipeline.

The MVP, including the Milestone 1.1 integration amendment, must:

- use the existing comma-separated `RETRIEVER` configuration;
- preserve the existing `{title, href, body}` retriever result contract;
- preserve the existing research and context-compression architecture while adding the minimum bridge needed for academic prefetched content;
- include authors, publication year, DOI, venue where available, and source identity in `body`;
- tolerate an individual academic provider failure without crashing the research task; and
- be testable without real network access.

## Current behavior

- `RETRIEVER=arxiv,semantic_scholar` resolves to the existing `ArxivSearch` and `SemanticScholarSearch` classes.
- The planning search uses the first configured retriever. With the approved order, that retriever is arXiv.
- Subsequent research queries iterate over all configured retrievers.
- Each retriever currently returns dictionaries containing `title`, `href`, and `body`.
- The current uncommitted Milestone 1 implementations of arXiv and Semantic Scholar place the normalized academic metadata and abstract in `result["body"]`.
- `ResearchConductor._search_relevant_source_urls` currently treats only `result["raw_content"]` longer than 100 characters as prefetched content.
- A normal `result["body"]` is ignored by that method; only its URL is retained and the landing page is fetched again through the ordinary scraper path.
- Consequently, the frozen author, year, venue, DOI, source, and abstract block in an academic result's `body` cannot currently be guaranteed to enter `ContextCompressor`.
- The original Milestone 1 file boundary is therefore insufficient to complete the end-to-end academic-content goal. Milestone 1.1 supplies the narrowly scoped integration bridge.
- Search-result limits retain their existing per-query, per-retriever meaning through `MAX_SEARCH_RESULTS_PER_QUERY`.

## Scope

### Milestone 1

Milestone 1 includes only:

- improving the existing arXiv retriever;
- improving the existing Semantic Scholar retriever;
- adding a small academic utility module for DOI normalization and body formatting;
- preserving metadata inside `body` in the frozen format so it is available to the Milestone 1.1 bridge;
- adding provider-specific logging, retry/error handling, and a Semantic Scholar HTTP timeout;
- skipping results that do not contain a usable abstract; and
- adding fully mocked unit tests for both retrievers and the shared formatting behavior.

### Milestone 1.1 integration amendment

Milestone 1.1 includes only:

- declaring a class-level academic-prefetch capability on the existing arXiv and Semantic Scholar retrievers;
- teaching `ResearchConductor._search_relevant_source_urls` to pass opted-in academic `body` content into the existing `prefetched_content` path;
- preventing those opted-in academic landing pages from being fetched a second time; and
- adding a fully isolated integration test for this bridge.

No new search mode or separate academic-search orchestration layer is introduced by either milestone.

## Non-goals

Milestones 1 and 1.1 do not include:

- `SEARCH_MODE`, `ACADEMIC_SOURCES`, `AcademicSearchService`, or another search architecture;
- changes to `agent.py`, any part of `researcher.py` other than `ResearchConductor._search_relevant_source_urls`, the retriever registry, frontend, report prompts, or dependency files;
- changes to `ContextCompressor` or the scraper manager;
- PDF download, PDF parsing, or full-text retrieval;
- year-range filtering;
- DOI-based or normalized-title cross-source deduplication;
- cross-source ranking;
- a global result limit across retrievers;
- changes to the meaning of `MAX_SEARCH_RESULTS_PER_QUERY`;
- Crossref calls or Crossref-derived metadata;
- title-based fuzzy metadata matching;
- changing the fact that the first configured retriever is used during planning;
- adding `raw_content` or any other top-level field to academic retriever results; or
- selecting academic behavior by hard-coded `ArxivSearch` or `SemanticScholarSearch` class names.

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
- The result dictionary contains exactly `title`, `href`, and `body`; neither Milestone 1 nor Milestone 1.1 adds `raw_content` or any other top-level result key.
- The Milestone 1.1 capability is expressed on the retriever class, never in an individual result dictionary.
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
- An absent or unusable DOI is formatted as `N/A`; DOI lookup is not performed in Milestone 1 or Milestone 1.1.
- The formatter is a pure function and performs no network access or logging.

### DOI source and usability clarification

- Milestones 1 and 1.1 trust the source semantics of arXiv `result.doi` and Semantic Scholar `externalIds.DOI`.
- For these milestones, an unusable DOI means a non-string value, a missing value, or a value that is empty after a supported prefix and surrounding whitespace are removed.
- DOI handling is limited to the already frozen operations: remove one supported prefix case-insensitively, remove surrounding whitespace, and lowercase the remaining identifier.
- No DOI syntax regular expression or other strict DOI-validity check is added.
- Strict DOI-validity validation is deferred and is outside this minimum integration amendment.

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

## Milestone 1.1 integration design

### Academic retriever capability marker

In the subsequent Milestone 1.1 implementation, both `ArxivSearch` and `SemanticScholarSearch` must define this class-level capability marker:

```python
BODY_IS_PREFETCHED_CONTENT = True
```

The marker states that the retriever's `body` is validated content ready for the existing prefetched-content path, rather than an ordinary search-result snippet. It must not be copied into a result dictionary. Academic results continue to use exactly:

```python
{
    "title": str,
    "href": str,
    "body": str,
}
```

### Minimal research-flow bridge

The subsequent implementation may change only `ResearchConductor._search_relevant_source_urls` within `gpt_researcher/skills/researcher.py`. Its behavior is frozen as follows:

- Preserve the existing `raw_content` handling, including its existing full-page threshold and precedence.
- Read the opt-in generically with `getattr(retriever_class, "BODY_IS_PREFETCHED_CONTENT", False)` or equivalent; do not hard-code either academic retriever class name.
- When that capability is `True` and a result's `body` is a non-empty string, append the body unchanged to `prefetched_content` as `{"url": href, "raw_content": body}`.
- Treat whitespace-only `body` as empty, but do not strip or otherwise rewrite a body that is passed through.
- Do not apply the existing 100-character full-page threshold to an opted-in academic `body`. The academic retriever has already validated the title, URL, and abstract.
- Do not add that URL to `new_search_urls`, so the paper landing page is not fetched again.
- Continue to call `add_research_sources` for the accepted URL.
- For a normal retriever without the capability marker, continue to treat `body` as a search snippet and add its URL to the ordinary page-fetch path.
- Do not modify `ContextCompressor`, the scraper manager, retriever registry, or retriever selection logic.

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

The combined Milestone 1 and approved Milestone 1.1 implementation boundary is limited to the following files, plus this specification.

Modify:

- `gpt_researcher/retrievers/arxiv/arxiv.py`
- `gpt_researcher/retrievers/semantic_scholar/semantic_scholar.py`
- `gpt_researcher/skills/researcher.py`, only within `ResearchConductor._search_relevant_source_urls`
- `tests/test_arxiv_retriever.py`
- `tests/test_semantic_scholar_retriever.py`

Add:

- `gpt_researcher/retrievers/academic_utils.py`
- `tests/test_academic_retriever_pipeline.py`

All other files remain protected, including:

- `gpt_researcher/agent.py`
- every other method or section of `gpt_researcher/skills/researcher.py`
- `gpt_researcher/actions/query_processing.py`
- `ContextCompressor` implementation files
- scraper manager implementation files
- retriever registry and selection code
- frontend code
- report prompts
- dependency and lock files

If implementation proves impossible within this combined file boundary, work must stop and this specification must be reconsidered before another file is changed.

## Test cases

All network interactions must be mocked. Tests must not depend on live arXiv or Semantic Scholar availability.

### Academic utilities

- Formats every metadata field in the frozen order.
- Joins multiple authors with `; `.
- Represents missing year, venue, DOI, or authors as `N/A`.
- Preserves the abstract's substantive text.
- Normalizes bare DOI values and supported DOI prefixes.
- Normalizes DOI casing and surrounding whitespace.
- Returns the agreed missing value for an absent or unusable DOI.

### arXiv retriever

- Declares `BODY_IS_PREFETCHED_CONTENT = True` without changing result dictionaries.
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

- Declares `BODY_IS_PREFETCHED_CONTENT = True` without changing result dictionaries.
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

### Academic retriever pipeline integration

`tests/test_academic_retriever_pipeline.py` must cover at least:

- A retriever with `BODY_IS_PREFETCHED_CONTENT = True` has its non-empty `body` transferred unchanged to `prefetched_content` as `raw_content`.
- The corresponding URL is absent from the list of URLs awaiting page fetch.
- The URL is still recorded through `add_research_sources`.
- A normal retriever without the capability marker still sends its URL to the page-fetch list even when its result contains a non-empty `body`.
- The academic retriever result contract remains exactly `title`, `href`, and `body`.
- No test accesses a real network, arXiv, Semantic Scholar, or webpage scraper.

## Independent baseline blocker

`gpt_researcher/actions/query_processing.py` currently uses `Any` and `List` in a function annotation before importing those names from `typing`. Under Python 3.11, after earlier imports are satisfied, this causes the standard package import and pytest collection path to fail.

- This is an upstream baseline issue already present at current `HEAD`; it was not caused by the Academic Search changes.
- It is outside the Milestone 1.1 implementation file boundary.
- It must be fixed separately and delivered in an independent commit.
- Neither this specification-only revision nor subsequent Academic Search implementation may opportunistically modify `gpt_researcher/actions/query_processing.py`.
- Until the baseline defect is fixed and a formal project environment is established, isolated Academic Search test success must not be described as a complete standard project pytest pass.

## Acceptance checklist

- [ ] `RETRIEVER=arxiv,semantic_scholar` continues to resolve through the existing registry.
- [ ] No new global search mode, service, or parallel retrieval architecture exists.
- [ ] Both retrievers return only the required `title`, `href`, and `body` contract.
- [ ] No `raw_content` or other top-level field is added to academic retriever results.
- [ ] Both retrievers use the shared `academic_utils.py` formatter.
- [ ] Both academic retrievers declare `BODY_IS_PREFETCHED_CONTENT = True` as a class-level capability marker.
- [ ] Academic `body` reaches `prefetched_content` unchanged as `raw_content`.
- [ ] An academic landing-page URL handled through that bridge is not added to the page-fetch list and is not fetched again.
- [ ] Normal webpage retrievers without the capability marker retain their existing page-fetch behavior even when they return `body`.
- [ ] The integration test proves that academic `body` reaches the downstream scraped-data input.
- [ ] The research-flow bridge does not hard-code `ArxivSearch`, `SemanticScholarSearch`, or other concrete academic retriever class names.
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
- [ ] Standard pytest verification and isolated Academic Search test results are reported accurately and distinctly.
- [ ] No protected file or dependency file is modified.

## Deferred Milestone 2

Crossref enrichment is deferred and is not part of Milestone 1 or Milestone 1.1 implementation or tests.

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
