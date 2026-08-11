# Discord Modularization Design

Date: 2026-08-11
Status: Approved design, pending written-spec review

## Goal

Refactor the current top-level `discord_bot.py` module into a small `toram_discord` package with clear responsibilities, while preserving the current Discord bot behavior and existing import compatibility.

This is a structural refactor only. It does not add session TTL/cleanup, change search behavior, change Discord UX, or alter Qwen usage.

## Scope

### In scope

Create a balanced Discord package:

```text
toram_discord/
    __init__.py
    config.py
    sessions.py
    render.py
    views.py
    app.py

discord_bot.py
```

Move the existing Discord responsibilities out of `discord_bot.py` into those modules, leaving `discord_bot.py` as a thin launcher and compatibility facade.

### Explicitly out of scope

- session expiration, TTLs, background cleanup, or bounded session caches
- changes to interaction timeout behavior
- changes to pagination size
- changes to bot commands, message wording, buttons, selects, embeds, or image behavior
- search parser/routing/ranking changes
- failed-query history changes
- Qwen/fallback behavior changes
- database schema changes
- deployment/systemd work
- boss, skill, build-role, tank, or DPS functionality
- removing legacy `discord_bot` imports

## Chosen approach

Use the balanced five-module split rather than either a conservative three-module split or a fine-grained many-file split.

The conservative approach would leave UI rendering and interaction state bundled in one large module. The fine-grained approach would create more files and dependency edges than the current project needs. The balanced split provides clear ownership without overengineering.

## Compatibility decision

`discord_bot.py` remains a backward-compatible facade and launcher.

Existing imports such as:

```python
from discord_bot import DiscordSessionManager
from discord_bot import SearchResultsView
from discord_bot import build_item_detail_embed
```

must continue to work after the refactor. The canonical implementations move under `toram_discord.*`, and `discord_bot.py` re-exports the existing public symbols.

New code should prefer imports from `toram_discord.*`, but this refactor does not remove or deprecate the old imports at runtime.

## Module responsibilities

### `toram_discord/config.py`

Own configuration and Discord message-gating helpers:

- `DiscordBotConfig`
- project `.env` loading
- Discord token/guild ID validation
- `build_intents()`
- `is_allowed_message()`
- mention/query extraction helpers
- bot display/example prefix helpers where they are configuration/message-gating concerns

This module must not own rendering, interaction views, session state, or bot startup.

### `toram_discord/sessions.py`

Own in-memory Discord interaction state:

- `SessionKey`
- `DiscordSearchSession`
- `DiscordSessionManager`

The existing generation semantics and failed-query-context cloning behavior remain unchanged.

There is no TTL, expiry timestamp, cleanup task, or background worker in this refactor.

### `toram_discord/render.py`

Own Discord presentation construction and local image-path helpers:

- text truncation
- attachment filename generation
- local image path resolution
- result-count/item helpers needed only for presentation
- result embeds
- item-detail embeds
- upgrade-tree embeds
- help embeds
- clarification/item-understanding embeds
- shared Discord field helpers

This module may depend on public semantic formatting from the existing search/core modules as needed, but it must not own Discord interaction callbacks or bot startup.

### `toram_discord/views.py`

Own interactive Discord UI and the synchronous service bridge used by that UI:

- base session-bound view
- action buttons/selects
- result pagination and selection views
- item-detail image/back navigation
- stat clarification views
- item-query understanding/confirmation views
- confirmed-search views
- message editing helpers used by view callbacks
- synchronous repository/SearchService bridge helpers that are invoked with `asyncio.to_thread`

Generation checks must remain in place so stale interactions cannot overwrite a newer query.

The service bridge remains behavior-equivalent: it creates the repository, invokes `SearchService`, and closes the repository exactly as before.

### `toram_discord/app.py`

Own Discord application wiring:

- bot/client construction
- event handlers
- handling a new mentioned query
- connecting config, sessions, service execution, rendering, and views
- startup/main entrypoint

It does not own reusable embed construction or session data structures.

### `discord_bot.py`

After the refactor, this file contains only:

- imports/re-exports required for compatibility
- the launcher/main entrypoint

It must not keep duplicate implementations of code moved into `toram_discord`.

## Dependency direction

The intended dependency direction is:

```text
discord_bot.py
        |
        v
toram_discord.app
        |
        v
toram_discord.views
      /             \
     v               v
sessions           render
      \             /
       v           v
     toram_search / toram_data
```

Important boundary rules:

- `toram_discord` may depend on `toram_search` and `toram_data`.
- `toram_search` and `toram_data` must not import `toram_discord` or `discord_bot`.
- `discord_bot.py` may import/re-export `toram_discord` symbols, but the package must not import `discord_bot.py` to obtain implementation logic.

## Data flow

A new Discord query keeps the current flow:

```text
Discord message
    -> toram_discord.app
    -> DiscordSessionManager.start_query()
    -> SearchService
    -> ServiceOutcome
    -> render.py + views.py
    -> Discord response / interaction controls
```

Button/select interactions remain session-bound and generation-bound. They read the current session, run any required synchronous service/database work in a worker thread, verify that the generation is still current, update session state, and then edit the Discord message.

## Behavior preservation requirements

The refactor must preserve all currently supported behavior, including:

- environment variable names and validation
- allowed-guild/message filtering
- Discord intents
- interaction timeout value
- result page size
- session generation behavior
- per-user/per-channel/per-guild session isolation
- failed-query-context cloning
- clarification and pending-item-understanding state
- result ordering and pagination
- button/select labels and interaction flow
- embed text and fields
- item image resolution and attachment naming
- exact SearchService/Qwen invocation behavior
- repository open/close lifecycle

If extraction reveals an unrelated existing bug, it should be documented rather than silently fixed in this structural change unless the bug prevents the modularization from functioning.

## Testing strategy

The implementation must preserve the existing Discord test coverage and add architecture-focused regression coverage.

Required checks:

1. Existing imports from `discord_bot.py` continue to resolve to the canonical `toram_discord` objects.
2. `toram_discord.sessions` can be imported/tested independently of bot startup.
3. `toram_discord.config`, `render`, `views`, and `app` import successfully.
4. No module under `toram_search` or `toram_data` imports `toram_discord` or `discord_bot`.
5. `discord_bot.py` contains launcher/compatibility wiring only, with no duplicate class/function implementations that belong to the package.
6. Existing Discord config, gate, rendering, session, image, upgrade, item-understanding, and follow-up tests remain green.
7. The full repository test suite remains green.
8. Core/front-end modules compile successfully.

Tests that monkey-patch implementation details in `discord_bot.py` should be updated only when necessary to point at the new canonical implementation owner. Compatibility behavior should still be tested through `discord_bot.py`.

## Migration strategy

The implementation should proceed in small responsibility-based moves, preserving compatibility exports after each move where practical:

1. create `toram_discord` package and extract configuration/session primitives
2. extract rendering helpers
3. extract views and service bridge functions
4. extract application/event wiring
5. reduce `discord_bot.py` to launcher + compatibility exports
6. add mechanical dependency/compatibility assertions and run full verification

This avoids a single all-at-once rewrite and makes regressions easier to localize.

## Success criteria

The refactor is complete when all of the following are true:

1. `toram_discord/{config,sessions,render,views,app}.py` own the responsibilities defined above.
2. `discord_bot.py` is a thin launcher and compatibility facade.
3. Existing `from discord_bot import ...` imports used by the project continue to work.
4. No intended Discord UX, search, session-lifetime, or Qwen behavior has changed.
5. `toram_search` and `toram_data` remain independent of Discord modules.
6. No duplicate implementation remains in `discord_bot.py` after extraction.
7. Existing and new architecture tests pass.
8. The full repository suite and compile checks pass on the final implementation head.
