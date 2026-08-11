# Item Query Understanding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make failed and uncertain item searches explainable and safe: exact/alias searches remain fast, known ambiguity uses structured choices, fuzzy matches require confirmation, partial understanding yields parser-validated suggestions, and Qwen is called only when deterministic understanding cannot safely handle the request.

**Architecture:** Preserve `core.route_deterministically()` and the existing parsed-stat clarification path as the first source of truth. Add a low-level item-entity matcher shared with reconstruction, then a frontend-neutral `ItemQueryUnderstanding` policy layer that classifies a failed simple item query as executable, clarifiable, confirmable, suggestible, or fallback-only. `SearchService` owns orchestration and deterministic revalidation; Discord stores only minimal pending interpretation state and renders buttons/labels from structured outcomes.

**Tech Stack:** Python 3, stdlib `dataclasses`/`unittest`, existing `rapidfuzz`, existing SQLite-backed `ItemRepository`, existing Discord.py UI components, existing Qwen/Ollama fallback.

## Global Constraints

- Keep the existing deterministic parser as the fast path and source of truth.
- Only fully resolved deterministic meaning may execute.
- Exact matches and intentional aliases may execute without confirmation.
- Known semantic ambiguity must never auto-execute.
- Fuzzy matches are confirmation-only and must never silently execute.
- Unknown meaningful tokens must not silently disappear on an auto-execution path.
- Do not add new multi-stat Boolean/relation semantics in this iteration.
- Qwen may interpret intent/terms, but may not provide trusted item facts, choose result rows, or generate arbitrary SQL.
- Any Qwen-derived search still requires deterministic validation and explicit user confirmation.
- Deterministic clarification, fuzzy confirmation, and safe suggestion must not create failed-query history; record failure only when the service actually enters Qwen fallback.
- Human-readable labels are primary UI; canonical syntax such as `cr wp xtal` is secondary guidance.
- A single resolved ambiguity/correction may execute after revalidation; two or more independent interpretation choices require one final Search/Edit confirmation.
- A new Discord query supersedes pending clarification state by creating a new session generation.
- Preserve the existing 24-token reconstruction safety bound.
- Do not add boss/skill/domain abstractions, build-role semantics, persistent telemetry, or a general confidence-score engine.

---

## File Structure

### Create

- `toram_search/item_query_entities.py` — low-level normalized token and item-filter matching shared by reconstruction and the understanding layer. No service, Discord, or LLM policy lives here.
- `toram_search/understanding.py` — `ItemQueryUnderstanding` data model, reason codes, conservative stat/filter recognition, fuzzy-confirmation policy, and confirmed-choice application.
- `tests/test_item_query_entities.py` — focused characterization tests for shared filter matching, overlap handling, and conservative fuzzy filter candidates.
- `tests/test_item_query_understanding.py` — unit tests for decisions, reason codes, unresolved tokens, ambiguity order, fuzzy confirmation, and multi-correction state.

### Modify

- `toram_search/reconstruction.py` — reuse the shared entity matcher while preserving `try_reconstruct_simple_search()` and `try_suggest_query()` behavior.
- `toram_search/session.py` — add immutable `PendingItemSearch` state; keep `FailedQueryContext` separate.
- `toram_search/service.py` — insert deterministic understanding before Qwen, expose continuation/final-confirmation methods, and remove post-Qwen suggestion generation from the live service flow.
- `discord_bot.py` — store pending item understanding in the Discord session, render recognized/unresolved parts, and add clarification/confirmation/suggestion controls.
- `tests/test_query_reconstruction.py` — preserve reconstruction compatibility and the 24-token guard.
- `tests/test_search_service.py` — cover orchestration order, no-Qwen paths, failed-history rules, continuation, revalidation, and Qwen confirmation boundary.
- `tests/test_discord_bot.py` — cover human-readable embeds, buttons, pending-state lifecycle, and final Search/Edit behavior.

---

### Task 1: Extract Shared Item-Filter Entity Matching Without Changing Reconstruction Behavior

**Files:**
- Create: `toram_search/item_query_entities.py`
- Create: `tests/test_item_query_entities.py`
- Modify: `toram_search/reconstruction.py`
- Test: `tests/test_query_reconstruction.py`

**Interfaces:**
- Produces:
  - `QueryToken(index: int, text: str, normalized: str)`
  - `ItemFilterMatch(typed_text: str, token_indexes: tuple[int, ...], phrase: ItemFilterPhrase, canonical_phrase: str, match_kind: Literal["exact", "fuzzy"], score: float)`
  - `FilterMatchStatus = Literal["unique", "none", "ambiguous"]`
  - `ExactFilterMatches(status: FilterMatchStatus, matches: tuple[ItemFilterMatch, ...])`
  - `tokenize_item_query(raw_query: str) -> tuple[QueryToken, ...]`
  - `find_exact_item_filter_matches(tokens: tuple[QueryToken, ...], available_item_types: set[str]) -> ExactFilterMatches`
  - `remaining_tokens(tokens: tuple[QueryToken, ...], consumed_indexes: tuple[int, ...]) -> tuple[QueryToken, ...]`
- Consumes: `ITEM_WORD_ALIASES`, `normalize_stat_text`, `ItemFilterPhrase`, `list_item_filter_phrases`.

- [ ] **Step 1: Write failing shared-matcher tests**

Create `tests/test_item_query_entities.py` with focused cases that characterize the current reconstruction semantics:

```python
import unittest

from toram_search.item_query_entities import (
    find_exact_item_filter_matches,
    remaining_tokens,
    tokenize_item_query,
)


TYPES = {
    "Weapon Crysta",
    "Enhancer Crysta (Red)",
    "Armor",
    "Bow",
}


class ItemQueryEntityTests(unittest.TestCase):
    def test_item_word_alias_is_normalized_without_becoming_fuzzy(self):
        tokens = tokenize_item_query("xtall cr weapon")
        self.assertEqual([token.normalized for token in tokens], ["xtal", "cr", "weapon"])

    def test_weapon_xtal_is_one_unique_semantic_filter(self):
        tokens = tokenize_item_query("xtall cr weapon")
        matches = find_exact_item_filter_matches(tokens, TYPES)
        self.assertEqual(matches.status, "unique")
        self.assertTrue(matches.matches)
        self.assertEqual(
            matches.matches[0].phrase.item_types,
            ("Weapon Crysta", "Enhancer Crysta (Red)"),
        )
        self.assertEqual(matches.matches[0].canonical_phrase, "wp xtal")

    def test_filter_overlap_keeps_multiple_position_options(self):
        tokens = tokenize_item_query("weapon atk weapon xtal")
        matches = find_exact_item_filter_matches(tokens, TYPES)
        remaining = {
            tuple(token.normalized for token in remaining_tokens(tokens, match.token_indexes))
            for match in matches.matches
        }
        self.assertIn(("weapon", "atk"), remaining)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the new test and verify RED**

Run:

```bash
python -m unittest tests.test_item_query_entities -v
```

Expected: FAIL because `toram_search.item_query_entities` does not exist.

- [ ] **Step 3: Implement the shared exact matcher**

Create `toram_search/item_query_entities.py` by moving, not semantically changing, the relevant mechanics currently embedded in `reconstruction.py`:

```python
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from itertools import combinations, product
from typing import Literal

from toram_data.aliases import ITEM_WORD_ALIASES, normalize_stat_text
from toram_data.stat_query import ItemFilterPhrase, list_item_filter_phrases


FilterMatchStatus = Literal["unique", "none", "ambiguous"]
FilterMatchKind = Literal["exact", "fuzzy"]


@dataclass(frozen=True)
class QueryToken:
    index: int
    text: str
    normalized: str


@dataclass(frozen=True)
class ItemFilterMatch:
    typed_text: str
    token_indexes: tuple[int, ...]
    phrase: ItemFilterPhrase
    canonical_phrase: str
    match_kind: FilterMatchKind
    score: float = 100.0


@dataclass(frozen=True)
class ExactFilterMatches:
    status: FilterMatchStatus
    matches: tuple[ItemFilterMatch, ...] = ()


def tokenize_item_query(raw_query: str) -> tuple[QueryToken, ...]:
    raw_tokens = normalize_stat_text(raw_query).split()
    return tuple(
        QueryToken(index, token, ITEM_WORD_ALIASES.get(token, token))
        for index, token in enumerate(raw_tokens)
    )


def _semantic_key(row: ItemFilterPhrase) -> tuple[str, tuple[str, ...]]:
    return row.label, row.item_types


def _canonical_phrase(row: ItemFilterPhrase, catalog: tuple[ItemFilterPhrase, ...]) -> str:
    key = _semantic_key(row)
    phrases = [entry.phrase for entry in catalog if _semantic_key(entry) == key]
    return min(phrases, key=lambda value: (len(value.split()), len(value), value.casefold()))


def _index_options(tokens: tuple[QueryToken, ...], required: Counter[str]) -> tuple[tuple[int, ...], ...]:
    groups = []
    for normalized, count in sorted(required.items()):
        positions = [token.index for token in tokens if token.normalized == normalized]
        if len(positions) < count:
            return ()
        groups.append(tuple(combinations(positions, count)))
    output = {
        tuple(sorted(index for group in selected for index in group))
        for selected in product(*groups)
    }
    return tuple(sorted(output))


def find_exact_item_filter_matches(
    tokens: tuple[QueryToken, ...],
    available_item_types: set[str],
) -> ExactFilterMatches:
    catalog = list_item_filter_phrases(available_item_types)
    available = Counter(token.normalized for token in tokens)
    eligible = []
    for row in catalog:
        required = Counter(row.phrase.split())
        if required and all(available[token] >= count for token, count in required.items()):
            eligible.append((row, required))
    if not eligible:
        return ExactFilterMatches("none")
    largest_size = max(sum(required.values()) for _row, required in eligible)
    largest = [(row, required) for row, required in eligible if sum(required.values()) == largest_size]
    if len({_semantic_key(row) for row, _required in largest}) != 1:
        return ExactFilterMatches("ambiguous")
    matches = []
    for row, required in largest:
        for indexes in _index_options(tokens, required):
            typed = " ".join(tokens[index].text for index in indexes)
            matches.append(
                ItemFilterMatch(
                    typed,
                    indexes,
                    row,
                    _canonical_phrase(row, catalog),
                    "exact",
                )
            )
    return ExactFilterMatches("unique", tuple(matches))


def remaining_tokens(
    tokens: tuple[QueryToken, ...],
    consumed_indexes: tuple[int, ...],
) -> tuple[QueryToken, ...]:
    consumed = set(consumed_indexes)
    return tuple(token for token in tokens if token.index not in consumed)
```

The implementation may use small private helpers with different names, but the public signatures above must remain stable for later tasks.

- [ ] **Step 4: Refactor `reconstruction.py` onto the shared matcher**

Keep these public APIs unchanged:

```python
try_reconstruct_simple_search(...)
try_suggest_query(...)
ReconstructionResult
```

Replace only the duplicated token/filter-position logic with `tokenize_item_query()`, `find_exact_item_filter_matches()`, and `remaining_tokens()`. Preserve:

- `_MAX_RECONSTRUCTION_TOKENS = 24` behavior;
- known fillers;
- rank-word behavior in `try_suggest_query()`;
- `allow_fuzzy=False` for auto reconstruction;
- overlap case `weapon atk weapon xtal` -> `weapon atk wp xtal`;
- `crit` ambiguity behavior.

- [ ] **Step 5: Run shared + reconstruction tests**

Run:

```bash
python -m unittest tests.test_item_query_entities tests.test_query_reconstruction -v
```

Expected: PASS, including `test_oversized_input_fails_closed`.

- [ ] **Step 6: Commit**

```bash
git add toram_search/item_query_entities.py toram_search/reconstruction.py tests/test_item_query_entities.py tests/test_query_reconstruction.py
git commit -m "refactor: share item query entity matching"
```

---

### Task 2: Add Structured Deterministic Item Understanding and Partial Diagnostics

**Files:**
- Create: `toram_search/understanding.py`
- Create: `tests/test_item_query_understanding.py`
- Modify: `toram_search/item_query_entities.py`

**Interfaces:**
- Consumes: Task 1 token/filter APIs; `resolve_stat_term(..., allow_fuzzy=False)`; `preferred_stat_alias()`.
- Produces:
  - `UnderstandingDecision = Literal["execute", "clarify", "confirm", "suggest", "fallback"]`
  - `UnderstandingSource = Literal["deterministic", "llm"]`
  - categorical `ReasonCode` values including `EXACT_MATCH`, `ALIAS_MATCH`, `AMBIGUOUS_STAT`, `FUZZY_STAT`, `FUZZY_FILTER`, `UNKNOWN_TOKEN`, `MULTIPLE_CORRECTIONS`, `UNSAFE_SHAPE`, `LLM_INTERPRETATION`, `LLM_REJECTED`;
  - `ResolvedItemPart`;
  - `ItemQueryChoice`;
  - `ItemQueryUncertainty`;
  - `ConfirmedItemChoice`;
  - `ItemQueryUnderstanding`;
  - `understand_item_query(raw_query, *, available_stats, available_item_types, confirmed_choices=()) -> ItemQueryUnderstanding`.

- [ ] **Step 1: Write failing decision-model tests**

Create `tests/test_item_query_understanding.py` with these initial cases:

```python
import unittest

from toram_search.understanding import understand_item_query


STATS = ["Critical Rate", "Critical Damage", "MaxHP", "Weapon ATK"]
TYPES = {
    "Weapon Crysta",
    "Enhancer Crysta (Red)",
    "Armor",
    "Bow",
}


class ItemQueryUnderstandingTests(unittest.TestCase):
    def understand(self, query, confirmed_choices=()):
        return understand_item_query(
            query,
            available_stats=STATS,
            available_item_types=TYPES,
            confirmed_choices=confirmed_choices,
        )

    def test_unique_exact_alias_meaning_is_executable(self):
        result = self.understand("xtall cr weapon")
        self.assertEqual(result.decision, "execute")
        self.assertEqual(result.canonical_query, "cr wp xtal")
        self.assertEqual([part.display_label for part in result.resolved_parts], ["Critical Rate", "Weapon Crysta + Red Enhancer"])
        self.assertEqual(result.unresolved_tokens, ())

    def test_known_crit_ambiguity_is_structured(self):
        result = self.understand("crit xtal weapon")
        self.assertEqual(result.decision, "clarify")
        issue = result.uncertainties[0]
        self.assertEqual(issue.typed_text, "crit")
        self.assertEqual(issue.mode, "choose")
        self.assertEqual([choice.value for choice in issue.choices], ["Critical Rate", "Critical Damage"])

    def test_unknown_meaning_is_not_silently_dropped(self):
        result = self.understand("cr weapon xtal blah")
        self.assertEqual(result.decision, "suggest")
        self.assertEqual(result.unresolved_tokens, ("blah",))
        self.assertEqual(result.suggested_query, "cr wp xtal")

    def test_rank_word_is_visible_and_suggestion_only(self):
        result = self.understand("highest xtall cr weapon")
        self.assertEqual(result.decision, "suggest")
        self.assertEqual(result.unresolved_tokens, ("highest",))
        self.assertEqual(result.suggested_query, "cr wp xtal")

    def test_two_independent_stats_do_not_gain_new_boolean_semantics(self):
        result = self.understand("cr hp weapon xtal")
        self.assertEqual(result.decision, "fallback")
        self.assertIsNone(result.suggested_query)

    def test_oversized_simple_input_fails_closed(self):
        query = " ".join(["show"] * 30 + ["cr", "weapon", "xtal"])
        result = self.understand(query)
        self.assertEqual(result.decision, "fallback")
        self.assertIn("UNSAFE_SHAPE", result.reasons)
```

- [ ] **Step 2: Run and verify RED**

Run:

```bash
python -m unittest tests.test_item_query_understanding -v
```

Expected: FAIL because the understanding module does not exist.

- [ ] **Step 3: Implement the frontend-neutral model**

Create `toram_search/understanding.py` with immutable dataclasses. Use strings for reason codes so logs/tests remain inspectable without exposing codes to Discord:

```python
from dataclasses import dataclass
from typing import Iterable, Literal


UnderstandingDecision = Literal["execute", "clarify", "confirm", "suggest", "fallback"]
UnderstandingSource = Literal["deterministic", "llm"]
PartKind = Literal["stat", "item_filter"]
UncertaintyMode = Literal["choose", "confirm"]


@dataclass(frozen=True)
class ResolvedItemPart:
    part_kind: PartKind
    typed_text: str
    value: str
    display_label: str
    canonical_text: str
    reason: str


@dataclass(frozen=True)
class ItemQueryChoice:
    value: str
    display_label: str
    canonical_text: str


@dataclass(frozen=True)
class ItemQueryUncertainty:
    issue_id: str
    part_kind: PartKind
    typed_text: str
    mode: UncertaintyMode
    reason: str
    choices: tuple[ItemQueryChoice, ...]


@dataclass(frozen=True)
class ConfirmedItemChoice:
    issue_id: str
    value: str


@dataclass(frozen=True)
class ItemQueryUnderstanding:
    decision: UnderstandingDecision
    resolved_parts: tuple[ResolvedItemPart, ...] = ()
    uncertainties: tuple[ItemQueryUncertainty, ...] = ()
    unresolved_tokens: tuple[str, ...] = ()
    canonical_query: str | None = None
    suggested_query: str | None = None
    source: UnderstandingSource = "deterministic"
    reasons: tuple[str, ...] = ()
```

Use stable issue IDs based on source token indexes, for example `stat:0:crit` or `item_filter:1,2:wepon-xtal`; do not identify an issue only by user-visible label.

- [ ] **Step 4: Implement exact/alias/ambiguity/partial recognition**

For a query that reached this layer:

1. Reject comparison/Boolean syntax (`>=`, `<=`, `==`, `>`, `<`, `=`, standalone `and`/`or`) and input longer than 24 normalized tokens with `decision="fallback"` and `UNSAFE_SHAPE`.
2. Use Task 1 to identify the largest exact item-filter semantic match. If there are multiple semantic filter meanings, fail closed.
3. For each valid filter token-removal option, inspect remaining non-filler tokens.
4. Resolve the entire remaining stat phrase with `resolve_stat_term(..., allow_fuzzy=False)` first.
5. If the entire phrase is unknown, enumerate contiguous remaining-token spans longest-first and collect exact/alias/known-ambiguous stat spans. A single semantic stat span plus leftover tokens is partial understanding; two independent resolved stat spans means `fallback` because this iteration does not invent AND/OR semantics.
6. Known filler words continue to be accepted from an explicit allowlist. `highest`, `best`, and `most` are not filler here; they stay visible as unresolved wording.
7. Build canonical syntax only from one semantic stat + one semantic filter. Use `preferred_stat_alias()` where available and the compact filter phrase from Task 1.
8. Sort uncertainties by policy priority: known semantic ambiguity first, then later fuzzy-stat/fuzzy-filter issues added in Task 3.

The exact/alias/partial decision table is:

```text
one resolved stat + one resolved filter + no unresolved -> execute
known stat ambiguity + resolved filter               -> clarify
one resolved stat + one resolved filter + leftovers -> suggest
multiple independent stats                          -> fallback
no unique stat/filter core                          -> fallback
```

- [ ] **Step 5: Run unit tests**

Run:

```bash
python -m unittest tests.test_item_query_understanding tests.test_query_reconstruction -v
```

Expected: PASS. Reconstruction behavior must remain unchanged.

- [ ] **Step 6: Commit**

```bash
git add toram_search/understanding.py toram_search/item_query_entities.py tests/test_item_query_understanding.py
git commit -m "feat: add deterministic item query understanding"
```

---

### Task 3: Add Conservative Fuzzy Stat and Item-Filter Confirmation Candidates

**Files:**
- Modify: `toram_search/item_query_entities.py`
- Modify: `toram_search/understanding.py`
- Modify: `tests/test_item_query_entities.py`
- Modify: `tests/test_item_query_understanding.py`

**Interfaces:**
- Adds `find_unique_fuzzy_item_filter_match(tokens, available_item_types, *, fuzzy_threshold=85.0) -> ItemFilterMatch | None`.
- `understand_item_query()` starts using existing `resolve_stat_term(..., allow_fuzzy=True, fuzzy_threshold=85.0, limit=2)` only after exact/alias/known-ambiguity recognition fails.
- Fuzzy candidates remain `decision="confirm"`; they never produce `decision="execute"` unless their issue is explicitly present in `confirmed_choices`.

- [ ] **Step 1: Add RED tests for fuzzy safety**

Extend the two focused test files with:

```python
def test_wepon_xtal_has_one_conservative_fuzzy_filter_candidate(self):
    tokens = tokenize_item_query("cr wepon xtal")
    match = find_unique_fuzzy_item_filter_match(tokens, TYPES)
    self.assertIsNotNone(match)
    self.assertEqual(match.phrase.item_types, ("Weapon Crysta", "Enhancer Crysta (Red)"))
    self.assertEqual(match.canonical_phrase, "wp xtal")
    self.assertEqual(match.match_kind, "fuzzy")
```

and:

```python
def test_fuzzy_stat_requires_confirmation(self):
    result = self.understand("weapon xtal crtical rate")
    self.assertEqual(result.decision, "confirm")
    issue = result.uncertainties[0]
    self.assertEqual(issue.reason, "FUZZY_STAT")
    self.assertEqual(issue.mode, "confirm")
    self.assertEqual([choice.value for choice in issue.choices], ["Critical Rate"])


def test_fuzzy_filter_requires_confirmation(self):
    result = self.understand("cr wepon xtal")
    self.assertEqual(result.decision, "confirm")
    issue = result.uncertainties[0]
    self.assertEqual(issue.reason, "FUZZY_FILTER")
    self.assertEqual(issue.mode, "confirm")


def test_semantic_ambiguity_precedes_fuzzy_filter(self):
    result = self.understand("crit wepon xtal")
    self.assertEqual(result.decision, "clarify")
    self.assertEqual([issue.reason for issue in result.uncertainties], ["AMBIGUOUS_STAT", "FUZZY_FILTER"])
```

- [ ] **Step 2: Run the new cases and verify RED**

Run:

```bash
python -m unittest tests.test_item_query_entities tests.test_item_query_understanding -v
```

Expected: FAIL on missing fuzzy-filter API and missing fuzzy decisions.

- [ ] **Step 3: Implement conservative fuzzy item-filter matching**

Reuse `rapidfuzz.fuzz`, matching the repository's existing fuzzy philosophy but with a stricter confirmation-candidate threshold:

```python
_FUZZY_CONFIRM_THRESHOLD = 85.0
```

For every contiguous normalized token span up to the longest catalog phrase:

```python
weighted = float(fuzz.WRatio(span_text, row.phrase))
token_score = float(fuzz.token_sort_ratio(span_text, row.phrase))
score = min(weighted, token_score)
```

Only keep rows with `score >= 85.0`. Collapse rows by semantic filter key `(label, item_types)`, retaining the best row/span per semantic key. Return a fuzzy filter candidate only when **exactly one semantic filter key** clears the threshold. If two different semantic filters clear the threshold, return `None` and fail closed. Multiple aliases such as `wp xtal` and `weapon xtal` for the same semantic filter do not create ambiguity.

Do not fuzzy-match across filler words or non-contiguous spans. Exact filter matching always has precedence.

- [ ] **Step 4: Implement conservative fuzzy stat matching and issue ordering**

When no exact/alias/known-ambiguous stat span resolves the required stat text, call:

```python
resolve_stat_term(
    typed_stat,
    available_stats,
    allow_fuzzy=True,
    fuzzy_threshold=85.0,
    limit=2,
)
```

Accept a fuzzy confirmation issue only when `resolution.status == "fuzzy"` and exactly one candidate is returned. More than one fuzzy stat candidate is not converted into a guess; keep the query unresolved/fallback-only.

When a query produces multiple uncertainty objects, sort them as:

```text
1. AMBIGUOUS_STAT
2. FUZZY_STAT
3. FUZZY_FILTER
```

This guarantees `crit wepon xtal` asks CR/CD first.

- [ ] **Step 5: Run focused tests**

Run:

```bash
python -m unittest tests.test_item_query_entities tests.test_item_query_understanding tests.test_query_reconstruction -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add toram_search/item_query_entities.py toram_search/understanding.py tests/test_item_query_entities.py tests/test_item_query_understanding.py
git commit -m "feat: add safe fuzzy item query confirmations"
```

---

### Task 4: Add Pending Interpretation State and Service Orchestration Before Qwen

**Files:**
- Modify: `toram_search/session.py`
- Modify: `toram_search/service.py`
- Modify: `tests/test_search_service.py`
- Modify: `tests/test_item_query_understanding.py`

**Interfaces:**
- Consumes: `understand_item_query()`, `ConfirmedItemChoice`, existing deterministic router/materializer, existing fallback service.
- Produces:
  - `PendingItemSearch(original_query, understanding, confirmed_choices=())` in `toram_search.session`.
  - New `ServiceKind` member `"item_understanding"`.
  - `ServiceOutcome.pending_item_search: PendingItemSearch | None`.
  - `SearchService.continue_item_understanding(pending, issue_id, selected_value, context) -> ServiceOutcome`.
  - `SearchService.confirm_pending_item_search(pending, context) -> ServiceOutcome`.

- [ ] **Step 1: Write RED service tests for orchestration order**

Update `tests/test_search_service.py` so safe deterministic understanding is proven to skip Qwen and failed history:

```python
def test_partial_safe_suggestion_skips_qwen_and_failure_history(self):
    repository = FakeRepository()
    service = SearchService(repository, llm_client=MustNotCallLLM())
    context = FailedQueryContext(max_entries=3)

    outcome = service.handle_query("highest xtall cr weapon", context)

    self.assertEqual(outcome.kind, "item_understanding")
    self.assertEqual(outcome.pending_item_search.understanding.decision, "suggest")
    self.assertEqual(outcome.pending_item_search.understanding.suggested_query, "cr wp xtal")
    self.assertEqual(context.snapshot(), ())
    self.assertEqual(repository.expression_calls, [])


def test_ambiguity_plus_fuzzy_filter_skips_qwen(self):
    repository = FakeRepository()
    service = SearchService(repository, llm_client=MustNotCallLLM())
    context = FailedQueryContext(max_entries=3)

    outcome = service.handle_query("crit wepon xtal", context)

    self.assertEqual(outcome.kind, "item_understanding")
    self.assertEqual(outcome.pending_item_search.understanding.decision, "clarify")
    self.assertEqual(context.snapshot(), ())
```

Also replace the old expectation that `highest xtall cr weapon` first calls Qwen; after this task that behavior is intentionally obsolete.

- [ ] **Step 2: Write RED tests for single vs multiple decisions**

Add a complete service-level flow:

```python
def test_two_interpretation_choices_require_final_confirmation(self):
    repository = FakeRepository()
    service = SearchService(repository, llm_client=MustNotCallLLM())
    context = FailedQueryContext(max_entries=3)

    first = service.handle_query("crit wepon xtal", context)
    first_issue = first.pending_item_search.understanding.uncertainties[0]
    second = service.continue_item_understanding(
        first.pending_item_search,
        first_issue.issue_id,
        "Critical Rate",
        context,
    )
    self.assertEqual(second.kind, "item_understanding")
    second_issue = second.pending_item_search.understanding.uncertainties[0]
    self.assertEqual(second_issue.reason, "FUZZY_FILTER")

    third = service.continue_item_understanding(
        second.pending_item_search,
        second_issue.issue_id,
        second_issue.choices[0].value,
        context,
    )
    self.assertEqual(third.kind, "item_understanding")
    self.assertEqual(third.pending_item_search.understanding.decision, "confirm")
    self.assertEqual(third.pending_item_search.understanding.uncertainties, ())
    self.assertEqual(repository.expression_calls, [])

    final = service.confirm_pending_item_search(third.pending_item_search, context)
    self.assertEqual(final.kind, "search")
    self.assertEqual(len(repository.expression_calls), 1)
    self.assertEqual(context.snapshot(), ())
```

Add the single-choice counterpart using a query that enters the new understanding layer with exactly one uncertainty; after that choice, it must execute without the extra final confirmation.

- [ ] **Step 3: Add `PendingItemSearch`**

In `toram_search/session.py`:

```python
from toram_search.understanding import ConfirmedItemChoice, ItemQueryUnderstanding


@dataclass(frozen=True)
class PendingItemSearch:
    original_query: str
    understanding: ItemQueryUnderstanding
    confirmed_choices: tuple[ConfirmedItemChoice, ...] = ()
```

Do not put Qwen transcript/history into this state. `FailedQueryContext` remains unchanged and separate.

- [ ] **Step 4: Teach understanding to apply confirmed issue choices**

`understand_item_query(..., confirmed_choices=())` must build a mapping by `issue_id`. When it rediscovers an ambiguity/fuzzy issue:

- if there is no confirmed choice, keep the issue;
- if the confirmed value is one of that issue's current choices, convert it to a resolved part;
- if a stored value is no longer a valid choice, fail closed with `decision="fallback"`.

When all meaning is resolved:

```text
0 confirmed choices -> execute
1 confirmed choice  -> execute
2+ confirmed choices -> confirm + MULTIPLE_CORRECTIONS, with no remaining uncertainty
```

The canonical query must be generated from selected semantic values, not by trusting user-supplied button text.

- [ ] **Step 5: Integrate understanding before fallback in `SearchService.handle_query()`**

After existing deterministic `search/help/database/refuse` handling and before `context.record_failure()`:

```python
understanding = understand_item_query(
    query,
    available_stats=self.repository.list_stat_names(),
    available_item_types=self.repository.list_item_types(),
)
```

Then:

- `execute` with a canonical query: reroute the canonical query through `core.route_deterministically()` and materialize only if that reroute is a valid search;
- `clarify`, `confirm`, or `suggest`: return `ServiceOutcome("item_understanding", pending_item_search=PendingItemSearch(query, understanding))` immediately;
- `fallback`: only now record the failed query (when `route.record_failure` allows it) and call Qwen once.

Remove the live post-Qwen `try_suggest_query()` branch from `handle_query()`. Safe suggestions are now produced before Qwen by deterministic understanding. Keep `try_suggest_query()` itself for compatibility unless a later cleanup explicitly removes it.

- [ ] **Step 6: Add continuation and final-confirmation methods**

`continue_item_understanding()` must:

1. validate that the supplied `issue_id` is the first currently surfaced uncertainty;
2. validate `selected_value` against that issue's choices;
3. append `ConfirmedItemChoice(issue_id, selected_value)`;
4. rerun `understand_item_query()` on `pending.original_query` using the accumulated confirmed choices;
5. return another `item_understanding` outcome for the next issue/suggestion/final confirmation;
6. execute only if the new understanding says `execute`, by rerouting its canonical query through the deterministic router.

`confirm_pending_item_search()` accepts only:

- `decision="suggest"` with a non-null `suggested_query`; or
- `decision="confirm"` with no unresolved uncertainty and a non-null `canonical_query`.

It reroutes that query through the deterministic router and executes only if the router returns a valid search. It never calls Qwen.

- [ ] **Step 7: Preserve Qwen boundary tests**

Keep and strengthen:

```text
unresolved structure -> one Qwen call
Qwen search candidate -> confirm_search
before confirmation -> no repository search call
confirmed Qwen request -> parse_structured_search_request -> execute
```

Also assert failed history is recorded when entering Qwen, but not for `item_understanding` outcomes.

- [ ] **Step 8: Run focused service tests**

Run:

```bash
python -m unittest tests.test_item_query_understanding tests.test_search_service -v
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add toram_search/session.py toram_search/service.py toram_search/understanding.py tests/test_item_query_understanding.py tests/test_search_service.py
git commit -m "feat: route item understanding before qwen"
```

---

### Task 5: Add Human-Readable Discord Clarification, Suggestion, and Final Confirmation UI

**Files:**
- Modify: `discord_bot.py`
- Modify: `tests/test_discord_bot.py`

**Interfaces:**
- Consumes: `ServiceOutcome(kind="item_understanding")`, `PendingItemSearch`, `SearchService.continue_item_understanding()`, `SearchService.confirm_pending_item_search()`.
- Produces:
  - `DiscordSearchSession.pending_item_search: PendingItemSearch | None`.
  - `build_item_understanding_embed(pending: PendingItemSearch) -> discord.Embed`.
  - `ItemUnderstandingView(SessionBoundView)`.
  - sync wrappers for continuation/final confirmation that mirror existing `run_clarification_sync()` style.

- [ ] **Step 1: Write RED formatting tests**

Add tests that inspect visible embed text rather than internal reason codes:

```python
def test_partial_understanding_embed_shows_known_unknown_and_canonical_form(self):
    pending = make_pending_item_search("cr weapon xtal blah")
    embed = discord_bot.build_item_understanding_embed(pending)
    visible = "\n".join(
        [embed.title or "", embed.description or "", *(f.name + "\n" + f.value for f in embed.fields)]
    )
    self.assertIn("Critical Rate", visible)
    self.assertIn("Weapon Crysta", visible)
    self.assertIn("blah", visible)
    self.assertIn("cr wp xtal", visible)
    self.assertNotIn("UNKNOWN_TOKEN", visible)


def test_ambiguity_view_uses_structured_choice_buttons(self):
    pending = make_pending_item_search("crit wepon xtal")
    view = discord_bot.ItemUnderstandingView(..., pending=pending)
    labels = [child.label for child in view.children if hasattr(child, "label")]
    self.assertIn("Critical Rate", labels)
    self.assertIn("Critical Damage", labels)
```

Use a small local helper in the test file that constructs understanding with the same fake stat/type catalog as service tests; do not hand-build impossible production states.

- [ ] **Step 2: Write RED session-lifecycle tests**

Cover:

```text
item_understanding outcome -> session.pending_item_search is set
successful continued search -> pending_item_search is cleared
Cancel/No/Edit -> pending_item_search is cleared
new start_query() -> new session has no pending item search and invalidates the old view generation
```

- [ ] **Step 3: Add pending state to `DiscordSearchSession` and sync wrappers**

Add:

```python
pending_item_search: PendingItemSearch | None = None
```

Add two wrappers using the existing repository-open/close pattern:

```python
run_item_understanding_choice_sync(
    database_path,
    pending,
    issue_id,
    selected_value,
    context,
    *,
    repository_factory=core.ItemRepository,
) -> ServiceOutcome

run_pending_item_search_confirmation_sync(
    database_path,
    pending,
    context,
    *,
    repository_factory=core.ItemRepository,
) -> ServiceOutcome
```

- [ ] **Step 4: Implement human-readable embed rendering**

`build_item_understanding_embed()` should render semantic labels first:

```text
I understood
• Critical Rate
• Weapon Crysta + Red Enhancer

I couldn't safely interpret
• `blah`

Suggested search
Critical Rate + Weapon Crysta + Red Enhancer

Search form
`cr wp xtal`
```

Decision-specific presentation:

- `clarify`: title `What does "<typed>" mean?`; list known choices.
- fuzzy `confirm`: title `Confirm correction`; description `Did you mean **<label>** for "<typed>"?`.
- `suggest`: title `I understood part of that search`; show recognized/unresolved + suggestion.
- final `confirm` with no uncertainty: title `Ready to search`; show the fully resolved semantic summary + canonical search form.

Never show raw reason-code strings or numeric fuzzy scores.

- [ ] **Step 5: Implement `ItemUnderstandingView` controls**

Use existing `SessionBoundView` and `ActionButton`.

Control rules:

```text
clarify / choose:
  [candidate 1] [candidate 2] ... [Cancel]

fuzzy / confirm:
  [Use <candidate>] [No]

suggest:
  [Use suggestion] [Cancel]

final multi-correction confirm:
  [Search] [Edit]
```

Callbacks must defer before repository work, call the sync wrapper in `asyncio.to_thread`, re-check generation, and then call `edit_service_outcome()`.

`No` and `Cancel` clear `session.pending_item_search` and edit the component message to `No search was executed.`

`Edit` does **not** add a modal in this iteration. It clears pending state and edits the message to a short instruction such as `Send a new @Bot query with the changes you want.` The next mentioned query creates a new generation and becomes the source of truth.

- [ ] **Step 6: Handle `item_understanding` in `build_service_outcome_message()`**

Before the ordinary search-payload branch:

1. copy `outcome.pending_item_search` into the current Discord session;
2. build the human-readable embed;
3. attach `ItemUnderstandingView`;
4. do not clear `FailedQueryContext` merely because a clarification/suggestion is shown.

When a continuation produces a real `search` outcome, clear both `pending_item_search` and failed context using the same successful-search rule already used by existing clarification/Qwen confirmation callbacks.

- [ ] **Step 7: Run Discord tests**

Run:

```bash
python -m unittest tests.test_discord_bot -v
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add discord_bot.py tests/test_discord_bot.py
git commit -m "feat: add item search clarification controls"
```

---

### Task 6: Add End-to-End Safety and Differential Regression Tests

**Files:**
- Modify: `tests/test_item_query_understanding.py`
- Modify: `tests/test_search_service.py`
- Modify: `tests/test_discord_bot.py`
- Modify: `tests/test_query_reconstruction.py` only if a regression fixture is needed; do not change production behavior in this task unless a test exposes a bug.

**Interfaces:**
- No new public interface. This task locks the approved safety invariants around the interfaces created above.

- [ ] **Step 1: Add table-driven unknown-token safety cases**

In `tests/test_search_service.py`, verify that injecting an unknown meaningful token into an otherwise deterministic core never executes before confirmation:

```python
def test_unknown_meaning_never_disappears_on_auto_execution_path(self):
    for query in (
        "cr weapon xtal blah",
        "blah cr weapon xtal",
        "cr blah weapon xtal",
    ):
        with self.subTest(query=query):
            repository = FakeRepository()
            service = SearchService(repository, llm_client=MustNotCallLLM())
            outcome = service.handle_query(query, FailedQueryContext(max_entries=3))
            self.assertEqual(outcome.kind, "item_understanding")
            self.assertIn("blah", outcome.pending_item_search.understanding.unresolved_tokens)
            self.assertEqual(repository.expression_calls, [])
            self.assertEqual(repository.stat_calls, [])
```

- [ ] **Step 2: Add semantic-equivalence tests**

For every understanding path that becomes executable, compare its materialized semantics with the canonical query. At minimum cover:

```text
xtall cr weapon <=> cr wp xtal
confirmed crit + weapon xtal <=> cr wp xtal (after choosing Critical Rate)
confirmed fuzzy `wepon xtal` <=> canonical wp xtal
```

Compare resolved stat name, item-type tuple, and sort direction; do not compare only the canonical string.

- [ ] **Step 3: Add multi-decision and ordering regression cases**

Lock these sequences:

```text
crit wepon xtal
-> AMBIGUOUS_STAT first
-> FUZZY_FILTER second
-> final confirmation
-> Search executes exactly once

highest crit xtal weapon
-> AMBIGUOUS_STAT first
-> after choice, `highest` remains visible
-> validated suggestion is shown
-> suggestion does not auto-execute
```

- [ ] **Step 4: Add Qwen boundary regressions**

Assert:

```text
safe deterministic suggestion -> 0 Qwen calls
known ambiguity -> 0 Qwen calls
fuzzy confirmation -> 0 Qwen calls
unresolved structure with no safe deterministic action -> exactly 1 Qwen call
Qwen search candidate -> 0 DB search calls until confirm_search_request()
rejected Qwen payload -> no DB search call
```

Also assert `FailedQueryContext.snapshot()` remains empty for the first three paths and gains an attempt only on the actual Qwen path.

- [ ] **Step 5: Add oversized/adversarial combinations**

Extend unit tests with repeated tokens, ambiguous terms, and typo candidates while keeping the existing 24-token guard. Cases longer than 24 normalized tokens must return fallback/unsafe without attempting combinatorial matching.

- [ ] **Step 6: Run the focused regression gate**

Run:

```bash
python -m unittest \
  tests.test_item_query_entities \
  tests.test_item_query_understanding \
  tests.test_query_reconstruction \
  tests.test_search_service \
  tests.test_discord_bot \
  -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add tests/test_item_query_entities.py tests/test_item_query_understanding.py tests/test_query_reconstruction.py tests/test_search_service.py tests/test_discord_bot.py
git commit -m "test: lock item query understanding safety invariants"
```

---

### Task 7: Verify Feature Against the Real Database and Full Baseline

**Files:**
- No planned production-file changes.
- If verification exposes a feature bug, return to the responsible task, add a failing regression test first, then fix and commit there before repeating this task.

**Interfaces:**
- Verifies all prior tasks as one integrated feature.

- [ ] **Step 1: Compile modified Python modules**

Run:

```bash
python -m py_compile \
  toram_search/item_query_entities.py \
  toram_search/understanding.py \
  toram_search/reconstruction.py \
  toram_search/session.py \
  toram_search/service.py \
  discord_bot.py
```

Expected: exit 0.

- [ ] **Step 2: Run the focused feature gate again from a clean checkout**

Run the Task 6 focused command. Expected: all focused tests PASS.

- [ ] **Step 3: Run real-database no-Qwen smoke checks**

Use the repository's real configured item database and a sentinel LLM that raises if called. Check at minimum:

```text
xtall cr weapon
-> search executes
-> Critical Rate
-> Weapon Crysta + Red Enhancer types
-> failed context empty

crit xtal weapon
-> structured CR/CD clarification
-> no DB search yet
-> failed context empty

highest xtall cr weapon
-> item-understanding suggestion
-> recognized Critical Rate + Weapon Crysta
-> unresolved highest
-> no Qwen
-> no DB search
-> failed context empty
```

Use normalized item-type comparisons because the real database currently contains casing such as `Enhancer Crysta (red)`.

- [ ] **Step 4: Run full suite**

Run:

```bash
python -m unittest discover -s tests -v
```

Expected baseline: the feature introduces no new failures. The repository currently has one known unrelated baseline failure:

```text
test_structured_fallback.StructuredFallbackTests.test_rejected_payload_is_logged_with_reason
expected: "missing or invalid search candidates"
actual log: "search payload has unexpected fields"
```

Do **not** claim the whole repository suite is green while that baseline failure remains. Report that the feature regression gate is green and the full suite matches baseline if and only if this is the sole failure.

- [ ] **Step 5: Review the final diff for scope**

Confirm the implementation changes only the item-search understanding/reconstruction/session/service/Discord paths and their tests. There must be no boss/skill abstraction, build-role logic, telemetry, arbitrary SQL, or new LLM execution path.

- [ ] **Step 6: Commit any verification-only documentation if needed**

If no file changes were needed, do not create an empty commit. If a verification note is intentionally added to an existing PR description later, keep it out of production code.

---

## Expected Behavior Matrix

| Query | Deterministic outcome | Qwen | DB before user action |
|---|---|---:|---:|
| `cr weapon xtal` | execute | 0 | yes |
| `xtall cr weapon` | reconstruct + execute | 0 | yes |
| `crit weapon xtal` | structured CR/CD clarification | 0 | no |
| `weapon xtal crtical rate` | fuzzy stat confirmation | 0 | no |
| `cr wepon xtal` | fuzzy filter confirmation | 0 | no |
| `crit wepon xtal` | CR/CD clarification, then fuzzy filter, then final confirmation | 0 | no until final Search |
| `cr weapon xtal blah` | recognized + unresolved + safe suggestion | 0 | no |
| `highest xtall cr weapon` | recognized + unresolved `highest` + safe suggestion | 0 | no |
| `cr hp weapon xtal` | no invented AND/OR semantics; fallback | at most 1 | no until validated Qwen confirmation |
| truly unresolved natural request | constrained fallback | 1 | no until validated Qwen confirmation |

## Completion Definition

The work is complete only when:

- exact/current deterministic item searches still behave as before;
- the new deterministic understanding paths require no Qwen call;
- ambiguous/fuzzy/partial input cannot auto-execute incorrectly;
- recognized and unresolved meaning is available as structured data and rendered human-readably in Discord;
- single vs multiple interpretation decisions follow the approved confirmation policy;
- Qwen is reached only after deterministic understanding has no safe action;
- Qwen-derived searches still require confirmation and deterministic request parsing;
- pending item-search state clears on success/cancel/rejection/edit/new query;
- focused feature tests pass;
- real-database smoke checks pass;
- the full suite has no failures beyond the already-known unrelated baseline failure.
