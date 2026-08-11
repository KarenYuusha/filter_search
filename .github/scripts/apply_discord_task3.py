from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE_PATH = ROOT / "discord_bot.py"
VIEWS_PATH = ROOT / "toram_discord" / "views.py"

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


def source_segment(node: ast.AST) -> str:
    lines = source.splitlines(keepends=True)
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

names = [
    "run_query_sync",
    "run_confirmed_request_sync",
    "run_clarification_sync",
    "run_item_understanding_choice_sync",
    "run_pending_item_search_confirmation_sync",
    "run_item_detail_sync",
    "run_upgrade_selection_sync",
    "send_if_current",
]
nodes = [find_named(name) for name in names]

header = '''from __future__ import annotations

from pathlib import Path
from typing import Mapping

import search_items as core
from toram_search.fallback import SearchIntentRequest
from toram_search.llm import OllamaQwenClient
from toram_search.service import ItemDetailPayload, SearchService, ServiceOutcome
from toram_search.session import FailedQueryContext, PendingItemSearch

from toram_discord.sessions import DiscordSessionManager, SessionKey


'''
VIEWS_PATH.write_text(
    header + "\n\n".join(source_segment(node).rstrip() for node in nodes) + "\n",
    encoding="utf-8",
)

updated = remove_nodes(source, nodes)
anchor = '''from toram_discord.render import (
'''
# Insert after the complete render import block using the last known imported name.
render_end = "    visible_attachment_name,\n)\n"
bridge_import = '''from toram_discord.views import (
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
if bridge_import not in updated:
    updated = updated.replace(render_end, render_end + bridge_import, 1)
SOURCE_PATH.write_text(updated, encoding="utf-8")
