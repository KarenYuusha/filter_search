# Upgrade Chain, Natural Multi-Stat Search, and Discord Display-Name Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every deterministic `upgrade <crysta>` lookup return the complete connected upgrade chain, and make plain natural `item-type has/have stat1 and stat2` queries work in both `search_items.py` and Discord without Qwen.

**Architecture:** Reuse the repository's existing `get_upgrade_component(item_id)` traversal and the existing `UpgradeDetailPayload` renderers instead of maintaining a second direct-successor path. Extend only the shared natural-stat normalizer in `toram_data/stat_query.py` so both frontends receive the same parsed AND expression and existing ambiguity handling remains authoritative.

**Tech Stack:** Python 3.12, `unittest`, SQLite-backed repository code, `discord.py>=2.7.1,<3`.

## Global Constraints

- `upgrade <name>` and every already-approved natural upgrade alias return the full connected upgrade component, including predecessors, successors, branches, and the selected item.
- Querying the first, middle, or last item in one component returns the same component.
- Fuzzy upgrade matching is only for choosing the intended crysta; selecting it transitions to the whole-chain result.
- Subjective upgrade queries such as `best upgrade for Don` remain outside deterministic normalization.
- `bow has cr and ampr`, `bows have cr and ampr`, and equivalent item-type `has/have` forms are deterministic shared parsing.
- AND keeps existing AND semantics. OR keeps existing parser semantics.
- `crt` remains ambiguous between Critical Rate and Critical Damage; never guess it.
- Choosing a `crt` meaning preserves all other clauses and the item-type filter.
- Recognized deterministic forms must not call Qwen.
- Discord output examples continue using the guild bot `display_name` as plain text, never raw `<@id>` syntax.
- No database schema change and no new dependency.

---

## File Structure

- Modify `toram_search/service.py`: materialize exact/selected upgrade lookups as `UpgradeDetailPayload` using `get_upgrade_component()`.
- Modify `search_items.py`: terminal exact/fuzzy-selected upgrade flow renders the existing full upgrade graph instead of the direct-successor list.
- Modify `discord_bot.py`: fuzzy upgrade candidate selection transitions to the full-chain payload; remove direct-successor-specific output assumptions where they conflict.
- Modify `toram_data/stat_query.py`: add plain `<item type> has/have <stat expression>` natural normalization.
- Modify `tests/test_cli_upgrade.py`: first/middle/last and fuzzy-selection whole-chain CLI regressions.
- Modify `tests/test_discord_followup_regressions.py`: service whole-chain and natural multi-stat routing/clarification regressions.
- Modify `tests/test_discord_bot.py`: Discord whole-chain rendering/selection regressions while retaining display-name tests.

---

### Task 1: Whole Upgrade Component in Shared Service

**Files:**
- Modify: `toram_search/service.py`
- Modify: `tests/test_discord_followup_regressions.py`

**Interfaces:**
- Existing `ItemRepository.get_upgrade_component(item_id: int) -> UpgradeGraph` is authoritative.
- Existing `UpgradeDetailPayload(graph: UpgradeGraph, selected_item_id: int)` becomes the exact-upgrade result.
- Existing `SearchService.continue_upgrade_selection(item_id: int, item_name: str) -> ServiceOutcome` continues to build an `exact_upgrade` intent and therefore inherits whole-chain behavior.

- [ ] **Step 1: Write RED tests for first/middle/last exact upgrade lookups**

Build a fake upgrade component such as `A -> B -> C` and make `FakeRepository.get_upgrade_component()` return the same graph for IDs 1, 2, and 3. For each query, assert:

```python
outcome = service.handle_query(query, FailedQueryContext(max_entries=3))
self.assertEqual(outcome.kind, "search")
self.assertIsInstance(outcome.payload, UpgradeDetailPayload)
self.assertEqual(set(outcome.payload.graph.nodes), {1, 2, 3})
self.assertEqual(outcome.payload.selected_item_id, expected_selected_id)
```

Include the last-node query to prove no-successor items still render the chain.

- [ ] **Step 2: Write RED test for fuzzy-selected candidate continuation**

```python
outcome = service.continue_upgrade_selection(3, "C")
self.assertIsInstance(outcome.payload, UpgradeDetailPayload)
self.assertEqual(set(outcome.payload.graph.nodes), {1, 2, 3})
self.assertEqual(outcome.payload.selected_item_id, 3)
```

- [ ] **Step 3: Run RED**

```bash
python -m unittest tests.test_discord_followup_regressions.UpgradeLookupTests -v
```

Expected: failures because current `exact_upgrade` materialization returns `UpgradeResultsPayload` with direct successors.

- [ ] **Step 4: Implement minimal shared-service fix**

Replace only the `parsed.intent == "exact_upgrade"` branch in `_materialize()` with:

```python
if parsed.intent == "exact_upgrade":
    selected_id = int(parsed.item_id)
    return UpgradeDetailPayload(
        graph=self.repository.get_upgrade_component(selected_id),
        selected_item_id=selected_id,
    )
```

Do not add a new graph algorithm.

- [ ] **Step 5: Keep fuzzy `upgrade_search` as candidate discovery only**

For a unique exact crysta inside `upgrade_search`, materialize the same `UpgradeDetailPayload`; for non-unique/fuzzy names, keep `UpgradeResultsPayload` as the candidate list. This avoids using a direct-successor result list for any resolved crysta.

- [ ] **Step 6: Run GREEN**

```bash
python -m unittest tests.test_discord_followup_regressions.UpgradeLookupTests -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add toram_search/service.py tests/test_discord_followup_regressions.py
git commit -m "fix: return complete upgrade components"
```

---

### Task 2: Whole Upgrade Chain in CLI and Discord Selection

**Files:**
- Modify: `search_items.py`
- Modify: `discord_bot.py`
- Modify: `tests/test_cli_upgrade.py`
- Modify: `tests/test_discord_bot.py`

**Interfaces:**
- CLI uses existing `get_upgrade_component()` and existing graph renderer.
- Discord already renders `UpgradeDetailPayload` with `build_upgrade_detail_embed()`.
- Fuzzy candidate `UpgradeResultsPayload` remains selectable; selection calls `continue_upgrade_selection()` and therefore produces `UpgradeDetailPayload`.

- [ ] **Step 1: Write CLI RED for last-node query**

Use a repository fixture whose chain is `A -> B -> C`. Feed `upgrade C` then `quit`. Assert output contains the full chain and does not show `No direct upgrade crystas found.`.

```python
self.assertIn("Upgrade chain for C", rendered)
self.assertIn("A", rendered)
self.assertIn("B", rendered)
self.assertIn("C", rendered)
self.assertNotIn("No direct upgrade crystas found", rendered)
```

- [ ] **Step 2: Write CLI RED for first/middle equivalence**

Run `upgrade A` and `upgrade B` separately and assert all chain nodes appear in each output.

- [ ] **Step 3: Run CLI RED**

```bash
python -m unittest tests.test_cli_upgrade -v
```

Expected: current direct-successor terminal flow fails whole-chain assertions.

- [ ] **Step 4: Restore terminal exact-upgrade graph rendering**

In `interactive_search()`, when a resolved upgrade item is known, call:

```python
graph = repository.get_upgrade_component(item_id)
output_fn(render_upgrade_graph(graph, selected_item_id=item_id))
```

Use the existing graph renderer/signature in the file. Remove only the direct-successor screen from the resolved-exact path; fuzzy candidate pagination remains for name selection.

- [ ] **Step 5: Verify Discord fuzzy selection uses chain payload**

Update Discord tests so `is_upgrade_suggestion_payload()` only distinguishes unresolved candidate lists. Selecting such a candidate must call `continue_upgrade_selection()` and receive an `UpgradeDetailPayload`; `build_upgrade_detail_embed()` should include predecessor and successor edges even when selected item is the last node.

- [ ] **Step 6: Run Task 2 GREEN**

```bash
python -m unittest tests.test_cli_upgrade tests.test_discord_bot tests.test_discord_followup_regressions.UpgradeLookupTests -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add search_items.py discord_bot.py tests/test_cli_upgrade.py tests/test_discord_bot.py
git commit -m "fix: show full upgrade chains in both frontends"
```

---

### Task 3: Plain `has/have` Natural Multi-Stat Grammar

**Files:**
- Modify: `toram_data/stat_query.py`
- Modify: `tests/test_discord_followup_regressions.py`
- Modify: `tests/test_cli_upgrade.py` only if an existing CLI integration fixture is appropriate; otherwise add the end-to-end CLI case to the existing deterministic-intent test module.

**Interfaces:**
- Extend `normalize_natural_stat_query(text, available_item_types) -> str`.
- Reuse `parse_stat_expression()` and existing `resolve_expression_noninteractively()` clarification behavior.

- [ ] **Step 1: Write RED parser/service tests for unambiguous aliases**

Add representative assertions for:

```text
bow has cr and ampr
bows have cr and ampr
armor has hp and physical resistance
bow has cr and ampr and stability
```

For `bow has cr and ampr`, assert the parsed/resolved expression contains one AND group with canonical `Critical Rate` and `Attack MP Recovery`, plus Bow filter. Use `MustNotCallLLM` in the service-level test.

- [ ] **Step 2: Write RED ambiguity-preservation test**

For `bow has crt and ampr`, assert `SearchService.handle_query()` returns `StatClarificationPayload` for `crt`, and that the payload's parsed expression still contains the AMPR clause and Bow filter.

Then continue with the selected `Critical Rate` choice and assert the resulting expression still includes both canonical stats and Bow filter.

- [ ] **Step 3: Run RED**

```bash
python -m unittest tests.test_discord_followup_regressions -v
```

Expected: plain `bow has ...` forms fail to normalize and route correctly.

- [ ] **Step 4: Add narrow natural patterns**

In `_NATURAL_SEARCH_PATTERNS`, add plain item-first forms after the more explicit existing patterns:

```python
re.compile(r"^\s*(.+?)\s+has\s+(.+?)\s*$", re.IGNORECASE),
re.compile(r"^\s*(.+?)\s+have\s+(.+?)\s*$", re.IGNORECASE),
```

Do not add generic filler stripping. `_complete_natural_item_filter_phrase()` remains the guard that requires the left side to resolve to a valid item type, including plural singularization.

- [ ] **Step 5: Run GREEN for parser/service**

```bash
python -m unittest tests.test_discord_followup_regressions tests.test_direct_structured_intent tests.test_search_service -v
```

Expected: PASS, including `crt` clarification without Qwen.

- [ ] **Step 6: Add/verify frontend integration cases**

Run a CLI end-to-end query and a Discord/service route using `bow has cr and ampr`; verify neither returns the failed-search/unavailable copy and both use the same expression semantics.

- [ ] **Step 7: Commit**

```bash
git add toram_data/stat_query.py tests/
git commit -m "feat: parse item-type has multi-stat queries"
```

---

### Task 4: Regression, Documentation, and PR Verification

**Files:**
- Modify: this plan only for checkbox completion if useful.
- Modify: PR #18 body to describe full-chain semantics and natural multi-stat support.
- Temporary verification files, if required by remote execution, must not remain in the final PR diff.

- [ ] **Step 1: Compile changed modules**

```bash
python -m py_compile search_items.py discord_bot.py toram_search/service.py toram_data/stat_query.py
```

Expected: exit 0.

- [ ] **Step 2: Run focused regression gate**

```bash
python -m unittest \
  tests.test_cli_upgrade \
  tests.test_discord_followup_regressions \
  tests.test_discord_bot \
  tests.test_search_service \
  tests.test_direct_structured_intent \
  tests.test_llm -v
```

Expected: all focused tests pass.

- [ ] **Step 3: Run full suite**

```bash
python -m unittest discover -s tests -v
```

Record exact totals. If the existing `test_rejected_payload_is_logged_with_reason` mismatch is still the only failure, report it explicitly rather than claiming a fully green repository.

- [ ] **Step 4: Review final diff against `main`**

Confirm:

- no direct-successor-only semantics remain for resolved upgrade lookups;
- fuzzy candidate lists still work;
- no raw Discord ID examples return;
- no `.env`, real token, guild ID, temporary workflow, or patch helper is present;
- no unrelated fallback/Qwen logging code was changed.

- [ ] **Step 5: Update PR #18 body**

Document whole-chain behavior, supported `has/have` examples, ambiguity handling, exact verification counts, and the known unrelated baseline failure if still present.
