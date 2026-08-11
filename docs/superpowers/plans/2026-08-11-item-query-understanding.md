# Item Query Understanding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve item-search failures and ambiguity without making the bot more willing to guess: exact/alias searches stay fast, known ambiguity uses structured choices, fuzzy matches require confirmation, partial understanding produces safe parser-validated suggestions, and Qwen runs only when deterministic understanding has no safe action.

**Architecture:** Keep `core.route_deterministically()` and the existing parsed-stat clarification path first. Add a shared low-level item-filter matcher, then a frontend-neutral `ItemQueryUnderstanding` layer for failed simple item queries. `SearchService` performs every reroute/revalidation and owns the Qwen boundary; Discord stores only minimal pending interpretation state and renders structured outcomes.

**Tech Stack:** Python 3, `dataclasses`, `unittest`, existing `rapidfuzz`, existing SQLite `ItemRepository`, existing Discord.py UI components, existing Ollama/Qwen fallback.

## Global Constraints

- The existing deterministic parser remains the fast path and source of truth.
- Only fully resolved deterministic meaning may execute.
- Exact matches and intentional aliases may execute directly.
- Known semantic ambiguity never auto-executes.
- Fuzzy matches are confirmation-only and never silently execute.
- Unknown meaningful input never silently disappears on an auto-execution path.
- No new multi-stat AND/OR/relation semantics are added in this iteration.
- Qwen may interpret intent/terms but may not provide trusted item facts, choose result rows, or generate arbitrary SQL.
- Any Qwen-derived search still requires deterministic validation and explicit user confirmation.
- Deterministic clarification, fuzzy confirmation, and safe suggestion do not create failed-query history; record a failure only when `SearchService` actually enters Qwen fallback.
- Human-readable labels are primary UI; canonical syntax such as `cr wp xtal` is secondary guidance.
- One resolved ambiguity/correction may execute after revalidation; two or more independent interpretation choices require one final Search/Edit confirmation.
- A new Discord query starts a new session generation and therefore supersedes any pending item clarification.
- Preserve the existing 24-token reconstruction bound.
- Do not add boss/skill abstractions, build-role semantics, telemetry, or numeric confidence scoring.

---

## File Map

**Create**

- `toram_search/item_query_entities.py` — normalized query tokens and exact/fuzzy item-filter matching. No service/Discord/LLM policy.
- `toram_search/understanding.py` — structured understanding models, reason codes, stat/filter recognition, confirmation policy, and confirmed-choice application.
- `tests/test_item_query_entities.py` — low-level matcher tests.
- `tests/test_item_query_understanding.py` — decision/safety tests.

**Modify**

- `toram_search/reconstruction.py` — reuse shared exact matcher while preserving public behavior.
- `toram_search/session.py` — add immutable `PendingItemSearch`; keep `FailedQueryContext` separate.
- `toram_search/service.py` — add deterministic-understanding stage before Qwen and continuation/final-confirmation methods.
- `discord_bot.py` — render recognized/unresolved meaning and add structured controls.
- `tests/test_query_reconstruction.py`
- `tests/test_search_service.py`
- `tests/test_discord_bot.py`

---

### Task 1: Extract Shared Exact Item-Filter Matching

**Files:**
- Create: `toram_search/item_query_entities.py`
- Create: `tests/test_item_query_entities.py`
- Modify: `toram_search/reconstruction.py`
- Test: `tests/test_query_reconstruction.py`

**Interfaces:**

```python
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
    match_kind: Literal["exact", "fuzzy"]
    score: float = 100.0

FilterMatchStatus = Literal["unique", "none", "ambiguous"]

@dataclass(frozen=True)
class ExactFilterMatches:
    status: FilterMatchStatus
    matches: tuple[ItemFilterMatch, ...] = ()

def tokenize_item_query(raw_query: str) -> tuple[QueryToken, ...]:
    ...

def find_exact_item_filter_matches(
    tokens: tuple[QueryToken, ...],
    available_item_types: set[str],
) -> ExactFilterMatches:
    ...

def remaining_tokens(
    tokens: tuple[QueryToken, ...],
    consumed_indexes: tuple[int, ...],
) -> tuple[QueryToken, ...]:
    ...
```

The signatures above are the required public boundary; implementation bodies are provided in Step 3.

- [ ] **Step 1: Add RED tests for alias normalization, semantic uniqueness, and overlap**

Create `tests/test_item_query_entities.py`:

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
    def test_item_word_alias_is_intentional_normalization(self):
        tokens = tokenize_item_query("xtall cr weapon")
        self.assertEqual(
            [token.normalized for token in tokens],
            ["xtal", "cr", "weapon"],
        )

    def test_weapon_xtal_resolves_to_one_semantic_filter(self):
        tokens = tokenize_item_query("xtall cr weapon")
        result = find_exact_item_filter_matches(tokens, TYPES)
        self.assertEqual(result.status, "unique")
        self.assertTrue(result.matches)
        self.assertEqual(
            result.matches[0].phrase.item_types,
            ("Weapon Crysta", "Enhancer Crysta (Red)"),
        )
        self.assertEqual(result.matches[0].canonical_phrase, "wp xtal")

    def test_repeated_weapon_token_preserves_valid_removal_option(self):
        tokens = tokenize_item_query("weapon atk weapon xtal")
        result = find_exact_item_filter_matches(tokens, TYPES)
        remaining = {
            tuple(token.normalized for token in remaining_tokens(tokens, match.token_indexes))
            for match in result.matches
        }
        self.assertIn(("weapon", "atk"), remaining)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run RED test**

```bash
python -m unittest tests.test_item_query_entities -v
```

Expected: FAIL because `toram_search.item_query_entities` does not exist.

- [ ] **Step 3: Implement shared exact matching**

Create `toram_search/item_query_entities.py`:

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


def semantic_filter_key(row: ItemFilterPhrase) -> tuple[str, tuple[str, ...]]:
    return row.label, row.item_types


def canonical_filter_phrase(
    row: ItemFilterPhrase,
    catalog: tuple[ItemFilterPhrase, ...],
) -> str:
    key = semantic_filter_key(row)
    phrases = [entry.phrase for entry in catalog if semantic_filter_key(entry) == key]
    return min(
        phrases,
        key=lambda value: (len(value.split()), len(value), value.casefold()),
    )


def _index_options(
    tokens: tuple[QueryToken, ...],
    required: Counter[str],
) -> tuple[tuple[int, ...], ...]:
    groups = []
    for normalized, count in sorted(required.items()):
        positions = [token.index for token in tokens if token.normalized == normalized]
        if len(positions) < count:
            return ()
        groups.append(tuple(combinations(positions, count)))
    selected = {
        tuple(sorted(index for group in chosen for index in group))
        for chosen in product(*groups)
    }
    return tuple(sorted(selected))


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

    max_size = max(sum(required.values()) for _row, required in eligible)
    largest = [
        (row, required)
        for row, required in eligible
        if sum(required.values()) == max_size
    ]
    if len({semantic_filter_key(row) for row, _required in largest}) != 1:
        return ExactFilterMatches("ambiguous")

    matches = []
    for row, required in largest:
        for indexes in _index_options(tokens, required):
            matches.append(
                ItemFilterMatch(
                    typed_text=" ".join(tokens[index].text for index in indexes),
                    token_indexes=indexes,
                    phrase=row,
                    canonical_phrase=canonical_filter_phrase(row, catalog),
                    match_kind="exact",
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

- [ ] **Step 4: Refactor reconstruction to consume the shared exact matcher**

Keep these APIs unchanged:

```python
ReconstructionResult
try_reconstruct_simple_search()
try_suggest_query()
```

Replace only duplicated token/filter-position matching. Preserve all current rules: 24-token bound, filler handling, no fuzzy auto reconstruction, rank-word suggestion behavior, overlap behavior, and `crit` ambiguity.

- [ ] **Step 5: Verify Task 1**

```bash
python -m unittest tests.test_item_query_entities tests.test_query_reconstruction -v
```

Expected: PASS.

- [ ] **Step 6: Commit Task 1**

```bash
git add toram_search/item_query_entities.py toram_search/reconstruction.py tests/test_item_query_entities.py tests/test_query_reconstruction.py
git commit -m "refactor: share item query entity matching"
```

---

### Task 2: Add Structured Exact/Ambiguous/Partial Understanding

**Files:**
- Create: `toram_search/understanding.py`
- Create: `tests/test_item_query_understanding.py`

**Interfaces:**

```python
UnderstandingDecision = Literal["execute", "clarify", "confirm", "suggest", "fallback"]
PartKind = Literal["stat", "item_filter"]
UncertaintyMode = Literal["choose", "confirm"]
ReasonCode = Literal[
    "EXACT_MATCH",
    "ALIAS_MATCH",
    "AMBIGUOUS_STAT",
    "FUZZY_STAT",
    "FUZZY_FILTER",
    "UNKNOWN_TOKEN",
    "MULTIPLE_CORRECTIONS",
    "UNSAFE_SHAPE",
]

@dataclass(frozen=True)
class ResolvedItemPart:
    part_kind: PartKind
    typed_text: str
    value: str
    display_label: str
    canonical_text: str
    reason: ReasonCode

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
    reason: ReasonCode
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
    reasons: tuple[ReasonCode, ...] = ()

def understand_item_query(
    raw_query: str,
    *,
    available_stats: list[str] | tuple[str, ...],
    available_item_types: set[str],
    confirmed_choices: tuple[ConfirmedItemChoice, ...] = (),
) -> ItemQueryUnderstanding:
    ...
```

The final implementation should not expose numeric fuzzy scores to callers.

- [ ] **Step 1: Add RED understanding tests**

Create `tests/test_item_query_understanding.py`:

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

    def test_unique_alias_meaning_is_executable(self):
        result = self.understand("xtall cr weapon")
        self.assertEqual(result.decision, "execute")
        self.assertEqual(result.canonical_query, "cr wp xtal")
        self.assertEqual(result.unresolved_tokens, ())
        self.assertEqual(
            [part.display_label for part in result.resolved_parts],
            ["Critical Rate", "Weapon Crysta + Red Enhancer"],
        )

    def test_known_crit_ambiguity_is_structured(self):
        result = self.understand("crit xtal weapon")
        self.assertEqual(result.decision, "clarify")
        issue = result.uncertainties[0]
        self.assertEqual(issue.mode, "choose")
        self.assertEqual(issue.reason, "AMBIGUOUS_STAT")
        self.assertEqual(
            [choice.value for choice in issue.choices],
            ["Critical Rate", "Critical Damage"],
        )

    def test_unknown_word_remains_visible_and_blocks_execution(self):
        result = self.understand("cr weapon xtal blah")
        self.assertEqual(result.decision, "suggest")
        self.assertEqual(result.unresolved_tokens, ("blah",))
        self.assertEqual(result.suggested_query, "cr wp xtal")

    def test_rank_word_is_not_silently_discarded(self):
        result = self.understand("highest xtall cr weapon")
        self.assertEqual(result.decision, "suggest")
        self.assertEqual(result.unresolved_tokens, ("highest",))
        self.assertEqual(result.suggested_query, "cr wp xtal")

    def test_two_stats_do_not_gain_implicit_boolean_semantics(self):
        result = self.understand("cr hp weapon xtal")
        self.assertEqual(result.decision, "fallback")
        self.assertIsNone(result.suggested_query)

    def test_oversized_input_fails_closed(self):
        query = " ".join(["show"] * 30 + ["cr", "weapon", "xtal"])
        result = self.understand(query)
        self.assertEqual(result.decision, "fallback")
        self.assertIn("UNSAFE_SHAPE", result.reasons)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run RED test**

```bash
python -m unittest tests.test_item_query_understanding -v
```

Expected: FAIL because `toram_search.understanding` does not exist.

- [ ] **Step 3: Implement exact/alias/ambiguity/partial recognition**

Use these rules in `understand_item_query()`:

1. Normalize with `tokenize_item_query()`.
2. If token count is greater than 24, return `fallback` with `UNSAFE_SHAPE`.
3. If raw input contains a comparison operator or standalone `and`/`or`, return `fallback`; the existing parser owns supported complex syntax.
4. Find the largest exact filter semantic match using Task 1. If filter semantics are ambiguous, return `fallback`.
5. For each exact filter token-removal option, inspect non-filler remaining tokens.
6. First resolve the whole remaining phrase with `resolve_stat_term(..., allow_fuzzy=False)`.
7. If the whole phrase is unknown, enumerate contiguous remaining-token spans longest-first and collect exact/alias/known-ambiguous stat spans. A single semantic stat span plus leftover words is partial understanding. Two independent resolved stat spans return `fallback` rather than inventing AND/OR.
8. Keep the current explicit filler set (`a`, `an`, `can`, `find`, `give`, `gives`, `has`, `have`, `having`, `i`, `me`, `show`, `some`, `that`, `the`, `want`, `which`, `with`, `you`). Do **not** put `highest`, `best`, or `most` into that filler set.
9. Build canonical syntax only from one semantic stat + one semantic filter. Use `preferred_stat_alias()` when available and the compact filter phrase from Task 1.
10. Stable issue IDs must include source token indexes, for example `stat:0:crit`; do not key pending choices only by display label.

Decision table:

```text
resolved stat + resolved filter + no unresolved -> execute
known stat ambiguity + resolved filter          -> clarify
resolved stat + resolved filter + leftovers    -> suggest
multiple independent stats                     -> fallback
no unique stat/filter core                     -> fallback
```

- [ ] **Step 4: Verify Task 2**

```bash
python -m unittest tests.test_item_query_understanding tests.test_query_reconstruction -v
```

Expected: PASS.

- [ ] **Step 5: Commit Task 2**

```bash
git add toram_search/understanding.py tests/test_item_query_understanding.py
git commit -m "feat: add deterministic item query understanding"
```

---

### Task 3: Add Conservative Fuzzy Confirmation Candidates

**Files:**
- Modify: `toram_search/item_query_entities.py`
- Modify: `toram_search/understanding.py`
- Modify: `tests/test_item_query_entities.py`
- Modify: `tests/test_item_query_understanding.py`

**Interfaces:**

```python
def find_unique_fuzzy_item_filter_match(
    tokens: tuple[QueryToken, ...],
    available_item_types: set[str],
    *,
    fuzzy_threshold: float = 85.0,
) -> ItemFilterMatch | None:
    ...
```

Fuzzy stat resolution uses existing `resolve_stat_term()` with `fuzzy_threshold=85.0` and `limit=2`.

- [ ] **Step 1: Add RED fuzzy tests**

Add to `tests/test_item_query_entities.py`:

```python
from toram_search.item_query_entities import find_unique_fuzzy_item_filter_match


def test_wepon_xtal_has_one_fuzzy_filter_candidate(self):
    tokens = tokenize_item_query("cr wepon xtal")
    match = find_unique_fuzzy_item_filter_match(tokens, TYPES)
    self.assertIsNotNone(match)
    self.assertEqual(
        match.phrase.item_types,
        ("Weapon Crysta", "Enhancer Crysta (Red)"),
    )
    self.assertEqual(match.canonical_phrase, "wp xtal")
    self.assertEqual(match.match_kind, "fuzzy")
```

Add to `tests/test_item_query_understanding.py`:

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
    self.assertEqual(
        [issue.reason for issue in result.uncertainties],
        ["AMBIGUOUS_STAT", "FUZZY_FILTER"],
    )
```

- [ ] **Step 2: Run RED tests**

```bash
python -m unittest tests.test_item_query_entities tests.test_item_query_understanding -v
```

Expected: FAIL on the new fuzzy APIs/decisions.

- [ ] **Step 3: Implement fuzzy filter matching**

Use `rapidfuzz.fuzz`. For each contiguous normalized token span up to the longest filter phrase, compare it to every catalog phrase:

```python
weighted = float(fuzz.WRatio(span_text, row.phrase))
token_score = float(fuzz.token_sort_ratio(span_text, row.phrase))
score = min(weighted, token_score)
```

Keep only `score >= 85.0`. Collapse candidates by semantic filter key `(label, item_types)` and retain the highest-scoring row/span for each semantic key. Return a candidate only when exactly one semantic filter key clears the threshold. Several aliases for the same semantic filter are fine; two distinct semantic filter keys means `None`.

Exact matching always runs first. Do not fuzzy-match across filler words or non-contiguous spans.

- [ ] **Step 4: Implement fuzzy stat confirmation**

Only after exact/alias/known-ambiguity stat recognition fails, call:

```python
resolve_stat_term(
    typed_stat,
    available_stats,
    allow_fuzzy=True,
    fuzzy_threshold=85.0,
    limit=2,
)
```

Create a `FUZZY_STAT` confirmation only when status is `fuzzy` and exactly one candidate remains. More than one fuzzy stat candidate fails closed.

Order uncertainty objects:

```text
AMBIGUOUS_STAT
FUZZY_STAT
FUZZY_FILTER
```

Only the first unresolved issue is presented to the user at a time, but the full tuple remains available to the service for deterministic continuation.

- [ ] **Step 5: Verify Task 3**

```bash
python -m unittest tests.test_item_query_entities tests.test_item_query_understanding tests.test_query_reconstruction -v
```

Expected: PASS.

- [ ] **Step 6: Commit Task 3**

```bash
git add toram_search/item_query_entities.py toram_search/understanding.py tests/test_item_query_entities.py tests/test_item_query_understanding.py
git commit -m "feat: add safe fuzzy item query confirmations"
```

---

### Task 4: Add Pending State and Put Understanding Before Qwen

**Files:**
- Modify: `toram_search/session.py`
- Modify: `toram_search/understanding.py`
- Modify: `toram_search/service.py`
- Modify: `tests/test_search_service.py`
- Modify: `tests/test_item_query_understanding.py`

**Interfaces:**

In `toram_search/session.py`:

```python
@dataclass(frozen=True)
class PendingItemSearch:
    original_query: str
    understanding: ItemQueryUnderstanding
    confirmed_choices: tuple[ConfirmedItemChoice, ...] = ()
```

In `toram_search/service.py`:

```python
ServiceKind = Literal[
    "search",
    "confirm_search",
    "item_understanding",
    "help",
    "database",
    "refuse",
    "unavailable",
    "failed",
]

@dataclass(frozen=True)
class ServiceOutcome:
    kind: ServiceKind
    payload: SearchPayload | None = None
    text: str | None = None
    search_requests: tuple[SearchIntentRequest, ...] = ()
    suggested_query: str | None = None
    pending_item_search: PendingItemSearch | None = None

def continue_item_understanding(
    self,
    pending: PendingItemSearch,
    issue_id: str,
    selected_value: str,
    context: FailedQueryContext,
) -> ServiceOutcome:
    ...

def confirm_pending_item_search(
    self,
    pending: PendingItemSearch,
    context: FailedQueryContext,
) -> ServiceOutcome:
    ...
```

The existing `confirm_search` kind remains the explicit source marker for Qwen-derived structured requests. Do not convert Qwen proposals into deterministic understanding objects merely to unify types; their separate path already guarantees that LLM origin cannot be mistaken for deterministic execution.

- [ ] **Step 1: Add RED no-Qwen/no-history tests**

In `tests/test_search_service.py`:

```python
def test_partial_safe_suggestion_skips_qwen_and_failure_history(self):
    repository = FakeRepository()
    service = SearchService(repository, llm_client=MustNotCallLLM())
    context = FailedQueryContext(max_entries=3)

    outcome = service.handle_query("highest xtall cr weapon", context)

    self.assertEqual(outcome.kind, "item_understanding")
    self.assertEqual(outcome.pending_item_search.understanding.decision, "suggest")
    self.assertEqual(
        outcome.pending_item_search.understanding.suggested_query,
        "cr wp xtal",
    )
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
    self.assertEqual(repository.expression_calls, [])
```

Replace the old test that expects `highest xtall cr weapon` to call Qwen; after this task that behavior is intentionally obsolete.

- [ ] **Step 2: Add RED single-choice continuation test**

```python
def test_one_fuzzy_filter_confirmation_executes_after_revalidation(self):
    repository = FakeRepository()
    service = SearchService(repository, llm_client=MustNotCallLLM())
    context = FailedQueryContext(max_entries=3)

    first = service.handle_query("cr wepon xtal", context)
    issue = first.pending_item_search.understanding.uncertainties[0]
    outcome = service.continue_item_understanding(
        first.pending_item_search,
        issue.issue_id,
        issue.choices[0].value,
        context,
    )

    self.assertEqual(outcome.kind, "search")
    self.assertEqual(len(repository.expression_calls), 1)
    self.assertEqual(context.snapshot(), ())
```

- [ ] **Step 3: Add RED multi-choice final-confirmation test**

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

- [ ] **Step 4: Apply confirmed choices deterministically**

`understand_item_query()` must map `confirmed_choices` by stable `issue_id`. When rediscovering an issue:

- no confirmed entry -> keep the uncertainty;
- confirmed value is currently one of the issue choices -> convert it to a resolved part;
- confirmed value is no longer valid -> return `fallback`.

When all meaning is resolved:

```text
0 confirmed choices -> execute
1 confirmed choice  -> execute
2+ confirmed choices -> confirm with MULTIPLE_CORRECTIONS and no uncertainty
```

Canonical syntax is rebuilt from selected semantic values; never trust button text as executable syntax.

- [ ] **Step 5: Integrate deterministic understanding before Qwen**

In `SearchService.handle_query()` keep current deterministic `search/help/database/refuse` handling first. For unresolved routes, call:

```python
understanding = understand_item_query(
    query,
    available_stats=self.repository.list_stat_names(),
    available_item_types=self.repository.list_item_types(),
)
```

Then:

```text
execute  -> reroute canonical query deterministically -> materialize
clarify  -> item_understanding outcome; no Qwen; no failure history
confirm  -> item_understanding outcome; no Qwen; no failure history
suggest  -> item_understanding outcome; no Qwen; no failure history
fallback -> record failure if route.record_failure -> call Qwen once
```

Remove the live post-Qwen `try_suggest_query()` branch from `handle_query()`. Keep `try_suggest_query()` itself for reconstruction compatibility tests.

- [ ] **Step 6: Implement continuation methods**

`continue_item_understanding()` validates the **first** current uncertainty and the selected semantic value, appends `ConfirmedItemChoice`, reruns `understand_item_query()` against `pending.original_query`, and either returns another pending outcome or executes a newly complete one-choice interpretation through `core.route_deterministically()`.

`confirm_pending_item_search()` accepts only:

```text
decision=suggest with suggested_query present
or
decision=confirm with no uncertainty and canonical_query present
```

It reroutes the target through the deterministic router and executes only if the reroute is a valid search. It never calls Qwen.

- [ ] **Step 7: Preserve Qwen confirmation boundary**

Keep tests proving:

```text
fallback -> exactly one Qwen call
Qwen search candidate -> ServiceOutcome("confirm_search")
no repository search before confirm_search_request()
confirm_search_request() -> parse_structured_search_request() -> execution
rejected Qwen candidate -> no execution
```

Also assert `FailedQueryContext` gains an entry only on the actual fallback/Qwen path.

- [ ] **Step 8: Verify Task 4**

```bash
python -m unittest tests.test_item_query_understanding tests.test_search_service -v
```

Expected: PASS.

- [ ] **Step 9: Commit Task 4**

```bash
git add toram_search/session.py toram_search/understanding.py toram_search/service.py tests/test_item_query_understanding.py tests/test_search_service.py
git commit -m "feat: route item understanding before qwen"
```

---

### Task 5: Add Discord Human-Readable Clarification and Confirmation Controls

**Files:**
- Modify: `discord_bot.py`
- Modify: `tests/test_discord_bot.py`

**Interfaces:**

Add to `DiscordSearchSession`:

```python
pending_item_search: PendingItemSearch | None = None
```

Add:

```python
def build_item_understanding_embed(pending: PendingItemSearch) -> discord.Embed:
    ...

class ItemUnderstandingView(SessionBoundView):
    def __init__(
        self,
        *,
        sessions: DiscordSessionManager,
        key: SessionKey,
        generation: int,
        database_path: Path,
        pending: PendingItemSearch,
    ) -> None:
        ...
```

Add sync wrappers:

```python
def run_item_understanding_choice_sync(
    database_path: Path,
    pending: PendingItemSearch,
    issue_id: str,
    selected_value: str,
    context: FailedQueryContext,
    *,
    repository_factory=core.ItemRepository,
) -> ServiceOutcome:
    repository = repository_factory(database_path.resolve())
    try:
        return SearchService(repository).continue_item_understanding(
            pending,
            issue_id,
            selected_value,
            context,
        )
    finally:
        repository.close()


def run_pending_item_search_confirmation_sync(
    database_path: Path,
    pending: PendingItemSearch,
    context: FailedQueryContext,
    *,
    repository_factory=core.ItemRepository,
) -> ServiceOutcome:
    repository = repository_factory(database_path.resolve())
    try:
        return SearchService(repository).confirm_pending_item_search(pending, context)
    finally:
        repository.close()
```

- [ ] **Step 1: Add an exact test helper and RED formatting test**

In `tests/test_discord_bot.py`, import `PendingItemSearch` and `understand_item_query`, then add:

```python
UNDERSTANDING_STATS = ["Critical Rate", "Critical Damage", "MaxHP"]
UNDERSTANDING_TYPES = {
    "Weapon Crysta",
    "Enhancer Crysta (Red)",
    "Armor",
    "Bow",
}


def make_pending_item_search(query: str) -> PendingItemSearch:
    understanding = understand_item_query(
        query,
        available_stats=UNDERSTANDING_STATS,
        available_item_types=UNDERSTANDING_TYPES,
    )
    return PendingItemSearch(query, understanding)
```

Then test:

```python
def test_partial_understanding_embed_shows_known_unknown_and_canonical_form(self):
    pending = make_pending_item_search("cr weapon xtal blah")
    embed = discord_bot.build_item_understanding_embed(pending)
    visible = "\n".join(
        [
            embed.title or "",
            embed.description or "",
            *(field.name + "\n" + field.value for field in embed.fields),
        ]
    )
    self.assertIn("Critical Rate", visible)
    self.assertIn("Weapon Crysta", visible)
    self.assertIn("blah", visible)
    self.assertIn("cr wp xtal", visible)
    self.assertNotIn("UNKNOWN_TOKEN", visible)
```

- [ ] **Step 2: Add RED structured-button test with a real constructor call**

```python
def test_ambiguity_view_uses_structured_choice_buttons(self):
    sessions = DiscordSessionManager()
    key = (10, 30, 20)
    session = sessions.start_query(key, "crit wepon xtal")
    pending = make_pending_item_search("crit wepon xtal")
    view = discord_bot.ItemUnderstandingView(
        sessions=sessions,
        key=key,
        generation=session.generation,
        database_path=Path("coryn_data/database/items.sqlite"),
        pending=pending,
    )
    labels = [child.label for child in view.children if hasattr(child, "label")]
    self.assertIn("Critical Rate", labels)
    self.assertIn("Critical Damage", labels)
```

- [ ] **Step 3: Add RED pending-state lifecycle tests**

Test all four lifecycle rules directly:

```text
item_understanding outcome -> session.pending_item_search set
successful continued search -> pending_item_search cleared
Cancel/No/Edit -> pending_item_search cleared
start_query() -> new session starts with pending_item_search=None and old generation becomes stale
```

For async button callbacks, patch the sync wrapper and call the handler with the existing fake interaction pattern used in this test module; assert the session field after callback completion.

- [ ] **Step 4: Implement embed rendering**

Render semantic labels, not reason codes or fuzzy scores.

For a partial suggestion, visible content should be equivalent to:

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

Decision-specific titles:

```text
clarify -> What does "<typed>" mean?
fuzzy confirm -> Confirm correction
suggest -> I understood part of that search
final multi-correction confirm -> Ready to search
```

- [ ] **Step 5: Implement `ItemUnderstandingView`**

Controls:

```text
clarify:
[candidate buttons] [Cancel]

fuzzy confirm:
[Use <candidate>] [No]

suggest:
[Use suggestion] [Cancel]

final multi-correction confirm:
[Search] [Edit]
```

Choice/Search callbacks defer, run the appropriate sync wrapper through `asyncio.to_thread`, re-check session generation, then call `edit_service_outcome()`.

`No` and `Cancel` clear `session.pending_item_search` and edit the message to `No search was executed.`

`Edit` intentionally does not add a modal in this iteration. It clears `session.pending_item_search` and edits the message to:

```text
Search not executed. Send a new @Bot query with the changes you want.
```

Use `bot_example_prefix(interaction.guild, interaction.client.user)` to replace `@Bot` with the current visible bot name.

- [ ] **Step 6: Handle `item_understanding` outcome in Discord**

In `build_service_outcome_message()`:

1. store `outcome.pending_item_search` in the current session;
2. return `build_item_understanding_embed(pending)` plus `ItemUnderstandingView`;
3. do not clear failed history merely because clarification/suggestion is shown.

When a continuation returns a real `search` outcome, clear `session.pending_item_search` and apply the existing successful-search failed-context clearing rule.

- [ ] **Step 7: Verify Task 5**

```bash
python -m unittest tests.test_discord_bot -v
```

Expected: PASS.

- [ ] **Step 8: Commit Task 5**

```bash
git add discord_bot.py tests/test_discord_bot.py
git commit -m "feat: add item search clarification controls"
```

---

### Task 6: Lock Safety Invariants and Verify the Integrated Feature

**Files:**
- Modify: `tests/test_item_query_understanding.py`
- Modify: `tests/test_search_service.py`
- Modify: `tests/test_discord_bot.py`
- Modify: `tests/test_query_reconstruction.py` only if a regression fixture is needed.
- No planned production changes; any bug discovered here must first receive a failing regression test in its owning task area.

- [ ] **Step 1: Add unknown-token table tests**

In `tests/test_search_service.py`:

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
            self.assertIn(
                "blah",
                outcome.pending_item_search.understanding.unresolved_tokens,
            )
            self.assertEqual(repository.expression_calls, [])
            self.assertEqual(repository.stat_calls, [])
```

- [ ] **Step 2: Add semantic-equivalence tests**

For each path below, execute only after the required confirmation and compare the resolved stat name, item-type tuple, and sort direction to the canonical query result:

```text
xtall cr weapon <=> cr wp xtal
crit wepon xtal + choose Critical Rate + confirm filter + final Search <=> cr wp xtal
cr wepon xtal + confirm filter <=> cr wp xtal
```

Do not assert only on canonical strings; compare repository call semantics.

- [ ] **Step 3: Add clarification-order regression**

Lock this sequence:

```text
highest crit xtal weapon
-> AMBIGUOUS_STAT first
-> choose Critical Rate
-> highest remains visible as unresolved wording
-> safe suggestion cr wp xtal is shown
-> no DB execution before accepting suggestion
```

- [ ] **Step 4: Add Qwen boundary table**

Assert:

```text
safe deterministic suggestion -> 0 Qwen calls
known ambiguity -> 0 Qwen calls
fuzzy confirmation -> 0 Qwen calls
unresolved structure with no safe deterministic action -> exactly 1 Qwen call
Qwen search candidate -> 0 DB search calls before confirm_search_request()
rejected Qwen payload -> 0 DB search calls
```

Assert `FailedQueryContext.snapshot()` remains empty for deterministic clarification/confirmation/suggestion and gains an attempt only on the Qwen fallback path.

- [ ] **Step 5: Add adversarial bound tests**

Cover repeated tokens, ambiguous terms, typo candidates, and inputs longer than 24 normalized tokens. Oversized input must return fallback/unsafe before combinatorial candidate enumeration.

- [ ] **Step 6: Run focused feature gate**

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

- [ ] **Step 7: Compile modified modules**

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

- [ ] **Step 8: Run real-database smoke script**

Run from repository root:

```bash
python - <<'PY'
import search_items as core
from toram_search.service import SearchService, StatClarificationPayload
from toram_search.session import FailedQueryContext


class MustNotCallLLM:
    def complete(self, *args, **kwargs):
        raise AssertionError("Qwen must not be called for deterministic smoke cases")


repository = core.ItemRepository(core.DEFAULT_DATABASE)
try:
    service = SearchService(repository, llm_client=MustNotCallLLM())

    context = FailedQueryContext(max_entries=3)
    direct = service.handle_query("xtall cr weapon", context)
    assert direct.kind == "search", direct
    assert context.snapshot() == ()

    context = FailedQueryContext(max_entries=3)
    ambiguous = service.handle_query("crit xtal weapon", context)
    assert ambiguous.kind == "search", ambiguous
    assert isinstance(ambiguous.payload, StatClarificationPayload), ambiguous.payload
    assert ambiguous.payload.clarification.candidates == (
        "Critical Rate",
        "Critical Damage",
    )
    assert context.snapshot() == ()

    context = FailedQueryContext(max_entries=3)
    partial = service.handle_query("highest xtall cr weapon", context)
    assert partial.kind == "item_understanding", partial
    understanding = partial.pending_item_search.understanding
    assert understanding.decision == "suggest", understanding
    assert understanding.suggested_query == "cr wp xtal", understanding
    assert "highest" in understanding.unresolved_tokens, understanding
    assert context.snapshot() == ()
finally:
    repository.close()
PY
```

Expected: exit 0. When inspecting item-type tuples in any added smoke assertion, compare normalized names because the real DB currently contains casing such as `Enhancer Crysta (red)`.

- [ ] **Step 9: Run full suite and compare to baseline**

```bash
python -m unittest discover -s tests -v
```

Expected: no new feature failures. The repository currently has one known unrelated baseline failure:

```text
test_structured_fallback.StructuredFallbackTests.test_rejected_payload_is_logged_with_reason
expected: "missing or invalid search candidates"
actual log: "search payload has unexpected fields"
```

Do not claim the entire repository suite is green while that baseline failure remains. Report “focused feature gate green; full suite matches baseline” only if that is the sole full-suite failure.

- [ ] **Step 10: Review final diff for scope**

Confirm the diff contains only item-search entity/understanding/reconstruction/session/service/Discord changes and their tests. Reject any boss/skill abstraction, build-role logic, telemetry, arbitrary SQL, or new LLM execution path.

- [ ] **Step 11: Commit safety tests**

If Steps 1–5 added tests, commit them:

```bash
git add tests/test_item_query_understanding.py tests/test_search_service.py tests/test_discord_bot.py tests/test_query_reconstruction.py
git commit -m "test: lock item query understanding safety invariants"
```

If those files were already committed in the owning tasks and no verification-only changes remain, do not create an empty commit.

---

## Expected Behavior Matrix

| Query | Outcome before user action | Qwen calls | DB search before user action |
|---|---|---:|---:|
| `cr weapon xtal` | execute | 0 | yes |
| `xtall cr weapon` | reconstruct + execute | 0 | yes |
| `crit weapon xtal` | CR/CD clarification | 0 | no |
| `weapon xtal crtical rate` | fuzzy stat confirmation | 0 | no |
| `cr wepon xtal` | fuzzy filter confirmation | 0 | no |
| `crit wepon xtal` | CR/CD, then fuzzy filter, then final confirmation | 0 | no until final Search |
| `cr weapon xtal blah` | recognized + unresolved + suggestion | 0 | no |
| `highest xtall cr weapon` | recognized + unresolved `highest` + suggestion | 0 | no |
| `cr hp weapon xtal` | no implicit Boolean semantics; fallback | at most 1 | no until validated Qwen confirmation |
| truly unresolved natural request | constrained fallback | 1 | no until validated Qwen confirmation |

## Completion Definition

The feature is complete only when exact/current item searches remain behaviorally unchanged, deterministic clarification/confirmation/suggestion paths use zero Qwen calls, ambiguity/fuzzy/unknown input cannot auto-execute incorrectly, Discord shows recognized/unresolved meaning in human-readable form, single vs multi-decision confirmation follows the approved policy, Qwen is called only after deterministic understanding has no safe action, Qwen-derived searches still require deterministic validation and confirmation, pending state clears on success/cancel/rejection/edit/new query, the focused gate passes, real-database smoke checks pass, and the full suite has no failures beyond the known unrelated baseline failure.