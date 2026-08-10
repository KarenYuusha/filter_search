from __future__ import annotations

import json
import unittest
from decimal import Decimal

import search_items as core
from discord_bot import build_item_detail_embed, build_search_results_embed
from toram_search.service import ExpressionResultsPayload, StatResultsPayload


def stat(name, amount, *conditions, condition_text=None, needs_review=False):
    return {
        "stat_name": name,
        "amount": amount,
        "conditions_json": json.dumps(list(conditions)),
        "condition_text": condition_text,
        "coryn_applies_to": None,
        "needs_condition_review": needs_review,
    }


def stat_row(name, amount, *conditions, condition_text=None):
    return core.StatRow(
        stat_name=name,
        amount=amount,
        conditions_json=json.dumps(list(conditions)),
        condition_text=condition_text,
        coryn_applies_to=None,
        needs_condition_review=False,
        position=0,
    )


def altadar_like_detail():
    return core.ItemDetail(
        summary=core.ItemSummary(1, "Altadar", "Armor Crysta"),
        sell_price=None,
        process_material=None,
        process_amount=None,
        badge=None,
        note=None,
        page_url=None,
        stats=[
            stat("STR %", 6),
            stat("VIT %", 6),
            stat("Stability %", 11),
            stat(
                "Short Range Damage %",
                11,
                "Light Armor",
                condition_text="Light Armor only",
            ),
            stat(
                "Long Range Damage %",
                11,
                "Heavy Armor",
                condition_text="Heavy Armor only",
            ),
            stat(
                "Stability %",
                -5,
                "Light Armor",
                "Heavy Armor",
                condition_text="Heavy Armor,Light Armor only",
            ),
        ],
        sources=[],
        images=[],
        upgrade_predecessors=[],
        upgrade_successors=[],
    )


class GameStyleStatFormattingTests(unittest.TestCase):
    def test_trailing_percent_moves_to_value(self):
        self.assertEqual(core.format_stat_display("STR %", 6), "STR +6%")
        self.assertEqual(core.format_stat_display("Stability %", -5), "Stability -5%")
        self.assertEqual(core.format_stat_display("Stability %", 0), "Stability 0%")

    def test_non_percent_stat_keeps_existing_numeric_format(self):
        self.assertEqual(core.format_stat_display("Critical Rate", 1), "Critical Rate +1")

    def test_motion_speed_uses_game_name(self):
        self.assertEqual(core.format_stat_display("Motion Speed %", 10), "Action Speed +10%")

    def test_prefix_percent_is_not_treated_as_trailing_unit(self):
        self.assertEqual(
            core.format_stat_display("% Stronger Against Earth", 10),
            "% Stronger Against Earth +10",
        )

    def test_unavailable_presence_behavior_is_preserved(self):
        self.assertEqual(core.format_stat_display("Tumble Unavailable", 1), "Tumble Unavailable")
        self.assertEqual(core.format_stat_display("Tumble Unavailable", 0), "Tumble Unavailable 0")


class GameStyleConditionTests(unittest.TestCase):
    def test_known_equipment_conditions_use_full_with_wording(self):
        cases = (
            (
                stat("STR", 1, "Light Armor", condition_text="Light Armor only"),
                "With Light Armor",
            ),
            (
                stat("STR", 1, "Heavy Armor", condition_text="Heavy Armor only"),
                "With Heavy Armor",
            ),
            (
                stat("STR", 1, "1 Handed Sword", condition_text="1 Handed Sword only"),
                "With 1-Handed Sword",
            ),
            (
                stat("STR", 1, "2 Handed Sword", condition_text="2 Handed Sword only"),
                "With 2-Handed Sword",
            ),
        )
        for row, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(core.format_condition_display(row), expected)

    def test_real_snake_case_condition_codes_use_game_style_names(self):
        light = stat(
            "Short Range Damage %",
            11,
            "light_armor",
            condition_text="Light Armor only",
        )
        shared = stat(
            "Stability %",
            -5,
            "heavy_armor",
            "light_armor",
            condition_text="Heavy Armor,Light Armor only",
        )
        self.assertEqual(core.format_condition_display(light), "With Light Armor")
        self.assertEqual(
            core.format_condition_display(shared),
            "With Heavy Armor / Light Armor",
        )

    def test_real_internal_equipment_tokens_use_game_style_names(self):
        cases = (
            (("one_handed_sword",), "1-Handed Sword only", "With 1-Handed Sword"),
            (("two_handed_sword",), "2-Handed Sword only", "With 2-Handed Sword"),
            (("additional",), "Additional Gear only", "With Additional Gear"),
            (("special",), "Special Gear only", "With Special Gear"),
            (
                ("knuckles", "magic_device", "staff", "bowgun", "bow", "two_handed_sword", "one_handed_sword"),
                "Knuckle,Magic Device,Staff,Bowgun,Bow,2-Handed Sword,1-Handed Sword only",
                "With Knuckles / Magic Device / Staff / Bowgun / Bow / 2-Handed Sword / 1-Handed Sword",
            ),
        )
        for conditions, condition_text, expected in cases:
            with self.subTest(condition_text=condition_text):
                row = stat("STR", 1, *conditions, condition_text=condition_text)
                self.assertEqual(core.format_condition_display(row), expected)

    def test_verified_real_equipment_vocabulary_is_explicitly_mapped(self):
        cases = (
            ("Additional Gear only", "With Additional Gear"),
            ("Armor only", "With Armor"),
            ("Dual Swords only", "With Dual Swords"),
            ("Knuckle only", "With Knuckles"),
            ("Ninjutsu Scroll only", "With Ninjutsu Scroll"),
            ("Special Gear only", "With Special Gear"),
        )
        for condition_text, expected in cases:
            with self.subTest(condition_text=condition_text):
                self.assertEqual(
                    core.format_condition_display(
                        stat("STR", 1, condition_text=condition_text)
                    ),
                    expected,
                )

    def test_non_equipment_event_only_condition_is_preserved(self):
        row = stat("STR", 1, condition_text="Event only")
        self.assertEqual(core.format_condition_display(row), "Event only")

    def test_unknown_only_condition_is_preserved(self):
        row = stat("STR", 1, condition_text="Special state only")
        self.assertEqual(core.format_condition_display(row), "Special state only")

    def test_terminal_full_detail_groups_shared_stat_under_both_sections(self):
        rendered = core.render_item(altadar_like_detail())
        self.assertIn("- STR +6%", rendered)
        self.assertIn("With Light Armor:", rendered)
        self.assertIn("With Heavy Armor:", rendered)
        self.assertEqual(rendered.count("- Stability -5%"), 2)
        self.assertNotIn("Heavy Armor,Light Armor only", rendered)
        self.assertLess(rendered.index("- STR +6%"), rendered.index("With Light Armor:"))
        self.assertLess(rendered.index("With Light Armor:"), rendered.index("With Heavy Armor:"))

    def test_compact_multi_equipment_condition_uses_single_with_prefix(self):
        row = stat(
            "Stability %",
            -5,
            "Light Armor",
            "Heavy Armor",
            condition_text="Heavy Armor,Light Armor only",
        )
        self.assertEqual(
            core.format_condition_display(row),
            "With Light Armor / Heavy Armor",
        )


class GameStyleFrontendParityTests(unittest.TestCase):
    def test_discord_full_detail_uses_one_grouped_stats_field(self):
        embed = build_item_detail_embed(altadar_like_detail())
        stats_fields = [field for field in embed.fields if field.name == "Stats"]
        self.assertEqual(len(stats_fields), 1)
        stats_value = stats_fields[0].value
        self.assertIn("STR +6%", stats_value)
        self.assertIn("**With Light Armor**", stats_value)
        self.assertIn("**With Heavy Armor**", stats_value)
        self.assertEqual(stats_value.count("Stability -5%"), 2)
        self.assertNotIn("Heavy Armor,Light Armor only", stats_value)

    def test_terminal_stat_result_keeps_multi_condition_compact(self):
        shared = stat_row(
            "Stability %",
            -5,
            "Light Armor",
            "Heavy Armor",
            condition_text="Heavy Armor,Light Armor only",
        )
        result = core.RankedStatItem(
            item=core.ItemSummary(2, "Compact Item", "Armor"),
            primary=shared,
            alternatives=(),
        )
        rendered = core.render_stat_results("Stability %", None, [result], 0)
        self.assertIn(
            "Stability -5% [With Light Armor / Heavy Armor]",
            rendered,
        )
        self.assertNotIn("Condition: Heavy Armor,Light Armor only", rendered)

    def test_discord_stat_result_keeps_multi_condition_compact(self):
        shared = stat_row(
            "Stability %",
            -5,
            "Light Armor",
            "Heavy Armor",
            condition_text="Heavy Armor,Light Armor only",
        )
        result = core.RankedStatItem(
            item=core.ItemSummary(3, "Compact Item", "Armor"),
            primary=shared,
            alternatives=(),
        )
        parsed = core.ParsedSearch(
            intent="stat_search",
            raw_query="stability armor",
            stat=core.StatResolution("Stability %", "stability", 100.0, False),
        )
        embed = build_search_results_embed(StatResultsPayload(parsed, (result,)), 0)
        self.assertIn(
            "Stability -5% [With Light Armor / Heavy Armor]",
            embed.description or "",
        )
        self.assertNotIn("Condition:", embed.description or "")

    def test_expression_results_use_action_speed_and_full_weapon_name(self):
        clause = core.ResolvedClause(
            typed_stat="action speed",
            stat_name="Motion Speed %",
            operator=">=",
            value=Decimal("1"),
        )
        expression = core.ResolvedStatExpression((core.ResolvedAndGroup((clause,)),))
        row = stat_row(
            "Motion Speed %",
            10,
            "1 Handed Sword",
            condition_text="1 Handed Sword only",
        )
        result = core.RankedExpressionItem(
            item=core.ItemSummary(4, "Speed Item", "Armor"),
            matches=(core.ClauseMatch(0, clause, (row,)),),
            primary_amount=10,
        )
        parsed = core.ParsedSearch(
            intent="stat_expression",
            raw_query="action speed >= 1 armor",
            resolved_expression=expression,
        )
        terminal = core.render_expression_results(expression, None, [result], 0)
        self.assertIn("Action Speed +10% [With 1-Handed Sword]", terminal)
        embed = build_search_results_embed(ExpressionResultsPayload(parsed, (result,)), 0)
        self.assertIn(
            "Action Speed +10% [With 1-Handed Sword]",
            embed.description or "",
        )


if __name__ == "__main__":
    unittest.main()
