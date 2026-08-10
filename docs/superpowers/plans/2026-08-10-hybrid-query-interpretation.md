# Hybrid Query Interpretation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users search with flexible natural wording without memorizing compact syntax, while keeping simple known queries off the LLM path and validating every LLM proposal before database execution.

**Architecture:** Keep `route_deterministically` as the first fast path. Add one strict deterministic reconstruction module that recognizes a single known stat plus a single known item filter in flexible order, then compiles that interpretation back into a canonical query which must pass the existing router. Only unresolved cases call Qwen once; successful Qwen search interpretations remain confirmation-gated, while failed Qwen interpretations may return one parser-validated suggestion.

**Tech Stack:** Python 3, `unittest`, existing `toram_data` alias/parser code, `toram_search.SearchService`, constrained Ollama/Qwen fallback, Discord.py.

## Global Constraints

- Users must not be required to memorize canonical query syntax.
- Keep the existing deterministic parser as the fastest path.
- High-confidence deterministic reconstruction runs before Qwen.
- Do not route every query through Qwen.
- Make at most one Qwen interpretation call per user query.
- Qwen interprets meaning; Python validates; the database answers.
- Qwen must never write arbitrary SQL, directly choose database rows, or answer item facts from memory.
- Automatic reconstruction may use only exact canonical stats, exact unambiguous aliases, intentional item-word aliases, and exact known item-filter phrases; no fuzzy semantic execution.
- Explicit parser ambiguity such as `crit` must remain a clarification, not a guessed stat.
- Unknown meaningful tokens must never be silently discarded to force a match.
- Automatic v1 reconstruction supports one stat + one item filter + conservative filler words only.
- Comparison, Boolean, multi-stat, and ranking reconstruction are not added to the automatic v1 path.
- Post-Qwen suggestions are guidance only and must never auto-execute.
- Every generated canonical query or suggestion must pass the existing deterministic router before use.
- `refuse` and `unavailable` behavior remains unchanged.
- Keep tank/DPS/build concepts out of scope.
- Current `main` contains merged PR #67 at commit `ecf04bbd235a6706fd75cbd9f93ed490693b54fa`; create the implementation branch from the latest `main`, not from this documentation branch.
- Refresh the full-suite baseline from clean `main` before final verification. The feature branch must introduce zero new failures relative to that baseline.

## File Map

- Modify `toram_data/stat_query.py` — expose the existing item-filter phrase catalog without duplicating filter definitions.
- Create `toram_search/reconstruction.py` — strict flexible-order reconstruction and safe suggestion generation.
- Modify `toram_search/service.py` — pre-Qwen reconstruction, failed-query suggestion attachment, and one-call orchestration.
- Modify `discord_bot.py` — render specific suggestions when available.
- Create `tests/test_query_reconstruction.py` — reconstruction unit tests.
- Modify `tests/test_search_service.py` — service routing, Qwen-call-count, history, and ambiguity tests.
- Modify `tests/test_discord_bot.py` — Discord rendering tests.

---

### Task 1: Build the Strict Reconstruction Component

**Files:**
- Modify: `toram_data/stat_query.py`
- Create: `toram_search/reconstruction.py`
- Create: `tests/test_query_reconstruction.py`

**Interfaces:**
- Produces: `ItemFilterPhrase(phrase: str, label: str, item_types: tuple[str, ...])`
- Produces: `list_item_filter_phrases(available_item_types: set[str]) -> tuple[ItemFilterPhrase, ...]`
- Produces: `ReconstructionResult(kind, canonical_query, stat_resolution, filter_phrase)`
- Produces: `try_reconstruct_simple_search(raw_query: str, *, available_stats: Iterable[str], available_item_types: set[str]) -> ReconstructionResult`
- Produces: `try_suggest_query(raw_query: str, *, available_stats: Iterable[str], available_item_types: set[str]) -> str | None`
- Depends on: `ITEM_WORD_ALIASES`, `resolve_stat_term(..., allow_fuzzy=False)`, `preferred_stat_alias`, and the existing `_filter_candidates()` definitions.

- [ ] **Step 1: Write failing unit tests**

Create `tests/test_query_reconstruction.py`:

```python
import unittest

from toram_search.reconstruction import (
    try_reconstruct_simple_search,
    try_suggest_query,
)


AVAILABLE_STATS = ["Critical Rate", "Critical Damage", "MaxHP", "Weapon ATK"]
AVAILABLE_TYPES = {
    "Weapon Crysta",
    "Enhancer Crysta (Red)",
    "Armor Crysta",
    "Enhancer Crysta (Green)",
    "Armor",
    "Bow",
}


class QueryReconstructionTests(unittest.TestCase):
    def reconstruct(self, text):
        return try_reconstruct_simple_search(
            text,
            available_stats=AVAILABLE_STATS,
            available_item_types=AVAILABLE_TYPES,
        )

    def test_reorders_known_weapon_xtal_tokens(self):
        result = self.reconstruct("xtall cr weapon")
        self.assertEqual(result.kind, "success")
        self.assertEqual(result.canonical_query, "cr wp xtal")
        self.assertEqual(result.stat_resolution.candidates, ("Critical Rate",))
        self.assertEqual(
            result.filter_phrase.item_types,
            ("Weapon Crysta", "Enhancer Crysta (Red)"),
        )

    def test_order_independent_known_tokens(self):
        self.assertEqual(
            self.reconstruct("weapon xtall cr").canonical_query,
            "cr wp xtal",
        )

    def test_conservative_filler_words_are_allowed(self):
        self.assertEqual(
            self.reconstruct("find xtall cr weapon").canonical_query,
            "cr wp xtal",
        )

    def test_crit_remains_ambiguous(self):
        result = self.reconstruct("crit xtal weapon")
        self.assertEqual(result.kind, "ambiguous")
        self.assertEqual(result.canonical_query, "crit wp xtal")
        self.assertEqual(
            result.stat_resolution.candidates,
            ("Critical Rate", "Critical Damage"),
        )

    def test_unknown_meaningful_token_fails_closed(self):
        result = self.reconstruct("xtall blah weapon")
        self.assertEqual(result.kind, "unsafe")
        self.assertIsNone(result.canonical_query)

    def test_multiple_stats_are_not_auto_reconstructed(self):
        result = self.reconstruct("cr cd weapon xtal")
        self.assertEqual(result.kind, "unsafe")
        self.assertIsNone(result.canonical_query)

    def test_comparison_and_boolean_are_not_auto_reconstructed(self):
        self.assertNotEqual(self.reconstruct("cr > 10 weapon xtal").kind, "success")
        self.assertNotEqual(self.reconstruct("cr and cd weapon xtal").kind, "success")

    def test_highest_is_suggestion_only_in_v1(self):
        self.assertNotEqual(
            self.reconstruct("highest xtall cr weapon").kind,
            "success",
        )
        self.assertEqual(
            try_suggest_query(
                "highest xtall cr weapon",
                available_stats=AVAILABLE_STATS,
                available_item_types=AVAILABLE_TYPES,
            ),
            "cr wp xtal",
        )

    def test_ambiguous_stat_never_generates_single_suggestion(self):
        self.assertIsNone(
            try_suggest_query(
                "highest crit xtal weapon",
                available_stats=AVAILABLE_STATS,
                available_item_types=AVAILABLE_TYPES,
            )
        )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```bash
python -m unittest tests.test_query_reconstruction -v
```

Expected: FAIL because `toram_search.reconstruction` does not exist.

- [ ] **Step 3: Expose the parser-owned filter phrase catalog**

In `toram_data/stat_query.py`, add this public dataclass near `ItemTypeFilter`:

```python
@dataclass(frozen=True)
class ItemFilterPhrase:
    phrase: str
    label: str
    item_types: tuple[str, ...]
```

Add this function after `_filter_candidates()` so it reuses the existing source of truth:

```python
def list_item_filter_phrases(
    available_item_types: set[str],
) -> tuple[ItemFilterPhrase, ...]:
    output: list[ItemFilterPhrase] = []
    seen: set[ItemFilterPhrase] = set()
    for phrase, label, configured_types in _filter_candidates():
        item_types = _existing_types(configured_types, available_item_types)
        if not item_types:
            continue
        row = ItemFilterPhrase(phrase, label, item_types)
        if row in seen:
            continue
        seen.add(row)
        output.append(row)
    return tuple(output)
```

Do not duplicate the current `combinations` dictionary. Future filter aliases must continue to have one source of truth.

- [ ] **Step 4: Create the reconstruction result types and constants**

Create `toram_search/reconstruction.py`:

```python
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Iterable, Literal

from toram_data.aliases import (
    ITEM_WORD_ALIASES,
    StatTermResolution,
    normalize_stat_text,
    preferred_stat_alias,
    resolve_stat_term,
)
from toram_data.stat_query import ItemFilterPhrase, list_item_filter_phrases


ReconstructionKind = Literal["success", "ambiguous", "no_match", "unsafe"]

_FILLER_WORDS = frozenset({
    "a", "an", "can", "find", "give", "gives", "has", "have", "having",
    "i", "me", "show", "some", "that", "the", "want", "which", "with", "you",
})
_HIGH_RANK_WORDS = frozenset({"highest", "best", "most"})
_COMPLEX_RE = re.compile(r"(>=|<=|==|>|<|=)|\b(?:and|or)\b", re.IGNORECASE)


@dataclass(frozen=True)
class ReconstructionResult:
    kind: ReconstructionKind
    canonical_query: str | None = None
    stat_resolution: StatTermResolution | None = None
    filter_phrase: ItemFilterPhrase | None = None
```

- [ ] **Step 5: Implement exact token normalization and filter matching**

Use explicit aliases only:

```python
def _normalized_tokens(raw_query: str) -> list[str]:
    return [
        ITEM_WORD_ALIASES.get(token, token)
        for token in normalize_stat_text(raw_query).split()
    ]


def _semantic_filter_key(row: ItemFilterPhrase) -> tuple[str, tuple[str, ...]]:
    return row.label, row.item_types


def _preferred_filter_phrase(
    matched: ItemFilterPhrase,
    catalog: tuple[ItemFilterPhrase, ...],
) -> str:
    same_filter = [
        row.phrase
        for row in catalog
        if _semantic_filter_key(row) == _semantic_filter_key(matched)
    ]
    return min(same_filter, key=lambda value: (len(value.split()), len(value), value))
```

For filter recognition:

1. Build `Counter(input_tokens)`.
2. A filter phrase is eligible only when `Counter(phrase.split())` is a subset of the input counter.
3. Keep only eligible phrases with the maximum number of phrase tokens.
4. Group those maximum-length matches by `(label, item_types)`.
5. If more than one semantic group remains, return `unsafe`.
6. Remove only the selected phrase's token counts from the input sequence, preserving the order of all remaining tokens.

This makes `xtal weapon` match the existing `weapon xtal` filter while preventing a shorter `weapon` filter from winning when `weapon xtal` is present.

- [ ] **Step 6: Implement simple reconstruction**

`try_reconstruct_simple_search` must:

1. Return `unsafe` immediately when `_COMPLEX_RE` matches the raw query.
2. Return `unsafe` when any `_HIGH_RANK_WORDS` appears; ranking is not automatic v1 reconstruction.
3. Resolve exactly one semantic item filter using Step 5.
4. Remove `_FILLER_WORDS` only after filter-token removal.
5. Join all remaining tokens into one stat phrase.
6. Call:

```python
resolution = resolve_stat_term(
    stat_text,
    available_stats,
    allow_fuzzy=False,
)
```

7. Render the stat token with:

```python
normalized_stat = normalize_stat_text(stat_text)
if resolution.status in {"alias", "ambiguous"}:
    rendered_stat = normalized_stat
else:
    rendered_stat = preferred_stat_alias(resolution.candidates[0]) or normalized_stat
```

8. Render the preferred filter phrase using `_preferred_filter_phrase`.
9. Return `success` only for `exact`/`alias` resolution.
10. Return `ambiguous` only for `ambiguous` resolution.
11. Return `unsafe` for `unknown`, `fuzzy`, empty stat text, or leftover text that does not resolve as one stat.
12. Return `no_match` when no item filter is recognized.

The canonical query is:

```python
canonical_query = f"{rendered_stat} {preferred_filter}"
```

- [ ] **Step 7: Implement safe suggestion generation**

`try_suggest_query` uses the same recognition primitives but may consume exactly one word from `_HIGH_RANK_WORDS`. It still rejects comparisons, Boolean expressions, ambiguous stats, unknown tokens, and multi-stat input.

For `highest xtall cr weapon`, return `cr wp xtal`. This is semantically safe because ordinary stat-result ordering is already highest-first.

- [ ] **Step 8: Run reconstruction and parser regressions**

Run:

```bash
python -m unittest \
  tests.test_query_reconstruction \
  tests.test_direct_structured_intent \
  tests.test_natural_give_ranking -v
```

Expected: PASS.

- [ ] **Step 9: Commit Task 1**

```bash
git add toram_data/stat_query.py toram_search/reconstruction.py tests/test_query_reconstruction.py
git commit -m "feat: add deterministic query reconstruction"
```

---

### Task 2: Route High-Confidence Reconstruction Before Qwen

**Files:**
- Modify: `toram_search/service.py`
- Modify: `tests/test_search_service.py`

**Interfaces:**
- Consumes: `try_reconstruct_simple_search(...)` from Task 1.
- Produces: reconstructed queries re-enter `core.route_deterministically` and the existing `_materialize` path.
- Invariant: successful or ambiguous reconstruction does not call Qwen and does not create failed-query history.

- [ ] **Step 1: Expand the service fake repository for weapon crysta filters**

In `FakeRepository.__init__`:

```python
self.item_types = {
    "Bow",
    "Armor",
    "Weapon Crysta",
    "Enhancer Crysta (Red)",
}
```

- [ ] **Step 2: Add failing pre-Qwen routing tests**

Add to `SearchServiceTests`:

```python
def test_reconstructed_weapon_xtal_query_never_calls_qwen_or_records_failure(self):
    repository = FakeRepository()
    service = SearchService(repository, llm_client=MustNotCallLLM())
    context = FailedQueryContext(max_entries=3)

    outcome = service.handle_query("xtall cr weapon", context)

    self.assertEqual(outcome.kind, "search")
    self.assertIsInstance(outcome.payload, ExpressionResultsPayload)
    self.assertEqual(context.snapshot(), ())
    expression, item_types, ascending = repository.expression_calls[-1]
    self.assertEqual(item_types, ("Weapon Crysta", "Enhancer Crysta (Red)"))
    self.assertFalse(ascending)
    self.assertEqual(expression.groups[0].clauses[0].stat_name, "Critical Rate")


def test_reconstructed_crit_returns_existing_clarification_without_qwen(self):
    repository = FakeRepository()
    service = SearchService(repository, llm_client=MustNotCallLLM())
    context = FailedQueryContext(max_entries=3)

    outcome = service.handle_query("crit xtal weapon", context)

    self.assertEqual(outcome.kind, "search")
    self.assertIsInstance(outcome.payload, StatClarificationPayload)
    self.assertEqual(
        outcome.payload.clarification.candidates,
        ("Critical Rate", "Critical Damage"),
    )
    self.assertEqual(context.snapshot(), ())
    self.assertEqual(repository.expression_calls, [])
```

- [ ] **Step 3: Run both tests and verify RED**

Run:

```bash
python -m unittest \
  tests.test_search_service.SearchServiceTests.test_reconstructed_weapon_xtal_query_never_calls_qwen_or_records_failure \
  tests.test_search_service.SearchServiceTests.test_reconstructed_crit_returns_existing_clarification_without_qwen -v
```

Expected: FAIL because `handle_query` currently reaches fallback after the first deterministic route misses.

- [ ] **Step 4: Insert reconstruction before failed-query recording**

In `toram_search/service.py`, import:

```python
from toram_search.reconstruction import try_reconstruct_simple_search
```

Immediately before the current `if route.record_failure:` block, add:

```python
reconstruction = try_reconstruct_simple_search(
    query,
    available_stats=self.repository.list_stat_names(),
    available_item_types=self.repository.list_item_types(),
)
if reconstruction.kind in {"success", "ambiguous"} and reconstruction.canonical_query:
    reconstructed_route = core.route_deterministically(
        reconstruction.canonical_query,
        self.repository,
        self.all_items,
        self.help_service,
        self.database_service,
    )
    if reconstructed_route.kind == "search" and reconstructed_route.parsed is not None:
        return ServiceOutcome(
            "search",
            payload=self._materialize(reconstructed_route.parsed, {}),
        )
```

Do not add a separate reconstruction executor. If the canonical query cannot pass the existing router, fall through to the existing failed-history/Qwen path.

- [ ] **Step 5: Run all service tests**

Run:

```bash
python -m unittest tests.test_search_service -v
```

Expected: PASS. Existing natural deterministic tests still bypass Qwen; existing unresolved natural wording still returns `confirm_search` and does not execute before confirmation.

- [ ] **Step 6: Commit Task 2**

```bash
git add toram_search/service.py tests/test_search_service.py
git commit -m "feat: route reconstructed queries before qwen"
```

---

### Task 3: Add One-Call Failed-Query Suggestions

**Files:**
- Modify: `toram_search/service.py`
- Modify: `tests/test_search_service.py`

**Interfaces:**
- Consumes: `try_suggest_query(...)` from Task 1.
- Produces: `ServiceOutcome.suggested_query: str | None`.
- Invariant: suggestions are attempted only after fallback kind `failed`.
- Invariant: `refuse`, `unavailable`, valid Qwen searches, help, and database actions retain current behavior.

- [ ] **Step 1: Make `FakeLLM` count calls**

Replace it with:

```python
class FakeLLM:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def complete(self, *args, **kwargs):
        self.calls += 1
        return self.payload
```

- [ ] **Step 2: Add failing suggestion and one-call tests**

Add:

```python
def test_failed_qwen_attaches_safe_specific_suggestion_once(self):
    repository = FakeRepository()
    llm = FakeLLM({"intent": "search", "candidates": []})
    service = SearchService(repository, llm_client=llm)
    context = FailedQueryContext(max_entries=3)

    outcome = service.handle_query("highest xtall cr weapon", context)

    self.assertEqual(outcome.kind, "failed")
    self.assertEqual(outcome.suggested_query, "cr wp xtal")
    self.assertEqual(llm.calls, 1)
    self.assertEqual(context.snapshot()[-1].suggested_query, "cr wp xtal")
    self.assertEqual(repository.expression_calls, [])


def test_failed_qwen_does_not_guess_ambiguous_crit(self):
    repository = FakeRepository()
    llm = FakeLLM({"intent": "search", "candidates": []})
    service = SearchService(repository, llm_client=llm)

    outcome = service.handle_query(
        "highest crit xtal weapon",
        FailedQueryContext(max_entries=3),
    )

    self.assertEqual(outcome.kind, "failed")
    self.assertIsNone(outcome.suggested_query)
    self.assertEqual(llm.calls, 1)


def test_successful_qwen_interpretation_is_still_confirmation_gated(self):
    repository = FakeRepository()
    llm = FakeLLM({
        "intent": "search",
        "candidates": [
            {"item_filter": "armor", "stats": [{"name": "MaxHP"}]}
        ],
    })
    service = SearchService(repository, llm_client=llm)

    outcome = service.handle_query(
        "could you locate protective equipment that increases health",
        FailedQueryContext(max_entries=3),
    )

    self.assertEqual(outcome.kind, "confirm_search")
    self.assertIsNone(outcome.suggested_query)
    self.assertEqual(llm.calls, 1)
    self.assertEqual(repository.expression_calls, [])
```

- [ ] **Step 3: Run service tests and verify RED**

Run:

```bash
python -m unittest tests.test_search_service -v
```

Expected: FAIL because `ServiceOutcome` has no suggestion field and failed fallback does not run the deterministic suggestion helper.

- [ ] **Step 4: Extend `ServiceOutcome`**

Append one optional field:

```python
@dataclass(frozen=True)
class ServiceOutcome:
    kind: ServiceKind
    payload: SearchPayload | None = None
    text: str | None = None
    search_requests: tuple[SearchIntentRequest, ...] = ()
    suggested_query: str | None = None
```

Appending the field preserves all existing positional construction.

- [ ] **Step 5: Add parser-validated suggestion handling only to the final failed branch**

Import:

```python
from toram_search.reconstruction import (
    try_reconstruct_simple_search,
    try_suggest_query,
)
```

After all existing successful/refuse/unavailable fallback branches and immediately before the final `ServiceOutcome("failed")`, add:

```python
suggestion = try_suggest_query(
    query,
    available_stats=self.repository.list_stat_names(),
    available_item_types=self.repository.list_item_types(),
)
if suggestion is not None:
    suggestion_route = core.route_deterministically(
        suggestion,
        self.repository,
        self.all_items,
        self.help_service,
        self.database_service,
    )
    if suggestion_route.kind == "search" and suggestion_route.parsed is not None:
        context.set_latest_suggestion(suggestion)
        return ServiceOutcome("failed", suggested_query=suggestion)
return ServiceOutcome("failed")
```

Do not call `_materialize` in this path. The suggestion is guidance, not an automatic database search.

- [ ] **Step 6: Add `refuse` and `unavailable` preservation assertions using existing fallback test fakes**

Where `tests/test_search_service.py` already constructs or can cheaply construct these outcomes, assert:

```python
self.assertEqual(outcome.kind, "refuse")
self.assertIsNone(outcome.suggested_query)
```

and:

```python
self.assertEqual(outcome.kind, "unavailable")
self.assertIsNone(outcome.suggested_query)
```

Reuse the repository and LLM exception/payload patterns already used by `tests/test_structured_fallback.py`; do not create a second fallback implementation for testing.

- [ ] **Step 7: Run service plus fallback-contract tests**

Run:

```bash
python -m unittest \
  tests.test_search_service \
  tests.test_structured_fallback -v
```

Expected: no new failures relative to clean `main`. Qwen is called once on unresolved success and unresolved failure paths; valid search proposals remain confirmation-gated.

- [ ] **Step 8: Commit Task 3**

```bash
git add toram_search/service.py tests/test_search_service.py
git commit -m "feat: add safe failed query suggestions"
```

---

### Task 4: Render Specific Suggestions in Discord

**Files:**
- Modify: `discord_bot.py`
- Modify: `tests/test_discord_bot.py`

**Interfaces:**
- Consumes: `ServiceOutcome.suggested_query` from Task 3.
- Produces: `Did you mean: @Bot <query>` on failed outcomes with a suggestion.
- Preserves: current three generic examples when no suggestion exists.

- [ ] **Step 1: Add failing Discord rendering tests**

Update the service import in `tests/test_discord_bot.py`:

```python
from toram_search.service import (
    ItemResultsPayload,
    ServiceOutcome,
    UpgradeResultsPayload,
)
```

Add:

```python
def test_failed_outcome_with_specific_suggestion_replaces_generic_examples(self):
    sessions = DiscordSessionManager()
    key = (10, 30, 20)
    session = sessions.start_query(key, "highest xtall cr weapon")

    embed, view, file = discord_bot.build_service_outcome_message(
        ServiceOutcome("failed", suggested_query="cr wp xtal"),
        bot_example_prefix="@Toram Search",
        sessions=sessions,
        key=key,
        generation=session.generation,
        database_path=Path("coryn_data/database/items.sqlite"),
    )

    self.assertEqual(embed.title, "I couldn't interpret that search")
    self.assertIn("Did you mean", embed.description)
    self.assertIn("@Toram Search cr wp xtal", embed.description)
    self.assertNotIn("hp > 5000 and cr bow", embed.description)
    self.assertIsNone(view)
    self.assertIsNone(file)


def test_failed_outcome_without_suggestion_keeps_generic_examples(self):
    sessions = DiscordSessionManager()
    key = (10, 30, 20)
    session = sessions.start_query(key, "unknown query")

    embed, view, file = discord_bot.build_service_outcome_message(
        ServiceOutcome("failed"),
        bot_example_prefix="@Toram Search",
        sessions=sessions,
        key=key,
        generation=session.generation,
        database_path=Path("coryn_data/database/items.sqlite"),
    )

    self.assertIn("@Toram Search hp armor", embed.description)
    self.assertIn("@Toram Search cr bow", embed.description)
    self.assertIn("@Toram Search hp > 5000 and cr bow", embed.description)
    self.assertIsNone(view)
    self.assertIsNone(file)
```

- [ ] **Step 2: Run the two new tests and verify RED**

Run:

```bash
python -m unittest \
  tests.test_discord_bot.DiscordFormattingTests.test_failed_outcome_with_specific_suggestion_replaces_generic_examples \
  tests.test_discord_bot.DiscordFormattingTests.test_failed_outcome_without_suggestion_keeps_generic_examples -v
```

Expected: the specific-suggestion test FAILS because the current failed branch always renders generic examples.

- [ ] **Step 3: Update only the `failed` rendering branch**

In `build_service_outcome_message`:

```python
if outcome.kind == "failed":
    if outcome.suggested_query:
        description = (
            "Did you mean: "
            f"`{bot_example_prefix} {outcome.suggested_query}`"
        )
    else:
        description = (
            "Try an explicit item/stat query, for example:\n"
            f"• `{bot_example_prefix} hp armor`\n"
            f"• `{bot_example_prefix} cr bow`\n"
            f"• `{bot_example_prefix} hp > 5000 and cr bow`"
        )
    return (
        _build_text_embed("I couldn't interpret that search", description),
        None,
        None,
    )
```

Do not add a Search button and do not auto-run the suggestion.

- [ ] **Step 4: Run all Discord tests**

Run:

```bash
python -m unittest tests.test_discord_bot -v
```

Expected: PASS.

- [ ] **Step 5: Commit Task 4**

```bash
git add discord_bot.py tests/test_discord_bot.py
git commit -m "feat: show specific failed query suggestions"
```

---

### Task 5: Real-Database and Full Regression Verification

**Files:**
- No production edits expected.
- Verify: `toram_data/stat_query.py`, `toram_search/reconstruction.py`, `toram_search/service.py`, `discord_bot.py`, and related tests.

**Interfaces:**
- Proves the original problematic query succeeds through the real checked-in database without Qwen.
- Proves `crit` ambiguity remains deterministic.
- Proves unresolved natural language still calls Qwen once and remains confirmation-gated.
- Proves the branch is no worse than clean `main` on the full suite.

- [ ] **Step 1: Refresh the clean-main baseline before judging the feature branch**

In a clean worktree or temporary branch at current `main`, run:

```bash
python -m unittest discover -s tests -v
```

Record the exact failing test names, if any. Do not modify `main` to make the feature branch look green.

- [ ] **Step 2: Compile every modified Python file**

On the feature branch, run:

```bash
python -m py_compile \
  toram_data/stat_query.py \
  toram_search/reconstruction.py \
  toram_search/service.py \
  discord_bot.py \
  tests/test_query_reconstruction.py \
  tests/test_search_service.py \
  tests/test_discord_bot.py
```

Expected: exit code 0.

- [ ] **Step 3: Verify `xtall cr weapon` with the real service and a forbidden LLM**

Run:

```bash
python - <<'PY'
import search_items as core
from toram_search.service import ExpressionResultsPayload, SearchService
from toram_search.session import FailedQueryContext


class MustNotCallLLM:
    def complete(self, *args, **kwargs):
        raise AssertionError("reconstructed query must not call Qwen")


repository = core.ItemRepository(core.DEFAULT_DATABASE)
service = SearchService(repository, llm_client=MustNotCallLLM())
context = FailedQueryContext(max_entries=3)
outcome = service.handle_query("xtall cr weapon", context)

assert outcome.kind == "search", outcome
assert isinstance(outcome.payload, ExpressionResultsPayload), outcome.payload
assert outcome.payload.parsed.filter is not None
assert outcome.payload.parsed.filter.label == "Weapon Crysta + Red Enhancer"
assert outcome.payload.parsed.resolved_expression is not None
clause = outcome.payload.parsed.resolved_expression.groups[0].clauses[0]
assert clause.stat_name == "Critical Rate", clause
assert context.snapshot() == (), context.snapshot()
print("real reconstruction OK")
PY
```

Expected: prints `real reconstruction OK`.

- [ ] **Step 4: Verify real `crit` ambiguity without Qwen**

Run:

```bash
python - <<'PY'
import search_items as core
from toram_search.service import SearchService, StatClarificationPayload
from toram_search.session import FailedQueryContext


class MustNotCallLLM:
    def complete(self, *args, **kwargs):
        raise AssertionError("known ambiguity must not call Qwen")


service = SearchService(
    core.ItemRepository(core.DEFAULT_DATABASE),
    llm_client=MustNotCallLLM(),
)
outcome = service.handle_query("crit xtal weapon", FailedQueryContext(max_entries=3))
assert outcome.kind == "search", outcome
assert isinstance(outcome.payload, StatClarificationPayload), outcome.payload
assert outcome.payload.clarification.candidates == (
    "Critical Rate",
    "Critical Damage",
)
print("real ambiguity guard OK")
PY
```

Expected: prints `real ambiguity guard OK`.

- [ ] **Step 5: Run the focused hybrid gate**

Run:

```bash
python -m unittest \
  tests.test_query_reconstruction \
  tests.test_direct_structured_intent \
  tests.test_natural_give_ranking \
  tests.test_search_service \
  tests.test_discord_bot \
  tests.test_structured_fallback -v
```

Expected: zero new failures relative to the clean-main baseline. The `SearchService` tests must explicitly show one Qwen call for unresolved natural success and unresolved Qwen failure, and zero Qwen calls for `xtall cr weapon` and `crit xtal weapon`.

- [ ] **Step 6: Run the full feature-branch suite**

Run:

```bash
python -m unittest discover -s tests -v
```

Expected: the failing-test set must be identical to or smaller than the clean-main baseline from Step 1. Any additional failure blocks completion.

- [ ] **Step 7: Review final diff scope**

Run:

```bash
git diff --stat main...HEAD
git diff main...HEAD -- \
  toram_data/stat_query.py \
  toram_search/reconstruction.py \
  toram_search/service.py \
  discord_bot.py \
  tests/test_query_reconstruction.py \
  tests/test_search_service.py \
  tests/test_discord_bot.py
```

Expected: only reconstruction, service orchestration, Discord failed-query rendering, and their direct tests are changed. No database files, unrelated UI, or build-role logic should change.

- [ ] **Step 8: Request code review before integration**

Invoke `superpowers:requesting-code-review` against the final implementation branch. Do not merge to `main` without explicit user approval.
