from __future__ import annotations

import ast
from pathlib import Path

RANKING = '''from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from rapidfuzz import fuzz

from toram_data.aliases import normalize_name
from toram_data.search_models import ItemSummary

MIN_QUERY_LENGTH = 2
ITEM_FUZZY_RELEVANCE_THRESHOLD = 70.0
PAGE_SIZE = 5


@dataclass(frozen=True)
class RankedItem:
    item: ItemSummary
    score: float
    match_kind: str


def _score_item(query: str, item: ItemSummary) -> RankedItem:
    normalized_query = normalize_name(query)
    normalized_name = normalize_name(item.name)
    query_tokens = normalized_query.split()
    name_tokens = normalized_name.split()
    score = max(
        float(fuzz.WRatio(normalized_query, normalized_name)),
        float(fuzz.token_set_ratio(normalized_query, normalized_name)),
        float(fuzz.token_sort_ratio(normalized_query, normalized_name)),
        float(fuzz.ratio(normalized_query, normalized_name)),
    )
    match_kind = "fuzzy"
    if normalized_name == normalized_query:
        score, match_kind = 100.0, "exact"
    elif normalized_name.startswith(normalized_query):
        score, match_kind = max(score, 98.0), "prefix"
    elif normalized_query in normalized_name:
        score, match_kind = max(score, 95.0), "substring"
    elif query_tokens and all(token in name_tokens for token in query_tokens):
        score, match_kind = max(score, 93.0), "all_tokens"
    score -= max(len(normalized_name) - len(normalized_query), 0) * 0.001
    return RankedItem(item, score, match_kind)


def _is_relevant_ranked_item(result: RankedItem) -> bool:
    if result.match_kind in {"exact", "prefix", "substring", "all_tokens"}:
        return True
    return result.score >= ITEM_FUZZY_RELEVANCE_THRESHOLD


def rank_items(query: str, items: Iterable[ItemSummary]) -> list[RankedItem]:
    normalized_query = normalize_name(query)
    if len(normalized_query) < MIN_QUERY_LENGTH:
        return []
    results = [
        result
        for item in items
        if _is_relevant_ranked_item(result := _score_item(normalized_query, item))
    ]
    results.sort(
        key=lambda result: (
            -result.score,
            len(normalize_name(result.item.name)),
            normalize_name(result.item.name),
            result.item.id,
        )
    )
    return results


def page_results(
    results: list[RankedItem],
    *,
    page: int,
    page_size: int = PAGE_SIZE,
) -> list[RankedItem]:
    start = page * page_size
    return results[start:start + page_size]
'''

Path('toram_search/ranking.py').write_text(RANKING)

source_path = Path('search_items.py')
source = source_path.read_text()
tree = ast.parse(source)

remove_names = {'RankedItem', '_score_item', '_is_relevant_ranked_item', 'rank_items', 'page_results'}
remove_nodes = [
    node for node in tree.body
    if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name in remove_names
]
lines = source.splitlines(keepends=True)
for node in sorted(remove_nodes, key=lambda value: value.lineno, reverse=True):
    decorator_lines = [decorator.lineno for decorator in getattr(node, 'decorator_list', [])]
    start_line = min([node.lineno, *decorator_lines])
    start = start_line - 1
    end = node.end_lineno
    while end < len(lines) and lines[end].strip() == '':
        end += 1
    del lines[start:end]
source = ''.join(lines)
source = source.replace('MIN_QUERY_LENGTH = 2\n', '', 1)
source = source.replace('ITEM_FUZZY_RELEVANCE_THRESHOLD = 70.0\n', '', 1)

ranking_import = '''from toram_search.ranking import (
    ITEM_FUZZY_RELEVANCE_THRESHOLD,
    MIN_QUERY_LENGTH,
    RankedItem,
    page_results,
    rank_items,
)

'''
marker = 'from toram_search.session import FailedQueryContext, classify_screen_input\n\n'
assert marker in source
source = source.replace(marker, marker + ranking_import, 1)
source_path.write_text(source)

test_path = Path('tests/test_core_module_boundaries.py')
test_text = test_path.read_text()
anchor = '    def test_editor_repository_stays_separate(self):\n'
addition = '''    def test_search_items_reexports_ranking_symbols(self):
        from toram_search.ranking import RankedItem, rank_items

        self.assertIs(search_items.RankedItem, RankedItem)
        self.assertIs(search_items.rank_items, rank_items)

'''
assert anchor in test_text
assert 'test_search_items_reexports_ranking_symbols' not in test_text
test_path.write_text(test_text.replace(anchor, addition + anchor, 1))

relevance_path = Path('tests/test_item_search_relevance.py')
relevance_text = relevance_path.read_text()
old = 'patch("search_items._score_item", return_value=ranked)'
assert relevance_text.count(old) == 3
relevance_path.write_text(
    relevance_text.replace(old, 'patch("toram_search.ranking._score_item", return_value=ranked)')
)
