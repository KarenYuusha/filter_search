from __future__ import annotations

from dataclasses import replace
import re
from typing import Protocol

from toram_skills.repository import SkillRepository

from .concepts import CONCEPT_ALIASES, resolve_ailment
from .models import SkillChatFilter, SkillChatPlan


_POSSESSIVE_RE = re.compile(r"['’]s\b", re.IGNORECASE)
_NON_WORD_RE = re.compile(r"[^\w]+", re.UNICODE)


class SkillChatContextLike(Protocol):
    active_skill_ids: tuple[str, ...]
    selected_skill_id: str | None
    active_skill_filters: dict[str, object]


_REFUSAL_REASON = (
    "I can compare objective database facts, but I can't decide which skill is "
    "best for DPS, tanking, a build, or overall strength."
)


def _match_normalize(text: str) -> str:
    value = _POSSESSIVE_RE.sub("", str(text).casefold().replace("’", "'"))
    return " ".join(_NON_WORD_RE.sub(" ", value).split())


def _contains_phrase(normalized_query: str, phrase: str) -> bool:
    return f" {phrase} " in f" {normalized_query} "


def _context_filter(context: SkillChatContextLike | None) -> SkillChatFilter:
    if context is None:
        return SkillChatFilter()
    values = getattr(context, "active_skill_filters", {}) or {}

    def strings(name: str) -> tuple[str, ...]:
        value = values.get(name, ())
        if isinstance(value, str):
            return (value,)
        if isinstance(value, (tuple, list)):
            return tuple(str(item) for item in value)
        return ()

    def ints(name: str) -> tuple[int, ...]:
        value = values.get(name, ())
        if isinstance(value, (tuple, list)):
            return tuple(int(item) for item in value if isinstance(item, int))
        return ()

    def optional_int(name: str) -> int | None:
        value = values.get(name)
        return value if isinstance(value, int) else None

    return SkillChatFilter(
        tree_ids=strings("tree_ids"),
        tiers=ints("tiers"),
        skill_types=strings("skill_types"),
        ailments=strings("ailments"),
        weapons=strings("weapons"),
        required_level_max=optional_int("required_level_max"),
        mp_cost_max=optional_int("mp_cost_max"),
    )


class SkillChatRouter:
    def __init__(self, repository: SkillRepository) -> None:
        self.repository = repository
        self._known_ailments = repository.list_known_ailments()
        self._skill_phrases = self._load_skill_phrases()
        self._tree_phrases = self._load_tree_phrases()

    def _load_skill_phrases(self) -> tuple[tuple[str, str], ...]:
        rows = self.repository.connection.execute(
            """
            SELECT id, normalized_name AS phrase
            FROM skills
            UNION ALL
            SELECT skill_id AS id, normalized_alias AS phrase
            FROM skill_aliases
            """
        )
        values = {
            (_match_normalize(str(row["phrase"])), str(row["id"]))
            for row in rows
            if _match_normalize(str(row["phrase"]))
        }
        return tuple(sorted(values, key=lambda value: (-len(value[0]), value[0], value[1])))

    def _load_tree_phrases(self) -> tuple[tuple[str, str], ...]:
        values: set[tuple[str, str]] = set()
        rows = self.repository.connection.execute(
            "SELECT id, normalized_name FROM skill_trees"
        )
        for row in rows:
            tree_id = str(row["id"])
            name = _match_normalize(str(row["normalized_name"]))
            values.add((name, tree_id))
            if name.endswith(" skills"):
                shorthand = name[:-len(" skills")].strip()
                if shorthand:
                    values.add((shorthand, tree_id))
        return tuple(sorted(values, key=lambda value: (-len(value[0]), value[0], value[1])))

    def _skill_ids(self, normalized_query: str) -> tuple[str, ...]:
        padded = f" {normalized_query} "
        matches: list[tuple[int, int, str]] = []
        for phrase, skill_id in self._skill_phrases:
            needle = f" {phrase} "
            start = padded.find(needle)
            if start >= 0:
                matches.append((start, -len(phrase), skill_id))
        matches.sort()
        seen: set[str] = set()
        ordered: list[str] = []
        for _, _, skill_id in matches:
            if skill_id not in seen:
                seen.add(skill_id)
                ordered.append(skill_id)
        return tuple(ordered)

    def _tree_id(self, normalized_query: str) -> str | None:
        for phrase, tree_id in self._tree_phrases:
            if _contains_phrase(normalized_query, phrase):
                return tree_id
        return None

    def _ailment(self, normalized_query: str) -> str | None:
        candidates = tuple(self._known_ailments) + tuple(CONCEPT_ALIASES)
        for candidate in candidates:
            normalized = _match_normalize(candidate)
            if not _contains_phrase(normalized_query, normalized):
                continue
            resolved = resolve_ailment(candidate, self._known_ailments)
            if resolved.canonical is not None:
                return resolved.canonical
        return None

    @staticmethod
    def _is_refusal(normalized_query: str) -> bool:
        phrases = (
            "highest dps",
            "most damage",
            "strongest skill",
            "best combo",
            "best dps",
            "best tank",
            "best mage",
        )
        if any(phrase in normalized_query for phrase in phrases):
            return True
        return (
            normalized_query.startswith("best ")
            and any(word in normalized_query for word in ("skill", "build", "dps", "tank", "mage"))
        )

    def route(
        self,
        query: str,
        *,
        context: SkillChatContextLike | None = None,
    ) -> SkillChatPlan:
        cleaned = " ".join(str(query).split())
        normalized = _match_normalize(cleaned)
        if not normalized:
            return SkillChatPlan(intent="unknown")

        if self._is_refusal(normalized):
            return SkillChatPlan(intent="refuse", refusal_reason=_REFUSAL_REASON)

        active_ids = tuple(getattr(context, "active_skill_ids", ()) or ())
        selected_id = getattr(context, "selected_skill_id", None) if context is not None else None

        if "mp cost" in normalized and (" its " in f" {normalized} " or normalized.startswith("what about")):
            if selected_id:
                return SkillChatPlan(intent="lookup", skill_ids=(selected_id,), field="mp_cost")
            if len(active_ids) == 1:
                return SkillChatPlan(intent="lookup", skill_ids=active_ids, field="mp_cost")
            if len(active_ids) > 1:
                return SkillChatPlan(
                    intent="unknown",
                    skill_ids=active_ids,
                    mechanic_query="ambiguous_reference",
                )

        if normalized in {
            "how does it work",
            "what does it do",
            "how does this work",
            "what does this do",
        }:
            if selected_id:
                return SkillChatPlan(intent="explain", skill_ids=(selected_id,))
            if len(active_ids) == 1:
                return SkillChatPlan(intent="explain", skill_ids=active_ids)
            if len(active_ids) > 1:
                return SkillChatPlan(
                    intent="unknown",
                    skill_ids=active_ids,
                    mechanic_query="ambiguous_reference",
                )
            return SkillChatPlan(intent="unknown")

        if normalized.startswith("only "):
            tree_id = self._tree_id(normalized)
            if tree_id is not None:
                filters = replace(_context_filter(context), tree_ids=(tree_id,))
                return SkillChatPlan(intent="filter", filters=filters)

        if active_ids and "mp" in normalized and "which one" in normalized:
            if any(word in normalized for word in ("least", "lowest", "less", "cheapest")):
                return SkillChatPlan(
                    intent="rank",
                    skill_ids=active_ids,
                    field="mp_cost_value",
                    direction="asc",
                    limit=1,
                )
            if any(word in normalized for word in ("highest", "most", "more")):
                return SkillChatPlan(
                    intent="rank",
                    skill_ids=active_ids,
                    field="mp_cost_value",
                    direction="desc",
                    limit=1,
                )

        skill_ids = self._skill_ids(normalized)

        if "mp" in normalized and any(word in normalized for word in ("highest", "lowest", "least")):
            direction = "desc" if "highest" in normalized else "asc"
            tree_id = self._tree_id(normalized)
            filters = SkillChatFilter(tree_ids=(tree_id,)) if tree_id else SkillChatFilter()
            return SkillChatPlan(
                intent="rank",
                filters=filters,
                field="mp_cost_value",
                direction=direction,
                limit=5,
            )

        ailment = self._ailment(normalized)
        if ailment is not None and any(word in normalized for word in ("inflict", "inflicts", "cause", "causes")):
            intent = "count" if normalized.startswith("how many") else "filter"
            return SkillChatPlan(
                intent=intent,
                filters=SkillChatFilter(ailments=(ailment,)),
            )

        if len(skill_ids) >= 2:
            pair = skill_ids[:2]
            if "unlocks" in normalized:
                direction = "desc" if "later" in normalized else "asc"
                return SkillChatPlan(
                    intent="compare_field",
                    skill_ids=pair,
                    field="required_level",
                    direction=direction,
                )
            if "mp" in normalized and any(word in normalized for word in ("cost", "costs")):
                direction = "asc" if any(word in normalized for word in ("less", "least")) else "desc"
                return SkillChatPlan(
                    intent="compare_field",
                    skill_ids=pair,
                    field="mp_cost_value",
                    direction=direction,
                )
            if normalized.startswith("compare "):
                return SkillChatPlan(intent="compare", skill_ids=pair)

        if len(skill_ids) == 1:
            skill_id = skill_ids[0]
            if normalized.startswith("how does ") and normalized.endswith(" work"):
                return SkillChatPlan(intent="explain", skill_ids=(skill_id,))
            if normalized.startswith("what does ") and normalized.endswith(" do"):
                return SkillChatPlan(intent="explain", skill_ids=(skill_id,))
            if "what tree" in normalized:
                return SkillChatPlan(intent="lookup", skill_ids=(skill_id,), field="tree")
            if "mp cost" in normalized:
                return SkillChatPlan(intent="lookup", skill_ids=(skill_id,), field="mp_cost")
            if "what tier" in normalized:
                return SkillChatPlan(intent="lookup", skill_ids=(skill_id,), field="tier")
            if normalized.startswith("what is "):
                return SkillChatPlan(intent="lookup", skill_ids=(skill_id,))

        if normalized.startswith("what is "):
            mechanic = re.sub(r"[?!.]+$", "", cleaned[len("what is "):].strip()).strip()
            if mechanic:
                return SkillChatPlan(intent="general_mechanic", mechanic_query=mechanic)
        if normalized.startswith("how does ") and normalized.endswith(" work"):
            mechanic = re.sub(r"[?!.]+$", "", cleaned[len("how does "):].strip()).strip()
            mechanic = re.sub(r"\s+work$", "", mechanic, flags=re.IGNORECASE).strip()
            if mechanic:
                return SkillChatPlan(intent="general_mechanic", mechanic_query=mechanic)

        return SkillChatPlan(intent="unknown")


__all__ = ["SkillChatContextLike", "SkillChatRouter"]
