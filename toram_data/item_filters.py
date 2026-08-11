from __future__ import annotations

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
