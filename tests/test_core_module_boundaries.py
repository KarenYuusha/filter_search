import unittest

import search_items
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

    def test_editor_repository_stays_separate(self):
        from toram_data.repository import ItemRepository as EditorItemRepository

        self.assertIsNot(ItemRepository, EditorItemRepository)
