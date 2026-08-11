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
from toram_search.item_query_entities import (
    ItemFilterMatch,
    QueryToken,
    find_exact_item_filter_matches,
    remaining_tokens,
    tokenize_item_query,
)
from toram_search.reconstruction import _FILLERS


UnderstandingDecision = Literal["execute", "clarify", "confirm", "suggest", "fallback"]
PartKind = Literal["stat", "item_filter"]
UncertaintyMode = Literal["choose", "confirm"]
ReasonCode = Literal[
    "EXACT_MATCH",
    "ALIAS_MATCH",
    "AMBIGUOUS_STAT",
    "FUZZY_STAT",
    "FUZZY_FILTER",
    "UNKNOWN_TOKEN",
    "MULTIPLE_CORRECTIONS",
    "UNSAFE_SHAPE",
]

_MAX_UNDERSTANDING_TOKENS = 24
_COMPLEX_RE = re.compile(r"(>=|<=|==|>|<|=)|\b(?:and|or)\b", re.IGNORECASE)


@dataclass(frozen=True)
class ResolvedItemPart:
    part_kind: PartKind
    typed_text: str
    value: str
    display_label: str
    canonical_text: str
    reason: ReasonCode


@dataclass(frozen=True)
class ItemQueryChoice:
    value: str
    display_label: str
    canonical_text: str


@dataclass(frozen=True)
class ItemQueryUncertainty:
    issue_id: str
    part_kind: PartKind
    typed_text: str
    mode: UncertaintyMode
    reason: ReasonCode
    choices: tuple[ItemQueryChoice, ...]


@dataclass(frozen=True)
class ConfirmedItemChoice:
    issue_id: str
    value: str


@dataclass(frozen=True)
class ItemQueryUnderstanding:
    decision: UnderstandingDecision
    resolved_parts: tuple[ResolvedItemPart, ...] = ()
    uncertainties: tuple[ItemQueryUncertainty, ...] = ()
    unresolved_tokens: tuple[str, ...] = ()
    canonical_query: str | None = None
    suggested_query: str | None = None
    reasons: tuple[ReasonCode, ...] = ()


@dataclass(frozen=True)
class _StatSpan:
    tokens: tuple[QueryToken, ...]
    resolution: StatTermResolution


@dataclass(frozen=True)
class _CoreOption:
    filter_match: ItemFilterMatch
    stat_span: _StatSpan
    unresolved: tuple[QueryToken, ...]


def _unique_reasons(*values: ReasonCode) -> tuple[ReasonCode, ...]:
    return tuple(dict.fromkeys(values))


def _canonical_stat_text(
    typed_text: str,
    resolution: StatTermResolution,
    *,
    selected_value: str | None = None,
) -> str:
    normalized = normalize_stat_text(typed_text)
    if selected_value is None and resolution.status in {"alias", "ambiguous"}:
        return normalized
    value = selected_value or resolution.candidates[0]
    return preferred_stat_alias(value) or normalize_stat_text(value)


def _stat_part(span: _StatSpan) -> ResolvedItemPart:
    resolution = span.resolution
    value = resolution.candidates[0]
    typed_text = " ".join(token.text for token in span.tokens)
    return ResolvedItemPart(
        part_kind="stat",
        typed_text=typed_text,
        value=value,
        display_label=value,
        canonical_text=_canonical_stat_text(typed_text, resolution),
        reason="ALIAS_MATCH" if resolution.status == "alias" else "EXACT_MATCH",
    )


def _filter_part(match: ItemFilterMatch, tokens: tuple[QueryToken, ...]) -> ResolvedItemPart:
    by_index = {token.index: token for token in tokens}
    used = tuple(by_index[index] for index in match.token_indexes)
    reason: ReasonCode = (
        "ALIAS_MATCH"
        if any(token.text != token.normalized for token in used)
        else "EXACT_MATCH"
    )
    return ResolvedItemPart(
        part_kind="item_filter",
        typed_text=match.typed_text,
        value=match.phrase.label,
        display_label=match.phrase.label,
        canonical_text=match.canonical_phrase,
        reason=reason,
    )


def _stat_uncertainty(span: _StatSpan) -> ItemQueryUncertainty:
    resolution = span.resolution
    typed_text = " ".join(token.text for token in span.tokens)
    first_index = span.tokens[0].index
    choices = tuple(
        ItemQueryChoice(
            value=candidate,
            display_label=(
                resolution.display_labels[index]
                if index < len(resolution.display_labels)
                else candidate
            ),
            canonical_text=preferred_stat_alias(candidate) or normalize_stat_text(candidate),
        )
        for index, candidate in enumerate(resolution.candidates)
    )
    return ItemQueryUncertainty(
        issue_id=f"stat:{first_index}:{normalize_stat_text(typed_text)}",
        part_kind="stat",
        typed_text=typed_text,
        mode="choose",
        reason="AMBIGUOUS_STAT",
        choices=choices,
    )


def _valid_stat_resolution(resolution: StatTermResolution) -> bool:
    return resolution.status in {"exact", "alias", "ambiguous"}


def _find_stat_spans(
    tokens: tuple[QueryToken, ...],
    available_stats: tuple[str, ...],
) -> tuple[_StatSpan, ...]:
    candidates: list[_StatSpan] = []
    for length in range(len(tokens), 0, -1):
        for start in range(0, len(tokens) - length + 1):
            span_tokens = tokens[start : start + length]
            typed = " ".join(token.normalized for token in span_tokens)
            resolution = resolve_stat_term(
                typed,
                available_stats,
                allow_fuzzy=False,
            )
            if _valid_stat_resolution(resolution):
                candidates.append(_StatSpan(span_tokens, resolution))

    selected: list[_StatSpan] = []
    consumed: set[int] = set()
    for candidate in candidates:
        indexes = {token.index for token in candidate.tokens}
        if indexes & consumed:
            continue
        selected.append(candidate)
        consumed.update(indexes)
    return tuple(selected)


def _analyze_filter_match(
    tokens: tuple[QueryToken, ...],
    match: ItemFilterMatch,
    available_stats: tuple[str, ...],
) -> tuple[_CoreOption | None, bool]:
    remaining = remaining_tokens(tokens, match.token_indexes)
    meaningful = tuple(
        token for token in remaining if token.normalized not in _FILLERS
    )
    if not meaningful:
        return None, False

    whole_text = " ".join(token.normalized for token in meaningful)
    whole_resolution = resolve_stat_term(
        whole_text,
        available_stats,
        allow_fuzzy=False,
    )
    if _valid_stat_resolution(whole_resolution):
        return _CoreOption(match, _StatSpan(meaningful, whole_resolution), ()), False

    spans = _find_stat_spans(meaningful, available_stats)
    if len(spans) > 1:
        return None, True
    if not spans:
        return None, False

    span = spans[0]
    consumed = {token.index for token in span.tokens}
    unresolved = tuple(token for token in meaningful if token.index not in consumed)
    return _CoreOption(match, span, unresolved), False


def _core_semantic_key(option: _CoreOption) -> tuple[object, ...]:
    resolution = option.stat_span.resolution
    return (
        option.filter_match.phrase.label,
        option.filter_match.phrase.item_types,
        resolution.status == "ambiguous",
        resolution.candidates,
        tuple(token.normalized for token in option.unresolved),
    )


def understand_item_query(
    raw_query: str,
    *,
    available_stats: Iterable[str],
    available_item_types: set[str],
    confirmed_choices: tuple[ConfirmedItemChoice, ...] = (),
) -> ItemQueryUnderstanding:
    del confirmed_choices  # Applied in the continuation task; accepted now for API stability.

    tokens = tokenize_item_query(raw_query)
    if len(tokens) > _MAX_UNDERSTANDING_TOKENS:
        return ItemQueryUnderstanding(
            "fallback",
            reasons=("UNSAFE_SHAPE",),
        )
    if _COMPLEX_RE.search(raw_query):
        return ItemQueryUnderstanding(
            "fallback",
            reasons=("UNSAFE_SHAPE",),
        )

    filter_matches = find_exact_item_filter_matches(tokens, available_item_types)
    if filter_matches.status != "unique":
        return ItemQueryUnderstanding("fallback")

    stat_catalog = tuple(available_stats)
    options: list[_CoreOption] = []
    found_multiple_stats = False
    for match in filter_matches.matches:
        option, multiple_stats = _analyze_filter_match(tokens, match, stat_catalog)
        found_multiple_stats = found_multiple_stats or multiple_stats
        if option is not None:
            options.append(option)

    if found_multiple_stats:
        return ItemQueryUnderstanding("fallback")
    if not options:
        return ItemQueryUnderstanding("fallback")

    semantic_keys = {_core_semantic_key(option) for option in options}
    if len(semantic_keys) != 1:
        return ItemQueryUnderstanding("fallback")

    option = min(
        options,
        key=lambda row: (
            len(row.unresolved),
            row.stat_span.tokens[0].index,
            row.filter_match.token_indexes,
        ),
    )
    resolution = option.stat_span.resolution
    filter_part = _filter_part(option.filter_match, tokens)
    unresolved = tuple(token.text for token in option.unresolved)

    if resolution.status == "ambiguous":
        uncertainty = _stat_uncertainty(option.stat_span)
        reasons: list[ReasonCode] = [filter_part.reason, "AMBIGUOUS_STAT"]
        if unresolved:
            reasons.append("UNKNOWN_TOKEN")
        return ItemQueryUnderstanding(
            "clarify",
            resolved_parts=(filter_part,),
            uncertainties=(uncertainty,),
            unresolved_tokens=unresolved,
            canonical_query=(
                f"{_canonical_stat_text(' '.join(token.text for token in option.stat_span.tokens), resolution)} "
                f"{filter_part.canonical_text}"
            ),
            reasons=_unique_reasons(*reasons),
        )

    stat_part = _stat_part(option.stat_span)
    canonical_query = f"{stat_part.canonical_text} {filter_part.canonical_text}"
    reasons = [stat_part.reason, filter_part.reason]
    if unresolved:
        reasons.append("UNKNOWN_TOKEN")
        return ItemQueryUnderstanding(
            "suggest",
            resolved_parts=(stat_part, filter_part),
            unresolved_tokens=unresolved,
            canonical_query=canonical_query,
            suggested_query=canonical_query,
            reasons=_unique_reasons(*reasons),
        )

    return ItemQueryUnderstanding(
        "execute",
        resolved_parts=(stat_part, filter_part),
        canonical_query=canonical_query,
        reasons=_unique_reasons(*reasons),
    )
