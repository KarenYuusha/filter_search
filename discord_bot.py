from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Mapping, Sequence

import discord

import search_items as core
from toram_search.fallback import SearchIntentRequest
from toram_search.llm import OllamaQwenClient
from toram_search.service import (
    ExpressionResultsPayload,
    GuidedStatPayload,
    ItemDetailPayload,
    ItemResultsPayload,
    SearchPayload,
    SearchService,
    ServiceOutcome,
    StatClarificationPayload,
    StatResultsPayload,
    UpgradeDetailPayload,
    UpgradeResultsPayload,
    format_search_request,
    item_id_from_payload,
)
from toram_search.session import FailedQueryContext, PendingItemSearch
from toram_discord.config import (
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
from toram_discord.views import (
    VIEW_TIMEOUT_SECONDS,
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


if __name__ == "__main__":
    main()
