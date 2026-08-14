from __future__ import annotations

from dataclasses import dataclass

from toram_skills.models import SkillDraft
from toram_skills.repository import SkillRepository
from toram_skills.search_models import SkillFilters
from toram_skills.structured_search import structured_skill_ids

from .models import SkillChatFilter, SortDirection


COMPARABLE_FIELDS = frozenset({"mp_cost_value", "required_level", "tier"})


@dataclass(frozen=True)
class SkillFieldValue:
    skill: SkillDraft
    field: str
    value: int | None


def _search_filters(filters: SkillChatFilter) -> SkillFilters:
    return SkillFilters(
        tree_ids=filters.tree_ids,
        tiers=filters.tiers,
        required_level_max=filters.required_level_max,
        skill_types=filters.skill_types,
        mp_cost_max=filters.mp_cost_max,
        ailments=filters.ailments,
        weapons=filters.weapons,
    )


class SkillAnalytics:
    def __init__(self, repository: SkillRepository) -> None:
        self.repository = repository

    def filter_skills(
        self,
        filters: SkillChatFilter = SkillChatFilter(),
    ) -> tuple[SkillDraft, ...]:
        return tuple(
            self.repository.get_skill(skill_id)
            for skill_id in structured_skill_ids(
                self.repository,
                _search_filters(filters),
            )
        )

    def count(self, filters: SkillChatFilter = SkillChatFilter()) -> int:
        return len(
            structured_skill_ids(
                self.repository,
                _search_filters(filters),
            )
        )

    def rank(
        self,
        field: str,
        direction: SortDirection,
        *,
        filters: SkillChatFilter = SkillChatFilter(),
        limit: int = 5,
    ) -> tuple[SkillDraft, ...]:
        if field not in COMPARABLE_FIELDS:
            raise ValueError(f"Unsupported rank field: {field}")
        if direction not in ("asc", "desc"):
            raise ValueError(f"Unsupported sort direction: {direction}")
        if limit <= 0:
            return ()

        candidates = [
            skill
            for skill in self.filter_skills(filters)
            if getattr(skill, field) is not None
        ]
        if direction == "asc":
            candidates.sort(
                key=lambda skill: (
                    int(getattr(skill, field)),
                    skill.normalized_name,
                    skill.id,
                )
            )
        else:
            candidates.sort(
                key=lambda skill: (
                    -int(getattr(skill, field)),
                    skill.normalized_name,
                    skill.id,
                )
            )
        return tuple(candidates[:limit])

    def compare_field(
        self,
        skill_ids: tuple[str, ...],
        field: str,
    ) -> tuple[SkillFieldValue, ...]:
        if field not in COMPARABLE_FIELDS:
            raise ValueError(f"Unsupported comparison field: {field}")
        return tuple(
            SkillFieldValue(
                skill=skill,
                field=field,
                value=getattr(skill, field),
            )
            for skill in (self.repository.get_skill(skill_id) for skill_id in skill_ids)
        )


__all__ = ["COMPARABLE_FIELDS", "SkillAnalytics", "SkillFieldValue"]
