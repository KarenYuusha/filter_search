# Item and Upgrade Search Relevance Filtering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent item-name and upgrade-name searches from exposing the entire candidate database by filtering ranked names to lexical matches or fuzzy matches scoring at least 70.0.

**Architecture:** Keep `rank_items(query, items)` as the only shared name-ranking entry point. Add one relevance predicate immediately after `_score_item()` so both normal item lookup and upgrade/crysta lookup inherit the same filtering without frontend-specific logic. Exact item/upgrade resolution, stat searches, Qwen fallback, and upgrade graph traversal remain unchanged.

**Tech Stack:** Python 3.12, `unittest`, RapidFuzz, existing `SearchService`.

## Global Constraints

- Preserve `exact`, `prefix`, `substring`, and `all_tokens` matches regardless of fuzzy threshold.
- Keep fuzzy-only candidates only when final score is at least `70.0`.
- Do not impose a top-N cap; retain every candidate that passes the relevance rule.
- Do not change result page size.
- Do not change exact-name resolution.
- Do not change stat search behavior, Qwen/fallback behavior, or upgrade graph contents.
- Normal item-name and upgrade-name search must share the same relevance implementation through `rank_items()`.

---

### Task 1: Add RED coverage for shared ranking and service flows

**Files:**
- Create: `tests/test_item_search_relevance.py`
- Read: `search_items.py`
- Read: `toram_search/service.py`

**Interfaces:**
- Consumes: `search_items.ItemSummary`, `search_items.rank_items`, `toram_search.service.SearchService`, `ItemResultsPayload`, and `UpgradeResultsPayload`.
- Produces: failing regression tests that demonstrate irrelevant candidates are currently retained.

- [ ] **Step 1: Create direct ranking fixtures and tests**

Create `tests/test_item_search_relevance.py` with a small candidate list such as:

```python
ITEMS = [
    core.ItemSummary(1, "Don", "Normal Crysta"),
    core.ItemSummary(2, "Don Profundo", "Normal Crysta"),
    core.ItemSummary(3, "Don Upgrade B", "Enhancer Crysta (Red)"),
    core.ItemSummary(4, "Completely Different Sword", "One-Handed Sword"),
    core.ItemSummary(5, "Unrelated Armor", "Armor"),
]
```

Add direct tests proving:

```python
def test_rank_items_keeps_lexical_matches_and_drops_irrelevant_candidates():
    results = core.rank_items("Don", ITEMS)
    assert [result.item.name for result in results] == ["Don", "Don Profundo", "Don Upgrade B"]


def test_rank_items_keeps_reasonable_fuzzy_typo():
    results = core.rank_items("Don Upgrad B", ITEMS)
    assert results
    assert results[0].item.name == "Don Upgrade B"
    assert results[0].score >= 70.0


def test_rank_items_returns_empty_for_unrelated_query():
    results = core.rank_items("asdfgh", ITEMS)
    assert results == []
```

Also add a controlled lexical-preservation test by patching `_score_item()` to return a low numeric score with `match_kind="substring"`, proving lexical kinds are retained independently of the threshold.

- [ ] **Step 2: Add service integration fixtures**

Create a minimal fake repository whose `list_items()` contains both relevant and unrelated normal items and crystas, whose exact-name methods return no match for fuzzy test queries, and whose database/help methods satisfy `SearchService` construction. Add tests:

```python
def test_item_search_payload_contains_only_relevant_candidates():
    outcome = service.handle_query("Donn", context)
    assert isinstance(outcome.payload, ItemResultsPayload)
    assert "Unrelated Armor" not in {result.item.name for result in outcome.payload.results}


def test_upgrade_search_payload_contains_only_relevant_crystas():
    outcome = service.handle_query("upgrade Donn", context)
    assert isinstance(outcome.payload, UpgradeResultsPayload)
    names = {result.item.name for result in outcome.payload.results}
    assert "Don" in names or "Don Upgrade B" in names
    assert "Completely Different Crysta" not in names
```

Add an unrelated-name integration test asserting the payload result tuple is empty rather than containing random database rows.

- [ ] **Step 3: Run the new tests and verify RED**

Run:

```bash
python -m unittest tests.test_item_search_relevance -v
```

Expected: failures showing `rank_items()` retains irrelevant fuzzy candidates and therefore item/upgrade payloads contain unrelated rows.

- [ ] **Step 4: Commit RED tests**

```bash
git add tests/test_item_search_relevance.py
git commit -m "test: expose irrelevant name search results"
```

---

### Task 2: Implement the shared relevance gate

**Files:**
- Modify: `search_items.py`
- Test: `tests/test_item_search_relevance.py`

**Interfaces:**
- Consumes: `_score_item(query, item) -> RankedItem`.
- Produces: `rank_items(query, items) -> list[RankedItem]` containing only relevant candidates.

- [ ] **Step 1: Add one shared threshold constant**

Near the existing search constants in `search_items.py`, add:

```python
ITEM_FUZZY_RELEVANCE_THRESHOLD = 70.0
```

- [ ] **Step 2: Add the minimal relevance predicate**

Near `_score_item()` / `rank_items()`, add:

```python
def _is_relevant_ranked_item(result: RankedItem) -> bool:
    if result.match_kind in {"exact", "prefix", "substring", "all_tokens"}:
        return True
    return result.score >= ITEM_FUZZY_RELEVANCE_THRESHOLD
```

Do not add query-specific or frontend-specific branches.

- [ ] **Step 3: Filter before sorting**

Change `rank_items()` from scoring every row into returning every row to:

```python
results = [
    result
    for item in items
    if _is_relevant_ranked_item(result := _score_item(normalized_query, item))
]
```

Keep the existing sort key unchanged.

- [ ] **Step 4: Run the new relevance tests and verify GREEN**

Run:

```bash
python -m unittest tests.test_item_search_relevance -v
```

Expected: all relevance tests pass.

- [ ] **Step 5: Run nearby service/upgrade regressions**

Run:

```bash
python -m unittest \
  tests.test_item_search_relevance \
  tests.test_search_service \
  tests.test_cli_upgrade \
  tests.test_discord_followup_regressions \
  tests.test_discord_bot -v
```

Expected: all focused tests pass.

- [ ] **Step 6: Commit production fix**

```bash
git add search_items.py
git commit -m "fix: filter irrelevant item search candidates"
```

---

### Task 3: Final verification and PR preparation

**Files:**
- No production changes expected.
- Update PR description only after verification.

**Interfaces:**
- Consumes: completed relevance filter.
- Produces: verification evidence and a clean review PR from `fix/item-upgrade-search-relevance` to `main`.

- [ ] **Step 1: Compile changed runtime modules**

Run:

```bash
python -m py_compile search_items.py toram_search/service.py discord_bot.py
```

Expected: success.

- [ ] **Step 2: Run focused regressions**

Run:

```bash
python -m unittest \
  tests.test_item_search_relevance \
  tests.test_search_service \
  tests.test_cli_upgrade \
  tests.test_discord_followup_regressions \
  tests.test_discord_bot \
  tests.test_direct_structured_intent \
  tests.test_llm -v
```

Expected: all focused tests pass.

- [ ] **Step 3: Run the full repository suite**

Run:

```bash
python -m unittest discover -s tests -v
```

Expected baseline: all relevance tests pass. If the existing `test_rejected_payload_is_logged_with_reason` still fails because it expects `missing or invalid search candidates` while fallback logs `search payload has unexpected fields`, record it as the unchanged unrelated baseline failure and do not modify fallback code in this fix.

- [ ] **Step 4: Inspect the final diff**

Confirm only these files differ from `main`:

```text
docs/superpowers/specs/2026-08-10-item-search-relevance-filter-design.md
docs/superpowers/plans/2026-08-10-item-search-relevance-filter.md
search_items.py
tests/test_item_search_relevance.py
```

No Discord rendering change, upgrade graph change, `.env`, token, guild ID, temporary workflow/helper, or fallback/Qwen code should be included.

- [ ] **Step 5: Open a draft PR**

Create a draft PR titled `Filter irrelevant item and upgrade search results`, include RED/GREEN evidence and final test counts, and do not merge without explicit user instruction.
