from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE_PATH = ROOT / "discord_bot.py"
VIEWS_PATH = ROOT / "toram_discord" / "views.py"

source = SOURCE_PATH.read_text(encoding="utf-8")
views_source = VIEWS_PATH.read_text(encoding="utf-8")
source_tree = ast.parse(source)
views_tree = ast.parse(views_source)


def node_start(node: ast.AST) -> int:
    decorators = getattr(node, "decorator_list", ())
    starts = [getattr(node, "lineno")]
    starts.extend(getattr(item, "lineno") for item in decorators)
    return min(starts)


def find_named(tree: ast.Module, name: str) -> ast.AST:
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == name:
            return node
    raise RuntimeError(f"missing definition: {name}")


def find_assignment(tree: ast.Module, name: str) -> ast.AST:
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        for target in targets:
            if isinstance(target, ast.Name) and target.id == name:
                return node
    raise RuntimeError(f"missing assignment: {name}")


def segment(text: str, node: ast.AST) -> str:
    lines = text.splitlines(keepends=True)
    return "".join(lines[node_start(node) - 1 : getattr(node, "end_lineno")]).rstrip() + "\n"


def remove_nodes(text: str, nodes: list[ast.AST]) -> str:
    working = text.splitlines(keepends=True)
    spans = sorted(
        ((node_start(node) - 1, getattr(node, "end_lineno")) for node in nodes),
        reverse=True,
    )
    for start, end in spans:
        del working[start:end]
    return "".join(working)

bridge_names = [
    "run_query_sync",
    "run_confirmed_request_sync",
    "run_clarification_sync",
    "run_item_understanding_choice_sync",
    "run_pending_item_search_confirmation_sync",
    "run_item_detail_sync",
    "run_upgrade_selection_sync",
    "send_if_current",
]
ui_names = [
    "SessionBoundView",
    "ActionButton",
    "ActionSelect",
    "_edit_component_message",
    "SearchResultsView",
    "ItemDetailView",
    "build_item_detail_message",
    "StatClarificationView",
    "ItemUnderstandingView",
    "QwenConfirmationView",
    "_make_search_view",
    "build_service_outcome_message",
    "edit_service_outcome",
]
bridge_nodes = [find_named(views_tree, name) for name in bridge_names]
ui_nodes = [find_named(source_tree, name) for name in ui_names]
timeout_node = find_assignment(source_tree, "VIEW_TIMEOUT_SECONDS")
views_import_node = next(
    node
    for node in source_tree.body
    if isinstance(node, ast.ImportFrom) and node.module == "toram_discord.views"
)

header = '''from __future__ import annotations

import asyncio
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

from toram_discord.config import bot_example_prefix
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
from toram_discord.sessions import DiscordSessionManager, SessionKey


VIEW_TIMEOUT_SECONDS = 900


'''
combined = header
combined += "\n\n".join(segment(views_source, node).rstrip() for node in bridge_nodes)
combined += "\n\n"
combined += "\n\n".join(segment(source, node).rstrip() for node in ui_nodes)
combined += "\n"
VIEWS_PATH.write_text(combined, encoding="utf-8")

updated = remove_nodes(source, ui_nodes + [timeout_node, views_import_node])
render_end = "    visible_attachment_name,\n)\n"
view_import = '''from toram_discord.views import (
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
'''
updated = updated.replace(render_end, render_end + view_import, 1)
SOURCE_PATH.write_text(updated, encoding="utf-8")
