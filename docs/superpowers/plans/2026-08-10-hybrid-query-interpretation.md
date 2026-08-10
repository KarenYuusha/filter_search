# Hybrid Query Interpretation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users search with flexible natural wording without memorizing compact syntax, while keeping simple known queries off the LLM path and validating every LLM proposal before database execution.

**Architecture:** Preserve `route_deterministically` as the first fast path. Add a small deterministic reconstruction module that recognizes one known stat plus one known item filter in flexible order and compiles that intent back into a canonical query that must pass the existing router. Only unresolved cases call Qwen once; valid Qwen search output remains confirmation-gated, and failed Qwen output may attach a parser-validated suggestion for Discord to display.

**Tech Stack:** Python 3, `unittest`, existing `toram_data` parser/alias modules, `toram_search.SearchService`, constrained Ollama/Qwen fallback, Discord.py.

## Global Constraints

- Users must not be required to memorize canonical query syntax.
- Keep the existing deterministic parser as the fastest path.
- High-confidence deterministic reconstruction runs before Qwen.
- Do not route every query through Qwen.
- Make at most one Qwen interpretation call per user query.
- Qwen interprets meaning; Python validates; the database answers.
- Qwen must never write arbitrary SQL, directly choose database rows, or answer item facts from memory.
- Automatic deterministic reconstruction may use only exact canonical stats, exact unambiguous aliases, intentional item-word aliases, and exact known item-filter phrases; no fuzzy semantic execution.
- Explicit parser ambiguity such as `crit` must remain a clarification, not a guessed stat.
- Unknown meaningful tokens must never be silently discarded to force a match.
- The first reconstruction version supports one stat + one item filter + conservative filler words only.
- Comparison, Boolean, multi-stat, and ranking reconstruction are out of the automatic v1 path unless the existing deterministic parser already handled the original query.
- Post-Qwen suggestions are guidance only and must never auto-execute.
- Every generated canonical query or suggestion must pass the existing deterministic router before use.
- `refuse` and `unavailable` behavior remains unchanged.
- Keep build concepts such as tank/DPS/build out of scope.
- Current `main` already contains PR #67 (`ecf04bbd235a6706fd75cbd9f93ed490693b54fa`); create the implementation branch from the latest `main`, not from the documentation branch.
- The suite has historically contained one unrelated structured-fallback logging assertion failure; refresh the baseline from `main` at execution time and require this feature branch to be no worse than that baseline.

## File Structure

- Modify `toram_data/stat_query.py` — expose the parser-owned item-filter phrase catalog without duplicating filter definitions.
- Create `toram_search/reconstruction.py` — normalize tokens, perform strict one-stat/one-filter reconstruction, and generate safe high-confidence suggestions.
- Modify `toram_search/service.py` — insert reconstruction before failed-query recording/Qwen and attach safe suggestions only after Qwen failure.
- Modify `discord_bot.py` — render a query-specific `Did you mean` message when `ServiceOutcome.suggested_query` exists.
- Create `tests/test_query_reconstruction.py` — focused unit tests for reconstruction/suggestion semantics.
- Modify `tests/test_search_service.py` — orchestration, call-count, history, ambiguity, and fallback tests.
- Modify `tests/test_discord_bot.py` — rendering tests for specific suggestions vs existing generic examples.

---

### Task 1: Expose Parser-Owned Filter Phrases and Build Strict Reconstruction

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
- Depends on: `ITEM_WORD_ALIASES`, `STAT_ALIASES`, `resolve_stat_term(..., allow_fuzzy=False)`, `preferred_stat_alias`, and the existing `_filter_candidates()` definitions.

- [ ] **Step 1: Write failing reconstruction tests**

Create `tests/test_query_reconstruction.py` with the real alias/filter vocabulary but no database dependency:

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

    def test_reconstruction_is_order_independent_for_known_tokens(self):
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

    def test_multi_stat_reconstruction_is_not_automatic(self):
        result = self.reconstruct("cr cd weapon xtal")
        self.assertEqual(result.kind, "unsafe")
        self.assertIsNone(result.canonical_query)

    def test_comparison_and_boolean_syntax_is_not_reconstructed(self):
        self.assertNotEqual(self.reconstruct("cr > 10 weapon xtal").kind, "success")
        self.assertNotEqual(self.reconstruct("cr and cd weapon xtal").kind, "success")

    def test_highest_word_is_guidance_only_in_v1(self):
        result = self.reconstruct("highest xtall cr weapon")
        self.assertNotEqual(result.kind, "success")
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

- [ ] **Step 2: Run the tests and observe RED**

Run:

```bash
python -m unittest tests.test_query_reconstruction -v
```

Expected: FAIL because `toram_search.reconstruction` and the public filter-phrase interface do not exist yet.

- [ ] **Step 3: Expose the existing filter catalog without copying it**

In `toram_data/stat_query.py`, add a public representation directly above `_filter_candidates()` and build it from the existing private source of truth:

```python
@dataclass(frozen=True)
class ItemFilterPhrase:
    phrase: str
    label: str
    item_types: tuple[str, ...]


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

Do not move or duplicate the existing `combinations` dictionary. The new function must call `_filter_candidates()` so future alias/filter changes automatically affect reconstruction.

- [ ] **Step 4: Implement strict reconstruction in a new module**

Create `toram_search/reconstruction.py`. Keep it independent of `search_items.py` to avoid circular imports:

```python
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Iterable, Literal

from toram_data.aliases import (
    ITEM_WORD_ALIASES,
    STAT_ALIASES,
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

Implement private helpers with these rules:

```python
def _normalized_tokens(raw_query: str) -> list[str]:
    tokens = normalize_stat_text(raw_query).split()
    return [ITEM_WORD_ALIASES.get(token, token) for token in tokens]


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

When matching filters, compare token **multisets** (`Counter`) so `xtal weapon` can match the existing `weapon xtal` phrase. Keep only candidates with the largest number of matched filter tokens. If those largest candidates map to more than one semantic filter key, return `unsafe` instead of guessing. Remove only the matched filter-token counts from the original token sequence, preserving the remaining token order.

After filter removal, remove only `_FILLER_WORDS`. The remaining tokens must form exactly one stat phrase. Resolve that phrase using:

```python
resolution = resolve_stat_term(
    stat_text,
    available_stats,
    allow_fuzzy=False,
)
```

Render the stat portion as follows:

```python
normalized_stat = normalize_stat_text(stat_text)
if resolution.status in {"alias", "ambiguous"}:
    rendered_stat = normalized_stat
else:
    rendered_stat = preferred_stat_alias(resolution.candidates[0]) or normalized_stat
```

Return `success` only for `exact` or `alias`; return `ambiguous` only for `ambiguous`; return `unsafe` for unknown/fuzzy/multiple-stat leftovers. Return `no_match` when no supported item filter can be identified.

`try_reconstruct_simple_search` must reject `_COMPLEX_RE` and any `_HIGH_RANK_WORDS` before automatic reconstruction.

`try_suggest_query` may additionally consume exactly one high-rank word (`highest`, `best`, or `most`) because ordinary stat results are already highest-first. It must still reject comparison/Boolean syntax, ambiguous stats, unknown tokens, and multiple stats.

- [ ] **Step 5: Run reconstruction tests and the existing parser tests**

Run:

```bash
python -m unittest \
  tests.test_query_reconstruction \
  tests.test_direct_structured_intent \
  tests.test_natural_give_ranking -v
```

Expected: PASS. Existing deterministic grammar behavior must be unchanged.

- [ ] **Step 6: Commit Task 1**

```bash
git add toram_data/stat_query.py toram_search/reconstruction.py tests/test_query_reconstruction.py
git commit -m "feat: add deterministic query reconstruction"
```

---

### Task 2: Insert Reconstruction Before Failed History and Qwen

**Files:**
- Modify: `toram_search/service.py`
- Modify: `tests/test_search_service.py`

**Interfaces:**
- Consumes: `try_reconstruct_simple_search(...) -> ReconstructionResult` from Task 1.
- Produces: `SearchService.handle_query` behavior where reconstructed searches are routed through `core.route_deterministically` and materialized through the existing `_materialize` path.
- Invariant: successful/ambiguous reconstruction must not call Qwen and must not add a `FailedQueryContext` entry.

- [ ] **Step 1: Add failing service tests for reordered tokens and ambiguity**

Extend `FakeRepository.item_types` in `tests/test_search_service.py` so it contains the real combined filter types:

```python
self.item_types = {
    "Bow",
    "Armor",
    "Weapon Crysta",
    "Enhancer Crysta (Red)",
}
```

Add these tests:

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

- [ ] **Step 2: Run the two new tests and observe RED**

Run:

```bash
python -m unittest \
  tests.test_search_service.SearchServiceTests.test_reconstructed_weapon_xtal_query_never_calls_qwen_or_records_failure \
  tests.test_search_service.SearchServiceTests.test_reconstructed_crit_returns_existing_clarification_without_qwen -v
```

Expected: FAIL because `SearchService.handle_query` currently records the failed query and invokes fallback immediately after `route_deterministically` misses.

- [ ] **Step 3: Integrate reconstruction in `SearchService.handle_query`**

Import:

```python
from toram_search.reconstruction import try_reconstruct_simple_search
```

Immediately before the existing `if route.record_failure: context.record_failure(query)` block, add a reconstruction attempt:

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

Do **not** call a new executor from reconstruction. If the canonical query cannot re-enter the existing deterministic search path, fall through to the current failure/Qwen flow.

- [ ] **Step 4: Re-run service tests**

Run:

```bash
python -m unittest tests.test_search_service -v
```

Expected: PASS. In particular, existing `test_qwen_search_requires_confirmation_before_execution` must remain green, proving genuinely unresolved language still reaches Qwen and does not execute immediately.

- [ ] **Step 5: Commit Task 2**

```bash
git add toram_search/service.py tests/test_search_service.py
git commit -m "feat: route high confidence reconstruction before qwen"
```

---

### Task 3: Attach Safe Suggestions Only After Qwen Failure

**Files:**
- Modify: `toram_search/service.py`
- Modify: `tests/test_search_service.py`

**Interfaces:**
- Consumes: `try_suggest_query(...) -> str | None` from Task 1.
- Produces: `ServiceOutcome.suggested_query: str | None`.
- Invariant: suggestion generation runs only after fallback returns `failed`; it does not run for `refuse` or `unavailable`.
- Invariant: Qwen is still called at most once.

- [ ] **Step 1: Make the fake LLM observable and write failing tests**

Change `FakeLLM` in `tests/test_search_service.py` to count calls without changing its existing behavior:

```python
class FakeLLM:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def complete(self, *args, **kwargs):
        self.calls += 1
        return self.payload
```

Add:

```python
def test_failed_qwen_can_attach_parser_validated_specific_suggestion(self):
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


def test_failed_qwen_does_not_guess_ambiguous_crit_suggestion(self):
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


def test_qwen_success_still_uses_confirmation_and_no_post_failure_suggestion(self):
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

- [ ] **Step 2: Run the new tests and observe RED**

Run:

```bash
python -m unittest tests.test_search_service -v
```

Expected: FAIL because `ServiceOutcome` has no `suggested_query` and the final failed branch does not call deterministic guidance.

- [ ] **Step 3: Add `suggested_query` to the frontend-neutral service outcome**

Update the dataclass in `toram_search/service.py`:

```python
@dataclass(frozen=True)
class ServiceOutcome:
    kind: ServiceKind
    payload: SearchPayload | None = None
    text: str | None = None
    search_requests: tuple[SearchIntentRequest, ...] = ()
    suggested_query: str | None = None
```

This field is intentionally separate from `text`; other frontends may want to render it differently.

- [ ] **Step 4: Add post-Qwen guidance with deterministic re-validation**

Import `try_suggest_query` beside the reconstruction import. Preserve all existing `search_requests`, `database_action`, `help`, `refuse`, and `unavailable` branches.

Only in the final fallback-failed path:

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

Do not call `_materialize` here; a suggestion is validation/guidance, not a search execution. `route_deterministically` must prove the suggested string is accepted by the existing parser before it is exposed.

- [ ] **Step 5: Add explicit regressions for unavailable/refuse**

Use fake clients/payloads already supported by the fallback tests and assert `suggested_query is None` for `unavailable` and `refuse`. If `LLMUnavailableError` is easier to trigger through the existing fake patterns in `tests/test_structured_fallback.py`, add the service assertion there rather than inventing a new exception fake.

- [ ] **Step 6: Run service and fallback contract tests**

Run:

```bash
python -m unittest \
  tests.test_search_service \
  tests.test_structured_fallback -v
```

Expected: all feature-related tests PASS. If the historical logging-message assertion still fails on current `main`, confirm the exact same single failure occurs on a clean `main` baseline; do not change unrelated logging in this task.

- [ ] **Step 7: Commit Task 3**

```bash
git add toram_search/service.py tests/test_search_service.py tests/test_structured_fallback.py
git commit -m "feat: add safe failed query suggestions"
```

If `tests/test_structured_fallback.py` required no edit, omit it from `git add`.

---

### Task 4: Render Query-Specific Suggestions in Discord

**Files:**
- Modify: `discord_bot.py`
- Modify: `tests/test_discord_bot.py`

**Interfaces:**
- Consumes: `ServiceOutcome.suggested_query` from Task 3.
- Produces: failed Discord embed with `Did you mean: @Bot <query>` when present.
- Preserves: existing three generic examples when no suggestion exists.

- [ ] **Step 1: Write failing Discord rendering tests**

Update imports in `tests/test_discord_bot.py`:

```python
from toram_search.service import (
    ItemResultsPayload,
    ServiceOutcome,
    UpgradeResultsPayload,
)
```

Add to `DiscordFormattingTests`:

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

- [ ] **Step 2: Run the two tests and observe RED**

Run:

```bash
python -m unittest \
  tests.test_discord_bot.DiscordFormattingTests.test_failed_outcome_with_specific_suggestion_replaces_generic_examples \
  tests.test_discord_bot.DiscordFormattingTests.test_failed_outcome_without_suggestion_keeps_generic_examples -v
```

Expected: first test FAIL because the current failed branch always renders generic examples.

- [ ] **Step 3: Update only the `failed` rendering branch**

In `build_service_outcome_message`, replace the current `if outcome.kind == "failed":` body with:

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

Do not add a Search button or automatically call `confirm_search_request`; the user explicitly retries a post-failure suggestion.

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

### Task 5: Verify Qwen Remains a Constrained Interpreter, Not an Executor

**Files:**
- Modify only if a missing regression is discovered: `tests/test_search_service.py`
- Modify only if a missing contract regression is discovered: `tests/test_structured_fallback.py`

**Interfaces:**
- Confirms the existing contract rather than adding a new execution path.
- Qwen search proposals must still return `confirm_search` before database execution.
- Confirmed requests must still pass `parse_structured_search_request` and `_materialize`.

- [ ] **Step 1: Run the existing Qwen confirmation/validation regressions**

Run:

```bash
python -m unittest \
  tests.test_search_service.SearchServiceTests.test_qwen_search_requires_confirmation_before_execution \
  tests.test_search_service.SearchServiceTests.test_confirmed_qwen_request_is_validated_then_executed \
  tests.test_structured_fallback -v
```

Expected: the two service tests PASS. Structured fallback should match the refreshed `main` baseline exactly.

- [ ] **Step 2: Add a regression only if current coverage does not prove one-call behavior**

If Task 3's `FakeLLM.calls` assertions already cover both Qwen success and failure, no code change is needed. Otherwise add:

```python
def test_unresolved_natural_query_calls_qwen_once(self):
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
    self.assertEqual(llm.calls, 1)
    self.assertEqual(repository.expression_calls, [])
```

- [ ] **Step 3: Run the focused hybrid interpretation gate**

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

Expected: no new failures relative to current `main`.

- [ ] **Step 4: Commit any test-only additions from this task**

If Step 2 added coverage:

```bash
git add tests/test_search_service.py tests/test_structured_fallback.py
git commit -m "test: lock hybrid qwen routing contract"
```

If no file changed, do not create an empty commit.

---

### Task 6: Real-Database Verification and Full Regression Gate

**Files:**
- No production files expected.
- Verify: `toram_data/stat_query.py`, `toram_search/reconstruction.py`, `toram_search/service.py`, `discord_bot.py`, and their tests.

**Interfaces:**
- Proves the target query works with the checked-in Toram database and no Qwen call.
- Proves ambiguous `crit` still returns deterministic clarification.
- Proves the branch does not worsen the full-suite baseline.

- [ ] **Step 1: Compile all modified Python files**

Run:

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

- [ ] **Step 2: Verify `xtall cr weapon` through the real service without Qwen**

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

Expected: prints `real reconstruction OK` and never calls Qwen.

- [ ] **Step 3: Verify real ambiguous reconstruction does not guess**

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

- [ ] **Step 4: Run the full test suite**

Run:

```bash
python -m unittest discover -s tests -v
```

Expected: the feature branch must have no failures beyond the refreshed clean-`main` baseline. If `main` still has only `test_structured_fallback.StructuredFallbackTests.test_rejected_payload_is_logged_with_reason`, the feature branch may have that same one unrelated failure and no others. If clean `main` is fully green by execution time, this branch must also be fully green.

- [ ] **Step 5: Review the final diff for scope**

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
  tests/test_discord_bot.py \
  tests/test_structured_fallback.py
```

Expected: only reconstruction, service orchestration, Discord failure rendering, and directly related tests are changed. No database files, parser data, unrelated UI, or build-role logic should be modified.

- [ ] **Step 6: Final commit only if verification produced legitimate tracked changes**

Do not create a verification-only empty commit. If a small test correction was required, commit only that correction with a specific message such as:

```bash
git add <changed-test-file>
git commit -m "test: cover hybrid interpretation regression"
```

- [ ] **Step 7: Request code review before integration**

Use `superpowers:requesting-code-review` against the final implementation branch. Do not merge to `main` without explicit user approval.
