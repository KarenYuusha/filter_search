from __future__ import annotations

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
