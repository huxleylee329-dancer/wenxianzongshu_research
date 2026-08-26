# Academic Writing Milestone 3.8: Deterministic Evidence Provenance Index

Status: **Approved and frozen**

规范已批准并冻结，仅授权冻结边界内实施；Implementation acceptance在实施完成并验证前保持未勾选。

## 1. Goal

Milestone 3.8 adds a deterministic, bounded provenance index to the existing
ResearchEvidence artifact. The index binds a final `WorkflowEvidenceSource`
identity to source-linked evidence text that was already present in the same
completed Milestone 3.1 researcher run:

```text
final WorkflowEvidenceSource.source_id
                    |
                    v
WorkflowEvidenceProvenance(source_id, evidence_blocks)
```

It does not infer provenance from title, URL similarity, text similarity,
semantic search, or LLM output. It does not claim that every final source has
supporting text. Absence from `provenance` means only that this run retained no
bounded mechanically linked text for that source; it never means verified,
unsupported, false, or irrelevant.

The operation remains part of the existing synchronous projection interval
after one completed research run. It adds no Retriever, scraper, compressor,
LLM, Provider, network, file, database, or other external call.

## 2. Exact future implementation boundary

Implementation may modify exactly these four existing files:

```text
gpt_researcher/workflows/academic_writing/state.py
gpt_researcher/workflows/academic_writing/research_evidence.py
tests/test_academic_writing_workflow_state.py
tests/test_academic_writing_research_evidence.py
```

No file is added during implementation. No other production file or test may
change. In particular, implementation must not modify `adapters.py`, `nodes.py`,
`graph.py`, any package `__init__.py`, TopicPlanner, OutlineWriter,
SectionWriter, SectionWriterSequence, SectionMerger, any frozen specification,
dependency, lock file, backend, frontend, product entry point, or legacy path.

Repository-wide static inspection establishes that existing direct
`WorkflowResearchEvidence` construction in the following test files remains
valid because `provenance` has the frozen default `()`:

```text
tests/test_academic_writing_outline_approval.py
tests/test_academic_writing_outline_writer.py
tests/test_academic_writing_section_merger.py
tests/test_academic_writing_section_writer.py
tests/test_academic_writing_section_writer_sequence.py
tests/test_academic_writing_topic_planner.py
tests/test_academic_writing_workflow_graph.py
```

Those files require no fixture, golden, assertion, or import change. Their
prompts and outputs continue to ignore provenance. If implementation requires
a fifth file, it must stop for a revised specification.

## 3. Normative baseline and exact supersession

Except for the rows below, Milestones 3.0 and 3.1 remain normative. Later
Milestones 3.2 through 3.7 remain unchanged in full.

| Baseline | Superseded only as follows | Everything else remains frozen |
| --- | --- | --- |
| 3.0 Section 8.3 exact `WorkflowResearchEvidence` fields | Add the defaulted `provenance` field and the new nested DTO in Section 4 | Existing IDs, attempt, context and source fields and validators |
| 3.0 Section 8.3 and acceptance language excluding body/raw body | Permit only bounded exact-string blocks copied by the frozen winner/link/chunk algorithm | Complete body, complete raw content, abstract, candidate/audit/raw-source objects and opaque data remain forbidden |
| 3.0 Section 8 Evidence aggregate | Add a separate provenance budget of 64 blocks and 262,144 code points | Existing `context_blocks` limits remain unchanged |
| 3.0 Section 10 four round-trip equalities | Old schema-version-1 payloads missing only `provenance` receive the one-way default migration in Section 6 | New payloads and every other shape retain all four equalities |
| 3.1 Sections 1-2 and 11 prohibiting DTO changes/additional fields | Add only `WorkflowEvidenceProvenance` and `WorkflowResearchEvidence.provenance` | Adapter Protocols, signatures, IDs, phases, events and return union remain unchanged |
| 3.1 Sections 7 and 10.11 discarding candidate/source values before final projection | Retain the already frozen snapshots synchronously until sources and provenance are jointly projected, then release them | Getter order/count, no-await interval and concurrency non-goals remain unchanged |
| 3.1 Sections 10.2-10.4 prohibiting traversal, measurement, slicing, rereading or return of `raw_content`/body | Read only the winning candidate body or winning ordinary record raw content under Sections 8-9 | Loser content, candidate abstract, ignored metadata and arbitrary fields are never read for provenance |
| 3.1 Section 10.4 multiple exact candidate matches | Source identity acceptance remains unchanged; provenance uses the first exact match in frozen candidate order | Zero-match failure and identity matching remain unchanged |
| 3.1 Section 12 sensitive-data exclusion | Permit only newly allocated bounded exact-string evidence blocks | Every live object, original container, complete text, secret and external component remains forbidden |
| 3.1 Sections 13.2 and 13.4 raw exception/cancellation propagation | No supersession: the original exception and traceback, or the same `asyncio.CancelledError` instance and args, propagate unchanged | Such a technical-failure traceback may reach live researcher/candidate/source/audit objects; this does not permit those objects in a DTO, checkpoint, log or fixed error |
| 3.1 four-boundary stop condition and old tests/checklists | This approved four-file successor boundary replaces it for 3.8 only | No unrelated 3.1 behavior or test infrastructure is reopened |

This table is exhaustive. No broad waiver of the 3.0 or 3.1 safety boundary is
intended.

## 4. Exact DTO contract

### 4.1 New strict DTO

`state.py` adds exactly:

```python
class WorkflowEvidenceProvenance(_StrictWorkflowModel):
    source_id: str
    evidence_blocks: tuple[str, ...]
```

It inherits the existing `frozen=True`, `extra="forbid"`, `strict=True`
configuration. Direct Python construction requires an exact built-in `dict`,
an exact built-in `tuple` for `evidence_blocks`, and exact `str` values. JSON
restoration accepts JSON arrays only through `model_validate_json()` and
restores them to tuples. A tuple/list/string subclass, iterator, generator,
mapping subclass, bytes, `None`, bool, float, object, or coercible value is
rejected. Mapping keys are exactly `source_id` and `evidence_blocks`.

`source_id` is nonblank and must use the existing derived identity for an order
from 1 through 200:

```text
source_id == f"evidence-source:{order:06d}"
```

One provenance entry contains at least one and at most 64 evidence blocks.
Every block is an exact, nonblank `str`, is preserved without Unicode
normalization, and contains at most 16,384 Python Unicode code points.

### 4.2 Existing Evidence extension

`WorkflowResearchEvidence` becomes exactly:

```python
class WorkflowResearchEvidence(_StrictWorkflowModel):
    evidence_id: Literal["evidence:000001"]
    topic_plan_id: Literal["topic-plan:000001"]
    attempt: FixedOne
    context_blocks: tuple[str, ...]
    sources: tuple[WorkflowEvidenceSource, ...]
    provenance: tuple[WorkflowEvidenceProvenance, ...] = ()
```

Direct Python input for `provenance` requires an exact built-in tuple containing
exact `WorkflowEvidenceProvenance` values. JSON restoration uses an array. An
explicit `None`, list in Python mode, wrong member type, subclass, extra field,
or coercible value is rejected. Omission alone selects the default `()`.

The parent model requires:

1. zero through 64 provenance entries;
2. one through 64 blocks per present entry;
3. at most 64 blocks across all entries;
4. at most 262,144 code points across all provenance blocks;
5. every provenance `source_id` resolves exactly once in `sources`;
6. provenance IDs are unique; and
7. their source indexes are strictly increasing, making provenance an exact
   ordered subset of final `sources`.

The parent performs binding; the nested DTO alone does not claim membership in
a particular Evidence artifact. Existing `context_blocks` and `sources` limits
remain independent and unchanged.

## 5. JSON, checkpoint and schema-version behavior

`AcademicWorkflowState.schema_version` remains exactly `Literal["1"]`. No
checkpoint migration module, alternate schema, facade branch, graph change, or
old-thread rejection is added.

For a legacy schema-version-1 checkpoint whose ResearchEvidence mapping omits
only `provenance`:

1. existing canonical JSON restoration succeeds;
2. Pydantic supplies `provenance=()`;
3. empty provenance means unavailable/unrecorded, never verified;
4. the thread may continue under the existing resumability rules; and
5. its first subsequent `model_dump(mode="json")` emits
   `"provenance":[]`.

That first dump is a deliberate one-way canonicalization. Byte equality with
the legacy missing-field payload is not promised. No other missing field,
wrong type, extra field, or noncanonical value receives this exception.

Every newly created or re-dumped checkpoint includes `provenance`, including
the empty array. For those payloads, recursive JSON value/type equality, exact
model equality, and canonical UTF-8 byte equality remain mandatory under the
existing formula:

```python
json.dumps(
    payload,
    ensure_ascii=False,
    allow_nan=False,
    sort_keys=True,
    separators=(",", ":"),
).encode("utf-8")
```

## 6. Approved aggregate and canonical-size expansion

The provenance budget is separate from the existing context budget:

```text
PROVENANCE_BLOCK_MAX_CHARS = 16_384
PROVENANCE_BLOCK_MAX_COUNT = 64
PROVENANCE_TOTAL_MAX_CHARS = 262_144
```

Therefore maximum stored Evidence text increases from 262,144 context code
points to 524,288 combined context-plus-provenance code points. This expansion
is explicit and approved by this successor; it is not described as preserving
the old aggregate.

The reproducible worst-case canonical `WorkflowResearchEvidence` subobject
uses 200 maximum sources, 64 maximum context blocks totaling 262,144, and 64
one-block provenance entries totaling 262,144. NUL code points maximize JSON
escaping; non-whitespace control-code pairs surrounded by NULs provide 200
distinct maximum-length URLs and candidate IDs without reducing six-byte
escapes. For each 64-block aggregate use 15 blocks of 16,384 code points, 48
blocks of one code point, and one block of 16,336 code points. Under the field
names and canonical formula frozen here:

```text
old Evidence maximum without provenance = 7,427,660 bytes
new Evidence maximum with provenance    = 9,004,507 bytes
maximum checkpoint increase             = 1,576,847 bytes
```

The full workflow checkpoint adds its unchanged surrounding state. There is no
pre-existing global checkpoint-byte cap. This size increase is nevertheless an
explicitly approved storage/memory consequence.

## 7. One frozen source/provenance snapshot

The existing getter order and counts do not change. After all five getters
have passed their existing basic contracts and while the candidate tuple,
audit snapshot or `None`, copied research-source tuple, and copied visited URL
tuple remain in the same synchronous no-await interval, the adapter constructs
sources and provenance together.

Each internal source-priority record carries only a private locator to its
mechanically linked candidate or research-source record until URL first-wins,
candidate-ID first-wins, the 200-source cap, final order, and final `source_id`
assignment are complete. A locator that loses URL deduplication or the source
cap is discarded and its body/raw content is never read for provenance.

Provenance is then emitted in final source order. It is bound to the final
`source_id` at the same point that the final source receives that ID. No later
join by title, URL similarity, body text, candidate similarity, hash, object
identity, or set/dict iteration is permitted.

## 8. Exact candidate-linked association

### 8.1 Audit present

The existing audit route and authoritative raw comparison remain:

```text
candidate.candidate_id == entry.candidate_id
candidate.title        == entry.title
candidate.href         == entry.href
```

Zero exact candidates remains the existing fixed contract violation. With one
or more exact matches, source construction remains accepted; for provenance,
choose the first exact candidate in the original finalized candidate tuple.
Candidate order is the only tie-break. Do not read any later matching
candidate's body.

### 8.2 Audit absent

The existing candidate/source URL-intersection rule remains. Each candidate
priority record carries its own candidate locator. When multiple candidate
records normalize to one final URL, existing frozen candidate traversal and URL
first-wins select the winner; only that candidate may provide text.

### 8.3 Candidate text

For the winning candidate only, statically read `body`. Require
`type(body) is str`; a corrupted or subclass body is the existing fixed
contract violation. Never access `abstract`, even to test presence, type,
length, emptiness, or identity. If the winning body normalizes to fewer than two
code points, create no provenance for that source and do not fall back to an
ordinary raw-content record with the same URL.

## 9. Exact ordinary-source association

Apply the existing URL normalization exactly: raw URL must be an exact string,
strip it, discard blank or over-4,096 results, and never rewrite or truncate.

For each normalized URL, the provenance winner is the first usable record in
the original copied research-source list. A record is usable under the existing
3.1 predicate: an absent `raw_content` key is a URL-only marker; a present value
must be an exact nonblank-after-strip string; present invalid/blank values are
discarded with their record. Existing lexicographic title selection remains
unchanged and is not a provenance selector.

If a candidate-priority record already won the same final URL, every ordinary
record for that URL loses and none may provide or backfill text. Otherwise the
first usable ordinary record is carried through the ordinary sorted URL record,
URL first-wins and the 200-source cap:

- if its `raw_content` key is absent, the source has no provenance;
- if it contains valid exact raw content, only that winner's value is read for
  provenance; and
- every later duplicate URL is a loser and may not contribute, replace, merge,
  append, or backfill text.

The duplicate-URL boundary has these two exact vectors. Getter counts remain
the existing once-per-getter counts; the counts below are additional
provenance-text extraction reads after the existing 3.1 availability pass:

1. Record 1 has the normalized target URL but a present unusable
   `raw_content`; it is discarded by the existing availability predicate and
   receives zero provenance-text reads. Record 2 has the same normalized URL
   and valid exact raw content; it is the first usable record, wins the final
   ordinary-source locator, receives exactly one provenance-text extraction
   read, and alone contributes provenance.
2. Record 1 has the normalized target URL and no `raw_content` key. It is the
   usable URL-only winner and receives zero provenance-text reads. A later
   record with the same URL and valid raw content is a loser, receives zero
   provenance-text extraction reads, and cannot backfill; the final source has
   no provenance entry.

A visited-only final source likewise has no provenance, but it can win only
when no usable research-source record exists for that URL. Because existing
3.1 priority places all usable research sources before visited URLs, a
visited-only winner followed by a usable same-URL research source is not a
reachable vector and must not be asserted by implementation or tests.

Visited-only sources never have a candidate/raw-content locator and never
create provenance.

## 10. Exact bounded text algorithm

Candidate body and ordinary raw content use one identical algorithm. Length is
Python `len()` over Unicode code points. No Unicode normalization, case folding,
HTML/Markdown parsing, tokenization, semantic splitting, sentence splitting,
hashing, ellipsis, separator, source label, URL, title, or synthetic text is
added.

The sole term `normalized` means exactly the following exact `str` value:

```python
text.replace("\r\n", "\n").replace("\r", "\n").strip()
```

Complete candidate body and complete raw content remain forbidden. The sole
term `retainable-prefix` means exactly `normalized[:-1]`. Only this
retainable-prefix is chunked or charged to either budget. The final normalized
code point is never copied, returned, hashed, replaced by an ellipsis, or
represented by metadata. A normalized text of zero or one code point creates
no provenance. This rule makes every stored value a bounded partial excerpt
even when the winner text itself is short. The 262,144-character maximum
remains reachable from a winner containing at least 262,145 normalized code
points.

These short-text vectors are frozen before chunking:

| Exact input | `normalized` | `retainable-prefix` | Stored blocks |
| --- | --- | --- | --- |
| `""` | `""` | `""` | none |
| `"A"` | `"A"` | `""` | none |
| `"AB"` | `"AB"` | `"A"` | `("A",)` |
| `"é😀"` | `"é😀"` | `"é"` | `("é",)` |
| `"AB\n"` | `"AB"` | `"A"` | `("A",)` |
| `"A\0"` | `"A\0"` | `"A"` | `("A",)` |

These results use Python Unicode code points. NUL is an ordinary non-whitespace
code point for normalization and prefix selection. No successful vector stores
its complete normalized or complete original short text.

Implementation must produce the same value incrementally and must not
materialize an unbounded normalized copy. It may locate outer strip boundaries
on the exact input string, then scan code points in order, converting CRLF to
one LF and bare CR to one LF, while excluding the final normalized code point.
It materializes at most one current window of 16,384 code points plus bounded
output blocks.

Traverse final sources in order. For each winning text while both global
budgets remain:

1. take the next retainable-prefix window of at most
   `min(16_384, 262_144 - stored_character_count)` code points;
2. advance the source offset by the raw characters consumed to form that
   normalized window;
3. apply Python `str.strip()` to that bounded window;
4. discard an empty result; otherwise copy it to a new exact built-in `str`,
   append it to the current source's blocks, increment the global block count,
   and add its stored length to the global character count; and
5. continue until source exhaustion, 64 stored blocks, or 262,144 stored code
   points.

A nonempty final partial block is allowed and required when fewer than 16,384
retainable-prefix code points remain or the aggregate character remainder is
smaller.
Whitespace removed from a bounded window does not count against the aggregate;
the next window begins after the raw input already consumed. Empty windows do
not consume block count or stored-character budget. The deliberately excluded
final normalized code point never belongs to a window.

A present provenance entry has at least one block. A single source may receive
at most all 64 global blocks. If a source yields no stored block, omit its
entry. The instant either global budget reaches 64 stored blocks or 262,144
stored code points, terminate the outer traversal without requesting, indexing
or probing a next element, even to learn whether one exists. Do not read any
further key, attribute, property or method of a later candidate, source, audit
or locator object, and do not continue reading unused fields of the current
object. Release the unvisited locators without dereferencing them and return
only the completed pure-string projection. Earlier 3.1 basic shape and
raw-content availability validation occurred before this provenance pass; it
is not repeated and is not reclassified as provenance extraction.

The final provenance tuple is a newly allocated tuple of newly constructed
strict DTOs. Stored blocks are bounded value copies; no original candidate,
dict, list, tuple, audit entry, audit snapshot, or locator is retained.

## 11. Failure, exception and cancellation behavior

All existing call counts and order remain exact: one factory, one conduct, then
context, candidates, audit, research sources, and visited URLs once each. Empty
context priority, audit-unavailable matching, `AdapterFailure`, propagated raw
getter/factory/conduct exceptions, outer-task cancellation, fixed contract
error, retry absence, and at-least-once resume semantics do not change.

An adapter-detected provenance shape, linkage, type, DTO, budget, or canonical
failure uses the existing private fixed `_ResearchEvidenceContractError` and
its exact fixed text. It is raised only after the isolation helper that held
original objects and content has exited. Cause and context are `None`; no
dynamic ID, URL, title, body, raw content, validation detail, or exception text
appears in the error, traceback-safe frame, event, task, state, or metadata.

On success or adapter-detected fixed contract/invariant failure, original
candidate/source/audit objects, getter containers, locators, complete text,
and partial mutable buffers are unreachable from the returned DTO, adapter
fields, closure, global, state, checkpoint, fixed exception chain, and the
caller-visible fixed-error traceback. DTO/checkpoint values, tasks and metadata
never retain those objects on any path.

Deliberately propagated factory, conduct, getter or other raw runtime
exceptions retain the exact existing 3.1 boundary: the original exception and
its original traceback propagate unchanged. `asyncio.CancelledError` propagates
with the same instance and args by bare propagation. Neither is converted,
rebuilt, traceback-sanitized, or subjected to adapter cleanup that changes the
original traceback. That traceback may mechanically reach a researcher,
candidate, source, audit or other live raw object through its original frames.
This accepted technical-failure reachability does not mean that any such object
enters a DTO, checkpoint value/task/metadata, log, event, state or fixed error
text. No test or checklist may demand raw-exception or cancellation traceback
unreachability.

## 12. Downstream behavior and non-goals

Milestones 3.2 through 3.7 continue to read only their existing Evidence fields.
They do not project provenance into prompts, change prompt-length calculations,
alter SectionWriter citation allowlists, change SectionMerger validation, or
add output fields. No downstream frozen production file or test changes.

Milestone 3.8 does not implement:

- CitationReviewer, claim extraction, entailment, contradiction, confidence,
  citation correction, or natural-language attribution recognition;
- SectionWriter prompt/allowlist changes or draft regeneration;
- reference generation, merged-draft rewriting, FinalEditor, or report export;
- graph, node, event, phase, facade, checkpoint saver, persistence, backend, or
  frontend changes;
- parallelism, Send, subgraphs, retries, repair, fallback, or exactly-once
  external execution; or
- any new Retriever, scraper, compressor, LLM, Provider, network call,
  dependency, or product integration.

## 13. Compact offline test matrix

Only the two existing approved test files are extended. Tests use existing
fixtures and parameterized matrices where practical; no new test file or
general security/import framework is added.

`tests/test_academic_writing_workflow_state.py` covers:

1. strict/frozen/extra-forbid/JSON behavior for the nested DTO;
2. exact Python tuple/string rules and subclass rejection;
3. omitted `provenance` defaulting to `()`, explicit empty acceptance, and
   explicit `None`/wrong-type rejection in Python and JSON modes;
4. JSON output containing exactly `"provenance":[]` for empty provenance;
5. source-subset membership, exact source order, uniqueness, and unknown,
   duplicate, reversed, or out-of-order rejection in one matrix;
6. one/64/65 per-entry and global block boundaries;
7. 16,384/16,385 block and 262,144/262,145 aggregate boundaries, including
   exact Unicode code-point counting;
8. canonical new-payload value/type/model/byte equality; and
9. one legacy checkpoint vector missing only provenance that restores to `()`,
   re-dumps with `[]`, differs from old bytes once, and is thereafter canonical.

`tests/test_academic_writing_research_evidence.py` extends existing behavior and
security matrices to cover:

1. audit-present zero, one, and multiple exact candidate matches, proving only
   the first matching candidate body is read;
2. audit-absent candidate binding and candidate URL first-wins;
3. corrupt/subclass winning body as fixed contract failure and blank body as no
   provenance without fallback;
4. ordinary exact raw-content binding, URL-only and visited-only omission;
5. duplicate ordinary URLs in one parameter matrix: unusable-first/valid-second
   makes the second record the sole provenance winner with extraction counts
   `0,1`; URL-only-first/valid-second retains the URL-only source with no
   provenance and extraction counts `0,0`;
6. candidate-over-ordinary URL priority with loser content never read;
7. CRLF/bare-CR, outer and per-window strip, Unicode preservation, empty-window
   discard, exact 16,384 split, allowed final partial block, and the six frozen
   `""`, `"A"`, `"AB"`, `"é😀"`, `"AB\n"`, `"A\0"` short-text
   retainable-prefix vectors in the existing parameter matrix;
8. source-order traversal, one-source 64-block maximum, global 64-block and
   262,144-character stopping, and a hostile candidate/source/audit tail in the
   same budget matrix proving zero iterator requests, key/attribute/property/
   method accesses or current-object unused-field reads after exhaustion;
9. final source cap and URL/candidate-ID dedup occurring before source-ID and
   provenance assignment;
10. unchanged factory/conduct/getter counts, failure priority, raw exception,
    cancellation, and no-retry behavior through representative existing cases;
11. bounded blocks present in DTO/checkpoint while complete text, abstract,
    original candidate/audit/source objects, raw containers, loser content,
    locators and mutable buffers are unreachable on success and fixed-error
    paths, without applying that assertion to unchanged raw/cancellation
    tracebacks; and
12. existing downstream construction locations remain statically compatible
    with the default without editing those files.

No permanent test may assert an initial missing field/module, symbol existence
alone, AST parseability alone, multiple file orders, or a duplicate full import
guard/sentinel walker. Every case must map to an Implementation acceptance item
or a concrete regression above. Development should run the two targeted files;
after green, one necessary academic-writing regression is sufficient. No real
external component is invoked.

## 14. Draft approval checklist

- [ ] The deterministic provenance-index goal and explicit non-goals are approved.
- [ ] The exact four-existing-file boundary and fifth-file stop condition are approved.
- [ ] Repository-wide default-compatible construction analysis is approved.
- [ ] The supersession table is exhaustive and narrowly scoped.
- [ ] The new DTO name, two exact fields, strict/frozen/JSON behavior are approved.
- [ ] Exact dict/tuple/string rules and subclass/null/coercion rejection are approved.
- [ ] The default-empty `WorkflowResearchEvidence.provenance` field is approved.
- [ ] Provenance source IDs form a unique ordered subset of final sources.
- [ ] Per-entry and global block count rules are approved.
- [ ] Per-block and global code-point bounds are approved.
- [ ] Schema version remains exactly `1` and no migration module is added.
- [ ] Old checkpoints may resume with empty, explicitly unverified provenance.
- [ ] The one-way legacy canonicalization exception is approved.
- [ ] New checkpoints retain all four canonical round-trip equalities.
- [ ] The separate 262,144-character provenance expansion is approved.
- [ ] The 9,004,507-byte worst-case Evidence payload is approved.
- [ ] Source and provenance construction share one no-await snapshot.
- [ ] Final URL/order/cap/source-ID binding precedes provenance emission.
- [ ] Audit-present candidate linkage and zero-match failure remain exact.
- [ ] Multiple exact candidates use the first frozen candidate match.
- [ ] Audit-absent candidate linkage and URL intersection remain exact.
- [ ] Only a winning candidate body may be read; abstract is never accessed.
- [ ] Blank candidate body creates no provenance and receives no fallback.
- [ ] `normalized` and `retainable-prefix` are the only text-algorithm terms.
- [ ] All six frozen short-text vectors exclude complete winning text exactly.
- [ ] Ordinary provenance uses the first usable normalized-URL record.
- [ ] Unusable-first/valid-second selects only the valid duplicate for provenance.
- [ ] URL-only-first forbids later duplicate backfill; visited-only reachability remains exact.
- [ ] Candidate priority prevents ordinary-text fallback for the same URL.
- [ ] The incremental CR/LF, strip, chunk and Unicode rules are approved.
- [ ] A nonempty partial final block is allowed and required.
- [ ] Each present source has 1-64 blocks and the global maximum is 64.
- [ ] Budget exhaustion stops traversal without any later object access or probing.
- [ ] Only bounded copied strings may enter state/checkpoint.
- [ ] Success, fixed-error and DTO/checkpoint surfaces retain no original objects.
- [ ] Raw exception/cancellation traceback reachability remains explicitly accepted.
- [ ] Existing fixed failure/raw exception/cancellation semantics remain unchanged.
- [ ] Existing call counts, getter order, retry absence and resume semantics remain unchanged.
- [ ] Downstream 3.2-3.7 prompts, allowlists, DTOs and outputs remain unchanged.
- [ ] CitationReviewer and every other listed feature remain deferred.
- [ ] The compact two-file test strategy is approved.
- [x] This specification received explicit approval before implementation began.

## 15. Implementation acceptance checklist

- [ ] Only the exact four approved existing files changed.
- [ ] No other production, test, specification, initializer, dependency or lock file changed.
- [ ] `WorkflowEvidenceProvenance` has exactly the approved fields and model configuration.
- [ ] Direct Python and JSON restoration enforce the approved exact container/value types.
- [ ] Missing provenance alone defaults to `()`; null and wrong types reject.
- [ ] Empty provenance dumps to the exact JSON array field.
- [ ] Parent validation enforces subset membership, order and uniqueness.
- [ ] Parent validation enforces per-entry and global block/count/character limits.
- [ ] Existing context and source validation remains unchanged.
- [ ] Schema version remains `1` and old missing-field checkpoints restore.
- [ ] Legacy first re-dump is one-way; its re-dumped payload is thereafter canonical.
- [ ] New payload recursive value/type, model and canonical-byte equalities pass.
- [ ] The approved maximum Evidence payload vector is mechanically reproduced.
- [ ] Existing researcher factory, conduct and getter order/counts are unchanged.
- [ ] No new external call, dependency, retry, fallback or repair exists.
- [ ] Source and provenance use the same frozen synchronous snapshots.
- [ ] Audit authoritative fields and zero-match failure remain unchanged.
- [ ] Multiple exact candidate matches read only the first winner's body.
- [ ] Candidate body requires exact str; blank produces no provenance.
- [ ] All six frozen short-text vectors and code-point/NUL behavior are exact.
- [ ] Candidate abstract is never accessed or stored.
- [ ] Unusable-first/valid-second uses only the second record with extraction counts `0,1`.
- [ ] URL-only-first/valid-second produces no provenance with extraction counts `0,0`.
- [ ] Visited-only, duplicate losers and capped sources contribute no blocks.
- [ ] Candidate URL priority prevents ordinary loser backfill.
- [ ] Source cap, URL first-wins and candidate-ID first-wins precede final binding.
- [ ] Provenance IDs are assigned with final source IDs and preserve source order.
- [ ] Incremental normalization exactly matches CRLF/CR replacement plus outer strip.
- [ ] Chunking uses exact 16,384 windows and bounded per-window strip.
- [ ] Empty windows, Unicode code points and final partial blocks behave exactly.
- [ ] One source may consume all 64 blocks; aggregate limits stop globally.
- [ ] Exhaustion causes zero later iterator/key/attribute/property/method access or probing.
- [ ] The existing budget matrix's hostile tail records zero dynamic accesses.
- [ ] Only newly allocated bounded exact strings enter the DTO/checkpoint.
- [ ] No complete text or original object is retained on success/fixed-error/DTO/checkpoint surfaces.
- [ ] Fixed contract errors keep exact type/text and cause/context `None`.
- [ ] Fixed-error traceback, closures, state, tasks and metadata retain no sensitive input.
- [ ] Raw runtime exceptions retain their original instance and traceback unchanged.
- [ ] Cancellation bare-propagates the same instance and args without traceback cleanup.
- [ ] Empty context and all existing failure priorities remain unchanged.
- [ ] Downstream adapters, prompts, boundaries and outputs remain unchanged.
- [ ] Existing construction sites pass without modification via the default.
- [ ] Tests remain in the two existing files and contain no prohibited low-value cases.
- [ ] Targeted state and research-evidence tests pass.
- [ ] One necessary academic-writing regression passes without multiple file orders.
- [ ] `git diff --check` passes and the staging area remains empty.
- [ ] No network, real external service, dependency installation, staging or commit occurred.

## 16. Mandatory stop conditions

Implementation must stop and request a revised approved specification if:

- any fifth implementation file must change;
- a node, graph, facade, event, phase, error, adapter Protocol, downstream prompt,
  citation allowlist, package export, dependency, or product path must change;
- old schema-version-1 checkpoints cannot restore through the frozen default;
- final source and provenance cannot be produced from the same deterministic
  snapshot and winner records;
- abstract or a losing/visited/URL-only source must be read to populate text;
- the frozen incremental algorithm cannot avoid an unbounded normalized copy;
- a new Retriever, scraper, compressor, LLM, Provider, network or external call
  is required; or
- implementation exposes another internal contradiction or unreachable test.
