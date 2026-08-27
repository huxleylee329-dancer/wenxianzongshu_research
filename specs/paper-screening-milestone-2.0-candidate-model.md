# Paper Screening / Milestone 2.0 Candidate Model Specification

Status: **Approved and frozen**
Milestone covered: **Paper Screening / Milestone 2.0 — internal candidate model and retriever compatibility protocol**

This standalone specification for the first Paper Screening milestone has completed review and received explicit implementation approval. It builds on, but does not modify, the Approved and frozen Academic Search MVP, Semantic Scholar Reliability, and Semantic Scholar Journal Filter specifications. Milestone 2.0 establishes only the internal `PaperCandidate` data foundation; it does not activate screening, deduplication, Crossref, LLM judgment, scoring, audit persistence, or core-flow integration. Implementation is authorized only within the frozen five-file boundary defined below. If implementation requires any other file, work must stop immediately and this specification must be revised, reviewed, and explicitly approved again. Real-network requests remain unauthorized.

## Background

The current academic retrievers normalize useful paper metadata directly into the established retriever result:

```python
{
    "title": str,
    "href": str,
    "body": str,
}
```

That exact contract is consumed by planning, `ResearchConductor`, Quick Search, MCP-facing paths, the Milestone 1.1 prefetched-content bridge, context compression, and report generation. It must not be widened.

The current projection loses provider fields needed by later paper screening:

- Semantic Scholar currently requests and retains only enough data to format the three-key result. It does not preserve `paperId`, the abstract as a separate field, `citationCount`, `publicationTypes`, structured `publicationVenue`, or the complete usable `externalIds` mapping after normalization.
- arXiv currently uses the title, abstract, authors, publication year, DOI, and `entry_id` while projecting a result, but it does not preserve `updated`, `journal_ref`, primary category, all categories, or a provider record identifier as structured candidate data.
- The formatted `body` is intentionally human-readable downstream content. Re-parsing it would be lossy and would couple later screening to presentation text.

Milestone 2.0 therefore introduces an internal, immutable `PaperCandidate` representation at the academic retriever boundary. It captures provider data before the existing three-key projection discards it. No screening, aggregation, deduplication, ranking, or core-flow integration is enabled in this milestone.

## Goal

Milestone 2.0 must:

- add an internal `search_candidates()` capability to `ArxivSearch` and `SemanticScholarSearch`;
- represent accepted provider records as immutable, strictly validated `PaperCandidate` instances;
- make each existing `search()` implementation delegate to `search_candidates()` and project each candidate back to exactly `title`, `href`, and `body`;
- preserve every current external behavior, accepted-result rule, provider order, error boundary, and Academic Search integration contract; and
- create the structured input required by later, separately approved run-wide paper-screening milestones without activating any screening behavior now.

## Internal model schema

Milestone 2.0 adds `gpt_researcher.screening.models`. It must use the project's existing Pydantic 2 dependency. No dependency or lock-file change is permitted.

### Supporting immutable external identifier

Provider external identifiers must not be stored in a mutable dictionary. They are represented by a supporting immutable model:

```python
class ExternalIdentifier(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        strict=True,
    )

    name: str
    value: str
```

Both fields must be non-empty after surrounding whitespace is removed. An identifier entry with a non-string, empty, or whitespace-only name or value is omitted. Provider order is preserved where the provider supplies a deterministic mapping order. Duplicate identifier names use the first usable value. The DOI entry, when present, uses the same normalized DOI value stored in `PaperCandidate.doi`.

### PaperCandidate

The internal candidate model is frozen as the following logical schema:

```python
class PaperCandidate(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        strict=True,
    )

    candidate_id: str
    source: Literal["arxiv", "semantic_scholar"]
    source_record_id: str | None
    retrieval_query: str
    source_rank: Annotated[int, Field(gt=0)]

    title: str
    href: str
    body: str
    abstract: str
    authors: tuple[str, ...] = ()

    published_year: Annotated[int, Field(ge=1000, le=9999)] | None = None
    published_at: datetime | None = None
    updated_at: datetime | None = None

    venue: str | None = None
    publication_venue_id: str | None = None
    publication_venue_name: str | None = None
    publication_venue_type: str | None = None
    publication_venue_alternate_names: tuple[str, ...] = ()

    doi: str | None = None
    external_ids: tuple[ExternalIdentifier, ...] = ()
    citation_count: Annotated[int, Field(ge=0)] | None = None
    publication_types: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()
    journal_reference: str | None = None
```

Schema rules:

- `candidate_id`, `retrieval_query`, `title`, `href`, `body`, and `abstract` are strings. The accepted-record rules require non-empty `candidate_id`, `title`, `href`, and `abstract`.
- `candidate_id`, `title`, `href`, and `abstract` use model validators that reject empty and whitespace-only values. They do not silently repair a blank required value.
- `ExternalIdentifier.name` and `ExternalIdentifier.value` use validators that remove surrounding whitespace and reject values that are then empty. The validators receive strict strings; non-string values are not coerced.
- `source_rank` is a strict positive integer and a one-based position in the provider's raw result order. `True`, `False`, `0`, negative integers, strings containing digits, floats, and other types are rejected. It is assigned before invalid records are skipped, so gaps accurately preserve provider rank.
- String collections use tuples, preserve provider order, discard non-string or blank entries, and de-duplicate by first occurrence only when the provider data itself contains duplicates.
- Every tuple field is cleaned and converted to a tuple by the provider adapter before model construction. The model must not depend on Pydantic to convert an input list into a tuple; a directly supplied list is rejected under strict validation.
- `published_year` is a normalized strict integer from `1000` through `9999`, inclusive, or `None`. It stores only a four-digit year that the frozen formatter can represent normally. A boolean, numeric string passed directly to the model, float, out-of-range integer, or other type is rejected rather than coerced.
- `citation_count` is a normalized non-negative strict integer or `None`. A boolean, numeric string passed directly to the model, float, negative integer, or other type is rejected rather than coerced. Provider adapters convert a negative citation count to `None` before model construction.
- `published_at` and `updated_at` are provider-supplied `datetime` values or `None`. Milestone 2.0 does not invent a time, timezone, or date from a year.
- `body` is generated from the same normalized values and `format_academic_body()` call used by the existing retriever behavior. It is stored so projection is exact; it is never parsed to recover structured fields.
- Pydantic `frozen=True` plus tuple-based nested collections must prevent mutation of both the candidate and its stored collections. `extra="forbid"` must reject undeclared fields, and `strict=True` must reject implicit model-level type conversion.
- Raw provider data is normalized by the retriever's provider adapter before it is passed to either model. The models must not rely on Pydantic coercion to repair supplier types, collection shapes, or values.
- Model validation is an internal record-processing boundary. A malformed provider record is skipped under the existing provider-specific safe logging behavior; it must not terminate the overall research task.
- Strict validation does not change provider-failure isolation. Adapter normalization failures and Pydantic validation failures still skip only the affected record, retain valid partial results, and do not escape into the research task.

## Candidate identity

`candidate_id` identifies a retrieved provider record. It is not a cross-provider deduplication key.

The deterministic algorithm is frozen in this precedence order:

1. When `source_record_id` is usable, use `<source>:<source_record_id>`.
2. Otherwise, when normalized `doi` is available, use `<source>:doi:<normalized-doi>`.
3. Otherwise, use `<source>:href-sha256:<lowercase SHA-256 hex digest of the exact normalized href UTF-8 bytes>`.

The source tokens are exactly `arxiv` and `semantic_scholar`. Python's process-randomized `hash()` must not be used. Including the source is intentional: two provider records for the same work remain separate candidates until a future global deduplication milestone evaluates DOI and title identity.

`source_record_id` is provider data, not an invented cross-provider ID:

- Semantic Scholar uses the trimmed non-empty `paperId`, otherwise `None`.
- arXiv uses the trimmed non-empty value returned by the provider object's `get_short_id()` capability, otherwise `None`. `href` continues to come from `entry_id`; Milestone 2.0 does not implement custom URL parsing to manufacture an arXiv ID.

## Missing-field strategy

Missing provider data must be represented explicitly and must never be inferred from unrelated display text.

| Field category | Missing or unusable representation |
| --- | --- |
| Optional scalar string | `None` |
| Optional integer | `None` |
| Optional datetime | `None` |
| Authors, alternate venue names, external IDs, publication types, categories | Empty tuple `()` |
| Required title, href, or abstract | Reject and skip the provider record, preserving current behavior |
| Candidate identity input | Use the frozen identity fallback order |

Additional rules:

- Do not copy `venue` into `publication_venue_name`, or vice versa. They record distinct provider fields.
- Do not derive `published_at` from `published_year`.
- Do not infer a journal, publication type, category, citation count, or structured venue from a title, abstract, URL, DOI, or formatted `body`.
- Do not substitute `N/A` into structured fields. `N/A` remains only part of the frozen human-readable body formatter.
- Semantic Scholar fields not present or not usable in a record become `None` or `()` under the table above.
- arXiv fields that its provider does not supply, including citation count and structured publication venue data, remain `None` or `()`.

## Provider-adapter normalization and body-year compatibility

Provider adapters own the boundary between permissive supplier data and strict model construction. They must complete all trimming, scalar validation, tuple cleaning, DOI normalization, and supported year normalization before instantiating a model. `strict=True` is a correctness guard; it is not a substitute for provider adaptation.

### Semantic Scholar `year`

Normalize the Semantic Scholar `year` value exactly as follows:

- A non-boolean integer from `1000` through `9999`, inclusive, is retained as an integer.
- A string whose `strip()` result strictly matches four decimal digits is converted to an integer only when the converted value is from `1000` through `9999`, inclusive.
- A negative integer, `0`, an integer from `1` through `999`, an integer of `10000` or greater, a boolean, a float, any other string, or any other type becomes `None`.
- No year is inferred from publication venue data, title, abstract, URL, DOI, or another provider field.

### arXiv `published.year`

Normalize arXiv `published.year` as follows:

- A non-boolean integer from `1000` through `9999`, inclusive, is retained as an integer.
- Every other value becomes `None`.
- No year is inferred from date text, title, `journal_ref`, DOI, or another provider field.

### Body source values

- `body` must be generated from the provider adapter's normalized `authors`, `published_year`, `venue`, `doi`, and `abstract` values.
- `PaperCandidate.published_year` stores only a year from `1000` through `9999`, inclusive, which the frozen formatter represents as four decimal digits.
- A Semantic Scholar four-digit string year such as `"2024"` becomes integer `2024` in the candidate and must continue to produce `Year: 2024`, matching the pre-refactor `format_academic_body()` result.
- Every valid `published_year` must appear in the body as `Year: YYYY`.
- Every `published_year=None` must appear in the body as `Year: N/A`.
- A candidate's structured `published_year` and its body year must never disagree. Both representations must come from the same normalized adapter value.
- The body still uses the existing formatter without modification; this milestone changes only which normalized adapter value is supplied to it.

## `search_candidates()` / `search()` compatibility protocol

Both academic retrievers add a synchronous public method:

```python
search_candidates(max_results=...) -> list[PaperCandidate]
```

The default `max_results` remains provider-compatible: `5` for arXiv and `20` for Semantic Scholar. Constructor parameters and sort validation remain unchanged.

The protocol is frozen as follows:

1. `search_candidates()` owns the existing provider request/client iteration, field validation, result normalization, failure isolation, and provider-local order.
2. `search()` performs exactly one call to `search_candidates(max_results=max_results)` and returns:

   ```python
   [candidate.to_retriever_result() for candidate in candidates]
   ```

3. `PaperCandidate.to_retriever_result()`, or an exactly equivalent projection, returns a newly allocated dictionary with exactly:

   ```python
   {
       "title": candidate.title,
       "href": candidate.href,
       "body": candidate.body,
   }
   ```

4. A fourth top-level result key is forbidden. In particular, no candidate, identifier, abstract, rank, metadata, or raw provider object is attached to the retriever result.
5. `search()` and the projection of `search_candidates()` must have identical accepted records, order, values, and limits for the same mocked provider response.
6. Calling `search()` remains the normal external behavior. `ResearchConductor`, Quick Search, MCP-facing consumers, and every existing caller continue to call `search()` and remain unaware of `PaperCandidate`.
7. No cross-call cache is introduced. A caller that separately invokes both methods accepts that each method invocation performs its own provider operation.

## Frozen body compatibility

Both retrievers must continue to build `body` with `gpt_researcher/retrievers/academic_utils.py::format_academic_body`.

- The field order, whitespace normalization, `N/A` values, DOI normalization, source display name, and abstract formatting remain unchanged.
- The arXiv body continues to use display source `arXiv` and venue `arXiv`.
- The Semantic Scholar body continues to use display source `Semantic Scholar`, its existing top-level provider `venue`, and DOI from `externalIds.DOI`.
- Structured fields are captured from provider data before formatting. They must not be reconstructed by parsing the generated body.
- `to_retriever_result()` projects the stored body unchanged.

## Semantic Scholar provider mapping

Semantic Scholar must request this complete field set on both relevance and bulk endpoint paths:

```text
paperId,title,abstract,url,authors,year,venue,publicationVenue,citationCount,publicationTypes,externalIds
```

The request must retain Milestone 1.2 endpoint, API-key, timeout, no-retry, and safe-error behavior and Milestone 1.3 journal-filter behavior.

| Provider input | PaperCandidate field | Frozen mapping |
| --- | --- | --- |
| Retriever query | `retrieval_query` | Original constructor query, unchanged |
| Raw `data` position | `source_rank` | One-based raw provider position |
| `paperId` | `source_record_id` | Trim non-empty string; otherwise `None` |
| Identity precedence | `candidate_id` | Frozen provider-scoped identity algorithm |
| Constant | `source` | `semantic_scholar` |
| `title` | `title` | Existing surrounding-whitespace removal; blank rejects record |
| `url` | `href` | Existing surrounding-whitespace removal; blank rejects record |
| `abstract` | `abstract` | Existing surrounding-whitespace removal; blank rejects record |
| `authors[].name` | `authors` | Usable names in provider order as a tuple |
| `year` | `published_year` | Retain a non-boolean integer in `1000..9999`; convert a stripped four-digit decimal string only when its value is in `1000..9999`; otherwise `None` |
| Not requested/provided | `published_at`, `updated_at` | `None` |
| `venue` | `venue` | Trim usable string; otherwise `None` |
| `publicationVenue.id` | `publication_venue_id` | Trim usable string; otherwise `None` |
| `publicationVenue.name` | `publication_venue_name` | Trim usable string; otherwise `None` |
| `publicationVenue.type` | `publication_venue_type` | Trim usable string; otherwise `None` |
| `publicationVenue.alternate_names` | `publication_venue_alternate_names` | Usable strings in provider order as a tuple |
| `externalIds.DOI` | `doi` | Existing `normalize_doi()` behavior |
| `externalIds` | `external_ids` | Usable string pairs only, converted to immutable identifiers; DOI uses normalized value |
| `citationCount` | `citation_count` | Provider adapter retains a non-boolean, non-negative integer; negative or otherwise unusable values become `None` before strict model validation |
| `publicationTypes` | `publication_types` | Usable strings in provider order as a tuple |
| Not supplied by this provider response | `categories` | `()` |
| Not supplied by this provider response | `journal_reference` | `None` |
| Frozen formatter output | `body` | Existing Semantic Scholar body, byte-for-byte equal for the same normalized inputs |

`publicationVenue` must be treated as optional structured data. A missing or malformed object must not remove an otherwise acceptable result. The top-level `venue` remains authoritative only for the existing body display; Milestone 2.0 does not reconcile or overwrite either representation.

## arXiv provider mapping

The existing `arxiv` library remains the client. Milestone 2.0 captures fields already available on each result object; it does not add another arXiv request path.

| Provider input | PaperCandidate field | Frozen mapping |
| --- | --- | --- |
| Retriever query | `retrieval_query` | Original constructor query, unchanged |
| Result iteration position | `source_rank` | One-based raw provider position |
| `result.get_short_id()` | `source_record_id` | Trim non-empty string; otherwise `None` |
| Identity precedence | `candidate_id` | Frozen provider-scoped identity algorithm |
| Constant | `source` | `arxiv` |
| `result.title` | `title` | Existing surrounding-whitespace removal; blank rejects record |
| `result.entry_id` | `href` | Existing surrounding-whitespace removal; blank rejects record; never replace with PDF URL |
| `result.summary` | `abstract` | Existing surrounding-whitespace removal; blank rejects record |
| `result.authors[].name` | `authors` | Usable names in provider order as a tuple |
| `result.published.year` | `published_year` | Retain a non-boolean integer in `1000..9999`; every other value becomes `None`; no inference |
| `result.published` | `published_at` | Provider `datetime` when usable; otherwise `None` |
| `result.updated` | `updated_at` | Provider `datetime` when usable; otherwise `None` |
| Constant | `venue` | `arXiv`, preserving current body behavior |
| Not supplied structurally | Publication venue fields | `None`, `None`, `None`, and `()` respectively |
| `result.doi` | `doi` | Existing `normalize_doi()` behavior |
| arXiv ID and usable DOI | `external_ids` | Immutable `arXiv` identifier followed by normalized `DOI` when each exists |
| Not supplied by arXiv | `citation_count` | `None` |
| Not supplied by arXiv | `publication_types` | `()` |
| `result.categories` | `categories` | Category strings in provider order as a tuple |
| `result.primary_category` | `categories` | Ensure a usable primary category is represented first, without duplication |
| `result.journal_ref` | `journal_reference` | Trim usable string; otherwise `None` |
| Frozen formatter output | `body` | Existing arXiv body, byte-for-byte equal for the same normalized inputs |

arXiv does not provide Semantic Scholar citation counts or a Semantic Scholar-style structured publication venue object. Those fields remain unknown. `journal_ref` is recorded only as `journal_reference`; it must not be parsed to infer venue, year, DOI, or publication type.

## Provider behavior that remains unchanged

### Semantic Scholar

- `SEMANTIC_SCHOLAR_API_KEY` remains optional, normalized with `strip()`, and sent only as `x-api-key`.
- `SEMANTIC_SCHOLAR_JOURNALS` retains its exact token parsing, venue mapping, safe warning, and fail-closed behavior.
- Relevance uses `/paper/search` without `sort`; `citationCount` and `publicationDate` use `/paper/search/bulk` with their existing `:desc` values.
- Both endpoint families receive the same configured venue parameter.
- Requests keep `timeout=10`, perform one request, and add no retry.
- HTTP and parsing failures retain safe logs and return safe partial candidates or `[]` without exposing response bodies, request data, or secrets.
- Results with missing or blank title, URL, or abstract remain skipped.

### arXiv

- The existing `arxiv.Client`, query, sort, `max_results`, provider-local order, and bounded retry configuration remain unchanged.
- `entry_id`, not a PDF URL, remains the external `href`.
- Results with missing or blank title, entry URL, or abstract remain skipped.
- Provider, generator, and individual-record failures retain their existing safe partial-result or empty-result behavior.
- Milestone 2.0 does not claim or add a hard arXiv request timeout.

## External compatibility and core-flow boundary

- `BODY_IS_PREFETCHED_CONTENT = True` remains unchanged on both academic retrievers.
- `ResearchConductor` remains unchanged and receives only the projected three-key dictionaries from `search()`.
- The Milestone 1.1 body-to-`prefetched_content` bridge remains unchanged.
- `ContextCompressor`, the scraper manager, retriever registration and selection, report generation, prompts, frontend, Quick Search, Deep Research, and MCP code remain unchanged.
- Existing Academic Search tests must continue to pass without modification.
- Milestone 2.0 adds no configuration variable and changes no runtime default.
- Merely exposing `search_candidates()` does not activate collection or screening in any existing path.

## Future run-wide scope

The final paper-screening target is global to one complete research run. It must eventually aggregate academic candidates across:

- every generated sub-query, including the original query when used;
- every configured academic retriever; and
- all provider-local result pages or batches that a future approved design permits.

DOI/title deduplication and screening only inside a single retriever call or a single sub-query would be insufficient because equivalent papers can appear under different queries and providers. Milestone 2.0 deliberately does not add that run-wide aggregation point and does not modify core research flow. Its only responsibility is to make lossless, immutable candidate records available for a later approved collector.

## Security and data-minimization requirements

- A `PaperCandidate` or `ExternalIdentifier` must never store an API key, request header, cookie, full environment value, complete provider response, `requests.Response`, request object, exception object, traceback, or serialized transport object.
- `external_ids` contains only the provider identifier name/value pairs required for paper identity. It must not become a generic copy of provider metadata.
- Semantic Scholar API keys retain the existing rule that the normalized value may appear only in the outbound `x-api-key` header.
- Provider errors must retain existing safe logging. Candidate validation errors may be categorized by exception type but must not log full raw records or sensitive request state.
- `retrieval_query` is stored because later global auditing must attribute discovery to a query. It must not be populated with headers, environment values, or serialized request parameters.
- Model `repr`, serialization, validation errors, unit-test assertion diffs, logs, and snapshots must contain no secret or complete provider response.
- All automated provider interactions must be mocked. This milestone authorizes no real-network smoke test.

## Implementation file boundary

Implementation may change only the following files.

Add:

- `gpt_researcher/screening/__init__.py`
- `gpt_researcher/screening/models.py`
- `tests/test_academic_paper_candidates.py`

Modify:

- `gpt_researcher/retrievers/arxiv/arxiv.py`
- `gpt_researcher/retrievers/semantic_scholar/semantic_scholar.py`

No other file may be changed. In particular, implementation must not modify:

- `gpt_researcher/retrievers/academic_utils.py`;
- `gpt_researcher/skills/researcher.py` or another core research-flow file;
- `ContextCompressor` or scraper-manager code;
- retriever registration or selection;
- Quick Search, Deep Research, MCP, report, prompt, or frontend code;
- `.env`, `.env.example`, dependency files, or lock files;
- any Approved and frozen specification; or
- existing Academic Search test files.

Any need to change a file outside this boundary requires implementation to stop. This specification must then be revised, reviewed, and explicitly approved before work continues.

## Fully mocked test matrix

The new `tests/test_academic_paper_candidates.py` suite must use synthetic provider records and fully mocked `arxiv.Client` and `requests.get` interactions. It must not contact arXiv, Semantic Scholar, Crossref, publishers, or any other network service.

| Area | Required coverage |
| --- | --- |
| Pydantic configuration | `PaperCandidate` and `ExternalIdentifier` are frozen; mutation fails; unknown top-level or nested fields fail because `extra="forbid"` |
| Field schema | Every candidate field has the frozen type; missing optional scalars become `None`; missing collections become immutable empty tuples |
| Strict integer rejection | `True` and `False` are rejected for `source_rank`, `published_year`, and `citation_count`; numeric strings are not implicitly converted by the models |
| Positive source rank | `source_rank=0` and negative ranks fail validation |
| Collection immutability | Authors, venue aliases, external IDs, publication types, and categories cannot be mutated and preserve frozen ordering rules |
| Strict tuple inputs | Passing a list directly to any tuple field fails; provider adapters prove that cleaned collections are converted to tuples before model construction |
| Identifier validation | `ExternalIdentifier` trims surrounding whitespace and rejects empty or whitespace-only `name` and `value` |
| Required-string validation | Empty and whitespace-only `candidate_id`, `title`, `href`, and `abstract` fail model validation |
| Candidate identity | Provider record ID, normalized DOI fallback, and SHA-256 href fallback produce deterministic provider-scoped `candidate_id` values |
| Source identifiers | Semantic Scholar `paperId` and arXiv `get_short_id()` map to `source_record_id`; missing values remain `None` |
| Source rank | Rank is one-based raw provider order and preserves gaps when malformed records are skipped |
| DOI normalization | Both providers use the existing `normalize_doi()` rules; no new DOI syntax validation is added |
| Semantic Scholar complete mapping | Every requested field, nested publication venue field, external ID, citation count, and publication type maps exactly |
| Semantic Scholar missing mapping | Missing or malformed optional fields use `None` or `()` and do not reject an otherwise acceptable paper |
| Published-year accepted boundaries | Model and provider-adapter coverage accepts integer `1000`, `2024`, and `9999`; each candidate body contains the matching `Year: YYYY` |
| Semantic Scholar year compatibility | `year="2024"` and `year=" 2024 "` both map to integer `2024`, and the projected body remains exactly the old formatter output with `Year: 2024` |
| Provider year rejection | `-1`, `0`, `999`, `10000`, `True`, `False`, `2024.0`, `"0999"`, `"10000"`, and `"not-a-year"` map to `None` and produce `Year: N/A`; the strict model rejects those non-`None` invalid inputs directly |
| arXiv complete mapping | Entry ID, short ID, title, summary, authors, published, updated, DOI, journal reference, primary category, and categories map exactly |
| arXiv missing mapping | Provider-unavailable and missing optional fields remain `None` or `()` without inference |
| Citation-count adaptation | A negative provider `citationCount` is converted to `None` before strict model construction and is not accepted directly by the model |
| Data minimization | `external_ids`, serialized candidates, logs, and exceptions contain no API key, headers, full response, or synthetic secret sentinel |
| Exact projection | `to_retriever_result()` returns a newly allocated dictionary with exactly `title`, `href`, and `body` |
| Body compatibility | Candidate body equals the existing `format_academic_body()` output exactly for each provider |
| Search compatibility | `search()` equals the projection of `search_candidates()` for equivalent mocked responses, including order and limit behavior |
| Acceptance behavior | Blank title, href, or abstract remains skipped; a malformed record does not discard earlier or later valid records |
| Semantic Scholar regression | API-key isolation, journal filter and fail-closed behavior, relevance/bulk routing, field selection, 10-second timeout, one request/no retry, and safe HTTP/JSON errors do not regress |
| arXiv regression | Query, sort, limit, entry-page URL, bounded retries, partial results, and provider-failure isolation do not regress |
| External contract regression | Existing Academic Search test suites pass unchanged; both retrievers still expose `BODY_IS_PREFETCHED_CONTENT = True` |
| Core-flow protection | Git diff proves no core flow, ContextCompressor, registry, prompt, frontend, dependency, or existing test file changed |
| Network isolation | Every automated provider call is mocked and a fail-fast guard prevents accidental real network access |

Environment-sensitive tests must explicitly delete, set, and restore `SEMANTIC_SCHOLAR_API_KEY` and `SEMANTIC_SCHOLAR_JOURNALS`. They must not inherit developer, CI, parent-process, or test-caller values.

## Explicit non-goals

Milestone 2.0 does not implement:

- year filtering;
- DOI or normalized-title deduplication;
- cross-retriever or cross-sub-query aggregation;
- paper-type inclusion or exclusion decisions;
- Crossref requests, enrichment, retraction checks, or matching;
- LLM topic-relevance judgment;
- scoring, ranking, or reranking;
- a `ScreeningDecision` model;
- audit-record or report-intermediate persistence;
- JSON screening audit output;
- ContextCompressor integration;
- a core research-flow refactor;
- Quick Search or Deep Research screening;
- changes to report generation, prompts, frontend, MCP, registry, or retriever selection;
- PDF or full-text retrieval;
- additional provider or publisher adapters;
- a real-network smoke test; or
- dependency or lock-file changes.

## Deferred Milestone 2.1–2.4 roadmap

The following sequence is directional only. Each milestone requires its own investigation, precise file boundary, mocked tests, Draft, and explicit approval before implementation.

### Milestone 2.1 — run-wide candidate collection

Design the smallest core-flow hook that collects `PaperCandidate` instances across every sub-query and academic retriever for one research run before context compression. Define lifecycle, concurrency, ownership, ordering, compatibility with non-academic retrievers, and the boundary between candidate collection and existing three-key results. Do not silently implement per-sub-query-only aggregation.

### Milestone 2.2 — deterministic deduplication and rule screening

Add global DOI-first and normalized-title fallback deduplication, then explicitly approved deterministic rules such as year and publication-type decisions. Preserve provider evidence and provenance for every merged candidate. This milestone must define conflict policy, canonical-record selection, stable ordering, and a `ScreeningDecision` schema before implementation.

### Milestone 2.3 — DOI-only Crossref retraction verification

Evaluate a minimal Crossref client that queries only an already normalized DOI for an exact record and checks explicitly defined retraction or relation metadata. It must not perform title search, fuzzy matching, discovery, abstract retrieval, or full-text access. The milestone must freeze rate limits, timeout, no/limited retry policy, cache policy, exact-match proof, conflict handling, safe failure behavior, and fully mocked tests.

### Milestone 2.4 — structured topic screening and audit integration

Define structured LLM output for topic relevance and final `ScreeningDecision` records, deterministic audit serialization, report-intermediate storage, and the controlled handoff of accepted candidates into the existing context-compression input. This milestone must preserve non-academic behavior and define prompt, model, failure, reproducibility, and audit-redaction rules before any implementation.

## Acceptance checklist

- [x] This specification received explicit approval before implementation began.
- [ ] `PaperCandidate` and `ExternalIdentifier` use existing Pydantic 2 with `frozen=True`, `extra="forbid"`, and `strict=True`.
- [ ] All candidate fields use the frozen types and explicit `None` or immutable-empty-tuple missing-value strategy.
- [ ] Provider adapters normalize raw supplier values and tuple collections before model construction; the models do not rely on implicit Pydantic coercion.
- [ ] Strict model tests reject booleans as integers, numeric strings as integers, non-positive source ranks, and lists supplied directly to tuple fields.
- [ ] Validators trim and reject blank external-identifier values and reject blank required candidate strings.
- [ ] Candidate and nested collection mutation is rejected.
- [ ] Neither model stores API keys, headers, complete provider responses, request objects, response objects, exceptions, or tracebacks.
- [ ] Both academic retrievers expose `search_candidates()` with their existing default result limits.
- [ ] Existing `search()` delegates once to `search_candidates()` and projects candidates without changing external behavior.
- [ ] Every projected result contains exactly `title`, `href`, and `body`, with no fourth top-level field.
- [ ] Projection returns the candidate's title, href, and body unchanged in provider order.
- [ ] Candidate bodies are built through the existing frozen `format_academic_body()` implementation and are never parsed for metadata.
- [ ] Deterministic provider-scoped `candidate_id` and provider `source_record_id` follow the frozen precedence and mapping rules.
- [ ] `source_rank` records the one-based raw provider position, including gaps caused by skipped records.
- [ ] Semantic Scholar requests and captures `paperId`, `title`, `abstract`, `url`, `authors`, `year`, `venue`, `publicationVenue`, `citationCount`, `publicationTypes`, and `externalIds`.
- [ ] Semantic Scholar complete and missing optional-field mappings match the frozen table.
- [ ] `published_year` accepts only strict integers from `1000` through `9999`, inclusive; accepted values and `None` always agree with `Year: YYYY` or `Year: N/A` in the body.
- [ ] Semantic Scholar `"2024"` and `" 2024 "` normalize to integer `2024` and retain the pre-refactor `Year: 2024` body output.
- [ ] Both provider adapters map `-1`, `0`, `999`, `10000`, `True`, `False`, and `2024.0` to `None`; Semantic Scholar also maps `"0999"`, `"10000"`, and `"not-a-year"` to `None`; every resulting body contains `Year: N/A`.
- [ ] Negative Semantic Scholar citation counts normalize to `None` before strict model validation.
- [ ] Semantic Scholar API-key safety, journal filtering, endpoint routing, 10-second timeout, no-retry behavior, abstract requirement, and safe error isolation do not regress.
- [ ] arXiv captures available entry ID/short ID, title, summary, authors, published, updated, DOI, journal reference, primary category, categories, and href.
- [ ] arXiv does not infer citation counts or structured publication venue data that the provider does not supply.
- [ ] DOI normalization remains the existing prefix-removal, trimming, and lowercase behavior without new syntax validation.
- [ ] `external_ids` is immutable, contains only usable paper identifiers, and contains no secret or complete raw response.
- [ ] Existing Academic Search tests pass unchanged.
- [ ] All new provider tests are fully mocked, fail fast on accidental network access, and isolate environment variables.
- [ ] `BODY_IS_PREFETCHED_CONTENT` and the Milestone 1.1 prefetched-content bridge remain unchanged.
- [ ] `ResearchConductor`, ContextCompressor, scraper manager, registry, selection, Quick Search, Deep Research, MCP, report generation, prompts, and frontend remain unchanged.
- [ ] Milestone 2.0 activates no filtering, deduplication, scoring, `ScreeningDecision`, audit persistence, or global aggregation behavior.
- [ ] The future target is documented as one research-run-wide aggregation across all sub-queries and academic retrievers, not per-sub-query deduplication.
- [ ] Only the approved Milestone 2.0 implementation files are changed.
- [ ] No dependency, lock, environment example, or protected specification file is modified.
- [ ] No real-network smoke test is run as part of Milestone 2.0.
