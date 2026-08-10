import unittest

from search_items import ItemSummary, route_deterministically


class FakeRepository:
    def list_stat_names(self):
        return ["MaxHP"]

    def list_item_types(self):
        return {"Armor"}

    def exact_upgrade_name_matches(self, query):
        return []

    def exact_name_matches(self, query):
        if query.strip().casefold() == "mage robe":
            return [
                ItemSummary(1, "Mage Robe", "Armor"),
                ItemSummary(2, "Mage Robe", "Armor"),
            ]
        return []


class NoHelpService:
    def answer_direct(self, query):
        return None


class NoDatabaseService:
    def match_direct(self, query):
        return None


class ExactItemBuildRefusalTests(unittest.TestCase):
    def setUp(self):
        self.repository = FakeRepository()

    def test_duplicate_exact_mage_item_routes_to_item_search(self):
        route = route_deterministically(
            "mage robe",
            self.repository,
            [],
            NoHelpService(),
            NoDatabaseService(),
        )

        self.assertEqual(route.kind, "search")
        self.assertIsNotNone(route.parsed)
        self.assertEqual(route.parsed.intent, "item_search")
        self.assertEqual(route.parsed.item_query, "mage robe")

    def test_non_exact_mage_request_remains_unsupported(self):
        route = route_deterministically(
            "mage gear",
            self.repository,
            [],
            NoHelpService(),
            NoDatabaseService(),
        )

        self.assertEqual(route.kind, "refuse")


if __name__ == "__main__":
    unittest.main()
