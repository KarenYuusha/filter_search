from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE_PATH = ROOT / "discord_bot.py"
RENDER_PATH = ROOT / "toram_discord" / "render.py"

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


def find_assignment(name: str) -> ast.AST:
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return node
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == name:
                return node
    raise RuntimeError(f"missing assignment: {name}")


def source_segment(node: ast.AST) -> str:
    lines = source.splitlines(keepends=True)
    start = node_start(node) - 1
    end = getattr(node, "end_lineno")
    return "".join(lines[start:end]).rstrip() + "\n"


def remove_nodes(text: str, nodes: list[ast.AST]) -> str:
    working = text.splitlines(keepends=True)
    spans = sorted(
        ((node_start(node) - 1, getattr(node, "end_lineno")) for node in nodes),
        reverse=True,
    )
    for start, end in spans:
        del working[start:end]
    return "".join(working)

names = [
    "truncate_discord_text",
    "visible_attachment_name",
    "valid_local_image_paths",
    "_format_number",
    "_condition_label",
    "_filter_label",
    "_safe_field",
    "_result_count",
    "_result_item",
    "is_upgrade_suggestion_payload",
    "_result_lines",
    "build_search_results_embed",
    "build_upgrade_detail_embed",
    "build_item_detail_embed",
    "build_help_embed",
    "_build_text_embed",
    "build_clarification_embed",
    "build_item_understanding_embed",
    "build_qwen_confirmation_embed",
]
nodes = [find_named(name) for name in names]
page_size_node = find_assignment("PAGE_SIZE")

header = '''from __future__ import annotations

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


'''
RENDER_PATH.write_text(
    header + "\n\n".join(source_segment(node).rstrip() for node in nodes) + "\n",
    encoding="utf-8",
)

updated = remove_nodes(source, nodes + [page_size_node])
# Render-only stdlib imports are no longer needed by the remaining module.
updated = updated.replace("import re\n", "")
updated = updated.replace("from typing import Iterable, Mapping, Sequence\n", "from typing import Mapping, Sequence\n")

anchor = "from toram_discord.sessions import DiscordSearchSession, DiscordSessionManager, SessionKey\n"
render_import = '''from toram_discord.render import (
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
'''
if render_import not in updated:
    updated = updated.replace(anchor, anchor + render_import)
SOURCE_PATH.write_text(updated, encoding="utf-8")
