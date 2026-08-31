# Paper Screening Milestone 2.2A — Deterministic Screening Engine

Status: **Approved and frozen**

This standalone specification has completed review and received explicit
implementation approval. It defines the approved pure in-memory deterministic
screening layer on top of the Approved and frozen Milestone 2.0
`PaperCandidate` model and Milestone 2.1 run-wide collection mechanism.
Implementation is authorized only within the frozen file boundary and
technical design defined here. Any need to change that boundary or design
requires implementation to stop and this specification to be revised,
reviewed, and explicitly approved again.

Milestone 2.2A defines data models and deterministic transformations only. It
does not connect screening to research orchestration, context compression,
report generation, environment configuration, or any Provider.

## 1. Background and architectural boundary

Milestone 2.0 preserves structured academic metadata in immutable
`PaperCandidate` objects while retaining the exact external Retriever result
contract:

```python
{"title": ..., "href": ..., "body": ...}
```

Milestone 2.1 collects every academic candidate occurrence produced during one
top-level run. Its `PaperCandidateCollector` deliberately preserves duplicates
and exposes a deterministic immutable snapshot only after owner finalization.
It does not screen candidates and it does not provide a pre-compression global
barrier.

The completed Milestone 2.2 architecture investigation found that screening
cannot control context merely by reading the post-run 2.1 snapshot: each
sub-query currently proceeds through scraping/prefetched-content assembly and
`ContextCompressor` before the top-level collector is finalized. A future
Milestone 2.2B must therefore define a separate two-stage orchestration boundary
and a run-scoped screening workspace. Changing the 2.1 collector state machine
or finalizing it early is not part of this milestone.

Milestone 2.2A isolates the deterministic policy engine first. Its behavior is
fully testable without an event loop, network, Retriever, collector, LLM,
scraper, compressor, report, environment variable, or mutable global state.

## 2. Frozen goal

Milestone 2.2A accepts:

```python
tuple[CandidateOccurrence, ...]
```

and returns:

```python
ScreeningResult
```

through one pure engine entry point:

```python
screen_paper_occurrences(
    occurrences: tuple[CandidateOccurrence, ...],
    policy: ScreeningPolicy,
) -> ScreeningResult
```

The engine must provide:

- DOI-first duplicate grouping;
- a conservative normalized-title fallback only when DOI is absent;
- deterministic year rules;
- deterministic paper-type classification and rules;
- deterministic canonical selection;
- exactly one `ScreeningDecision` per occurrence;
- fixed machine-readable reason codes and fixed primary-reason precedence;
- complete recording of all matched exclusion and duplicate rules;
- deterministic routing from every retrieval request to retained canonical
  papers; and
- complete preservation of duplicate and excluded occurrence evidence in the
  returned in-memory result.

The input tuple is treated as immutable. The engine must not mutate a
`CandidateOccurrence`, its nested `PaperCandidate`, the input tuple, or caller
state. The same value-equivalent input and policy must produce a
value-equivalent result regardless of process, hash seed, locale, timezone,
thread, or original tuple order.

## 3. Pure execution and side-effect rules

`screen_paper_occurrences()` must:

- be synchronous and CPU-only;
- perform no file, network, subprocess, environment, clock, random, logging,
  telemetry, database, cache, or global-state access;
- use no LLM or Provider client;
- make no call to `PaperCandidateCollector`;
- never call a Retriever or issue a second Provider request;
- never call `ContextManager` or `ContextCompressor`;
- return new immutable Pydantic model values; and
- raise deterministic validation/configuration errors for invalid engine input
  rather than silently changing policy.

Expected screening exclusions are data, not exceptions. An occurrence excluded
by year, type, or duplication appears in `ScreeningResult` with a decision.
Exceptions are reserved for invalid `CandidateOccurrence` values, invalid
policy, duplicate occurrence identities, or internal invariant violations.

## 4. Strict immutable models

Every new Pydantic model and enum-backed model in this milestone must use the
project's existing Pydantic 2 dependency. No dependency change is permitted.
The new models use:

```python
ConfigDict(
    frozen=True,
    extra="forbid",
    strict=True,
)
```

Collection fields are tuples. Callers and engine builders must explicitly
construct tuples; models must not rely on Pydantic to convert lists. Boolean
values must never be accepted as integers. Required string validators strip
surrounding whitespace and reject an empty result unless a field explicitly
states that its exact original value is retained.

### 4.1 `CandidateOccurrence`

`PaperCandidate.candidate_id` identifies a Provider record, not a particular
appearance of that record in a run. The engine therefore receives a separate
occurrence envelope:

```python
class CandidateOccurrence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    occurrence_id: str
    retrieval_request_id: str
    planning_only: bool
    candidate: PaperCandidate
```

Frozen semantics:

- `occurrence_id` is a non-blank, run-local, stable identifier for exactly one
  observed candidate occurrence.
- `retrieval_request_id` is a non-blank, run-local, stable identifier for the
  exact Retriever request that produced the occurrence.
- `planning_only` is a strict boolean. `True` means the occurrence came from a
  planning-stage Retriever request; `False` means it came from a normal
  evidence-retrieval request. Integers, strings, and other truthy/falsy values
  are rejected rather than coerced.
- Both identifiers are normalized only by surrounding `strip()` in their
  validators. Case and interior characters are retained.
- `candidate` must be an already validated Milestone 2.0 `PaperCandidate`.
- The engine does not derive occurrence identity from tuple position,
  `candidate_id`, `retrieval_query`, completion time, object identity, or
  `hash()`.
- Every `occurrence_id` in one engine call must be unique. A duplicate rejects
  the complete call before screening begins.
- More than one occurrence may have the same `retrieval_request_id`,
  `candidate_id`, DOI, title, or even an identical complete `PaperCandidate`.
  Those values remain distinct evidence because their `occurrence_id` values
  differ.
- Every occurrence with the same `retrieval_request_id` must have the same
  `planning_only` value. A mixed planning/non-planning request identity rejects
  the complete engine call as invalid input.
- A planning occurrence participates normally in DOI/title grouping, year and
  type rules, canonical/reference selection, and one-decision-per-occurrence
  output. Planning evidence is never silently removed from the result.
- The future 2.2B integration owns creation of stable request and occurrence
  identifiers before concurrent completion order can affect them. 2.2A does
  not prescribe or implement the orchestration-side ID allocation algorithm.

`CandidateOccurrence` must not add fields to `PaperCandidate` and must not be
projected into the existing Retriever result dictionary.

### 4.2 Classification enums

The publication-type enum is exactly:

```python
class PaperType(str, Enum):
    JOURNAL = "journal"
    CONFERENCE = "conference"
    PREPRINT = "preprint"
    REVIEW = "review"
    BOOK_CHAPTER = "book_chapter"
    UNKNOWN = "unknown"
```

The unknown-field policies are exactly:

```python
class UnknownValuePolicy(str, Enum):
    INCLUDE = "include"
    EXCLUDE = "exclude"
```

### 4.3 `ScreeningPolicy`

Policy is passed explicitly; the engine never reads environment variables or
project configuration. `ScreeningPolicy` contains screening rules only. It
does not contain an enabled/disabled runtime switch:

```python
class ScreeningPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    min_year: Annotated[int, Field(ge=1000, le=9999)] | None = None
    max_year: Annotated[int, Field(ge=1000, le=9999)] | None = None
    unknown_year: UnknownValuePolicy = UnknownValuePolicy.INCLUDE
    allowed_paper_types: tuple[PaperType, ...] | None = None
    unknown_paper_type: UnknownValuePolicy = UnknownValuePolicy.INCLUDE
```

Policy rules:

- Year bounds are inclusive.
- Either year bound may be supplied independently.
- `min_year > max_year` is invalid.
- `True`, `False`, floats, and numeric strings are invalid year bounds.
- `allowed_paper_types=None` disables paper-type filtering.
- An explicit empty tuple is valid and allows no known paper type; an
  `UNKNOWN` classification is still governed independently by
  `unknown_paper_type`.
- Duplicate entries in `allowed_paper_types` are invalid rather than silently
  normalized.
- When `allowed_paper_types` is not `None`, `PaperType.UNKNOWN` must not appear
  in it; unknown handling is controlled only by `unknown_paper_type`.
- The default policy includes unknown years and unknown paper types and applies
  no year or type restriction.
- A future 2.2B integration that has screening disabled must bypass the entire
  2.2A engine rather than construct a permissive policy and invoke it. Runtime
  enablement belongs to orchestration/config integration, not this pure policy.

### 4.4 Reason codes

The machine-readable reason enum is exactly:

```python
class ScreeningReasonCode(str, Enum):
    INCLUDED = "included"
    DUPLICATE_OF_CANONICAL = "duplicate_of_canonical"
    YEAR_BELOW_MIN = "year_below_min"
    YEAR_ABOVE_MAX = "year_above_max"
    YEAR_UNKNOWN = "year_unknown"
    TYPE_NOT_ALLOWED = "type_not_allowed"
    TYPE_UNKNOWN = "type_unknown"
```

No free-form reason produced by an LLM is permitted. A future milestone may
add separately approved reason codes but must not reinterpret these values.

### 4.5 `ScreeningDecision`

Every occurrence produces exactly one immutable decision:

```python
class ScreeningDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    decision_order: Annotated[int, Field(gt=0)]
    occurrence_id: str
    retrieval_request_id: str
    candidate_id: str
    source: Literal["arxiv", "semantic_scholar"]
    retrieval_query: str
    source_rank: Annotated[int, Field(gt=0)]
    included: bool
    primary_reason: ScreeningReasonCode
    matched_rules: tuple[ScreeningReasonCode, ...]
    classified_type: PaperType
    type_evidence: tuple[str, ...]
    duplicate_group_id: str
    duplicate_key_kind: Literal["doi", "title", "singleton"]
    canonical_occurrence_id: str | None
    canonical_candidate_id: str | None
```

Frozen semantics:

- `decision_order` is the one-based position in the result's deterministic
  decision order. It is not input or concurrency order.
- `included=True` is allowed only for the selected eligible canonical member
  of a group.
- `primary_reason=INCLUDED` if and only if `included=True`.
- `matched_rules` contains every applicable exclusion code in the fixed order
  defined in Section 9. `INCLUDED` appears as the sole item only for an
  included canonical.
- A non-canonical duplicate records `DUPLICATE_OF_CANONICAL` even if it also
  fails year or type rules.
- `canonical_occurrence_id` and `canonical_candidate_id` identify the included
  canonical when the group has one. They are `None` when no group member is
  eligible.
- Decisions retain identifiers and rule evidence but do not copy title,
  abstract, body, secrets, Provider responses, requests, headers, exceptions,
  or tracebacks.

`type_evidence` is a strict immutable tuple rather than a summary string. Each
entry must use one of these deterministic forms:

```text
source:arxiv
publication_types:<normalized-token>
publication_venue_type:<normalized-token>
none
```

Evidence construction is frozen as follows:

1. arXiv produces exactly `("source:arxiv",)`.
2. Semantic Scholar first emits one
   `publication_types:<normalized-token>` entry for every usable structured
   `publication_types` value in Provider tuple order.
3. It then emits one
   `publication_venue_type:<normalized-token>` entry when the structured
   `publication_venue_type` is usable.
4. Each token is normalized with `strip().casefold()`. Non-string and blank
   values are unusable; no token is inferred from display fields.
5. Exact duplicate encoded entries are removed while preserving their first
   occurrence. Evidence is never alphabetically sorted: field order,
   Provider tuple order, and first occurrence are authoritative.
6. When no usable structured type evidence exists, the tuple is exactly
   `("none",)`.

The tuple preserves recognized, unrecognized, conflicting, and fallback
evidence alike. Classification must not discard evidence merely because a token
does not map to a `PaperType` or because another token determines the result.

### 4.6 Duplicate evidence

The result retains one group record for every duplicate group, including
singletons and groups whose members are all excluded:

```python
class DuplicateGroup(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    group_id: str
    key_kind: Literal["doi", "title", "singleton"]
    key_value: str
    member_occurrence_ids: tuple[str, ...]
    reference_occurrence_id: str
    canonical_occurrence_id: str | None
```

- `member_occurrence_ids` includes every occurrence exactly once in
  deterministic member order.
- `reference_occurrence_id` is always present and is the best member under the
  canonical ordering, even when all members are rule-ineligible.
- `canonical_occurrence_id` is the best rule-eligible member, or `None` when
  none is eligible.
- A reference member is evidence only; it must not be routed or described as
  included when `canonical_occurrence_id is None`.
- `group_id` is `group:sha256:<lowercase SHA-256 hex digest>` of the UTF-8 bytes
  of `<key_kind>:<key_value>`. Python `hash()` is forbidden.

### 4.7 Request routing

The routing output is:

```python
class RetrievalRequestRoute(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    retrieval_request_id: str
    canonical_occurrence_ids: tuple[str, ...]
```

There is exactly one route for every distinct input `retrieval_request_id`,
including a request for which all occurrences are excluded. Such a route has
an empty tuple.

Every planning request has an empty route, regardless of whether its
occurrences are included canonicals or share groups with non-planning
occurrences. Planning occurrences still remain in screening, grouping,
decisions, and duplicate evidence.

For a non-planning request, and only for a duplicate group actually represented
by at least one non-planning occurrence from that request, the route points to
that group's included canonical. The canonical may itself be a planning
occurrence or may have been retrieved by another request, but the group cannot
enter a non-planning request's route unless that request itself retrieved a
non-planning member of the group. Planning-only discovery never injects a new
group into a normal request route.

The same canonical appears at most once in a route. Canonicals are ordered by
their global canonical decision order, not by input order or worker completion
order.

This routing model is only a pure 2.2A artifact. Milestone 2.2A does not feed it
to a scraper, compressor, context manager, or report.

### 4.8 `ScreeningResult`

```python
class ScreeningResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    occurrences: tuple[CandidateOccurrence, ...]
    decisions: tuple[ScreeningDecision, ...]
    duplicate_groups: tuple[DuplicateGroup, ...]
    included_canonical_occurrence_ids: tuple[str, ...]
    routes: tuple[RetrievalRequestRoute, ...]
```

The result is self-contained in memory:

- `occurrences` contains every input occurrence exactly once in deterministic
  occurrence order.
- `decisions` has the same length and order as `occurrences` and maps one to
  one by `occurrence_id`.
- `duplicate_groups` covers every occurrence exactly once.
- `included_canonical_occurrence_ids` contains exactly the included canonical
  occurrences in global deterministic canonical order.
- `routes` contains every distinct request exactly once in ascending
  `retrieval_request_id` order.
- Excluded and duplicate occurrences are never removed from `occurrences`,
  `decisions`, or their group evidence.

## 5. DOI-first duplicate grouping

Grouping occurs before year and type exclusion. Policy changes may change the
selected canonical but must not change group membership.

### 5.1 Usable DOI key

A DOI is usable only when `PaperCandidate.doi` is a string whose `strip()` is
non-empty. The engine uses:

```text
doi key = candidate.doi.strip().casefold()
```

Milestone 2.0 Provider adapters already apply the frozen supported-prefix
removal, whitespace removal, and lowercase normalization. 2.2A does not add a
DOI syntax regular expression and does not parse `external_ids` or `body` to
recover a DOI.

Occurrences with the same usable DOI belong to the same DOI group regardless
of source, title, query, request, or `candidate_id`.

### 5.2 DOI conflict and missing-DOI policy

- Two occurrences with different usable DOI keys never merge by title.
- An occurrence with a usable DOI and an occurrence without a usable DOI never
  merge by title, even when their normalized titles are identical.
- The engine performs no fuzzy DOI matching and no Crossref lookup.

This conservative one-present/one-missing rule favors false negatives over a
false cross-record merge and is frozen for 2.2A.

## 6. Conservative title fallback

Title fallback applies only among occurrences for which DOI is unusable.

The normalized title algorithm is exactly:

1. require a string `candidate.title` already accepted by `PaperCandidate`;
2. apply Unicode NFKC normalization;
3. apply `casefold()`;
4. replace every maximal run of Unicode whitespace with one ASCII space; and
5. remove surrounding whitespace.

The algorithm does not remove or translate punctuation, hyphens, dashes,
colons, slashes, symbols, mathematical notation, accents, version suffixes, or
abbreviations. It does not use stemming, token sorting, edit distance,
transliteration, stop-word removal, an LLM, locale-sensitive comparison, or
`hash()`.

A normalized title is eligible for fallback grouping only if it contains at
least 12 Unicode alphanumeric characters (`str.isalnum()` count, ignoring all
other characters). Eligible equal normalized titles form one title group.

An ineligible short title is a singleton keyed by `occurrence_id`. It does not
merge even with another equal short title. This is the frozen conservative
short-title threshold for 2.2A.

## 7. Deterministic paper-type classification

Classification is based only on structured `PaperCandidate` fields. The engine
must not inspect title, abstract, body, venue name, journal reference, URL,
DOI, categories, or external identifiers to guess a type.

### 7.1 arXiv

Every candidate whose `source == "arxiv"` is classified as `PREPRINT` with
`type_evidence=("source:arxiv",)`. `journal_reference` does not change this
rule.

### 7.2 Semantic Scholar

For `source == "semantic_scholar"`, each usable `publication_types` value is
normalized with `strip().casefold()`. The following fixed Semantic Scholar
Academic Graph API values are recognized:

| Official `publicationTypes` value | Normalized token | PaperType |
| --- | --- | --- |
| `Review` | `review` | `REVIEW` |
| `JournalArticle` | `journalarticle` | `JOURNAL` |
| `Conference` | `conference` | `CONFERENCE` |
| `BookSection` | `booksection` | `BOOK_CHAPTER` |

The remaining official values are deliberately unmapped:

| Official `publicationTypes` value | Normalized token | Classification |
| --- | --- | --- |
| `Book` | `book` | `UNKNOWN` |
| `CaseReport` | `casereport` | `UNKNOWN` |
| `ClinicalTrial` | `clinicaltrial` | `UNKNOWN` |
| `Dataset` | `dataset` | `UNKNOWN` |
| `Editorial` | `editorial` | `UNKNOWN` |
| `LettersAndComments` | `lettersandcomments` | `UNKNOWN` |
| `MetaAnalysis` | `metaanalysis` | `UNKNOWN` |
| `News` | `news` | `UNKNOWN` |
| `Study` | `study` | `UNKNOWN` |

`Book` must not map to `BOOK_CHAPTER`; only the official `BookSection` value
does. The engine must not infer a type from the English meaning of an official
or unknown token. In particular, `CaseReport`, `ClinicalTrial`, `Dataset`,
`Editorial`, `LettersAndComments`, `MetaAnalysis`, `News`, and `Study` remain
`UNKNOWN`.

Any token outside the official mapped set remains preserved in `type_evidence`
but classifies as `UNKNOWN`. Semantic Scholar never infers `PREPRINT` from
`publication_types`; only arXiv receives the fixed source-based `PREPRINT`
classification.

Classification uses the complete set of distinct recognized types and no
general multi-type priority:

- no usable normalized `publication_types` token permits the structured
  venue-type fallback below;
- one or more usable tokens with no recognized type produce `UNKNOWN`;
- exactly one recognized type produces that type;
- exactly the pair `REVIEW` plus `JOURNAL` produces `REVIEW`, because a review
  may also be supplied as a journal article; and
- every other set containing more than one recognized type produces
  `UNKNOWN`.

Consequently, journal/conference conflicts and book-section/any-other-recognized
formal-type conflict all classify as `UNKNOWN`. Review combined with conference
or book section is also `UNKNOWN`. Provider order does not resolve a conflict.
Because Semantic Scholar has no recognized preprint token, a supplied
`preprint` token is unrecognized evidence and never participates as a
recognized type in this conflict set.

Only when `publication_types` contains no usable normalized token may the
normalized `publication_venue_type` be used with this exact mapping. Thus an
explicit `Book`, another officially unmapped value, or an unknown token remains
`UNKNOWN` even when a mappable venue type is also present:

| PaperType | `publication_venue_type` tokens |
| --- | --- |
| `JOURNAL` | `journal` |
| `CONFERENCE` | `conference` |

These are the only venue fallback mappings supported by repository tests or
official structured evidence in this milestone. No venue-type token maps to
`REVIEW`, `BOOK_CHAPTER`, or `PREPRINT`. Every other normalized venue-type token
is retained in `type_evidence` but classifies as `UNKNOWN`.

Venue type is ignored for classification when at least one
`publication_types` token is recognized, but its normalized structured value
remains in `type_evidence`. An unrecognized or absent type produces `UNKNOWN`;
`type_evidence` still contains every normalized structured token, or exactly
`("none",)` when none exists.

## 8. Year and type rules

For every occurrence, evaluate all intrinsic rules rather than stopping at the
first failure:

- `published_year is None` matches `YEAR_UNKNOWN` only when
  `policy.unknown_year == EXCLUDE`.
- A known year lower than `min_year` matches `YEAR_BELOW_MIN`.
- A known year greater than `max_year` matches `YEAR_ABOVE_MAX`.
- A classified `UNKNOWN` type matches `TYPE_UNKNOWN` only when
  `policy.unknown_paper_type == EXCLUDE`.
- A known type matches `TYPE_NOT_ALLOWED` when
  `allowed_paper_types is not None` and it is absent from that tuple.
- Unknown type is never evaluated against `allowed_paper_types`; it is governed
  only by `unknown_paper_type`.

A member is intrinsically eligible if it matches none of these year or type
exclusion rules. Canonical selection considers only intrinsically eligible
members. A group with no intrinsically eligible member has no included
canonical and contributes no route target.

## 9. Matched rules and primary reason

`matched_rules` uses this fixed order and contains each applicable code at most
once:

1. `YEAR_BELOW_MIN`
2. `YEAR_ABOVE_MAX`
3. `YEAR_UNKNOWN`
4. `TYPE_NOT_ALLOWED`
5. `TYPE_UNKNOWN`
6. `DUPLICATE_OF_CANONICAL`

`DUPLICATE_OF_CANONICAL` applies to every non-canonical member when its group
has an included canonical, including a member that also fails intrinsic rules.
When a group has no eligible canonical, no member matches
`DUPLICATE_OF_CANONICAL`; its exclusion evidence consists only of intrinsic
rules.

The primary reason is the first item in the ordered `matched_rules`. If no
intrinsic exclusion applies, the selected canonical is `INCLUDED`; another
eligible group member has primary reason `DUPLICATE_OF_CANONICAL`.

Thus every decision follows these invariants:

- included canonical: `included=True`, `primary_reason=INCLUDED`, and
  `matched_rules=(INCLUDED,)`;
- excluded occurrence: `included=False`, `primary_reason` is the first matched
  exclusion, and `matched_rules` is non-empty; and
- no occurrence is silently dropped or receives two decisions.

## 10. Deterministic canonical selection

Canonical selection does not score, rerank, or discard evidence. It chooses one
representative deterministically from an already formed group.

Members are ordered by this exact key, with the stated preferred direction:

1. classified type quality: `REVIEW`, `JOURNAL`, `CONFERENCE`,
   `BOOK_CHAPTER`, `PREPRINT`, `UNKNOWN` (earlier preferred);
2. usable DOI present (present preferred);
3. structured publication venue present in any of
   `publication_venue_id`, `publication_venue_name`, or
   `publication_venue_type` (present preferred);
4. citation count known (known preferred);
5. citation count value (larger preferred);
6. publication year known (known preferred);
7. publication year (newer preferred);
8. abstract Unicode code-point length (longer preferred);
9. Provider priority: `semantic_scholar` before `arxiv`;
10. `source_rank` (smaller preferred);
11. `candidate_id` ascending;
12. complete stable `PaperCandidate` serialization ascending; and
13. `occurrence_id` ascending.

The stable candidate serialization uses the Milestone 2.1 rules: declared
model-field order, Pydantic JSON-mode values, deterministic ISO-8601 datetime
encoding, preserved tuple order, UTF-8 JSON with `ensure_ascii=False`, and no
object `repr()`, memory address, runtime identity, clock value, or `hash()`.

The best member among all group members is `reference_occurrence_id`. The best
intrinsically eligible member is `canonical_occurrence_id`. When none is
eligible, only the reference remains and no paper is included.

Citation count is only a late deterministic tie-breaker. This milestone does
not define or expose a citation score, composite score, or global ranking.

## 11. Deterministic result ordering

Before grouping, occurrences are placed in this exact ascending order:

1. `retrieval_request_id`;
2. `candidate.retrieval_query`;
3. `candidate.source`;
4. `candidate.source_rank`;
5. `candidate.candidate_id`;
6. stable complete candidate serialization; and
7. `occurrence_id`.

`ScreeningResult.occurrences` and `decisions` use this order.

Duplicate groups are ordered by `(key_kind_order, key_value, group_id)`, where
the kind order is `doi`, `title`, then `singleton`. Group members use the
canonical-selection key. Included canonical occurrences are ordered by their
groups' order. Routes are ordered by `retrieval_request_id`, and each route's
canonical IDs use that global included-canonical order.

No output ordering may depend on input tuple order, coroutine completion order,
set/dict iteration accident, Python hash randomization, locale, or object
identity.

## 12. Input validation and atomicity

The complete call is validated before a `ScreeningResult` is returned:

- `occurrences` must be exactly a tuple, not a list or lazy iterable;
- every member must be a `CandidateOccurrence`;
- `occurrence_id` values must be unique;
- every `retrieval_request_id` must have one consistent strict `planning_only`
  value across all of its occurrences;
- `policy` must be a `ScreeningPolicy`;
- all result cross-references and one-decision-per-occurrence invariants must
  hold; and
- no partially built result is returned if validation or invariant checking
  fails.

An empty occurrence tuple is valid. It returns empty tuples for occurrences,
decisions, duplicate groups, included canonical IDs, and routes.

## 13. Security and data minimization

- The engine must not read or retain API keys, headers, cookies, environment
  values, raw Provider responses, request/response objects, exception objects,
  tracebacks, or transport serialization.
- It must not log candidate titles, abstracts, bodies, DOI values, query text,
  policy, or decisions; the pure engine performs no logging at all.
- Decisions contain identifiers and fixed rule evidence only. Full paper text
  remains present once, through the nested candidate in `occurrences`.
- Duplicate keys are internal return data and are not persisted or emitted to
  APIs by 2.2A.
- Tests use synthetic data and must fail fast on any accidental network access.
- No test may inherit API keys or journal-filter environment values from its
  host, even though the engine itself does not read them.

## 14. External compatibility

Milestone 2.2A does not modify the Milestone 2.0 model or projection:

```python
PaperCandidate.to_retriever_result() == {
    "title": candidate.title,
    "href": candidate.href,
    "body": candidate.body,
}
```

No screening model is added to a Retriever result. `search()` remains exact
three-key behavior. `BODY_IS_PREFETCHED_CONTENT`, prefetched academic body
handling, ordinary Retriever scraping, candidate collection, reports, and API
output remain unchanged because the engine has no runtime caller in 2.2A.

## 15. Frozen implementation file boundary

After this Draft is explicitly approved, implementation may add only:

- `gpt_researcher/screening/decisions.py`
- `gpt_researcher/screening/rules.py`
- `tests/test_paper_screening_deterministic_engine.py`

Implementation may modify only:

- `gpt_researcher/screening/__init__.py`

No other file may change. In particular, implementation must not modify:

- `gpt_researcher/screening/models.py`;
- `gpt_researcher/screening/collection.py`;
- either academic Retriever;
- `gpt_researcher/agent.py`;
- `gpt_researcher/actions/query_processing.py`;
- `gpt_researcher/skills/researcher.py` or another research skill;
- `ContextManager`, `ContextCompressor`, or the scraper manager;
- Quick Search, Deep Research, Detailed Report, or Hybrid behavior;
- Retriever registration or selection;
- `.env`, `.env.example`, or project Config parsing;
- Prompt, frontend, report template, API, or WebSocket code;
- dependency or lock files; or
- any Approved and frozen specification.

The implementation may update this specification's status only in a separate
approval-only change. Any implementation need outside this four-file boundary
requires work to stop and the Draft to be revised, reviewed, and explicitly
approved again.

The dependency direction is:

```text
screening.models
      -> screening.decisions
      -> screening.rules
      -> screening.__init__ (public exports)
```

`decisions.py` may import `PaperCandidate`. `rules.py` may import the approved
screening models. Neither module may import orchestration, Retriever, Provider,
network, context, report, frontend, or configuration code.

## 16. Fully isolated test matrix

The new test file must cover all of the following.

### 16.1 Model strictness and immutability

- every new model is frozen, strict, and forbids extras;
- direct mutation fails;
- list inputs do not coerce into tuple fields;
- blank occurrence/request IDs fail after trimming;
- `planning_only` accepts only strict `True` or `False`, and a request identity
  with mixed planning flags is rejected atomically;
- a non-`PaperCandidate` nested value fails;
- booleans and numeric strings do not coerce into integer fields;
- duplicate occurrence IDs reject the entire engine call; and
- empty input returns a valid fully empty immutable result.

### 16.2 DOI and title grouping

- equal normalized DOI across arXiv and Semantic Scholar forms one DOI group;
- DOI matching is case-insensitive after `strip()` and adds no syntax regex;
- differing usable DOIs never title-merge;
- one DOI-present and one DOI-missing occurrence never title-merge;
- two DOI-missing occurrences use the exact NFKC/casefold/whitespace title
  algorithm;
- punctuation, hyphens, symbols, accents, and version suffixes are retained;
- the 12-alphanumeric threshold is inclusive;
- an 11-alphanumeric title remains a singleton;
- equal short titles remain distinct singletons;
- group IDs are deterministic SHA-256 values; and
- duplicates and singleton evidence remain fully present.

### 16.3 Year policy

- no bounds leaves known and unknown years eligible;
- minimum and maximum are inclusive;
- lower-only, upper-only, and bounded ranges work;
- below-minimum and above-maximum codes are exact;
- unknown year include and exclude policies work;
- invalid bounds, booleans, strings, and `min_year > max_year` fail; and
- every occurrence is still retained as evidence after exclusion.

### 16.4 Type classification and policy

- arXiv is always `PREPRINT`, including with a journal reference;
- Semantic Scholar maps only official `Review`, `JournalArticle`, `Conference`,
  and `BookSection` values to `REVIEW`, `JOURNAL`, `CONFERENCE`, and
  `BOOK_CHAPTER` respectively;
- every official Semantic Scholar `publicationTypes` value is covered;
- `Book` remains distinct from `BookSection` and classifies as `UNKNOWN`;
- `CaseReport`, `ClinicalTrial`, `Dataset`, `Editorial`,
  `LettersAndComments`, `MetaAnalysis`, `News`, and `Study` classify as
  `UNKNOWN` without name-based inference;
- an arbitrary synthetic unknown token remains evidence but is not a recognized
  mapping;
- Semantic Scholar never classifies `PREPRINT` from `publication_types`;
- case and surrounding whitespace normalization is deterministic;
- `REVIEW` plus `JOURNAL` maps to `REVIEW`;
- journal/conference, book-section/other-recognized-formal-type, and every other
  non-exempt recognized multi-type conflict map to `UNKNOWN`;
- every pairwise and higher-arity combination of distinct recognized types is
  tested, with `{REVIEW, JOURNAL}` as the sole multi-type exception;
- Provider ordering cannot resolve a type conflict;
- venue-type fallback is used only when no usable publication-type token is
  present;
- venue fallback recognizes only `journal` and `conference`;
- unsupported venue-type tokens remain in evidence but classify as `UNKNOWN`;
- an official unmapped or synthetic unknown publication-type token suppresses
  venue fallback and remains `UNKNOWN`;
- no title/abstract/body/venue-name guessing occurs;
- unknown type include and exclude policies work;
- `allowed_paper_types=None`, an explicit empty tuple, and a populated tuple
  have the frozen meanings;
- duplicate or `UNKNOWN` allowed-type entries are rejected; and
- `type_evidence` is a strict immutable tuple that preserves all normalized
  recognized, unrecognized, conflicting, and fallback structured evidence;
- type evidence uses publication-types-before-venue-type field order, preserves
  Provider order, and de-duplicates by first occurrence; and
- `("none",)` is used only when there is no usable structured type evidence.

### 16.5 Multi-rule decisions

- every occurrence receives exactly one decision;
- decision and occurrence orders align one to one;
- year and type failures are both retained when both match;
- matched-rule order and primary-reason precedence are exact;
- a non-canonical ineligible duplicate also records the duplicate code;
- a group with no eligible member has no canonical and no duplicate code;
- included decisions contain only `INCLUDED`;
- excluded decisions never use `INCLUDED`; and
- all decision references resolve to returned occurrences and groups.

### 16.6 Canonical selection

- each canonical tie-breaker is tested independently in its frozen order;
- only intrinsically eligible members can be included canonicals;
- an all-excluded group retains a deterministic reference but no canonical;
- citation count acts only as a tie-breaker;
- stable serialization covers datetime and nested external identifiers;
- input order and Python hash seed cannot affect selection;
- exact duplicate candidates with distinct occurrence IDs remain distinct; and
- an occurrence ID is the final stable tie-breaker.

### 16.7 Routing and result invariants

- every request receives exactly one route, including all-excluded requests;
- every planning request has an empty route while its occurrences still receive
  complete grouping, classification, canonical selection, and decisions;
- a non-planning route includes a group only when that same request actually
  retrieved a non-planning occurrence belonging to the group;
- a planning canonical may be a route target only for a non-planning request
  that independently retrieved another member of its group;
- a planning-only discovery cannot inject its group into an unrelated
  non-planning route;
- duplicates from different requests route to the same included canonical;
- a canonical appears at most once per request route;
- route target and route ordering are deterministic;
- no route targets a merely referential all-excluded group member;
- all input occurrences, duplicates, and exclusions remain in the result;
- group membership covers occurrences exactly once;
- included canonical IDs equal included decisions exactly; and
- repeated runs with value-equivalent input permutations return equal results.

### 16.8 Isolation and regression

- tests use only synthetic `PaperCandidate` objects;
- a fail-fast network guard proves no real request is possible;
- host API-key and journal-filter variables are deleted and restored;
- no Retriever, collector, LLM, scraper, context, compressor, report, API, or
  WebSocket object is invoked;
- the existing Milestone 2.0 and 2.1 tests continue to pass unchanged;
- the exact three-key projection remains unchanged; and
- Git diff contains only the approved 2.2A implementation boundary.

## 17. Explicit non-goals and deferrals

Milestone 2.2A does not implement or modify:

- the `ResearchConductor` two-stage retrieve-screen-compress flow;
- `ScreeningWorkspace` or another run-active screening container;
- `PaperCandidateCollector` or its `OPEN`/`FINALIZED`/`ABORTED` state machine;
- `GPTResearcher` ownership, borrowing, lifecycle, or getters;
- any Retriever or Provider request;
- `ContextManager`, `ContextCompressor`, or scraping;
- Quick Search, Deep Research, Detailed Report, or Hybrid;
- `.env.example` or project Config/environment parsing;
- Crossref, DOI enrichment, or retraction checks;
- LLM topic relevance or LLM exclusion reasons;
- composite scoring, ranking, or reranking;
- audit-file, JSON, report-intermediate, or database persistence;
- Prompt, frontend, report templates, API, or WebSocket output;
- Retriever registration, selection, or orchestration;
- a third academic Provider;
- PDF/full-text retrieval;
- real-network smoke tests; or
- dependency or lock-file changes.

These concerns are deferred as follows:

- **2.2B**: direct Basic Web two-stage orchestration, run-scoped
  `ScreeningWorkspace`, pre-compression barrier, request/occurrence ID
  allocation, Basic/Web planning-stage occurrence collection, runtime
  enablement that bypasses the entire engine when disabled, and canonical
  academic-body routing while preserving ordinary Retriever behavior.
- **2.2C**: explicit mode expansion and lifecycle decisions for Hybrid, Deep
  Research, and Detailed Report. Quick Search has no compressor and remains
  unchanged unless separately specified.
- **2.3**: exact existing-DOI Crossref retraction verification, with no title
  discovery or fuzzy matching.
- **2.4**: structured LLM topic assessment, any approved scoring, deterministic
  audit persistence, and controlled report/API exposure.

## 18. Official references

- [Semantic Scholar Academic Graph API documentation](https://api.semanticscholar.org/api-docs/)

## 19. Acceptance checklist

Approval:

- [x] This specification received explicit approval before implementation
  began.

Models and policy:

- [ ] All new models use Pydantic 2 with `frozen=True`, `extra="forbid"`, and
  `strict=True`.
- [ ] `CandidateOccurrence` preserves one unique occurrence and one stable
  retrieval-request identity plus its strict planning-only classification
  without modifying `PaperCandidate`.
- [ ] `ScreeningPolicy` implements the exact explicit year/type semantics and
  reads no environment or project Config and contains no runtime enablement
  switch.
- [ ] Every enum, required validator, strict integer, and tuple behavior matches
  this specification.

Grouping and classification:

- [ ] Duplicate grouping is DOI-first across Providers.
- [ ] Differing DOI and one-present/one-missing DOI records do not title-merge.
- [ ] DOI-missing fallback uses exactly NFKC, casefold, Unicode whitespace
  collapse, and the 12-alphanumeric threshold.
- [ ] Conservative title matching performs no fuzzy or semantic inference.
- [ ] arXiv and Semantic Scholar paper types use only the frozen structured
  mappings, conflict rules, and complete immutable evidence tuple.
- [ ] Semantic Scholar recognizes only `Review`, `JournalArticle`,
  `Conference`, and `BookSection`; every other official value and unknown token
  follows the frozen `UNKNOWN` rules.
- [ ] `Book` remains `UNKNOWN`, `BookSection` maps to `BOOK_CHAPTER`, and
  Semantic Scholar never infers `PREPRINT` from `publication_types`.
- [ ] Venue fallback recognizes only `journal` and `conference`, while every
  normalized structured token remains in immutable `type_evidence`.
- [ ] Unknown years and types follow only the explicit policy.

Decisions, canonical selection, and routing:

- [ ] Every occurrence has exactly one aligned `ScreeningDecision`.
- [ ] Fixed reason codes, complete multi-rule evidence, and primary-reason
  precedence are exact.
- [ ] Duplicate and excluded occurrences remain fully represented.
- [ ] Canonical and reference selection use the exact deterministic key.
- [ ] An all-excluded group has a reference but no included canonical.
- [ ] Every retrieval request has one deterministic route to its retained
  canonical papers, with no duplicate route target.
- [ ] Planning requests always have empty routes, while their occurrences still
  participate fully in screening, grouping, canonical selection, and decisions.
- [ ] Non-planning routes contain only groups actually retrieved by that
  request, including when a shared group's canonical is a planning occurrence.
- [ ] `ScreeningResult` cross-references and immutable tuple invariants hold.
- [ ] Results are independent of input order, concurrency order, locale,
  timezone, object identity, and hash seed.

Compatibility, tests, and scope:

- [ ] The engine is synchronous, pure, in-memory, and side-effect free.
- [ ] No second Provider request or any real-network request occurs.
- [ ] No Retriever result gains a fourth key; `{title, href, body}` remains
  exact.
- [ ] `PaperCandidate` and `PaperCandidateCollector` remain unchanged.
- [ ] No research flow, workspace, context, compressor, report, mode,
  environment, Prompt, frontend, API, dependency, or persistence integration is
  enabled.
- [ ] Future disabled 2.2B operation is documented to bypass the engine rather
  than model enablement inside `ScreeningPolicy`.
- [ ] The fully isolated test matrix passes together with unchanged Milestone
  2.0 and 2.1 regression tests.
- [ ] Only the frozen 2.2A implementation files change.
- [ ] No implementation starts while this specification remains Draft.
- [ ] No dependency is installed and no real-network smoke test is run.
