from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from toram_data.aliases import (
    ALL_CRYSTA_TYPES,
    ITEM_TYPE_ALIASES,
    ITEM_WORD_ALIASES,
    MAIN_WEAPON_TYPES,
    STAT_ALIASES,
    normalize_name,
    normalize_stat_text,
)

ComparisonOperator = Literal[">", ">=", "<", "<=", "=", "=="]


@dataclass(frozen=True)
class ItemTypeFilter:
    label: str
    item_types: tuple[str, ...]
    consumed_text: str


@dataclass(frozen=True)
class ParsedClause:
    typed_stat: str
    operator: ComparisonOperator
    value: Decimal
    explicit_comparison: bool


@dataclass(frozen=True)
class ParsedAndGroup:
    clauses: tuple[ParsedClause, ...]


@dataclass(frozen=True)
class ParsedStatExpression:
    groups: tuple[ParsedAndGroup, ...]
    item_filter: ItemTypeFilter | None
    had_stat_prefix: bool
    raw_expression: str


@dataclass(frozen=True)
class ResolvedClause:
    typed_stat: str
    stat_name: str
    operator: ComparisonOperator
    value: Decimal


@dataclass(frozen=True)
class ResolvedAndGroup:
    clauses: tuple[ResolvedClause, ...]


@dataclass(frozen=True)
class ResolvedStatExpression:
    groups: tuple[ResolvedAndGroup, ...]


class StatQuerySyntaxError(ValueError):
    pass


_BOOLEAN_SPLIT_RE = re.compile(r"\b(and|or)\b", re.IGNORECASE)
_COMPARISON_RE = re.compile(r"(>=|<=|==|>|<|=)")
_NUMBER_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$")
_UNSUPPORTED_BOOLEAN_RE = re.compile(r"\b(not|without|no)\b", re.IGNORECASE)
_STAT_PREFIX_RE = re.compile(r"^\s*stat\b", re.IGNORECASE)


def compare_amount(amount: float, operator: ComparisonOperator, expected: Decimal) -> bool:
    actual = Decimal(str(amount))
    if operator == ">":
        return actual > expected
    if operator == ">=":
        return actual >= expected
    if operator == "<":
        return actual < expected
    if operator == "<=":
        return actual <= expected
    return actual == expected


def _format_decimal(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"", "-0", "+0"} else text


def format_resolved_expression(expression: ResolvedStatExpression) -> str:
    rendered_groups: list[str] = []
    for group in expression.groups:
        rendered_clauses = []
        for clause in group.clauses:
            operator = "=" if clause.operator == "==" else clause.operator
            rendered_clauses.append(
                f"{clause.stat_name} {operator} {_format_decimal(clause.value)}"
            )
        rendered_groups.append(" AND ".join(rendered_clauses))
    return " OR ".join(rendered_groups)


def _normalize_filter_text(value: str) -> str:
    tokens = normalize_stat_text(value).split()
    return " ".join(ITEM_WORD_ALIASES.get(token, token) for token in tokens)


def _available_types_by_normalized(available_item_types: set[str]) -> dict[str, str]:
    return {normalize_name(value): value for value in available_item_types}


def _existing_types(
    configured: tuple[str, ...],
    available_item_types: set[str],
) -> tuple[str, ...]:
    by_normalized = _available_types_by_normalized(available_item_types)
    output: list[str] = []
    for configured_type in configured:
        actual = by_normalized.get(normalize_name(configured_type))
        if actual is not None and actual not in output:
            output.append(actual)
    return tuple(output)


def _filter_candidates() -> list[tuple[str, str, tuple[str, ...]]]:
    combinations = {
        "wp xtal": (
            "Weapon Crysta + Red Enhancer",
            ("Weapon Crysta", "Enhancer Crysta (Red)"),
        ),
        "weapon xtal": (
            "Weapon Crysta + Red Enhancer",
            ("Weapon Crysta", "Enhancer Crysta (Red)"),
        ),
        "ring xtal": (
            "Special Crysta + Purple Enhancer",
            ("Special Crysta", "Enhancer Crysta (Purple)"),
        ),
        "rings xtal": (
            "Special Crysta + Purple Enhancer",
            ("Special Crysta", "Enhancer Crysta (Purple)"),
        ),
        "special xtal": (
            "Special Crysta + Purple Enhancer",
            ("Special Crysta", "Enhancer Crysta (Purple)"),
        ),
        "special gear xtal": (
            "Special Crysta + Purple Enhancer",
            ("Special Crysta", "Enhancer Crysta (Purple)"),
        ),
        "arm xtal": (
            "Armor Crysta + Green Enhancer",
            ("Armor Crysta", "Enhancer Crysta (Green)"),
        ),
        "armor xtal": (
            "Armor Crysta + Green Enhancer",
            ("Armor Crysta", "Enhancer Crysta (Green)"),
        ),
        "add xtal": (
            "Additional Crysta + Yellow Enhancer",
            ("Additional Crysta", "Enhancer Crysta (Yellow)"),
        ),
        "ad xtal": (
            "Additional Crysta + Yellow Enhancer",
            ("Additional Crysta", "Enhancer Crysta (Yellow)"),
        ),
        "hat xtal": (
            "Additional Crysta + Yellow Enhancer",
            ("Additional Crysta", "Enhancer Crysta (Yellow)"),
        ),
        "additional xtal": (
            "Additional Crysta + Yellow Enhancer",
            ("Additional Crysta", "Enhancer Crysta (Yellow)"),
        ),
        "red xtal": ("Enhancer Crysta (Red)", ("Enhancer Crysta (Red)",)),
        "purple xtal": (
            "Enhancer Crysta (Purple)",
            ("Enhancer Crysta (Purple)",),
        ),
        "green xtal": (
            "Enhancer Crysta (Green)",
            ("Enhancer Crysta (Green)",),
        ),
        "yellow xtal": (
            "Enhancer Crysta (Yellow)",
            ("Enhancer Crysta (Yellow)",),
        ),
        "xtal": ("All Crysta", ALL_CRYSTA_TYPES),
        "wp": ("Main Weapons", MAIN_WEAPON_TYPES),
        "weapon": ("Main Weapons", MAIN_WEAPON_TYPES),
    }
    candidates = [
        (_normalize_filter_text(phrase), label, tuple(types))
        for phrase, (label, types) in combinations.items()
    ]
    for alias, item_type in ITEM_TYPE_ALIASES.items():
        candidates.append((_normalize_filter_text(alias), item_type, (item_type,)))
    candidates.sort(key=lambda row: (len(row[0].split()), len(row[0])), reverse=True)
    return candidates


def _extract_trailing_filter(
    text: str,
    available_item_types: set[str],
) -> tuple[ItemTypeFilter | None, str]:
    raw_tokens = text.strip().split()
    for phrase, label, configured_types in _filter_candidates():
        phrase_tokens = phrase.split()
        if len(phrase_tokens) > len(raw_tokens):
            continue
        raw_suffix = " ".join(raw_tokens[-len(phrase_tokens) :])
        if _normalize_filter_text(raw_suffix) != phrase:
            continue
        item_types = _existing_types(configured_types, available_item_types)
        if not item_types:
            continue
        remaining = " ".join(raw_tokens[: -len(phrase_tokens)]).strip()
        return ItemTypeFilter(label, item_types, phrase), remaining
    return None, text.strip()


def _first_clause_stat_text(text: str) -> str:
    boundaries = []
    comparison = _COMPARISON_RE.search(text)
    boolean = _BOOLEAN_SPLIT_RE.search(text)
    if comparison is not None:
        boundaries.append(comparison.start())
    if boolean is not None:
        boundaries.append(boolean.start())
    end = min(boundaries) if boundaries else len(text)
    return text[:end].strip()


def _extract_leading_filter(
    text: str,
    available_item_types: set[str],
    available_stats: list[str],
) -> tuple[ItemTypeFilter | None, str]:
    raw_tokens = text.strip().split()
    first_stat = normalize_stat_text(_first_clause_stat_text(text))
    known_stats = {normalize_stat_text(stat) for stat in available_stats}

    # A filter word can also begin a real stat name, e.g. "Weapon ATK" or
    # "Additional Magic %". Exact stat text always wins over a leading filter.
    if first_stat in known_stats:
        return None, text.strip()

    for phrase, label, configured_types in _filter_candidates():
        phrase_tokens = phrase.split()
        if len(phrase_tokens) >= len(raw_tokens):
            continue
        raw_prefix = " ".join(raw_tokens[: len(phrase_tokens)])
        if _normalize_filter_text(raw_prefix) != phrase:
            continue
        item_types = _existing_types(configured_types, available_item_types)
        if not item_types:
            continue
        remaining = " ".join(raw_tokens[len(phrase_tokens) :]).strip()
        if not remaining:
            continue
        return ItemTypeFilter(label, item_types, phrase), remaining
    return None, text.strip()


def _extract_edge_filter(
    text: str,
    available_item_types: set[str],
    available_stats: list[str],
) -> tuple[ItemTypeFilter | None, str]:
    trailing, remaining = _extract_trailing_filter(text, available_item_types)
    if trailing is not None:
        return trailing, remaining
    return _extract_leading_filter(text, available_item_types, available_stats)


def _parse_clause(text: str) -> ParsedClause:
    clause_text = text.strip()
    operator_match = _COMPARISON_RE.search(clause_text)
    if operator_match is None:
        if not clause_text:
            raise StatQuerySyntaxError("Invalid stat query: expected a stat.")
        return ParsedClause(clause_text, ">=", Decimal("1"), False)

    operator = operator_match.group(1)
    typed_stat = clause_text[: operator_match.start()].strip()
    value_text = clause_text[operator_match.end() :].strip()
    if not typed_stat:
        raise StatQuerySyntaxError(
            f'Invalid stat query: expected a stat before "{operator}".'
        )
    if not _NUMBER_RE.fullmatch(value_text):
        raise StatQuerySyntaxError(
            f'Invalid stat query: expected a number after "{typed_stat} {operator}".'
        )
    return ParsedClause(typed_stat, operator, Decimal(value_text), True)


def parse_stat_expression(
    text: str,
    available_item_types: set[str],
    available_stats: list[str] | None = None,
) -> ParsedStatExpression:
    raw_query = text.strip()
    if "(" in raw_query or ")" in raw_query:
        raise StatQuerySyntaxError("Parentheses are not supported.")

    had_stat_prefix = bool(_STAT_PREFIX_RE.match(raw_query))
    expression_text = _STAT_PREFIX_RE.sub("", raw_query, count=1).strip()
    if not expression_text:
        raise StatQuerySyntaxError("Invalid stat query: expected a stat.")

    unsupported = _UNSUPPORTED_BOOLEAN_RE.search(expression_text)
    if unsupported is not None:
        word = unsupported.group(1).casefold()
        raise StatQuerySyntaxError(f'Unsupported Boolean operator: "{word}".')

    if available_stats is None:
        item_filter, expression_text = _extract_trailing_filter(
            expression_text,
            available_item_types,
        )
    else:
        item_filter, expression_text = _extract_edge_filter(
            expression_text,
            available_item_types,
            available_stats,
        )
    if not expression_text:
        raise StatQuerySyntaxError("Invalid stat query: expected a stat before the item filter.")

    pieces = _BOOLEAN_SPLIT_RE.split(expression_text)
    if not pieces[0].strip():
        first_operator = pieces[1].casefold() if len(pieces) > 1 else ""
        raise StatQuerySyntaxError(
            f'Invalid stat query: expression cannot begin with "{first_operator}".'
        )

    groups: list[list[ParsedClause]] = [[]]
    pending_operator: str | None = None
    for index, piece in enumerate(pieces):
        if index % 2 == 1:
            pending_operator = piece.casefold()
            continue

        clause_text = piece.strip()
        if not clause_text:
            operator = pending_operator or "and"
            raise StatQuerySyntaxError(
                f'Invalid stat query: expected a stat after "{operator}".'
            )
        clause = _parse_clause(clause_text)
        if index == 0:
            groups[0].append(clause)
        elif pending_operator == "and":
            groups[-1].append(clause)
        elif pending_operator == "or":
            groups.append([clause])
        else:
            raise StatQuerySyntaxError("Invalid stat query: expected a Boolean operator.")
        pending_operator = None

    return ParsedStatExpression(
        groups=tuple(ParsedAndGroup(tuple(group)) for group in groups),
        item_filter=item_filter,
        had_stat_prefix=had_stat_prefix,
        raw_expression=raw_query,
    )


def looks_like_stat_expression(
    text: str,
    available_stats: list[str],
    available_item_types: set[str],
    ambiguous_terms: set[str],
) -> bool:
    raw = text.strip()
    if not raw:
        return False
    if _STAT_PREFIX_RE.match(raw):
        return True
    if _COMPARISON_RE.search(raw) or _BOOLEAN_SPLIT_RE.search(raw):
        return True

    _item_filter, remaining = _extract_edge_filter(
        raw,
        available_item_types,
        available_stats,
    )
    normalized = normalize_stat_text(remaining)
    if not normalized:
        return False
    if normalized in {normalize_stat_text(term) for term in ambiguous_terms}:
        return True
    if normalized in {normalize_stat_text(stat) for stat in available_stats}:
        return True
    normalized_aliases = {normalize_stat_text(alias) for alias in STAT_ALIASES}
    return normalized in normalized_aliases
