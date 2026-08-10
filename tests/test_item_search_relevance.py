import unittest
from unittest.mock import patch

import search_items as core
from toram_search.service import ItemResultsPayload, SearchService, UpgradeResultsPayload
from toram_search.session import FailedQueryContext


ITEMS = [
    core.ItemSummary(1, "Don", "Normal Crysta"),
    core.ItemSummary(2, "Don Profundo", "Normal Crysta"),
    core.ItemSummary(3, "Don Upgrade B", "Enhancer Crysta (Red)"),
    core.ItemSummary(4, "Completely Different Crysta", "Normal Crysta"),
    core.ItemSummary(5, "Unrelated Armor", "Armor"),
]


class FakeRepository:
    def __init__(self) -> None:
        self.items = list(ITEMS)

    def list_items(self):
        return list(self.items)

    def list_item_types(self):
        return {item.item_type for item in self.items}

    def list_stat_names(self):
        return ["Critical Rate", "MaxHP"]

    def exact_name_matches(self, query):
        normalized = core.normalize_name(query)
        return [item for item in self.items if core.normalize_name(item.name) == normalized]

    def exact_upgrade_name_matches(self, query):
        normalized = core.normalize_name(query)
        return [
            item
            for item in self.items
            if core.is_crysta_item_type(item.item_type)
            and core.normalize_name(item.name) == normalized
        ]

    def count_items_total(self):
        return len(self.items)

    def count_items_by_types(self, item_types):
        return sum(item.item_type in item_types for item in self.items)

    def count_items_with_stat(self, stat_name):
        return 0


class MustNotCallLLM:
    def complete(self, *args, **kwargs):
        raise AssertionError("name search must stay deterministic")


class ItemSearchRelevanceTests(unittest.TestCase):
    def test_rank_items_keeps_lexical_matches_and_drops_irrelevant_candidates(self):
        results = core.rank_items("Don", ITEMS)

        self.assertEqual(
            [result.item.name for result in results],
            ["Don", "Don Profundo", "Don Upgrade B"],
        )

    def test_rank_items_keeps_reasonable_fuzzy_typo_but_not_unrelated_rows(self):
        results = core.rank_items("Don Upgrad B", ITEMS)
        names = [result.item.name for result in results]

        self.assertIn("Don Upgrade B", names)
        selected = next(result for result in results if result.item.name == "Don Upgrade B")
        self.assertGreaterEqual(selected.score, 70.0)
        self.assertNotIn("Unrelated Armor", names)
        self.assertNotIn("Completely Different Crysta", names)

    def test_rank_items_drops_fuzzy_candidate_below_threshold(self):
        item = ITEMS[-1]
        ranked = core.RankedItem(item=item, score=69.999, match_kind="fuzzy")

        with patch("search_items._score_item", return_value=ranked):
            results = core.rank_items("zz", [item])

        self.assertEqual(results, [])

    def test_rank_items_keeps_fuzzy_candidate_at_threshold(self):
        item = ITEMS[-1]
        ranked = core.RankedItem(item=item, score=70.0, match_kind="fuzzy")

        with patch("search_items._score_item", return_value=ranked):
            results = core.rank_items("zz", [item])

        self.assertEqual(results, [ranked])

    def test_rank_items_keeps_strong_lexical_match_even_below_fuzzy_threshold(self):
        item = ITEMS[-1]
        ranked = core.RankedItem(item=item, score=5.0, match_kind="substring")

        with patch("search_items._score_item", return_value=ranked):
            results = core.rank_items("zz", [item])

        self.assertEqual(results, [ranked])

    def test_rank_items_returns_empty_for_unrelated_query(self):
        results = core.rank_items("qzxvjk", ITEMS)

        self.assertEqual(results, [])

    def test_item_search_payload_contains_only_relevant_candidates(self):
        service = SearchService(FakeRepository(), llm_client=MustNotCallLLM())

        outcome = service.handle_query("Donn", FailedQueryContext(max_entries=3))

        self.assertEqual(outcome.kind, "search")
        self.assertIsInstance(outcome.payload, ItemResultsPayload)
        names = {result.item.name for result in outcome.payload.results}
        self.assertIn("Don", names)
        self.assertNotIn("Unrelated Armor", names)
        self.assertNotIn("Completely Different Crysta", names)

    def test_upgrade_search_payload_contains_only_relevant_crystas(self):
        service = SearchService(FakeRepository(), llm_client=MustNotCallLLM())

        outcome = service.handle_query("upgrade Donn", FailedQueryContext(max_entries=3))

        self.assertEqual(outcome.kind, "search")
        self.assertIsInstance(outcome.payload, UpgradeResultsPayload)
        names = {result.item.name for result in outcome.payload.results}
        self.assertIn("Don", names)
        self.assertNotIn("Completely Different Crysta", names)
        self.assertNotIn("Unrelated Armor", names)

    def test_unrelated_item_search_returns_empty_payload(self):
        service = SearchService(FakeRepository(), llm_client=MustNotCallLLM())

        outcome = service.handle_query("qzxvjk", FailedQueryContext(max_entries=3))

        self.assertEqual(outcome.kind, "search")
        self.assertIsInstance(outcome.payload, ItemResultsPayload)
        self.assertEqual(outcome.payload.results, ())


if __name__ == "__main__":
    unittest.main()
