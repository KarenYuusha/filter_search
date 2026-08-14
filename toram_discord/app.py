from __future__ import annotations

import asyncio
import logging

import discord

from toram_search.service import StatClarificationPayload
from toram_skill_search import parse_skill_command
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
from toram_discord.skill_ui import build_skill_payload_message, run_skill_query_sync
from toram_discord.views import build_service_outcome_message, run_query_sync


logger = logging.getLogger(__name__)


async def process_tagged_query(
    message: discord.Message,
    *,
    bot_user,
    config: DiscordBotConfig,
    sessions: DiscordSessionManager,
) -> None:
    bot_user_id = bot_user.id
    query = extract_mentioned_query(message.content, bot_user_id)
    if not query:
        query = "help"
    key: SessionKey = (message.guild.id, message.channel.id, message.author.id)
    session = sessions.start_query(key, query)

    skill_query = parse_skill_command(query)
    if skill_query is not None:
        try:
            payload = await asyncio.to_thread(
                run_skill_query_sync,
                config.skill_database_path,
                skill_query,
            )
            if not sessions.is_current(key, session.generation):
                return
            session = sessions.get(key)
            if session is None:
                return
            rendered = build_skill_payload_message(
                payload,
                bot_example_prefix=bot_example_prefix(message.guild, bot_user),
                sessions=sessions,
                key=key,
                generation=session.generation,
                database_path=config.skill_database_path,
            )
            kwargs = {
                "embeds": list(rendered.embeds),
                "view": rendered.view,
                "mention_author": False,
                "allowed_mentions": discord.AllowedMentions.none(),
            }
            if rendered.files:
                kwargs["files"] = list(rendered.files)
            await message.reply(**kwargs)
        except Exception:
            logger.exception("Discord skill search failed")
            if not sessions.is_current(key, session.generation):
                return
            await message.reply(
                embed=discord.Embed(
                    title="Skill search unavailable",
                    description=(
                        "The skill search failed due to an internal error. "
                        "Item and stat search are still available."
                    ),
                ),
                view=None,
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        return

    outcome = await asyncio.to_thread(
        run_query_sync,
        config.database_path,
        query,
        session.failed_context,
    )
    if not sessions.is_current(key, session.generation):
        return
    session = sessions.get(key)
    if session is None:
        return
    if outcome.kind == "search" and not isinstance(outcome.payload, StatClarificationPayload):
        session.failed_context.clear()
    embed, view, file = build_service_outcome_message(
        outcome,
        bot_example_prefix=bot_example_prefix(message.guild, bot_user),
        sessions=sessions,
        key=key,
        generation=session.generation,
        database_path=config.database_path,
    )
    kwargs = {
        "embed": embed,
        "view": view,
        "mention_author": False,
        "allowed_mentions": discord.AllowedMentions.none(),
    }
    if file is not None:
        kwargs["file"] = file
    await message.reply(**kwargs)


def create_client(config: DiscordBotConfig) -> discord.Client:
    client = discord.Client(
        intents=build_intents(),
        allowed_mentions=discord.AllowedMentions.none(),
    )
    sessions = DiscordSessionManager()

    @client.event
    async def on_ready() -> None:
        logger.info(
            "Discord bot connected as %s for guilds %s",
            client.user,
            ", ".join(str(guild_id) for guild_id in sorted(config.guild_ids)),
        )

    @client.event
    async def on_message(message: discord.Message) -> None:
        if client.user is None:
            return
        if not is_allowed_message(
            message,
            bot_user_id=client.user.id,
            guild_ids=config.guild_ids,
        ):
            return
        try:
            await process_tagged_query(
                message,
                bot_user=client.user,
                config=config,
                sessions=sessions,
            )
        except Exception:
            logger.exception("Discord search failed")
            try:
                await message.reply(
                    "The search failed due to an internal error.",
                    mention_author=False,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except Exception:
                logger.exception("Failed to send Discord error response")

    return client


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    load_project_environment()
    config = load_config()
    client = create_client(config)
    client.run(config.token, log_handler=None)
