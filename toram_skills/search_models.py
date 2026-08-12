from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class SkillFilters:
    tree_ids: tuple[str, ...] = ()
    tree_groups: tuple[str, ...] = ()
    tiers: tuple[int, ...] = ()
    required_level_max: int | None = None
    skill_types: tuple[str, ...] = ()
    mp_cost_max: int | None = None
    damage_types: tuple[str, ...] = ()
    ailments: tuple[str, ...] = ()
    weapons: tuple[str, ...] = ()


SearchChannel = Literal["lexical", "semantic"]


@dataclass(frozen=True)
class ChannelScore:
    channel: SearchChannel
    rank: int
    raw_score: float


@dataclass(frozen=True)
class SkillSearchHit:
    skill_id: str
    score: float
    channels: tuple[ChannelScore, ...] = ()
    evidence_document_ids: tuple[str, ...] = ()


__all__ = ["ChannelScore", "SearchChannel", "SkillFilters", "SkillSearchHit"]
