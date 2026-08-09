# Upgrade Query Normalization and Discord Display-Name Examples Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make approved natural-language upgrade queries deterministic in both the CLI and Discord, and render Discord example queries with the bot's current guild display name instead of raw mention IDs.

**Architecture:** Add a narrow natural-upgrade target extractor in the shared `search_items.py` parser so both `interactive_search()` and `toram_search.service.SearchService` receive the same `exact_upgrade` / `upgrade_search` intents without Qwen. Keep Discord naming presentation-only by deriving an `@<display_name>` example prefix from the current guild member, falling back to the client account name, and pass that prefix through existing Discord embed/message builders.

**Tech Stack:** Python 3.12, `unittest`, existing `discord.py>=2.7.1,<3`, existing Toram SQLite repository/search services.

## Global Constraints

- Supported natural upgrade forms are exactly the approved whitelist: `upgrade <name>`, `upgrade for <name>`, `upgrades for <name>`, `show upgrades for <name>`, `find upgrades for <name>`, `what upgrades from <name>`, `what can upgrade <name>`, `what comes after <name>`, and `next xtal after <name>`.
- Matching is case-insensitive and ignores simple trailing `?`, `.`, and `!` punctuation.
- Supported natural forms must bypass Qwen and preserve existing exact/fuzzy crysta resolution.
- Subjective/build-dependent forms such as `best upgrade for Don`, `strongest upgrade for Don`, and `better xtal after Don` must not be normalized.
- Upgrade semantics remain direct successors only; no full-chain traversal or ranking behavior changes.
- Discord examples use the bot guild member's `display_name`; if unavailable, fall back to the bot account `display_name`/`name`.
- Discord example prefixes are plain text such as `@Toram Search`; they must never contain raw `<@123...>` mention syntax or create a ping.
- Existing `AllowedMentions.none()` behavior remains unchanged.
- No new runtime dependency is required.

---

## File Structure

- Modify `search_items.py`: shared natural-upgrade phrase extraction and parser integration only.
- Modify `discord_bot.py`: presentation-only bot example-prefix resolution and renaming of `bot_mention` message-builder parameters to reflect plain-text usage.
- Modify `tests/test_discord_followup_regressions.py`: shared service/parser deterministic upgrade normalization regressions and subjective-form guard.
- Modify `tests/test_cli_upgrade.py`: CLI natural-upgrade end-to-end regression.
- Modify `tests/test_discord_bot.py`: Discord guild display-name/fallback/raw-ID rendering regressions.
- Keep `toram_search/service.py` unchanged unless a failing regression proves shared parser output is not propagated correctly.

---

### Task 1: Shared Natural Upgrade Normalization

**Files:**
- Modify: `search_items.py` near `parse_search_query()`
- Modify: `tests/test_discord_followup_regressions.py`
- Modify: `tests/test_cli_upgrade.py`

**Interfaces:**
- Consumes: existing `normalize_name()`, `ItemRepository.exact_upgrade_name_matches()`, `ParsedSearch`, `SearchService.handle_query()`, and `_interactive_direct_upgrade_results()`.
- Produces: `extract_natural_upgrade_target(text: str) -> str | None`, used by `parse_search_query()` before ordinary item/stat parsing.

- [ ] **Step 1: Add failing shared-service regressions for every approved phrase**

Add to `UpgradeLookupTests`:

```python
    def test_natural_upgrade_forms_are_deterministic(self):
        repository = FakeRepository()
        service = SearchService(repository, llm_client=MustNotCallLLM())
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
```

Add a guard proving subjective wording is not rewritten:

```python
    def test_subjective_upgrade_wording_is_not_normalized(self):
        repository = FakeRepository()
        for query in ("best upgrade for Don", "strongest upgrade for Don", "better xtal after Don"):
            with self.subTest(query=query):
                self.assertIsNone(core.extract_natural_upgrade_target(query))
```

- [ ] **Step 2: Run shared regressions and verify RED**

Run:

```bash
python -m unittest tests.test_discord_followup_regressions.UpgradeLookupTests -v
```

Expected: FAIL because `extract_natural_upgrade_target` does not exist and/or natural forms do not produce `UpgradeResultsPayload`.

- [ ] **Step 3: Add failing CLI end-to-end regression**

Add to `CliUpgradeTests`:

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

- [ ] **Step 4: Run the CLI regression and verify RED**

Run:

```bash
python -m unittest tests.test_cli_upgrade.CliUpgradeTests.test_natural_upgrade_query_uses_direct_successor_screen_without_qwen -v
```

Expected: FAIL because the natural phrase does not yet route to the direct-upgrade screen.

- [ ] **Step 5: Implement the narrow shared target extractor**

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
    if not cleaned:
        return None
    for pattern in _NATURAL_UPGRADE_PATTERNS:
        match = re.fullmatch(pattern, cleaned, flags=re.IGNORECASE)
        if match is not None:
            target = match.group(1).strip()
            return target or None
    return None
```

Do not include patterns containing `best`, `strongest`, `better`, build roles, or arbitrary filler-word stripping.

- [ ] **Step 6: Integrate normalization into the existing upgrade parser without duplicating relationship logic**

At the start of `parse_search_query()` after empty-query handling, resolve the natural target before the existing `first/command/remainder` branch:

```python
    natural_upgrade_target = extract_natural_upgrade_target(raw)
    if natural_upgrade_target is not None:
        upgrade_exact = repository.exact_upgrade_name_matches(natural_upgrade_target)
        if len(upgrade_exact) == 1:
            return ParsedSearch("exact_upgrade", raw, item_id=upgrade_exact[0].id)
        return ParsedSearch("upgrade_search", raw, item_query=natural_upgrade_target)
```

Keep the existing explicit `upgrade Don` command branch unchanged so its current validation/error behavior remains authoritative.

- [ ] **Step 7: Run Task 1 regressions and verify GREEN**

Run:

```bash
python -m unittest \
  tests.test_discord_followup_regressions.UpgradeLookupTests \
  tests.test_cli_upgrade -v
```

Expected: all tests pass; `MustNotCallLLM` proves supported natural forms are deterministic.

- [ ] **Step 8: Commit Task 1**

```bash
git add search_items.py tests/test_discord_followup_regressions.py tests/test_cli_upgrade.py
git commit -m "feat: normalize natural upgrade queries"
```

---

### Task 2: Discord Guild Display-Name Example Prefixes

**Files:**
- Modify: `discord_bot.py` near `build_help_embed()`, `build_service_outcome_message()`, `edit_service_outcome()`, and `process_tagged_query()`
- Modify: `tests/test_discord_bot.py`

**Interfaces:**
- Consumes: a Discord guild-like object exposing `get_member(user_id)`, and a bot-user-like object exposing `id`, `display_name` and/or `name`.
- Produces: `bot_example_prefix(guild, bot_user) -> str`, returning presentation-only text such as `@Toram Search`.
- Changes message-builder keyword from `bot_mention` to `bot_example_prefix` so callers cannot mistake the value for raw Discord mention syntax.

- [ ] **Step 1: Add failing display-name prefix unit tests**

Import `bot_example_prefix`, `build_help_embed`, and `build_service_outcome_message` in `tests/test_discord_bot.py`. Add:

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

- [ ] **Step 2: Add failing rendering regression proving no raw mention ID appears**

Use the help embed directly:

```python
    def test_help_examples_use_plain_display_name_not_raw_mention(self):
        embed = build_help_embed("@Toram Search")
        visible = "\n".join(field.value for field in embed.fields)

        self.assertIn("@Toram Search hp armor", visible)
        self.assertIn("@Toram Search upgrade <crysta name>", visible)
        self.assertNotIn("<@", visible)
```

Also test the failed-search surface, because it currently interpolates the same raw mention input:

```python
    def test_failed_search_examples_use_display_name_prefix(self):
        sessions = DiscordSessionManager()
        key = (10, 30, 20)
        session = sessions.start_query(key, "bad query")
        outcome = ServiceOutcome(kind="failed")

        embed, _view, _file = build_service_outcome_message(
            outcome,
            bot_example_prefix="@Toram Search",
            sessions=sessions,
            key=key,
            generation=session.generation,
            database_path=Path("coryn_data/database/items.sqlite"),
        )

        self.assertIn("@Toram Search hp armor", embed.description or "")
        self.assertNotIn("<@", embed.description or "")
```

Import `ServiceOutcome` from `toram_search.service` for this test.

- [ ] **Step 3: Run Discord formatting tests and verify RED**

Run:

```bash
python -m unittest tests.test_discord_bot.DiscordFormattingTests -v
```

Expected: FAIL because `bot_example_prefix` and the renamed message-builder keyword do not exist.

- [ ] **Step 4: Implement guild display-name prefix resolution**

Add near `extract_mentioned_query()`:

```python
def bot_example_prefix(guild, bot_user) -> str:
    member = None
    if guild is not None and bot_user is not None:
        get_member = getattr(guild, "get_member", None)
        if callable(get_member):
            member = get_member(bot_user.id)
    display_name = (
        getattr(member, "display_name", None)
        or getattr(bot_user, "display_name", None)
        or getattr(bot_user, "name", None)
        or "Bot"
    )
    return f"@{display_name}"
```

This helper returns plain text only; do not construct `<@...>` syntax.

- [ ] **Step 5: Rename presentation parameters and replace raw mention construction**

Change:

```python
def build_help_embed(bot_mention: str) -> discord.Embed:
```

to:

```python
def build_help_embed(bot_example_prefix: str) -> discord.Embed:
```

and use `bot_example_prefix` in every example line.

Change `build_service_outcome_message(..., bot_mention: str, ...)` to `bot_example_prefix: str` and replace the `bot_mention` interpolations in help/refusal/failed-search examples.

In `edit_service_outcome()`:

```python
    prefix = bot_example_prefix(interaction.guild, interaction.client.user)
    embed, view, file = build_service_outcome_message(
        outcome,
        bot_example_prefix=prefix,
        ...
    )
```

In `process_tagged_query()`, accept a bot-user object rather than only a numeric ID:

```python
async def process_tagged_query(
    message: discord.Message,
    *,
    bot_user,
    config: DiscordBotConfig,
    sessions: DiscordSessionManager,
) -> None:
    bot_user_id = bot_user.id
    ...
    prefix = bot_example_prefix(message.guild, bot_user)
    embed, view, file = build_service_outcome_message(
        outcome,
        bot_example_prefix=prefix,
        ...
    )
```

Update `create_client().on_message()` to call `process_tagged_query(..., bot_user=client.user, ...)`.

Do not modify `is_allowed_message()` or `extract_mentioned_query()`; incoming messages still require a real Discord mention.

- [ ] **Step 6: Run Task 2 regressions and verify GREEN**

Run:

```bash
python -m unittest tests.test_discord_bot -v
```

Expected: all Discord config/gating/session/formatting tests pass, including guild nickname preference and account-name fallback.

- [ ] **Step 7: Commit Task 2**

```bash
git add discord_bot.py tests/test_discord_bot.py
git commit -m "feat: show Discord bot display name in examples"
```

---

### Task 3: Final Cross-Frontend Verification and PR Cleanup

**Files:**
- Verify only; no production file should change unless a test exposes a defect.
- Remove any temporary CI workflow used solely for remote verification before final review.

**Interfaces:**
- Consumes: Task 1 shared parser behavior and Task 2 Discord presentation behavior.
- Produces: a review-ready PR with no temporary workflow files and documented test evidence.

- [ ] **Step 1: Compile changed modules**

Run:

```bash
python -m py_compile search_items.py discord_bot.py toram_search/service.py
```

Expected: exit code 0.

- [ ] **Step 2: Run the focused cross-frontend regression gate**

Run:

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

- [ ] **Step 3: Run the complete unittest suite**

Run:

```bash
python -m unittest discover -s tests -v
```

Expected baseline: all new/related tests pass. If `test_structured_fallback.StructuredFallbackTests.test_rejected_payload_is_logged_with_reason` still fails because it expects `missing or invalid search candidates` while production reports `search payload has unexpected fields`, record it explicitly as the existing unrelated baseline failure rather than changing fallback behavior in this PR.

- [ ] **Step 4: Review final diff scope**

Confirm the final PR contains only the approved spec/plan plus relevant code/tests:

```text
docs/superpowers/specs/2026-08-09-upgrade-normalization-discord-display-name-design.md
docs/superpowers/plans/2026-08-09-upgrade-normalization-discord-display-name.md
search_items.py
discord_bot.py
tests/test_discord_followup_regressions.py
tests/test_cli_upgrade.py
tests/test_discord_bot.py
```

No `.env`, token, raw Discord guild/user IDs from real configuration, or temporary CI workflow should remain.

- [ ] **Step 5: Prepare the PR for review**

PR summary must state:

```text
- natural upgrade aliases are deterministic in both CLI and Discord
- supported aliases do not call Qwen
- subjective `best/strongest` upgrade wording remains outside scope
- Discord examples show the current guild bot display name as plain text, never a raw mention ID
- focused and full-suite verification results, including any unchanged baseline failure
```
