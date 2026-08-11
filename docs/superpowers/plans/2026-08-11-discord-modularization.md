# Discord Modularization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the current top-level `discord_bot.py` god module into a focused `toram_discord` package while preserving all current Discord behavior and keeping existing `from discord_bot import ...` imports working.

**Architecture:** Introduce `toram_discord/config.py`, `sessions.py`, `render.py`, `views.py`, and `app.py`, with dependencies flowing from the launcher/application layer down into views, rendering/session primitives, and then the existing search/data layers. `discord_bot.py` becomes a compatibility facade plus launcher only; it must not retain duplicate implementations.

**Tech Stack:** Python 3, `discord.py`, `python-dotenv`, `unittest`, existing `toram_search` / `toram_data` modules, SQLite repository abstractions already present in the project.

## Global Constraints

- This is a structural refactor only; no intended Discord UX or search behavior changes.
- Preserve `PAGE_SIZE = 5` and `VIEW_TIMEOUT_SECONDS = 900` exactly.
- Preserve environment variables `DISCORD_BOT_TOKEN`, `DISCORD_GUILD_IDS`, and legacy `DISCORD_GUILD_ID` exactly.
- Preserve session generation semantics, failed-query-context cloning, pending clarification state, and per-guild/channel/user isolation.
- Do not add session expiration, TTLs, cleanup tasks, background workers, or bounded caches.
- Preserve all existing button/select labels, embed wording, image handling, Qwen invocation behavior, and repository open/close lifecycle.
- `toram_search` and `toram_data` must not import `toram_discord` or `discord_bot`.
- `toram_discord` must never import `discord_bot` to obtain implementation logic.
- Keep `discord_bot.py` backward compatible for existing public imports used by the project.
- If an unrelated pre-existing bug is discovered, document it rather than changing behavior in this refactor unless it blocks the modularization.
- No new runtime dependencies.

---

## Target File Structure

**Create:**

- `toram_discord/__init__.py` — package marker only; do not turn this into a second compatibility facade.
- `toram_discord/config.py` — Discord config, environment loading, intents, guild/message gating, mention extraction, bot example prefix.
- `toram_discord/sessions.py` — `SessionKey`, `DiscordSearchSession`, `DiscordSessionManager`.
- `toram_discord/render.py` — presentation helpers and embed/image construction; owns `PAGE_SIZE = 5`.
- `toram_discord/views.py` — synchronous SearchService bridge, Discord views/components, interaction message construction/editing; owns `VIEW_TIMEOUT_SECONDS = 900`.
- `toram_discord/app.py` — query processing, Discord client/event wiring, startup `main()`.
- `tests/test_discord_module_boundaries.py` — canonical ownership, compatibility identity, dependency direction, facade-thinness tests.

**Modify:**

- `discord_bot.py` — progressively replace local implementations with imports/re-exports, ending as launcher + compatibility facade only.
- `tests/test_discord_bot.py` — keep behavioral coverage; only redirect monkey-patches to canonical implementation owners where extraction makes the old patch target ineffective.

**Do not modify unless a failing compatibility test proves it is required:**

- `toram_search/*`
- `toram_data/*`
- database schema/data files
- `.env.example`

---

### Task 1: Extract Configuration and Session Primitives

**Files:**
- Create: `toram_discord/__init__.py`
- Create: `toram_discord/config.py`
- Create: `toram_discord/sessions.py`
- Create: `tests/test_discord_module_boundaries.py`
- Modify: `discord_bot.py:1-190`
- Test: `tests/test_discord_bot.py` (`DiscordConfigTests`, `DiscordBotGateTests`, `DiscordSessionTests`)

**Interfaces:**
- Consumes: `core.DEFAULT_DATABASE`, `SearchPayload`, `ItemDetailPayload`, `SearchIntentRequest`, `FailedQueryContext`, `PendingItemSearch`.
- Produces from `toram_discord.config`:
  - `DiscordBotConfig`
  - `PROJECT_ROOT`
  - `load_project_environment(env_path: Path | None = None) -> Path`
  - `load_config(environ: Mapping[str, str] = os.environ) -> DiscordBotConfig`
  - `build_intents() -> discord.Intents`
  - `is_allowed_message(message, *, bot_user_id: int, guild_ids: Iterable[int] | None = None, guild_id: int | None = None) -> bool`
  - `extract_mentioned_query(content: str, bot_user_id: int) -> str`
  - `bot_example_prefix(guild, bot_user) -> str`
- Produces from `toram_discord.sessions`:
  - `SessionKey = tuple[int, int, int]`
  - `DiscordSearchSession`
  - `DiscordSessionManager`
- `discord_bot.py` must re-export those exact objects, not wrapper subclasses/functions.

- [ ] **Step 1: Add RED ownership/compatibility tests**

Create `tests/test_discord_module_boundaries.py` with the first boundary assertions:

```python
import unittest

import discord_bot


class DiscordModuleBoundaryTests(unittest.TestCase):
    def test_config_symbols_have_canonical_package_owner(self):
        from toram_discord.config import (
            DiscordBotConfig,
            build_intents,
            extract_mentioned_query,
            is_allowed_message,
            load_config,
            load_project_environment,
        )

        self.assertIs(discord_bot.DiscordBotConfig, DiscordBotConfig)
        self.assertIs(discord_bot.build_intents, build_intents)
        self.assertIs(discord_bot.extract_mentioned_query, extract_mentioned_query)
        self.assertIs(discord_bot.is_allowed_message, is_allowed_message)
        self.assertIs(discord_bot.load_config, load_config)
        self.assertIs(discord_bot.load_project_environment, load_project_environment)

    def test_session_symbols_have_canonical_package_owner(self):
        from toram_discord.sessions import DiscordSearchSession, DiscordSessionManager

        self.assertIs(discord_bot.DiscordSearchSession, DiscordSearchSession)
        self.assertIs(discord_bot.DiscordSessionManager, DiscordSessionManager)
```

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```bash
python -m unittest tests.test_discord_module_boundaries -v
```

Expected: FAIL because `toram_discord.config` and `toram_discord.sessions` do not exist yet.

- [ ] **Step 3: Create the package and move config code verbatim**

Create `toram_discord/__init__.py` as:

```python
"""Discord frontend package for Toram item search."""
```

Create `toram_discord/config.py` by moving, without semantic edits:

- `PROJECT_ROOT`
- `DiscordBotConfig`
- `load_project_environment`
- `load_config`
- `build_intents`
- `is_allowed_message`
- `extract_mentioned_query`
- `bot_example_prefix`

Use these imports:

```python
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

import discord
from dotenv import load_dotenv

import search_items as core
```

Keep `DiscordBotConfig.database_path: Path = core.DEFAULT_DATABASE` and preserve the existing legacy `guild_id` property exactly.

- [ ] **Step 4: Move session code verbatim**

Create `toram_discord/sessions.py` with:

```python
from __future__ import annotations

from dataclasses import dataclass, field

from toram_search.fallback import SearchIntentRequest
from toram_search.service import ItemDetailPayload, SearchPayload
from toram_search.session import FailedQueryContext, PendingItemSearch

SessionKey = tuple[int, int, int]
```

Then move `DiscordSearchSession` and `DiscordSessionManager` unchanged, including:

```python
cloned = FailedQueryContext(max_entries=3)
```

and the existing generation increment behavior.

- [ ] **Step 5: Replace local definitions in `discord_bot.py` with canonical imports**

At the top of `discord_bot.py`, import:

```python
from toram_discord.config import (
    DiscordBotConfig,
    bot_example_prefix,
    build_intents,
    extract_mentioned_query,
    is_allowed_message,
    load_config,
    load_project_environment,
)
from toram_discord.sessions import (
    DiscordSearchSession,
    DiscordSessionManager,
    SessionKey,
)
```

Remove the local definitions that were moved. Do not change any later call sites yet.

- [ ] **Step 6: Run focused config/session/gate tests**

Run:

```bash
python -m unittest \
  tests.test_discord_module_boundaries \
  tests.test_discord_bot.DiscordConfigTests \
  tests.test_discord_bot.DiscordBotGateTests \
  tests.test_discord_bot.DiscordSessionTests -v
```

Expected: PASS.

- [ ] **Step 7: Commit Task 1**

```bash
git add toram_discord/__init__.py toram_discord/config.py toram_discord/sessions.py \
  discord_bot.py tests/test_discord_module_boundaries.py
git commit -m "refactor: extract Discord config and sessions"
```

---

### Task 2: Extract Rendering and Image Helpers

**Files:**
- Create: `toram_discord/render.py`
- Modify: `discord_bot.py` (remove local render/image implementations; re-export canonical names)
- Modify: `tests/test_discord_module_boundaries.py`
- Test: existing rendering/image/help/detail tests in `tests/test_discord_bot.py`

**Interfaces:**
- Consumes: search payload/model types, `PendingItemSearch`, `SearchIntentRequest`, existing semantic formatting from `search_items` compatibility API.
- Produces:
  - `PAGE_SIZE = 5`
  - `truncate_discord_text`
  - `visible_attachment_name`
  - `valid_local_image_paths`
  - `is_upgrade_suggestion_payload`
  - `build_search_results_embed`
  - `build_upgrade_detail_embed`
  - `build_item_detail_embed`
  - `build_help_embed`
  - `build_clarification_embed`
  - `build_item_understanding_embed`
  - `build_qwen_confirmation_embed`
  - package-private `_build_text_embed`, `_result_count`, `_result_item`
- `views.py` will later consume `_build_text_embed`, `_result_count`, `_result_item`, and all public embed builders.

- [ ] **Step 1: Add RED canonical-owner tests for rendering**

Append to `DiscordModuleBoundaryTests`:

```python
def test_render_symbols_have_canonical_package_owner(self):
    from toram_discord.render import (
        build_help_embed,
        build_item_detail_embed,
        build_search_results_embed,
        truncate_discord_text,
        valid_local_image_paths,
        visible_attachment_name,
    )

    self.assertIs(discord_bot.build_help_embed, build_help_embed)
    self.assertIs(discord_bot.build_item_detail_embed, build_item_detail_embed)
    self.assertIs(discord_bot.build_search_results_embed, build_search_results_embed)
    self.assertIs(discord_bot.truncate_discord_text, truncate_discord_text)
    self.assertIs(discord_bot.valid_local_image_paths, valid_local_image_paths)
    self.assertIs(discord_bot.visible_attachment_name, visible_attachment_name)
```

- [ ] **Step 2: Run the boundary test and verify RED**

```bash
python -m unittest tests.test_discord_module_boundaries.DiscordModuleBoundaryTests.test_render_symbols_have_canonical_package_owner -v
```

Expected: FAIL because `toram_discord.render` does not exist.

- [ ] **Step 3: Create `toram_discord/render.py` and move presentation code without wording changes**

Use imports equivalent to the current module:

```python
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

import discord

import search_items as core
from toram_search.fallback import SearchIntentRequest
from toram_search.service import (
    ExpressionResultsPayload,
    ItemResultsPayload,
    SearchPayload,
    StatClarificationPayload,
    StatResultsPayload,
    UpgradeDetailPayload,
    UpgradeResultsPayload,
    format_search_request,
)
from toram_search.session import PendingItemSearch

PAGE_SIZE = 5
```

Move exactly these existing helpers/builders:

```text
truncate_discord_text
visible_attachment_name
valid_local_image_paths
_format_number
_condition_label
_filter_label
_safe_field
_result_count
_result_item
is_upgrade_suggestion_payload
_result_lines
build_search_results_embed
build_upgrade_detail_embed
build_item_detail_embed
build_help_embed
_build_text_embed
build_clarification_embed
build_item_understanding_embed
build_qwen_confirmation_embed
```

Do not change any strings, field sizes, page math, image suffix rules, source formatting, stat formatting, or result ordering.

- [ ] **Step 4: Re-export moved public names from `discord_bot.py`**

Import the moved names from `toram_discord.render`. Also import private helpers needed by still-local view code:

```python
from toram_discord.render import (
    PAGE_SIZE,
    _build_text_embed,
    _result_count,
    _result_item,
    build_clarification_embed,
    build_help_embed,
    build_item_detail_embed,
    build_item_understanding_embed,
    build_qwen_confirmation_embed,
    build_search_results_embed,
    build_upgrade_detail_embed,
    is_upgrade_suggestion_payload,
    truncate_discord_text,
    valid_local_image_paths,
    visible_attachment_name,
)
```

Delete the moved implementations from `discord_bot.py` so there is one implementation owner.

- [ ] **Step 5: Run rendering-focused regression tests**

First list the test methods if needed with:

```bash
python -m unittest tests.test_discord_bot -v
```

Then rerun the whole Discord test module after the move:

```bash
python -m unittest tests.test_discord_bot tests.test_discord_module_boundaries -v
```

Expected: PASS with identical embed/button/image assertions.

- [ ] **Step 6: Commit Task 2**

```bash
git add toram_discord/render.py discord_bot.py tests/test_discord_module_boundaries.py
git commit -m "refactor: extract Discord rendering"
```

---

### Task 3: Extract the Synchronous SearchService Bridge

**Files:**
- Create: `toram_discord/views.py`
- Modify: `discord_bot.py`
- Modify: `tests/test_discord_module_boundaries.py`
- Modify: `tests/test_discord_bot.py` only if existing mocks target `discord_bot.SearchService`, `discord_bot.run_query_sync`, or another implementation-local symbol that no longer controls the canonical function.

**Interfaces:**
- Consumes:
  - `DiscordSessionManager`, `SessionKey`
  - `SearchService`
  - `OllamaQwenClient`
  - `SearchIntentRequest`
  - `FailedQueryContext`, `PendingItemSearch`
  - canonical search repository exposed through `core.ItemRepository`
- Produces in `toram_discord.views`:
  - `run_query_sync`
  - `run_confirmed_request_sync`
  - `run_clarification_sync`
  - `run_item_understanding_choice_sync`
  - `run_pending_item_search_confirmation_sync`
  - `run_item_detail_sync`
  - `run_upgrade_selection_sync`
  - `send_if_current`
- Later Task 4 extends this same module with view classes.

- [ ] **Step 1: Add RED bridge ownership tests**

Append:

```python
def test_service_bridge_has_canonical_package_owner(self):
    from toram_discord.views import (
        run_confirmed_request_sync,
        run_item_detail_sync,
        run_query_sync,
        run_upgrade_selection_sync,
        send_if_current,
    )

    self.assertIs(discord_bot.run_query_sync, run_query_sync)
    self.assertIs(discord_bot.run_confirmed_request_sync, run_confirmed_request_sync)
    self.assertIs(discord_bot.run_item_detail_sync, run_item_detail_sync)
    self.assertIs(discord_bot.run_upgrade_selection_sync, run_upgrade_selection_sync)
    self.assertIs(discord_bot.send_if_current, send_if_current)
```

- [ ] **Step 2: Run the bridge test and verify RED**

```bash
python -m unittest tests.test_discord_module_boundaries.DiscordModuleBoundaryTests.test_service_bridge_has_canonical_package_owner -v
```

Expected: FAIL because `toram_discord.views` does not exist.

- [ ] **Step 3: Create `toram_discord/views.py` with only the bridge first**

Start with:

```python
from __future__ import annotations

from pathlib import Path
from typing import Mapping

import search_items as core
from toram_search.fallback import SearchIntentRequest
from toram_search.llm import OllamaQwenClient
from toram_search.service import ItemDetailPayload, SearchService, ServiceOutcome
from toram_search.session import FailedQueryContext, PendingItemSearch
from toram_discord.sessions import DiscordSessionManager, SessionKey
```

Move the seven existing `run_*_sync` functions and `send_if_current` verbatim. Preserve the repository lifecycle pattern in every bridge function:

```python
repository = repository_factory(database_path.resolve())
try:
    ...
finally:
    repository.close()
```

Do not consolidate the bridge functions into a new abstraction during this refactor.

- [ ] **Step 4: Re-export bridge functions from `discord_bot.py`**

Import them from `toram_discord.views` and remove their local definitions.

If a test previously patches a local dependency used inside one of these functions, redirect the patch to the canonical module. Example:

```python
with patch("toram_discord.views.SearchService", FakeSearchService):
    ...
```

Do not preserve monkey-patch-by-alias behavior through wrappers; preserve callable import compatibility instead.

- [ ] **Step 5: Run bridge and full Discord tests**

```bash
python -m unittest tests.test_discord_module_boundaries tests.test_discord_bot -v
```

Expected: PASS.

- [ ] **Step 6: Commit Task 3**

```bash
git add toram_discord/views.py discord_bot.py tests/test_discord_module_boundaries.py tests/test_discord_bot.py
git commit -m "refactor: extract Discord service bridge"
```

---

### Task 4: Extract Interactive Views and Outcome Message Wiring

**Files:**
- Modify: `toram_discord/views.py`
- Modify: `discord_bot.py`
- Modify: `tests/test_discord_module_boundaries.py`
- Modify: `tests/test_discord_bot.py` only for canonical patch paths

**Interfaces:**
- Consumes from `toram_discord.sessions`:
  - `DiscordSessionManager`
  - `SessionKey`
- Consumes from `toram_discord.render`:
  - `PAGE_SIZE`
  - `_build_text_embed`
  - `_result_count`
  - `_result_item`
  - all existing embed/image helpers used by callbacks
- Produces:
  - `VIEW_TIMEOUT_SECONDS = 900`
  - `SessionBoundView`
  - `ActionButton`
  - `ActionSelect`
  - `SearchResultsView`
  - `ItemDetailView`
  - `build_item_detail_message`
  - `StatClarificationView`
  - `ItemUnderstandingView`
  - `QwenConfirmationView`
  - `build_service_outcome_message`
  - `edit_service_outcome`
  - package-private `_edit_component_message`, `_make_search_view`

- [ ] **Step 1: Add RED view ownership tests**

Append:

```python
def test_view_symbols_have_canonical_package_owner(self):
    from toram_discord.views import (
        ItemDetailView,
        ItemUnderstandingView,
        QwenConfirmationView,
        SearchResultsView,
        SessionBoundView,
        StatClarificationView,
        build_service_outcome_message,
    )

    self.assertIs(discord_bot.SessionBoundView, SessionBoundView)
    self.assertIs(discord_bot.SearchResultsView, SearchResultsView)
    self.assertIs(discord_bot.ItemDetailView, ItemDetailView)
    self.assertIs(discord_bot.StatClarificationView, StatClarificationView)
    self.assertIs(discord_bot.ItemUnderstandingView, ItemUnderstandingView)
    self.assertIs(discord_bot.QwenConfirmationView, QwenConfirmationView)
    self.assertIs(discord_bot.build_service_outcome_message, build_service_outcome_message)
```

- [ ] **Step 2: Run the new view test and verify RED**

```bash
python -m unittest tests.test_discord_module_boundaries.DiscordModuleBoundaryTests.test_view_symbols_have_canonical_package_owner -v
```

Expected: FAIL because those classes/functions are still defined in `discord_bot.py`.

- [ ] **Step 3: Add the exact view imports/constants to `toram_discord/views.py`**

Extend imports with:

```python
import asyncio
from typing import Mapping, Sequence

import discord

from toram_search.service import (
    ExpressionResultsPayload,
    GuidedStatPayload,
    ItemDetailPayload,
    ItemResultsPayload,
    SearchPayload,
    ServiceOutcome,
    StatClarificationPayload,
    StatResultsPayload,
    UpgradeDetailPayload,
    UpgradeResultsPayload,
    format_search_request,
    item_id_from_payload,
)
from toram_discord.config import bot_example_prefix
from toram_discord.render import (
    PAGE_SIZE,
    _build_text_embed,
    _result_count,
    _result_item,
    build_clarification_embed,
    build_item_detail_embed,
    build_item_understanding_embed,
    build_qwen_confirmation_embed,
    build_search_results_embed,
    build_upgrade_detail_embed,
    is_upgrade_suggestion_payload,
    truncate_discord_text,
    valid_local_image_paths,
    visible_attachment_name,
)

VIEW_TIMEOUT_SECONDS = 900
```

- [ ] **Step 4: Move the base components and result/detail views verbatim**

Move in this order so references are defined before use:

```text
SessionBoundView
ActionButton
ActionSelect
_edit_component_message
SearchResultsView
ItemDetailView
build_item_detail_message
```

Preserve:

- owner-only interaction check wording
- stale-generation interaction check wording
- `asyncio.to_thread` calls
- pagination clamping and page mutations
- `selected_index`, `detail_payload`, and `image_index` mutations
- upgrade-suggestion selection behavior
- attachment replacement behavior

- [ ] **Step 5: Move clarification/understanding/Qwen views verbatim**

Move:

```text
StatClarificationView
ItemUnderstandingView
QwenConfirmationView
```

Preserve all current state transitions, especially:

```python
session.pending_item_search = None
```

on the same existing cancel/edit/search paths, and:

```python
if outcome.kind == "search" and not isinstance(outcome.payload, StatClarificationPayload):
    session.failed_context.clear()
```

where it exists today.

- [ ] **Step 6: Move outcome-message composition/editing verbatim**

Move:

```text
_make_search_view
build_service_outcome_message
edit_service_outcome
```

Keep every existing `outcome.kind` branch and its exact text/controls:

```text
help
database
refuse
unavailable
failed
item_understanding
confirm_search
payload/detail/search handling
```

No wording edits are allowed in this task.

- [ ] **Step 7: Re-export view symbols from `discord_bot.py` and delete local implementations**

Import all moved public view symbols and any public message builders needed for compatibility from `toram_discord.views`.

After removal, `discord_bot.py` should contain no `discord.ui.View`, `discord.ui.Button`, or `discord.ui.Select` subclass definitions.

- [ ] **Step 8: Run the complete Discord behavior suite**

```bash
python -m unittest tests.test_discord_bot tests.test_discord_module_boundaries -v
```

Expected: PASS. Any failure in labels, view children, session state, pagination, or payload routing is a regression and must be fixed by restoring the old behavior rather than changing the expected tests.

- [ ] **Step 9: Commit Task 4**

```bash
git add toram_discord/views.py discord_bot.py tests/test_discord_module_boundaries.py tests/test_discord_bot.py
git commit -m "refactor: extract Discord interaction views"
```

---

### Task 5: Extract Discord Application Wiring and Reduce the Facade

**Files:**
- Create: `toram_discord/app.py`
- Modify: `discord_bot.py`
- Modify: `tests/test_discord_module_boundaries.py`
- Modify: `tests/test_discord_bot.py` only for canonical app patch targets

**Interfaces:**
- Consumes:
  - `DiscordBotConfig`, config helpers from `toram_discord.config`
  - `DiscordSessionManager`, `SessionKey`
  - `run_query_sync`, `build_service_outcome_message` from `toram_discord.views`
- Produces:
  - `process_tagged_query(message, *, bot_user, config, sessions) -> None`
  - `create_client(config: DiscordBotConfig) -> discord.Client`
  - `main() -> None`
- `discord_bot.py` becomes imports/re-exports plus `if __name__ == "__main__": main()` only.

- [ ] **Step 1: Add RED app ownership and facade-thinness tests**

Append to `tests/test_discord_module_boundaries.py`:

```python
import ast
from pathlib import Path


def test_app_symbols_have_canonical_package_owner(self):
    from toram_discord.app import create_client, main, process_tagged_query

    self.assertIs(discord_bot.create_client, create_client)
    self.assertIs(discord_bot.main, main)
    self.assertIs(discord_bot.process_tagged_query, process_tagged_query)


def test_discord_bot_facade_has_no_implementation_definitions(self):
    path = Path(__file__).resolve().parents[1] / "discord_bot.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    definitions = [
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]
    self.assertEqual(definitions, [])
```

- [ ] **Step 2: Run those two tests and verify RED**

```bash
python -m unittest \
  tests.test_discord_module_boundaries.DiscordModuleBoundaryTests.test_app_symbols_have_canonical_package_owner \
  tests.test_discord_module_boundaries.DiscordModuleBoundaryTests.test_discord_bot_facade_has_no_implementation_definitions -v
```

Expected: FAIL because `app.py` does not exist and `discord_bot.py` still defines app functions.

- [ ] **Step 3: Create `toram_discord/app.py` and move application wiring verbatim**

Use:

```python
from __future__ import annotations

import asyncio
import logging

import discord

from toram_search.service import StatClarificationPayload
from toram_discord.config import (
    DiscordBotConfig,
    bot_example_prefix,
    build_intents,
    extract_mentioned_query,
    is_allowed_message,
    load_config,
    load_project_environment,
)
from toram_discord.sessions import DiscordSessionManager, SessionKey
from toram_discord.views import build_service_outcome_message, run_query_sync

logger = logging.getLogger(__name__)
```

Move unchanged:

```text
process_tagged_query
create_client
main
```

Preserve:

- empty mention query becoming `"help"`
- key construction `(guild.id, channel.id, author.id)`
- stale generation early return
- clearing failed context only after successful non-clarification search
- `mention_author=False`
- `discord.AllowedMentions.none()`
- internal-error reply wording
- `logging.basicConfig(level=logging.INFO)`
- `client.run(config.token, log_handler=None)`

- [ ] **Step 4: Replace `discord_bot.py` with a compatibility facade**

The final file should have this shape:

```python
from toram_discord.app import create_client, main, process_tagged_query
from toram_discord.config import (
    DiscordBotConfig,
    bot_example_prefix,
    build_intents,
    extract_mentioned_query,
    is_allowed_message,
    load_config,
    load_project_environment,
)
from toram_discord.render import (
    build_clarification_embed,
    build_help_embed,
    build_item_detail_embed,
    build_item_understanding_embed,
    build_qwen_confirmation_embed,
    build_search_results_embed,
    build_upgrade_detail_embed,
    is_upgrade_suggestion_payload,
    truncate_discord_text,
    valid_local_image_paths,
    visible_attachment_name,
)
from toram_discord.sessions import DiscordSearchSession, DiscordSessionManager, SessionKey
from toram_discord.views import (
    ActionButton,
    ActionSelect,
    ItemDetailView,
    ItemUnderstandingView,
    QwenConfirmationView,
    SearchResultsView,
    SessionBoundView,
    StatClarificationView,
    build_item_detail_message,
    build_service_outcome_message,
    edit_service_outcome,
    run_clarification_sync,
    run_confirmed_request_sync,
    run_item_detail_sync,
    run_item_understanding_choice_sync,
    run_pending_item_search_confirmation_sync,
    run_query_sync,
    run_upgrade_selection_sync,
    send_if_current,
)

if __name__ == "__main__":
    main()
```

Also re-export any additional pre-refactor **non-private** symbol actually imported by `tests/` or another repository module, as proven by repository search/test failure. Do not re-export private `_...` helpers merely for convenience.

- [ ] **Step 5: Redirect tests that intentionally monkey-patch app internals**

If a test currently uses a patch like:

```python
patch("discord_bot.run_query_sync", ...)
```

while invoking `toram_discord.app.process_tagged_query`, change it to:

```python
patch("toram_discord.app.run_query_sync", ...)
```

The public import identity remains preserved; only implementation-local patch targeting changes.

- [ ] **Step 6: Run app/facade/Discord tests**

```bash
python -m unittest tests.test_discord_module_boundaries tests.test_discord_bot -v
```

Expected: PASS.

- [ ] **Step 7: Commit Task 5**

```bash
git add toram_discord/app.py discord_bot.py tests/test_discord_module_boundaries.py tests/test_discord_bot.py
git commit -m "refactor: extract Discord application wiring"
```

---

### Task 6: Enforce Dependency Direction and Run Final Verification

**Files:**
- Modify: `tests/test_discord_module_boundaries.py`
- Modify production files only if a new boundary test exposes an actual dependency/duplication regression.

**Interfaces:**
- Consumes: final package from Tasks 1-5.
- Produces: permanent architectural regression tests and verification evidence for the final PR.

- [ ] **Step 1: Add dependency-direction/importability tests**

Append:

```python
import ast
import importlib
from pathlib import Path


def _imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".", 1)[0])
    return roots


def test_search_and_data_layers_do_not_depend_on_discord_frontend(self):
    root = Path(__file__).resolve().parents[1]
    for package_name in ("toram_search", "toram_data"):
        for path in (root / package_name).glob("*.py"):
            imported = _imported_roots(path)
            self.assertNotIn("toram_discord", imported, path)
            self.assertNotIn("discord_bot", imported, path)


def test_discord_package_does_not_import_compatibility_facade(self):
    root = Path(__file__).resolve().parents[1]
    for path in (root / "toram_discord").glob("*.py"):
        self.assertNotIn("discord_bot", _imported_roots(path), path)


def test_discord_modules_import_independently(self):
    for module_name in (
        "toram_discord.config",
        "toram_discord.sessions",
        "toram_discord.render",
        "toram_discord.views",
        "toram_discord.app",
    ):
        self.assertIsNotNone(importlib.import_module(module_name))
```

- [ ] **Step 2: Run boundary tests first**

```bash
python -m unittest tests.test_discord_module_boundaries -v
```

Expected: PASS.

- [ ] **Step 3: Compile the changed frontend and existing core packages**

```bash
python -m compileall -q toram_discord toram_search toram_data discord_bot.py search_items.py
```

Expected: exit code 0, no output.

- [ ] **Step 4: Run all Discord tests**

```bash
python -m unittest tests.test_discord_bot tests.test_discord_module_boundaries -v
```

Expected: PASS.

- [ ] **Step 5: Run the full repository suite**

```bash
python -m unittest discover -s tests -v
```

Expected: all tests PASS. The pre-refactor verified baseline is 234 tests; the final count should be **greater than 234** because `test_discord_module_boundaries.py` adds new tests.

- [ ] **Step 6: Review the final diff for behavior-only accidents**

Run:

```bash
git diff main...HEAD -- discord_bot.py toram_discord tests/test_discord_bot.py tests/test_discord_module_boundaries.py
```

Review specifically for:

- changed user-facing strings
- changed button/select labels
- changed constants (`5`, `900`)
- changed state-clearing conditions
- changed `asyncio.to_thread` boundaries
- changed repository `try/finally close()` behavior
- duplicate implementations left in `discord_bot.py`
- accidental Discord imports added to `toram_search`/`toram_data`

If any are present, restore the pre-refactor behavior unless explicitly required by the approved design.

- [ ] **Step 7: Commit final boundary tests / verification cleanup**

```bash
git add tests/test_discord_module_boundaries.py toram_discord discord_bot.py tests/test_discord_bot.py
git commit -m "test: enforce Discord module boundaries"
```

- [ ] **Step 8: Prepare the implementation PR without merging**

Create a draft PR from the implementation branch to `main` with a body that records:

- structural-only scope
- files/modules created
- Option A compatibility decision (`discord_bot.py` facade)
- explicit no-TTL/no-UX-change boundary
- exact final commit SHA
- compile command/result
- Discord-focused test count/result
- full-suite test count/result
- confirmation that `toram_search`/`toram_data` do not import Discord modules

Do not merge the PR until the user explicitly asks.
