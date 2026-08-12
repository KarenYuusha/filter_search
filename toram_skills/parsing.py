from __future__ import annotations

from pathlib import Path
import re

from .models import ParseIssue, ParsedSkillFile, SkillDraft, SkillSection, SkillTreeDraft
from .source_inventory import SkillSource


_SKILL_BLOCK_RE = re.compile(
    r"(?ms)^=+\s*\nSKILL:\s*(?P<name>[^\n]+)\s*\n=+\s*\n(?P<body>.*?)(?=^=+\s*\nSKILL:|\Z)"
)
_SECTION_HEADING_RE = re.compile(
    r"(?m)^(?P<label>[A-Za-z][A-Za-z0-9 /+()'\"%&.-]{0,79}):\s*$"
)


def normalize_skill_name(text: str) -> str:
    return " ".join(text.casefold().replace("’", "'").split())


def _slug(text: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", normalize_skill_name(text)).strip("-")
    if not value:
        raise ValueError("Cannot build identifier from empty text")
    return value


def skill_tree_id(relative_path: str) -> str:
    return Path(relative_path).with_suffix("").as_posix()


def skill_id(tree_id: str, skill_name: str) -> str:
    return f"{tree_id}/{_slug(skill_name)}"


def _tree_name(source: SkillSource) -> str:
    if source.declared_category:
        return source.declared_category.strip()
    return Path(source.relative_path).stem.replace("_", " ").title()


def _extract_sections(body: str) -> tuple[SkillSection, ...]:
    matches = list(_SECTION_HEADING_RE.finditer(body))
    sections: list[SkillSection] = []
    for position, match in enumerate(matches):
        start = match.end()
        end = matches[position + 1].start() if position + 1 < len(matches) else len(body)
        label = match.group("label").strip()
        section_body = body[start:end].strip()
        sections.append(
            SkillSection(
                position=position,
                label=label,
                normalized_label=normalize_skill_name(label),
                body=section_body,
            )
        )
    return tuple(sections)


def _section_body(sections: tuple[SkillSection, ...], label: str) -> str | None:
    wanted = normalize_skill_name(label)
    for section in sections:
        if section.normalized_label == wanted:
            return section.body or None
    return None


def parse_standard_skill_file(source: SkillSource) -> ParsedSkillFile:
    matches = list(_SKILL_BLOCK_RE.finditer(source.text))
    general_text = source.text[: matches[0].start()].rstrip() if matches else source.text.rstrip()
    tree_id = skill_tree_id(source.relative_path)
    tree_name = _tree_name(source)
    issues: list[ParseIssue] = []
    skills: list[SkillDraft] = []
    seen_names: set[str] = set()

    for source_order, match in enumerate(matches):
        name = match.group("name").strip()
        normalized_name = normalize_skill_name(name)
        if normalized_name in seen_names:
            issues.append(
                ParseIssue(
                    level="error",
                    code="duplicate_skill_name",
                    source_file=source.relative_path,
                    skill_name=name,
                    message=f"Duplicate normalized skill name in tree: {name}",
                )
            )
        seen_names.add(normalized_name)
        body = match.group("body").strip()
        raw_text = match.group(0).strip()
        sections = _extract_sections(body)
        skills.append(
            SkillDraft(
                id=skill_id(tree_id, name),
                tree_id=tree_id,
                source_order=source_order,
                name=name,
                normalized_name=normalized_name,
                sections=sections,
                description=_section_body(sections, "Description"),
                game_description=_section_body(sections, "Game Description"),
                raw_text=raw_text,
            )
        )

    tree = SkillTreeDraft(
        id=tree_id,
        name=tree_name,
        normalized_name=normalize_skill_name(tree_name),
        tree_group=source.tree_group,
        source_file=source.relative_path,
        general_text=general_text,
    )
    return ParsedSkillFile(
        tree=tree,
        skills=tuple(skills),
        issues=tuple(issues),
        discovered_skill_blocks=len(matches),
    )
