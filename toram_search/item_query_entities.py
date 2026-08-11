from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from itertools import combinations, product
from typing import Literal

from toram_data.aliases import ITEM_WORD_ALIASES, normalize_stat_text
from toram_data.stat_query import ItemFilterPhrase, list_item_filter_phrases


FilterMatchStatus = Literal["unique", "none", "ambiguous"]
FilterMatchKind = Literal["exact", "fuzzy"]


@dataclass(frozen=True)
class QueryToken:
    index: int
    text: str
    normalized: str


@dataclass(frozen=True)
class ItemFilterMatch:
    typed_text: str
    token_indexes: tuple[int, ...]
    phrase: ItemFilterPhrase
    canonical_phrase: str
    match_kind: FilterMatchKind
    score: float = 100.0


@dataclass(frozen=True)
class ExactFilterMatches:
    status: FilterMatchStatus
    matches: tuple[ItemFilterMatch, ...] = ()


def tokenize_item_query(raw_query: str) -> tuple[QueryToken, ...]:
    raw_tokens = normalize_stat_text(raw_query).split()
    return tuple(
        QueryToken(index, token, ITEM_WORD_ALIASES.get(token, token))
        for index, token in enumerate(raw_tokens)
    )


def semantic_filter_key(row: ItemFilterPhrase) -> tuple[str, tuple[str, ...]]:
    return row.label, row.item_types


def canonical_filter_phrase(
    row: ItemFilterPhrase,
    catalog: tuple[ItemFilterPhrase, ...],
) -> str:
    key = semantic_filter_key(row)
    phrases = [entry.phrase for entry in catalog if semantic_filter_key(entry) == key]
    return min(
        phrases,
        key=lambda value: (len(value.split()), len(value), value.casefold()),
    )


def _index_options(
    tokens: tuple[QueryToken, ...],
    required: Counter[str],
) -> tuple[tuple[int, ...], ...]:
    groups: list[tuple[tuple[int, ...], ...]] = []
    for normalized, count in sorted(required.items()):
        positions = [token.index for token in tokens if token.normalized == normalized]
        if len(positions) < count:
            return ()
        groups.append(tuple(combinations(positions, count)))
    options = {
        tuple(sorted(index for group in selected for index in group))
        for selected in product(*groups)
    }
    return tuple(sorted(options))


def find_exact_item_filter_matches(
    tokens: tuple[QueryToken, ...],
    available_item_types: set[str],
) -> ExactFilterMatches:
    catalog = list_item_filter_phrases(available_item_types)
    available = Counter(token.normalized for token in tokens)
    eligible: list[tuple[ItemFilterPhrase, Counter[str]]] = []
    for row in catalog:
        required = Counter(row.phrase.split())
        if required and all(available[token] >= count for token, count in required.items()):
            eligible.append((row, required))
    if not eligible:
        return ExactFilterMatches("none")

    max_size = max(sum(required.values()) for _row, required in eligible)
    largest = [
        (row, required)
        for row, required in eligible
        if sum(required.values()) == max_size
    ]
    if len({semantic_filter_key(row) for row, _required in largest}) != 1:
        return ExactFilterMatches("ambiguous")

    by_index = {token.index: token for token in tokens}
    matches: list[ItemFilterMatch] = []
    for row, required in largest:
        for indexes in _index_options(tokens, required):
            matches.append(
                ItemFilterMatch(
                    typed_text=" ".join(by_index[index].text for index in indexes),
                    token_indexes=indexes,
                    phrase=row,
                    canonical_phrase=canonical_filter_phrase(row, catalog),
                    match_kind="exact",
                )
            )
    return ExactFilterMatches("unique", tuple(matches))


def remaining_tokens(
    tokens: tuple[QueryToken, ...],
    consumed_indexes: tuple[int, ...],
) -> tuple[QueryToken, ...]:
    consumed = set(consumed_indexes)
    return tuple(token for token in tokens if token.index not in consumed)
