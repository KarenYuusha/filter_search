import ast
from pathlib import Path

import unittest

import search_items

PROJECT_ROOT = Path(__file__).resolve().parents[1]
from toram_data.search_models import (
    ClauseMatch,
    ItemDetail,
    ItemSummary,
    RankedExpressionItem,
    RankedStatItem,
    StatRow,
    UpgradeGraph,
)
from toram_data.search_repository import ItemRepository


class CoreModuleBoundaryTests(unittest.TestCase):
    def test_search_items_reexports_domain_models(self):
        self.assertIs(search_items.ItemSummary, ItemSummary)
        self.assertIs(search_items.ItemDetail, ItemDetail)
        self.assertIs(search_items.StatRow, StatRow)
        self.assertIs(search_items.RankedStatItem, RankedStatItem)
        self.assertIs(search_items.ClauseMatch, ClauseMatch)
        self.assertIs(search_items.RankedExpressionItem, RankedExpressionItem)
        self.assertIs(search_items.UpgradeGraph, UpgradeGraph)

    def test_search_items_reexports_search_repository(self):
        self.assertIs(search_items.ItemRepository, ItemRepository)

    def test_search_items_reexports_ranking_symbols(self):
        from toram_search.ranking import RankedItem, rank_items

        self.assertIs(search_items.RankedItem, RankedItem)
        self.assertIs(search_items.rank_items, rank_items)

    def test_search_items_reexports_parser_symbols(self):
        from toram_search import parser as parser_module

        self.assertIs(search_items.ParsedSearch, parser_module.ParsedSearch)
        self.assertIs(search_items.StatResolution, parser_module.StatResolution)
        self.assertIs(search_items.parse_search_query, parser_module.parse_search_query)
        self.assertIs(
            search_items.parse_structured_search_request,
            parser_module.parse_structured_search_request,
        )

    def test_search_items_reexports_routing_symbols(self):
        from toram_search.routing import DeterministicRoute, route_deterministically

        self.assertIs(search_items.DeterministicRoute, DeterministicRoute)
        self.assertIs(search_items.route_deterministically, route_deterministically)

    def test_search_items_reexports_filter_symbols(self):
        from toram_data import item_filters

        self.assertIs(search_items.extract_item_filter, item_filters.extract_item_filter)
        self.assertIs(search_items.resolve_item_filter, item_filters.resolve_item_filter)
        self.assertIs(
            search_items.list_item_filter_phrases,
            item_filters.list_item_filter_phrases,
        )

    def test_package_modules_do_not_import_top_level_search_items(self):
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

    def test_toram_skills_stays_independent_of_search_discord_and_ollama(self):
        forbidden = {"search_items", "discord_bot", "toram_discord", "toram_search", "ollama"}
        offenders = []
        for path in sorted((PROJECT_ROOT / "toram_skills").glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.split(".", 1)[0] in forbidden:
                            offenders.append(str(path.relative_to(PROJECT_ROOT)))
                elif isinstance(node, ast.ImportFrom) and node.module:
                    if node.module.split(".", 1)[0] in forbidden:
                        offenders.append(str(path.relative_to(PROJECT_ROOT)))
        self.assertEqual(offenders, [])

    def test_only_embedding_adapter_imports_ollama_inside_embedding_package(self):
        offenders = []
        package = PROJECT_ROOT / "toram_skill_embeddings"
        for path in sorted(package.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            imports_ollama = False
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports_ollama |= any(alias.name.split(".", 1)[0] == "ollama" for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imports_ollama |= node.module.split(".", 1)[0] == "ollama"
            if imports_ollama and path.name != "ollama_provider.py":
                offenders.append(str(path.relative_to(PROJECT_ROOT)))
        self.assertEqual(offenders, [])

    def test_editor_repository_stays_separate(self):
        from toram_data.repository import ItemRepository as EditorItemRepository

        self.assertIsNot(ItemRepository, EditorItemRepository)
