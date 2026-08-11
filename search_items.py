from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import unicodedata
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Iterable, Literal

from rapidfuzz import fuzz

from toram_search.help_db import DatabaseActionRequest, DatabaseQuestionService, HelpService
from toram_search.fallback import (
    FallbackOutcome,
    QwenFallbackService,
    SearchIntentRequest,
)
from toram_search.llm import OllamaQwenClient
from toram_search.session import FailedQueryContext, classify_screen_input

SCRIPT_VERSION = "2026.08.08-direct-structured-intent-v11"

DEFAULT_DATABASE = Path("coryn_data/database/items.sqlite")
PAGE_SIZE = 5
MIN_QUERY_LENGTH = 2
FUZZY_STAT_THRESHOLD = 70.0
ITEM_FUZZY_RELEVANCE_THRESHOLD = 70.0
SEARCH_ONLY_STAT_ALIASES = {
    "aggro": "aggro %",
}

from toram_data.aliases import (
    ALL_CRYSTA_TYPES,
    ITEM_TYPE_ALIASES,
    ITEM_WORD_ALIASES,
    MAIN_WEAPON_TYPES,
    STAT_ALIASES,
    STAT_AMBIGUOUS_GROUPS,
    STAT_CATEGORY_ALIASES,
    expand_stat_aliases,
    is_crysta_item_type,
    normalize_name,
    normalize_stat_text,
    resolve_stat_term,
)

from toram_data.item_filters import (
    extract_item_filter,
    list_item_filter_phrases,
    resolve_item_filter,
)

from toram_data.stat_query import (
    ItemTypeFilter,
    ParsedAndGroup,
    ParsedStatExpression,
    ResolvedAndGroup,
    ResolvedClause,
    ResolvedStatExpression,
    StatQuerySyntaxError,
    compare_amount,
    format_resolved_expression,
    looks_like_stat_expression,
    parse_stat_expression,
)

from toram_data.search_models import (
    ClauseMatch,
    ItemDetail,
    ItemSummary,
    RankedExpressionItem,
    RankedStatItem,
    StatRow,
    UpgradeGraph,
)
from toram_data.search_repository import ItemRepository

FilterResolution = ItemTypeFilter

@dataclass(frozen=True)
class RankedItem:
    item: ItemSummary
    score: float
    match_kind: str


@dataclass(frozen=True)
class StatResolution:
    stat_name: str
    matched_text: str
    confidence: float
    requires_confirmation: bool


@dataclass(frozen=True)
class UpgradeDisplay:
    selected_name: str
    selected_paths: tuple[str, ...]
    tree_lines: tuple[str, ...]


def build_upgrade_display(graph: UpgradeGraph, selected_item_id: int) -> UpgradeDisplay:
    all_nodes = {**graph.missing_nodes, **graph.nodes}
    selected = all_nodes.get(
        selected_item_id,
        ItemSummary(selected_item_id, "Unknown item", "Unknown"),
    )

    def node(node_id: int) -> ItemSummary:
        return all_nodes.get(node_id, ItemSummary(node_id, "Unknown item", "Unknown"))

    def sort_key(node_id: int) -> tuple[str, int]:
        value = node(node_id)
        return value.name.casefold(), node_id

    incoming = {
        target_id
        for target_ids in graph.edges.values()
        for target_id in target_ids
    }
    roots = sorted(
        (node_id for node_id in all_nodes if node_id not in incoming),
        key=sort_key,
    )
    traversal_roots = roots or [selected_item_id]

    path_ids: list[tuple[int, ...]] = []

    def collect_paths(node_id: int, active: tuple[int, ...]) -> None:
        if node_id in active:
            return
        current = active + (node_id,)
        if node_id == selected_item_id:
            path_ids.append(current)
            return
        for child_id in sorted(graph.edges.get(node_id, ()), key=sort_key):
            collect_paths(child_id, current)

    for root_id in traversal_roots:
        collect_paths(root_id, ())
    if not path_ids:
        path_ids = [(selected_item_id,)]

    path_ids.sort(
        key=lambda path_ids_value: tuple(
            (node(path_node_id).name.casefold(), path_node_id)
            for path_node_id in path_ids_value
        )
    )
    selected_paths = tuple(
        f"{index}. " + " → ".join(node(node_id).name for node_id in path_value)
        for index, path_value in enumerate(path_ids, start=1)
    )

    tree_lines: list[str] = []
    expanded: set[int] = set()

    def render_node(
        node_id: int,
        *,
        prefix: str,
        is_last: bool,
        is_root: bool,
        active_path: frozenset[int],
    ) -> None:
        value = node(node_id)
        connector = "" if is_root else ("└── " if is_last else "├── ")
        label = value.name + ("  ◀ selected" if node_id == selected_item_id else "")
        if node_id in active_path:
            tree_lines.append(prefix + connector + label + " [cycle]")
            return
        if node_id in expanded:
            tree_lines.append(prefix + connector + label + "  ↩ already shown")
            return

        tree_lines.append(prefix + connector + label)
        expanded.add(node_id)
        children = sorted(graph.edges.get(node_id, ()), key=sort_key)
        child_prefix = prefix if is_root else prefix + ("    " if is_last else "│   ")
        next_active = active_path | {node_id}
        for index, child_id in enumerate(children):
            render_node(
                child_id,
                prefix=child_prefix,
                is_last=index == len(children) - 1,
                is_root=False,
                active_path=next_active,
            )

    for root_index, root_id in enumerate(traversal_roots):
        if root_index:
            tree_lines.append("")
        render_node(
            root_id,
            prefix="",
            is_last=True,
            is_root=True,
            active_path=frozenset(),
        )

    for node_id in sorted(all_nodes, key=sort_key):
        if node_id not in expanded:
            if tree_lines:
                tree_lines.append("")
            render_node(
                node_id,
                prefix="",
                is_last=True,
                is_root=True,
                active_path=frozenset(),
            )

    return UpgradeDisplay(selected.name, selected_paths, tuple(tree_lines))


SearchIntent = Literal["exact_item", "item_search", "stat_search", "stat_choices", "guided_stat", "stat_expression", "exact_upgrade", "upgrade_search"]


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


@dataclass(frozen=True)
class DeterministicRoute:
    kind: Literal["search", "help", "database", "fallback", "refuse"]
    parsed: ParsedSearch | None = None
    database_request: DatabaseActionRequest | None = None
    help_text: str | None = None
    record_failure: bool = False


@dataclass(frozen=True)
class ResultScreenOutcome:
    kind: Literal["selected", "new_query", "new", "quit"]
    query: str | None = None
    search_request: SearchIntentRequest | None = None


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


def page_stat_results(
    results: list[RankedStatItem],
    *,
    page: int,
    page_size: int = PAGE_SIZE,
) -> list[RankedStatItem]:
    start = page * page_size
    return results[start:start + page_size]


def page_expression_results(
    results: list[RankedExpressionItem],
    page: int,
    page_size: int = PAGE_SIZE,
) -> list[RankedExpressionItem]:
    start = max(page, 0) * page_size
    return results[start:start + page_size]


def _format_number(value: Any, *, signed: bool = False) -> str:
    if value is None:
        return "?"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    text = str(int(number)) if number.is_integer() else f"{number:g}"
    if signed and number > 0:
        return "+" + text
    return text


_UNAVAILABLE_PRESENCE_STATS = frozenset({
    "flinch unavailable",
    "stun unavailable",
    "tumble unavailable",
})


def _is_unavailable_presence_value(stat_name: object, amount: object) -> bool:
    name = normalize_stat_text(str(stat_name or ""))
    if name not in _UNAVAILABLE_PRESENCE_STATS:
        return False
    try:
        return float(amount) == 1.0
    except (TypeError, ValueError):
        return False


def format_stat_value(stat_name: object, amount: object, *, signed: bool = True) -> str:
    if _is_unavailable_presence_value(stat_name, amount):
        return ""
    return _format_number(amount, signed=signed)


_STAT_DISPLAY_ALIASES = {
    "motion speed %": "Action Speed",
}


def format_stat_name(stat_name: object) -> tuple[str, str]:
    raw_name = str(stat_name or "Unknown stat")
    normalized = normalize_stat_text(raw_name)
    has_trailing_percent = normalized.endswith(" %") and raw_name.rstrip().endswith("%")
    visible_name = raw_name.rstrip()
    value_suffix = "%" if has_trailing_percent else ""
    if has_trailing_percent:
        visible_name = visible_name[:-1].rstrip()
    visible_name = _STAT_DISPLAY_ALIASES.get(normalized, visible_name)
    return visible_name, value_suffix


def format_stat_display(stat_name: object, amount: object) -> str:
    visible_name, value_suffix = format_stat_name(stat_name)
    value = format_stat_value(stat_name, amount, signed=True)
    if not value:
        return visible_name
    return f"{visible_name} {value}{value_suffix}"


def _parse_conditions(raw: Any) -> list[str]:
    if not isinstance(raw, str):
        return []
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return [str(item) for item in value] if isinstance(value, list) else []


_EQUIPMENT_CONDITION_NAMES = {
    "1 handed sword": "1-Handed Sword",
    "one handed sword": "1-Handed Sword",
    "2 handed sword": "2-Handed Sword",
    "two handed sword": "2-Handed Sword",
    "bow": "Bow",
    "bowgun": "Bowgun",
    "katana": "Katana",
    "staff": "Staff",
    "magic device": "Magic Device",
    "knuckles": "Knuckles",
    "halberd": "Halberd",
    "arrow": "Arrow",
    "dagger": "Dagger",
    "shield": "Shield",
    "light armor": "Light Armor",
    "heavy armor": "Heavy Armor",
    "additional gear": "Additional Gear",
    "additional": "Additional Gear",
    "armor": "Armor",
    "dual swords": "Dual Swords",
    "knuckle": "Knuckles",
    "ninjutsu scroll": "Ninjutsu Scroll",
    "special gear": "Special Gear",
    "special": "Special Gear",
}


def _condition_fields(row: StatRow | dict[str, Any]) -> tuple[str | None, str, bool]:
    if isinstance(row, StatRow):
        return row.condition_text, row.conditions_json, row.needs_condition_review
    return (
        row.get("condition_text"),
        str(row.get("conditions_json") or "[]"),
        bool(row.get("needs_condition_review")),
    )


def _raw_condition_label(row: StatRow | dict[str, Any]) -> str | None:
    condition_text, conditions_json, _needs_review = _condition_fields(row)
    if condition_text:
        return str(condition_text)
    conditions = _parse_conditions(conditions_json)
    return ", ".join(conditions) if conditions else None


def _equipment_condition_names(row: StatRow | dict[str, Any]) -> tuple[str, ...]:
    condition_text, conditions_json, _needs_review = _condition_fields(row)
    parts = _parse_conditions(conditions_json)
    if not parts and condition_text:
        cleaned = re.sub(r"\s+only\s*$", "", str(condition_text), flags=re.IGNORECASE)
        parts = [part.strip() for part in cleaned.split(",") if part.strip()]
    if not parts:
        return ()

    visible: list[str] = []
    for part in parts:
        normalized = normalize_name(
            re.sub(r"\s+only\s*$", "", part, flags=re.IGNORECASE)
        ).replace("_", " ")
        display = _EQUIPMENT_CONDITION_NAMES.get(normalized)
        if display is None:
            return ()
        if display not in visible:
            visible.append(display)
    return tuple(visible)


def format_condition_display(row: StatRow | dict[str, Any]) -> str | None:
    names = _equipment_condition_names(row)
    raw = _raw_condition_label(row)
    _condition_text, _conditions_json, needs_review = _condition_fields(row)
    label = "With " + " / ".join(names) if names else (raw or "")
    if needs_review:
        label = (label + " [needs review]").strip()
    return label or None


def _condition_label(row: StatRow | dict[str, Any]) -> str | None:
    return format_condition_display(row)


@dataclass(frozen=True)
class StatDisplayGroup:
    heading: str | None
    lines: tuple[str, ...]


def build_item_stat_groups(
    stats: Iterable[dict[str, Any]],
) -> tuple[StatDisplayGroup, ...]:
    unconditional: list[str] = []
    order: list[str] = []
    grouped: dict[str, list[str]] = {}

    for stat in stats:
        line = format_stat_display(
            stat.get("stat_name") or "Unknown stat",
            stat.get("amount"),
        )
        _condition_text, _conditions_json, needs_review = _condition_fields(stat)
        equipment_names = _equipment_condition_names(stat)
        raw_condition = _raw_condition_label(stat)

        if needs_review:
            line += " [needs review]"

        if equipment_names:
            headings = tuple(f"With {name}" for name in equipment_names)
        elif raw_condition:
            headings = (raw_condition,)
        else:
            unconditional.append(line)
            continue

        for heading in headings:
            if heading not in grouped:
                grouped[heading] = []
                order.append(heading)
            grouped[heading].append(line)

    output: list[StatDisplayGroup] = []
    if unconditional:
        output.append(StatDisplayGroup(None, tuple(unconditional)))
    output.extend(
        StatDisplayGroup(heading, tuple(grouped[heading]))
        for heading in order
    )
    return tuple(output)


def render_item(detail: ItemDetail) -> str:
    lines = [
        "",
        detail.summary.name,
        "=" * len(detail.summary.name),
        f"ID: {detail.summary.id}",
        f"Type: {detail.summary.item_type}",
    ]
    if detail.sell_price is not None:
        lines.append(f"Sell price: {_format_number(detail.sell_price)}")
    if detail.process_material or detail.process_amount is not None:
        material = detail.process_material or "Unknown"
        lines.extend(["", "Process:", f"- {material} × {_format_number(detail.process_amount)}"])
    if detail.badge:
        lines.append(f"Badge: {detail.badge}")
    if detail.note:
        lines.extend(["", "Notes:", str(detail.note)])

    lines.extend(["", "Stats:"])
    groups = build_item_stat_groups(detail.stats)
    if not groups:
        lines.append("- None")
    else:
        for group_index, group in enumerate(groups):
            if group.heading is not None:
                if group_index > 0:
                    lines.append("")
                lines.append(f"{group.heading}:")
            lines.extend(f"- {line}" for line in group.lines)

    if detail.upgrade_predecessors or detail.upgrade_successors:
        lines.extend(["", "Upgrade:"])
        for item in detail.upgrade_predecessors:
            lines.append(f"- Previous: {item.name} — ID {item.id}")
        for item in detail.upgrade_successors:
            lines.append(f"- Next: {item.name} — ID {item.id}")

    lines.extend(["", "Obtained from:"])
    if not detail.sources:
        lines.append("- Unknown")
    else:
        for source in detail.sources:
            source_name = source.get("source_name") or "Unknown"
            details: list[str] = []
            if source.get("source_id") is not None:
                details.append(f"source ID {source['source_id']}")
            if source.get("level") is not None:
                details.append(f"Lv {source['level']}")
            if source.get("map"):
                details.append(str(source["map"]))
            if source.get("dye"):
                details.append(f"Dye: {source['dye']}")
            label = str(source_name)
            if details:
                label += " — " + " — ".join(details)
            lines.append(f"- {label}")
            if source.get("source_url"):
                lines.append(f"  Source URL: {source['source_url']}")
            if source.get("lookup_error"):
                lines.append(f"  Lookup warning: {source['lookup_error']}")

    if detail.images:
        lines.extend(["", f"Images: {len(detail.images)}"])
    if detail.page_url:
        lines.extend(["", f"Coryn page: {detail.page_url}"])
    return "\n".join(lines)


def render_upgrade_terminal(graph: UpgradeGraph, selected_item_id: int) -> str:
    display = build_upgrade_display(graph, selected_item_id)
    lines = [
        "",
        f"Upgrade Tree — {display.selected_name}",
        "",
        "Selected paths",
        *display.selected_paths,
        "",
        "Full tree",
        *display.tree_lines,
    ]
    return "\n".join(lines)


def render_direct_upgrade_results(
    base_name: str,
    results: list[RankedItem],
    page: int,
) -> str:
    current = page_results(results, page=page)
    start = page * PAGE_SIZE
    end = start + len(current)
    lines = [
        "",
        f"Upgrades from {base_name}",
        f"Showing {start + 1 if current else 0}–{end} of {len(results)}",
        "",
    ]
    for index, result in enumerate(current, start=1):
        lines.append(f"{index}. {result.item.name} — {result.item.item_type}")
    if not results:
        lines.append("No direct upgrade crystas found.")
    lines.extend([
        "",
        "Choose 1–5, type 'next', 'prev', a new search, or 'quit'.",
    ])
    return "\n".join(lines)


def render_suggestions(query: str, results: list[RankedItem], page: int) -> str:
    current = page_results(results, page=page)
    start = page * PAGE_SIZE
    end = start + len(current)
    lines = ["", f'No unique exact match for "{query}".', f"Suggestions {start + 1}–{end} of {len(results)}:", ""]
    for index, result in enumerate(current, start=1):
        lines.append(
            f"{index}. {result.item.name} — {result.item.item_type} — "
            f"ID {result.item.id} ({result.score:.1f})"
        )
    if results and results[0].score < 60:
        lines.extend(["", "The match is unclear. Add another word or correct the spelling."])
    lines.extend(["", "Choose 1–5, type 'next', 'prev', a new search, or 'quit'."])
    return "\n".join(lines)


def render_stat_results(
    stat_name: str,
    filter_resolution: FilterResolution | None,
    results: list[RankedStatItem],
    page: int,
) -> str:
    current = page_stat_results(results, page=page)
    start = page * PAGE_SIZE
    end = start + len(current)
    filter_label = filter_resolution.label if filter_resolution else "All item types"
    lines = [
        "",
        f"Searching stat: {stat_name}",
        f"Filter: {filter_label}",
        "Order: Highest first",
        f"Showing {start + 1 if current else 0}–{end} of {len(results)}",
        "",
    ]
    for index, result in enumerate(current, start=1):
        primary_line = format_stat_display(stat_name, result.primary.amount)
        condition = format_condition_display(result.primary)
        if condition:
            primary_line += f" [{condition}]"
        lines.append(
            f"{index}. {result.item.name} — {result.item.item_type} — "
            + primary_line
        )
        for alternative in result.alternatives:
            alternative_line = "   Also: " + format_stat_display(
                stat_name, alternative.amount
            )
            alternative_condition = format_condition_display(alternative)
            if alternative_condition:
                alternative_line += f" [{alternative_condition}]"
            lines.append(alternative_line)
    if not results:
        lines.append("No matching items found.")
    lines.extend([
        "",
        "Commands: 1–5, next, prev, filter <type>, filter all, new, quit",
    ])
    return "\n".join(lines)


def render_expression_results(
    expression: ResolvedStatExpression,
    filter_resolution: FilterResolution | None,
    results: list[RankedExpressionItem],
    page: int,
    *,
    primary_sort_ascending: bool = False,
) -> str:
    current = page_expression_results(results, page)
    start = page * PAGE_SIZE
    end = start + len(current)
    primary_name = expression.groups[0].clauses[0].stat_name
    lines = [
        "",
        f"Expression: {format_resolved_expression(expression)}",
        f"Filter: {filter_resolution.label if filter_resolution else 'All item types'}",
        f"Order: {primary_name} {'lowest' if primary_sort_ascending else 'highest'} first; items without {primary_name} follow alphabetically",
        f"Showing {start + 1 if current else 0}–{end} of {len(results)}",
        "",
    ]
    for number, result in enumerate(current, start=1):
        lines.append(f"{number}. {result.item.name} — {result.item.item_type}")
        for match in result.matches:
            for row_index, row in enumerate(match.rows):
                prefix = "   " if row_index == 0 else "   Also: "
                rendered = format_stat_display(match.clause.stat_name, row.amount)
                condition = format_condition_display(row)
                if condition:
                    rendered += f" [{condition}]"
                lines.append(prefix + rendered)
    if not results:
        lines.append("No matching items found.")
    lines.extend(["", "Commands: 1–5, next, prev, filter <type>, filter all, new, quit"])
    return "\n".join(lines)


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


def resolve_expression_interactively(
    parsed: ParsedSearch,
    repository: ItemRepository,
    *,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> ParsedSearch | None:
    if parsed.error:
        output_fn(parsed.error)
        return None
    if parsed.resolved_expression is not None:
        return parsed
    if parsed.parsed_expression is None:
        output_fn("No stat expression was parsed.")
        return None

    available_stats = repository.list_stat_names()
    resolved_groups: list[ResolvedAndGroup] = []
    for group in parsed.parsed_expression.groups:
        resolved_clauses: list[ResolvedClause] = []
        for clause in group.clauses:
            typed_for_resolution = SEARCH_ONLY_STAT_ALIASES.get(
                normalize_stat_text(clause.typed_stat),
                clause.typed_stat,
            )
            resolution = resolve_stat_term(
                typed_for_resolution,
                available_stats,
                allow_fuzzy=True,
            )
            if resolution.status in {"exact", "alias"}:
                stat_name = resolution.candidates[0]
            elif resolution.status == "ambiguous":
                output_fn(f'What does "{clause.typed_stat}" mean?')
                for index, label in enumerate(resolution.display_labels, start=1):
                    output_fn(f"{index}. {label}")
                output_fn(f"{len(resolution.candidates) + 1}. Cancel")
                while True:
                    try:
                        answer = input_fn("Choose a stat number: ").strip()
                    except (EOFError, KeyboardInterrupt, StopIteration):
                        return None
                    if answer.casefold() in {"c", "cancel", "new"}:
                        return None
                    if answer.isdigit():
                        selected = int(answer) - 1
                        if 0 <= selected < len(resolution.candidates):
                            stat_name = resolution.candidates[selected]
                            break
                        if selected == len(resolution.candidates):
                            return None
                    output_fn(f"Choose a number from 1 to {len(resolution.candidates) + 1}.")
            elif resolution.status == "fuzzy":
                label = resolution.display_labels[0]
                output_fn(f'Did you mean {label} for "{clause.typed_stat}"?')
                try:
                    answer = input_fn("Use this stat? [Y/n]: ").strip().casefold()
                except (EOFError, KeyboardInterrupt, StopIteration):
                    return None
                if answer not in {"", "y", "yes"}:
                    return None
                stat_name = resolution.candidates[0]
            else:
                output_fn(f'Unknown stat: "{clause.typed_stat}"')
                return None
            resolved_clauses.append(
                ResolvedClause(
                    typed_stat=clause.typed_stat,
                    stat_name=stat_name,
                    operator=clause.operator,
                    value=clause.value,
                )
            )
        resolved_groups.append(ResolvedAndGroup(tuple(resolved_clauses)))

    return replace(
        parsed,
        resolved_expression=ResolvedStatExpression(tuple(resolved_groups)),
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


def _format_structured_search_request(request: SearchIntentRequest) -> str:
    rendered_stats: list[str] = []
    for stat in request.stats:
        rendered = stat.name
        if stat.operator is not None and stat.value is not None:
            rendered += f" {stat.operator} {_format_number(stat.value)}"
        rendered_stats.append(rendered)
    joiner = " AND " if request.match == "all" else " OR "
    label = joiner.join(rendered_stats)
    if request.item_filter:
        label += f" — {request.item_filter}"
    if request.sort_stat:
        label += f" — highest {request.sort_stat} first"
    return label


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

def prompt_guided_stat(
    repository: ItemRepository,
    *,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> ParsedSearch | None:
    try:
        stat_text = input_fn("Stat: ").strip()
        if not stat_text:
            output_fn("Stat cannot be empty.")
            return None
        filter_text = input_fn("Item type filter (optional): ").strip()
    except (EOFError, KeyboardInterrupt, StopIteration):
        return None
    combined = stat_text if not filter_text else f"{stat_text} {filter_text}"
    parsed = parse_expression_request(combined, repository)
    if parsed.error:
        output_fn(parsed.error)
        return None
    return parsed



def prompt_stat_choice(
    parsed: ParsedSearch,
    *,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> ParsedSearch | None:
    if not parsed.stat_choices:
        return None

    lines = ["Which stat did you mean?", ""]
    lines.extend(
        f"{index}. {stat_name}"
        for index, stat_name in enumerate(parsed.stat_choices, start=1)
    )
    output_fn("\n".join(lines))

    while True:
        try:
            answer = input_fn("Choose a stat number, or 'cancel': ").strip()
        except (EOFError, KeyboardInterrupt, StopIteration):
            return None
        if answer.casefold() in {"c", "cancel", "new"}:
            return None
        if answer.isdigit():
            index = int(answer) - 1
            if 0 <= index < len(parsed.stat_choices):
                stat_name = parsed.stat_choices[index]
                return ParsedSearch(
                    intent="stat_search",
                    raw_query=parsed.raw_query,
                    stat=StatResolution(stat_name, stat_name, 100.0, False),
                    filter=parsed.filter,
                )
        output_fn(f"Choose a number from 1 to {len(parsed.stat_choices)}.")

def apply_filter_command(
    command: str,
    current: FilterResolution | None,
    repository: ItemRepository,
) -> tuple[FilterResolution | None, str | None]:
    text = command.strip()
    if not text.casefold().startswith("filter"):
        return current, "Expected a filter command."
    requested = text[6:].strip()
    if not requested:
        return current, "Enter an item type after 'filter'."
    if normalize_name(requested) == "all":
        return None, None
    resolution = resolve_item_filter(requested, repository.list_item_types())
    if resolution is None:
        return current, f'Unknown item-type filter: "{requested}"'
    return resolution, None


def _build_fallback_service(
    repository: ItemRepository,
    all_items: list[ItemSummary],
    help_service: HelpService,
    database_service: DatabaseQuestionService,
    llm_client: object,
) -> QwenFallbackService:
    del all_items, help_service
    aliases: list[str] = []
    for alias, target in sorted(STAT_ALIASES.items()):
        aliases.append(f"{alias} -> {target}")
    for alias, targets in sorted(STAT_AMBIGUOUS_GROUPS.items()):
        aliases.append(f"{alias} -> {' / '.join(targets)}")
    for alias, target in sorted(SEARCH_ONLY_STAT_ALIASES.items()):
        aliases.append(f"{alias} -> {target}")

    filter_labels: list[str] = []
    seen: set[str] = set()
    for row in list_item_filter_phrases(repository.list_item_types()):
        value = f"{row.phrase} -> {row.label}"
        if value not in seen:
            seen.add(value)
            filter_labels.append(value)

    return QwenFallbackService(
        llm_client,
        validate_search_request=lambda request: parse_structured_search_request(
            request,
            repository,
        ) is not None,
        validate_database_action=database_service.validate_request,
        stat_catalog=tuple(repository.list_stat_names()),
        alias_catalog=tuple(aliases),
        item_filter_catalog=tuple(filter_labels),
    )


def _interactive_search_requests(
    original_query: str,
    outcome: FallbackOutcome,
    *,
    input_fn: Callable[[str], str],
    output_fn: Callable[[str], None],
) -> ResultScreenOutcome:
    requests = outcome.search_requests
    if not requests:
        output_fn("I couldn't convert that into a supported item/stat search. Type 'help' for search examples.")
        return ResultScreenOutcome("new")

    output_fn(f'I interpreted "{original_query}" as:')
    if len(requests) == 1:
        request = requests[0]
        output_fn(_format_structured_search_request(request))
        while True:
            try:
                answer = input_fn("[y] Use this search  [n] Enter another query: ").strip()
            except (EOFError, KeyboardInterrupt, StopIteration):
                return ResultScreenOutcome("quit")
            lowered = answer.casefold()
            if lowered in {"y", "yes", "1"}:
                return ResultScreenOutcome("selected", search_request=request)
            if lowered in {"n", "no", "new", ""}:
                return ResultScreenOutcome("new")
            if lowered in {"q", "quit", "exit"}:
                return ResultScreenOutcome("quit")
            return ResultScreenOutcome("new_query", query=answer)

    output_fn("Possible interpretations:")
    for index, request in enumerate(requests, start=1):
        output_fn(f"{index}. {_format_structured_search_request(request)}")
    output_fn("Enter a number to use an interpretation, or type a new query.")
    while True:
        try:
            answer = input_fn("> ").strip()
        except (EOFError, KeyboardInterrupt, StopIteration):
            return ResultScreenOutcome("quit")
        parsed = classify_screen_input(answer, allow_filter=False, allow_new_command=False)
        if parsed.kind == "quit":
            return ResultScreenOutcome("quit")
        if parsed.kind == "select":
            assert parsed.selection is not None
            if 1 <= parsed.selection <= len(requests):
                return ResultScreenOutcome(
                    "selected",
                    search_request=requests[parsed.selection - 1],
                )
            output_fn(f"Choose a number from 1 to {len(requests)}.")
            continue
        if parsed.kind == "empty":
            continue
        return ResultScreenOutcome("new_query", query=answer)


def _confirm_interpretation(
    parsed: ParsedSearch,
    *,
    input_fn: Callable[[str], str],
    output_fn: Callable[[str], None],
) -> bool:
    if not parsed.requires_confirmation or parsed.stat is None:
        return True
    output_fn(f"Suggested stat: {parsed.stat.stat_name}")
    output_fn(f"Filter: {parsed.filter.label if parsed.filter else 'All item types'}")
    try:
        answer = input_fn("Use this search? [Y/n]: ").strip().casefold()
    except (EOFError, KeyboardInterrupt, StopIteration):
        return False
    return answer in {"", "y", "yes"}


def _interactive_item_results(
    repository: ItemRepository,
    query: str,
    all_items: list[ItemSummary],
    *,
    input_fn: Callable[[str], str],
    output_fn: Callable[[str], None],
) -> ResultScreenOutcome:
    exact = repository.exact_name_matches(query)
    if len(exact) == 1:
        output_fn(render_item(repository.get_item(exact[0].id)))
        return ResultScreenOutcome("new")
    results = (
        [RankedItem(item=item, score=100.0, match_kind="exact") for item in exact]
        if len(exact) > 1 else rank_items(query, all_items)
    )
    if not results:
        output_fn("No suggestions found. Enter a more specific name.")
        return ResultScreenOutcome("new")
    if len(results) == 1:
        output_fn(render_item(repository.get_item(results[0].item.id)))
        return ResultScreenOutcome("selected")
    page = 0
    while True:
        current = page_results(results, page=page)
        if not current:
            page = max(page - 1, 0)
            output_fn("There are no more suggestions.")
            continue
        output_fn(render_suggestions(query, results, page))
        try:
            command = input_fn("> ").strip()
        except (EOFError, KeyboardInterrupt, StopIteration):
            return ResultScreenOutcome("quit")
        parsed_input = classify_screen_input(command, allow_filter=False, allow_new_command=False)
        if parsed_input.kind == "quit":
            return ResultScreenOutcome("quit")
        if parsed_input.kind == "next":
            if (page + 1) * PAGE_SIZE >= len(results):
                output_fn("There are no more suggestions.")
            else:
                page += 1
            continue
        if parsed_input.kind == "prev":
            if page == 0:
                output_fn("You are already on the first page.")
            else:
                page -= 1
            continue
        if parsed_input.kind == "select":
            assert parsed_input.selection is not None
            if 1 <= parsed_input.selection <= len(current):
                output_fn(render_item(repository.get_item(current[parsed_input.selection - 1].item.id)))
                return ResultScreenOutcome("selected")
            output_fn(f"Choose a number from 1 to {len(current)}.")
            continue
        if parsed_input.kind == "empty":
            output_fn("Enter a number, 'next', 'prev', a new search, or 'quit'.")
            continue
        return ResultScreenOutcome("new_query", query=command)


def _interactive_direct_upgrade_results(
    repository: ItemRepository,
    base_item: ItemSummary,
    successors: Iterable[ItemSummary],
    *,
    input_fn: Callable[[str], str],
    output_fn: Callable[[str], None],
) -> ResultScreenOutcome:
    unique = {item.id: item for item in successors}
    ordered = sorted(unique.values(), key=lambda item: (item.name.casefold(), item.id))
    results = [
        RankedItem(item=item, score=100.0, match_kind="upgrade")
        for item in ordered
    ]
    if not results:
        output_fn(f"No direct upgrade crystas found for {base_item.name}.")
        return ResultScreenOutcome("new")

    page = 0
    while True:
        current = page_results(results, page=page)
        if not current:
            page = max(page - 1, 0)
            output_fn("There are no more results.")
            continue
        output_fn(render_direct_upgrade_results(base_item.name, results, page))
        try:
            command = input_fn("> ").strip()
        except (EOFError, KeyboardInterrupt, StopIteration):
            return ResultScreenOutcome("quit")
        parsed_input = classify_screen_input(
            command,
            allow_filter=False,
            allow_new_command=False,
        )
        if parsed_input.kind == "quit":
            return ResultScreenOutcome("quit")
        if parsed_input.kind == "next":
            if (page + 1) * PAGE_SIZE >= len(results):
                output_fn("There are no more results.")
            else:
                page += 1
            continue
        if parsed_input.kind == "prev":
            if page == 0:
                output_fn("You are already on the first page.")
            else:
                page -= 1
            continue
        if parsed_input.kind == "select":
            assert parsed_input.selection is not None
            if 1 <= parsed_input.selection <= len(current):
                selected = current[parsed_input.selection - 1].item
                output_fn(render_item(repository.get_item(selected.id)))
                return ResultScreenOutcome("selected")
            output_fn(f"Choose a number from 1 to {len(current)}.")
            continue
        if parsed_input.kind == "empty":
            output_fn("Enter a number, 'next', 'prev', a new search, or 'quit'.")
            continue
        return ResultScreenOutcome("new_query", query=command)


def _show_upgrade_component(
    repository: ItemRepository,
    selected: ItemSummary,
    *,
    output_fn: Callable[[str], None],
) -> ResultScreenOutcome:
    graph = repository.get_upgrade_component(selected.id)
    output_fn(render_upgrade_terminal(graph, selected.id))
    return ResultScreenOutcome("selected")


def _interactive_upgrade_results(
    repository: ItemRepository,
    query: str,
    all_items: list[ItemSummary],
    *,
    input_fn: Callable[[str], str],
    output_fn: Callable[[str], None],
) -> ResultScreenOutcome:
    upgrade_items = [item for item in all_items if is_crysta_item_type(item.item_type)]
    exact = repository.exact_upgrade_name_matches(query)
    if len(exact) == 1:
        return _show_upgrade_component(
            repository,
            exact[0],
            output_fn=output_fn,
        )
    results = (
        [RankedItem(item=item, score=100.0, match_kind="exact") for item in exact]
        if len(exact) > 1 else rank_items(query, upgrade_items)
    )
    if not results:
        output_fn("No crysta suggestions found. Enter a more specific crysta name.")
        return ResultScreenOutcome("new")
    if len(results) == 1:
        return _show_upgrade_component(
            repository,
            results[0].item,
            output_fn=output_fn,
        )
    page = 0
    while True:
        current = page_results(results, page=page)
        if not current:
            page = max(page - 1, 0)
            output_fn("There are no more suggestions.")
            continue
        output_fn(render_suggestions(query, results, page))
        try:
            command = input_fn("> ").strip()
        except (EOFError, KeyboardInterrupt, StopIteration):
            return ResultScreenOutcome("quit")
        parsed_input = classify_screen_input(command, allow_filter=False, allow_new_command=False)
        if parsed_input.kind == "quit":
            return ResultScreenOutcome("quit")
        if parsed_input.kind == "next":
            if (page + 1) * PAGE_SIZE >= len(results):
                output_fn("There are no more suggestions.")
            else:
                page += 1
            continue
        if parsed_input.kind == "prev":
            if page == 0:
                output_fn("You are already on the first page.")
            else:
                page -= 1
            continue
        if parsed_input.kind == "select":
            assert parsed_input.selection is not None
            if 1 <= parsed_input.selection <= len(current):
                selected = current[parsed_input.selection - 1].item
                return _show_upgrade_component(
                    repository,
                    selected,
                    output_fn=output_fn,
                )
            output_fn(f"Choose a number from 1 to {len(current)}.")
            continue
        if parsed_input.kind == "empty":
            output_fn("Enter a number, 'next', 'prev', a new search, or 'quit'.")
            continue
        return ResultScreenOutcome("new_query", query=command)


def interactive_expression_results(
    repository: ItemRepository,
    parsed: ParsedSearch,
    *,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> ResultScreenOutcome:
    if parsed.resolved_expression is None:
        output_fn(parsed.error or "No stat expression was resolved.")
        return ResultScreenOutcome("new")
    active_filter = parsed.filter
    page = 0
    while True:
        results = repository.search_by_expression(
            parsed.resolved_expression,
            active_filter.item_types if active_filter else None,
            primary_sort_ascending=parsed.primary_sort_ascending,
        )
        if len(results) == 1:
            output_fn(render_item(repository.get_item(results[0].item.id)))
            return ResultScreenOutcome("selected")
        max_page = max((len(results) - 1) // PAGE_SIZE, 0)
        page = min(page, max_page)
        current = page_expression_results(results, page)
        output_fn(render_expression_results(
            parsed.resolved_expression,
            active_filter,
            results,
            page,
            primary_sort_ascending=parsed.primary_sort_ascending,
        ))
        try:
            command = input_fn("> ").strip()
        except (EOFError, KeyboardInterrupt, StopIteration):
            return ResultScreenOutcome("quit")
        parsed_input = classify_screen_input(command, allow_filter=True, allow_new_command=True)
        if parsed_input.kind == "quit":
            return ResultScreenOutcome("quit")
        if parsed_input.kind == "new":
            return ResultScreenOutcome("new")
        if parsed_input.kind == "next":
            if (page + 1) * PAGE_SIZE >= len(results):
                output_fn("There are no more results.")
            else:
                page += 1
            continue
        if parsed_input.kind == "prev":
            if page == 0:
                output_fn("You are already on the first page.")
            else:
                page -= 1
            continue
        if parsed_input.kind == "filter":
            changed, error = apply_filter_command(command, active_filter, repository)
            if error:
                output_fn(error)
            else:
                active_filter = changed
                page = 0
            continue
        if parsed_input.kind == "select":
            assert parsed_input.selection is not None
            if 1 <= parsed_input.selection <= len(current):
                output_fn(render_item(repository.get_item(current[parsed_input.selection - 1].item.id)))
                return ResultScreenOutcome("selected")
            output_fn(f"Choose a number from 1 to {len(current)}.")
            continue
        if parsed_input.kind == "empty":
            output_fn("Use 1–5, next, prev, filter <type>, filter all, new, or type a new query.")
            continue
        return ResultScreenOutcome("new_query", query=command)


def interactive_stat_results(
    repository: ItemRepository,
    parsed: ParsedSearch,
    *,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> ResultScreenOutcome:
    if parsed.stat is None:
        output_fn(parsed.error or "No stat was selected.")
        return ResultScreenOutcome("new")
    active_filter = parsed.filter
    page = 0
    while True:
        results = repository.search_by_stat(
            parsed.stat.stat_name,
            active_filter.item_types if active_filter else None,
        )
        if len(results) == 1:
            output_fn(render_item(repository.get_item(results[0].item.id)))
            return ResultScreenOutcome("selected")
        max_page = max((len(results) - 1) // PAGE_SIZE, 0)
        page = min(page, max_page)
        current = page_stat_results(results, page=page)
        output_fn(render_stat_results(parsed.stat.stat_name, active_filter, results, page))
        try:
            command = input_fn("> ").strip()
        except (EOFError, KeyboardInterrupt, StopIteration):
            return ResultScreenOutcome("quit")
        parsed_input = classify_screen_input(command, allow_filter=True, allow_new_command=True)
        if parsed_input.kind == "quit":
            return ResultScreenOutcome("quit")
        if parsed_input.kind == "new":
            return ResultScreenOutcome("new")
        if parsed_input.kind == "next":
            if (page + 1) * PAGE_SIZE >= len(results):
                output_fn("There are no more results.")
            else:
                page += 1
            continue
        if parsed_input.kind == "prev":
            if page == 0:
                output_fn("You are already on the first page.")
            else:
                page -= 1
            continue
        if parsed_input.kind == "filter":
            changed, error = apply_filter_command(command, active_filter, repository)
            if error:
                output_fn(error)
            else:
                active_filter = changed
                page = 0
            continue
        if parsed_input.kind == "select":
            assert parsed_input.selection is not None
            if 1 <= parsed_input.selection <= len(current):
                output_fn(render_item(repository.get_item(current[parsed_input.selection - 1].item.id)))
                return ResultScreenOutcome("selected")
            output_fn(f"Choose a number from 1 to {len(current)}.")
            continue
        if parsed_input.kind == "empty":
            output_fn("Use 1–5, next, prev, filter <type>, filter all, new, or type a new query.")
            continue
        return ResultScreenOutcome("new_query", query=command)


def interactive_search(
    repository: ItemRepository,
    *,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
    llm_client: object | None = None,
    failed_context: FailedQueryContext | None = None,
) -> int:
    all_items = repository.list_items()
    if not all_items:
        output_fn("The database contains no items.")
        return 1

    help_service = HelpService()
    database_service = make_database_question_service(repository)
    context = failed_context if failed_context is not None else FailedQueryContext(max_entries=3)
    client = llm_client if llm_client is not None else OllamaQwenClient()
    fallback_service = _build_fallback_service(
        repository,
        all_items,
        help_service,
        database_service,
        client,
    )

    output_fn("Coryn Item Search")
    output_fn("Search an item name, try 'hp > 5000 and cr bow', use 'upgrade <crysta>', or type 'help'.")
    pending_query: str | None = None

    def handle_screen(outcome: ResultScreenOutcome) -> tuple[bool, str | None]:
        if outcome.kind == "quit":
            return True, None
        if outcome.kind == "selected":
            context.clear()
            return False, None
        if outcome.kind == "new_query":
            return False, outcome.query
        return False, None

    while True:
        try:
            query = pending_query if pending_query is not None else input_fn("\nSearch: ").strip()
        except (EOFError, KeyboardInterrupt, StopIteration):
            output_fn("Goodbye.")
            return 0
        pending_query = None
        if query.casefold() in {"q", "quit", "exit"}:
            output_fn("Goodbye.")
            return 0
        if not query:
            continue

        route = route_deterministically(
            query,
            repository,
            all_items,
            help_service,
            database_service,
        )
        if route.kind == "help":
            output_fn(route.help_text or "")
            continue
        if route.kind == "database":
            assert route.database_request is not None
            output_fn(database_service.execute(route.database_request))
            continue
        if route.kind == "refuse":
            output_fn("I can only help search this item database or answer questions about its stats, item types, counts, and supported search syntax.")
            continue
        if route.kind == "fallback":
            if route.record_failure:
                context.record_failure(query)
            outcome = fallback_service.interpret(query, context.snapshot())
            if outcome.kind == "search_requests":
                if outcome.search_requests:
                    context.set_latest_suggestion(
                        _format_structured_search_request(outcome.search_requests[0])
                    )
                screen = _interactive_search_requests(
                    query,
                    outcome,
                    input_fn=input_fn,
                    output_fn=output_fn,
                )
                if screen.kind == "quit":
                    output_fn("Goodbye.")
                    return 0
                if screen.kind == "new_query":
                    pending_query = screen.query
                    continue
                if screen.kind != "selected" or screen.search_request is None:
                    continue
                parsed_request = parse_structured_search_request(
                    screen.search_request,
                    repository,
                    raw_query=query,
                )
                if parsed_request is None:
                    output_fn("I couldn't convert that into a supported item/stat search. Type 'help' for search examples.")
                    continue
                context.clear()
                route = DeterministicRoute("search", parsed=parsed_request)
            elif outcome.kind == "unavailable":
                output_fn("I couldn't interpret that query automatically because the local Qwen fallback is unavailable. Normal item/stat search is still available.")
                continue
            elif outcome.kind == "refuse":
                output_fn("I can only help search this item database or answer questions about its stats, item types, counts, and supported search syntax.")
                continue
            elif outcome.kind == "database_action":
                if outcome.database_request is None:
                    output_fn("I couldn't convert that into a supported database question.")
                else:
                    output_fn(database_service.execute(outcome.database_request))
                continue
            elif outcome.kind == "help":
                help_text = help_service.answer_topic(outcome.help_topic or "")
                if help_text is None:
                    output_fn("I couldn't convert that into supported search help.")
                else:
                    output_fn(help_text)
                continue
            else:
                output_fn("I couldn't convert that into a supported item/stat search. Type 'help' for search examples.")
                continue

        parsed = route.parsed
        if parsed is None:
            continue
        if parsed.intent == "exact_upgrade":
            selected_id = int(parsed.item_id)
            selected = next((item for item in all_items if item.id == selected_id), None)
            if selected is None:
                output_fn("Selected crysta could not be found.")
                continue
            screen = _show_upgrade_component(
                repository,
                selected,
                output_fn=output_fn,
            )
            should_quit, new_query = handle_screen(screen)
            if should_quit:
                output_fn("Goodbye.")
                return 0
            pending_query = new_query
            continue
        if parsed.intent == "upgrade_search":
            if parsed.error:
                output_fn(parsed.error)
                continue
            screen = _interactive_upgrade_results(
                repository,
                parsed.item_query or "",
                all_items,
                input_fn=input_fn,
                output_fn=output_fn,
            )
            should_quit, new_query = handle_screen(screen)
            if should_quit:
                output_fn("Goodbye.")
                return 0
            pending_query = new_query
            continue
        if parsed.intent == "exact_item":
            output_fn(render_item(repository.get_item(int(parsed.item_id))))
            continue
        if parsed.intent == "guided_stat":
            guided = prompt_guided_stat(repository, input_fn=input_fn, output_fn=output_fn)
            if guided is None:
                continue
            parsed = guided
        if parsed.intent == "stat_expression":
            if parsed.error:
                output_fn(parsed.error)
                continue
            resolved = resolve_expression_interactively(
                parsed,
                repository,
                input_fn=input_fn,
                output_fn=output_fn,
            )
            if resolved is None:
                continue
            screen = interactive_expression_results(
                repository,
                resolved,
                input_fn=input_fn,
                output_fn=output_fn,
            )
            should_quit, new_query = handle_screen(screen)
            if should_quit:
                output_fn("Goodbye.")
                return 0
            pending_query = new_query
            continue
        if parsed.intent == "stat_choices":
            chosen = prompt_stat_choice(parsed, input_fn=input_fn, output_fn=output_fn)
            if chosen is None:
                continue
            parsed = chosen
        if parsed.intent == "stat_search":
            if parsed.error:
                output_fn(parsed.error)
                continue
            if not _confirm_interpretation(parsed, input_fn=input_fn, output_fn=output_fn):
                continue
            screen = interactive_stat_results(
                repository,
                parsed,
                input_fn=input_fn,
                output_fn=output_fn,
            )
            should_quit, new_query = handle_screen(screen)
            if should_quit:
                output_fn("Goodbye.")
                return 0
            pending_query = new_query
            continue

        item_query = parsed.item_query or query
        if len(normalize_name(item_query)) < MIN_QUERY_LENGTH:
            output_fn("Please enter at least 2 letters of the item name.")
            continue
        screen = _interactive_item_results(
            repository,
            item_query,
            all_items,
            input_fn=input_fn,
            output_fn=output_fn,
        )
        should_quit, new_query = handle_screen(screen)
        if should_quit:
            output_fn("Goodbye.")
            return 0
        pending_query = new_query


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Search Coryn items by name or stat, and display crysta-only upgrade chains."
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=DEFAULT_DATABASE,
        help=f"SQLite database path (default: {DEFAULT_DATABASE})",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        repository = ItemRepository(args.database.resolve())
    except (FileNotFoundError, RuntimeError, sqlite3.Error) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    try:
        return interactive_search(repository)
    finally:
        repository.close()


if __name__ == "__main__":
    raise SystemExit(main())
