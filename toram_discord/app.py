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
from toram_discord.database_chat import (
    build_database_chat_help_embed,
    build_mixed_chat_message,
    build_skill_chat_embed,
    is_database_chat_candidate,
    run_database_chat_sync,
)
from toram_discord.sessions import DiscordSessionManager, SessionKey
from toram_discord.skill_chat_ui import (
    build_skill_chat_detail_message,
    run_skill_detail_by_id_sync,
)
from toram_discord.skill_ui import build_skill_payload_message, run_skill_query_sync
from toram_discord.views import build_service_outcome_message, run_query_sync


logger = logging.getLogger(__name__)
RESET_QUERIES = frozenset({"reset", "clear", "new search"})


def _is_reset_query(query: str) -> bool:
    return " ".join(str(query).casefold().split()) in RESET_QUERIES


async def _reply_item_outcome(
    message,
    outcome,
    *,
    bot_user,
    config: DiscordBotConfig,
    sessions: DiscordSessionManager,
    key: SessionKey,
    session,
) -> None:
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


async def _reply_skill_chat_result(
    message,
    result,
    *,
    config: DiscordBotConfig,
    sessions: DiscordSessionManager,
    key: SessionKey,
    session,
) -> None:
    if result.kind == "answer" and len(result.skill_ids) == 1:
        try:
            detail_payload = await asyncio.to_thread(
                run_skill_detail_by_id_sync,
                config.skill_database_path,
                result.skill_ids[0],
            )
            rendered = build_skill_chat_detail_message(
                detail_payload,
                result.text,
                sessions=sessions,
                key=key,
                generation=session.generation,
            )
        except Exception:
            logger.debug(
                "rich skill chat detail unavailable; using compact chat embed",
                exc_info=True,
            )
        else:
            kwargs = {
                "embeds": list(rendered.embeds),
                "view": rendered.view,
                "mention_author": False,
                "allowed_mentions": discord.AllowedMentions.none(),
            }
            if rendered.files:
                kwargs["files"] = list(rendered.files)
            await message.reply(**kwargs)
            return

    await message.reply(
        embed=build_skill_chat_embed(result),
        view=None,
        mention_author=False,
        allowed_mentions=discord.AllowedMentions.none(),
    )


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

    if _is_reset_query(query):
        sessions.clear_chat_context(key)
        session.failed_context.clear()
        await message.reply(
            content="Search context cleared. Start a new item, skill, or database question.",
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return

    if query.casefold() == "help":
        await message.reply(
            embed=build_database_chat_help_embed(bot_example_prefix(message.guild, bot_user)),
            view=None,
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return

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

    if is_database_chat_candidate(query, session.chat_context):
        chat_outcome = await asyncio.to_thread(
            run_database_chat_sync,
            config.database_path,
            config.skill_database_path,
            query,
            session.chat_context,
            skill_rag_model=config.skill_rag_model,
            ollama_host=config.ollama_host,
            skill_rag_top_k=config.skill_rag_top_k,
            skill_rag_max_context_chars=config.skill_rag_max_context_chars,
            skill_rag_max_output_tokens=config.skill_rag_max_output_tokens,
            skill_rag_keep_alive=config.skill_rag_keep_alive,
        )
        if not sessions.is_current(key, session.generation):
            return
        session = sessions.get(key)
        if session is None:
            return
        if chat_outcome.kind == "item" and chat_outcome.item_outcome is not None:
            await _reply_item_outcome(
                message,
                chat_outcome.item_outcome,
                bot_user=bot_user,
                config=config,
                sessions=sessions,
                key=key,
                session=session,
            )
            return
        if chat_outcome.kind == "skill" and chat_outcome.skill_result is not None:
            await _reply_skill_chat_result(
                message,
                chat_outcome.skill_result,
                config=config,
                sessions=sessions,
                key=key,
                session=session,
            )
            return
        if chat_outcome.kind == "mixed":
            embed, view = build_mixed_chat_message(
                chat_outcome,
                sessions=sessions,
                key=key,
                generation=session.generation,
            )
            await message.reply(
                embed=embed,
                view=view,
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
    await _reply_item_outcome(
        message,
        outcome,
        bot_user=bot_user,
        config=config,
        sessions=sessions,
        key=key,
        session=session,
    )


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
