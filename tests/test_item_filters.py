import unittest

from toram_data.item_filters import extract_item_filter, extract_trailing_item_filter, list_item_filter_phrases

ITEM_TYPES = {"Bow", "Usable", "Weapon Crysta", "Enhancer Crysta (Red)", "Normal Crysta"}

class ItemFilterTests(unittest.TestCase):
    def test_consume_alias_resolves_to_usable_without_consuming_stat(self):
        item_filter, remaining = extract_item_filter("consume dte", ITEM_TYPES)
        self.assertIsNotNone(item_filter)
        self.assertEqual(item_filter.label, "Usable")
        self.assertEqual(item_filter.item_types, ("Usable",))
        self.assertEqual(remaining, "dte")

    def test_weapon_xtal_keeps_combined_crysta_semantics(self):
        item_filter, remaining = extract_item_filter("cr weapon xtal", ITEM_TYPES)
        self.assertIsNotNone(item_filter)
        self.assertEqual(item_filter.label, "Weapon Crysta + Red Enhancer")
        self.assertEqual(item_filter.item_types, ("Weapon Crysta", "Enhancer Crysta (Red)"))
        self.assertEqual(remaining, "cr")

    def test_trailing_filter_uses_same_semantics(self):
        item_filter, remaining = extract_trailing_item_filter("critical rate weapon xtal", ITEM_TYPES)
        self.assertIsNotNone(item_filter)
        self.assertEqual(item_filter.label, "Weapon Crysta + Red Enhancer")
        self.assertEqual(remaining, "critical rate")

    def test_catalog_contains_consume_alias(self):
        rows = list_item_filter_phrases(ITEM_TYPES)
        self.assertIn(("consume", "Usable"), {(row.phrase, row.label) for row in rows})
