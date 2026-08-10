import unittest

from toram_search.reconstruction import try_reconstruct_simple_search, try_suggest_query


STATS = ["Critical Rate", "Critical Damage", "MaxHP", "Weapon ATK"]
TYPES = {
    "Weapon Crysta",
    "Enhancer Crysta (Red)",
    "Armor Crysta",
    "Enhancer Crysta (Green)",
    "Armor",
    "Bow",
}


class QueryReconstructionTests(unittest.TestCase):
    def reconstruct(self, query):
        return try_reconstruct_simple_search(
            query,
            available_stats=STATS,
            available_item_types=TYPES,
        )

    def test_xtall_cr_weapon(self):
        result = self.reconstruct("xtall cr weapon")
        self.assertEqual(result.kind, "success")
        self.assertEqual(result.canonical_query, "cr wp xtal")
        self.assertEqual(result.stat_resolution.candidates, ("Critical Rate",))
        self.assertEqual(
            result.filter_phrase.item_types,
            ("Weapon Crysta", "Enhancer Crysta (Red)"),
        )

    def test_order_is_flexible(self):
        self.assertEqual(self.reconstruct("weapon xtall cr").canonical_query, "cr wp xtal")

    def test_filter_token_overlap_preserves_flexible_order(self):
        for query in ("weapon atk weapon xtal", "weapon xtal weapon atk"):
            with self.subTest(query=query):
                result = self.reconstruct(query)
                self.assertEqual(result.kind, "success")
                self.assertEqual(result.canonical_query, "weapon atk wp xtal")
                self.assertEqual(result.stat_resolution.candidates, ("Weapon ATK",))

    def test_known_filler_is_allowed(self):
        self.assertEqual(self.reconstruct("find xtall cr weapon").canonical_query, "cr wp xtal")

    def test_oversized_input_fails_closed(self):
        query = " ".join(["show"] * 30 + ["cr", "weapon", "xtal"])
        result = self.reconstruct(query)
        self.assertEqual(result.kind, "unsafe")
        self.assertIsNone(result.canonical_query)

    def test_crit_is_ambiguous(self):
        result = self.reconstruct("crit xtal weapon")
        self.assertEqual(result.kind, "ambiguous")
        self.assertEqual(result.canonical_query, "crit wp xtal")
        self.assertEqual(
            result.stat_resolution.candidates,
            ("Critical Rate", "Critical Damage"),
        )

    def test_unknown_token_fails_closed(self):
        result = self.reconstruct("xtall blah weapon")
        self.assertEqual(result.kind, "unsafe")
        self.assertIsNone(result.canonical_query)

    def test_multiple_stats_fail_closed(self):
        self.assertEqual(self.reconstruct("cr cd weapon xtal").kind, "unsafe")

    def test_complex_syntax_is_not_auto_reconstructed(self):
        self.assertNotEqual(self.reconstruct("cr > 10 weapon xtal").kind, "success")
        self.assertNotEqual(self.reconstruct("cr and cd weapon xtal").kind, "success")

    def test_highest_is_guidance_only(self):
        self.assertNotEqual(self.reconstruct("highest xtall cr weapon").kind, "success")
        self.assertEqual(
            try_suggest_query(
                "highest xtall cr weapon",
                available_stats=STATS,
                available_item_types=TYPES,
            ),
            "cr wp xtal",
        )

    def test_ambiguous_suggestion_is_rejected(self):
        self.assertIsNone(
            try_suggest_query(
                "highest crit xtal weapon",
                available_stats=STATS,
                available_item_types=TYPES,
            )
        )


if __name__ == "__main__":
    unittest.main()
