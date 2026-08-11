from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE_PATH = ROOT / "discord_bot.py"
APP_PATH = ROOT / "toram_discord" / "app.py"

source = SOURCE_PATH.read_text(encoding="utf-8")
tree = ast.parse(source)


def node_start(node: ast.AST) -> int:
    decorators = getattr(node, "decorator_list", ())
    starts = [getattr(node, "lineno")]
    starts.extend(getattr(item, "lineno") for item in decorators)
    return min(starts)


def find_named(name: str) -> ast.AST:
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == name:
            return node
    raise RuntimeError(f"missing definition: {name}")


def segment(node: ast.AST) -> str:
    lines = source.splitlines(keepends=True)
    return "".join(lines[node_start(node) - 1 : getattr(node, "end_lineno")]).rstrip() + "\n"

app_nodes = [find_named(name) for name in ("process_tagged_query", "create_client", "main")]

app_header = '''from __future__ import annotations

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


'''
APP_PATH.write_text(
    app_header + "\n\n".join(segment(node).rstrip() for node in app_nodes) + "\n",
    encoding="utf-8",
)

facade = '''from __future__ import annotations

from toram_discord.app import create_client, main, process_tagged_query
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
from toram_discord.sessions import DiscordSearchSession, DiscordSessionManager, SessionKey
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


if __name__ == "__main__":
    main()
'''
SOURCE_PATH.write_text(facade, encoding="utf-8")
