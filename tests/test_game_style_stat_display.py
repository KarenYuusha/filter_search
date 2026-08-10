from __future__ import annotations

import unittest

import search_items as core


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


if __name__ == "__main__":
    unittest.main()
