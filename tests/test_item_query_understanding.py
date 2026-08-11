import unittest

from toram_search.understanding import understand_item_query


STATS = ["Critical Rate", "Critical Damage", "MaxHP", "Weapon ATK"]
TYPES = {
    "Weapon Crysta",
    "Enhancer Crysta (Red)",
    "Armor",
    "Bow",
}


class ItemQueryUnderstandingTests(unittest.TestCase):
    def understand(self, query, confirmed_choices=()):
        return understand_item_query(
            query,
            available_stats=STATS,
            available_item_types=TYPES,
            confirmed_choices=confirmed_choices,
        )

    def test_unique_alias_meaning_is_executable(self):
        result = self.understand("xtall cr weapon")
        self.assertEqual(result.decision, "execute")
        self.assertEqual(result.canonical_query, "cr wp xtal")
        self.assertEqual(result.unresolved_tokens, ())
        self.assertEqual(
            [part.display_label for part in result.resolved_parts],
            ["Critical Rate", "Weapon Crysta + Red Enhancer"],
        )

    def test_known_crit_ambiguity_is_structured(self):
        result = self.understand("crit xtal weapon")
        self.assertEqual(result.decision, "clarify")
        issue = result.uncertainties[0]
        self.assertEqual(issue.mode, "choose")
        self.assertEqual(issue.reason, "AMBIGUOUS_STAT")
        self.assertEqual(
            [choice.value for choice in issue.choices],
            ["Critical Rate", "Critical Damage"],
        )

    def test_unknown_word_stays_visible(self):
        result = self.understand("cr weapon xtal blah")
        self.assertEqual(result.decision, "suggest")
        self.assertEqual(result.unresolved_tokens, ("blah",))
        self.assertEqual(result.suggested_query, "cr wp xtal")

    def test_highest_is_not_silently_discarded(self):
        result = self.understand("highest xtall cr weapon")
        self.assertEqual(result.decision, "suggest")
        self.assertEqual(result.unresolved_tokens, ("highest",))
        self.assertEqual(result.suggested_query, "cr wp xtal")

    def test_two_stats_do_not_gain_implicit_boolean_semantics(self):
        result = self.understand("cr hp weapon xtal")
        self.assertEqual(result.decision, "fallback")
        self.assertIsNone(result.suggested_query)

    def test_oversized_input_fails_closed(self):
        query = " ".join(["show"] * 30 + ["cr", "weapon", "xtal"])
        result = self.understand(query)
        self.assertEqual(result.decision, "fallback")
        self.assertIn("UNSAFE_SHAPE", result.reasons)

    def test_fuzzy_stat_requires_confirmation(self):
        result = self.understand("weapon xtal crtical rate")
        self.assertEqual(result.decision, "confirm")
        issue = result.uncertainties[0]
        self.assertEqual(issue.reason, "FUZZY_STAT")
        self.assertEqual([choice.value for choice in issue.choices], ["Critical Rate"])

    def test_fuzzy_filter_requires_confirmation(self):
        result = self.understand("cr wepon xtal")
        self.assertEqual(result.decision, "confirm")
        self.assertEqual(result.uncertainties[0].reason, "FUZZY_FILTER")

    def test_semantic_ambiguity_precedes_fuzzy_filter(self):
        result = self.understand("crit wepon xtal")
        self.assertEqual(result.decision, "clarify")
        self.assertEqual(
            [issue.reason for issue in result.uncertainties],
            ["AMBIGUOUS_STAT", "FUZZY_FILTER"],
        )


if __name__ == "__main__":
    unittest.main()
