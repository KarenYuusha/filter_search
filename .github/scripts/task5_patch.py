from __future__ import annotations

import ast
from pathlib import Path

source_path = Path('search_items.py')
source = source_path.read_text()
tree = ast.parse(source)

node_names = {
    'StatResolution',
    'ParsedSearch',
    'resolve_stat_choices',
    'resolve_stat_name',
    '_parse_stat_request',
    '_parse_negative_bare_expression',
    'parse_expression_request',
    '_resolve_item_type_for_database',
    '_resolve_stat_for_database',
    '_resolve_structured_item_filter',
    'parse_structured_search_request',
    '_format_structured_search_request',
    'build_search_stat_terms',
    'find_non_overlapping_stat_terms',
    'extract_natural_upgrade_target',
    'parse_search_query',
}

segments: dict[str, str] = {}
for node in tree.body:
    name = getattr(node, 'name', None)
    if name in node_names:
        segment = ast.get_source_segment(source, node)
        assert segment is not None, name
        segments[name] = segment
assert node_names == set(segments), node_names - set(segments)

natural_patterns = next(
    node for node in tree.body
    if isinstance(node, ast.Assign)
    and any(isinstance(target, ast.Name) and target.id == '_NATURAL_UPGRADE_PATTERNS' for target in node.targets)
)
natural_segment = ast.get_source_segment(source, natural_patterns)
assert natural_segment is not None

format_segment = segments['_format_structured_search_request'].replace(
    'def _format_structured_search_request(',
    'def format_structured_search_request(',
    1,
).replace('_format_number(', '_format_search_number(')
segments['_format_structured_search_request'] = format_segment

header = '''from __future__ import annotations

import re
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Literal

from rapidfuzz import fuzz

from toram_data.aliases import (
    STAT_ALIASES,
    STAT_AMBIGUOUS_GROUPS,
    STAT_CATEGORY_ALIASES,
    expand_stat_aliases,
    normalize_name,
    normalize_stat_text,
    resolve_stat_term,
)
from toram_data.item_filters import ItemTypeFilter, extract_item_filter
from toram_data.search_repository import ItemRepository
from toram_data.stat_query import (
    ParsedAndGroup,
    ParsedStatExpression,
    ResolvedAndGroup,
    ResolvedClause,
    ResolvedStatExpression,
    StatQuerySyntaxError,
    looks_like_stat_expression,
    parse_stat_expression,
)
from toram_search.fallback import SearchIntentRequest

FUZZY_STAT_THRESHOLD = 70.0
SEARCH_ONLY_STAT_ALIASES = {
    "aggro": "aggro %",
}
FilterResolution = ItemTypeFilter
SearchIntent = Literal[
    "exact_item",
    "item_search",
    "stat_search",
    "stat_choices",
    "guided_stat",
    "stat_expression",
    "exact_upgrade",
    "upgrade_search",
]


def _format_search_number(value: object) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(number)) if number.is_integer() else f"{number:g}"

'''

ordered_names = [
    'StatResolution',
    'ParsedSearch',
    'resolve_stat_choices',
    'resolve_stat_name',
    '_parse_stat_request',
    '_parse_negative_bare_expression',
    'parse_expression_request',
    '_resolve_item_type_for_database',
    '_resolve_stat_for_database',
    '_resolve_structured_item_filter',
    'parse_structured_search_request',
    '_format_structured_search_request',
    'build_search_stat_terms',
    'find_non_overlapping_stat_terms',
]
parser_parts = [header]
parser_parts.extend(segments[name] + '\n\n\n' for name in ordered_names)
parser_parts.append(natural_segment + '\n\n\n')
parser_parts.append(segments['extract_natural_upgrade_target'] + '\n\n\n')
parser_parts.append(segments['parse_search_query'] + '\n')
Path('toram_search/parser.py').write_text(''.join(parser_parts))

# Remove the moved classes/functions and their decorators from search_items.py.
remove_nodes = [
    node for node in tree.body
    if isinstance(node, (ast.ClassDef, ast.FunctionDef))
    and getattr(node, 'name', None) in node_names
]
# Also remove the SearchIntent alias and natural-upgrade pattern assignment.
for node in tree.body:
    if isinstance(node, ast.Assign):
        assigned = {
            target.id for target in node.targets if isinstance(target, ast.Name)
        }
        if assigned & {'SearchIntent', '_NATURAL_UPGRADE_PATTERNS'}:
            remove_nodes.append(node)

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

source = source.replace('FUZZY_STAT_THRESHOLD = 70.0\n', '', 1)
source = source.replace(
    'SEARCH_ONLY_STAT_ALIASES = {\n    "aggro": "aggro %",\n}\n',
    '',
    1,
)

parser_import = '''from toram_search.parser import (
    FUZZY_STAT_THRESHOLD,
    SEARCH_ONLY_STAT_ALIASES,
    ParsedSearch,
    SearchIntent,
    StatResolution,
    _parse_negative_bare_expression,
    _parse_stat_request,
    _resolve_item_type_for_database,
    _resolve_stat_for_database,
    _resolve_structured_item_filter,
    build_search_stat_terms,
    extract_natural_upgrade_target,
    find_non_overlapping_stat_terms,
    format_structured_search_request,
    parse_expression_request,
    parse_search_query,
    parse_structured_search_request,
    resolve_stat_choices,
    resolve_stat_name,
)

_format_structured_search_request = format_structured_search_request

'''
marker = 'from toram_search.ranking import (\n'
index = source.index(marker)
source = source[:index] + parser_import + source[index:]
source_path.write_text(source)

# Boundary tests verify canonical parser ownership while preserving compatibility.
test_path = Path('tests/test_core_module_boundaries.py')
test_text = test_path.read_text()
anchor = '    def test_editor_repository_stays_separate(self):\n'
addition = '''    def test_search_items_reexports_parser_symbols(self):
        from toram_search import parser as parser_module

        self.assertIs(search_items.ParsedSearch, parser_module.ParsedSearch)
        self.assertIs(search_items.StatResolution, parser_module.StatResolution)
        self.assertIs(search_items.parse_search_query, parser_module.parse_search_query)
        self.assertIs(
            search_items.parse_structured_search_request,
            parser_module.parse_structured_search_request,
        )

'''
assert anchor in test_text
assert 'test_search_items_reexports_parser_symbols' not in test_text
test_path.write_text(test_text.replace(anchor, addition + anchor, 1))
