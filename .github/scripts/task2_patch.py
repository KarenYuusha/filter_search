from pathlib import Path

ITEM_FILTERS = r'''from __future__ import annotations

from dataclasses import dataclass

from toram_data.aliases import (
    ALL_CRYSTA_TYPES,
    ITEM_TYPE_ALIASES,
    ITEM_WORD_ALIASES,
    MAIN_WEAPON_TYPES,
    normalize_name,
    normalize_stat_text,
)


@dataclass(frozen=True)
class ItemTypeFilter:
    label: str
    item_types: tuple[str, ...]
    consumed_text: str


@dataclass(frozen=True)
class ItemFilterPhrase:
    phrase: str
    label: str
    item_types: tuple[str, ...]


def normalize_filter_text(value: str) -> str:
    tokens = normalize_stat_text(value).split()
    return " ".join(ITEM_WORD_ALIASES.get(token, token) for token in tokens)


def _available_types_by_normalized(available_item_types: set[str]) -> dict[str, str]:
    return {normalize_name(value): value for value in available_item_types}


def _existing_types(configured: tuple[str, ...], available_item_types: set[str]) -> tuple[str, ...]:
    by_normalized = _available_types_by_normalized(available_item_types)
    output: list[str] = []
    for configured_type in configured:
        actual = by_normalized.get(normalize_name(configured_type))
        if actual is not None and actual not in output:
            output.append(actual)
    return tuple(output)


def _filter_candidates() -> list[tuple[str, str, tuple[str, ...]]]:
    combinations = {
        "wp xtal": ("Weapon Crysta + Red Enhancer", ("Weapon Crysta", "Enhancer Crysta (Red)")),
        "weapon xtal": ("Weapon Crysta + Red Enhancer", ("Weapon Crysta", "Enhancer Crysta (Red)")),
        "ring xtal": ("Special Crysta + Purple Enhancer", ("Special Crysta", "Enhancer Crysta (Purple)")),
        "rings xtal": ("Special Crysta + Purple Enhancer", ("Special Crysta", "Enhancer Crysta (Purple)")),
        "special xtal": ("Special Crysta + Purple Enhancer", ("Special Crysta", "Enhancer Crysta (Purple)")),
        "special gear xtal": ("Special Crysta + Purple Enhancer", ("Special Crysta", "Enhancer Crysta (Purple)")),
        "arm xtal": ("Armor Crysta + Green Enhancer", ("Armor Crysta", "Enhancer Crysta (Green)")),
        "armor xtal": ("Armor Crysta + Green Enhancer", ("Armor Crysta", "Enhancer Crysta (Green)")),
        "add xtal": ("Additional Crysta + Yellow Enhancer", ("Additional Crysta", "Enhancer Crysta (Yellow)")),
        "ad xtal": ("Additional Crysta + Yellow Enhancer", ("Additional Crysta", "Enhancer Crysta (Yellow)")),
        "hat xtal": ("Additional Crysta + Yellow Enhancer", ("Additional Crysta", "Enhancer Crysta (Yellow)")),
        "additional xtal": ("Additional Crysta + Yellow Enhancer", ("Additional Crysta", "Enhancer Crysta (Yellow)")),
        "red xtal": ("Enhancer Crysta (Red)", ("Enhancer Crysta (Red)",)),
        "purple xtal": ("Enhancer Crysta (Purple)", ("Enhancer Crysta (Purple)",)),
        "green xtal": ("Enhancer Crysta (Green)", ("Enhancer Crysta (Green)",)),
        "yellow xtal": ("Enhancer Crysta (Yellow)", ("Enhancer Crysta (Yellow)",)),
        "xtal": ("All Crysta", ALL_CRYSTA_TYPES),
        "wp": ("Main Weapons", MAIN_WEAPON_TYPES),
        "weapon": ("Main Weapons", MAIN_WEAPON_TYPES),
    }
    candidates = [(normalize_filter_text(phrase), label, tuple(types)) for phrase, (label, types) in combinations.items()]
    for alias, item_type in ITEM_TYPE_ALIASES.items():
        candidates.append((normalize_filter_text(alias), item_type, (item_type,)))
    candidates.sort(key=lambda row: (len(row[0].split()), len(row[0])), reverse=True)
    return candidates


def _find_phrase_tokens(tokens: list[str], phrase: str) -> tuple[int, int] | None:
    phrase_tokens = phrase.split()
    if not phrase_tokens or len(phrase_tokens) > len(tokens):
        return None
    for start in range(len(tokens) - len(phrase_tokens) + 1):
        end = start + len(phrase_tokens)
        if tokens[start:end] == phrase_tokens:
            return start, end
    return None


def list_item_filter_phrases(available_item_types: set[str]) -> tuple[ItemFilterPhrase, ...]:
    output: list[ItemFilterPhrase] = []
    seen: set[ItemFilterPhrase] = set()
    for phrase, label, configured_types in _filter_candidates():
        item_types = _existing_types(configured_types, available_item_types)
        if not item_types:
            continue
        row = ItemFilterPhrase(phrase, label, item_types)
        if row in seen:
            continue
        seen.add(row)
        output.append(row)
    return tuple(output)


def extract_item_filter(text: str, available_item_types: set[str]) -> tuple[ItemTypeFilter | None, str]:
    normalized = normalize_filter_text(text)
    tokens = normalized.split()
    for row in list_item_filter_phrases(available_item_types):
        span = _find_phrase_tokens(tokens, row.phrase)
        if span is None:
            continue
        start, end = span
        remaining = " ".join(tokens[:start] + tokens[end:])
        return ItemTypeFilter(row.label, row.item_types, row.phrase), remaining
    return None, normalized


def resolve_item_filter(text: str, available_item_types: set[str]) -> ItemTypeFilter | None:
    resolution, _remaining = extract_item_filter(text, available_item_types)
    return resolution


def extract_trailing_item_filter(text: str, available_item_types: set[str]) -> tuple[ItemTypeFilter | None, str]:
    raw_tokens = text.strip().split()
    for row in list_item_filter_phrases(available_item_types):
        phrase_tokens = row.phrase.split()
        if len(phrase_tokens) > len(raw_tokens):
            continue
        raw_suffix = " ".join(raw_tokens[-len(phrase_tokens):])
        if normalize_filter_text(raw_suffix) != row.phrase:
            continue
        remaining = " ".join(raw_tokens[:-len(phrase_tokens)]).strip()
        return ItemTypeFilter(row.label, row.item_types, row.phrase), remaining
    return None, text.strip()
'''

TESTS = r'''import unittest

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
'''

Path('toram_data/item_filters.py').write_text(ITEM_FILTERS)
Path('tests/test_item_filters.py').write_text(TESTS)

path = Path('toram_data/stat_query.py')
text = path.read_text()
marker = ')\n\nComparisonOperator = Literal'
shared = ')\n\nfrom toram_data.item_filters import (\n    ItemFilterPhrase,\n    ItemTypeFilter,\n    extract_trailing_item_filter,\n    list_item_filter_phrases,\n    normalize_filter_text,\n)\n\nComparisonOperator = Literal'
assert marker in text
text = text.replace(marker, shared, 1)
type_start = text.index('@dataclass(frozen=True)\nclass ItemTypeFilter:')
type_end = text.index('@dataclass(frozen=True)\nclass ParsedClause:', type_start)
text = text[:type_start] + text[type_end:]
helper_start = text.index('def _normalize_filter_text(')
helper_end = text.index('def _first_clause_stat_text(', helper_start)
text = text[:helper_start] + text[helper_end:]
old_loop = '''    for phrase, label, configured_types in _filter_candidates():\n        phrase_tokens = phrase.split()\n        if len(phrase_tokens) >= len(raw_tokens):\n            continue\n        raw_prefix = " ".join(raw_tokens[: len(phrase_tokens)])\n        if _normalize_filter_text(raw_prefix) != phrase:\n            continue\n        item_types = _existing_types(configured_types, available_item_types)\n        if not item_types:\n            continue\n        remaining = " ".join(raw_tokens[len(phrase_tokens) :]).strip()\n        if not remaining:\n            continue\n        return ItemTypeFilter(label, item_types, phrase), remaining\n'''
new_loop = '''    for row in list_item_filter_phrases(available_item_types):\n        phrase_tokens = row.phrase.split()\n        if len(phrase_tokens) >= len(raw_tokens):\n            continue\n        raw_prefix = " ".join(raw_tokens[: len(phrase_tokens)])\n        if normalize_filter_text(raw_prefix) != row.phrase:\n            continue\n        remaining = " ".join(raw_tokens[len(phrase_tokens) :]).strip()\n        if not remaining:\n            continue\n        return ItemTypeFilter(row.label, row.item_types, row.phrase), remaining\n'''
assert old_loop in text
text = text.replace(old_loop, new_loop, 1)
text = text.replace('_extract_trailing_filter(', 'extract_trailing_item_filter(')
path.write_text(text)

path = Path('search_items.py')
text = path.read_text()
anchor = 'from toram_data.stat_query import (\n'
canonical_import = 'from toram_data.item_filters import (\n    extract_item_filter,\n    list_item_filter_phrases,\n    resolve_item_filter,\n)\n\n'
assert anchor in text
text = text.replace(anchor, canonical_import + anchor, 1)
helper_start = text.index('def _normalize_filter_text(')
helper_end = text.index('def resolve_stat_choices(', helper_start)
text = text[:helper_start] + text[helper_end:]
old_catalog = '''    filter_labels: list[str] = []\n    seen: set[str] = set()\n    for phrase, label, _types in _filter_candidates():\n        value = f"{phrase} -> {label}"\n        if value not in seen:\n            seen.add(value)\n            filter_labels.append(value)\n'''
new_catalog = '''    filter_labels: list[str] = []\n    seen: set[str] = set()\n    for row in list_item_filter_phrases(repository.list_item_types()):\n        value = f"{row.phrase} -> {row.label}"\n        if value not in seen:\n            seen.add(value)\n            filter_labels.append(value)\n'''
assert old_catalog in text
text = text.replace(old_catalog, new_catalog, 1)
path.write_text(text)

path = Path('toram_search/item_query_entities.py')
text = path.read_text()
old = 'from toram_data.stat_query import ItemFilterPhrase, list_item_filter_phrases\n'
assert old in text
path.write_text(text.replace(old, 'from toram_data.item_filters import ItemFilterPhrase, list_item_filter_phrases\n', 1))

path = Path('toram_search/reconstruction.py')
text = path.read_text()
old = 'from toram_data.stat_query import ItemFilterPhrase\n'
assert old in text
path.write_text(text.replace(old, 'from toram_data.item_filters import ItemFilterPhrase\n', 1))
