from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


SkillChatIntent = Literal[
    "lookup",
    "filter",
    "rank",
    "count",
    "compare_field",
    "explain",
    "compare",
    "database_meta",
    "general_mechanic",
    "refuse",
    "unknown",
]
SortDirection = Literal["asc", "desc"]


@dataclass(frozen=True)
class SkillChatFilter:
    tree_ids: tuple[str, ...] = ()
    tiers: tuple[int, ...] = ()
    skill_types: tuple[str, ...] = ()
    ailments: tuple[str, ...] = ()
    weapons: tuple[str, ...] = ()
    required_level_max: int | None = None
    mp_cost_max: int | None = None


@dataclass(frozen=True)
class SkillChatPlan:
    intent: SkillChatIntent
    skill_ids: tuple[str, ...] = ()
    filters: SkillChatFilter = SkillChatFilter()
    field: str | None = None
    direction: SortDirection | None = None
    limit: int = 5
    mechanic_query: str | None = None
    refusal_reason: str | None = None


@dataclass(frozen=True)
class SkillEvidence:
    document_id: str
    skill_id: str
    skill_name: str
    tree_name: str
    text: str
    source_kind: str
    label: str | None = None


@dataclass(frozen=True)
class SkillChatResult:
    kind: Literal[
        "structured",
        "results",
        "answer",
        "clarify",
        "refuse",
        "not_found",
        "unavailable",
    ]
    text: str | None = None
    skill_ids: tuple[str, ...] = ()
    evidence: tuple[SkillEvidence, ...] = ()


__all__ = [
    "SkillChatFilter",
    "SkillChatIntent",
    "SkillChatPlan",
    "SkillChatResult",
    "SkillEvidence",
    "SortDirection",
]
