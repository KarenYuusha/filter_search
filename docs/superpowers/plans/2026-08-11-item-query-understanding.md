# Item Query Understanding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make uncertain item searches explainable and safe while keeping exact/alias searches fast and minimizing Qwen use.

**Architecture:** Preserve `core.route_deterministically()` and the existing parsed-stat clarification path as the fast path. Add a low-level item-filter matcher shared with reconstruction, then a frontend-neutral `ItemQueryUnderstanding` layer for failed simple item queries. `SearchService` owns deterministic revalidation and the Qwen boundary; Discord owns only presentation and minimal pending state.

**Tech Stack:** Python 3, stdlib `dataclasses`/`unittest`, existing `rapidfuzz`, existing SQLite `ItemRepository`, existing Discord.py components, existing Ollama/Qwen fallback.

## Global Constraints

- Exact/current deterministic item searches must remain behaviorally unchanged.
- Only fully resolved deterministic meaning may execute.
- Intentional aliases such as `cr`, `hp`, and configured item-word aliases may execute without confirmation.
- Known semantic ambiguity never auto-executes.
- Fuzzy matching is confirmation-only.
- Unknown meaningful tokens never silently disappear on an auto-execution path.
- Do not add new multi-stat AND/OR/relation semantics.
- Human-readable labels are primary UI; canonical syntax such as `cr wp xtal` is secondary.
- One resolved ambiguity/correction may execute after revalidation; two or more independent interpretation choices require final Search/Edit confirmation.
- Deterministic clarification/confirmation/suggestion must not add failed-query history. Record failure only when the service actually enters Qwen fallback.
- Qwen never provides trusted database facts, chooses result rows, or writes SQL. Qwen-derived searches still require deterministic validation plus user confirmation.
- Preserve the existing 24-token reconstruction safety bound.
- Boss/skill/domain abstractions, build-role semantics, telemetry, and numeric confidence scoring are out of scope.

---

## File Map

**Create**
- `toram_search/item_query_entities.py` — normalized tokens plus exact/fuzzy item-filter matching.
- `toram_search/understanding.py` — structured understanding data, reason codes, stat/filter interpretation, confirmation policy.
- `tests/test_item_query_entities.py`
- `tests/test_item_query_understanding.py`

**Modify**
- `toram_search/reconstruction.py`
- `toram_search/session.py`
- `toram_search/service.py`
- `discord_bot.py`
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

**Produces:**
- `QueryToken`
- `ItemFilterMatch`
- `ExactFilterMatches`
- `tokenize_item_query(raw_query)`
- `find_exact_item_filter_matches(tokens, available_item_types)`
- `remaining_tokens(tokens, consumed_indexes)`
- `semantic_filter_key(row)`
- `canonical_filter_phrase(row, catalog)`

- [ ] **Step 1: Write RED matcher tests**

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
    def test_item_word_alias_is_normalized(self):
        tokens = tokenize_item_query("xtall cr weapon")
        self.assertEqual(
            [token.normalized for token in tokens],
            ["xtal", "cr", "weapon"],
        )

    def test_weapon_xtal_has_one_semantic_filter(self):
        tokens = tokenize_item_query("xtall cr weapon")
        result = find_exact_item_filter_matches(tokens, TYPES)
        self.assertEqual(result.status, "unique")
        self.assertTrue(result.matches)
        self.assertEqual(
            result.matches[0].phrase.item_types,
            ("Weapon Crysta", "Enhancer Crysta (Red)"),
        )
        self.assertEqual(result.matches[0].canonical_phrase, "wp xtal")

    def test_repeated_weapon_preserves_overlap_solution(self):
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

- [ ] **Step 2: Verify RED**

```bash
python -m unittest tests.test_item_query_entities -v
```

Expected: import failure because the module does not exist.

- [ ] **Step 3: Implement the shared matcher**

Create `toram_search/item_query_entities.py` with this structure and logic:

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
    options = {
        tuple(sorted(index for group in selected for index in group))
        for selected in product(*groups)
    }
    return tuple(sorted(options))


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

- [ ] **Step 4: Refactor reconstruction onto the shared exact matcher**

Keep `ReconstructionResult`, `try_reconstruct_simple_search()`, and `try_suggest_query()` public behavior unchanged. Replace only duplicated token/filter-position matching. Preserve:

```text
24-token bound
known filler handling
rank-word behavior in try_suggest_query()
allow_fuzzy=False for auto reconstruction
weapon atk / weapon xtal overlap handling
crit ambiguity behavior
```

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

### Task 2: Add Structured Exact, Ambiguous, and Partial Understanding

**Files:**
- Create: `toram_search/understanding.py`
- Create: `tests/test_item_query_understanding.py`

**Produces:**

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
```

and immutable dataclasses `ResolvedItemPart`, `ItemQueryChoice`, `ItemQueryUncertainty`, `ConfirmedItemChoice`, `ItemQueryUnderstanding` plus `understand_item_query()`.

- [ ] **Step 1: Write RED understanding tests**

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

    def test_unknown_word_stays_visible(self):
        result = self.understand("cr weapon xtal blah")
        self.assertEqual(result.decision, "suggest")
        self.assertEqual(result.unresolved_tokens, ("blah",))
        self.assertEqual(result.suggested_query, "cr wp xtal")

    def test_highest_is_not_silently_discarded(self):
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
```

- [ ] **Step 2: Verify RED**

```bash
python -m unittest tests.test_item_query_understanding -v
```

Expected: import failure because `toram_search.understanding` does not exist.

- [ ] **Step 3: Implement the model and decision rules**

Use these exact fields:

```python
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
```

Implement `understand_item_query()` with this algorithm:

```text
1. tokenize using tokenize_item_query()
2. >24 tokens -> fallback + UNSAFE_SHAPE
3. comparison operator or standalone and/or -> fallback; existing parser owns complex syntax
4. find largest exact item-filter semantic match
5. for each filter-removal option, remove configured fillers only
6. resolve whole remaining stat phrase with resolve_stat_term(... allow_fuzzy=False)
7. if whole phrase is unknown, enumerate contiguous stat spans longest-first
8. one exact/alias/known-ambiguous stat span + leftover tokens -> partial understanding
9. two independent stat spans -> fallback, never invent AND/OR
10. `highest`, `best`, `most` remain unresolved rather than filler
11. canonical query requires one semantic stat + one semantic filter
12. use preferred_stat_alias() and canonical_filter_phrase()
```

Use the current filler set from reconstruction. Stable issue IDs must include source token indexes, for example `stat:0:crit`.

Decision table:

```text
resolved stat + resolved filter + no leftovers -> execute
known stat ambiguity + resolved filter         -> clarify
resolved stat + resolved filter + leftovers   -> suggest
multiple independent stats                    -> fallback
no unique stat/filter core                    -> fallback
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

### Task 3: Add Conservative Fuzzy Stat and Filter Confirmation

**Files:**
- Modify: `toram_search/item_query_entities.py`
- Modify: `toram_search/understanding.py`
- Modify: `tests/test_item_query_entities.py`
- Modify: `tests/test_item_query_understanding.py`

**Produces:** `find_unique_fuzzy_item_filter_match(tokens, available_item_types, fuzzy_threshold=85.0)`.

- [ ] **Step 1: Write RED fuzzy tests**

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
    self.assertEqual([choice.value for choice in issue.choices], ["Critical Rate"])


def test_fuzzy_filter_requires_confirmation(self):
    result = self.understand("cr wepon xtal")
    self.assertEqual(result.decision, "confirm")
    self.assertEqual(result.uncertainties[0].reason, "FUZZY_FILTER")


def test_semantic_ambiguity_precedes_fuzzy_filter(self):
    result = self.understand("crit wepon xtal")
    self.assertEqual(result.decision, "clarify")
    self.assertEqual(
        [issue.reason for issue in result.uncertainties],
        ["AMBIGUOUS_STAT", "FUZZY_FILTER"],
    )
```

- [ ] **Step 2: Verify RED**

```bash
python -m unittest tests.test_item_query_entities tests.test_item_query_understanding -v
```

Expected: failures on missing fuzzy behavior.

- [ ] **Step 3: Implement fuzzy filter candidate generation**

Reuse `rapidfuzz.fuzz`. For each contiguous normalized token span up to the longest catalog phrase:

```python
weighted = float(fuzz.WRatio(span_text, row.phrase))
token_score = float(fuzz.token_sort_ratio(span_text, row.phrase))
score = min(weighted, token_score)
```

Keep `score >= 85.0`, collapse aliases by semantic filter key `(label, item_types)`, and return a candidate only when exactly one semantic filter key clears the threshold. Multiple aliases for one semantic filter are acceptable; two distinct semantic filters fail closed. Exact filter matching always takes precedence. Do not fuzzy-match across filler words or non-contiguous spans.

- [ ] **Step 4: Implement fuzzy stat candidate generation**

Only after exact/alias/known-ambiguity stat recognition fails:

```python
resolution = resolve_stat_term(
    typed_stat,
    available_stats,
    allow_fuzzy=True,
    fuzzy_threshold=85.0,
    limit=2,
)
```

Create `FUZZY_STAT` only when `resolution.status == "fuzzy"` and exactly one candidate exists. Otherwise fail closed.

Sort uncertainties:

```text
AMBIGUOUS_STAT
FUZZY_STAT
FUZZY_FILTER
```

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

### Task 4: Add Pending State and Route Understanding Before Qwen

**Files:**
- Modify: `toram_search/session.py`
- Modify: `toram_search/understanding.py`
- Modify: `toram_search/service.py`
- Modify: `tests/test_search_service.py`
- Modify: `tests/test_item_query_understanding.py`

**Produces:**
- `PendingItemSearch`
- service kind `item_understanding`
- `ServiceOutcome.pending_item_search`
- `SearchService.continue_item_understanding()`
- `SearchService.confirm_pending_item_search()`

- [ ] **Step 1: Write RED no-Qwen tests**

Add to `tests/test_search_service.py`:

```python
def test_partial_suggestion_skips_qwen_and_failure_history(self):
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
```

Update the old test that expected `highest xtall cr weapon` to call Qwen; that expectation is obsolete after this task.

- [ ] **Step 2: Write RED single-choice and multi-choice tests**

```python
def test_one_fuzzy_filter_choice_executes_after_revalidation(self):
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


def test_two_choices_require_final_confirmation(self):
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
```

- [ ] **Step 3: Add pending state**

In `toram_search/session.py`:

```python
from toram_search.understanding import ConfirmedItemChoice, ItemQueryUnderstanding


@dataclass(frozen=True)
class PendingItemSearch:
    original_query: str
    understanding: ItemQueryUnderstanding
    confirmed_choices: tuple[ConfirmedItemChoice, ...] = ()
```

Keep `FailedQueryContext` unchanged and separate.

- [ ] **Step 4: Apply confirmed choices in understanding**

Build a mapping from `confirmed_choices` by `issue_id`. On rediscovery:

```text
no confirmed value -> keep uncertainty
confirmed value is still one of current choices -> convert to resolved part
confirmed value is no longer valid -> fallback
```

When all meaning is resolved:

```text
0 choices -> execute
1 choice -> execute
2+ choices -> confirm + MULTIPLE_CORRECTIONS with no remaining uncertainty
```

Canonical syntax must be rebuilt from selected semantic values, not from user-visible button text.

- [ ] **Step 5: Integrate understanding before Qwen**

In `SearchService.handle_query()`, keep existing deterministic `search/help/database/refuse` handling first. For an unresolved route:

```python
understanding = understand_item_query(
    query,
    available_stats=self.repository.list_stat_names(),
    available_item_types=self.repository.list_item_types(),
)
```

Then apply:

```text
execute -> reroute canonical query deterministically -> materialize
clarify/confirm/suggest -> return item_understanding; no Qwen; no failure history
fallback -> record failure if route.record_failure -> call Qwen once
```

Remove the live post-Qwen safe-suggestion branch from `handle_query()`. Keep `try_suggest_query()` itself for compatibility tests.

Extend `ServiceOutcome`:

```python
pending_item_search: PendingItemSearch | None = None
```

Add `item_understanding` to `ServiceKind`.

- [ ] **Step 6: Implement continuation methods**

`continue_item_understanding()`:

```text
validate issue_id equals the first current uncertainty
validate selected_value belongs to that issue
append ConfirmedItemChoice
rerun understand_item_query(original_query, confirmed_choices=updated)
if decision execute -> reroute canonical query and materialize
otherwise -> return item_understanding with updated PendingItemSearch
```

`confirm_pending_item_search()` accepts only:

```text
decision=suggest with suggested_query present
or
decision=confirm with no uncertainty and canonical_query present
```

It reroutes the selected canonical query through `core.route_deterministically()` and executes only a valid deterministic search. It never calls Qwen.

- [ ] **Step 7: Preserve Qwen boundary tests**

Keep tests proving:

```text
true fallback -> exactly one Qwen call
Qwen search candidate -> confirm_search
no DB search before confirm_search_request()
confirm_search_request() -> deterministic structured parsing -> execution
rejected Qwen request -> no execution
```

Assert failed history is added only on the true Qwen path.

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

### Task 5: Add Discord Human-Readable Controls

**Files:**
- Modify: `discord_bot.py`
- Modify: `tests/test_discord_bot.py`

**Produces:**
- `DiscordSearchSession.pending_item_search`
- `build_item_understanding_embed()`
- `ItemUnderstandingView`
- `run_item_understanding_choice_sync()`
- `run_pending_item_search_confirmation_sync()`

- [ ] **Step 1: Add RED formatting/button tests**

In `tests/test_discord_bot.py`:

```python
from toram_search.session import PendingItemSearch
from toram_search.understanding import understand_item_query


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

Add:

```python
def test_partial_understanding_embed_is_human_readable(self):
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


def test_ambiguity_view_has_structured_choice_buttons(self):
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

- [ ] **Step 2: Add pending state and sync wrappers**

Add to `DiscordSearchSession`:

```python
pending_item_search: PendingItemSearch | None = None
```

Add wrappers using the existing repository open/close pattern:

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

- [ ] **Step 3: Implement human-readable embed rendering**

For partial understanding, render semantic content equivalent to:

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

Titles:

```text
clarify -> What does "<typed>" mean?
fuzzy confirm -> Confirm correction
suggest -> I understood part of that search
final multi-correction confirm -> Ready to search
```

Never expose reason-code strings or fuzzy scores.

- [ ] **Step 4: Implement `ItemUnderstandingView` controls**

Use `SessionBoundView` and `ActionButton`.

```text
clarify: candidate buttons + Cancel
fuzzy confirm: Use <candidate> + No
suggest: Use suggestion + Cancel
final confirm: Search + Edit
```

Choice/Search callbacks must defer, call the sync wrapper through `asyncio.to_thread`, verify the generation is still current, and then call `edit_service_outcome()`.

`No` and `Cancel` clear `session.pending_item_search` and edit the message to `No search was executed.`

`Edit` clears `session.pending_item_search` and edits the message to:

```text
Search not executed. Send a new @Bot query with the changes you want.
```

Resolve `@Bot` with `bot_example_prefix(interaction.guild, interaction.client.user)`. Do not add a modal in this iteration.

- [ ] **Step 5: Handle item-understanding outcomes in Discord**

In `build_service_outcome_message()`:

```text
store outcome.pending_item_search on current session
build item-understanding embed
return ItemUnderstandingView
leave FailedQueryContext untouched while waiting for user action
```

When continuation yields a real search, clear `pending_item_search` and apply the existing successful-search failed-context clearing rule.

A new `start_query()` naturally creates a new session with `pending_item_search=None`, so old controls are invalidated by generation.

- [ ] **Step 6: Add lifecycle assertions**

Add tests that verify:

```python
def test_new_query_drops_pending_item_understanding(self):
    sessions = DiscordSessionManager()
    key = (10, 30, 20)
    first = sessions.start_query(key, "crit wepon xtal")
    first.pending_item_search = make_pending_item_search("crit wepon xtal")
    second = sessions.start_query(key, "cr bow")
    self.assertIsNone(second.pending_item_search)
    self.assertFalse(sessions.is_current(key, first.generation))
```

For Cancel/No/Edit callbacks, assert `sessions.get(key).pending_item_search is None` after the callback returns.

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

### Task 6: Lock Safety Invariants and Verify Integration

**Files:**
- Modify: `tests/test_item_query_understanding.py`
- Modify: `tests/test_search_service.py`
- Modify: `tests/test_discord_bot.py`
- Modify: `tests/test_query_reconstruction.py` only when a new regression fixture is necessary.

- [ ] **Step 1: Add unknown-token safety table**

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

- [ ] **Step 2: Add semantic-equivalence regression tests**

For each path below, compare resolved stat name, item-type tuple, and sort direction against the canonical query's repository call:

```text
xtall cr weapon <=> cr wp xtal
cr wepon xtal + confirm filter <=> cr wp xtal
crit wepon xtal + choose Critical Rate + confirm filter + final Search <=> cr wp xtal
```

Do not assert only on canonical strings.

- [ ] **Step 3: Add clarification-order regression**

Lock:

```text
highest crit xtal weapon
-> AMBIGUOUS_STAT first
-> choose Critical Rate
-> highest remains unresolved
-> safe suggestion cr wp xtal
-> no DB execution before accepting suggestion
```

- [ ] **Step 4: Add Qwen boundary regression table**

Assert:

```text
safe deterministic suggestion -> 0 Qwen calls
known ambiguity -> 0 Qwen calls
fuzzy confirmation -> 0 Qwen calls
true unresolved structure -> exactly 1 Qwen call
Qwen search candidate -> no DB call before confirm_search_request()
rejected Qwen payload -> no DB call
```

Also assert failed context is empty for deterministic clarification/confirmation/suggestion and gains an entry only when fallback actually calls Qwen.

- [ ] **Step 5: Add adversarial bound cases**

Cover repeated tokens, ambiguity, typo candidates, and inputs longer than 24 normalized tokens. Oversized input must fail closed before expensive matching.

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

- [ ] **Step 8: Run real-database smoke checks**

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

Expected: exit 0. If an item-type tuple is asserted in an added smoke check, normalize names because the real DB currently contains casing such as `Enhancer Crysta (red)`.

- [ ] **Step 9: Run full suite and compare with baseline**

```bash
python -m unittest discover -s tests -v
```

Expected: no new feature failures. Current known unrelated baseline failure:

```text
test_structured_fallback.StructuredFallbackTests.test_rejected_payload_is_logged_with_reason
expected: "missing or invalid search candidates"
actual log: "search payload has unexpected fields"
```

Do not claim the whole repository suite is green while this remains. Report `focused feature gate green; full suite matches baseline` only when this is the sole full-suite failure.

- [ ] **Step 10: Review scope and commit safety tests**

The diff must contain only item-search entity/understanding/reconstruction/session/service/Discord changes plus tests. If Steps 1–5 added test changes, commit them:

```bash
git add tests/test_item_query_understanding.py tests/test_search_service.py tests/test_discord_bot.py tests/test_query_reconstruction.py
git commit -m "test: lock item query understanding safety invariants"
```

If no uncommitted verification changes remain, do not create an empty commit.

---

## Expected Behavior Matrix

| Query | Outcome before user action | Qwen | DB before action |
|---|---|---:|---:|
| `cr weapon xtal` | execute | 0 | yes |
| `xtall cr weapon` | reconstruct + execute | 0 | yes |
| `crit weapon xtal` | CR/CD clarification | 0 | no |
| `weapon xtal crtical rate` | fuzzy stat confirmation | 0 | no |
| `cr wepon xtal` | fuzzy filter confirmation | 0 | no |
| `crit wepon xtal` | CR/CD, fuzzy filter, final confirmation | 0 | no |
| `cr weapon xtal blah` | recognized + unresolved + suggestion | 0 | no |
| `highest xtall cr weapon` | recognized + unresolved `highest` + suggestion | 0 | no |
| `cr hp weapon xtal` | no implicit Boolean semantics; fallback | at most 1 | no |
| truly unresolved natural request | constrained Qwen fallback | 1 | no |

## Completion Definition

Complete means: current exact searches are unchanged; deterministic clarification/confirmation/suggestion uses zero Qwen calls; ambiguity/fuzzy/unknown input cannot auto-execute incorrectly; Discord renders recognized/unresolved meaning human-readably; single versus multiple interpretation decisions follow the approved policy; Qwen is called only after deterministic understanding has no safe action; Qwen-derived searches still require deterministic validation and confirmation; pending state clears on success/cancel/rejection/edit/new query; the focused gate passes; real-database smoke checks pass; and the full suite has no failures beyond the known unrelated baseline failure.