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
    find_unique_fuzzy_item_filter_match,
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
_FUZZY_THRESHOLD = 85.0
_COMPLEX_RE = re.compile(r"(>=|<=|==|>|<|=)|\b(?:and|or)\b", re.IGNORECASE)
_UNCERTAINTY_ORDER = {
    "AMBIGUOUS_STAT": 0,
    "FUZZY_STAT": 1,
    "FUZZY_FILTER": 2,
}


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


def _confirmed_stat_part(
    span: _StatSpan,
    choice: ItemQueryChoice,
    reason: ReasonCode,
) -> ResolvedItemPart:
    return ResolvedItemPart(
        part_kind="stat",
        typed_text=" ".join(token.text for token in span.tokens),
        value=choice.value,
        display_label=choice.value,
        canonical_text=choice.canonical_text,
        reason=reason,
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


def _confirmed_filter_part(
    match: ItemFilterMatch,
    choice: ItemQueryChoice,
) -> ResolvedItemPart:
    return ResolvedItemPart(
        part_kind="item_filter",
        typed_text=match.typed_text,
        value=choice.value,
        display_label=choice.display_label,
        canonical_text=choice.canonical_text,
        reason="FUZZY_FILTER",
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
    fuzzy = resolution.status == "fuzzy"
    return ItemQueryUncertainty(
        issue_id=f"stat:{first_index}:{normalize_stat_text(typed_text)}",
        part_kind="stat",
        typed_text=typed_text,
        mode="confirm" if fuzzy else "choose",
        reason="FUZZY_STAT" if fuzzy else "AMBIGUOUS_STAT",
        choices=choices,
    )


def _filter_uncertainty(match: ItemFilterMatch) -> ItemQueryUncertainty:
    first_index = min(match.token_indexes)
    return ItemQueryUncertainty(
        issue_id=f"filter:{first_index}:{normalize_stat_text(match.typed_text)}",
        part_kind="item_filter",
        typed_text=match.typed_text,
        mode="confirm",
        reason="FUZZY_FILTER",
        choices=(
            ItemQueryChoice(
                value=match.phrase.label,
                display_label=match.phrase.label,
                canonical_text=match.canonical_phrase,
            ),
        ),
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


def _find_unique_fuzzy_stat_span(
    tokens: tuple[QueryToken, ...],
    available_stats: tuple[str, ...],
) -> _StatSpan | None:
    for length in range(len(tokens), 0, -1):
        level: list[_StatSpan] = []
        for start in range(0, len(tokens) - length + 1):
            span_tokens = tokens[start : start + length]
            typed = " ".join(token.normalized for token in span_tokens)
            resolution = resolve_stat_term(
                typed,
                available_stats,
                allow_fuzzy=True,
                fuzzy_threshold=_FUZZY_THRESHOLD,
                limit=2,
            )
            if resolution.status == "fuzzy" and len(resolution.candidates) == 1:
                level.append(_StatSpan(span_tokens, resolution))
        if not level:
            continue
        semantic = {row.resolution.candidates[0] for row in level}
        if len(semantic) != 1:
            return None
        return min(level, key=lambda row: row.tokens[0].index)
    return None


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
    if spans:
        span = spans[0]
        consumed = {token.index for token in span.tokens}
        unresolved = tuple(token for token in meaningful if token.index not in consumed)
        return _CoreOption(match, span, unresolved), False

    fuzzy_span = _find_unique_fuzzy_stat_span(meaningful, available_stats)
    if fuzzy_span is None:
        return None, False
    consumed = {token.index for token in fuzzy_span.tokens}
    unresolved = tuple(token for token in meaningful if token.index not in consumed)
    return _CoreOption(match, fuzzy_span, unresolved), False


def _core_semantic_key(option: _CoreOption) -> tuple[object, ...]:
    resolution = option.stat_span.resolution
    return (
        option.filter_match.phrase.label,
        option.filter_match.phrase.item_types,
        resolution.status,
        resolution.candidates,
        tuple(token.normalized for token in option.unresolved),
    )


def _ordered_uncertainties(
    values: list[ItemQueryUncertainty],
) -> tuple[ItemQueryUncertainty, ...]:
    return tuple(
        sorted(
            values,
            key=lambda value: (
                _UNCERTAINTY_ORDER.get(value.reason, 99),
                value.issue_id,
            ),
        )
    )


def _confirmed_choice_map(
    confirmed_choices: tuple[ConfirmedItemChoice, ...],
) -> dict[str, str] | None:
    output: dict[str, str] = {}
    for choice in confirmed_choices:
        previous = output.get(choice.issue_id)
        if previous is not None and previous != choice.value:
            return None
        output[choice.issue_id] = choice.value
    return output


def _selected_choice(
    issue: ItemQueryUncertainty,
    confirmed: dict[str, str],
) -> ItemQueryChoice | None | bool:
    selected_value = confirmed.get(issue.issue_id)
    if selected_value is None:
        return None
    return next(
        (choice for choice in issue.choices if choice.value == selected_value),
        False,
    )


def understand_item_query(
    raw_query: str,
    *,
    available_stats: Iterable[str],
    available_item_types: set[str],
    confirmed_choices: tuple[ConfirmedItemChoice, ...] = (),
) -> ItemQueryUnderstanding:
    confirmed = _confirmed_choice_map(confirmed_choices)
    if confirmed is None:
        return ItemQueryUnderstanding("fallback")

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

    exact_filters = find_exact_item_filter_matches(tokens, available_item_types)
    fuzzy_filter = find_unique_fuzzy_item_filter_match(tokens, available_item_types)
    if fuzzy_filter is not None:
        filter_matches = (fuzzy_filter,)
    elif exact_filters.status == "unique":
        filter_matches = exact_filters.matches
    else:
        return ItemQueryUnderstanding("fallback")

    stat_catalog = tuple(available_stats)
    options: list[_CoreOption] = []
    found_multiple_stats = False
    for match in filter_matches:
        option, multiple_stats = _analyze_filter_match(tokens, match, stat_catalog)
        found_multiple_stats = found_multiple_stats or multiple_stats
        if option is not None:
            options.append(option)

    if found_multiple_stats or not options:
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
    unresolved = tuple(token.text for token in option.unresolved)
    uncertainties: list[ItemQueryUncertainty] = []
    resolved_parts: list[ResolvedItemPart] = []
    reasons: list[ReasonCode] = []
    seen_issue_ids: set[str] = set()

    typed_stat = " ".join(token.text for token in option.stat_span.tokens)
    if resolution.status in {"ambiguous", "fuzzy"}:
        issue = _stat_uncertainty(option.stat_span)
        seen_issue_ids.add(issue.issue_id)
        selected = _selected_choice(issue, confirmed)
        if selected is False:
            return ItemQueryUnderstanding("fallback")
        if selected is None:
            uncertainties.append(issue)
            reasons.append(issue.reason)
            stat_canonical = _canonical_stat_text(typed_stat, resolution)
        else:
            resolved_parts.append(_confirmed_stat_part(option.stat_span, selected, issue.reason))
            reasons.append(issue.reason)
            stat_canonical = selected.canonical_text
    else:
        stat_part = _stat_part(option.stat_span)
        resolved_parts.append(stat_part)
        reasons.append(stat_part.reason)
        stat_canonical = stat_part.canonical_text

    if option.filter_match.match_kind == "fuzzy":
        issue = _filter_uncertainty(option.filter_match)
        seen_issue_ids.add(issue.issue_id)
        selected = _selected_choice(issue, confirmed)
        if selected is False:
            return ItemQueryUnderstanding("fallback")
        if selected is None:
            uncertainties.append(issue)
            reasons.append("FUZZY_FILTER")
            filter_canonical = option.filter_match.canonical_phrase
        else:
            resolved_parts.append(_confirmed_filter_part(option.filter_match, selected))
            reasons.append("FUZZY_FILTER")
            filter_canonical = selected.canonical_text
    else:
        filter_part = _filter_part(option.filter_match, tokens)
        resolved_parts.append(filter_part)
        reasons.append(filter_part.reason)
        filter_canonical = filter_part.canonical_text

    if set(confirmed) - seen_issue_ids:
        return ItemQueryUnderstanding("fallback")

    canonical_query = f"{stat_canonical} {filter_canonical}"
    if unresolved:
        reasons.append("UNKNOWN_TOKEN")

    ordered = _ordered_uncertainties(uncertainties)
    if ordered:
        decision: UnderstandingDecision = (
            "clarify"
            if any(issue.reason == "AMBIGUOUS_STAT" for issue in ordered)
            else "confirm"
        )
        return ItemQueryUnderstanding(
            decision,
            resolved_parts=tuple(resolved_parts),
            uncertainties=ordered,
            unresolved_tokens=unresolved,
            canonical_query=canonical_query,
            reasons=_unique_reasons(*reasons),
        )

    if unresolved:
        return ItemQueryUnderstanding(
            "suggest",
            resolved_parts=tuple(resolved_parts),
            unresolved_tokens=unresolved,
            canonical_query=canonical_query,
            suggested_query=canonical_query,
            reasons=_unique_reasons(*reasons),
        )

    if len(confirmed) >= 2:
        reasons.append("MULTIPLE_CORRECTIONS")
        return ItemQueryUnderstanding(
            "confirm",
            resolved_parts=tuple(resolved_parts),
            canonical_query=canonical_query,
            reasons=_unique_reasons(*reasons),
        )

    return ItemQueryUnderstanding(
        "execute",
        resolved_parts=tuple(resolved_parts),
        canonical_query=canonical_query,
        reasons=_unique_reasons(*reasons),
    )
