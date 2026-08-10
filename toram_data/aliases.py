from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Callable, Iterable, Literal, Mapping

from rapidfuzz import fuzz

MAIN_WEAPON_TYPES = (
    "1 Handed Sword",
    "2 Handed Sword",
    "Bow",
    "Bowgun",
    "Katana",
    "Staff",
    "Magic Device",
    "Knuckles",
    "Halberd",
)

ALL_CRYSTA_TYPES = (
    "Normal Crysta",
    "Weapon Crysta",
    "Armor Crysta",
    "Additional Crysta",
    "Special Crysta",
    "Enhancer Crysta (Red)",
    "Enhancer Crysta (Purple)",
    "Enhancer Crysta (Green)",
    "Enhancer Crysta (Yellow)",
    "Enhancer Crysta (Blue)",
)

ITEM_WORD_ALIASES = {
    "crystal": "xtal",
    "crystall": "xtal",
    "crysta": "xtal",
    "xtall": "xtal",
}

ITEM_TYPE_ALIASES = {
    "one hand": "1 Handed Sword",
    "1h": "1 Handed Sword",
    "1 h": "1 Handed Sword",
    "ohs": "1 Handed Sword",
    "two hand": "2 Handed Sword",
    "2h": "2 Handed Sword",
    "2 h": "2 Handed Sword",
    "ths": "2 Handed Sword",
    "bow gun": "Bowgun",
    "bg": "Bowgun",
    "bwg": "Bowgun",
    "bowgun": "Bowgun",
    "bow": "Bow",
    "bw": "Bow",
    "katana": "Katana",
    "ktn": "Katana",
    "mononofu": "Katana",
    "staff": "Staff",
    "stf": "Staff",
    "magic device": "Magic Device",
    "md": "Magic Device",
    "knuckles": "Knuckles",
    "knuckle": "Knuckles",
    "knuck": "Knuckles",
    "knk": "Knuckles",
    "halberd": "Halberd",
    "hb": "Halberd",
    "additional": "Additional",
    "add": "Additional",
    "hat": "Additional",
    "ad": "Additional",
    "special gear": "Special",
    "special": "Special",
    "ring": "Special",
    "rings": "Special",
    "armor": "Armor",
    "arm": "Armor",
    "arrow": "Arrow",
    "dagger": "Dagger",
    "shield": "Shield",
    "usable": "Usable",
    "useable": "Usable",
    "consumable": "Usable",
    "consumables": "Usable",
    "consumable item": "Usable",
    "consumable items": "Usable",
    "consumalbe": "Usable",
}

STAT_ALIASES = {
    "dte": "% stronger against earth",
    "dt earth": "% stronger against earth",
    "dtearth": "% stronger against earth",
    "dtf": "% stronger against fire",
    "dt fire": "% stronger against fire",
    "dtfire": "% stronger against fire",
    "dtw": "% stronger against wind",
    "dt wind": "% stronger against wind",
    "dtwind": "% stronger against wind",
    "dtwa": "% stronger against water",
    "dt water": "% stronger against water",
    "dtwater": "% stronger against water",
    "dtn": "% stronger against neutral",
    "dt neutral": "% stronger against neutral",
    "dtneutral": "% stronger against neutral",
    "dtd": "% stronger against dark",
    "dt dark": "% stronger against dark",
    "dtdark": "% stronger against dark",
    "dtl": "% stronger against light",
    "dt light": "% stronger against light",
    "dtlight": "% stronger against light",
    "cast speed": "cspd",
    "attack speed": "aspd",
    "attack spped": "aspd",
    "acc": "accuracy",
    "defense": "def",
    "mag": "magic",
    "phys": "physical",
    "dmg": "damage",
    "anti": "anticipate %",
    "ampr": "attack mp recovery",
    "hp": "maxhp",
    "max hp": "maxhp",
    "lrd": "long range damage %",
    "srd": "short range damage %",
    "ref": "refine",
    "natural hp": "natural hp regen",
    "natural mp": "natural mp regen",
    "motion": "motion speed %",
    "motion %": "motion speed %",
    "motion%": "motion speed %",
    "cr": "critical rate",
    "cd": "critical damage",
    "stab": "stability",
    "rev": "revive time %",
    "xp": "exp gain %",
    "ele": "element",
    "pp": "physical pierce %",
    "mp": "magic pierce %",
    "pot": "potential",
    "p rest": "physical resistance %",
    "pres": "physical resistance %",
    "p resist": "physical resistance %",
    "prest": "physical resistance %",
    "presist": "physical resistance %",
    "phys rest": "physical resistance %",
    "phys resist": "physical resistance %",
    "physical rest": "physical resistance %",
    "physical resist": "physical resistance %",
    "physical resistance": "physical resistance %",
    "mres": "magic resistance %",
    "m rest": "magic resistance %",
    "m resist": "magic resistance %",
    "mrest": "magic resistance %",
    "mresist": "magic resistance %",
    "mag rest": "magic resistance %",
    "mag resist": "magic resistance %",
    "magic rest": "magic resistance %",
    "magic resist": "magic resistance %",
    "magical rest": "magic resistance %",
    "magical resist": "magic resistance %",
    "magic resistance": "magic resistance %",
    "fire resist": "fire resistance %",
    "fire rest": "fire resistance %",
    "water resist": "water resistance %",
    "water rest": "water resistance %",
    "wind resist": "wind resistance %",
    "wind rest": "wind resistance %",
    "earth resist": "earth resistance %",
    "earth rest": "earth resistance %",
    "light resist": "light resistance %",
    "light rest": "light resistance %",
    "dark resist": "dark resistance %",
    "dark rest": "dark resistance %",
    "neutral resist": "neutral resistance %",
    "neutral rest": "neutral resistance %",
    "invi": "invincible",
    "gem dust": "gem dust drop amount %",
    "gem dust drop amount %": "gem dust drop amount %",
    "drop rate %": "drop rate %",
    "drop rate": "drop rate %",
}

STAT_CATEGORY_ALIASES = {
    "dt": "damage_to_element",
    "bar": "barrier",
    "rest": "resistance",
    "resist": "resistance",
}

STAT_AMBIGUOUS_GROUPS = {
    "crit": ("Critical Rate", "Critical Damage"),
    "crt": ("Critical Rate", "Critical Damage"),
}

STAT_PREFERRED_DISPLAY_ALIASES = {
    "critical rate": "cr",
    "critical damage": "cd",
    "attack mp recovery": "ampr",
    "physical pierce %": "pp",
    "magic pierce %": "mp",
}

STAT_FORCED_ALIASES = {"hp", "mp"}


def normalize_name(value: str) -> str:
    """Normalize an item name for exact and fuzzy matching."""
    value = unicodedata.normalize("NFKC", value).casefold()
    value = re.sub(r"[^\w]+", " ", value, flags=re.UNICODE)
    return " ".join(value.split())


def is_crysta_item_type(item_type: str) -> bool:
    """Return True when an item type is any crysta category."""
    return "crysta" in normalize_name(item_type).split()


def normalize_stat_text(value: str) -> str:
    """Normalize stat/query text while preserving percent as a token."""
    value = unicodedata.normalize("NFKC", value).casefold()
    value = re.sub(r"[^\w%]+", " ", value, flags=re.UNICODE)
    value = re.sub(r"\s*%\s*", " % ", value)
    return " ".join(value.split())


def expand_stat_aliases(value: str) -> str:
    """Apply supplied stat aliases as longest phrase substitutions."""
    tokens = normalize_stat_text(value).split()
    aliases = sorted(
        (
            (normalize_stat_text(alias).split(), normalize_stat_text(target).split())
            for alias, target in STAT_ALIASES.items()
        ),
        key=lambda pair: (len(pair[0]), len(" ".join(pair[0]))),
        reverse=True,
    )
    output: list[str] = []
    index = 0
    while index < len(tokens):
        matched = False
        for alias_tokens, target_tokens in aliases:
            end = index + len(alias_tokens)
            if alias_tokens and tokens[index:end] == alias_tokens:
                output.extend(target_tokens)
                index = end
                matched = True
                break
        if not matched:
            output.append(tokens[index])
            index += 1
    return " ".join(output)



ResolutionStatus = Literal["exact", "alias", "ambiguous", "fuzzy", "new", "empty"]


@dataclass(frozen=True)
class EditorResolution:
    typed_value: str
    status: ResolutionStatus
    candidates: tuple[str, ...]
    scores: tuple[float, ...] = ()


def resolve_editor_value(
    text: str,
    existing_values: Iterable[str],
    *,
    aliases: Mapping[str, str],
    ambiguous_groups: Mapping[str, tuple[str, ...]] | None = None,
    fuzzy_threshold: float = 70.0,
    limit: int = 5,
    normalizer: Callable[[str], str] = normalize_name,
) -> EditorResolution:
    typed = text.strip()
    if not typed:
        return EditorResolution(typed, "empty", ())

    values = tuple(dict.fromkeys(value for value in existing_values if value and value.strip()))
    spelling_exact = next((value for value in values if typed == value), None)
    if spelling_exact is not None:
        return EditorResolution(typed, "exact", (spelling_exact,), (100.0,))

    by_normalized = {normalizer(value): value for value in values}
    normalized = normalizer(typed)
    normalized_aliases = {normalizer(alias): target for alias, target in aliases.items()}

    if normalized in STAT_FORCED_ALIASES:
        forced_target = normalized_aliases.get(normalized)
        if forced_target is not None:
            actual = by_normalized.get(normalizer(forced_target))
            if actual is not None:
                return EditorResolution(typed, "alias", (actual,), (100.0,))

    exact = by_normalized.get(normalized)
    if exact is not None:
        return EditorResolution(typed, "exact", (exact,), (100.0,))

    groups = {normalizer(key): values for key, values in (ambiguous_groups or {}).items()}
    group = groups.get(normalized)
    if group:
        candidates = tuple(
            by_normalized[normalizer(value)]
            for value in group
            if normalizer(value) in by_normalized
        )
        if candidates:
            return EditorResolution(typed, "ambiguous", candidates)

    alias_target = normalized_aliases.get(normalized)
    if alias_target is not None:
        actual = by_normalized.get(normalizer(alias_target))
        if actual is not None:
            return EditorResolution(typed, "alias", (actual,), (100.0,))

    aliases_for_value: dict[str, list[str]] = {}
    for alias, target in aliases.items():
        aliases_for_value.setdefault(normalizer(target), []).append(normalizer(alias))

    ranked: list[tuple[float, float, int, str]] = []
    for value in values:
        candidate = normalizer(value)
        representations = (candidate, *aliases_for_value.get(candidate, ()))
        eligible: list[tuple[float, float]] = []
        for representation in representations:
            weighted = float(fuzz.WRatio(normalized, representation))
            token_score = float(fuzz.token_sort_ratio(normalized, representation))
            # WRatio alone over-ranks values sharing one generic token (for example
            # "Magic Burst Damage" versus "Magic Pierce"). Requiring the token
            # score to clear the threshold keeps typo correction conservative.
            if token_score >= fuzzy_threshold:
                eligible.append((weighted, token_score))
        if eligible:
            weighted, token_score = max(
                eligible,
                key=lambda pair: (max(pair), min(pair)),
            )
            ranked.append((max(weighted, token_score), token_score, len(value), value))
    ranked.sort(key=lambda row: (-row[0], -row[1], row[2], row[3].casefold()))
    selected = [(score, value) for score, _token_score, _length, value in ranked[:limit]]
    if selected:
        return EditorResolution(
            typed,
            "fuzzy",
            tuple(value for score, value in selected),
            tuple(score for score, value in selected),
        )
    return EditorResolution(typed, "new", ())

StatTermStatus = Literal["exact", "alias", "ambiguous", "fuzzy", "unknown", "empty"]


@dataclass(frozen=True)
class StatTermResolution:
    status: StatTermStatus
    typed_value: str
    candidates: tuple[str, ...]
    display_labels: tuple[str, ...]
    scores: tuple[float, ...] = ()


def preferred_stat_alias(stat_name: str) -> str | None:
    normalized_stat = normalize_stat_text(stat_name)
    pinned = STAT_PREFERRED_DISPLAY_ALIASES.get(normalized_stat)
    if pinned is not None:
        return pinned

    matches = [
        alias
        for alias, target in STAT_ALIASES.items()
        if normalize_stat_text(target) == normalized_stat
        and normalize_stat_text(alias) != normalized_stat
    ]
    if not matches:
        return None
    return sorted(matches, key=lambda value: (len(value), value.casefold()))[0]


def _stat_display_label(stat_name: str) -> str:
    alias = preferred_stat_alias(stat_name)
    if alias is None:
        return stat_name
    return f"{stat_name} ({alias})"


def _dynamic_stat_candidates(
    normalized_query: str,
    available_stats: tuple[str, ...],
) -> tuple[str, ...]:
    category = STAT_CATEGORY_ALIASES.get(normalized_query)
    if category is None:
        return ()
    if category == "damage_to_element":
        matches = [
            stat
            for stat in available_stats
            if normalize_stat_text(stat).startswith("% stronger against ")
        ]
    elif category == "barrier":
        matches = [
            stat
            for stat in available_stats
            if "barrier" in normalize_stat_text(stat).split()
        ]
    else:
        matches = [
            stat
            for stat in available_stats
            if any(
                token.startswith("resist")
                for token in normalize_stat_text(stat).split()
            )
        ]
    return tuple(sorted(matches, key=lambda value: value.casefold()))


def resolve_stat_term(
    text: str,
    available_stats: Iterable[str],
    *,
    allow_fuzzy: bool,
    fuzzy_threshold: float = 70.0,
    limit: int = 5,
) -> StatTermResolution:
    typed = text.strip()
    if not typed:
        return StatTermResolution("empty", typed, (), ())

    values = tuple(
        dict.fromkeys(
            value
            for value in available_stats
            if value
            and value.strip()
            and normalize_stat_text(value) != "upgrade for"
        )
    )
    by_normalized: dict[str, list[str]] = {}
    for value in values:
        by_normalized.setdefault(normalize_stat_text(value), []).append(value)
    normalized = normalize_stat_text(typed)

    explicit_group = STAT_AMBIGUOUS_GROUPS.get(normalized)
    if explicit_group:
        candidates = tuple(
            sorted(
                (
                    actual
                    for requested in explicit_group
                    for actual in by_normalized.get(normalize_stat_text(requested), ())
                ),
                key=lambda value: explicit_group.index(
                    next(
                        requested
                        for requested in explicit_group
                        if normalize_stat_text(requested) == normalize_stat_text(value)
                    )
                ),
            )
        )
        if candidates:
            return StatTermResolution(
                "ambiguous",
                typed,
                candidates,
                tuple(_stat_display_label(value) for value in candidates),
            )

    dynamic = _dynamic_stat_candidates(normalized, values)
    if dynamic:
        return StatTermResolution(
            "ambiguous",
            typed,
            dynamic,
            tuple(_stat_display_label(value) for value in dynamic),
        )

    aliases_by_normalized = {
        normalize_stat_text(alias): normalize_stat_text(target)
        for alias, target in STAT_ALIASES.items()
    }
    if normalized in STAT_FORCED_ALIASES:
        forced_target = aliases_by_normalized.get(normalized)
        if forced_target is not None:
            matches = by_normalized.get(forced_target, ())
            if matches:
                chosen = sorted(matches, key=lambda value: (len(value), value.casefold()))[0]
                return StatTermResolution(
                    "alias",
                    typed,
                    (chosen,),
                    (_stat_display_label(chosen),),
                    (100.0,),
                )

    exact_matches = by_normalized.get(normalized, ())
    if exact_matches:
        chosen = sorted(exact_matches, key=lambda value: (len(value), value.casefold()))[0]
        return StatTermResolution(
            "exact",
            typed,
            (chosen,),
            (_stat_display_label(chosen),),
            (100.0,),
        )

    aliases_by_normalized = {
        normalize_stat_text(alias): normalize_stat_text(target)
        for alias, target in STAT_ALIASES.items()
    }
    alias_target = aliases_by_normalized.get(normalized)
    if alias_target is not None:
        matches = by_normalized.get(alias_target, ())
        if matches:
            chosen = sorted(matches, key=lambda value: (len(value), value.casefold()))[0]
            return StatTermResolution(
                "alias",
                typed,
                (chosen,),
                (_stat_display_label(chosen),),
                (100.0,),
            )

    if not allow_fuzzy:
        return StatTermResolution("unknown", typed, (), ())

    aliases_for_stat: dict[str, list[str]] = {}
    for alias, target in STAT_ALIASES.items():
        aliases_for_stat.setdefault(normalize_stat_text(target), []).append(
            normalize_stat_text(alias)
        )

    ranked: list[tuple[float, float, int, str]] = []
    for value in values:
        canonical = normalize_stat_text(value)
        representations = (canonical, *aliases_for_stat.get(canonical, ()))
        eligible: list[tuple[float, float]] = []
        for representation in representations:
            weighted = float(fuzz.WRatio(normalized, representation))
            token_score = float(fuzz.token_sort_ratio(normalized, representation))
            if weighted >= fuzzy_threshold and token_score >= fuzzy_threshold:
                eligible.append((weighted, token_score))
        if not eligible:
            continue
        weighted, token_score = max(
            eligible,
            key=lambda pair: (min(pair), max(pair)),
        )
        ranked.append((max(weighted, token_score), min(weighted, token_score), len(value), value))

    ranked.sort(key=lambda row: (-row[0], -row[1], row[2], row[3].casefold()))
    selected = ranked[:limit]
    if not selected:
        return StatTermResolution("unknown", typed, (), ())
    candidates = tuple(row[3] for row in selected)
    return StatTermResolution(
        "fuzzy",
        typed,
        candidates,
        tuple(_stat_display_label(value) for value in candidates),
        tuple(row[0] for row in selected),
    )
