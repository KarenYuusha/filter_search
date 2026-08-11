import unittest

from toram_search.item_query_entities import (
    find_exact_item_filter_matches,
    find_unique_fuzzy_item_filter_match,
    remaining_tokens,
    tokenize_item_query,
)


TYPES = {
    "Weapon Crysta",
    "Enhancer Crysta (Red)",
    "Armor",
    "Bow",
}


class ItemQueryEntityTests(unittest.TestCase):
    def test_item_word_alias_is_normalized(self):
        tokens = tokenize_item_query("xtall cr weapon")
        self.assertEqual(
            [token.normalized for token in tokens],
            ["xtal", "cr", "weapon"],
        )

    def test_weapon_xtal_has_one_semantic_filter(self):
        tokens = tokenize_item_query("xtall cr weapon")
        result = find_exact_item_filter_matches(tokens, TYPES)
        self.assertEqual(result.status, "unique")
        self.assertTrue(result.matches)
        self.assertEqual(
            result.matches[0].phrase.item_types,
            ("Weapon Crysta", "Enhancer Crysta (Red)"),
        )
        self.assertEqual(result.matches[0].canonical_phrase, "wp xtal")

    def test_repeated_weapon_preserves_overlap_solution(self):
        tokens = tokenize_item_query("weapon atk weapon xtal")
        result = find_exact_item_filter_matches(tokens, TYPES)
        remaining = {
            tuple(token.normalized for token in remaining_tokens(tokens, match.token_indexes))
            for match in result.matches
        }
        self.assertIn(("weapon", "atk"), remaining)

    def test_wepon_xtal_has_one_fuzzy_filter_candidate(self):
        tokens = tokenize_item_query("cr wepon xtal")
        match = find_unique_fuzzy_item_filter_match(tokens, TYPES)
        self.assertIsNotNone(match)
        self.assertEqual(
            match.phrase.item_types,
            ("Weapon Crysta", "Enhancer Crysta (Red)"),
        )
        self.assertEqual(match.canonical_phrase, "wp xtal")
        self.assertEqual(match.match_kind, "fuzzy")


if __name__ == "__main__":
    unittest.main()
