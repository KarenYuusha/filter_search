# Discord Bot Design

## Goal

Add a new `discord_bot.py` frontend for the Toram item-search system. The Discord bot must reuse the existing deterministic parser, database search, help/database-question handling, and Qwen fallback rather than duplicating the search engine.

The bot responds only when explicitly mentioned and only inside one configured Discord server.

## Scope

The Discord frontend supports the same user-facing search capabilities as `search_items.py`:

- item-name search
- explicit stat search
- stat expressions and item-type filters
- natural-language searches already supported by the deterministic parser
- Qwen fallback for supported-but-hard-to-parse requests
- database/help questions
- crysta upgrade lookup
- supported clarifications and confirmations
- refusal of unsupported build/role interpretation such as `tank xtal` or `dps gear`

This first Discord version does not add new Toram search semantics. It changes transport and presentation only.

## Architecture

Do not copy the complete contents of `search_items.py` into `discord_bot.py`.

Extract the minimum non-interactive orchestration needed by both frontends into a shared service module, proposed as `toram_search/service.py`.

```text
Discord message                           Terminal input
      |                                        |
      v                                        v
 discord_bot.py                           search_items.py
      \                                        /
       \                                      /
        +-------- toram_search/service.py ----+
                           |
                           v
                 deterministic parser
                           |
                    Qwen fallback if needed
                           |
                           v
                    ItemRepository / SQLite
```

The shared service owns search orchestration and returns typed outcomes. It must not call `input()` or render Discord/terminal-specific UI.

The frontends are responsible only for transport and presentation:

- `search_items.py`: terminal prompts and terminal rendering
- `discord_bot.py`: Discord messages, embeds, dropdowns, buttons, and attachments

## Discord Dependency

Add `discord.py` to `pyproject.toml`.

Use `discord.Client` plus `discord.ui.View` / interaction components. A command-prefix framework is unnecessary because the bot is mention-triggered rather than command-prefix driven.

## Configuration

Read configuration from environment variables:

```text
DISCORD_BOT_TOKEN=<secret bot token>
DISCORD_GUILD_ID=<allowed server id>
OLLAMA_MODEL=qwen3.5:2b
```

Rules:

- Never hardcode the Discord bot token.
- `DISCORD_GUILD_ID` is required and represents exactly one allowed server in this version.
- Continue using the existing Ollama model configuration behavior.

## Message Acceptance Rules

A Discord message is processed only when every condition is true:

1. The message is inside a guild/server, not a DM.
2. `message.guild.id == DISCORD_GUILD_ID`.
3. The bot is explicitly mentioned in the message.
4. The author is not the bot itself.
5. The author is not another bot/webhook source that should be ignored.

Everything else is ignored silently.

For an accepted message, remove the bot mention from the content and trim surrounding whitespace before sending the query to the shared search service.

Example:

```text
@ToramBot can you find armor with hp
```

becomes:

```text
can you find armor with hp
```

## Search Routing

Keep the current priority:

```text
query
  |
  v
deterministic routing/parser
  |
  +-- understood --> deterministic database search
  |
  +-- not understood but supported --> Qwen fallback
                                  |
                                  v
                          strict validation
                                  |
                                  v
                          deterministic search
```

The Discord frontend must not make Qwen the source of database facts.

Natural-language phrases that the deterministic parser can already normalize, such as `can you find armor with hp`, must bypass Qwen.

## Shared Service Outcomes

The shared service should return typed, frontend-neutral outcomes. Exact class names may be adjusted during implementation, but the behavioral categories are:

- search results
- exact item detail
- upgrade result
- ambiguity / clarification
- Qwen interpretation requiring confirmation
- help answer
- database answer
- refusal
- no results
- unavailable Qwen
- unsupported/failed query

Discord rendering must consume these outcomes without reimplementing search semantics.

## Discord Session Model

Use one active session per user per channel, keyed by:

```text
(guild_id, channel_id, user_id)
```

A session may contain:

- current query
- current result set
- current result page
- selected item
- current item image index
- failed-query context
- pending ambiguity
- pending Qwen interpretation/confirmation
- active/inactive state or generation token

### New Query Behavior

A new tagged Discord message from the same user in the same channel immediately replaces that user's previous active session.

Example:

```text
@ToramBot hp armor
```

creates session A.

Then:

```text
@ToramBot cr bow
```

marks session A inactive and creates session B.

The old Discord message remains visible for history, but its controls become stale.

If the user presses a stale button or dropdown from an old search, respond ephemerally:

```text
This search is no longer active. Use the controls on your latest search.
```

### Session Isolation

Another user's query must not replace or modify the first user's session.

Example:

```text
Alice -> hp armor
Bob   -> cr bow
```

Both sessions remain independently active.

### Interaction Ownership

Only the user who created a search can operate its buttons or dropdowns.

If another user interacts with those components, respond ephemerally:

```text
Only the person who started this search can use these controls.
```

## Result Pagination

Display up to five items per page, matching the current terminal page size unless implementation constraints justify keeping the same constant centrally.

Example embed content:

```text
MaxHP — Armor
Highest first
Showing 1–5 of 38

1. Armor A
   MaxHP +10,000

2. Armor B
   MaxHP +8,000

3. Armor C
   MaxHP +7,500
```

Controls:

```text
[ Select an item ▼ ]
[ Previous ] [ Next ]
```

Pagination edits the same result message rather than sending a new result message for every page.

Disable or omit Previous/Next where the direction is unavailable.

## Item Selection

Use a Discord dropdown/select menu containing the item names on the current page.

Do not use five separate numeric buttons.

Selecting an item opens that item's detail view using the same active session.

## Item Detail Presentation

The item detail embed should show useful Toram information without database/debug identifiers.

Display, where available:

- item name
- item type
- stats
- stat conditions
- source information
- notes
- upgrade relationship information when relevant
- local item image

Explicitly do not show:

- item database ID
- Coryn page URL

Example:

```text
Rapier
1 Handed Sword

Stats
- Critical Rate +...
- ...

Obtained From
- Boss / NPC / other source

Notes
...
```

## Item Images

If an item has one or more valid local images, display an image in the item detail embed.

The first valid image is shown initially.

If there are multiple valid images, show:

```text
Image 1 of N
[ Previous Image ] [ Next Image ]
```

Image navigation edits the same item-detail message.

The current image index is stored in the user's active session.

If an item has no valid image, omit the image field and image-navigation controls entirely.

Image paths must be resolved using the same database-relative/local-path conventions already used by the project. Missing files should be skipped rather than crashing the bot.

## Back to Results

When an item was opened from a result list, provide:

```text
[ Back to Results ]
```

This restores the current result page and dropdown without creating a new search.

## Ambiguous Stats

Do not guess an explicitly ambiguous stat such as `crit`.

Example:

```text
@ToramBot crit bow
```

may render:

```text
What does "crit" mean?

[ Critical Rate ] [ Critical Damage ] [ Cancel ]
```

The selected choice continues the same active session.

## Qwen Interpretation Confirmation

If deterministic routing cannot handle the query and Qwen produces a valid structured search interpretation, show the interpretation before executing it when confirmation is appropriate.

Example:

```text
I interpreted this as:

Item type: Bow
Stats:
- MaxHP
- Critical Rate
Match: ALL

[ Search ] [ Cancel ]
```

The Search button executes the validated deterministic request.

Deterministic queries do not receive this extra confirmation unless existing ambiguity rules require one.

## New Tagged Query During Results or Detail

A new tagged query always starts a fresh active session, even if the user currently has:

- a result list open
- an item detail open
- an image navigation view open
- an ambiguity prompt open
- a Qwen confirmation prompt open

All controls from the previous session become stale immediately.

Button/dropdown interactions are not new queries; they continue the current session.

## Help Presentation

`@ToramBot help` returns a compact help embed with supported examples such as:

```text
@ToramBot hp armor
@ToramBot find armor with hp
@ToramBot hp > 5000 and cr bow
@ToramBot item Rapier
@ToramBot upgrade <crysta name>
```

Help text should explain explicit-stat/item searches and current limitations without exposing implementation details.

## Database Questions

Retain the allow-listed database/help questions already supported by the search system, for example:

```text
@ToramBot what stats can I search?
@ToramBot how many bows are there?
@ToramBot what item types do you have?
```

Answers are rendered as compact Discord text/embed content.

## Unsupported Requests

Keep the current refusal boundary for unsupported semantic build/role interpretation.

Example:

```text
@ToramBot best tank xtal
```

must not produce made-up Toram recommendations.

The bot should explain that it can search explicit item types and stats and provide a few explicit-stat examples.

## No Results

When a valid search returns no items, show what was interpreted so the user can correct the query.

Example:

```text
No results

No Armor items matching this stat search were found.

Search interpreted as:
- Item type: Armor
- Stat: MaxHP
```

Future deterministic suggestions may be added, but they are not required for the first Discord implementation.

## Error Handling

User-facing Discord messages must not expose Python tracebacks, Ollama exception internals, filesystem paths, or SQLite implementation details.

Log technical exceptions to the process console/logger.

User-facing categories include:

### Qwen unavailable

```text
I couldn't interpret that automatically right now. Explicit item/stat searches are still available.
```

### Database/search failure

```text
The search failed due to an internal database error.
```

### Unsupported/failed interpretation

```text
I couldn't convert that into a supported item/stat search.

Try:
- @ToramBot hp armor
- @ToramBot cr bow
- @ToramBot hp > 5000 and cr bow
```

## Concurrency

Discord users may search concurrently. Shared repository/search objects must be used safely with the bot's async event loop.

Potential blocking work, especially Ollama calls and SQLite operations if they become noticeable, must not freeze the Discord heartbeat/event loop. The implementation plan should explicitly decide whether to use `asyncio.to_thread`, a small executor, or another narrow boundary around synchronous service calls.

Do not introduce a complex distributed job system for this version.

## Discord Message Limits

Renderers must respect Discord limits for message/embed length, field counts, dropdown option counts, and attachment behavior.

Pagination should be the primary mechanism for keeping search-result messages compact.

Long notes/source/stat sections on item details should be truncated or split predictably rather than causing message-send failures.

## Intents and Permissions

Use the minimum Discord intents necessary for:

- receiving guild messages that mention the bot
- identifying guild/channel/user
- processing the mentioned message content
- sending replies and interactions

Document required Discord application permissions and bot invite permissions in the implementation documentation.

## Files Expected to Change

Proposed implementation surface:

```text
discord_bot.py
    Discord startup, guild/mention gate, embeds, views, dropdowns,
    image attachments, and session ownership

toram_search/service.py
    shared non-interactive search orchestration

search_items.py
    preserve CLI behavior while delegating reusable orchestration where practical

pyproject.toml
    add discord.py

tests/test_discord_bot.py
    Discord transport, rendering, ownership, stale-session behavior

tests/test_search_service.py
    shared service outcomes and CLI/Discord-neutral behavior
```

The implementation should avoid unrelated refactors.

## Testing Requirements

At minimum, add tests for:

1. messages from a different guild are ignored
2. DMs are ignored
3. unmentioned messages are ignored
4. messages from bots are ignored
5. valid mention in the allowed guild is processed
6. bot mention is stripped correctly
7. deterministic queries do not call Qwen unnecessarily
8. Qwen fallback is used only when needed
9. result pagination updates the correct page
10. item dropdown contains only current-page items
11. selecting an item hides database ID and Coryn URL
12. item detail includes an image when a valid local image exists
13. multiple images support Previous/Next Image
14. missing image files do not crash the bot
15. Back to Results restores the current page
16. only the originating user can use controls
17. a new tagged query invalidates the old session
18. stale controls return the inactive-search message
19. another user's new query does not invalidate the first user's session
20. ambiguity choices continue the same session
21. Qwen confirmation continues the same session
22. unsupported build/role requests remain refused
23. existing CLI tests remain green
24. full test suite passes before merge

## Acceptance Criteria

The Discord frontend is complete when:

- `discord_bot.py` starts from environment configuration
- it is silent outside the configured guild
- it is silent unless explicitly mentioned
- it reuses the same deterministic/Qwen/database search semantics as the CLI
- common deterministic searches respond without Qwen
- results use embeds, pagination, and a dropdown item selector
- item details never show item IDs or Coryn page URLs
- item details show local images when available
- multiple images use Previous/Next Image controls
- controls are restricted to the originating user
- one active session exists per `(guild_id, channel_id, user_id)`
- a new tagged query invalidates that user's previous session in that channel
- stale controls fail safely and ephemerally
- separate users can search independently
- unsupported Toram build advice remains refused
- existing terminal behavior remains intact
