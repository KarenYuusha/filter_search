from __future__ import annotations

import ast
import subprocess
from pathlib import Path

SOURCE_COMMIT = 'c672e9589b956da26e43100841f119c308805299'

SEARCH_MODELS = '''from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from toram_data.stat_query import ResolvedClause


@dataclass(frozen=True)
class ItemSummary:
    id: int
    name: str
    item_type: str


@dataclass(frozen=True)
class ItemDetail:
    summary: ItemSummary
    sell_price: float | None
    process_material: str | None
    process_amount: float | None
    badge: str | None
    note: str | None
    page_url: str | None
    stats: list[dict[str, Any]]
    sources: list[dict[str, Any]]
    images: list[dict[str, Any]]
    upgrade_predecessors: list[ItemSummary]
    upgrade_successors: list[ItemSummary]


@dataclass(frozen=True)
class StatRow:
    stat_name: str
    amount: float
    conditions_json: str
    condition_text: str | None
    coryn_applies_to: int | None
    needs_condition_review: bool
    position: int


@dataclass(frozen=True)
class RankedStatItem:
    item: ItemSummary
    primary: StatRow
    alternatives: tuple[StatRow, ...]


@dataclass(frozen=True)
class ClauseMatch:
    clause_index: int
    clause: ResolvedClause
    rows: tuple[StatRow, ...]


@dataclass(frozen=True)
class RankedExpressionItem:
    item: ItemSummary
    matches: tuple[ClauseMatch, ...]
    primary_amount: float | None


@dataclass(frozen=True)
class UpgradeGraph:
    nodes: dict[int, ItemSummary]
    edges: dict[int, tuple[int, ...]]
    missing_nodes: dict[int, ItemSummary]
'''

subprocess.run(
    ['git', 'fetch', 'origin', SOURCE_COMMIT, '--depth=1'],
    check=True,
    stdout=subprocess.DEVNULL,
)
old_source = subprocess.check_output(
    ['git', 'show', f'{SOURCE_COMMIT}:search_items.py'],
    text=True,
)
tree = ast.parse(old_source)
repo_node = next(
    node
    for node in tree.body
    if isinstance(node, ast.ClassDef) and node.name == 'ItemRepository'
)
repo_segment = ast.get_source_segment(old_source, repo_node)
assert repo_segment is not None

repository_header = '''from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from toram_data.aliases import is_crysta_item_type, normalize_name, normalize_stat_text
from toram_data.search_models import (
    ClauseMatch,
    ItemDetail,
    ItemSummary,
    RankedExpressionItem,
    RankedStatItem,
    StatRow,
    UpgradeGraph,
)
from toram_data.stat_query import ResolvedStatExpression, compare_amount


'''

Path('toram_data/search_models.py').write_text(SEARCH_MODELS)
Path('toram_data/search_repository.py').write_text(repository_header + repo_segment + '\n')
