from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


IssueLevel = Literal["error", "warning"]


@dataclass(frozen=True)
class ParseIssue:
    level: IssueLevel
    code: str
    source_file: str
    skill_name: str | None
    message: str


@dataclass(frozen=True)
class SkillSection:
    position: int
    label: str
    normalized_label: str
    body: str


@dataclass(frozen=True)
class SkillTreeDraft:
    id: str
    name: str
    normalized_name: str
    tree_group: str
    source_file: str
    general_text: str
    tier_requirements: tuple[tuple[int, int | None], ...] = ()
    weapon_restrictions: tuple[str, ...] = ()
    issues: tuple[ParseIssue, ...] = ()


@dataclass(frozen=True)
class SkillDraft:
    id: str
    tree_id: str
    source_order: int
    name: str
    normalized_name: str
    aliases: tuple[str, ...] = ()
    tier: int | None = None
    required_level: int | None = None
    skill_type: str | None = None
    mp_cost_text: str | None = None
    mp_cost_value: int | None = None
    damage_type: str | None = None
    element: str | None = None
    cast_range_text: str | None = None
    hit_range_text: str | None = None
    cast_time_text: str | None = None
    hit_count_text: str | None = None
    ailments: tuple[str, ...] = ()
    weapon_requirements: tuple[str, ...] = ()
    weapon_restrictions: tuple[str, ...] = ()
    sections: tuple[SkillSection, ...] = ()
    description: str | None = None
    game_description: str | None = None
    raw_text: str = ""
    issues: tuple[ParseIssue, ...] = ()


@dataclass(frozen=True)
class ParsedSkillFile:
    tree: SkillTreeDraft
    skills: tuple[SkillDraft, ...]
    issues: tuple[ParseIssue, ...]
    discovered_skill_blocks: int


@dataclass(frozen=True)
class ImportReport:
    files_discovered: int
    trees_created: int
    skill_blocks_discovered: int
    skills_created: int
    manifest_hash: str
    issues: tuple[ParseIssue, ...] = ()

    @property
    def errors(self) -> tuple[ParseIssue, ...]:
        return tuple(issue for issue in self.issues if issue.level == "error")

    @property
    def warnings(self) -> tuple[ParseIssue, ...]:
        return tuple(issue for issue in self.issues if issue.level == "warning")

    @property
    def error_codes(self) -> frozenset[str]:
        return frozenset(issue.code for issue in self.errors)

    @property
    def is_valid(self) -> bool:
        return not self.errors
