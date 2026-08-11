from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Literal

from toram_data.aliases import (
    StatTermResolution,
    normalize_stat_text,
    preferred_stat_alias,
    resolve_stat_term,
)
from toram_data.stat_query import ItemFilterPhrase
from toram_search.item_query_entities import (
    find_exact_item_filter_matches,
    remaining_tokens,
    tokenize_item_query,
)


ReconstructionKind = Literal["success", "ambiguous", "no_match", "unsafe"]

_MAX_RECONSTRUCTION_TOKENS = 24
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


def _recognize(
    raw_query: str,
    *,
    available_stats: Iterable[str],
    available_item_types: set[str],
    allow_one_rank_word: bool,
) -> ReconstructionResult:
    if _COMPLEX_RE.search(raw_query):
        return ReconstructionResult("unsafe")

    tokens = tokenize_item_query(raw_query)
    if len(tokens) > _MAX_RECONSTRUCTION_TOKENS:
        return ReconstructionResult("unsafe")

    rank_tokens = [token for token in tokens if token.normalized in _RANK_WORDS]
    if allow_one_rank_word:
        if len(rank_tokens) > 1:
            return ReconstructionResult("unsafe")
        if rank_tokens:
            removed_index = rank_tokens[0].index
            tokens = tuple(token for token in tokens if token.index != removed_index)
    elif rank_tokens:
        return ReconstructionResult("unsafe")

    filter_matches = find_exact_item_filter_matches(tokens, available_item_types)
    if filter_matches.status == "none":
        return ReconstructionResult("no_match")
    if filter_matches.status == "ambiguous":
        return ReconstructionResult("unsafe")

    valid: list[tuple[ItemFilterPhrase, str, str, StatTermResolution]] = []
    for match in filter_matches.matches:
        remaining = remaining_tokens(tokens, match.token_indexes)
        stat_tokens = [
            token.normalized
            for token in remaining
            if token.normalized not in _FILLERS
        ]
        if not stat_tokens:
            continue
        stat_text = " ".join(stat_tokens)
        resolution = resolve_stat_term(
            stat_text,
            available_stats,
            allow_fuzzy=False,
        )
        if resolution.status in {"exact", "alias", "ambiguous"}:
            valid.append(
                (match.phrase, match.canonical_phrase, stat_text, resolution)
            )

    if not valid:
        matched = min(
            (match.phrase for match in filter_matches.matches),
            key=lambda row: (len(row.phrase), row.phrase.casefold()),
        )
        return ReconstructionResult("unsafe", filter_phrase=matched)

    semantic_stat_keys = {
        (resolution.status == "ambiguous", resolution.candidates)
        for _row, _canonical_filter, _stat_text, resolution in valid
    }
    if len(semantic_stat_keys) != 1:
        return ReconstructionResult("unsafe")

    matched, canonical_filter, stat_text, resolution = min(
        valid,
        key=lambda option: (
            len(option[2].split()),
            len(option[2]),
            option[2].casefold(),
            option[0].phrase.casefold(),
        ),
    )
    normalized_stat = normalize_stat_text(stat_text)
    if resolution.status in {"alias", "ambiguous"}:
        rendered_stat = normalized_stat
    else:
        rendered_stat = preferred_stat_alias(resolution.candidates[0]) or normalized_stat
    canonical_query = f"{rendered_stat} {canonical_filter}"
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
