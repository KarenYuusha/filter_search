from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from rapidfuzz import fuzz

from toram_data.aliases import normalize_name
from toram_data.search_models import ItemSummary

MIN_QUERY_LENGTH = 2
ITEM_FUZZY_RELEVANCE_THRESHOLD = 70.0
PAGE_SIZE = 5


@dataclass(frozen=True)
class RankedItem:
    item: ItemSummary
    score: float
    match_kind: str


def _score_item(query: str, item: ItemSummary) -> RankedItem:
    normalized_query = normalize_name(query)
    normalized_name = normalize_name(item.name)
    query_tokens = normalized_query.split()
    name_tokens = normalized_name.split()
    score = max(
        float(fuzz.WRatio(normalized_query, normalized_name)),
        float(fuzz.token_set_ratio(normalized_query, normalized_name)),
        float(fuzz.token_sort_ratio(normalized_query, normalized_name)),
        float(fuzz.ratio(normalized_query, normalized_name)),
    )
    match_kind = "fuzzy"
    if normalized_name == normalized_query:
        score, match_kind = 100.0, "exact"
    elif normalized_name.startswith(normalized_query):
        score, match_kind = max(score, 98.0), "prefix"
    elif normalized_query in normalized_name:
        score, match_kind = max(score, 95.0), "substring"
    elif query_tokens and all(token in name_tokens for token in query_tokens):
        score, match_kind = max(score, 93.0), "all_tokens"
    score -= max(len(normalized_name) - len(normalized_query), 0) * 0.001
    return RankedItem(item, score, match_kind)


def _is_relevant_ranked_item(result: RankedItem) -> bool:
    if result.match_kind in {"exact", "prefix", "substring", "all_tokens"}:
        return True
    return result.score >= ITEM_FUZZY_RELEVANCE_THRESHOLD


def rank_items(query: str, items: Iterable[ItemSummary]) -> list[RankedItem]:
    normalized_query = normalize_name(query)
    if len(normalized_query) < MIN_QUERY_LENGTH:
        return []
    results = [
        result
        for item in items
        if _is_relevant_ranked_item(result := _score_item(normalized_query, item))
    ]
    results.sort(
        key=lambda result: (
            -result.score,
            len(normalize_name(result.item.name)),
            normalize_name(result.item.name),
            result.item.id,
        )
    )
    return results


def page_results(
    results: list[RankedItem],
    *,
    page: int,
    page_size: int = PAGE_SIZE,
) -> list[RankedItem]:
    start = page * page_size
    return results[start:start + page_size]
