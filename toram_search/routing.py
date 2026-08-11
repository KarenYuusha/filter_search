from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from toram_data.aliases import normalize_name, normalize_stat_text, resolve_stat_term
from toram_data.item_filters import extract_item_filter
from toram_data.search_models import ItemSummary
from toram_data.search_repository import ItemRepository
from toram_search.help_db import DatabaseActionRequest, DatabaseQuestionService, HelpService
from toram_search.parser import (
    SEARCH_ONLY_STAT_ALIASES,
    ParsedSearch,
    _resolve_item_type_for_database,
    _resolve_stat_for_database,
    build_search_stat_terms,
    find_non_overlapping_stat_terms,
    parse_search_query,
)

@dataclass(frozen=True)
class DeterministicRoute:
    kind: Literal["search", "help", "database", "fallback", "refuse"]
    parsed: ParsedSearch | None = None
    database_request: DatabaseActionRequest | None = None
    help_text: str | None = None
    record_failure: bool = False


def _parsed_expression_has_unknown_stats(parsed: ParsedSearch, repository: ItemRepository) -> bool:
    if parsed.intent != "stat_expression" or parsed.error or parsed.parsed_expression is None:
        return False
    available_stats = repository.list_stat_names()
    for group in parsed.parsed_expression.groups:
        for clause in group.clauses:
            typed = SEARCH_ONLY_STAT_ALIASES.get(
                normalize_stat_text(clause.typed_stat),
                clause.typed_stat,
            )
            resolution = resolve_stat_term(typed, available_stats, allow_fuzzy=True)
            if resolution.status == "unknown":
                return True
    return False


def _try_simple_ranking_search(
    raw: str,
    repository: ItemRepository,
) -> ParsedSearch | None:
    match = re.match(r"^\s*(best|highest)\b", raw, flags=re.IGNORECASE)
    if match is None:
        return None
    candidate = raw[match.end():].strip()
    if not candidate:
        return None
    parsed = parse_search_query(candidate, repository)
    if parsed.intent in {"stat_search", "stat_choices"} and not parsed.error:
        return parsed
    if (
        parsed.intent == "stat_expression"
        and not parsed.error
        and not _parsed_expression_has_unknown_stats(parsed, repository)
    ):
        return parsed
    return None


def make_database_question_service(repository: ItemRepository) -> DatabaseQuestionService:
    return DatabaseQuestionService(
        repository,
        resolve_item_type=lambda text: _resolve_item_type_for_database(text, repository),
        resolve_stat=lambda text: _resolve_stat_for_database(text, repository),
    )


def _looks_like_failed_stat_query(raw: str, repository: ItemRepository) -> bool:
    if repository.exact_name_matches(raw):
        return False
    if normalize_name(raw).startswith("item "):
        return False

    parsed = parse_search_query(raw, repository)
    if parsed.intent == "stat_expression":
        if not parsed.error and not _parsed_expression_has_unknown_stats(parsed, repository):
            return False
        if _parsed_expression_has_unknown_stats(parsed, repository):
            return True
    elif parsed.intent in {"stat_search", "stat_choices", "guided_stat"}:
        return False

    item_filter, remaining = extract_item_filter(raw, repository.list_item_types())
    normalized_remaining = normalize_stat_text(remaining)
    stat_terms = build_search_stat_terms(repository.list_stat_names())
    stat_hits = find_non_overlapping_stat_terms(normalized_remaining, stat_terms)
    has_boolean = bool(re.search(r"\b(and|or)\b", normalized_remaining))
    has_comparison = bool(re.search(r"(?:>=|<=|==|>|<|=)", raw))
    has_ranking = bool(re.search(r"\b(highest|lowest|best)\b", normalized_remaining))
    return (
        len(stat_hits) >= 2
        or (len(stat_hits) >= 1 and item_filter is not None)
        or (len(stat_hits) >= 1 and (has_boolean or has_comparison or has_ranking))
    )


def _contains_out_of_scope_build_concept(text: str) -> bool:
    tokens = set(re.findall(r"[a-z0-9%]+", text.casefold()))
    return bool(tokens & {"tank", "dps", "build", "mage"})


def _looks_like_assistant_question(text: str) -> bool:
    normalized = " ".join(text.strip().casefold().split())
    if not normalized:
        return False
    first = normalized.split()[0]
    return first in {
        "what", "how", "which", "does", "is", "can", "could", "would",
        "tell", "explain", "why", "who", "when", "where",
    }


def route_deterministically(
    query: str,
    repository: ItemRepository,
    all_items: list[ItemSummary],
    help_service: HelpService,
    database_service: DatabaseQuestionService,
) -> DeterministicRoute:
    del all_items
    raw = query.strip()
    parsed = parse_search_query(raw, repository)

    if parsed.intent in {
        "exact_item",
        "exact_upgrade",
        "upgrade_search",
        "guided_stat",
        "stat_search",
        "stat_choices",
    }:
        return DeterministicRoute("search", parsed=parsed)
    if parsed.intent == "item_search" and repository.exact_name_matches(raw):
        return DeterministicRoute("search", parsed=parsed)
    if (
        parsed.intent == "stat_expression"
        and not parsed.error
        and not _parsed_expression_has_unknown_stats(parsed, repository)
    ):
        return DeterministicRoute("search", parsed=parsed)

    ranking_search = _try_simple_ranking_search(raw, repository)
    if ranking_search is not None:
        return DeterministicRoute("search", parsed=ranking_search)

    help_text = help_service.answer_direct(raw)
    if help_text is not None:
        return DeterministicRoute("help", help_text=help_text)

    db_request = database_service.match_direct(raw)
    if db_request is not None:
        return DeterministicRoute("database", database_request=db_request)

    if _contains_out_of_scope_build_concept(raw):
        return DeterministicRoute("refuse")

    if _looks_like_assistant_question(raw):
        return DeterministicRoute("fallback", record_failure=False)

    if _looks_like_failed_stat_query(raw, repository):
        return DeterministicRoute("fallback", record_failure=True)

    return DeterministicRoute("search", parsed=parsed)
