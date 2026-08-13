from __future__ import annotations

from dataclasses import dataclass

from toram_skills.models import SkillDraft, SkillTreeDraft


@dataclass(frozen=True)
class SkillDetailPayload:
    skill: SkillDraft
    tree: SkillTreeDraft


@dataclass(frozen=True)
class SkillResultItem:
    skill: SkillDraft
    tree: SkillTreeDraft
    snippet: str


@dataclass(frozen=True)
class SkillResultsPayload:
    query: str
    results: tuple[SkillResultItem, ...]


@dataclass(frozen=True)
class SkillHelpPayload:
    text: str


@dataclass(frozen=True)
class SkillUnavailablePayload:
    text: str


SkillPayload = (
    SkillDetailPayload
    | SkillResultsPayload
    | SkillHelpPayload
    | SkillUnavailablePayload
)


__all__ = [
    "SkillDetailPayload",
    "SkillHelpPayload",
    "SkillPayload",
    "SkillResultItem",
    "SkillResultsPayload",
    "SkillUnavailablePayload",
]
