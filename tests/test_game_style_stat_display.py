from __future__ import annotations

import json
import unittest

import search_items as core


def stat(name, amount, *conditions, condition_text=None, needs_review=False):
    return {
        "stat_name": name,
        "amount": amount,
        "conditions_json": json.dumps(list(conditions)),
        "condition_text": condition_text,
        "coryn_applies_to": None,
        "needs_condition_review": needs_review,
    }


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


if __name__ == "__main__":
    unittest.main()
