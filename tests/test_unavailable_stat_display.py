from __future__ import annotations

import unittest
from decimal import Decimal

import search_items as core
from discord_bot import build_item_detail_embed, build_search_results_embed
from toram_search.service import ExpressionResultsPayload, StatResultsPayload


def make_stat_row(stat_name: str, amount: float) -> core.StatRow:
    return core.StatRow(
        stat_name=stat_name,
        amount=amount,
        conditions_json="[]",
        condition_text=None,
        coryn_applies_to=None,
        needs_condition_review=False,
        position=0,
    )


def make_detail(*stats: tuple[str, float]) -> core.ItemDetail:
    return core.ItemDetail(
        summary=core.ItemSummary(1, "Flag Test Item", "Armor"),
        sell_price=None,
        process_material=None,
        process_amount=None,
        badge=None,
        note=None,
        page_url=None,
        stats=[
            {
                "stat_name": stat_name,
                "amount": amount,
                "conditions_json": "[]",
                "condition_text": None,
                "coryn_applies_to": None,
                "needs_condition_review": False,
            }
            for stat_name, amount in stats
        ],
        sources=[],
        images=[],
        upgrade_predecessors=[],
        upgrade_successors=[],
    )


class UnavailableStatDisplayTests(unittest.TestCase):
    def test_terminal_item_detail_hides_true_unavailable_amount_but_keeps_numeric_plus_one(self):
        rendered = core.render_item(
            make_detail(("Tumble Unavailable", 1), ("STR", 1))
        )
        self.assertIn("- Tumble Unavailable\n", rendered)
        self.assertNotIn("Tumble Unavailable +1", rendered)
        self.assertIn("- STR +1", rendered)

    def test_discord_item_detail_hides_true_unavailable_amount_but_keeps_numeric_plus_one(self):
        embed = build_item_detail_embed(
            make_detail(("Flinch Unavailable", 1), ("Critical Rate", 1))
        )
        stats = next(field.value for field in embed.fields if field.name == "Stats")
        self.assertIn("Flinch Unavailable", stats)
        self.assertNotIn("Flinch Unavailable +1", stats)
        self.assertIn("Critical Rate +1", stats)

    def test_terminal_and_discord_stat_results_hide_true_unavailable_amount(self):
        row = make_stat_row("Stun Unavailable", 1)
        result = core.RankedStatItem(
            item=core.ItemSummary(2, "Result Item", "Armor"),
            primary=row,
            alternatives=(),
        )
        terminal = core.render_stat_results("Stun Unavailable", None, [result], 0)
        self.assertIn("Stun Unavailable", terminal)
        self.assertNotIn("Stun Unavailable +1", terminal)

        parsed = core.ParsedSearch(
            intent="stat_search",
            raw_query="stun unavailable",
            stat=core.StatResolution("Stun Unavailable", "stun unavailable", 100.0, False),
        )
        embed = build_search_results_embed(StatResultsPayload(parsed, (result,)), 0)
        self.assertIn("Stun Unavailable", embed.description or "")
        self.assertNotIn("Stun Unavailable +1", embed.description or "")

    def test_terminal_and_discord_expression_results_hide_true_unavailable_amount(self):
        clause = core.ResolvedClause(
            typed_stat="tumble unavailable",
            stat_name="Tumble Unavailable",
            operator="=",
            value=Decimal("1"),
        )
        expression = core.ResolvedStatExpression((core.ResolvedAndGroup((clause,)),))
        row = make_stat_row("Tumble Unavailable", 1)
        match = core.ClauseMatch(0, clause, (row,))
        result = core.RankedExpressionItem(
            item=core.ItemSummary(3, "Expression Item", "Armor"),
            matches=(match,),
            primary_amount=1,
        )

        terminal = core.render_expression_results(expression, None, [result], 0)
        self.assertIn("Tumble Unavailable", terminal)
        self.assertNotIn("Tumble Unavailable +1", terminal)

        parsed = core.ParsedSearch(
            intent="stat_expression",
            raw_query="tumble unavailable = 1",
            resolved_expression=expression,
        )
        embed = build_search_results_embed(ExpressionResultsPayload(parsed, (result,)), 0)
        self.assertIn("Tumble Unavailable", embed.description or "")
        self.assertNotIn("Tumble Unavailable +1", embed.description or "")

    def test_unavailable_zero_remains_visible_instead_of_becoming_a_true_flag(self):
        terminal = core.render_item(make_detail(("Tumble Unavailable", 0)))
        self.assertIn("- Tumble Unavailable 0", terminal)

        embed = build_item_detail_embed(make_detail(("Tumble Unavailable", 0)))
        stats = next(field.value for field in embed.fields if field.name == "Stats")
        self.assertIn("Tumble Unavailable 0", stats)


if __name__ == "__main__":
    unittest.main()
