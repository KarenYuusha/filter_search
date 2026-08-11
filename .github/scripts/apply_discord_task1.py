from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE_PATH = ROOT / "discord_bot.py"
PACKAGE = ROOT / "toram_discord"
PACKAGE.mkdir(exist_ok=True)

source = SOURCE_PATH.read_text(encoding="utf-8")
tree = ast.parse(source)
lines = source.splitlines(keepends=True)


def node_start(node: ast.AST) -> int:
    decorators = getattr(node, "decorator_list", ())
    values = [getattr(node, "lineno")]
    values.extend(getattr(item, "lineno") for item in decorators)
    return min(values)


def find_named(name: str) -> ast.AST:
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == name:
            return node
    raise RuntimeError(f"missing definition: {name}")


def find_assignment(name: str) -> ast.AST:
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return node
    raise RuntimeError(f"missing assignment: {name}")


def remove_nodes(text: str, nodes: list[ast.AST]) -> str:
    working = text.splitlines(keepends=True)
    spans = sorted(
        ((node_start(node) - 1, getattr(node, "end_lineno")) for node in nodes),
        reverse=True,
    )
    for start, end in spans:
        del working[start:end]
    return "".join(working)

(PACKAGE / "__init__.py").write_text(
    '"""Discord frontend package for Toram item search."""\n',
    encoding="utf-8",
)

(PACKAGE / "config.py").write_text(
    '''from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

import discord
from dotenv import load_dotenv

import search_items as core


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class DiscordBotConfig:
    token: str
    guild_ids: frozenset[int]
    database_path: Path = core.DEFAULT_DATABASE

    @property
    def guild_id(self) -> int:
        """Legacy single-guild accessor kept for compatibility."""
        if len(self.guild_ids) != 1:
            raise RuntimeError("guild_id is only available when exactly one guild is configured")
        return next(iter(self.guild_ids))


def load_project_environment(env_path: Path | None = None) -> Path:
    path = env_path if env_path is not None else PROJECT_ROOT / ".env"
    load_dotenv(dotenv_path=path, override=False)
    return path


def load_config(environ: Mapping[str, str] = os.environ) -> DiscordBotConfig:
    token = environ.get("DISCORD_BOT_TOKEN", "").strip()
    plural_guild_text = environ.get("DISCORD_GUILD_IDS", "").strip()
    legacy_guild_text = environ.get("DISCORD_GUILD_ID", "").strip()
    guild_setting = "DISCORD_GUILD_IDS" if plural_guild_text else "DISCORD_GUILD_ID"
    guild_text = plural_guild_text or legacy_guild_text

    if not token:
        raise RuntimeError("DISCORD_BOT_TOKEN is required")
    if not guild_text:
        raise RuntimeError("DISCORD_GUILD_IDS or DISCORD_GUILD_ID is required")

    guild_parts = [part.strip() for part in guild_text.split(",")]
    if any(not part.isdigit() for part in guild_parts):
        raise RuntimeError(f"{guild_setting} must contain valid Discord server IDs")
    guild_ids = frozenset(int(part) for part in guild_parts)
    if not guild_ids:
        raise RuntimeError(f"{guild_setting} must contain at least one Discord server ID")
    return DiscordBotConfig(token=token, guild_ids=guild_ids)


def build_intents() -> discord.Intents:
    intents = discord.Intents.none()
    intents.guilds = True
    intents.guild_messages = True
    return intents


def is_allowed_message(
    message,
    *,
    bot_user_id: int,
    guild_ids: Iterable[int] | None = None,
    guild_id: int | None = None,
) -> bool:
    allowed_guild_ids = guild_ids if guild_ids is not None else (() if guild_id is None else (guild_id,))
    return (
        message.guild is not None
        and message.guild.id in allowed_guild_ids
        and not getattr(message.author, "bot", False)
        and getattr(message, "webhook_id", None) is None
        and any(user.id == bot_user_id for user in getattr(message, "mentions", ()))
    )


def extract_mentioned_query(content: str, bot_user_id: int) -> str:
    cleaned = re.sub(rf"<@!?{bot_user_id}>", " ", content)
    return " ".join(cleaned.split())


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
''',
    encoding="utf-8",
)

(PACKAGE / "sessions.py").write_text(
    '''from __future__ import annotations

from dataclasses import dataclass, field

from toram_search.fallback import SearchIntentRequest
from toram_search.service import ItemDetailPayload, SearchPayload
from toram_search.session import FailedQueryContext, PendingItemSearch

SessionKey = tuple[int, int, int]


@dataclass
class DiscordSearchSession:
    generation: int
    query: str
    failed_context: FailedQueryContext
    page: int = 0
    payload: SearchPayload | None = None
    detail_payload: ItemDetailPayload | None = None
    resolution_choices: dict[tuple[int, int], str] = field(default_factory=dict)
    selected_index: int | None = None
    image_index: int = 0
    pending_requests: tuple[SearchIntentRequest, ...] = ()
    selected_request_index: int = 0
    pending_item_search: PendingItemSearch | None = None


class DiscordSessionManager:
    def __init__(self) -> None:
        self._sessions: dict[SessionKey, DiscordSearchSession] = {}

    @staticmethod
    def _clone_failed_context(previous: FailedQueryContext | None) -> FailedQueryContext:
        cloned = FailedQueryContext(max_entries=3)
        if previous is None:
            return cloned
        for attempt in previous.snapshot():
            cloned.record_failure(attempt.original_query)
            if attempt.suggested_query:
                cloned.set_latest_suggestion(attempt.suggested_query)
        return cloned

    def start_query(self, key: SessionKey, query: str) -> DiscordSearchSession:
        previous = self._sessions.get(key)
        generation = 1 if previous is None else previous.generation + 1
        session = DiscordSearchSession(
            generation=generation,
            query=query,
            failed_context=self._clone_failed_context(
                previous.failed_context if previous is not None else None
            ),
        )
        self._sessions[key] = session
        return session

    def get(self, key: SessionKey) -> DiscordSearchSession | None:
        return self._sessions.get(key)

    def is_current(self, key: SessionKey, generation: int) -> bool:
        session = self._sessions.get(key)
        return session is not None and session.generation == generation
''',
    encoding="utf-8",
)

targets = [
    find_assignment("PROJECT_ROOT"),
    find_assignment("SessionKey"),
    find_named("DiscordBotConfig"),
    find_named("DiscordSearchSession"),
    find_named("DiscordSessionManager"),
    find_named("load_project_environment"),
    find_named("load_config"),
    find_named("build_intents"),
    find_named("is_allowed_message"),
    find_named("extract_mentioned_query"),
    find_named("bot_example_prefix"),
]
updated = remove_nodes(source, targets)
updated = updated.replace("import os\n", "")
updated = updated.replace("from dataclasses import dataclass, field\n", "")
updated = updated.replace("from dotenv import load_dotenv\n", "")
anchor = "from toram_search.session import FailedQueryContext, PendingItemSearch\n"
imports = '''from toram_discord.config import (
    PROJECT_ROOT,
    DiscordBotConfig,
    bot_example_prefix,
    build_intents,
    extract_mentioned_query,
    is_allowed_message,
    load_config,
    load_project_environment,
)
from toram_discord.sessions import DiscordSearchSession, DiscordSessionManager, SessionKey
'''
if imports not in updated:
    updated = updated.replace(anchor, anchor + imports)
SOURCE_PATH.write_text(updated, encoding="utf-8")
