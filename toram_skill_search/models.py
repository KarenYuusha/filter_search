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
class SkillTreeResultsPayload:
    tree: SkillTreeDraft
    results: tuple[SkillResultItem, ...]


@dataclass(frozen=True)
class SkillTreeConfirmationPayload:
    query: str
    suggested_tree: SkillTreeDraft


@dataclass(frozen=True)
class SkillTreeChoicesPayload:
    query: str
    candidates: tuple[SkillTreeDraft, ...]


@dataclass(frozen=True)
class SkillTreeNotFoundPayload:
    query: str
    suggestions: tuple[str, ...]


@dataclass(frozen=True)
class SkillTreeHelpPayload:
    tree_names: tuple[str, ...]


@dataclass(frozen=True)
class SkillHelpPayload:
    text: str


@dataclass(frozen=True)
class SkillUnavailablePayload:
    text: str


SkillPayload = (
    SkillDetailPayload
    | SkillResultsPayload
    | SkillTreeResultsPayload
    | SkillTreeConfirmationPayload
    | SkillTreeChoicesPayload
    | SkillTreeNotFoundPayload
    | SkillTreeHelpPayload
    | SkillHelpPayload
    | SkillUnavailablePayload
)


__all__ = [
    "SkillDetailPayload",
    "SkillHelpPayload",
    "SkillPayload",
    "SkillResultItem",
    "SkillResultsPayload",
    "SkillTreeChoicesPayload",
    "SkillTreeConfirmationPayload",
    "SkillTreeHelpPayload",
    "SkillTreeNotFoundPayload",
    "SkillTreeResultsPayload",
    "SkillUnavailablePayload",
]
