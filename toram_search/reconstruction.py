from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from itertools import combinations, product
from typing import Iterable, Literal

from toram_data.aliases import (
    ITEM_WORD_ALIASES,
    StatTermResolution,
    normalize_stat_text,
    preferred_stat_alias,
    resolve_stat_term,
)
from toram_data.stat_query import ItemFilterPhrase, list_item_filter_phrases


ReconstructionKind = Literal["success", "ambiguous", "no_match", "unsafe"]

_FILLERS = frozenset(
    {
        "a",
        "an",
        "can",
        "find",
        "give",
        "gives",
        "has",
        "have",
        "having",
        "i",
        "me",
        "show",
        "some",
        "that",
        "the",
        "want",
        "which",
        "with",
        "you",
    }
)
_RANK_WORDS = frozenset({"highest", "best", "most"})
_COMPLEX_RE = re.compile(r"(>=|<=|==|>|<|=)|\b(?:and|or)\b", re.IGNORECASE)


@dataclass(frozen=True)
class ReconstructionResult:
    kind: ReconstructionKind
    canonical_query: str | None = None
    stat_resolution: StatTermResolution | None = None
    filter_phrase: ItemFilterPhrase | None = None


def _tokens(raw_query: str) -> list[str]:
    return [
        ITEM_WORD_ALIASES.get(token, token)
        for token in normalize_stat_text(raw_query).split()
    ]


def _semantic_filter_key(row: ItemFilterPhrase) -> tuple[str, tuple[str, ...]]:
    return row.label, row.item_types


def _is_counter_subset(required: Counter[str], available: Counter[str]) -> bool:
    return all(available[token] >= count for token, count in required.items())


def _removal_options(
    tokens: list[str],
    counts: Counter[str],
) -> tuple[tuple[str, ...], ...]:
    position_groups: list[tuple[tuple[int, ...], ...]] = []
    for token, count in sorted(counts.items()):
        positions = [index for index, value in enumerate(tokens) if value == token]
        if len(positions) < count:
            return ()
        position_groups.append(tuple(combinations(positions, count)))

    options: set[tuple[str, ...]] = set()
    for selected_groups in product(*position_groups):
        removed = {
            index
            for selected_group in selected_groups
            for index in selected_group
        }
        options.add(
            tuple(value for index, value in enumerate(tokens) if index not in removed)
        )
    return tuple(sorted(options))


def _preferred_filter_phrase(
    matched: ItemFilterPhrase,
    catalog: tuple[ItemFilterPhrase, ...],
) -> str:
    key = _semantic_filter_key(matched)
    phrases = [row.phrase for row in catalog if _semantic_filter_key(row) == key]
    return min(
        phrases,
        key=lambda value: (len(value.split()), len(value), value.casefold()),
    )


def _recognize(
    raw_query: str,
    *,
    available_stats: Iterable[str],
    available_item_types: set[str],
    allow_one_rank_word: bool,
) -> ReconstructionResult:
    if _COMPLEX_RE.search(raw_query):
        return ReconstructionResult("unsafe")

    tokens = _tokens(raw_query)
    rank_count = sum(token in _RANK_WORDS for token in tokens)
    if allow_one_rank_word:
        if rank_count > 1:
            return ReconstructionResult("unsafe")
        if rank_count == 1:
            removed = False
            cleaned: list[str] = []
            for token in tokens:
                if token in _RANK_WORDS and not removed:
                    removed = True
                    continue
                cleaned.append(token)
            tokens = cleaned
    elif rank_count:
        return ReconstructionResult("unsafe")

    catalog = list_item_filter_phrases(available_item_types)
    available_counter = Counter(tokens)
    eligible: list[tuple[ItemFilterPhrase, Counter[str]]] = []
    for row in catalog:
        phrase_counter = Counter(row.phrase.split())
        if phrase_counter and _is_counter_subset(phrase_counter, available_counter):
            eligible.append((row, phrase_counter))
    if not eligible:
        return ReconstructionResult("no_match")

    max_tokens = max(sum(counter.values()) for _row, counter in eligible)
    largest = [
        (row, counter)
        for row, counter in eligible
        if sum(counter.values()) == max_tokens
    ]
    semantic_keys = {_semantic_filter_key(row) for row, _counter in largest}
    if len(semantic_keys) != 1:
        return ReconstructionResult("unsafe")

    valid: list[tuple[ItemFilterPhrase, str, StatTermResolution]] = []
    for row, matched_counter in largest:
        for remaining in _removal_options(tokens, matched_counter):
            stat_tokens = [token for token in remaining if token not in _FILLERS]
            if not stat_tokens:
                continue
            stat_text = " ".join(stat_tokens)
            resolution = resolve_stat_term(
                stat_text,
                available_stats,
                allow_fuzzy=False,
            )
            if resolution.status in {"exact", "alias", "ambiguous"}:
                valid.append((row, stat_text, resolution))

    if not valid:
        matched = min(
            (row for row, _counter in largest),
            key=lambda row: (len(row.phrase), row.phrase.casefold()),
        )
        return ReconstructionResult("unsafe", filter_phrase=matched)

    semantic_stat_keys = {
        (resolution.status == "ambiguous", resolution.candidates)
        for _row, _stat_text, resolution in valid
    }
    if len(semantic_stat_keys) != 1:
        return ReconstructionResult("unsafe")

    matched, stat_text, resolution = min(
        valid,
        key=lambda option: (
            len(option[1].split()),
            len(option[1]),
            option[1].casefold(),
            option[0].phrase.casefold(),
        ),
    )
    normalized_stat = normalize_stat_text(stat_text)
    if resolution.status in {"alias", "ambiguous"}:
        rendered_stat = normalized_stat
    else:
        rendered_stat = preferred_stat_alias(resolution.candidates[0]) or normalized_stat
    canonical_query = f"{rendered_stat} {_preferred_filter_phrase(matched, catalog)}"
    return ReconstructionResult(
        "ambiguous" if resolution.status == "ambiguous" else "success",
        canonical_query=canonical_query,
        stat_resolution=resolution,
        filter_phrase=matched,
    )


def try_reconstruct_simple_search(
    raw_query: str,
    *,
    available_stats: Iterable[str],
    available_item_types: set[str],
) -> ReconstructionResult:
    return _recognize(
        raw_query,
        available_stats=available_stats,
        available_item_types=available_item_types,
        allow_one_rank_word=False,
    )


def try_suggest_query(
    raw_query: str,
    *,
    available_stats: Iterable[str],
    available_item_types: set[str],
) -> str | None:
    result = _recognize(
        raw_query,
        available_stats=available_stats,
        available_item_types=available_item_types,
        allow_one_rank_word=True,
    )
    if result.kind != "success":
        return None
    return result.canonical_query
