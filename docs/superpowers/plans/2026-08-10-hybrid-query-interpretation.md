# Hybrid Query Interpretation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Accept flexible user wording without requiring memorized syntax, while keeping simple known searches deterministic and using Qwen only once when semantic interpretation is genuinely needed.

**Architecture:** Keep `route_deterministically` first. Add a strict reconstruction layer that recognizes one known stat plus one known item filter in flexible order, emits a canonical query, and must pass the existing router before execution. Only unresolved input reaches Qwen; Qwen search output stays confirmation-gated, while failed Qwen output may carry one parser-validated suggestion.

**Tech Stack:** Python 3, `unittest`, `toram_data`, `toram_search`, Ollama/Qwen, Discord.py.

## Global Constraints

- Users do not need to learn canonical query syntax.
- Deterministic parser remains the fastest path.
- High-confidence reconstruction runs before Qwen.
- At most one Qwen call per user query.
- Qwen interprets; Python validates; database code answers.
- Qwen never writes arbitrary SQL, picks result rows, or answers item facts from memory.
- Auto-reconstruction uses exact canonical stats, exact unambiguous aliases, intentional word aliases, and existing item-filter phrases only; no fuzzy semantic execution.
- `crit` stays ambiguous between Critical Rate and Critical Damage.
- Unknown meaningful tokens are never silently discarded.
- Auto-reconstruction v1 supports one stat + one item filter + conservative filler words only.
- Comparisons, Boolean expressions, multiple stats, and rankings are not added to automatic v1 reconstruction.
- Post-Qwen suggestions never auto-execute.
- Every reconstructed/suggested query must pass the existing deterministic router.
- `refuse`, `unavailable`, help, and database-action behavior remains constrained as today.
- Tank/DPS/build concepts remain out of scope.
- Create the implementation branch from latest `main`; merged PR #67 is already on `main` at `ecf04bbd235a6706fd75cbd9f93ed490693b54fa`.
- Refresh the clean-`main` full-suite baseline before final verification; the feature branch may introduce zero new failures.

## File Map

- Modify `toram_data/stat_query.py`: expose existing item-filter phrases without copying the catalog.
- Create `toram_search/reconstruction.py`: strict reconstruction + safe suggestion generation.
- Modify `toram_search/service.py`: reconstruction before failed history/Qwen; suggestions after Qwen failure.
- Modify `discord_bot.py`: specific `Did you mean` rendering.
- Create `tests/test_query_reconstruction.py`.
- Modify `tests/test_search_service.py`.
- Modify `tests/test_discord_bot.py`.

---

### Task 1: Strict Deterministic Reconstruction

**Files:**
- Modify: `toram_data/stat_query.py`
- Create: `toram_search/reconstruction.py`
- Create: `tests/test_query_reconstruction.py`

**Interfaces:**
- Produces `ItemFilterPhrase(phrase, label, item_types)`.
- Produces `list_item_filter_phrases(available_item_types) -> tuple[ItemFilterPhrase, ...]`.
- Produces `ReconstructionResult(kind, canonical_query, stat_resolution, filter_phrase)`.
- Produces `try_reconstruct_simple_search(...) -> ReconstructionResult`.
- Produces `try_suggest_query(...) -> str | None`.

- [ ] **Step 1: Write RED tests**

Create `tests/test_query_reconstruction.py`:

```python
import unittest

from toram_search.reconstruction import try_reconstruct_simple_search, try_suggest_query

STATS = ["Critical Rate", "Critical Damage", "MaxHP", "Weapon ATK"]
TYPES = {
    "Weapon Crysta", "Enhancer Crysta (Red)",
    "Armor Crysta", "Enhancer Crysta (Green)",
    "Armor", "Bow",
}


class QueryReconstructionTests(unittest.TestCase):
    def reconstruct(self, query):
        return try_reconstruct_simple_search(
            query,
            available_stats=STATS,
            available_item_types=TYPES,
        )

    def test_xtall_cr_weapon(self):
        result = self.reconstruct("xtall cr weapon")
        self.assertEqual(result.kind, "success")
        self.assertEqual(result.canonical_query, "cr wp xtal")
        self.assertEqual(result.stat_resolution.candidates, ("Critical Rate",))
        self.assertEqual(
            result.filter_phrase.item_types,
            ("Weapon Crysta", "Enhancer Crysta (Red)"),
        )

    def test_order_is_flexible(self):
        self.assertEqual(self.reconstruct("weapon xtall cr").canonical_query, "cr wp xtal")

    def test_known_filler_is_allowed(self):
        self.assertEqual(self.reconstruct("find xtall cr weapon").canonical_query, "cr wp xtal")

    def test_crit_is_ambiguous(self):
        result = self.reconstruct("crit xtal weapon")
        self.assertEqual(result.kind, "ambiguous")
        self.assertEqual(result.canonical_query, "crit wp xtal")
        self.assertEqual(result.stat_resolution.candidates, ("Critical Rate", "Critical Damage"))

    def test_unknown_token_fails_closed(self):
        result = self.reconstruct("xtall blah weapon")
        self.assertEqual(result.kind, "unsafe")
        self.assertIsNone(result.canonical_query)

    def test_multiple_stats_fail_closed(self):
        self.assertEqual(self.reconstruct("cr cd weapon xtal").kind, "unsafe")

    def test_complex_syntax_is_not_auto_reconstructed(self):
        self.assertNotEqual(self.reconstruct("cr > 10 weapon xtal").kind, "success")
        self.assertNotEqual(self.reconstruct("cr and cd weapon xtal").kind, "success")

    def test_highest_is_guidance_only(self):
        self.assertNotEqual(self.reconstruct("highest xtall cr weapon").kind, "success")
        self.assertEqual(
            try_suggest_query(
                "highest xtall cr weapon",
                available_stats=STATS,
                available_item_types=TYPES,
            ),
            "cr wp xtal",
        )

    def test_ambiguous_suggestion_is_rejected(self):
        self.assertIsNone(
            try_suggest_query(
                "highest crit xtal weapon",
                available_stats=STATS,
                available_item_types=TYPES,
            )
        )
```

- [ ] **Step 2: Run RED test**

```bash
python -m unittest tests.test_query_reconstruction -v
```

Expected: FAIL because `toram_search.reconstruction` does not exist.

- [ ] **Step 3: Expose the existing filter catalog**

In `toram_data/stat_query.py`, add:

```python
@dataclass(frozen=True)
class ItemFilterPhrase:
    phrase: str
    label: str
    item_types: tuple[str, ...]
```

After `_filter_candidates()` add:

```python
def list_item_filter_phrases(available_item_types: set[str]) -> tuple[ItemFilterPhrase, ...]:
    output = []
    seen = set()
    for phrase, label, configured_types in _filter_candidates():
        item_types = _existing_types(configured_types, available_item_types)
        if not item_types:
            continue
        row = ItemFilterPhrase(phrase, label, item_types)
        if row not in seen:
            seen.add(row)
            output.append(row)
    return tuple(output)
```

Do not copy the current filter `combinations` mapping; reconstruction must consume this public view of the existing source of truth.

- [ ] **Step 4: Create reconstruction types/constants**

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
_FILLERS = frozenset({
    "a", "an", "can", "find", "give", "gives", "has", "have", "having",
    "i", "me", "show", "some", "that", "the", "want", "which", "with", "you",
})
_RANK_WORDS = frozenset({"highest", "best", "most"})
_COMPLEX_RE = re.compile(r"(>=|<=|==|>|<|=)|\b(?:and|or)\b", re.IGNORECASE)


@dataclass(frozen=True)
class ReconstructionResult:
    kind: ReconstructionKind
    canonical_query: str | None = None
    stat_resolution: StatTermResolution | None = None
    filter_phrase: ItemFilterPhrase | None = None
```

- [ ] **Step 5: Implement exact matching rules**

Normalize tokens with existing intentional item-word aliases only:

```python
def _tokens(raw_query: str) -> list[str]:
    return [
        ITEM_WORD_ALIASES.get(token, token)
        for token in normalize_stat_text(raw_query).split()
    ]
```

Filter matching algorithm:

1. Build `Counter(input_tokens)`.
2. A catalog phrase is eligible only if `Counter(phrase.split())` is a multiset subset.
3. Keep eligible phrases with the largest token count.
4. Group them by `(label, item_types)`; more than one semantic group means `unsafe`.
5. Remove exactly the matched filter-token counts while preserving remaining-token order.
6. Choose the canonical filter alias among phrases with the same `(label, item_types)` using `min(..., key=(token_count, text_length, lexical))`; this makes weapon crysta render as `wp xtal`.

Then remove `_FILLERS` and resolve the remaining text with:

```python
resolution = resolve_stat_term(stat_text, available_stats, allow_fuzzy=False)
```

For exact/alias resolution return `success`. For explicit ambiguity return `ambiguous`. Unknown/fuzzy/empty/multiple-stat leftovers return `unsafe`. No item filter returns `no_match`.

Render the stat portion with:

```python
normalized_stat = normalize_stat_text(stat_text)
rendered_stat = (
    normalized_stat
    if resolution.status in {"alias", "ambiguous"}
    else preferred_stat_alias(resolution.candidates[0]) or normalized_stat
)
canonical_query = f"{rendered_stat} {preferred_filter_phrase}"
```

`try_reconstruct_simple_search` rejects `_COMPLEX_RE` and `_RANK_WORDS` before automatic reconstruction.

`try_suggest_query` reuses the same recognition but may consume exactly one of `highest`, `best`, or `most`; it still rejects ambiguity, unknown tokens, comparisons, Boolean expressions, and multiple stats. Returning `cr wp xtal` for `highest xtall cr weapon` is safe because stat results are already highest-first.

- [ ] **Step 6: Run GREEN tests and parser regressions**

```bash
python -m unittest \
  tests.test_query_reconstruction \
  tests.test_direct_structured_intent \
  tests.test_natural_give_ranking -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add toram_data/stat_query.py toram_search/reconstruction.py tests/test_query_reconstruction.py
git commit -m "feat: add deterministic query reconstruction"
```

---

### Task 2: Put Reconstruction Before Qwen

**Files:**
- Modify: `toram_search/service.py`
- Modify: `tests/test_search_service.py`

**Interfaces:**
- Consumes `try_reconstruct_simple_search`.
- Reconstructed queries must re-enter `core.route_deterministically` and existing `_materialize`.
- Successful/ambiguous reconstruction does not call Qwen and does not enter failed history.

- [ ] **Step 1: Extend `FakeRepository.item_types`**

```python
self.item_types = {
    "Bow", "Armor", "Weapon Crysta", "Enhancer Crysta (Red)"
}
```

- [ ] **Step 2: Write RED service tests**

```python
def test_reconstructed_weapon_xtal_never_calls_qwen_or_records_failure(self):
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


def test_reconstructed_crit_uses_existing_clarification_without_qwen(self):
    repository = FakeRepository()
    service = SearchService(repository, llm_client=MustNotCallLLM())
    context = FailedQueryContext(max_entries=3)

    outcome = service.handle_query("crit xtal weapon", context)

    self.assertEqual(outcome.kind, "search")
    self.assertIsInstance(outcome.payload, StatClarificationPayload)
    self.assertEqual(outcome.payload.clarification.candidates, ("Critical Rate", "Critical Damage"))
    self.assertEqual(context.snapshot(), ())
    self.assertEqual(repository.expression_calls, [])
```

- [ ] **Step 3: Run RED tests**

```bash
python -m unittest \
  tests.test_search_service.SearchServiceTests.test_reconstructed_weapon_xtal_never_calls_qwen_or_records_failure \
  tests.test_search_service.SearchServiceTests.test_reconstructed_crit_uses_existing_clarification_without_qwen -v
```

Expected: FAIL because `handle_query` still falls directly into failed-history/Qwen handling.

- [ ] **Step 4: Integrate reconstruction**

Import `try_reconstruct_simple_search`. Immediately before `if route.record_failure:` add:

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

Do not add a new executor. If re-routing fails, continue into existing Qwen fallback.

- [ ] **Step 5: Run service suite**

```bash
python -m unittest tests.test_search_service -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add toram_search/service.py tests/test_search_service.py
git commit -m "feat: route reconstructed queries before qwen"
```

---

### Task 3: Add Safe Suggestions After Qwen Failure

**Files:**
- Modify: `toram_search/service.py`
- Modify: `tests/test_search_service.py`

**Interfaces:**
- Adds `ServiceOutcome.suggested_query: str | None`.
- Suggestions run only after fallback `failed`.
- Valid Qwen searches remain `confirm_search`; `refuse`/`unavailable` stay unchanged.

- [ ] **Step 1: Make `FakeLLM` observable**

```python
class FakeLLM:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def complete(self, *args, **kwargs):
        self.calls += 1
        return self.payload
```

Add to imports:

```python
from toram_search.llm import LLMUnavailableError
```

Add:

```python
class UnavailableLLM:
    def __init__(self):
        self.calls = 0

    def complete(self, *args, **kwargs):
        self.calls += 1
        raise LLMUnavailableError("offline")
```

- [ ] **Step 2: Write RED tests**

```python
def test_failed_qwen_attaches_safe_suggestion_once(self):
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


def test_failed_qwen_does_not_guess_crit(self):
    llm = FakeLLM({"intent": "search", "candidates": []})
    outcome = SearchService(FakeRepository(), llm_client=llm).handle_query(
        "highest crit xtal weapon", FailedQueryContext(max_entries=3)
    )
    self.assertEqual(outcome.kind, "failed")
    self.assertIsNone(outcome.suggested_query)
    self.assertEqual(llm.calls, 1)


def test_successful_qwen_search_remains_confirmation_gated(self):
    repository = FakeRepository()
    llm = FakeLLM({
        "intent": "search",
        "candidates": [{"item_filter": "armor", "stats": [{"name": "MaxHP"}]}],
    })
    outcome = SearchService(repository, llm_client=llm).handle_query(
        "could you locate protective equipment that increases health",
        FailedQueryContext(max_entries=3),
    )
    self.assertEqual(outcome.kind, "confirm_search")
    self.assertIsNone(outcome.suggested_query)
    self.assertEqual(llm.calls, 1)
    self.assertEqual(repository.expression_calls, [])


def test_qwen_refuse_has_no_suggestion(self):
    llm = FakeLLM({"intent": "refuse"})
    outcome = SearchService(FakeRepository(), llm_client=llm).handle_query(
        "tell me something unrelated", FailedQueryContext(max_entries=3)
    )
    self.assertEqual(outcome.kind, "refuse")
    self.assertIsNone(outcome.suggested_query)
    self.assertEqual(llm.calls, 1)


def test_qwen_unavailable_has_no_suggestion(self):
    llm = UnavailableLLM()
    outcome = SearchService(FakeRepository(), llm_client=llm).handle_query(
        "could you locate protective equipment that increases health",
        FailedQueryContext(max_entries=3),
    )
    self.assertEqual(outcome.kind, "unavailable")
    self.assertIsNone(outcome.suggested_query)
    self.assertEqual(llm.calls, 1)
```

- [ ] **Step 3: Run RED service suite**

```bash
python -m unittest tests.test_search_service -v
```

Expected: FAIL because the service outcome has no suggestion field and no post-failure suggestion path.

- [ ] **Step 4: Extend `ServiceOutcome`**

```python
@dataclass(frozen=True)
class ServiceOutcome:
    kind: ServiceKind
    payload: SearchPayload | None = None
    text: str | None = None
    search_requests: tuple[SearchIntentRequest, ...] = ()
    suggested_query: str | None = None
```

- [ ] **Step 5: Add failed-only suggestion handling**

Import both reconstruction helpers. Preserve all current successful/refuse/unavailable branches. Immediately before the final failed return:

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

Do not `_materialize` the suggestion; validation must not execute it.

- [ ] **Step 6: Run service + fallback contract tests**

```bash
python -m unittest tests.test_search_service tests.test_structured_fallback -v
```

Expected: zero new failures relative to clean `main`; Qwen calls exactly once in each unresolved test.

- [ ] **Step 7: Commit**

```bash
git add toram_search/service.py tests/test_search_service.py
git commit -m "feat: add safe failed query suggestions"
```

---

### Task 4: Discord Suggestion Rendering

**Files:**
- Modify: `discord_bot.py`
- Modify: `tests/test_discord_bot.py`

**Interfaces:**
- Failed + suggestion: show one `Did you mean` query.
- Failed without suggestion: preserve current generic examples.
- Never add an auto-search control for post-failure guidance.

- [ ] **Step 1: Write RED Discord tests**

Import `ServiceOutcome`, then add:

```python
def test_failed_outcome_with_suggestion_replaces_generic_examples(self):
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

- [ ] **Step 2: Run RED tests**

```bash
python -m unittest \
  tests.test_discord_bot.DiscordFormattingTests.test_failed_outcome_with_suggestion_replaces_generic_examples \
  tests.test_discord_bot.DiscordFormattingTests.test_failed_outcome_without_suggestion_keeps_generic_examples -v
```

Expected: first test FAIL.

- [ ] **Step 3: Modify only the `failed` branch**

```python
if outcome.kind == "failed":
    if outcome.suggested_query:
        description = f"Did you mean: `{bot_example_prefix} {outcome.suggested_query}`"
    else:
        description = (
            "Try an explicit item/stat query, for example:\n"
            f"• `{bot_example_prefix} hp armor`\n"
            f"• `{bot_example_prefix} cr bow`\n"
            f"• `{bot_example_prefix} hp > 5000 and cr bow`"
        )
    return _build_text_embed("I couldn't interpret that search", description), None, None
```

- [ ] **Step 4: Run Discord suite**

```bash
python -m unittest tests.test_discord_bot -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add discord_bot.py tests/test_discord_bot.py
git commit -m "feat: show specific failed query suggestions"
```

---

### Task 5: Real Database and Regression Gate

**Files:**
- No production edits expected.

**Interfaces:**
- Confirms `xtall cr weapon` uses no Qwen.
- Confirms `crit xtal weapon` still clarifies.
- Confirms unresolved natural wording uses one Qwen call and confirmation.
- Confirms zero new full-suite failures.

- [ ] **Step 1: Establish clean-main baseline**

In a clean worktree at current `main`:

```bash
python -m unittest discover -s tests -v
```

Record exact failing test names, if any.

- [ ] **Step 2: Compile modified files**

```bash
python -m py_compile \
  toram_data/stat_query.py toram_search/reconstruction.py toram_search/service.py \
  discord_bot.py tests/test_query_reconstruction.py tests/test_search_service.py \
  tests/test_discord_bot.py
```

Expected: exit 0.

- [ ] **Step 3: Verify real reconstruction without Qwen**

```bash
python - <<'PY'
import search_items as core
from toram_search.service import ExpressionResultsPayload, SearchService
from toram_search.session import FailedQueryContext

class MustNotCallLLM:
    def complete(self, *args, **kwargs):
        raise AssertionError("Qwen must not be called")

repo = core.ItemRepository(core.DEFAULT_DATABASE)
context = FailedQueryContext(max_entries=3)
outcome = SearchService(repo, llm_client=MustNotCallLLM()).handle_query("xtall cr weapon", context)
assert outcome.kind == "search"
assert isinstance(outcome.payload, ExpressionResultsPayload)
assert outcome.payload.parsed.filter.label == "Weapon Crysta + Red Enhancer"
assert outcome.payload.parsed.resolved_expression.groups[0].clauses[0].stat_name == "Critical Rate"
assert context.snapshot() == ()
print("real reconstruction OK")
PY
```

Expected: `real reconstruction OK`.

- [ ] **Step 4: Verify real ambiguity without Qwen**

```bash
python - <<'PY'
import search_items as core
from toram_search.service import SearchService, StatClarificationPayload
from toram_search.session import FailedQueryContext

class MustNotCallLLM:
    def complete(self, *args, **kwargs):
        raise AssertionError("Qwen must not be called")

outcome = SearchService(
    core.ItemRepository(core.DEFAULT_DATABASE),
    llm_client=MustNotCallLLM(),
).handle_query("crit xtal weapon", FailedQueryContext(max_entries=3))
assert outcome.kind == "search"
assert isinstance(outcome.payload, StatClarificationPayload)
assert outcome.payload.clarification.candidates == ("Critical Rate", "Critical Damage")
print("real ambiguity OK")
PY
```

Expected: `real ambiguity OK`.

- [ ] **Step 5: Run focused gate**

```bash
python -m unittest \
  tests.test_query_reconstruction \
  tests.test_direct_structured_intent \
  tests.test_natural_give_ranking \
  tests.test_search_service \
  tests.test_discord_bot \
  tests.test_structured_fallback -v
```

Expected: no new failures compared with clean `main`.

- [ ] **Step 6: Run full feature-branch suite**

```bash
python -m unittest discover -s tests -v
```

Expected: failing-test set is identical to or smaller than the Step 1 baseline. Any additional failure blocks completion.

- [ ] **Step 7: Review scope**

```bash
git diff --stat main...HEAD
git diff main...HEAD -- \
  toram_data/stat_query.py toram_search/reconstruction.py toram_search/service.py \
  discord_bot.py tests/test_query_reconstruction.py tests/test_search_service.py \
  tests/test_discord_bot.py
```

Expected: only reconstruction, service orchestration, Discord failure rendering, and direct tests changed.

- [ ] **Step 8: Request code review**

Invoke `superpowers:requesting-code-review`. Do not merge to `main` without explicit user approval.
