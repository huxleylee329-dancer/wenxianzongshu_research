# Academic Writing Milestone 3.7: Off-Graph Deterministic Section Merger

Status: **Approved and frozen**

规范已批准并冻结，仅授权冻结边界内实施；Implementation acceptance在实施完成并验证前保持未勾选。

## 1. Goal

Milestone 3.7 adds one synchronous, deterministic, off-graph merger. It accepts
one exact approved academic-workflow state and one exact tuple containing the
strict section drafts returned by the Milestone 3.6 boundary, validates the
complete input before concatenation, and returns one strict merged-draft DTO:

```text
AcademicWorkflowState(outline_approved/completed, decision=approve)
             + exact tuple[WorkflowSectionDraft, ...]
                              |
                              v
                       merge_sections()
                              |
                              v
                    WorkflowMergedDraft
```

The operation is synchronous and pure deterministic computation. It performs
no LLM, Config, Provider, completion, graph, checkpoint, legacy-report,
network, filesystem, persistence, review, correction, or reference generation.

## 2. Baseline, precedence, and narrow supersession

The direct frozen baselines are:

```text
specs/academic-writing-milestone-3.5-section-writer-adapter.md
specs/academic-writing-milestone-3.6-section-writer-sequence.md
```

Milestone 3.7 narrowly supersedes only their roadmap/non-goal reservation of a
future deterministic ordered merge. It does not change either writer, the
sequence, their prompts, their result validation, or any earlier state, DTO,
graph, event, facade, checkpoint, package-export, failure, or cancellation
contract. The approved graph state remains terminal and is consumed off graph.

The merger does not claim that an input draft was produced by Milestone 3.5,
by a production LLM, or by Milestone 3.6. Python provenance is not mechanically
provable. Trust is established only by exact state restoration, exact internal
draft extraction, fresh strict DTO reconstruction, canonical round trips,
identity/order binding, and fresh citation validation defined below.

## 3. Exact future implementation boundary

After explicit approval, implementation may add exactly:

```text
gpt_researcher/workflows/academic_writing/section_merger.py
tests/test_academic_writing_section_merger.py
```

No existing file may be modified. In particular, implementation must not
modify `state.py`, `section_writer.py`, `section_writer_sequence.py`, `graph.py`,
`nodes.py`, `adapters.py`, any event or facade, any package initializer, any
frozen specification, test, dependency, lock file, backend, frontend, legacy
report path, or generated file. `WorkflowMergedDraft` is defined only in the
new merger module and is not inserted into application state or re-exported.

## 4. Complete public surface and exact synchronous signature

The complete module public surface is exactly:

```python
__all__ = (
    "WorkflowMergedDraft",
    "merge_sections",
)
```

Every other definition introduced by the module has a leading underscore.
There is no public or private merger class, constructor, Protocol, adapter,
factory, client, async function, generator, iterator, or context manager.

The sole operation is exactly:

```python
def merge_sections(
    state: AcademicWorkflowState,
    drafts: tuple[WorkflowSectionDraft, ...],
) -> WorkflowMergedDraft: ...
```

It is an ordinary synchronous function. Calling it completes the entire
operation before returning or raising.

## 5. Strict module-local output DTO

### 5.1 Exact fields

```python
class WorkflowMergedDraft(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    outline_id: Literal["outline:000001"]
    section_ids: tuple[str, ...]
    attempt: Literal[1]
    content: str
```

Before field validation, direct Python input accepts only an exact
`WorkflowMergedDraft` or exact built-in `dict`. Mapping keys must be exactly
the four declared fields. `outline_id`, every `section_ids` member, and
`content` require exact `str`; `section_ids` requires exact built-in `tuple`;
and `attempt` requires exact `int` equal to `1`, rejecting bool and every other
value. JSON input is accepted only through `model_validate_json()`.

`outline_id` is the existing exact literal. `section_ids` contains one through twelve
exact, nonblank, code-point-unique strings in preserved tuple order. The DTO
does not freeze a six-digit ID grammar; the merger binds every value to the
strict restored outline. `content` is exact, nonblank, is not normalized, and
contains at most 359,538 Unicode code points. No Unicode normalization,
line-ending conversion, stripping, Markdown parsing, or citation repair occurs
in the DTO.

### 5.2 Canonical DTO representation

The sole canonical representation is:

```python
json.dumps(
    merged.model_dump(mode="json"),
    ensure_ascii=False,
    allow_nan=False,
    sort_keys=True,
    separators=(",", ":"),
).encode("utf-8")
```

Construction must complete strict JSON restoration and require exact DTO
type/equality, recursive JSON value/type equality, and canonical-byte equality.

## 6. Complete preflight before concatenation

No title, separator, body, or partial output string is concatenated until all
of these checks have completed in order:

1. require `type(state) is AcademicWorkflowState`;
2. require `type(drafts) is tuple`;
3. canonically dump the complete state with the frozen JSON formula, strictly
   restore it, and require exact recursive type, model, and byte equality;
4. require `phase == "outline_approved"` and `status == "completed"`; strict
   restoration remains the sole proof of `decision=approve`, digest binding,
   and artifact-reference consistency;
5. require one through twelve restored outline sections;
6. require `len(drafts) == len(outline.sections)` before reading a member;
7. traverse outline sections and tuple positions once, without sorting;
8. statically extract and freshly reconstruct every untrusted draft as
   Section 7 specifies;
9. require each reconstructed draft's `outline_id`, `section_id`, and exact
   integer attempt `1` to equal the current restored outline expectations;
10. require the resulting section-ID tuple to be exact, nonblank, unique, and
    equal to the outline's original section-ID tuple;
11. for every target in outline order, execute the complete frozen 3.5
    input-side validation and projected-source admission algorithm in Section
    8, retaining its exact allowed-source-ID tuple;
12. parse and validate every reconstructed body under Section 9 against that
    target's allowed tuple; and
13. return only a pure-primitive plan containing the exact outline ID and an
    exact tuple of `(section_id, title, content)` primitive tuples.

The isolated preflight helper catches every ordinary exception and returns
only a successful primitive plan or one private failure marker. It never
raises a contract error through its boundary. The public frame deletes the
original state and draft tuple before inspecting that result. A failure for
the final draft or final citation therefore performs zero concatenation.

## 7. Trusted reconstruction of untrusted section drafts

Every tuple member remains untrusted, including when
`type(member) is WorkflowSectionDraft`. Validation exactly mirrors the frozen
Milestone 3.6 trusted extraction surface:

1. require exact `WorkflowSectionDraft` type before reading a member;
2. use only `object.__getattribute__` to read `__dict__`,
   `__pydantic_fields_set__`, `__pydantic_extra__`, and
   `__pydantic_private__`;
3. require exact built-in `dict`, exact built-in `set`, and `None`/`None`;
4. require the dict key tuple and exact-string field set to contain exactly
   `outline_id`, `section_id`, `attempt`, and `content`;
5. read values only with `dict.__getitem__`; require exact `str` for the three
   string fields and exact `int` equal to `1` for attempt;
6. copy those four primitives and release the original member and internal
   containers before comparison or construction;
7. construct a fresh trusted `WorkflowSectionDraft` from the copied values;
8. reject if construction normalizes any copied value; and
9. complete its canonical JSON restoration, recursive-type equality, exact
   model equality, and byte equality.

No path calls a method, equality, `repr`, `str`, iterator, dynamic `getattr`,
property, or descriptor on the writer-supplied member. A subclass, shadowed
method, extra/private state, hostile key/value, string subclass, wrong attempt,
blank/overlong content, wrong ID, or malformed internal shape becomes the one
fixed merger failure without executing hostile code.

## 8. Exact reconstruction of the 3.5 projected source allowlist

### 8.1 Frozen constants and validation order

The merger locally mirrors all input-side 3.5 Sections 6-8 constants and
ordering: query 4,096; language 128; questions 1-3, item 512, aggregate 1,024;
context 8, item 4,096, aggregate 24,576; sources 24, projected title 256; and
canonical user message 65,536. It requires report type `research_report`,
report source `web`, and exact topic/query equality before projection.

For every outline target it projects the same context prefixes, complete
outline, research questions, language, root topic, target ID, and tentative
whole source objects. The payload keys and sole JSON encoding are exactly the
3.5 Section 8.4 formula. A tentative source that would make the payload exceed
65,536 is removed; traversal stops and no later source is inspected. The exact
ordered admitted `source_id` tuple is that target's sole allowlist.

The merger must not import or call a private 3.5 or 3.6 helper. It imports no
writer module in production. Duplication is deliberately local to keep the
two-file boundary; any ambiguity or behavioral drift is a mandatory stop.

### 8.2 Public-boundary golden consistency matrix

Tests use one formula-built parameter matrix and the public 3.5 adapter with an
injected fake client as the sole behavioral oracle. Every row captures and
parses the adapter's canonical user message, verifies the exact projected
source objects and IDs, supplies the matching marker/citation response, and
then requires the merger to make the same marker-membership decision. All rows
remain cases of the existing golden test function; none introduces a test
function, private-helper import, source/AST comparison, or new test framework.
The exact rows are:

- **count cutoff:** one otherwise minimal valid one-section state uses the
  following exact source formula:

  ```python
  sources = tuple(
      WorkflowEvidenceSource(
          source_id=f"evidence-source:{i:06d}",
          order=i,
          title=f"S{i}",
          url=f"https://e.test/{i}",
          candidate_id=None,
      )
      for i in range(1, 26)
  )
  ```

  Its canonical message is mechanically asserted below the cap. The expected
  projected IDs are exactly
  `tuple(f"evidence-source:{i:06d}" for i in range(1, 25))` in input order and
  the titles are exactly `tuple(f"S{i}" for i in range(1, 25))`; source 25 is
  absent. Both public 3.5 behavior and the merger accept a marker for source 24
  and reject one for source 25;
- **prompt cutoff:** the exact frozen 3.5 Section 8.5 65,536 vector, for which
  tentatively adding source 1 exceeds the cap. The expected projected ID/title
  tuples are both empty. Both accept marker-free content and reject a marker
  for source 1. The paired 65,537 state is rejected before body validation;
- **257-code-point Unicode title:** two rows inherit every frozen Section 8.5
  value except that `input_sources` contains only source 1 with title
  `"\u00e9" * 257` and URL `https://example.test/1`. They use:

  ```python
  unicode_admit_context = ("\0" * 885) + ("A" * 3211)
  unicode_reject_context = ("\0" * 885) + "\n" + ("A" * 3210)
  ```

  With no projected source their exact canonical lengths are respectively
  65,200 and 65,201. Correct code-point-prefix projection produces title
  `"\u00e9" * 256` and lengths 65,536/65,537. Thus the admit row projects
  exactly `("evidence-source:000001",)` and `("\u00e9" * 256,)`, exposes that
  literal unescaped title in the captured `ensure_ascii=False` message, and
  accepts its marker; the reject row projects `()`/`()`, accepts marker-free
  content, and rejects that marker. The paired lengths for erroneous
  255-code-point, untruncated 257,
  `ensure_ascii=True`, and 256-UTF-8-byte-prefix implementations are exactly
  65,535/65,536, 65,537/65,538, 66,816/66,817, and 65,408/65,409. The two rows
  therefore distinguish and kill all four drifts;
- **legal duplicate titles:** one minimal one-section state uses query/root
  topic `r`, language `l`, question `q`, context `c`, outline title `o`, section
  title `s`, brief `b`, and these two exact sources:

  ```python
  ("evidence-source:000001", 1, "DUP", "https://e.test/1")
  ("evidence-source:000002", 2, "DUP", "https://e.test/2")
  ```

  Candidate IDs are `None`. The exact canonical message length is 430; expected
  projected IDs are `("evidence-source:000001", "evidence-source:000002")` and
  titles are `("DUP", "DUP")` in that order. Public 3.5 and the merger accept
  content citing both. Deduplication by title loses source 2 and is therefore
  mechanically detected;
- **empty sources:** the same minimal state with `sources=()` has exact canonical
  length 275, projected IDs/titles `()`/`()`, and accepts marker-free content in
  both public 3.5 and the merger. Any marker is rejected as unknown; rejecting
  the empty source collection itself fails this row; and
- **first-wins/cutoff interaction:** inherit the frozen Section 8.5 values but
  use first context `("\0" * 125) + ("A" * 3971)` and exactly two sources with
  common title `DUP`. That title is the deliberately shared hypothetical dedup
  identity; their source IDs and URLs remain distinct as strict state requires.
  Source 1 has URL
  `"https://e.test/" + ("X" * 4081)` (exactly 4,096 code points); source 2 has
  URL `https://e.test/2`. Their identities/orders are the derived 1/2 values and
  candidate IDs are `None`. The empty-source canonical length is 61,400;
  source 1 tentatively makes 65,557, while source 2 alone would make 61,477.
  The sole expected projected ID/title tuples are nevertheless `()`/`()`: the
  first source is removed and traversal stops. Both oracle and merger accept
  marker-free content and reject markers for either source. Continuing after
  the first overflow, backfilling source 2, choosing the shorter duplicate, or
  changing first-wins order admits source 2 and fails this row.

These cases compare captured public canonical messages and observable
acceptance/rejection, not private data or error-class identity. They do not
copy the full historical 3.5 test matrix.

## 9. Exact citation revalidation

The only recognized syntax is the literal marker:

```text
[[cite:<source_id>]]
```

Scan each trusted reconstructed content left to right using literal prefix
`[[cite:` and the next literal suffix `]]`. Missing suffix, empty value,
nested bracket text, or any `[`/`]` outside a successfully parsed marker fails.
Every value must equal one ID in that section's reconstructed allowlist code
point for code point. A source excluded by count or prompt budget is unknown.

Repeated markers are allowed. The validator builds the stable unique ID tuple
in first-appearance order; later repeats do not append. The tuple is a private
validation projection and is not added to `WorkflowMergedDraft`. Content
containing the exact literal substring `://` fails. `www` text, bare domains,
DOI text, email-like text, and unmarked natural-language attribution are not
recognized. No marker is deleted, inserted, reordered, normalized, or repaired.

This is syntax/membership validation only. It makes no claim that a citation
supports nearby prose and performs no CitationReviewer work.

Only reconstructed draft bodies are scanned. Outline titles are trusted
structural data from the strictly restored state; their characters are
preserved in generated headings and are not reclassified as body citations or
URLs. A title containing brackets or `://` does not weaken or change the body
allowlist contract.

## 10. Unique Markdown template and opaque-body policy

For each outline position, construct exactly:

```python
"## " + exact_outline_section_title + "\n\n" + exact_draft_content
```

Join section strings with exactly `"\n\n"`. The complete result therefore:

- begins with `## ` and has no preceding character;
- has exactly two LF code points between one section body's final code point
  and the next `## `;
- has no merger-added trailing LF or other trailing character;
- uses LF only for merger-owned separators;
- preserves titles and bodies code point for code point; and
- performs no Unicode or line-ending normalization, escaping, trimming,
  parsing, rendering, or locale operation.

Markdown headings inside a draft body are allowed and treated as opaque body
text. They are neither rejected nor interpreted and are preserved exactly.
The merger does not escape them or silently rewrite heading levels. The output
contains no document-level title, table of contents, introduction, conclusion,
reference list, or metadata rendering.

## 11. Exact merged-content maximum

For `n` sections, the template length is exactly:

```text
sum(title lengths) + sum(content lengths) + 7*n - 2
```

The per-draft DTO cap gives `sum(content lengths) <= 24,576*n`. Section titles
have no direct DTO cap, so their real bound is derived from the mandatory 3.5
canonical prompt. With all other legal projected values at their one-code-point
minimum, no sources, distinct one-code-point titles, and fixed derived IDs, the
minimal canonical payload length is:

```text
B(n) = 209 + 66*n + max(0, n - 9)       for 1 <= n <= 12
```

`B(n)` already includes `n` title code points. Therefore:

```text
max_title_sum(n) = n + 65,536 - B(n)
max_merged(n) = 65,325 + 24,518*n - max(0, n - 9)
```

The latter is strictly increasing for `1..12`; its unique maximum is:

```text
max_title_sum(12) = 64,544
max_merged(12) = 359,538
```

The reachable maximum vector uses 12 sections, empty sources, one-character
query/root topic, language, question, context block, outline title, and briefs;
titles 2-12 are the distinct one-character strings `B` through `L`, while
title 1 is `"A" * 64_533`. Its 3.5 canonical payload is exactly 65,536 for
every target. Each trusted draft content is `"C" * 24_576`, contains no marker,
bracket, or `://`, and is individually valid. The merged length is exactly:

```text
(12 * 24,576) + 64,544 + (7 * 12 - 2) = 359,538
```

No legal merger-input max-plus-one vector exists. One additional title code point makes the
canonical prompt 65,537; one additional body code point violates the existing
24,576 DTO cap; and a thirteenth section violates the frozen sequence count.
Thus 359,539 is unreachable through `merge_sections()` from otherwise legal
inputs and no artificial merger-input behavior case is required. Direct
construction of the public output DTO with 359,539 content remains a reachable
DTO-validation case and rejects under its independent 359,538 field cap.

## 12. Deterministic result construction

Only after complete preflight does a separate synchronous isolation helper
receive the primitive plan. It builds section strings in outline order, joins
them once with the frozen separator, checks the formula-derived length, and
constructs a fresh `WorkflowMergedDraft` from:

```python
WorkflowMergedDraft(
    outline_id=approved_outline_id,
    section_ids=approved_section_ids,
    attempt=1,
    content=merged_content,
)
```

It returns only canonical merged-DTO bytes or a private marker and releases the
plan, section strings, full merged content, DTO, and ordinary exception before
exiting. A final no-sensitive-input helper restores the DTO from those bytes,
revalidates the canonical representation, deletes the bytes on failure, and
returns the exact DTO or a marker. Only a finishing helper that has never
received state, drafts, plan, content, or DTO bytes may raise the fixed error.

## 13. Fixed failure and synchronous information safety

The module defines exactly one private error:

```python
class _SectionMergerError(RuntimeError):
    pass
```

Its sole text is:

```text
section merger failed
```

Every ordinary input, snapshot, draft, allowlist, citation, template,
construction, canonicalization, or internal failure becomes that error. It
contains no dynamic type, position, ID, title, content, marker, source, state,
or underlying exception text. It has `__cause__ is None`, `__context__ is
None`, and `__suppress_context__ is False`.

The public function first transfers state/drafts to a marker-only isolated
preflight, then deletes both live inputs. It deletes every primitive plan or
canonical output byte projection before invoking the safe error finisher.
Fixed-error arguments, attributes, traceback locals, exception chains, closure
cells, and module globals must not reach the original state, original draft
tuple, any original draft/member/internal container, the primitive plan, any
partial section string, complete merged content, canonical DTO bytes, or raw
ordinary exception. No exception is logged, stringified, wrapped with `from`,
or preserved. This classification applies to `merge_sections()`; ordinary
direct `WorkflowMergedDraft` validation uses standard Pydantic validation.

## 14. Synchronous execution and absence of cancellation semantics

There is no `await`, coroutine, task, callback, thread, process, generator, or
external call. The milestone freezes no `asyncio.CancelledError` behavior and
makes no cancellation, timeout, interruption, concurrent mutation, or partial
progress promise. Repeating the pure function with equal canonical inputs
produces an equal canonical DTO; no external cost or idempotency mechanism
exists.

## 15. Import and side-effect isolation

At module scope production imports only standard-library JSON/typing,
Pydantic primitives required for its module-local DTO, and
`AcademicWorkflowState`/`WorkflowSectionDraft` from `state.py`. It does not
import `section_writer`, `section_writer_sequence`, graph, nodes, adapters,
Config, LLM helpers, GPTResearcher, legacy report generation, backend,
frontend, or multi-agents.

The module owns no mutable call state, cache, logger, handler, environment
read, filesystem operation, database, socket, network, subprocess, task,
queue, lock, semaphore, persistence, or checkpoint behavior.

## 16. Test scope and behavior matrix

The single new test file remains compact and uses only strict local DTOs and
the public 3.5 adapter with injected fakes for the one golden parameter matrix:

1. exact two-name `__all__`, DTO and function signatures combined with real
   DTO/merge behavior, not a symbol-existence-only test;
2. strict/frozen/extra-forbid DTO mapping, tuple, string, attempt, reachable
   359,538/359,539 direct-content bounds, and canonical JSON behavior in one
   parameterized matrix;
3. exact state, approved shape, 1/12/13 counts, exact tuple, length, order,
   identity, and state/draft nonmutation preflight behavior;
4. hostile/subclass/shadow/extra/private/string-subclass draft cases proving
   no untrusted dynamic operation executes;
5. the Section 8.2 public-boundary golden matrix, including the original
   count/prompt cutoffs plus Unicode truncation/encoding, duplicate-title,
   empty-source, and first-wins/overflow rows, with no private helper import or
   full 3.5 matrix duplication;
6. malformed/unknown/repeated/first-order/marker-free citation behavior,
   forbidden bracket, and literal `://` in one parameterized matrix;
7. exact one- and twelve-section Markdown, Unicode, LF, opaque inner heading,
   no outer newline, and code-point preservation;
8. the formula-built reachable 359,538 maximum and the arithmetic proof that
   359,539 is unreachable from otherwise legal inputs;
9. fixed error type/text/cause/context and one targeted identity inspection of
   merger traceback locals/closures, without a reusable historical safety
   walker; and
10. deterministic repeated calls, exact output equality/canonical bytes, and
    zero input mutation or external side effect.

Tests do not retain the initial collection failure. They contain no AST-only,
symbol-only, import-registry, interpreter-restoration, generic safety-walker,
network, Config, Provider, real completion, graph, checkpoint, filesystem,
subprocess, or multi-order test. Parameterized cases sharing a branch remain
in one function and every case maps to a behavior or acceptance item.

During implementation run only the new merger test until green. Final related
verification runs exactly once in this order:

```text
tests/test_academic_writing_section_merger.py
tests/test_academic_writing_section_writer_sequence.py
tests/test_academic_writing_section_writer.py
```

No full academic-writing suite or file-order permutation is required.

## 17. Legacy and existing-entry boundary

`add_references()`, `generate_report()`, `GPTResearcher.write_report()`, and
`ReportGenerator.write_report()` are not reusable merger contracts. They use
URL sets, mutable legacy state, LLM prompts, streaming, retry/fallback,
exception swallowing, or permissive strings. Milestone 3.7 does not import,
call, wrap, monkeypatch, or modify them.

The new production module also does not call either writer. It consumes only
the frozen public DTO boundary supplied by its caller.

## 18. Non-goals

Milestone 3.7 does not implement:

- CitationReviewer, semantic claim-support review, correction, or traceability;
- reference-list generation, URL rendering, citation renumbering, or footnotes;
- introduction, conclusion, document title, table of contents, abstract,
  bibliography, final composition, export, or `FinalEditor`;
- content rewrite, normalization, heading correction, deduplication, repair,
  fallback, regeneration, or attempt 2+;
- LLM, Config, Provider, completion, GPTResearcher, legacy report behavior, or
  external service;
- graph, node, edge, state field, existing DTO, event, phase, status, error
  code, facade, checkpoint, persistence, backend, frontend, or package export;
- parallelism, `Send`, subgraph, task, async execution, streaming, or progress;
- authentication, authorization, database, ReportStore, billing, or UI; or
- any modification to frozen Milestones 3.0-3.6 behavior.

## 19. Mandatory stop conditions

Implementation must stop for a revised, explicitly approved Draft if:

- either approved implementation file is insufficient or any existing/third
  implementation file must change;
- a state field, existing DTO, graph, event, facade, package export, dependency,
  Config value, backend/frontend, legacy path, or frozen spec must change;
- output requires an LLM, external service, async operation, persistence,
  checkpoint, parallelism, review, correction, or reference generation;
- exact input drafts cannot be safely reconstructed without invoking an
  untrusted instance operation;
- the complete 3.5 input/source-admission algorithm cannot be reproduced
  unambiguously without a private helper import;
- any public golden-matrix row differs from current 3.5 behavior;
- complete preflight cannot finish before the first body concatenation;
- body text cannot be preserved code point for code point under the template;
- the exact 359,538 derivation or reachable maximum vector is false;
- fixed failure isolation cannot make all live input, plan, partial/full text,
  DTO bytes, and underlying exceptions unreachable;
- testing requires real Config, LLM, Provider, GPTResearcher, network,
  subprocess, graph, checkpoint, persistent output, or global import mutation;
  or
- this Draft conflicts internally or with an unchanged frozen contract.

No implementer may resolve a stop by weakening validation, accepting arbitrary
evidence IDs, importing a private helper, claiming provenance, returning a
partial merge, altering body text, swallowing an exception, or expanding scope.

## 20. Draft approval checklist

- [ ] Status is Draft and implementation is not authorized.
- [ ] The exact two-added-file implementation boundary is approved.
- [ ] Every existing source, test, specification, initializer, and dependency remains unchanged.
- [ ] The narrow supersession authorizes only synchronous deterministic ordered merge.
- [ ] The exact two-name public surface and sole synchronous function signature are approved.
- [ ] The module-local strict/frozen/JSON-compatible DTO and four exact fields are approved.
- [ ] DTO exact dict/tuple/string/integer rules and bool/non-one rejection are approved.
- [ ] DTO section-ID order/uniqueness, content preservation, and 359,538 cap are approved.
- [ ] No state, existing DTO, graph, event, facade, checkpoint, or package export changes.
- [ ] Exact state and tuple types precede every member read or concatenation.
- [ ] Canonical strict state restoration remains the sole approval/reference proof.
- [ ] One through twelve sections and exact draft/outline count equality are approved.
- [ ] Every untrusted draft uses only the frozen static Pydantic extraction surface.
- [ ] Fresh trusted draft reconstruction and canonical round-trip validation are approved.
- [ ] No draft provenance claim is made.
- [ ] Complete 3.5 input and projected-source allowlist reconstruction is approved.
- [ ] No private 3.5/3.6 helper import or writer call is permitted.
- [ ] The original count-cutoff and prompt-cutoff public golden rows are approved.
- [ ] The paired Unicode rows distinguish 255/256/257 code-point, `ensure_ascii`, and UTF-8-byte truncation behavior.
- [ ] Legal duplicate-title and empty-source public golden rows are approved.
- [ ] The first-wins overflow row forbids continued traversal, backfill, shorter-duplicate selection, or reordering.
- [ ] Exact marker parsing, allowlist membership, stable first occurrence, and repeated markers are approved.
- [ ] Malformed/unknown markers, forbidden brackets, and literal `://` fail without repair.
- [ ] Natural-language attribution and broader URL recognition remain excluded.
- [ ] The exact per-section template, two-LF join, and no outer newline are approved.
- [ ] Opaque inner Markdown headings are allowed and preserved without rewriting.
- [ ] Unicode, titles, and bodies are preserved code point for code point.
- [ ] The exact length formula, B(n), 359,538 maximum, and reachable vector are approved.
- [ ] 359,539 is unreachable from legal merger inputs and needs no artificial merge-input test; direct DTO cap validation remains reachable.
- [ ] All preflight completes before any concatenation.
- [ ] Primitive-plan construction and isolated output construction are approved.
- [ ] The one fixed private error/text and null cause/context are approved.
- [ ] Error traceback/locals/closures cannot reach inputs, plan, partial/full text, or DTO bytes.
- [ ] Synchronous execution has no cancellation contract, async primitive, or external cost.
- [ ] The concise behavior-focused test matrix and single final related order are approved.
- [ ] Every non-goal and mandatory stop condition is approved.
- [x] This specification received explicit approval before implementation began.

## 21. Implementation acceptance checklist

- [ ] Only the exact two approved implementation files were added.
- [ ] No existing file, frozen specification, initializer, dependency, or lock file changed.
- [ ] The module exports exactly `WorkflowMergedDraft` and `merge_sections` in order.
- [ ] Every other new production definition is private and no merger class/Protocol/factory exists.
- [ ] The public merge operation is synchronous with the exact approved signature.
- [ ] `WorkflowMergedDraft` is strict, frozen, extra-forbid, and JSON-compatible.
- [ ] DTO direct Python input requires exact dict/tuple/strings and exact integer one.
- [ ] DTO JSON restoration, recursive types, exact equality, and canonical bytes are exact.
- [ ] DTO outline ID, 1-12 unique ordered section IDs, content, and cap are exact.
- [ ] Exact input state/tuple checks and canonical state restoration occur before member reads.
- [ ] Approved/completed shape and one-through-twelve outline sections are required.
- [ ] Draft count exactly equals outline count before reading a draft member.
- [ ] Every input member is statically extracted through exact dict/set/None/None internals.
- [ ] Hostile methods, properties, equality, repr, iterators, and string subclasses never execute.
- [ ] Every copied draft is freshly reconstructed and canonically validated before use.
- [ ] Every outline ID, section ID, order position, uniqueness rule, and attempt matches exactly.
- [ ] Complete 3.5 query/language/question/context/outline/source/prompt validation runs for every target.
- [ ] Whole-source admission and the exact target-specific allowed-source tuple match 3.5.
- [ ] No private writer/sequence helper is imported or called.
- [ ] Count-cutoff and prompt-cutoff public golden outcomes match the current 3.5 adapter.
- [ ] Unicode-title projected content, encoding, boundary membership, and captured canonical lengths match the current 3.5 adapter.
- [ ] Duplicate-title sources remain distinct and ordered, while empty sources remain valid with an empty allowlist.
- [ ] First-source overflow stops traversal and excludes the otherwise admissible shorter duplicate.
- [ ] Every body is rescanned for exact markers before concatenation.
- [ ] Unknown/malformed markers, stray brackets, and literal `://` use the fixed failure.
- [ ] Repeated markers and stable first-appearance order match 3.5 without body mutation.
- [ ] Marker-free content, `www`, bare domains, and natural-language attribution remain mechanically unclassified.
- [ ] Complete preflight returns only a primitive plan or marker before any string merge.
- [ ] The template is exact, uses two LF between sections, and adds no outer newline.
- [ ] Inner headings remain opaque and every title/body code point is preserved.
- [ ] The B(n) formula and all `n=1..12` maxima are mechanically reproduced.
- [ ] The legal 12-section vector reaches exactly 359,538 output code points.
- [ ] No reachable legal input produces 359,539 code points.
- [ ] Output construction and restoration release plan, partial/full text, DTO, and canonical bytes before fixed failure.
- [ ] The fixed error has exact type/text, null cause/context, and no dynamic data.
- [ ] Fixed-error reachability excludes state, drafts, member internals, plan, text, bytes, and raw exceptions.
- [ ] Equal canonical inputs produce equal DTOs and canonical bytes without mutating inputs.
- [ ] Production imports no writer, sequence, graph, Config, LLM, Provider, legacy, backend, or frontend module.
- [ ] Production performs no async, task, I/O, environment, logging, persistence, checkpoint, or external work.
- [ ] Tests contain no retained initial-red, AST-only, symbol-only, generic safety walker, import registry, or unrelated matrix.
- [ ] Focused merger tests pass offline.
- [ ] The single final 3.7+3.6+3.5 regression passes in the approved order.
- [ ] No test invokes real Config, LLM, Provider, GPTResearcher, network, subprocess, graph, checkpoint, or file output.
- [ ] `git diff --check` passes, staging is empty, and only the approved Draft existed before implementation.
- [ ] Implementation remains unstaged and uncommitted before review.
