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

    def test_editor_repository_stays_separate(self):
        from toram_data.repository import ItemRepository as EditorItemRepository

        self.assertIsNot(ItemRepository, EditorItemRepository)
