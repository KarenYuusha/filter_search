from __future__ import annotations

import unittest

from search_items import parse_search_query, resolve_expression_interactively, route_deterministically


class FakeRepository:
    def list_stat_names(self):
        return ["Critical Rate", "Critical Damage", "MaxHP"]

    def list_item_types(self):
        return {"Armor", "Bow"}

    def exact_name_matches(self, query):
        return []

    def exact_upgrade_name_matches(self, query):
        return []


class NoHelpService:
    def answer_direct(self, query):
        return None


class NoDatabaseService:
    def match_direct(self, query):
        return None


class NaturalGiveRankingTests(unittest.TestCase):
    def setUp(self):
        self.repository = FakeRepository()

    def test_which_armor_give_highest_crit_routes_as_ranked_stat_search(self):
        query = "which armor give highest crit"
        parsed = parse_search_query(query, self.repository)

        self.assertEqual(parsed.intent, "stat_expression")
        self.assertIsNotNone(parsed.parsed_expression)
        self.assertEqual(parsed.filter.label, "Armor")
        self.assertFalse(parsed.primary_sort_ascending)
        self.assertEqual(parsed.parsed_expression.groups[0].clauses[0].typed_stat, "crit")

        route = route_deterministically(
            query,
            self.repository,
            [],
            NoHelpService(),
            NoDatabaseService(),
        )
        self.assertEqual(route.kind, "search")

    def test_ambiguous_crit_still_requires_existing_clarification(self):
        parsed = parse_search_query("which armor give highest crit", self.repository)
        prompts: list[str] = []

        resolved = resolve_expression_interactively(
            parsed,
            self.repository,
            input_fn=lambda _prompt: "1",
            output_fn=prompts.append,
        )

        self.assertIsNotNone(resolved)
        self.assertIn('What does "crit" mean?', prompts)
        clause = resolved.resolved_expression.groups[0].clauses[0]
        self.assertEqual(clause.stat_name, "Critical Rate")


if __name__ == "__main__":
    unittest.main()
