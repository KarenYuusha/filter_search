# Discord Bot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `discord_bot.py` as a mention-only, single-guild Discord frontend that reuses the existing Toram deterministic parser, database search, help/database-question handling, and Qwen fallback while presenting results with embeds, dropdowns, buttons, and local item images.

**Architecture:** Keep the existing search engine authoritative. Add a small frontend-neutral `toram_search/service.py` that centralizes route/fallback orchestration through injected existing parser/repository callbacks, then use it from both the CLI and Discord frontend. Discord blocking work runs with `asyncio.to_thread`; each worker operation opens its own `ItemRepository` so SQLite connections never cross threads. Discord session state is in-memory and keyed by `(guild_id, channel_id, user_id)` with a monotonically increasing generation so new tagged queries invalidate old controls and suppress late results from superseded queries.

**Tech Stack:** Python >=3.12, `discord.py>=2.6.4,<3`, SQLite, `asyncio`, existing `ollama` client, `unittest`.

## Global Constraints

- The bot processes only guild messages from exactly `DISCORD_GUILD_ID`.
- The bot processes a message only when its own user is explicitly mentioned.
- DMs, unmentioned messages, bot-authored messages, and webhook messages are ignored silently.
- Do not require the privileged Message Content intent for the mention-only design; use standard guild/guild-message intents and rely on Discord's mentioned-message content exception.
- Never hardcode `DISCORD_BOT_TOKEN`; read it from the environment.
- Reuse the existing deterministic parser, aliases, repository behavior, database/help behavior, and Qwen fallback. Do not create a second search engine in `discord_bot.py`.
- Qwen interprets supported requests but never becomes the source of database facts.
- Do not add semantic build/role interpretation such as `tank xtal` or `dps gear`.
- Never show item database IDs in Discord-visible content, component labels, attachment filenames, or error messages.
- Do not show Coryn page URLs in Discord item details.
- Show a local item image when at least one valid local image exists.
- For multiple local images, show Previous Image / Next Image controls and edit the same detail message.
- Search result pages contain at most five items and use one item dropdown plus Previous / Next buttons.
- Only the user who started a search may use its controls.
- Keep exactly one active session per `(guild_id, channel_id, user_id)`.
- A new tagged query invalidates that user's previous session in that channel immediately.
- Stale controls answer ephemerally: `This search is no longer active. Use the controls on your latest search.`
- Controls used by another user answer ephemerally: `Only the person who started this search can use these controls.`
- A newer query must suppress a late response from an older slow Qwen/SQLite worker.
- Blocking Ollama/SQLite work must not block the Discord event loop.
- Existing terminal behavior and existing tests must remain green.

---

### Task 1: Add a frontend-neutral query service

**Files:**
- Create: `toram_search/service.py`
- Modify: `search_items.py`
- Create: `tests/test_search_service.py`

**Interfaces:**
- Consumes existing `DeterministicRoute`, `ParsedSearch`, `SearchIntentRequest`, `FallbackOutcome`, `FailedQueryContext`, `QwenFallbackService`, help/database services, and repository-backed execution functions from `search_items.py` through callbacks.
- Produces `SearchService`, `ServiceOutcome`, and materialized frontend-neutral outcome dataclasses used by both CLI and Discord.

- [ ] **Step 1: Write failing service tests**

Create `tests/test_search_service.py` with fake callbacks proving the service owns route/fallback orchestration without Discord or terminal I/O:

```python
import unittest

from toram_search.service import SearchService
from toram_search.session import FailedQueryContext


class SearchServiceTests(unittest.TestCase):
    def test_deterministic_search_executes_without_qwen(self):
        calls = []
        service = SearchService(
            route_query=lambda query: FakeRoute("search", parsed="parsed"),
            execute_parsed=lambda parsed: calls.append(parsed) or FakePayload("results"),
            interpret_fallback=lambda query, context: self.fail("Qwen should not run"),
            parse_structured_request=lambda request, raw_query: None,
            answer_help=lambda topic: None,
            execute_database=lambda request: "",
        )

        outcome = service.handle_query("hp armor", FailedQueryContext(max_entries=3))

        self.assertEqual(outcome.kind, "search")
        self.assertEqual(calls, ["parsed"])

    def test_fallback_search_requests_require_confirmation(self):
        request = object()
        service = make_service_with_fallback(FakeFallback("search_requests", (request,)))

        outcome = service.handle_query("hard wording", FailedQueryContext(max_entries=3))

        self.assertEqual(outcome.kind, "confirm_search")
        self.assertEqual(outcome.search_requests, (request,))

    def test_confirmed_request_is_validated_before_execution(self):
        service = make_service(parse_structured_request=lambda request, raw: "parsed")

        outcome = service.confirm_search_request(object(), "original", FailedQueryContext(max_entries=3))

        self.assertEqual(outcome.kind, "search")
```

Use tiny local `FakeRoute`, `FakeFallback`, and `FakePayload` dataclasses in the test; do not import Discord.

- [ ] **Step 2: Run the focused service test and verify RED**

Run:

```bash
uv run python -m unittest tests.test_search_service -v
```

Expected: FAIL because `toram_search.service` does not exist.

- [ ] **Step 3: Implement service outcome types**

In `toram_search/service.py`, define compact frontend-neutral outcomes:

```python
from dataclasses import dataclass
from typing import Literal


ServiceKind = Literal[
    "search",
    "confirm_search",
    "help",
    "database",
    "refuse",
    "unavailable",
    "failed",
]


@dataclass(frozen=True)
class ServiceOutcome:
    kind: ServiceKind
    payload: object | None = None
    text: str | None = None
    search_requests: tuple[object, ...] = ()
```

Also define small materialized payload classes instead of returning terminal-rendered strings:

```python
@dataclass(frozen=True)
class ItemDetailPayload:
    detail: object

@dataclass(frozen=True)
class ItemResultsPayload:
    query: str
    results: tuple[object, ...]

@dataclass(frozen=True)
class UpgradeDetailPayload:
    graph: object
    selected_item_id: int  # internal only; never render this value in Discord

@dataclass(frozen=True)
class UpgradeResultsPayload:
    query: str
    results: tuple[object, ...]

@dataclass(frozen=True)
class StatChoicesPayload:
    parsed: object

@dataclass(frozen=True)
class StatResultsPayload:
    parsed: object
    results: tuple[object, ...]

@dataclass(frozen=True)
class ExpressionResultsPayload:
    parsed: object
    results: tuple[object, ...]
```

The payload classes are intentionally presentation-neutral. Existing domain objects may remain defined in `search_items.py` in this first refactor; do not expand this task into a repository-wide model move.

- [ ] **Step 4: Implement `SearchService` with injected existing behavior**

Use injected callables so `service.py` does not import `search_items.py` and cannot create an import cycle:

```python
class SearchService:
    def __init__(
        self,
        *,
        route_query,
        execute_parsed,
        interpret_fallback,
        parse_structured_request,
        answer_help,
        execute_database,
    ) -> None:
        ...

    def handle_query(self, query: str, context: FailedQueryContext) -> ServiceOutcome:
        ...

    def confirm_search_request(
        self,
        request: object,
        raw_query: str,
        context: FailedQueryContext,
    ) -> ServiceOutcome:
        ...
```

Map current route/fallback kinds exactly:

```text
search           -> execute_parsed -> ServiceOutcome("search")
help             -> ServiceOutcome("help")
database         -> ServiceOutcome("database")
refuse           -> ServiceOutcome("refuse")
fallback search  -> ServiceOutcome("confirm_search")
fallback help    -> ServiceOutcome("help")
fallback database-> ServiceOutcome("database")
unavailable      -> ServiceOutcome("unavailable")
invalid/failed   -> ServiceOutcome("failed")
```

Keep the existing failed-query context recording behavior when routing into fallback. Do not invent new context semantics in this task.

- [ ] **Step 5: Add a `make_search_service(...)` factory in `search_items.py`**

Use the existing repository/parser/fallback functions to satisfy the injected callbacks:

```python
def make_search_service(
    repository: ItemRepository,
    *,
    llm_client: object | None = None,
) -> SearchService:
    all_items = repository.list_items()
    help_service = HelpService()
    database_service = make_database_question_service(repository)
    client = llm_client if llm_client is not None else OllamaQwenClient()
    fallback_service = _build_fallback_service(
        repository,
        all_items,
        help_service,
        database_service,
        client,
    )
    ...
```

Add one non-interactive `execute_parsed` callback that materializes existing search domain results without calling `input()` or terminal renderers. Preserve all current parser/repository behavior.

- [ ] **Step 6: Run focused tests and verify GREEN**

Run:

```bash
uv run python -m unittest tests.test_search_service -v
```

Expected: all service tests PASS.

- [ ] **Step 7: Run existing direct structured-intent tests**

Run:

```bash
uv run python -m unittest tests.test_direct_structured_intent -v
```

Expected: PASS with no behavior changes.

- [ ] **Step 8: Commit Task 1**

```bash
git add toram_search/service.py search_items.py tests/test_search_service.py
git commit -m "refactor: add shared search service"
```

---

### Task 2: Make the CLI consume the shared service without changing presentation

**Files:**
- Modify: `search_items.py`
- Modify: `tests/test_search_service.py`
- Test existing CLI/search tests under `tests/`

**Interfaces:**
- Consumes `make_search_service()` and `SearchService.handle_query()` from Task 1.
- Produces unchanged terminal-visible behavior while removing duplicate route/fallback orchestration from `interactive_search()`.

- [ ] **Step 1: Add a regression test for CLI service delegation**

Add a focused test using a fake service to prove a deterministic parsed outcome is handed to the existing terminal screens and a Qwen confirmation still uses `_interactive_search_requests` semantics. Keep the test at the behavior boundary; do not assert implementation-private call counts unless needed.

- [ ] **Step 2: Run the focused test and verify RED**

```bash
uv run python -m unittest tests.test_search_service -v
```

Expected: the new CLI-delegation assertion FAILS because `interactive_search()` still owns the old route/fallback block.

- [ ] **Step 3: Replace only the top-level route/fallback block in `interactive_search()`**

Construct one shared service after the repository is opened:

```python
service = make_search_service(repository, llm_client=client)
```

For each query:

```python
outcome = service.handle_query(query, context)
```

Map `ServiceOutcome` back into the existing terminal screens. Keep existing result rendering, prompts, confirmation text, pagination, and command handling unchanged.

- [ ] **Step 4: Verify CLI behavior**

Run:

```bash
uv run python -m unittest tests.test_search_service tests.test_direct_structured_intent -v
```

Expected: PASS.

- [ ] **Step 5: Commit Task 2**

```bash
git add search_items.py tests/test_search_service.py
git commit -m "refactor: route CLI through shared search service"
```

---

### Task 3: Add Discord configuration, dependency, message gate, and session generations

**Files:**
- Create: `discord_bot.py`
- Create: `tests/test_discord_bot.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Produces `DiscordBotConfig`, `SessionKey`, `DiscordSearchSession`, `DiscordSessionManager`, `is_allowed_message()`, and `extract_mentioned_query()`.
- Does not connect to Discord in tests.

- [ ] **Step 1: Write failing configuration/message/session tests**

Create `tests/test_discord_bot.py` covering:

```python
class DiscordBotGateTests(unittest.TestCase):
    def test_wrong_guild_is_ignored(self): ...
    def test_dm_is_ignored(self): ...
    def test_unmentioned_message_is_ignored(self): ...
    def test_bot_message_is_ignored(self): ...
    def test_webhook_message_is_ignored(self): ...
    def test_allowed_mention_is_processed(self): ...
    def test_bot_mention_is_removed_from_query(self): ...

class DiscordSessionManagerTests(unittest.TestCase):
    def test_new_query_increments_generation_and_invalidates_old(self): ...
    def test_other_user_has_independent_session(self): ...
    def test_failed_context_is_copied_into_new_generation(self): ...
```

Use `types.SimpleNamespace` fake message/user/guild/channel objects so no Discord connection is required.

- [ ] **Step 2: Run tests and verify RED**

```bash
uv run python -m unittest tests.test_discord_bot -v
```

Expected: FAIL because `discord_bot.py` and `discord.py` dependency are absent.

- [ ] **Step 3: Add `discord.py` dependency**

Update `pyproject.toml`:

```toml
"discord.py>=2.6.4,<3",
```

Then refresh the project lockfile if the repository tracks one:

```bash
uv lock
```

- [ ] **Step 4: Implement environment configuration**

In `discord_bot.py`:

```python
@dataclass(frozen=True)
class DiscordBotConfig:
    token: str
    guild_id: int
    database_path: Path = DEFAULT_DATABASE


def load_config(environ: Mapping[str, str] = os.environ) -> DiscordBotConfig:
    token = environ.get("DISCORD_BOT_TOKEN", "").strip()
    guild_text = environ.get("DISCORD_GUILD_ID", "").strip()
    if not token:
        raise RuntimeError("DISCORD_BOT_TOKEN is required")
    if not guild_text.isdigit():
        raise RuntimeError("DISCORD_GUILD_ID must be a Discord server ID")
    return DiscordBotConfig(token=token, guild_id=int(guild_text))
```

Do not print the token in errors/logs.

- [ ] **Step 5: Implement the message gate and mention stripping**

```python
def is_allowed_message(message, *, bot_user_id: int, guild_id: int) -> bool:
    return (
        message.guild is not None
        and message.guild.id == guild_id
        and not getattr(message.author, "bot", False)
        and getattr(message, "webhook_id", None) is None
        and any(user.id == bot_user_id for user in message.mentions)
    )


def extract_mentioned_query(content: str, bot_user_id: int) -> str:
    text = re.sub(rf"<@!?{bot_user_id}>", " ", content)
    return " ".join(text.split())
```

- [ ] **Step 6: Implement generation-based session state**

```python
SessionKey = tuple[int, int, int]

@dataclass
class DiscordSearchSession:
    generation: int
    query: str
    failed_context: FailedQueryContext
    page: int = 0
    payload: object | None = None
    selected_index: int | None = None
    image_index: int = 0

class DiscordSessionManager:
    def start_query(self, key: SessionKey, query: str) -> DiscordSearchSession: ...
    def is_current(self, key: SessionKey, generation: int) -> bool: ...
    def get(self, key: SessionKey) -> DiscordSearchSession | None: ...
```

`start_query()` must copy the previous failed-query snapshot into a new `FailedQueryContext` object instead of reusing the old mutable instance. This prevents a slow superseded worker from racing the new session context.

- [ ] **Step 7: Configure minimum intents**

Create a helper:

```python
def build_intents() -> discord.Intents:
    intents = discord.Intents.none()
    intents.guilds = True
    intents.guild_messages = True
    return intents
```

Do not set `message_content=True` for the mention-only design. Discord exposes content for messages that mention the app even without the privileged Message Content intent.

- [ ] **Step 8: Run focused tests and verify GREEN**

```bash
uv run python -m unittest tests.test_discord_bot -v
```

Expected: configuration, gate, mention-stripping, and session-generation tests PASS.

- [ ] **Step 9: Commit Task 3**

```bash
git add discord_bot.py tests/test_discord_bot.py pyproject.toml uv.lock
git commit -m "feat: add Discord bot message gate and sessions"
```

If the repository has no `uv.lock`, omit it from `git add`.

---

### Task 4: Execute searches off the event loop and suppress superseded responses

**Files:**
- Modify: `discord_bot.py`
- Modify: `tests/test_discord_bot.py`

**Interfaces:**
- Consumes `make_search_service()` and `ItemRepository` from `search_items.py` plus the session manager from Task 3.
- Produces `run_query_sync()`, `run_confirmed_request_sync()`, and async handlers that use `asyncio.to_thread` safely.

- [ ] **Step 1: Write failing worker/concurrency tests**

Add tests proving:

1. one worker operation opens and closes its own repository;
2. a slow generation-1 result is not sent after generation 2 becomes current;
3. two different users can have independent current generations.

Structure the late-result test around a pure helper rather than sleeping in the test:

```python
manager = DiscordSessionManager()
first = manager.start_query(key, "old")
second = manager.start_query(key, "new")
self.assertFalse(manager.is_current(key, first.generation))
self.assertTrue(manager.is_current(key, second.generation))
```

Then test the async handler with an injected fake `to_thread` result and fake send callback.

- [ ] **Step 2: Run and verify RED**

```bash
uv run python -m unittest tests.test_discord_bot -v
```

Expected: new worker/late-result tests FAIL.

- [ ] **Step 3: Implement per-operation repository lifetime**

```python
def run_query_sync(
    database_path: Path,
    query: str,
    context: FailedQueryContext,
    *,
    llm_client_factory=OllamaQwenClient,
) -> ServiceOutcome:
    repository = ItemRepository(database_path.resolve())
    try:
        service = make_search_service(repository, llm_client=llm_client_factory())
        return service.handle_query(query, context)
    finally:
        repository.close()
```

Use the same pattern for confirmed structured requests and item-detail fetches. Never create one SQLite connection on the event-loop thread and then use it inside `asyncio.to_thread`.

- [ ] **Step 4: Implement async query handling**

```python
async def process_tagged_query(...):
    session = sessions.start_query(key, query)
    outcome = await asyncio.to_thread(
        run_query_sync,
        config.database_path,
        query,
        session.failed_context,
    )
    if not sessions.is_current(key, session.generation):
        return
    await render_service_outcome(...)
```

The generation check must occur after every potentially slow `await asyncio.to_thread(...)` and before sending/editing Discord-visible results.

- [ ] **Step 5: Verify worker tests**

```bash
uv run python -m unittest tests.test_discord_bot -v
```

Expected: PASS.

- [ ] **Step 6: Commit Task 4**

```bash
git add discord_bot.py tests/test_discord_bot.py
git commit -m "feat: run Discord searches off event loop"
```

---

### Task 5: Render paginated search results with requester-only dropdown/buttons

**Files:**
- Modify: `discord_bot.py`
- Modify: `tests/test_discord_bot.py`

**Interfaces:**
- Consumes materialized item/stat/expression/upgrade result payloads from `SearchService`.
- Produces `SearchResultsView`, `ItemSelect`, result embed builders, and shared interaction ownership/staleness checks.

- [ ] **Step 1: Write failing result-rendering tests**

Cover:

```python
def test_result_embed_never_contains_item_id(self): ...
def test_stat_page_contains_five_items_max(self): ...
def test_dropdown_contains_only_current_page_items(self): ...
def test_previous_disabled_on_first_page(self): ...
def test_next_disabled_on_last_page(self): ...
def test_other_user_is_rejected(self): ...
def test_old_generation_is_rejected_as_stale(self): ...
```

Use domain fakes with names/types/stat amounts. Assert visible labels/descriptions, not Discord internals unrelated to behavior.

- [ ] **Step 2: Run and verify RED**

```bash
uv run python -m unittest tests.test_discord_bot -v
```

Expected: result UI tests FAIL.

- [ ] **Step 3: Implement a shared session-bound view base**

```python
class SessionBoundView(discord.ui.View):
    def __init__(self, *, sessions, key, generation, owner_id, timeout=900):
        super().__init__(timeout=timeout)
        ...

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Only the person who started this search can use these controls.",
                ephemeral=True,
            )
            return False
        if not self.sessions.is_current(self.key, self.generation):
            await interaction.response.send_message(
                "This search is no longer active. Use the controls on your latest search.",
                ephemeral=True,
            )
            return False
        return True
```

- [ ] **Step 4: Implement five-item page builders**

Use existing `PAGE_SIZE == 5`. Build embeds that show names, item types where useful, matched stat/value, conditions, expression clauses, and ordering. Never include IDs.

Add a safe text helper so no embed field exceeds Discord limits:

```python
def truncate_discord_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(limit - 1, 0)].rstrip() + "…"
```

- [ ] **Step 5: Implement item dropdown and page navigation**

Use page-local indexes as select values, never database IDs:

```python
options = [
    discord.SelectOption(
        label=truncate_discord_text(item.name, 100),
        value=str(page_offset + local_index),
        description=truncate_discord_text(item.item_type, 100),
    )
    for local_index, item in enumerate(current_page)
]
```

Previous/Next edits the same Discord message and rebuilds both embed and dropdown for the new page.

- [ ] **Step 6: Verify result UI tests**

```bash
uv run python -m unittest tests.test_discord_bot -v
```

Expected: PASS.

- [ ] **Step 7: Commit Task 5**

```bash
git add discord_bot.py tests/test_discord_bot.py
git commit -m "feat: add Discord search result views"
```

---

### Task 6: Add item detail, local image carousel, and Back to Results

**Files:**
- Modify: `discord_bot.py`
- Modify: `tests/test_discord_bot.py`

**Interfaces:**
- Produces `build_item_detail_embed()`, `valid_local_image_paths()`, `ItemDetailView`, image navigation callbacks, and Back to Results.

- [ ] **Step 1: Write failing detail/image tests**

Cover:

1. detail contains item name/type/stats/sources/notes;
2. visible output does not contain database ID;
3. visible output does not contain Coryn page URL;
4. valid local image is selected;
5. missing local image is skipped;
6. `../appearance/...` database-relative image paths resolve from `database_path.parent`;
7. multiple images enable Previous Image / Next Image;
8. attachment filenames do not contain item IDs;
9. Back to Results restores the prior page.

- [ ] **Step 2: Run and verify RED**

```bash
uv run python -m unittest tests.test_discord_bot -v
```

Expected: detail/image tests FAIL.

- [ ] **Step 3: Implement image path resolution**

```python
def valid_local_image_paths(database_path: Path, images: Iterable[dict]) -> tuple[Path, ...]:
    output = []
    for image in images:
        local_path = image.get("local_path")
        if not local_path:
            continue
        path = Path(str(local_path))
        if not path.is_absolute():
            path = (database_path.parent / path).resolve()
        if path.is_file():
            output.append(path)
    return tuple(output)
```

This intentionally supports current database values such as `../appearance/...`.

- [ ] **Step 4: Build item detail embed without IDs/URLs**

Show:

```text
Item name
Item type
Stats (+ conditions)
Obtained From
Notes
Upgrade relationship names when relevant
```

Do not add `detail.summary.id` or `detail.page_url` to any visible field/footer/title. Upgrade tree/detail rendering may use IDs internally for graph traversal but must render names only.

- [ ] **Step 5: Attach the current image**

Use a user-visible filename based on a sanitized item name and image position, not ID:

```python
filename = f"{slugify_visible_name(item_name)}-{image_index + 1}{path.suffix.lower()}"
file = discord.File(path, filename=filename)
embed.set_image(url=f"attachment://{filename}")
```

On Previous/Next Image, edit the same message with a replacement attachment. `discord.py` supports editing component messages with an `attachments` list containing new `File` objects.

- [ ] **Step 6: Implement Back to Results**

Keep the original result payload/page in the active session. `Back to Results` rebuilds the page embed and result view; it does not start a new generation.

- [ ] **Step 7: Verify detail/image tests**

```bash
uv run python -m unittest tests.test_discord_bot -v
```

Expected: PASS.

- [ ] **Step 8: Commit Task 6**

```bash
git add discord_bot.py tests/test_discord_bot.py
git commit -m "feat: add Discord item details and images"
```

---

### Task 7: Add ambiguity choices, Qwen confirmation, help/database/refusal/error rendering

**Files:**
- Modify: `discord_bot.py`
- Modify: `tests/test_discord_bot.py`

**Interfaces:**
- Consumes `StatChoicesPayload`, `ServiceOutcome("confirm_search")`, help/database/refusal/unavailable/failed outcomes.
- Produces clarification/confirmation views and compact informational embeds/messages.

- [ ] **Step 1: Write failing interaction-flow tests**

Cover:

```python
def test_crit_choices_use_buttons_and_same_generation(self): ...
def test_qwen_interpretation_requires_search_confirmation(self): ...
def test_confirm_search_runs_selected_structured_request(self): ...
def test_cancel_does_not_execute_search(self): ...
def test_help_renders_examples(self): ...
def test_database_answer_renders_compactly(self): ...
def test_build_request_is_refused(self): ...
def test_qwen_unavailable_message_is_safe(self): ...
def test_failed_interpretation_has_search_examples(self): ...
```

Also assert that Qwen/database exception details are not included in user-facing output.

- [ ] **Step 2: Run and verify RED**

```bash
uv run python -m unittest tests.test_discord_bot -v
```

Expected: interaction-flow tests FAIL.

- [ ] **Step 3: Implement deterministic stat-choice view**

Build one button per existing candidate and a Cancel button. Choice callbacks stay within the current generation and call the existing deterministic parser/search path; do not ask Qwen to choose between known ambiguity candidates.

- [ ] **Step 4: Implement Qwen confirmation view**

Render each validated `SearchIntentRequest` using the existing structured request formatting semantics, adapted for Discord-visible text. For one interpretation:

```text
I interpreted this as:
Item type: Bow
Stats: MaxHP AND Critical Rate

[ Search ] [ Cancel ]
```

For multiple interpretations, use a select menu rather than creating many buttons. Selecting/confirming executes `SearchService.confirm_search_request()` off the event loop using the same worker/repository pattern from Task 4.

- [ ] **Step 5: Implement help/database/refusal/error renderers**

Use compact embeds/text. Required user-facing messages include:

```text
I couldn't interpret that automatically right now. Explicit item/stat searches are still available.
```

and:

```text
I couldn't convert that into a supported item/stat search.
Try:
- @ToramBot hp armor
- @ToramBot cr bow
- @ToramBot hp > 5000 and cr bow
```

Never include tracebacks, filesystem paths, SQLite errors, raw Ollama errors, item IDs, or Coryn page URLs.

- [ ] **Step 6: Verify interaction-flow tests**

```bash
uv run python -m unittest tests.test_discord_bot -v
```

Expected: PASS.

- [ ] **Step 7: Commit Task 7**

```bash
git add discord_bot.py tests/test_discord_bot.py
git commit -m "feat: add Discord clarification and fallback flows"
```

---

### Task 8: Wire the Discord client, startup, logging, and real message replies

**Files:**
- Modify: `discord_bot.py`
- Modify: `tests/test_discord_bot.py`
- Modify: `README.md` if it contains run/setup instructions; otherwise create a concise Discord section in the existing project documentation rather than adding a new top-level doc.

**Interfaces:**
- Produces `create_client(config)`, `main()`, `on_ready`, and `on_message` wiring.

- [ ] **Step 1: Write failing client wiring tests**

Use an injected/fake client factory and fake message channel to prove:

1. an allowed tagged message calls `process_tagged_query`;
2. ignored messages send nothing;
3. an empty query after the mention renders help or a concise prompt rather than searching an empty string;
4. replies suppress accidental mentions via `discord.AllowedMentions.none()`.

- [ ] **Step 2: Run and verify RED**

```bash
uv run python -m unittest tests.test_discord_bot -v
```

Expected: client wiring tests FAIL.

- [ ] **Step 3: Create the Discord client**

```python
def create_client(config: DiscordBotConfig) -> discord.Client:
    client = discord.Client(
        intents=build_intents(),
        allowed_mentions=discord.AllowedMentions.none(),
    )
    ...
    return client
```

Inside `on_message`, obtain `client.user.id`, apply `is_allowed_message`, strip the mention, compute the session key from guild/channel/author IDs, and call `process_tagged_query`.

- [ ] **Step 4: Add startup/error logging**

Log the connected bot username and allowed guild ID, but never the token. Catch unexpected query/render exceptions at the Discord boundary, log the traceback to the process logger, and send only the safe generic database/search error to the active requester if that generation is still current.

- [ ] **Step 5: Add `main()`**

```python
def main() -> None:
    config = load_config()
    client = create_client(config)
    client.run(config.token, log_handler=None)


if __name__ == "__main__":
    main()
```

If project logging is configured explicitly, use that instead of duplicate discord.py logging handlers.

- [ ] **Step 6: Document Discord application setup**

Document:

```text
DISCORD_BOT_TOKEN=<token>
DISCORD_GUILD_ID=<server id>
OLLAMA_MODEL=qwen3.5:2b   # optional existing behavior
```

Document bot permissions: View Channels, Send Messages, Embed Links, Attach Files. Explain that the implementation intentionally listens only to messages that explicitly mention the bot. Note that Discord exposes content for messages mentioning the app without requiring the privileged Message Content intent; do not instruct users to enable unnecessary privileged intents.

Run command:

```bash
uv run python discord_bot.py
```

- [ ] **Step 7: Verify client wiring tests**

```bash
uv run python -m unittest tests.test_discord_bot -v
```

Expected: PASS.

- [ ] **Step 8: Commit Task 8**

```bash
git add discord_bot.py tests/test_discord_bot.py README.md
git commit -m "feat: wire Discord bot startup"
```

Only include `README.md` if it changed.

---

### Task 9: Full regression verification and branch review

**Files:**
- Review all files changed by Tasks 1-8.

**Interfaces:**
- Produces verified implementation ready for PR review; does not merge automatically.

- [ ] **Step 1: Run the full repository test suite**

```bash
uv run python -m unittest discover -s tests -v
```

Expected: exit code 0 with zero failures/errors.

- [ ] **Step 2: Run focused Discord/service tests again**

```bash
uv run python -m unittest tests.test_search_service tests.test_discord_bot -v
```

Expected: exit code 0.

- [ ] **Step 3: Run a syntax/import smoke check**

```bash
uv run python -m py_compile discord_bot.py toram_search/service.py search_items.py
```

Expected: exit code 0.

- [ ] **Step 4: Perform a local manual smoke test with the configured guild**

Run:

```bash
uv run python discord_bot.py
```

Verify these exact cases in the allowed server:

```text
normal unmentioned message
-> bot stays silent

@Bot can you find armor with hp
-> MaxHP Armor result embed; no Qwen needed

@Bot which bow has the highest critical rate
-> Critical Rate Bow result embed, highest first

@Bot crit bow
-> Critical Rate / Critical Damage clarification controls

@Bot item <known item with multiple images>
-> detail with first image and Previous/Next Image controls

@Bot hp armor
then immediately @Bot cr bow
-> old controls become stale; only new query remains active

another user presses first user's controls
-> ephemeral ownership warning
```

Inspect the Discord messages and confirm no visible database IDs and no Coryn page URL.

- [ ] **Step 5: Verify Qwen 2B fallback separately**

Use one supported request that deterministic normalization does not handle but Qwen can interpret. Confirm it produces the interpretation confirmation UI, then Search executes deterministic validated results. Confirm a Qwen outage produces the safe unavailable message without crashing the Discord client.

- [ ] **Step 6: Compare branch against `main`**

```bash
git diff --stat main...HEAD
git diff main...HEAD -- discord_bot.py toram_search/service.py search_items.py pyproject.toml tests/test_discord_bot.py tests/test_search_service.py
```

Review for:

- duplicated search semantics in `discord_bot.py`
- accidental item ID rendering
- accidental Coryn URL rendering
- token logging
- `message_content=True`
- SQLite connections crossing worker threads
- component callbacks missing ownership/generation checks
- old worker responses not guarded by `is_current`

- [ ] **Step 7: Open a PR without merging**

PR summary must state:

```text
- mention-only, one-guild Discord frontend
- shared deterministic/Qwen search service
- embeds, five-item pagination, dropdown item selection
- requester-only controls and stale-generation handling
- local image carousel
- no item IDs / no Coryn page URLs
- off-event-loop search execution with per-worker SQLite connections
- full test-suite result
- manual guild smoke-test result
```

Do not merge without explicit user instruction.
