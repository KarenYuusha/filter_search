from __future__ import annotations

import unittest
from decimal import Decimal
from pathlib import Path

import search_items as core
from discord_bot import DiscordSessionManager, build_service_outcome_message
from toram_search.service import (
    ExpressionResultsPayload,
    ItemDetailPayload,
    ItemResultsPayload,
    SearchService,
    ServiceOutcome,
    StatResultsPayload,
    UpgradeDetailPayload,
    UpgradeResultsPayload,
)


class MustNotCallLLM:
    def complete(self, *args, **kwargs):
        raise AssertionError("deterministic search must not call Qwen")


def row(name: str, amount: float) -> core.StatRow:
    return core.StatRow(name, amount, "[]", None, None, False, 0)


class AutoOpenRepository:
    def __init__(self):
        self.altadar = core.ItemSummary(1, "Altadar", "Armor Crysta")
        self.other = core.ItemSummary(2, "Other Item", "Armor")
        self.don = core.ItemSummary(3, "Don Upgrade B", "Enhancer Crysta (Blue)")
        self.mage_a = core.ItemSummary(4, "Mage Robe", "Armor")
        self.mage_b = core.ItemSummary(5, "Mage Robe", "Armor")
        self.items = [self.altadar, self.other, self.don, self.mage_a, self.mage_b]
        self.stat_results: list[core.RankedStatItem] = []
        self.expression_results: list[core.RankedExpressionItem] = []
        self.exact_names: dict[str, list[core.ItemSummary]] = {}
        self.exact_upgrades: dict[str, list[core.ItemSummary]] = {}

    def list_items(self):
        return list(self.items)

    def list_item_types(self):
        return {item.item_type for item in self.items}

    def list_stat_names(self):
        return ["MaxHP", "Critical Rate", "Attack MP Recovery"]

    def exact_name_matches(self, query):
        return list(self.exact_names.get(query, ()))

    def exact_upgrade_name_matches(self, query):
        return list(self.exact_upgrades.get(query, ()))

    def get_item(self, item_id):
        summary = next(item for item in self.items if item.id == item_id)
        return core.ItemDetail(
            summary,
            None,
            None,
            None,
            None,
            None,
            None,
            [],
            [],
            [],
            [],
            [],
        )

    def get_upgrade_component(self, item_id):
        item = next(item for item in self.items if item.id == item_id)
        return core.UpgradeGraph(
            nodes={item.id: item},
            edges={item.id: ()},
            missing_nodes={},
        )

    def search_by_stat(self, stat_name, item_types):
        return list(self.stat_results)

    def search_by_expression(
        self,
        expression,
        item_types,
        *,
        primary_sort_ascending=False,
    ):
        return list(self.expression_results)

    def count_items_total(self):
        return len(self.items)

    def count_items_by_types(self, item_types):
        return sum(item.item_type in item_types for item in self.items)

    def count_items_with_stat(self, stat_name):
        return 0


class ServiceSingleResultAutoOpenTests(unittest.TestCase):
    def setUp(self):
        self.repository = AutoOpenRepository()
        self.service = SearchService(self.repository, llm_client=MustNotCallLLM())

    @staticmethod
    def stat_search() -> core.ParsedSearch:
        return core.ParsedSearch(
            intent="stat_search",
            raw_query="hp armor",
            stat=core.StatResolution("MaxHP", "hp", 100.0, False),
        )

    def test_one_stat_result_opens_item_detail(self):
        self.repository.stat_results = [
            core.RankedStatItem(self.repository.other, row("MaxHP", 5000), ())
        ]
        payload = self.service._materialize(self.stat_search(), {})
        self.assertIsInstance(payload, ItemDetailPayload)
        self.assertEqual(payload.detail.summary.id, self.repository.other.id)

    def test_two_stat_results_keep_results_payload(self):
        self.repository.stat_results = [
            core.RankedStatItem(self.repository.other, row("MaxHP", 5000), ()),
            core.RankedStatItem(self.repository.mage_a, row("MaxHP", 4000), ()),
        ]
        payload = self.service._materialize(self.stat_search(), {})
        self.assertIsInstance(payload, StatResultsPayload)

    def test_one_expression_result_opens_item_detail(self):
        clause = core.ResolvedClause(
            typed_stat="hp",
            stat_name="MaxHP",
            operator=">=",
            value=Decimal("1"),
        )
        expression = core.ResolvedStatExpression((core.ResolvedAndGroup((clause,)),))
        parsed = core.ParsedSearch(
            intent="stat_expression",
            raw_query="hp >= 1",
            resolved_expression=expression,
        )
        self.repository.expression_results = [
            core.RankedExpressionItem(self.repository.other, (), 5000)
        ]
        payload = self.service._materialize(parsed, {})
        self.assertIsInstance(payload, ItemDetailPayload)
        self.assertEqual(payload.detail.summary.id, self.repository.other.id)

    def test_two_expression_results_keep_results_payload(self):
        clause = core.ResolvedClause(
            typed_stat="hp",
            stat_name="MaxHP",
            operator=">=",
            value=Decimal("1"),
        )
        expression = core.ResolvedStatExpression((core.ResolvedAndGroup((clause,)),))
        parsed = core.ParsedSearch(
            intent="stat_expression",
            raw_query="hp >= 1",
            resolved_expression=expression,
        )
        self.repository.expression_results = [
            core.RankedExpressionItem(self.repository.other, (), 5000),
            core.RankedExpressionItem(self.repository.mage_a, (), 4000),
        ]
        payload = self.service._materialize(parsed, {})
        self.assertIsInstance(payload, ExpressionResultsPayload)

    def test_one_fuzzy_item_result_opens_detail(self):
        payload = self.service._materialize(
            core.ParsedSearch(
                intent="item_search",
                raw_query="altadr",
                item_query="altadr",
            ),
            {},
        )
        self.assertIsInstance(payload, ItemDetailPayload)
        self.assertEqual(payload.detail.summary.id, self.repository.altadar.id)

    def test_multiple_item_results_keep_results_payload(self):
        self.repository.exact_names["Mage Robe"] = [
            self.repository.mage_a,
            self.repository.mage_b,
        ]
        payload = self.service._materialize(
            core.ParsedSearch(
                intent="item_search",
                raw_query="Mage Robe",
                item_query="Mage Robe",
            ),
            {},
        )
        self.assertIsInstance(payload, ItemResultsPayload)
        self.assertEqual(len(payload.results), 2)

    def test_one_fuzzy_upgrade_result_opens_upgrade_detail(self):
        payload = self.service._materialize(
            core.ParsedSearch(
                intent="upgrade_search",
                raw_query="upgrade Don Upgrad B",
                item_query="Don Upgrad B",
            ),
            {},
        )
        self.assertIsInstance(payload, UpgradeDetailPayload)
        self.assertEqual(payload.selected_item_id, self.repository.don.id)

    def test_multiple_upgrade_results_keep_results_payload(self):
        second = core.ItemSummary(6, "Don Upgrade Beta", "Enhancer Crysta (Blue)")
        self.repository.items.append(second)
        service = SearchService(self.repository, llm_client=MustNotCallLLM())
        self.repository.exact_upgrades["Don Upgrade"] = [self.repository.don, second]
        payload = service._materialize(
            core.ParsedSearch(
                intent="upgrade_search",
                raw_query="upgrade Don Upgrade",
                item_query="Don Upgrade",
            ),
            {},
        )
        self.assertIsInstance(payload, UpgradeResultsPayload)
        self.assertEqual(len(payload.results), 2)

    def test_zero_stat_results_remain_results_payload(self):
        payload = self.service._materialize(self.stat_search(), {})
        self.assertIsInstance(payload, StatResultsPayload)
        self.assertEqual(payload.results, ())

    def test_unique_stat_outcome_builds_detail_without_item_selector(self):
        self.repository.stat_results = [
            core.RankedStatItem(self.repository.other, row("MaxHP", 5000), ())
        ]
        payload = self.service._materialize(self.stat_search(), {})
        outcome = ServiceOutcome("search", payload=payload)
        sessions = DiscordSessionManager()
        key = (1, 2, 3)
        session = sessions.start_query(key, "hp armor")
        embed, view, _file = build_service_outcome_message(
            outcome,
            bot_example_prefix="@Bot",
            sessions=sessions,
            key=key,
            generation=session.generation,
            database_path=Path("coryn_data/database/items.sqlite"),
        )
        self.assertEqual(embed.title, "Other Item")
        self.assertIsNotNone(view)
        self.assertFalse(
            any(
                getattr(child, "placeholder", "") == "Select an item"
                for child in view.children
            )
        )


class CliSingleResultAutoOpenTests(unittest.TestCase):
    def setUp(self):
        self.repository = AutoOpenRepository()

    @staticmethod
    def no_input(_prompt: str) -> str:
        raise AssertionError("single result should auto-open before requesting input")

    @staticmethod
    def stat_search() -> core.ParsedSearch:
        return core.ParsedSearch(
            intent="stat_search",
            raw_query="hp",
            stat=core.StatResolution("MaxHP", "hp", 100.0, False),
        )

    @staticmethod
    def expression_search() -> core.ParsedSearch:
        clause = core.ResolvedClause(
            typed_stat="hp",
            stat_name="MaxHP",
            operator=">=",
            value=Decimal("1"),
        )
        return core.ParsedSearch(
            intent="stat_expression",
            raw_query="hp >= 1",
            resolved_expression=core.ResolvedStatExpression(
                (core.ResolvedAndGroup((clause,)),)
            ),
        )

    def test_one_fuzzy_item_result_opens_without_choice(self):
        output: list[str] = []
        outcome = core._interactive_item_results(
            self.repository,
            "altadr",
            self.repository.list_items(),
            input_fn=self.no_input,
            output_fn=output.append,
        )
        self.assertEqual(outcome.kind, "selected")
        rendered = "\n".join(output)
        self.assertIn("Altadar", rendered)
        self.assertNotIn("Choose 1–5", rendered)

    def test_duplicate_exact_item_results_keep_choice(self):
        self.repository.exact_names["Mage Robe"] = [
            self.repository.mage_a,
            self.repository.mage_b,
        ]
        answers = iter(("1",))
        output: list[str] = []
        outcome = core._interactive_item_results(
            self.repository,
            "Mage Robe",
            self.repository.list_items(),
            input_fn=lambda _prompt: next(answers),
            output_fn=output.append,
        )
        self.assertEqual(outcome.kind, "selected")
        self.assertIn("Suggestions 1–2 of 2", "\n".join(output))

    def test_one_stat_result_opens_without_commands(self):
        self.repository.stat_results = [
            core.RankedStatItem(self.repository.other, row("MaxHP", 5000), ())
        ]
        output: list[str] = []
        outcome = core.interactive_stat_results(
            self.repository,
            self.stat_search(),
            input_fn=self.no_input,
            output_fn=output.append,
        )
        self.assertEqual(outcome.kind, "selected")
        rendered = "\n".join(output)
        self.assertIn("Other Item", rendered)
        self.assertNotIn("Commands: 1–5", rendered)

    def test_two_stat_results_keep_commands(self):
        self.repository.stat_results = [
            core.RankedStatItem(self.repository.other, row("MaxHP", 5000), ()),
            core.RankedStatItem(self.repository.mage_a, row("MaxHP", 4000), ()),
        ]
        answers = iter(("1",))
        output: list[str] = []
        outcome = core.interactive_stat_results(
            self.repository,
            self.stat_search(),
            input_fn=lambda _prompt: next(answers),
            output_fn=output.append,
        )
        self.assertEqual(outcome.kind, "selected")
        self.assertIn("Commands: 1–5", "\n".join(output))

    def test_one_expression_result_opens_without_commands(self):
        self.repository.expression_results = [
            core.RankedExpressionItem(self.repository.other, (), 5000)
        ]
        output: list[str] = []
        outcome = core.interactive_expression_results(
            self.repository,
            self.expression_search(),
            input_fn=self.no_input,
            output_fn=output.append,
        )
        self.assertEqual(outcome.kind, "selected")
        rendered = "\n".join(output)
        self.assertIn("Other Item", rendered)
        self.assertNotIn("Commands: 1–5", rendered)

    def test_two_expression_results_keep_commands(self):
        self.repository.expression_results = [
            core.RankedExpressionItem(self.repository.other, (), 5000),
            core.RankedExpressionItem(self.repository.mage_a, (), 4000),
        ]
        answers = iter(("1",))
        output: list[str] = []
        outcome = core.interactive_expression_results(
            self.repository,
            self.expression_search(),
            input_fn=lambda _prompt: next(answers),
            output_fn=output.append,
        )
        self.assertEqual(outcome.kind, "selected")
        self.assertIn("Commands: 1–5", "\n".join(output))

    def test_one_fuzzy_upgrade_result_opens_tree_without_choice(self):
        output: list[str] = []
        outcome = core._interactive_upgrade_results(
            self.repository,
            "Don Upgrad B",
            self.repository.list_items(),
            input_fn=self.no_input,
            output_fn=output.append,
        )
        self.assertEqual(outcome.kind, "selected")
        rendered = "\n".join(output)
        self.assertIn("Upgrade Tree — Don Upgrade B", rendered)
        self.assertNotIn("Choose 1–5", rendered)

    def test_two_upgrade_results_keep_choice(self):
        second = core.ItemSummary(6, "Don Upgrade Beta", "Enhancer Crysta (Blue)")
        self.repository.items.append(second)
        self.repository.exact_upgrades["Don Upgrade"] = [self.repository.don, second]
        answers = iter(("1",))
        output: list[str] = []
        outcome = core._interactive_upgrade_results(
            self.repository,
            "Don Upgrade",
            self.repository.list_items(),
            input_fn=lambda _prompt: next(answers),
            output_fn=output.append,
        )
        self.assertEqual(outcome.kind, "selected")
        self.assertIn("Suggestions 1–2 of 2", "\n".join(output))


if __name__ == "__main__":
    unittest.main()
