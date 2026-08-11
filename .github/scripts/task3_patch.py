from __future__ import annotations

import ast
from pathlib import Path

SEARCH_MODELS = '''


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

BOUNDARY_TESTS = '''import unittest

import search_items
from toram_data.models import (
    ClauseMatch,
    ItemDetail,
    ItemSummary,
    RankedExpressionItem,
    RankedStatItem,
    StatRow,
    UpgradeGraph,
)
from toram_data.repository import ItemRepository


class CoreModuleBoundaryTests(unittest.TestCase):
    def test_search_items_reexports_domain_models(self):
        self.assertIs(search_items.ItemSummary, ItemSummary)
        self.assertIs(search_items.ItemDetail, ItemDetail)
        self.assertIs(search_items.StatRow, StatRow)
        self.assertIs(search_items.RankedStatItem, RankedStatItem)
        self.assertIs(search_items.ClauseMatch, ClauseMatch)
        self.assertIs(search_items.RankedExpressionItem, RankedExpressionItem)
        self.assertIs(search_items.UpgradeGraph, UpgradeGraph)

    def test_search_items_reexports_repository(self):
        self.assertIs(search_items.ItemRepository, ItemRepository)
'''

source_path = Path('search_items.py')
source = source_path.read_text()
tree = ast.parse(source)

repo_node = next(
    node
    for node in tree.body
    if isinstance(node, ast.ClassDef) and node.name == 'ItemRepository'
)
repo_segment = ast.get_source_segment(source, repo_node)
assert repo_segment is not None

repository_header = '''from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from toram_data.aliases import is_crysta_item_type, normalize_name, normalize_stat_text
from toram_data.models import (
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

models_path = Path('toram_data/models.py')
models_text = models_path.read_text()
assert 'class ConditionDraft:' in models_text
assert 'class ItemDraft:' in models_text
assert 'class ItemSummary:' not in models_text
models_text = models_text.replace(
    'from typing import Literal\n',
    'from typing import Any, Literal\n\nfrom toram_data.stat_query import ResolvedClause\n',
    1,
)
models_path.write_text(models_text.rstrip() + SEARCH_MODELS + '\n')

Path('toram_data/repository.py').write_text(repository_header + repo_segment + '\n')
Path('tests/test_core_module_boundaries.py').write_text(BOUNDARY_TESTS)

moved_names = {
    'ItemSummary',
    'ItemDetail',
    'StatRow',
    'RankedStatItem',
    'ClauseMatch',
    'RankedExpressionItem',
    'UpgradeGraph',
    'ItemRepository',
}
remove_nodes = [
    node
    for node in tree.body
    if isinstance(node, ast.ClassDef) and node.name in moved_names
]
lines = source.splitlines(keepends=True)
for node in sorted(remove_nodes, key=lambda value: value.lineno, reverse=True):
    start = node.lineno - 1
    end = node.end_lineno
    while end < len(lines) and lines[end].strip() == '':
        end += 1
    del lines[start:end]
source = ''.join(lines)

imports = '''from toram_data.models import (
    ClauseMatch,
    ItemDetail,
    ItemSummary,
    RankedExpressionItem,
    RankedStatItem,
    StatRow,
    UpgradeGraph,
)
from toram_data.repository import ItemRepository

'''
marker = 'FilterResolution = ItemTypeFilter\n'
assert marker in source
source = source.replace(marker, imports + marker, 1)
source_path.write_text(source)
