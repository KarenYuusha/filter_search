# Deterministic Natural-Language Search Normalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route simple conversational item/stat requests through the existing deterministic parser before invoking Qwen.

**Architecture:** Add a pure normalization helper in `search_items.py` that recognizes a narrow set of conversational patterns, validates the item-filter side with existing resolvers, and returns an existing-grammar query only when safe. `route_deterministically()` attempts that normalized query after the current direct parse/ranking checks and before help/database/Qwen fallback.

**Tech Stack:** Python 3.10+, `unittest`, existing `toram_data.aliases` and stat-query parser.

## Global Constraints

- Do not add semantic build/role interpretation such as `tank xtal` or `dps gear`.
- Do not add SQL generation or LLM factual authority.
- Preserve existing ambiguity behavior and existing deterministic syntax.
- Qwen remains fallback only when deterministic normalization cannot safely produce a supported search.
- Keep PR #7 independent from this change.

---

### Task 1: Add regression tests for conversational normalization

**Files:**
- Modify: `tests/test_direct_structured_intent.py`

**Interfaces:**
- Consumes: `route_deterministically(query, repository, all_items, help_service, database_service)` and the new `_try_natural_search_normalization(query, repository)` helper.
- Produces: regression coverage for supported conversational forms and non-rewrite cases.

- [ ] **Step 1: Write failing tests**

Add tests using the existing `FakeRepository` plus `HelpService` / `DatabaseQuestionService` stubs where needed:

```python
def test_natural_armor_hp_routes_without_qwen(self):
    parsed = _try_natural_search_normalization("can you find armor with hp", self.repository)
    self.assertIsNotNone(parsed)
    self.assertEqual(parsed.intent, "stat_search")
    self.assertEqual(parsed.stat.stat_name, "MaxHP")
    self.assertEqual(parsed.filter.label, "Armor")


def test_natural_bow_cr_routes_without_qwen(self):
    parsed = _try_natural_search_normalization("show me bow with cr", self.repository)
    self.assertIsNotNone(parsed)
    self.assertEqual(parsed.stat.stat_name, "Critical Rate")
    self.assertEqual(parsed.filter.label, "Bow")


def test_unsupported_conversation_is_not_rewritten(self):
    self.assertIsNone(
        _try_natural_search_normalization("tell me something interesting about armor", self.repository)
    )
```

Also cover `which bows have critical rate` and `armor having hp`.

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```bash
uv run python -m unittest tests.test_direct_structured_intent -v
```

Expected: import/test failures because `_try_natural_search_normalization` does not exist yet.

- [ ] **Step 3: Commit RED tests**

```bash
git add tests/test_direct_structured_intent.py
git commit -m "test: cover deterministic natural-language search"
```

---

### Task 2: Implement narrow deterministic conversational normalization

**Files:**
- Modify: `search_items.py`
- Test: `tests/test_direct_structured_intent.py`

**Interfaces:**
- Produces: `_try_natural_search_normalization(raw: str, repository: ItemRepository) -> ParsedSearch | None`

- [ ] **Step 1: Implement pattern extraction**

Recognize only the approved forms. Extract `item_text` and `stat_text`, trim punctuation/whitespace, and reject empty sides.

- [ ] **Step 2: Validate item filter completely**

Use `_resolve_structured_item_filter(item_text, repository)` so partial/unknown item phrases are rejected rather than guessed.

- [ ] **Step 3: Build existing-grammar candidate**

Construct `candidate = f"{stat_text} {item_text}"` and call `parse_search_query(candidate, repository)`.

Accept only parser outcomes already considered supported searches:

```python
{"stat_search", "stat_choices", "stat_expression"}
```

Reject parsed errors and stat expressions containing unknown stats. Return the parsed object with `raw_query` replaced by the original conversational query for presentation/history.

- [ ] **Step 4: Integrate before Qwen fallback**

In `route_deterministically()`, after direct parsing and the existing simple ranking path, call `_try_natural_search_normalization(raw, repository)`. If it succeeds, return `DeterministicRoute("search", parsed=normalized_search)`.

- [ ] **Step 5: Run focused tests and verify GREEN**

```bash
uv run python -m unittest tests.test_direct_structured_intent -v
```

Expected: all tests pass.

- [ ] **Step 6: Run broader search tests**

```bash
uv run python -m unittest discover -s tests -v
```

Expected: full suite passes. If the connector environment cannot run the repository, document that limitation and run a focused reconstructed harness instead; do not claim the full suite passed.

- [ ] **Step 7: Commit production change**

```bash
git add search_items.py tests/test_direct_structured_intent.py
git commit -m "feat: normalize simple natural-language searches"
```

---

### Task 3: Review routing boundaries and open PR

**Files:**
- Review: `search_items.py`
- Review: `tests/test_direct_structured_intent.py`
- Review: design/plan docs

- [ ] **Step 1: Verify no semantic build expansion**

Confirm `tank`, `dps`, `build`, and `mage` still follow the existing refusal path.

- [ ] **Step 2: Verify direct syntax unchanged**

Confirm queries such as `hp armor`, `cr bow`, and `best bow cr` still use existing deterministic behavior.

- [ ] **Step 3: Compare branch with `main`**

Confirm branch is not behind and only intended files changed.

- [ ] **Step 4: Open a PR without merging**

Document focused verification and any environment limitation. Do not merge without explicit user instruction.
