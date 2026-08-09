# Discord Bot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `discord_bot.py` as a mention-only, single-guild Discord frontend that reuses the existing Toram deterministic parser, database search, help/database-question handling, and Qwen fallback while presenting results with embeds, dropdowns, buttons, and local item images.

**Architecture:** Keep the existing search engine authoritative. Add `toram_search/service.py` for frontend-neutral route/fallback orchestration and payloads, plus one pure non-interactive expression-resolution path so Discord never calls terminal `input()`. Discord blocking work runs through `asyncio.to_thread`; each worker operation opens its own `ItemRepository`, preventing SQLite connections from crossing threads. Discord session state is in memory and keyed by `(guild_id, channel_id, user_id)` with a monotonically increasing generation so a new tagged query invalidates old controls and suppresses late responses from superseded work.

**Tech Stack:** Python >=3.12, `discord.py>=2.6.4,<3`, SQLite, `asyncio`, existing `ollama` client, `unittest`.

## Global Constraints

- Process only guild messages from exactly `DISCORD_GUILD_ID`.
- Process a message only when the bot user is explicitly mentioned.
- Ignore DMs, unmentioned messages, bot-authored messages, and webhook messages silently.
- Do not enable the privileged Message Content intent for this mention-only design. Use standard guild and guild-message intents; Discord exposes content for messages that mention the app.
- Never hardcode or log `DISCORD_BOT_TOKEN`.
- Reuse existing parser, aliases, repository behavior, database/help behavior, and Qwen fallback. Do not create a second search engine in `discord_bot.py`.
- Qwen may interpret supported requests but must never supply database facts.
- Do not add semantic build/role interpretation such as `tank xtal` or `dps gear`.
- Never show item database IDs in Discord-visible content, component labels, dropdown descriptions, attachment filenames, or user-facing errors.
- Do not show Coryn page URLs in Discord item details.
- Show a local item image when at least one valid local image exists.
- When multiple images exist, show Previous Image / Next Image and edit the same detail message.
- Search result pages contain at most five items and use one item dropdown plus Previous / Next buttons.
- Only the user who started a search may use its controls.
- Keep exactly one active session per `(guild_id, channel_id, user_id)`.
- A new tagged query invalidates that user's previous session in that channel immediately.
- Stale controls answer ephemerally: `This search is no longer active. Use the controls on your latest search.`
- Controls used by another user answer ephemerally: `Only the person who started this search can use these controls.`
- A newer query suppresses any late response from an older slow Qwen/SQLite worker.
- Blocking Ollama/SQLite work must not block the Discord event loop.
- Existing terminal behavior and existing tests must remain green.

---

### Task 1: Add pure expression resolution for non-terminal frontends

**Files:**
- Modify: `search_items.py`
- Create: `tests/test_noninteractive_resolution.py`

**Interfaces:**
- Produces `StatClarification`, `StatResolutionChoices`, and `resolve_expression_noninteractively(parsed, repository, choices)`.
- Existing `resolve_expression_interactively()` remains public and becomes a terminal wrapper over the new pure resolver.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_noninteractive_resolution.py` with the same fake repository shape already used by `tests/test_direct_structured_intent.py`:

```python
import unittest

from search_items import (
    StatClarification,
    parse_expression_request,
    resolve_expression_noninteractively,
)


class FakeRepository:
    def list_item_types(self):
        return {"Bow", "Armor"}

    def list_stat_names(self):
        return ["Critical Rate", "Critical Damage", "MaxHP"]


class NoninteractiveResolutionTests(unittest.TestCase):
    def setUp(self):
        self.repository = FakeRepository()

    def test_exact_alias_resolves_without_prompt(self):
        parsed = parse_expression_request("hp armor", self.repository)
        outcome = resolve_expression_noninteractively(parsed, self.repository, {})
        self.assertEqual(outcome.intent, "stat_expression")
        self.assertEqual(outcome.resolved_expression.groups[0].clauses[0].stat_name, "MaxHP")

    def test_ambiguous_stat_returns_clarification(self):
        parsed = parse_expression_request("crit bow", self.repository)
        outcome = resolve_expression_noninteractively(parsed, self.repository, {})
        self.assertIsInstance(outcome, StatClarification)
        self.assertEqual(outcome.typed_stat, "crit")
        self.assertEqual(outcome.candidates, ("Critical Rate", "Critical Damage"))

    def test_ambiguous_choice_completes_resolution(self):
        parsed = parse_expression_request("crit bow", self.repository)
        first = resolve_expression_noninteractively(parsed, self.repository, {})
        key = (first.group_index, first.clause_index)
        resolved = resolve_expression_noninteractively(
            parsed,
            self.repository,
            {key: "Critical Rate"},
        )
        self.assertEqual(
            resolved.resolved_expression.groups[0].clauses[0].stat_name,
            "Critical Rate",
        )
```

Also add one fuzzy-match case proving the pure resolver returns a confirmation-style `StatClarification` instead of calling `input()`.

- [ ] **Step 2: Run the focused test and verify RED**

```bash
uv run python -m unittest tests.test_noninteractive_resolution -v
```

Expected: import failure because the new types/functions do not exist.

- [ ] **Step 3: Add the frontend-neutral clarification types**

In `search_items.py` near `ParsedSearch`, add:

```python
ClarificationMode = Literal["choose", "confirm"]
ResolutionChoiceKey = tuple[int, int]
StatResolutionChoices = dict[ResolutionChoiceKey, str]


@dataclass(frozen=True)
class StatClarification:
    group_index: int
    clause_index: int
    typed_stat: str
    candidates: tuple[str, ...]
    display_labels: tuple[str, ...]
    mode: ClarificationMode
```

- [ ] **Step 4: Implement `resolve_expression_noninteractively`**

Implement the same clause traversal currently inside `resolve_expression_interactively`, but replace terminal prompts with returned state:

```python
def resolve_expression_noninteractively(
    parsed: ParsedSearch,
    repository: ItemRepository,
    choices: Mapping[ResolutionChoiceKey, str],
) -> ParsedSearch | StatClarification | None:
    if parsed.error or parsed.parsed_expression is None:
        return None
    if parsed.resolved_expression is not None:
        return parsed

    available_stats = repository.list_stat_names()
    resolved_groups: list[ResolvedAndGroup] = []

    for group_index, group in enumerate(parsed.parsed_expression.groups):
        resolved_clauses: list[ResolvedClause] = []
        for clause_index, clause in enumerate(group.clauses):
            typed = SEARCH_ONLY_STAT_ALIASES.get(
                normalize_stat_text(clause.typed_stat),
                clause.typed_stat,
            )
            resolution = resolve_stat_term(typed, available_stats, allow_fuzzy=True)
            key = (group_index, clause_index)

            if resolution.status in {"exact", "alias"}:
                stat_name = resolution.candidates[0]
            elif resolution.status == "ambiguous":
                selected = choices.get(key)
                if selected is None:
                    return StatClarification(
                        group_index,
                        clause_index,
                        clause.typed_stat,
                        resolution.candidates,
                        resolution.display_labels,
                        "choose",
                    )
                if selected not in resolution.candidates:
                    return None
                stat_name = selected
            elif resolution.status == "fuzzy":
                selected = choices.get(key)
                if selected is None:
                    return StatClarification(
                        group_index,
                        clause_index,
                        clause.typed_stat,
                        resolution.candidates[:1],
                        resolution.display_labels[:1],
                        "confirm",
                    )
                if selected != resolution.candidates[0]:
                    return None
                stat_name = selected
            else:
                return None

            resolved_clauses.append(
                ResolvedClause(
                    typed_stat=clause.typed_stat,
                    stat_name=stat_name,
                    operator=clause.operator,
                    value=clause.value,
                )
            )
        resolved_groups.append(ResolvedAndGroup(tuple(resolved_clauses)))

    return replace(
        parsed,
        resolved_expression=ResolvedStatExpression(tuple(resolved_groups)),
    )
```

- [ ] **Step 5: Refactor the existing terminal resolver to use the pure resolver**

Keep the current prompts and wording. The loop should call the pure function with an initially empty `choices` mapping, prompt only when it receives `StatClarification`, add the selected candidate to `choices`, and call the pure function again. Cancel/no behavior remains unchanged.

- [ ] **Step 6: Verify focused and existing parser tests**

```bash
uv run python -m unittest tests.test_noninteractive_resolution tests.test_direct_structured_intent -v
```

Expected: exit code 0.

- [ ] **Step 7: Commit Task 1**

```bash
git add search_items.py tests/test_noninteractive_resolution.py
git commit -m "refactor: add noninteractive stat resolution"
```

---

### Task 2: Add a shared query service and route the CLI through it

**Files:**
- Create: `toram_search/service.py`
- Modify: `search_items.py`
- Create: `tests/test_search_service.py`

**Interfaces:**
- Produces `SearchService`, `ServiceOutcome`, and payload dataclasses.
- `SearchService` is constructed with callbacks so `toram_search/service.py` never imports `search_items.py`, avoiding an import cycle.
- `make_search_service(repository, llm_client=None)` in `search_items.py` supplies the existing parser/repository behavior.

- [ ] **Step 1: Write the failing service tests**

Create `tests/test_search_service.py`:

```python
import unittest
from dataclasses import dataclass

from toram_search.service import SearchService
from toram_search.session import FailedQueryContext


@dataclass(frozen=True)
class FakeRoute:
    kind: str
    parsed: object | None = None
    help_text: str | None = None
    database_request: object | None = None
    record_failure: bool = False


@dataclass(frozen=True)
class FakeFallback:
    kind: str
    search_requests: tuple[object, ...] = ()
    database_request: object | None = None
    help_topic: str | None = None


class SearchServiceTests(unittest.TestCase):
    def test_deterministic_search_never_calls_qwen(self):
        qwen_calls = []
        service = SearchService(
            route_query=lambda query: FakeRoute("search", parsed="parsed"),
            execute_parsed=lambda parsed: ("materialized", parsed),
            interpret_fallback=lambda query, context: qwen_calls.append(query),
            parse_structured_request=lambda request, raw_query: None,
            answer_help=lambda topic: None,
            execute_database=lambda request: "",
        )
        outcome = service.handle_query("hp armor", FailedQueryContext(max_entries=3))
        self.assertEqual(outcome.kind, "search")
        self.assertEqual(outcome.payload, ("materialized", "parsed"))
        self.assertEqual(qwen_calls, [])

    def test_fallback_search_requests_require_confirmation(self):
        request = object()
        service = SearchService(
            route_query=lambda query: FakeRoute("fallback"),
            execute_parsed=lambda parsed: parsed,
            interpret_fallback=lambda query, context: FakeFallback(
                "search_requests",
                (request,),
            ),
            parse_structured_request=lambda request, raw_query: None,
            answer_help=lambda topic: None,
            execute_database=lambda request: "",
        )
        outcome = service.handle_query("hard wording", FailedQueryContext(max_entries=3))
        self.assertEqual(outcome.kind, "confirm_search")
        self.assertEqual(outcome.search_requests, (request,))
```

Add concrete tests for help, database, refusal, unavailable, failed fallback, and `confirm_search_request` rejecting `None` from the structured-request validator.

- [ ] **Step 2: Run the focused service test and verify RED**

```bash
uv run python -m unittest tests.test_search_service -v
```

Expected: import failure because `toram_search.service` does not exist.

- [ ] **Step 3: Implement service outcome types**

Create `toram_search/service.py` with:

```python
from dataclasses import dataclass
from typing import Literal

from .session import FailedQueryContext


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

Add presentation-neutral payloads:

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
    selected_item_id: int

@dataclass(frozen=True)
class UpgradeResultsPayload:
    query: str
    results: tuple[object, ...]

@dataclass(frozen=True)
class GuidedStatPayload:
    message: str

@dataclass(frozen=True)
class StatClarificationPayload:
    parsed: object
    clarification: object
    choices: tuple[tuple[tuple[int, int], str], ...]

@dataclass(frozen=True)
class StatResultsPayload:
    parsed: object
    results: tuple[object, ...]

@dataclass(frozen=True)
class ExpressionResultsPayload:
    parsed: object
    results: tuple[object, ...]
```

`selected_item_id` is internal traversal state only and must never be rendered in Discord.

- [ ] **Step 4: Implement `SearchService` route/fallback orchestration**

The constructor accepts these callbacks:

```text
route_query(query) -> current DeterministicRoute
execute_parsed(parsed) -> one materialized payload
interpret_fallback(query, failed_attempts) -> current FallbackOutcome
parse_structured_request(request, raw_query) -> ParsedSearch | None
answer_help(topic) -> str | None
execute_database(request) -> str
```

`handle_query()` performs this exact mapping:

```python
def handle_query(self, query: str, context: FailedQueryContext) -> ServiceOutcome:
    route = self.route_query(query)
    if route.kind == "search":
        return ServiceOutcome("search", payload=self.execute_parsed(route.parsed))
    if route.kind == "help":
        return ServiceOutcome("help", text=route.help_text or "")
    if route.kind == "database":
        return ServiceOutcome("database", text=self.execute_database(route.database_request))
    if route.kind == "refuse":
        return ServiceOutcome("refuse")

    if route.record_failure:
        context.record_failure(query)
    fallback = self.interpret_fallback(query, context.snapshot())

    if fallback.kind == "search_requests" and fallback.search_requests:
        return ServiceOutcome("confirm_search", search_requests=fallback.search_requests)
    if fallback.kind == "database_action" and fallback.database_request is not None:
        return ServiceOutcome(
            "database",
            text=self.execute_database(fallback.database_request),
        )
    if fallback.kind == "help" and fallback.help_topic is not None:
        text = self.answer_help(fallback.help_topic)
        return ServiceOutcome("help", text=text) if text is not None else ServiceOutcome("failed")
    if fallback.kind == "refuse":
        return ServiceOutcome("refuse")
    if fallback.kind == "unavailable":
        return ServiceOutcome("unavailable")
    return ServiceOutcome("failed")
```

`confirm_search_request()` validates through the injected structured parser, returns `failed` when parsing returns `None`, and otherwise returns `search` with `execute_parsed(parsed)`.

- [ ] **Step 5: Add `make_search_service()` and a non-interactive execution adapter in `search_items.py`**

The execution adapter maps the existing `ParsedSearch.intent` values exactly:

```text
exact_item      -> repository.get_item -> ItemDetailPayload
item_search     -> exact-name check/rank_items -> ItemDetailPayload or ItemResultsPayload
exact_upgrade   -> repository.get_upgrade_component -> UpgradeDetailPayload
upgrade_search  -> rank crysta items -> UpgradeResultsPayload
guided_stat     -> GuidedStatPayload with "Include a stat after 'stat'."
stat_choices    -> StatClarificationPayload using existing stat choices
stat_search     -> repository.search_by_stat -> StatResultsPayload
stat_expression -> resolve_expression_noninteractively; return StatClarificationPayload until resolved, then repository.search_by_expression -> ExpressionResultsPayload
```

Do not duplicate SQL or stat-resolution rules in `service.py`.

- [ ] **Step 6: Route the CLI through `SearchService.handle_query()`**

Replace only the top-level deterministic/fallback branch selection in `interactive_search()`. Keep the existing terminal renderers, prompts, pagination, and item-selection screens. When the service returns a `StatClarificationPayload`, the CLI may continue using the existing terminal prompt wrapper from Task 1.

- [ ] **Step 7: Verify shared-service and CLI behavior**

```bash
uv run python -m unittest \
  tests.test_search_service \
  tests.test_noninteractive_resolution \
  tests.test_direct_structured_intent -v
```

Expected: exit code 0.

- [ ] **Step 8: Commit Task 2**

```bash
git add toram_search/service.py search_items.py tests/test_search_service.py
git commit -m "refactor: add shared search service"
```

---

### Task 3: Add Discord dependency, configuration, message gate, and session generations

**Files:**
- Create: `discord_bot.py`
- Create: `tests/test_discord_bot.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock` if present

**Interfaces:**
- Produces `DiscordBotConfig`, `SessionKey`, `DiscordSearchSession`, `DiscordSessionManager`, `is_allowed_message()`, `extract_mentioned_query()`, and `build_intents()`.

- [ ] **Step 1: Write the failing gate/session tests**

Create `tests/test_discord_bot.py` using `types.SimpleNamespace` fakes:

```python
import unittest
from types import SimpleNamespace

from discord_bot import (
    DiscordSessionManager,
    extract_mentioned_query,
    is_allowed_message,
)


class DiscordBotGateTests(unittest.TestCase):
    def make_message(
        self,
        *,
        guild_id=10,
        author_id=20,
        author_bot=False,
        webhook_id=None,
        mentions=(99,),
    ):
        guild = None if guild_id is None else SimpleNamespace(id=guild_id)
        author = SimpleNamespace(id=author_id, bot=author_bot)
        mentioned_users = [SimpleNamespace(id=value) for value in mentions]
        return SimpleNamespace(
            guild=guild,
            author=author,
            webhook_id=webhook_id,
            mentions=mentioned_users,
        )

    def test_wrong_guild_is_ignored(self):
        message = self.make_message(guild_id=11)
        self.assertFalse(is_allowed_message(message, bot_user_id=99, guild_id=10))

    def test_dm_is_ignored(self):
        message = self.make_message(guild_id=None)
        self.assertFalse(is_allowed_message(message, bot_user_id=99, guild_id=10))

    def test_unmentioned_message_is_ignored(self):
        message = self.make_message(mentions=())
        self.assertFalse(is_allowed_message(message, bot_user_id=99, guild_id=10))

    def test_bot_and_webhook_messages_are_ignored(self):
        bot_message = self.make_message(author_bot=True)
        webhook_message = self.make_message(webhook_id=123)
        self.assertFalse(is_allowed_message(bot_message, bot_user_id=99, guild_id=10))
        self.assertFalse(is_allowed_message(webhook_message, bot_user_id=99, guild_id=10))

    def test_mention_is_removed_from_query(self):
        self.assertEqual(
            extract_mentioned_query("<@99> can you find armor with hp", 99),
            "can you find armor with hp",
        )
```

Add session tests proving generation increments for the same key and remains independent for a second user's key.

- [ ] **Step 2: Run and verify RED**

```bash
uv run python -m unittest tests.test_discord_bot -v
```

Expected: import failure because `discord_bot.py` does not exist.

- [ ] **Step 3: Add `discord.py`**

Update `pyproject.toml` dependencies with:

```toml
"discord.py>=2.6.4,<3",
```

If `uv.lock` exists, run:

```bash
uv lock
```

- [ ] **Step 4: Implement environment configuration**

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
    cleaned = re.sub(rf"<@!?{bot_user_id}>", " ", content)
    return " ".join(cleaned.split())
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
    resolution_choices: dict[tuple[int, int], str] = field(default_factory=dict)
    selected_index: int | None = None
    image_index: int = 0


class DiscordSessionManager:
    def __init__(self) -> None:
        self._sessions: dict[SessionKey, DiscordSearchSession] = {}

    def start_query(self, key: SessionKey, query: str) -> DiscordSearchSession:
        previous = self._sessions.get(key)
        generation = 1 if previous is None else previous.generation + 1
        context = FailedQueryContext(max_entries=3)
        if previous is not None:
            for attempt in previous.failed_context.snapshot():
                context.record_failure(attempt.original_query)
                if attempt.suggested_query:
                    context.set_latest_suggestion(attempt.suggested_query)
        session = DiscordSearchSession(generation, query, context)
        self._sessions[key] = session
        return session

    def get(self, key: SessionKey) -> DiscordSearchSession | None:
        return self._sessions.get(key)

    def is_current(self, key: SessionKey, generation: int) -> bool:
        session = self._sessions.get(key)
        return session is not None and session.generation == generation
```

This preserves both the prior failed query and the latest deterministic/Qwen suggestion without sharing the old mutable context object with a superseded worker.

- [ ] **Step 7: Use minimum intents**

```python
def build_intents() -> discord.Intents:
    intents = discord.Intents.none()
    intents.guilds = True
    intents.guild_messages = True
    return intents
```

Do not set `message_content=True`.

- [ ] **Step 8: Verify and commit Task 3**

```bash
uv run python -m unittest tests.test_discord_bot -v
git add discord_bot.py tests/test_discord_bot.py pyproject.toml
test ! -f uv.lock || git add uv.lock
git commit -m "feat: add Discord bot message gate and sessions"
```

---

### Task 4: Execute queries off the event loop and suppress superseded results

**Files:**
- Modify: `discord_bot.py`
- Modify: `tests/test_discord_bot.py`

**Interfaces:**
- Produces `run_query_sync()`, `run_confirmed_request_sync()`, `run_materialization_sync()`, and `process_tagged_query()`.
- Every synchronous worker opens and closes its own `ItemRepository`.

- [ ] **Step 1: Write the failing worker-generation test**

Add an async test using `unittest.IsolatedAsyncioTestCase`:

```python
class DiscordWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_late_old_result_is_not_sent(self):
        sessions = DiscordSessionManager()
        key = (10, 30, 20)
        first = sessions.start_query(key, "old query")
        second = sessions.start_query(key, "new query")
        sent = []

        async def fake_send(value):
            sent.append(value)

        await send_if_current(
            sessions,
            key,
            first.generation,
            "old result",
            fake_send,
        )
        await send_if_current(
            sessions,
            key,
            second.generation,
            "new result",
            fake_send,
        )
        self.assertEqual(sent, ["new result"])
```

Add a repository-factory fake proving `close()` is called in a `finally` block.

- [ ] **Step 2: Run and verify RED**

```bash
uv run python -m unittest tests.test_discord_bot -v
```

Expected: failure because worker helpers do not exist.

- [ ] **Step 3: Implement per-operation repository lifetime**

```python
def run_query_sync(
    database_path: Path,
    query: str,
    context: FailedQueryContext,
    *,
    repository_factory=ItemRepository,
    llm_client_factory=OllamaQwenClient,
) -> ServiceOutcome:
    repository = repository_factory(database_path.resolve())
    try:
        service = make_search_service(repository, llm_client=llm_client_factory())
        return service.handle_query(query, context)
    finally:
        repository.close()
```

Implement the confirmed-request worker using the same pattern and `SearchService.confirm_search_request()`.

- [ ] **Step 4: Implement current-generation guarding**

```python
async def send_if_current(
    sessions: DiscordSessionManager,
    key: SessionKey,
    generation: int,
    value: object,
    send_callback,
) -> None:
    if sessions.is_current(key, generation):
        await send_callback(value)
```

- [ ] **Step 5: Implement `process_tagged_query`**

```python
async def process_tagged_query(
    message,
    *,
    bot_user_id: int,
    config: DiscordBotConfig,
    sessions: DiscordSessionManager,
) -> None:
    query = extract_mentioned_query(message.content, bot_user_id)
    key = (message.guild.id, message.channel.id, message.author.id)
    session = sessions.start_query(key, query)

    outcome = await asyncio.to_thread(
        run_query_sync,
        config.database_path,
        query,
        session.failed_context,
    )
    if not sessions.is_current(key, session.generation):
        return
    session.payload = outcome.payload
    await render_service_outcome(message, outcome, key, session, sessions, config)
```

`on_message` in Task 8 passes `client.user.id` explicitly. No helper in `discord_bot.py` may depend on discord.py private state.

- [ ] **Step 6: Verify and commit Task 4**

```bash
uv run python -m unittest tests.test_discord_bot -v
git add discord_bot.py tests/test_discord_bot.py
git commit -m "feat: run Discord searches off event loop"
```

---

### Task 5: Add paginated result embeds, dropdown selection, and ownership checks

**Files:**
- Modify: `discord_bot.py`
- Modify: `tests/test_discord_bot.py`

**Interfaces:**
- Produces `SessionBoundView`, `SearchResultsView`, `ItemSelect`, and result embed builders.

- [ ] **Step 1: Write failing result UI tests**

Add concrete tests that build a six-item fake result payload and assert:

```python
embed, view = build_search_results_message(payload, page=0, session_context=context)
self.assertNotIn("ID", embed.description or "")
self.assertEqual(len(view.item_select.options), 5)
self.assertTrue(view.previous_button.disabled)
self.assertFalse(view.next_button.disabled)
```

Build page 1 and assert the dropdown contains only the sixth item and Next is disabled. Add interaction-check tests for wrong owner and stale generation using fake interactions that record `ephemeral=True` responses.

- [ ] **Step 2: Run and verify RED**

```bash
uv run python -m unittest tests.test_discord_bot -v
```

Expected: result UI tests fail.

- [ ] **Step 3: Implement the shared view guard**

```python
class SessionBoundView(discord.ui.View):
    def __init__(self, *, sessions, key, generation, owner_id, timeout=900):
        super().__init__(timeout=timeout)
        self.sessions = sessions
        self.key = key
        self.generation = generation
        self.owner_id = owner_id

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

- [ ] **Step 4: Add safe Discord text truncation**

```python
def truncate_discord_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit <= 1:
        return "…"[:limit]
    return text[: limit - 1].rstrip() + "…"
```

Use 100 characters for select labels/descriptions, 1024 per embed field, and keep the full embed under Discord's total embed-text limit.

- [ ] **Step 5: Build five-item result pages**

Render item names, item types where useful, matched stat/value, conditions, expression clauses, and ordering. Do not include IDs or Coryn URLs.

Select values use page-global result indexes, not database IDs:

```python
option = discord.SelectOption(
    label=truncate_discord_text(item.name, 100),
    value=str(result_index),
    description=truncate_discord_text(item.item_type, 100),
)
```

- [ ] **Step 6: Implement Previous / Next / dropdown callbacks**

Previous and Next update `session.page` and edit the same message. Dropdown selection stores the selected result index in the session and moves into the item-detail flow from Task 6.

- [ ] **Step 7: Verify and commit Task 5**

```bash
uv run python -m unittest tests.test_discord_bot -v
git add discord_bot.py tests/test_discord_bot.py
git commit -m "feat: add Discord search result views"
```

---

### Task 6: Add item detail embeds, local image carousel, and Back to Results

**Files:**
- Modify: `discord_bot.py`
- Modify: `tests/test_discord_bot.py`

**Interfaces:**
- Produces `valid_local_image_paths()`, `build_item_detail_embed()`, `ItemDetailView`, image navigation, and Back to Results.

- [ ] **Step 1: Write failing detail/image tests**

Create a temporary database directory and image files. Assert:

```python
paths = valid_local_image_paths(
    database_path,
    [
        {"local_path": "../appearance/armor/sample/item_01.jpg"},
        {"local_path": "../appearance/armor/sample/missing.jpg"},
    ],
)
self.assertEqual(paths, (existing_image.resolve(),))
```

Build an item-detail embed and assert the visible text contains the name/type/stats but does not contain `detail.summary.id` as text and does not contain `detail.page_url`. Add a two-image view test asserting both image navigation buttons are enabled and Back to Results exists when the item came from a result page.

- [ ] **Step 2: Run and verify RED**

```bash
uv run python -m unittest tests.test_discord_bot -v
```

Expected: detail/image tests fail.

- [ ] **Step 3: Implement local image resolution**

```python
def valid_local_image_paths(
    database_path: Path,
    images: Iterable[dict[str, object]],
) -> tuple[Path, ...]:
    output: list[Path] = []
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

- [ ] **Step 4: Build item detail without IDs or Coryn URL**

Display:

```text
item name
item type
stats and conditions
Obtained From
notes
upgrade relationship names when available
```

Do not add `summary.id`, `page_url`, or source IDs to visible fields. Upgrade graph traversal may use IDs internally but renders names only.

- [ ] **Step 5: Attach the current image with a non-ID filename**

```python
def visible_attachment_name(item_name: str, image_index: int, suffix: str) -> str:
    stem = re.sub(r"[^a-z0-9]+", "-", item_name.casefold()).strip("-") or "item"
    safe_suffix = suffix.lower() if suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"} else ".jpg"
    return f"{stem}-{image_index + 1}{safe_suffix}"
```

Use `discord.File(path, filename=name)` and `embed.set_image(url=f"attachment://{name}")`.

- [ ] **Step 6: Implement image navigation**

Previous/Next Image changes only `session.image_index`. Edit the same component message with a replacement `discord.File` in the `attachments` argument and rebuild the embed image URL for the new filename.

- [ ] **Step 7: Implement Back to Results**

Keep the result payload and `session.page` while viewing details. Back to Results resets `selected_index` and `image_index`, rebuilds the current result page, and does not increment the session generation.

- [ ] **Step 8: Verify and commit Task 6**

```bash
uv run python -m unittest tests.test_discord_bot -v
git add discord_bot.py tests/test_discord_bot.py
git commit -m "feat: add Discord item details and images"
```

---

### Task 7: Add stat clarification and Qwen interpretation confirmation

**Files:**
- Modify: `discord_bot.py`
- Modify: `tests/test_discord_bot.py`

**Interfaces:**
- Consumes `StatClarificationPayload` and `ServiceOutcome(kind="confirm_search")`.
- Produces `StatClarificationView` and `QwenConfirmationView`.

- [ ] **Step 1: Write failing clarification tests**

Add tests proving:

```text
crit bow -> Critical Rate / Critical Damage choices
choosing Critical Rate keeps the same generation and executes deterministic resolution
fuzzy suggestion -> Use suggestion / Cancel controls
Qwen one-candidate outcome -> Search / Cancel
Qwen multi-candidate outcome -> interpretation select menu plus Search / Cancel
Cancel never executes repository search
```

Assert all views inherit the owner/stale checks from `SessionBoundView`.

- [ ] **Step 2: Run and verify RED**

```bash
uv run python -m unittest tests.test_discord_bot -v
```

Expected: clarification tests fail.

- [ ] **Step 3: Implement deterministic stat clarification**

For `mode="choose"`, create one button per `display_labels` plus Cancel. The selected button stores:

```python
session.resolution_choices[(group_index, clause_index)] = selected_stat_name
```

Then rerun the materialization worker with the same parsed search and choices. If another clarification is returned, edit the same message with the next clarification. When resolution completes, render the search results.

For `mode="confirm"`, provide Use Suggestion and Cancel buttons using the single candidate.

- [ ] **Step 4: Implement Qwen confirmation**

Format the validated request using existing structured-request semantics without exposing raw JSON. For one request, show Search and Cancel. For multiple requests, add a select menu whose values are request indexes and then Search / Cancel.

Search executes `run_confirmed_request_sync()` via `asyncio.to_thread`. After the await, check `sessions.is_current(key, generation)` before editing the message.

- [ ] **Step 5: Verify and commit Task 7**

```bash
uv run python -m unittest tests.test_discord_bot -v
git add discord_bot.py tests/test_discord_bot.py
git commit -m "feat: add Discord clarification flows"
```

---

### Task 8: Add help, database answers, refusal/errors, client wiring, and startup documentation

**Files:**
- Modify: `discord_bot.py`
- Modify: `tests/test_discord_bot.py`
- Modify: `README.md`

**Interfaces:**
- Produces `render_service_outcome()`, `create_client()`, Discord event handlers, and `main()`.

- [ ] **Step 1: Write failing informational/client tests**

Add tests asserting these service outcomes map to safe user-visible text:

```text
help -> compact examples
database -> compact answer
refuse -> explicit-stat-only limitation
unavailable -> "I couldn't interpret that automatically right now. Explicit item/stat searches are still available."
failed -> supported-search examples
```

Add a fake-client/fake-message test proving an allowed tagged message invokes `process_tagged_query`, while an ignored message sends nothing. Add a test asserting `discord.AllowedMentions.none()` is configured for outgoing bot responses.

- [ ] **Step 2: Run and verify RED**

```bash
uv run python -m unittest tests.test_discord_bot -v
```

Expected: informational/client tests fail.

- [ ] **Step 3: Implement safe informational renderers**

Help should include examples:

```text
@Bot hp armor
@Bot find armor with hp
@Bot hp > 5000 and cr bow
@Bot item Rapier
@Bot upgrade <crysta name>
```

Refusal must explain that explicit item types/stats are supported and build-role evaluation is not. User-facing errors must not include tracebacks, filesystem paths, raw SQLite exceptions, or raw Ollama errors.

- [ ] **Step 4: Create the Discord client**

```python
def create_client(config: DiscordBotConfig) -> discord.Client:
    client = discord.Client(
        intents=build_intents(),
        allowed_mentions=discord.AllowedMentions.none(),
    )
    sessions = DiscordSessionManager()

    @client.event
    async def on_ready() -> None:
        logger.info("Discord bot connected as %s for guild %s", client.user, config.guild_id)

    @client.event
    async def on_message(message: discord.Message) -> None:
        if client.user is None:
            return
        if not is_allowed_message(
            message,
            bot_user_id=client.user.id,
            guild_id=config.guild_id,
        ):
            return
        await process_tagged_query(
            message,
            bot_user_id=client.user.id,
            config=config,
            sessions=sessions,
        )

    return client
```

- [ ] **Step 5: Add safe boundary exception handling**

Wrap query processing/rendering at the Discord event boundary. Log technical exceptions with `logger.exception`. If the affected generation is still current, send only:

```text
The search failed due to an internal error.
```

- [ ] **Step 6: Add `main()`**

```python
def main() -> None:
    config = load_config()
    client = create_client(config)
    client.run(config.token, log_handler=None)


if __name__ == "__main__":
    main()
```

If the project configures logging elsewhere, retain one logging configuration instead of adding a second handler.

- [ ] **Step 7: Document setup in `README.md`**

Document environment variables:

```text
DISCORD_BOT_TOKEN=<bot token>
DISCORD_GUILD_ID=<allowed server id>
OLLAMA_MODEL=qwen3.5:2b
```

Document run command:

```bash
uv run python discord_bot.py
```

Document required bot permissions: View Channels, Send Messages, Embed Links, Attach Files. Explain that the bot is intentionally mention-only and does not require enabling the privileged Message Content intent for mentioned messages.

- [ ] **Step 8: Verify and commit Task 8**

```bash
uv run python -m unittest tests.test_discord_bot -v
git add discord_bot.py tests/test_discord_bot.py README.md
git commit -m "feat: wire Discord bot startup"
```

---

### Task 9: Full verification, live guild smoke test, and PR

**Files:**
- Review every file changed by Tasks 1-8.

**Interfaces:**
- Produces a verified implementation ready for review. Does not merge automatically.

- [ ] **Step 1: Run the full test suite**

```bash
uv run python -m unittest discover -s tests -v
```

Expected: exit code 0 with zero failures and zero errors.

- [ ] **Step 2: Run focused Discord/service tests again**

```bash
uv run python -m unittest \
  tests.test_noninteractive_resolution \
  tests.test_search_service \
  tests.test_discord_bot -v
```

Expected: exit code 0.

- [ ] **Step 3: Run syntax/import smoke checks**

```bash
uv run python -m py_compile \
  discord_bot.py \
  toram_search/service.py \
  search_items.py
```

Expected: exit code 0.

- [ ] **Step 4: Run the Discord bot locally**

```bash
uv run python discord_bot.py
```

In the configured allowed guild, verify exactly:

```text
normal unmentioned message
-> bot stays silent

@Bot can you find armor with hp
-> MaxHP Armor result embed, no Qwen confirmation

@Bot which bow has the highest critical rate
-> Critical Rate Bow result embed, highest first

@Bot crit bow
-> Critical Rate / Critical Damage clarification controls

@Bot item <known item with multiple images>
-> detail with first image and image navigation controls

@Bot hp armor
then immediately @Bot cr bow
-> old controls report stale; only the new generation may update

another user presses the first user's controls
-> ephemeral ownership warning
```

Inspect visible messages and attachments and confirm no database item ID and no Coryn page URL appears.

- [ ] **Step 5: Verify Qwen fallback behavior**

Use one supported wording not handled by deterministic normalization. Confirm Qwen produces an interpretation confirmation and Search executes only the validated deterministic request. Stop Ollama or use an unreachable test host and confirm the bot returns the safe unavailable message without crashing.

- [ ] **Step 6: Review the diff**

```bash
git diff --stat main...HEAD
git diff main...HEAD -- \
  discord_bot.py \
  toram_search/service.py \
  search_items.py \
  pyproject.toml \
  tests/test_discord_bot.py \
  tests/test_search_service.py \
  tests/test_noninteractive_resolution.py
```

Reject the branch for review if any of these are present:

```text
duplicated SQL/search semantics in discord_bot.py
visible item IDs
visible Coryn page URLs
bot token logging
message_content=True
one SQLite connection reused across worker threads
component callback without owner/generation validation
slow worker result sent without a current-generation check
```

- [ ] **Step 7: Open a PR without merging**

The PR description must report the actual full-test result and actual live-guild smoke-test result. It must summarize mention-only/single-guild gating, shared service reuse, pagination/dropdown UX, image carousel, requester-only controls, stale-session generations, and off-event-loop execution. Do not merge without explicit user instruction.
