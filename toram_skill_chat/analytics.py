from __future__ import annotations

from dataclasses import dataclass, replace

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

    def _documented_positive_ailment_ids(
        self,
        ailments: tuple[str, ...],
    ) -> set[str]:
        """Find conservative positive prose evidence such as 'inflict STUN'."""
        matched: set[str] = set()
        for ailment in ailments:
            term = " ".join(str(ailment).casefold().split())
            if not term:
                continue
            positive_patterns = (
                f"%inflict {term}%",
                f"%inflicts {term}%",
                f"%cause {term}%",
                f"%causes {term}%",
            )
            rows = self.repository.connection.execute(
                """
                SELECT DISTINCT skill_id, LOWER(text) AS lowered_text
                FROM skill_search_documents
                WHERE LOWER(text) LIKE ?
                   OR LOWER(text) LIKE ?
                   OR LOWER(text) LIKE ?
                   OR LOWER(text) LIKE ?
                ORDER BY skill_id
                """,
                positive_patterns,
            )
            for row in rows:
                text = str(row["lowered_text"])
                negative_phrases = (
                    f"cannot inflict {term}",
                    f"can't inflict {term}",
                    f"does not inflict {term}",
                    f"doesn't inflict {term}",
                    f"not inflict {term}",
                    f"cannot cause {term}",
                    f"does not cause {term}",
                )
                if any(phrase in text for phrase in negative_phrases):
                    continue
                matched.add(str(row["skill_id"]))
        return matched

    def filter_skills(
        self,
        filters: SkillChatFilter = SkillChatFilter(),
    ) -> tuple[SkillDraft, ...]:
        if not filters.ailments:
            skill_ids = structured_skill_ids(
                self.repository,
                _search_filters(filters),
            )
            return tuple(self.repository.get_skill(skill_id) for skill_id in skill_ids)

        # Structured ailment rows remain authoritative when present. For older parser
        # records where a positive ailment is documented only in prose (for example
        # "Chance to inflict STUN"), conservatively union that evidence while still
        # applying every other structured filter such as tree/weapon/tier.
        eligible_ids = structured_skill_ids(
            self.repository,
            _search_filters(replace(filters, ailments=())),
        )
        eligible = set(eligible_ids)
        structured_matches = set(
            structured_skill_ids(
                self.repository,
                _search_filters(filters),
            )
        )
        documented_matches = self._documented_positive_ailment_ids(filters.ailments)
        matched = structured_matches | (documented_matches & eligible)
        return tuple(
            self.repository.get_skill(skill_id)
            for skill_id in eligible_ids
            if skill_id in matched
        )

    def count(self, filters: SkillChatFilter = SkillChatFilter()) -> int:
        return len(self.filter_skills(filters))

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
