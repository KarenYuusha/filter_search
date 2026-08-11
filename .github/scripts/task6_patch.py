from __future__ import annotations

import ast
from pathlib import Path

source_path = Path('search_items.py')
source = source_path.read_text()
tree = ast.parse(source)

node_names = {
    'DeterministicRoute',
    '_try_simple_ranking_search',
    'make_database_question_service',
    '_parsed_expression_has_unknown_stats',
    '_looks_like_failed_stat_query',
    '_contains_out_of_scope_build_concept',
    '_looks_like_assistant_question',
    'route_deterministically',
}

segments: dict[str, str] = {}
for node in tree.body:
    name = getattr(node, 'name', None)
    if name in node_names:
        segment = ast.get_source_segment(source, node)
        assert segment is not None, name
        if isinstance(node, ast.ClassDef) and node.decorator_list:
            segment = '@dataclass(frozen=True)\n' + segment
        segments[name] = segment
assert node_names == set(segments), node_names - set(segments)

header = '''from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from toram_data.aliases import normalize_name, normalize_stat_text, resolve_stat_term
from toram_data.item_filters import extract_item_filter
from toram_data.search_models import ItemSummary
from toram_data.search_repository import ItemRepository
from toram_search.help_db import DatabaseActionRequest, DatabaseQuestionService, HelpService
from toram_search.parser import (
    SEARCH_ONLY_STAT_ALIASES,
    ParsedSearch,
    _resolve_item_type_for_database,
    _resolve_stat_for_database,
    build_search_stat_terms,
    find_non_overlapping_stat_terms,
    parse_search_query,
)

'''
ordered = [
    'DeterministicRoute',
    '_parsed_expression_has_unknown_stats',
    '_try_simple_ranking_search',
    'make_database_question_service',
    '_looks_like_failed_stat_query',
    '_contains_out_of_scope_build_concept',
    '_looks_like_assistant_question',
    'route_deterministically',
]
Path('toram_search/routing.py').write_text(
    header + '\n\n\n'.join(segments[name] for name in ordered) + '\n'
)

remove_nodes = [
    node for node in tree.body
    if isinstance(node, (ast.ClassDef, ast.FunctionDef))
    and getattr(node, 'name', None) in node_names
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

routing_import = '''from toram_search.routing import (
    DeterministicRoute,
    _contains_out_of_scope_build_concept,
    _looks_like_assistant_question,
    _looks_like_failed_stat_query,
    _parsed_expression_has_unknown_stats,
    _try_simple_ranking_search,
    make_database_question_service,
    route_deterministically,
)

'''
marker = 'from toram_search.session import FailedQueryContext, classify_screen_input\n\n'
assert marker in source
source = source.replace(marker, marker + routing_import, 1)
source_path.write_text(source)

service_path = Path('toram_search/service.py')
service_text = service_path.read_text()
service_marker = 'from toram_search.reconstruction import try_reconstruct_simple_search\n'
assert service_marker in service_text
service_text = service_text.replace(
    service_marker,
    service_marker + 'from toram_search.routing import route_deterministically\n',
    1,
)
assert 'core.route_deterministically(' in service_text
service_text = service_text.replace('core.route_deterministically(', 'route_deterministically(')
service_path.write_text(service_text)

test_path = Path('tests/test_core_module_boundaries.py')
test_text = test_path.read_text()
anchor = '    def test_editor_repository_stays_separate(self):\n'
addition = '''    def test_search_items_reexports_routing_symbols(self):
        from toram_search.routing import DeterministicRoute, route_deterministically

        self.assertIs(search_items.DeterministicRoute, DeterministicRoute)
        self.assertIs(search_items.route_deterministically, route_deterministically)

'''
assert anchor in test_text
assert 'test_search_items_reexports_routing_symbols' not in test_text
test_path.write_text(test_text.replace(anchor, addition + anchor, 1))
