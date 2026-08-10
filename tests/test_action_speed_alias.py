from __future__ import annotations

import unittest

import search_items as core
from toram_search.service import SearchService
from toram_search.session import FailedQueryContext


class MustNotCallLLM:
    def complete(self, *args, **kwargs):
        raise AssertionError("action speed must resolve deterministically")


class AliasRepository:
    def list_items(self):
        return []

    def list_item_types(self):
        return {"Armor"}

    def list_stat_names(self):
        return ["Motion Speed %"]

    def exact_name_matches(self, query):
        return []

    def exact_upgrade_name_matches(self, query):
        return []

    def search_by_stat(self, stat_name, item_types):
        return []

    def search_by_expression(self, expression, item_types, *, primary_sort_ascending=False):
        return []

    def count_items_total(self):
        return 0

    def count_items_by_types(self, item_types):
        return 0

    def count_items_with_stat(self, stat_name):
        return 0


class ActionSpeedAliasTests(unittest.TestCase):
    def test_alias_forms_resolve_to_motion_speed(self):
        available = ["Motion Speed %"]
        for typed in ("action speed", "action speed %", "action speed%", "motion"):
            with self.subTest(typed=typed):
                resolution = core.resolve_stat_term(typed, available, allow_fuzzy=False)
                self.assertIn(resolution.status, {"exact", "alias"})
                self.assertEqual(resolution.candidates, ("Motion Speed %",))

    def test_action_speed_query_never_calls_qwen(self):
        service = SearchService(AliasRepository(), llm_client=MustNotCallLLM())
        outcome = service.handle_query(
            "find armor with action speed",
            FailedQueryContext(max_entries=3),
        )
        self.assertEqual(outcome.kind, "search")


if __name__ == "__main__":
    unittest.main()
