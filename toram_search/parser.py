from __future__ import annotations

import re
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Literal

from rapidfuzz import fuzz

from toram_data.aliases import (
    STAT_ALIASES,
    STAT_AMBIGUOUS_GROUPS,
    STAT_CATEGORY_ALIASES,
    expand_stat_aliases,
    normalize_name,
    normalize_stat_text,
    resolve_stat_term,
)
from toram_data.item_filters import ItemTypeFilter, extract_item_filter
from toram_data.search_repository import ItemRepository
from toram_data.stat_query import (
    ParsedAndGroup,
    ParsedStatExpression,
    ResolvedAndGroup,
    ResolvedClause,
    ResolvedStatExpression,
    StatQuerySyntaxError,
    looks_like_stat_expression,
    parse_stat_expression,
)
from toram_search.fallback import SearchIntentRequest

FUZZY_STAT_THRESHOLD = 70.0
SEARCH_ONLY_STAT_ALIASES = {
    "aggro": "aggro %",
}
FilterResolution = ItemTypeFilter
SearchIntent = Literal[
    "exact_item",
    "item_search",
    "stat_search",
    "stat_choices",
    "guided_stat",
    "stat_expression",
    "exact_upgrade",
    "upgrade_search",
]


def _format_search_number(value: object) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(number)) if number.is_integer() else f"{number:g}"

@dataclass(frozen=True)
class StatResolution:
    stat_name: str
    matched_text: str
    confidence: float
    requires_confirmation: bool


@dataclass(frozen=True)
class ParsedSearch:
    intent: SearchIntent
    raw_query: str
    item_query: str | None = None
    item_id: int | None = None
    stat: StatResolution | None = None
    stat_choices: tuple[str, ...] = ()
    filter: FilterResolution | None = None
    requires_confirmation: bool = False
    error: str | None = None
    parsed_expression: ParsedStatExpression | None = None
    resolved_expression: ResolvedStatExpression | None = None
    primary_sort_ascending: bool = False


def resolve_stat_choices(text: str, available_stats: list[str]) -> tuple[str, ...]:
    query = normalize_stat_text(text)
    category = STAT_CATEGORY_ALIASES.get(query)
    if category is None:
        return ()

    if category == "damage_to_element":
        matches = [
            stat for stat in available_stats
            if normalize_stat_text(stat).startswith("% stronger against ")
        ]
    elif category == "barrier":
        matches = [
            stat for stat in available_stats
            if "barrier" in normalize_stat_text(stat).split()
        ]
    else:
        matches = [
            stat for stat in available_stats
            if any(
                token.startswith("resist")
                for token in normalize_stat_text(stat).split()
            )
        ]

    return tuple(sorted(matches, key=lambda value: value.casefold()))


def resolve_stat_name(
    text: str,
    available_stats: list[str],
    *,
    allow_fuzzy: bool = True,
) -> StatResolution | None:
    query = normalize_stat_text(text)
    if not query:
        return None

    canonical: dict[str, list[str]] = {}
    for stat_name in available_stats:
        canonical.setdefault(normalize_stat_text(stat_name), []).append(stat_name)

    expanded_query = expand_stat_aliases(text)
    if expanded_query != query:
        expanded_matches = canonical.get(expanded_query)
        if expanded_matches:
            chosen = sorted(
                expanded_matches,
                key=lambda value: (len(value), value.casefold()),
            )[0]
            return StatResolution(chosen, text.strip(), 100.0, False)

    exact = canonical.get(query)
    if exact:
        chosen = sorted(exact, key=lambda value: (len(value), value.casefold()))[0]
        return StatResolution(chosen, text.strip(), 100.0, False)

    expanded_matches = canonical.get(expanded_query)
    if expanded_matches:
        chosen = sorted(
            expanded_matches,
            key=lambda value: (len(value), value.casefold()),
        )[0]
        return StatResolution(chosen, text.strip(), 100.0, False)

    if not allow_fuzzy:
        return None

    best_name: str | None = None
    best_score = -1.0
    for stat_name in available_stats:
        score = float(fuzz.WRatio(expanded_query, normalize_stat_text(stat_name)))
        if score > best_score:
            best_name = stat_name
            best_score = score
    if best_name is None or best_score < FUZZY_STAT_THRESHOLD:
        return None
    return StatResolution(best_name, text.strip(), best_score, True)


def _parse_stat_request(
    text: str,
    repository: ItemRepository,
    *,
    allow_fuzzy: bool,
) -> ParsedSearch:
    filter_resolution, remaining = extract_item_filter(text, repository.list_item_types())
    if normalize_stat_text(remaining) == "upgrade for":
        return ParsedSearch(
            intent="stat_search",
            raw_query=text,
            filter=filter_resolution,
            error="'Upgrade for' is an item relationship, not a numeric stat.",
        )
    available_stats = repository.list_stat_names()

    stat_choices = resolve_stat_choices(remaining, available_stats)
    if stat_choices:
        return ParsedSearch(
            intent="stat_choices",
            raw_query=text,
            stat_choices=stat_choices,
            filter=filter_resolution,
        )

    stat_resolution = resolve_stat_name(
        remaining,
        available_stats,
        allow_fuzzy=allow_fuzzy,
    )
    if stat_resolution is None:
        return ParsedSearch(
            intent="stat_search",
            raw_query=text,
            filter=filter_resolution,
            error=f'Unknown stat: "{remaining or text}"',
        )
    return ParsedSearch(
        intent="stat_search",
        raw_query=text,
        stat=stat_resolution,
        filter=filter_resolution,
        requires_confirmation=stat_resolution.requires_confirmation,
    )


def _parse_negative_bare_expression(
    text: str,
    repository: ItemRepository,
) -> ParsedStatExpression | None:
    raw = text.strip()
    if not raw:
        return None
    if re.search(r"(>=|<=|==|>|<|=)", raw) or re.search(r"\b(and|or)\b", raw, re.IGNORECASE):
        return None

    tokens = raw.split()
    negative_indexes = [
        index
        for index, token in enumerate(tokens)
        if token.startswith("-")
        and len(token) > 1
        and not re.fullmatch(r"-[0-9]+(?:\.[0-9]+)?", token)
    ]
    if len(negative_indexes) != 1:
        return None

    index = negative_indexes[0]
    tokens[index] = tokens[index][1:]
    positive_query = " ".join(tokens)
    try:
        parsed = parse_stat_expression(
            positive_query,
            repository.list_item_types(),
            repository.list_stat_names(),
        )
    except StatQuerySyntaxError:
        return None

    if len(parsed.groups) != 1 or len(parsed.groups[0].clauses) != 1:
        return None
    clause = parsed.groups[0].clauses[0]
    if clause.explicit_comparison:
        return None

    negative_clause = replace(
        clause,
        operator="<=",
        value=Decimal("-1"),
        explicit_comparison=False,
    )
    return replace(
        parsed,
        groups=(ParsedAndGroup((negative_clause,)),),
        raw_expression=raw,
    )


def parse_expression_request(text: str, repository: ItemRepository) -> ParsedSearch:
    raw = text.strip()
    negative_expression = _parse_negative_bare_expression(raw, repository)
    if negative_expression is not None:
        return ParsedSearch(
            intent="stat_expression",
            raw_query=raw,
            filter=negative_expression.item_filter,
            parsed_expression=negative_expression,
            primary_sort_ascending=True,
        )

    normalized_without_prefix = re.sub(r"^\s*stat\b", "", raw, count=1, flags=re.IGNORECASE).strip()
    if normalize_stat_text(normalized_without_prefix) == "upgrade for":
        return ParsedSearch(
            intent="stat_expression",
            raw_query=raw,
            error="'Upgrade for' is an item relationship, not a numeric stat.",
        )
    try:
        expression = parse_stat_expression(
            raw,
            repository.list_item_types(),
            repository.list_stat_names(),
        )
    except StatQuerySyntaxError as exc:
        return ParsedSearch(intent="stat_expression", raw_query=raw, error=str(exc))
    return ParsedSearch(
        intent="stat_expression",
        raw_query=raw,
        filter=expression.item_filter,
        parsed_expression=expression,
    )


def _resolve_item_type_for_database(
    text: str,
    repository: ItemRepository,
) -> tuple[str, tuple[str, ...]] | None:
    resolution, remaining = extract_item_filter(text, repository.list_item_types())
    if resolution is None or remaining.strip():
        return None
    return resolution.label, resolution.item_types


def _resolve_stat_for_database(text: str, repository: ItemRepository) -> str | None:
    typed = SEARCH_ONLY_STAT_ALIASES.get(normalize_stat_text(text), text)
    resolution = resolve_stat_term(typed, repository.list_stat_names(), allow_fuzzy=False)
    if resolution.status in {"exact", "alias"} and len(resolution.candidates) == 1:
        return resolution.candidates[0]
    return None


def _resolve_structured_item_filter(
    text: str,
    repository: ItemRepository,
) -> FilterResolution | None:
    resolution, remaining = extract_item_filter(text, repository.list_item_types())
    if resolution is None or remaining.strip():
        return None
    return resolution


def parse_structured_search_request(
    request: SearchIntentRequest,
    repository: ItemRepository,
    *,
    raw_query: str = "",
) -> ParsedSearch | None:
    if not request.stats or request.match not in {"all", "any"}:
        return None

    filter_resolution: FilterResolution | None = None
    if request.item_filter is not None:
        filter_resolution = _resolve_structured_item_filter(request.item_filter, repository)
        if filter_resolution is None:
            return None

    resolved_stats: list[tuple[object, str]] = []
    for stat in request.stats:
        canonical = _resolve_stat_for_database(stat.name, repository)
        if canonical is None:
            return None
        if stat.operator is None:
            if stat.value is not None:
                return None
        elif stat.value is None or isinstance(stat.value, bool):
            return None
        resolved_stats.append((stat, canonical))

    ordered_stats = list(resolved_stats)
    if request.sort_stat is not None:
        sort_key = normalize_stat_text(request.sort_stat)
        sort_index = next(
            (
                index
                for index, (stat, canonical) in enumerate(ordered_stats)
                if normalize_stat_text(stat.name) == sort_key
                or normalize_stat_text(canonical) == sort_key
            ),
            None,
        )
        if sort_index is None:
            return None
        ordered_stats.insert(0, ordered_stats.pop(sort_index))

    if len(ordered_stats) == 1 and ordered_stats[0][0].operator is None:
        stat, canonical = ordered_stats[0]
        return ParsedSearch(
            intent="stat_search",
            raw_query=raw_query or stat.name,
            stat=StatResolution(canonical, stat.name, 100.0, False),
            filter=filter_resolution,
        )

    clauses: list[ResolvedClause] = []
    for stat, canonical in ordered_stats:
        operator = stat.operator or ">="
        if stat.operator is None:
            value = Decimal("1")
        else:
            try:
                value = Decimal(str(stat.value))
            except (ValueError, ArithmeticError):
                return None
        clauses.append(
            ResolvedClause(
                typed_stat=stat.name,
                stat_name=canonical,
                operator=operator,
                value=value,
            )
        )

    if request.match == "all":
        groups = (ResolvedAndGroup(tuple(clauses)),)
    else:
        groups = tuple(ResolvedAndGroup((clause,)) for clause in clauses)

    return ParsedSearch(
        intent="stat_expression",
        raw_query=raw_query or "structured search",
        filter=filter_resolution,
        resolved_expression=ResolvedStatExpression(groups),
    )


def format_structured_search_request(request: SearchIntentRequest) -> str:
    rendered_stats: list[str] = []
    for stat in request.stats:
        rendered = stat.name
        if stat.operator is not None and stat.value is not None:
            rendered += f" {stat.operator} {_format_search_number(stat.value)}"
        rendered_stats.append(rendered)
    joiner = " AND " if request.match == "all" else " OR "
    label = joiner.join(rendered_stats)
    if request.item_filter:
        label += f" — {request.item_filter}"
    if request.sort_stat:
        label += f" — highest {request.sort_stat} first"
    return label


def build_search_stat_terms(available_stats: list[str]) -> tuple[str, ...]:
    terms: set[str] = {normalize_stat_text(stat) for stat in available_stats}
    terms.update(normalize_stat_text(alias) for alias in STAT_ALIASES)
    terms.update(normalize_stat_text(alias) for alias in STAT_AMBIGUOUS_GROUPS)
    terms.update(normalize_stat_text(alias) for alias in STAT_CATEGORY_ALIASES)
    terms.update(normalize_stat_text(alias) for alias in SEARCH_ONLY_STAT_ALIASES)
    terms.discard("")
    return tuple(sorted(terms, key=lambda value: (len(value.split()), len(value)), reverse=True))


def find_non_overlapping_stat_terms(text: str, terms: tuple[str, ...]) -> tuple[str, ...]:
    normalized = normalize_stat_text(text)
    tokens = normalized.split()
    occupied = [False] * len(tokens)
    hits: list[str] = []
    for term in terms:
        phrase = term.split()
        if not phrase or len(phrase) > len(tokens):
            continue
        for start in range(len(tokens) - len(phrase) + 1):
            end = start + len(phrase)
            if any(occupied[start:end]):
                continue
            if tokens[start:end] == phrase:
                hits.append(term)
                for index in range(start, end):
                    occupied[index] = True
                break
    return tuple(hits)


_NATURAL_UPGRADE_PATTERNS = (
    r"upgrade\s+for\s+(.+)",
    r"upgrades\s+for\s+(.+)",
    r"(?:show|find)\s+upgrades\s+for\s+(.+)",
    r"what\s+upgrades\s+from\s+(.+)",
    r"what\s+can\s+upgrade\s+(.+)",
    r"what\s+comes\s+after\s+(.+)",
    r"next\s+xtal\s+after\s+(.+)",
)


def extract_natural_upgrade_target(text: str) -> str | None:
    cleaned = " ".join(text.strip().rstrip("?.!").split())
    if not cleaned:
        return None
    for pattern in _NATURAL_UPGRADE_PATTERNS:
        match = re.fullmatch(pattern, cleaned, flags=re.IGNORECASE)
        if match is not None:
            target = match.group(1).strip()
            return target or None
    return None


def parse_search_query(query: str, repository: ItemRepository) -> ParsedSearch:
    raw = query.strip()
    normalized = normalize_stat_text(raw)
    if not normalized:
        return ParsedSearch("item_search", raw, item_query="")

    natural_upgrade_target = extract_natural_upgrade_target(raw)
    if natural_upgrade_target is not None:
        upgrade_exact = repository.exact_upgrade_name_matches(natural_upgrade_target)
        if len(upgrade_exact) == 1:
            canonical_query = f"upgrade {upgrade_exact[0].name}"
            return ParsedSearch("exact_upgrade", canonical_query, item_id=upgrade_exact[0].id)
        canonical_query = f"upgrade {natural_upgrade_target}"
        return ParsedSearch("upgrade_search", canonical_query, item_query=natural_upgrade_target)

    first, _, remainder = raw.partition(" ")
    command = normalize_name(first)
    remainder = remainder.strip()
    if command == "upgrade":
        if not remainder:
            return ParsedSearch(
                "upgrade_search",
                raw,
                item_query="",
                error="Enter an item name after 'upgrade'.",
            )
        upgrade_exact = repository.exact_upgrade_name_matches(remainder)
        if len(upgrade_exact) == 1:
            return ParsedSearch("exact_upgrade", raw, item_id=upgrade_exact[0].id)
        return ParsedSearch("upgrade_search", raw, item_query=remainder)

    exact = repository.exact_name_matches(raw)
    if len(exact) == 1:
        return ParsedSearch("exact_item", raw, item_id=exact[0].id)

    if command == "item":
        return ParsedSearch("item_search", raw, item_query=remainder)
    if command == "stat":
        if not remainder:
            return ParsedSearch("guided_stat", raw)
        return parse_expression_request(raw, repository)

    negative_expression = _parse_negative_bare_expression(raw, repository)
    if negative_expression is not None:
        return ParsedSearch(
            intent="stat_expression",
            raw_query=raw,
            filter=negative_expression.item_filter,
            parsed_expression=negative_expression,
            primary_sort_ascending=True,
        )

    ambiguous_terms = (
        set(STAT_AMBIGUOUS_GROUPS)
        | set(STAT_CATEGORY_ALIASES)
        | set(SEARCH_ONLY_STAT_ALIASES)
    )
    if looks_like_stat_expression(
        raw,
        repository.list_stat_names(),
        repository.list_item_types(),
        ambiguous_terms,
    ):
        return parse_expression_request(raw, repository)
    return ParsedSearch("item_search", raw, item_query=raw)
