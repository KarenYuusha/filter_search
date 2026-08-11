from __future__ import annotations

import ast
from pathlib import Path

FALLBACK_ADAPTER = '''from __future__ import annotations

from toram_data.aliases import STAT_ALIASES, STAT_AMBIGUOUS_GROUPS
from toram_data.item_filters import list_item_filter_phrases
from toram_data.search_repository import ItemRepository
from toram_search.fallback import QwenFallbackService
from toram_search.help_db import DatabaseQuestionService
from toram_search.parser import SEARCH_ONLY_STAT_ALIASES, parse_structured_search_request


def build_fallback_service(
    repository: ItemRepository,
    database_service: DatabaseQuestionService,
    llm_client: object,
) -> QwenFallbackService:
    aliases: list[str] = []
    for alias, target in sorted(STAT_ALIASES.items()):
        aliases.append(f"{alias} -> {target}")
    for alias, targets in sorted(STAT_AMBIGUOUS_GROUPS.items()):
        aliases.append(f"{alias} -> {' / '.join(targets)}")
    for alias, target in sorted(SEARCH_ONLY_STAT_ALIASES.items()):
        aliases.append(f"{alias} -> {target}")

    filter_labels: list[str] = []
    seen: set[str] = set()
    for row in list_item_filter_phrases(repository.list_item_types()):
        value = f"{row.phrase} -> {row.label}"
        if value in seen:
            continue
        seen.add(value)
        filter_labels.append(value)

    return QwenFallbackService(
        llm_client,
        validate_search_request=lambda request: parse_structured_search_request(
            request,
            repository,
        ) is not None,
        validate_database_action=database_service.validate_request,
        stat_catalog=tuple(repository.list_stat_names()),
        alias_catalog=tuple(aliases),
        item_filter_catalog=tuple(filter_labels),
    )
'''
Path('toram_search/fallback_adapter.py').write_text(FALLBACK_ADAPTER)

service_path = Path('toram_search/service.py')
text = service_path.read_text()
text = text.replace('import search_items as core\n', '')
import_anchor = 'from toram_search.fallback import SearchIntentRequest\n'
imports = '''from toram_data.aliases import is_crysta_item_type, normalize_stat_text, resolve_stat_term
from toram_data.search_models import (
    ItemDetail,
    ItemSummary,
    RankedExpressionItem,
    RankedStatItem,
    UpgradeGraph,
)
from toram_data.search_repository import ItemRepository
from toram_data.stat_query import ResolvedAndGroup, ResolvedClause, ResolvedStatExpression
from toram_search.fallback_adapter import build_fallback_service
from toram_search.help_db import HelpService
from toram_search.llm import OllamaQwenClient
from toram_search.parser import (
    SEARCH_ONLY_STAT_ALIASES,
    ParsedSearch,
    StatResolution,
    format_structured_search_request,
    parse_structured_search_request,
)
from toram_search.ranking import RankedItem, rank_items
'''
assert import_anchor in text
text = text.replace(import_anchor, imports + import_anchor, 1)

replacements = {
    'core.ItemDetail': 'ItemDetail',
    'core.RankedItem': 'RankedItem',
    'core.UpgradeGraph': 'UpgradeGraph',
    'core.ParsedSearch': 'ParsedSearch',
    'core.RankedStatItem': 'RankedStatItem',
    'core.RankedExpressionItem': 'RankedExpressionItem',
    'core.ItemRepository': 'ItemRepository',
    'core.ResolvedAndGroup': 'ResolvedAndGroup',
    'core.ResolvedClause': 'ResolvedClause',
    'core.ResolvedStatExpression': 'ResolvedStatExpression',
    'core.SEARCH_ONLY_STAT_ALIASES': 'SEARCH_ONLY_STAT_ALIASES',
    'core.normalize_stat_text': 'normalize_stat_text',
    'core.resolve_stat_term': 'resolve_stat_term',
    'core.HelpService': 'HelpService',
    'core.make_database_question_service': 'make_database_question_service',
    'core.OllamaQwenClient': 'OllamaQwenClient',
    'core._format_structured_search_request': 'format_structured_search_request',
    'core.parse_structured_search_request': 'parse_structured_search_request',
    'core.StatResolution': 'StatResolution',
    'core.ItemSummary': 'ItemSummary',
    'core.rank_items': 'rank_items',
    'core.is_crysta_item_type': 'is_crysta_item_type',
}
for old, new in replacements.items():
    text = text.replace(old, new)

old_builder = '''        self.fallback_service = core._build_fallback_service(
            repository,
            self.all_items,
            self.help_service,
            self.database_service,
            self.llm_client,
        )
'''
new_builder = '''        self.fallback_service = build_fallback_service(
            repository,
            self.database_service,
            self.llm_client,
        )
'''
assert old_builder in text
text = text.replace(old_builder, new_builder, 1)

assert 'import search_items' not in text
assert 'core.' not in text
service_path.write_text(text)

# Move the actual fallback implementation out of the terminal module but retain
# a compatibility wrapper with the old call signature.
search_path = Path('search_items.py')
search_text = search_path.read_text()
tree = ast.parse(search_text)
builder = next(
    node for node in tree.body
    if isinstance(node, ast.FunctionDef) and node.name == '_build_fallback_service'
)
lines = search_text.splitlines(keepends=True)
start = builder.lineno - 1
end = builder.end_lineno
while end < len(lines) and lines[end].strip() == '':
    end += 1
wrapper = '''def _build_fallback_service(
    repository: ItemRepository,
    all_items: list[ItemSummary],
    help_service: HelpService,
    database_service: DatabaseQuestionService,
    llm_client: object,
) -> QwenFallbackService:
    del all_items, help_service
    return build_fallback_service(repository, database_service, llm_client)


'''
lines[start:end] = [wrapper]
search_text = ''.join(lines)
search_anchor = 'from toram_search.fallback import (\n'
assert search_anchor in search_text
search_text = search_text.replace(
    search_anchor,
    'from toram_search.fallback_adapter import build_fallback_service\n' + search_anchor,
    1,
)
search_path.write_text(search_text)

# Add a permanent dependency-direction regression.
test_path = Path('tests/test_core_module_boundaries.py')
test_text = test_path.read_text()
if 'import ast\n' not in test_text:
    test_text = 'import ast\nfrom pathlib import Path\n\n' + test_text
if 'PROJECT_ROOT = Path(__file__).resolve().parents[1]' not in test_text:
    marker = 'import search_items\n'
    assert marker in test_text
    test_text = test_text.replace(
        marker,
        marker + '\nPROJECT_ROOT = Path(__file__).resolve().parents[1]\n',
        1,
    )
anchor = '    def test_editor_repository_stays_separate(self):\n'
addition = '''    def test_package_modules_do_not_import_top_level_search_items(self):
        offenders = []
        for package in ("toram_data", "toram_search"):
            for path in sorted((PROJECT_ROOT / package).glob("*.py")):
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        if any(alias.name == "search_items" for alias in node.names):
                            offenders.append(str(path.relative_to(PROJECT_ROOT)))
                    elif isinstance(node, ast.ImportFrom) and node.module == "search_items":
                        offenders.append(str(path.relative_to(PROJECT_ROOT)))

        self.assertEqual(offenders, [])

'''
assert anchor in test_text
assert 'test_package_modules_do_not_import_top_level_search_items' not in test_text
test_path.write_text(test_text.replace(anchor, addition + anchor, 1))
