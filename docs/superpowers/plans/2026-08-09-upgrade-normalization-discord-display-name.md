# Upgrade Query Normalization and Discord Display-Name Examples Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make approved natural-language upgrade queries deterministic in both the CLI and Discord, preserve fuzzy upgrade selection through to direct successors, and render Discord example queries with the bot's current guild display name instead of raw mention IDs.

**Architecture:** Add a narrow natural-upgrade target extractor in shared `search_items.py`, so CLI and `SearchService` receive the same upgrade intents without Qwen. Add one public `SearchService.continue_upgrade_selection()` path for Discord fuzzy candidate selection, then keep Discord naming presentation-only through a helper that returns plain `@<display_name>` example text.

**Tech Stack:** Python 3.12, `unittest`, existing `discord.py>=2.7.1,<3`, existing Toram search/repository code.

## Global Constraints

- Supported forms: `upgrade <name>`, `upgrade for <name>`, `upgrades for <name>`, `show upgrades for <name>`, `find upgrades for <name>`, `what upgrades from <name>`, `what can upgrade <name>`, `what comes after <name>`, `next xtal after <name>`.
- Matching is case-insensitive and ignores trailing `?`, `.`, `!`.
- Supported forms must bypass Qwen.
- Existing exact/fuzzy crysta resolution stays available.
- Selecting a fuzzy Discord upgrade candidate must show that candidate's direct upgrade successors, not normal item detail.
- `best upgrade`, `strongest upgrade`, `better xtal`, build-role wording, and other subjective forms are not normalized.
- Upgrade semantics remain direct successors only.
- Discord examples use guild member `display_name`, falling back to account `display_name`/`name`.
- Example prefixes are plain text such as `@Toram Search`, never `<@123...>` mention syntax.
- Existing `AllowedMentions.none()` remains unchanged.
- No new dependency.

---

## File Structure

- Modify `search_items.py`: shared natural-upgrade target extraction and parser integration.
- Modify `toram_search/service.py`: public deterministic continuation from selected fuzzy upgrade candidate to direct successors.
- Modify `discord_bot.py`: fuzzy upgrade selection continuation plus display-name example prefix rendering.
- Modify `tests/test_discord_followup_regressions.py`: shared parser/service normalization and continuation tests.
- Modify `tests/test_cli_upgrade.py`: CLI natural-query regression.
- Modify `tests/test_discord_bot.py`: Discord display-name and upgrade-selection helper regressions.

---

### Task 1: Shared Natural Upgrade Normalization

**Files:**
- Modify: `search_items.py` near `parse_search_query()`
- Modify: `tests/test_discord_followup_regressions.py`
- Modify: `tests/test_cli_upgrade.py`

**Interfaces:**
- Produces: `extract_natural_upgrade_target(text: str) -> str | None`.
- `parse_search_query()` uses this before normal item/stat parsing and returns existing `exact_upgrade` or `upgrade_search` intents.

- [ ] **Step 1: Add failing service regressions for the whitelist**

Add to `UpgradeLookupTests`:

```python
    def test_natural_upgrade_forms_are_deterministic(self):
        service = SearchService(FakeRepository(), llm_client=MustNotCallLLM())
        queries = (
            "upgrade Don",
            "upgrade for Don",
            "upgrades for Don",
            "show upgrades for Don",
            "find upgrades for Don",
            "what upgrades from Don",
            "what can upgrade Don",
            "what comes after Don",
            "next xtal after Don",
            "WHAT UPGRADES FROM DON?",
            "show upgrades for Don.",
        )
        for query in queries:
            with self.subTest(query=query):
                outcome = service.handle_query(query, FailedQueryContext(max_entries=3))
                self.assertEqual(outcome.kind, "search")
                self.assertIsInstance(outcome.payload, UpgradeResultsPayload)
                self.assertEqual(
                    [result.item.name for result in outcome.payload.results],
                    ["Don Upgrade A", "Don Upgrade B"],
                )

    def test_subjective_upgrade_wording_is_not_normalized(self):
        for query in ("best upgrade for Don", "strongest upgrade for Don", "better xtal after Don"):
            with self.subTest(query=query):
                self.assertIsNone(core.extract_natural_upgrade_target(query))
```

- [ ] **Step 2: Run and verify RED**

```bash
python -m unittest tests.test_discord_followup_regressions.UpgradeLookupTests -v
```

Expected: FAIL because the extractor/natural routing does not exist.

- [ ] **Step 3: Add failing CLI end-to-end regression**

```python
    def test_natural_upgrade_query_uses_direct_successor_screen_without_qwen(self):
        repository = CliRepository()
        answers = iter(["what upgrades from Don?", "quit"])
        output: list[str] = []
        result = core.interactive_search(
            repository,
            input_fn=lambda _prompt: next(answers),
            output_fn=output.append,
            llm_client=MustNotCallLLM(),
        )
        rendered = "\n".join(output)
        self.assertEqual(result, 0)
        self.assertIn("Upgrades from Don", rendered)
        self.assertIn("Don Upgrade A", rendered)
        self.assertIn("Don Upgrade B", rendered)
```

- [ ] **Step 4: Run and verify CLI RED**

```bash
python -m unittest tests.test_cli_upgrade.CliUpgradeTests.test_natural_upgrade_query_uses_direct_successor_screen_without_qwen -v
```

Expected: FAIL because the natural phrase does not reach the upgrade screen.

- [ ] **Step 5: Implement the narrow extractor**

Add before `parse_search_query()`:

```python
_NATURAL_UPGRADE_PATTERNS = (
    r"upgrade\s+for\s+(.+)",
    r"upgrades\s+for\s+(.+)",
    r"(?:show|find)\s+upgrades\s+for\s+(.+)",
    r"what\s+upgrades\s+from\s+(.+)",
    r"what\s+can\s+upgrade\s+(.+)",
    r"what\s+comes\s+after\s+(.+)",
    r"next\s+xtal\s+after\s+(.+)",
)


def extract_natural_upgrade_target(text: str) -> str | None:
    cleaned = " ".join(text.strip().rstrip("?.!").split())
    for pattern in _NATURAL_UPGRADE_PATTERNS:
        match = re.fullmatch(pattern, cleaned, flags=re.IGNORECASE)
        if match is not None:
            target = match.group(1).strip()
            return target or None
    return None
```

Do not add generic filler stripping or subjective keywords.

- [ ] **Step 6: Integrate with existing parser**

After empty-query handling in `parse_search_query()`:

```python
    natural_upgrade_target = extract_natural_upgrade_target(raw)
    if natural_upgrade_target is not None:
        exact = repository.exact_upgrade_name_matches(natural_upgrade_target)
        if len(exact) == 1:
            return ParsedSearch("exact_upgrade", raw, item_id=exact[0].id)
        return ParsedSearch("upgrade_search", raw, item_query=natural_upgrade_target)
```

Keep explicit `upgrade Don` handling unchanged.

- [ ] **Step 7: Run Task 1 GREEN gate**

```bash
python -m unittest tests.test_discord_followup_regressions.UpgradeLookupTests tests.test_cli_upgrade -v
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add search_items.py tests/test_discord_followup_regressions.py tests/test_cli_upgrade.py
git commit -m "feat: normalize natural upgrade queries"
```

---

### Task 2: Discord Fuzzy Upgrade Candidate Continuation

**Files:**
- Modify: `toram_search/service.py`
- Modify: `discord_bot.py` in `SearchResultsView._select_item()` and worker helpers
- Modify: `tests/test_discord_followup_regressions.py`
- Modify: `tests/test_discord_bot.py`

**Interfaces:**
- Produces: `SearchService.continue_upgrade_selection(item_id: int, item_name: str) -> ServiceOutcome`.
- Produces: `run_upgrade_selection_sync(database_path, item_id, item_name, *, repository_factory=...) -> ServiceOutcome`.
- Produces: `is_upgrade_suggestion_payload(payload: SearchPayload) -> bool` for a pure UI decision test.

- [ ] **Step 1: Add failing service continuation test**

```python
    def test_selected_fuzzy_upgrade_candidate_returns_direct_successors(self):
        service = SearchService(FakeRepository(), llm_client=MustNotCallLLM())
        outcome = service.continue_upgrade_selection(1, "Don")
        self.assertEqual(outcome.kind, "search")
        self.assertIsInstance(outcome.payload, UpgradeResultsPayload)
        self.assertEqual(
            [result.item.name for result in outcome.payload.results],
            ["Don Upgrade A", "Don Upgrade B"],
        )
```

- [ ] **Step 2: Run and verify RED**

```bash
python -m unittest tests.test_discord_followup_regressions.UpgradeLookupTests.test_selected_fuzzy_upgrade_candidate_returns_direct_successors -v
```

Expected: FAIL because `continue_upgrade_selection()` does not exist.

- [ ] **Step 3: Implement public service continuation**

Add beside `item_detail()`:

```python
    def continue_upgrade_selection(self, item_id: int, item_name: str) -> ServiceOutcome:
        parsed = core.ParsedSearch(
            intent="exact_upgrade",
            raw_query=f"upgrade {item_name}",
            item_id=item_id,
        )
        return ServiceOutcome("search", payload=self._materialize(parsed, {}))
```

This reuses `_materialize()` and therefore the same direct-successor ranking as normal exact upgrade queries.

- [ ] **Step 4: Add failing pure Discord suggestion classifier test**

Add imports for `is_upgrade_suggestion_payload`. Build two `UpgradeResultsPayload` objects: one with `RankedItem(..., match_kind="fuzzy")` and one with `match_kind="upgrade"`. Assert the first is `True` and the second `False`.

```python
    def test_upgrade_suggestion_payload_is_distinguished_from_direct_results(self):
        fuzzy = UpgradeResultsPayload(
            "Dno",
            (core.RankedItem(core.ItemSummary(1, "Don", "Normal Crysta"), 90.0, "fuzzy"),),
        )
        direct = UpgradeResultsPayload(
            "Don",
            (core.RankedItem(core.ItemSummary(2, "Don Upgrade A", "Enhancer Crysta (Blue)"), 100.0, "upgrade"),),
        )
        self.assertTrue(is_upgrade_suggestion_payload(fuzzy))
        self.assertFalse(is_upgrade_suggestion_payload(direct))
```

- [ ] **Step 5: Implement Discord continuation worker and selection branch**

Add:

```python
def is_upgrade_suggestion_payload(payload: SearchPayload) -> bool:
    return (
        isinstance(payload, UpgradeResultsPayload)
        and bool(payload.results)
        and not all(result.match_kind == "upgrade" for result in payload.results)
    )


def run_upgrade_selection_sync(
    database_path: Path,
    item_id: int,
    item_name: str,
    *,
    repository_factory=core.ItemRepository,
) -> ServiceOutcome:
    repository = repository_factory(database_path.resolve())
    try:
        return SearchService(repository).continue_upgrade_selection(item_id, item_name)
    finally:
        repository.close()
```

In `SearchResultsView._select_item()`, before `run_item_detail_sync`, branch on `is_upgrade_suggestion_payload(self.payload)`. Resolve the selected item using `_result_item()`, defer, call `run_upgrade_selection_sync` in `asyncio.to_thread`, recheck session generation, set `session.page = 0`, then call `edit_service_outcome(...)` with the same session/generation. Do not start a new query or generation.

- [ ] **Step 6: Run Task 2 GREEN gate**

```bash
python -m unittest tests.test_discord_followup_regressions.UpgradeLookupTests tests.test_discord_bot.DiscordFormattingTests -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add toram_search/service.py discord_bot.py tests/test_discord_followup_regressions.py tests/test_discord_bot.py
git commit -m "fix: continue fuzzy Discord upgrade selection"
```

---

### Task 3: Discord Guild Display-Name Example Prefixes

**Files:**
- Modify: `discord_bot.py` near `extract_mentioned_query()`, `build_help_embed()`, `build_service_outcome_message()`, `edit_service_outcome()`, `process_tagged_query()`, `create_client()`
- Modify: `tests/test_discord_bot.py`

**Interfaces:**
- Produces: `bot_example_prefix(guild, bot_user) -> str`.
- Renames message-builder keyword `bot_mention` to `bot_example_prefix`.

- [ ] **Step 1: Add failing prefix tests**

```python
    def test_bot_example_prefix_prefers_guild_display_name(self):
        member = SimpleNamespace(display_name="Toram Search")
        guild = SimpleNamespace(get_member=lambda user_id: member if user_id == 99 else None)
        bot_user = SimpleNamespace(id=99, display_name="GlobalBot", name="GlobalBot")
        self.assertEqual(bot_example_prefix(guild, bot_user), "@Toram Search")

    def test_bot_example_prefix_falls_back_to_account_name(self):
        guild = SimpleNamespace(get_member=lambda _user_id: None)
        bot_user = SimpleNamespace(id=99, display_name="GlobalBot", name="GlobalBot")
        self.assertEqual(bot_example_prefix(guild, bot_user), "@GlobalBot")
```

- [ ] **Step 2: Add failing no-raw-ID rendering tests**

For `build_help_embed("@Toram Search")`, assert examples include `@Toram Search hp armor` and contain no `<@`.

For `build_service_outcome_message(ServiceOutcome(kind="failed"), bot_example_prefix="@Toram Search", ...)`, assert description contains `@Toram Search hp armor` and no `<@`.

- [ ] **Step 3: Run and verify RED**

```bash
python -m unittest tests.test_discord_bot.DiscordFormattingTests -v
```

Expected: FAIL because the helper/new keyword do not exist.

- [ ] **Step 4: Implement prefix helper**

```python
def bot_example_prefix(guild, bot_user) -> str:
    member = None
    get_member = getattr(guild, "get_member", None) if guild is not None else None
    if bot_user is not None and callable(get_member):
        member = get_member(bot_user.id)
    display_name = (
        getattr(member, "display_name", None)
        or getattr(bot_user, "display_name", None)
        or getattr(bot_user, "name", None)
        or "Bot"
    )
    return f"@{display_name}"
```

- [ ] **Step 5: Replace raw mention example plumbing**

Rename `build_help_embed(bot_mention)` and `build_service_outcome_message(..., bot_mention=...)` parameters to `bot_example_prefix` and update every help/refusal/failed-search interpolation.

In `edit_service_outcome()` compute:

```python
    prefix = bot_example_prefix(interaction.guild, interaction.client.user)
```

and pass it to the message builder.

Change `process_tagged_query()` to accept `bot_user`, derive `bot_user_id = bot_user.id`, compute `prefix = bot_example_prefix(message.guild, bot_user)`, and pass it to the message builder. Update `create_client().on_message()` to pass `bot_user=client.user`.

Incoming gating still uses real mention IDs; only output examples change.

- [ ] **Step 6: Run Task 3 GREEN gate**

```bash
python -m unittest tests.test_discord_bot -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add discord_bot.py tests/test_discord_bot.py
git commit -m "feat: show Discord display name in examples"
```

---

### Task 4: Final Verification and PR Cleanup

**Files:**
- Verify final diff only.
- Remove any temporary GitHub Actions workflow used solely for remote test execution.

- [ ] **Step 1: Compile changed modules**

```bash
python -m py_compile search_items.py discord_bot.py toram_search/service.py
```

Expected: exit code 0.

- [ ] **Step 2: Run focused gate**

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

If `test_structured_fallback.StructuredFallbackTests.test_rejected_payload_is_logged_with_reason` still expects `missing or invalid search candidates` while production logs `search payload has unexpected fields`, record that unchanged baseline failure; do not change fallback behavior in this PR.

- [ ] **Step 4: Review final diff**

Expected final files:

```text
docs/superpowers/specs/2026-08-09-upgrade-normalization-discord-display-name-design.md
docs/superpowers/plans/2026-08-09-upgrade-normalization-discord-display-name.md
search_items.py
toram_search/service.py
discord_bot.py
tests/test_discord_followup_regressions.py
tests/test_cli_upgrade.py
tests/test_discord_bot.py
```

No `.env`, real token/config IDs, or temporary CI workflow remains.

- [ ] **Step 5: Prepare PR for review**

PR body must state:

```text
- natural upgrade aliases work deterministically in CLI and Discord
- fuzzy Discord base-crysta selection continues to direct upgrades
- supported aliases do not call Qwen
- subjective best/strongest upgrade wording remains outside scope
- Discord examples show guild display name as plain text with no raw mention ID
- focused/full-suite verification results and any unchanged baseline failure
```
