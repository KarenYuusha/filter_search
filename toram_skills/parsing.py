from __future__ import annotations

from dataclasses import replace
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
_LINE_FIELD_RE = re.compile(r"(?mi)^\s*(?P<label>[A-Za-z][A-Za-z0-9 /+()'\"%&.-]{0,79}):\s*(?P<value>.*?)\s*$")
_LEGACY_TYPE_RE = re.compile(
    r"(?i)^(?P<kind>Active|Passive|Support)\s+skill(?:\s*\((?P<detail>[^)]+)\))?"
)
_ROMAN_TIERS = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5}
_MINSTREL_PATH = "assist_skills/minstrel_skills.txt"
_MINSTREL_ROSTER_START_RE = re.compile(r"(?mi)^Lvl req,\s*.*$")
_MINSTREL_TIER_RE = re.compile(r"(?i)^Tier\s+(I|II|III|IV|V)\s*$")
_MINSTREL_WEAPON_RE = re.compile(
    r"(?mi)^All Minstrel skills are limited to\s+(.+?)\.?\s*$"
)
_EXPLICIT_TIER_RE = re.compile(
    r"(?mi)^\s*[-*]?\s*Tier\s+(I|II|III|IV|V)\s*:\s*(?:Level\s*(\d+)|None)\s*$"
)
_SHORT_TIER_RE = re.compile(r"(?i)\bT([1-5])\s*(none|lv\s*\d+)\b")
_UNCERTAINTY_PHRASES = (
    "unknown in the source",
    "not sure",
    "expected formula",
    "trial and error",
    "could be",
    "idk",
)

_FIELD_LABELS = {
    "mp cost": "mp_cost",
    "damage type": "damage_type",
    "maximum cast range": "cast_range",
    "action range": "cast_range",
    "hit range": "hit_range",
    "base cast time": "cast_time",
    "hit count": "hit_count",
    "description": "description",
    "game description": "game_description",
    "element": "element",
    "ailment": "ailment",
    "limitation": "limitation",
    "type": "skill_type",
    "alias": "aliases",
    "aliases": "aliases",
    "alternate name": "aliases",
    "alternate names": "aliases",
}


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


def _plain_int(text: str) -> int | None:
    match = re.fullmatch(r"\s*(\d+)\s*", text)
    return int(match.group(1)) if match else None


def _level(text: str) -> int | None:
    match = re.fullmatch(r"\s*(?:Level\s*)?(\d+)\s*", text, re.IGNORECASE)
    return int(match.group(1)) if match else None


def _tier_value(text: str) -> int | None:
    value = text.strip().upper()
    if value in _ROMAN_TIERS:
        return _ROMAN_TIERS[value]
    if value.isdigit() and 1 <= int(value) <= 5:
        return int(value)
    return None


def _explicit_line_fields(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for match in _LINE_FIELD_RE.finditer(text):
        label = normalize_skill_name(match.group("label"))
        field = _FIELD_LABELS.get(label)
        if field is None or field in values:
            continue
        value = match.group("value").strip()
        if value:
            values[field] = value
    return values


def _aliases_from_text(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(part.strip() for part in re.split(r"\s*[;,]\s*", value) if part.strip())


def _ailments_from_text(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    result: list[str] = []
    for part in value.split(";"):
        name = re.split(r"\s*[([]", part.strip(), maxsplit=1)[0].strip()
        if name and name not in result:
            result.append(name)
    return tuple(result)


def _weapon_requirements_from_limitation(value: str | None) -> tuple[str, ...]:
    if not value or not re.search(r"(?i)\bonly\b", value):
        return ()
    restricted = re.split(r"(?i)\bonly\b", value, maxsplit=1)[0].strip()
    if not restricted or normalize_skill_name(restricted) in {"no limit", "none"}:
        return ()
    parts = re.split(r"\s*/\s*|\s*,\s*|\s+and\s+", restricted, flags=re.IGNORECASE)
    return tuple(part.strip() for part in parts if part.strip())


def _legacy_skill_type(text: str) -> tuple[str | None, str | None]:
    for raw_line in text.splitlines():
        match = _LEGACY_TYPE_RE.match(raw_line.strip())
        if not match:
            continue
        kind = match.group("kind").title()
        detail = (match.group("detail") or "").strip()
        damage_type = None
        if detail.casefold() in {"physical", "magic", "magical", "physical/magic"}:
            damage_type = "Magic" if detail.casefold() == "magical" else detail.title()
        return kind, damage_type
    return None, None


def _uncertainty_issues(skill: SkillDraft, source_file: str) -> tuple[ParseIssue, ...]:
    folded = skill.raw_text.casefold()
    if not any(phrase in folded for phrase in _UNCERTAINTY_PHRASES):
        return ()
    return (
        ParseIssue(
            level="warning",
            code="source_uncertainty",
            source_file=source_file,
            skill_name=skill.name,
            message="Source text explicitly marks part of this skill as uncertain",
        ),
    )


def _normalize_skill_draft(skill: SkillDraft, source_file: str) -> SkillDraft:
    fields = _explicit_line_fields(skill.raw_text)

    tier_match = re.search(r"(?mi)^\s*Tier:\s*(.+?)\s*$", skill.raw_text)
    required_match = re.search(r"(?mi)^\s*Required Level:\s*(.+?)\s*$", skill.raw_text)
    tier = _tier_value(tier_match.group(1)) if tier_match else skill.tier
    if tier is None:
        tier = skill.tier
    required_level = _level(required_match.group(1)) if required_match else skill.required_level
    if required_match is None:
        required_level = skill.required_level

    legacy_type, legacy_damage = _legacy_skill_type(skill.raw_text)
    skill_type = fields.get("skill_type") or legacy_type or skill.skill_type
    damage_type = fields.get("damage_type") or legacy_damage or skill.damage_type

    mp_cost_text = fields.get("mp_cost") or skill.mp_cost_text
    cast_range_text = fields.get("cast_range") or skill.cast_range_text
    hit_range_text = fields.get("hit_range") or skill.hit_range_text
    cast_time_text = fields.get("cast_time") or skill.cast_time_text
    hit_count_text = fields.get("hit_count") or skill.hit_count_text
    element = fields.get("element") or skill.element
    ailments = _ailments_from_text(fields.get("ailment")) or skill.ailments
    weapon_requirements = (
        _weapon_requirements_from_limitation(fields.get("limitation"))
        or skill.weapon_requirements
    )
    aliases = _aliases_from_text(fields.get("aliases")) or skill.aliases

    description = fields.get("description") or skill.description or _section_body(skill.sections, "Description")
    game_description = (
        fields.get("game_description")
        or skill.game_description
        or _section_body(skill.sections, "Game Description")
    )

    normalized = replace(
        skill,
        aliases=aliases,
        tier=tier,
        required_level=required_level,
        skill_type=skill_type,
        mp_cost_text=mp_cost_text,
        mp_cost_value=_plain_int(mp_cost_text) if mp_cost_text else None,
        damage_type=damage_type,
        element=element,
        cast_range_text=cast_range_text,
        hit_range_text=hit_range_text,
        cast_time_text=cast_time_text,
        hit_count_text=hit_count_text,
        ailments=ailments,
        weapon_requirements=weapon_requirements,
        description=description,
        game_description=game_description,
    )
    warnings = _uncertainty_issues(normalized, source_file)
    return replace(normalized, issues=tuple((*normalized.issues, *warnings)))


def _parse_tier_requirements(text: str) -> tuple[tuple[int, int | None], ...]:
    requirements: dict[int, int | None] = {}
    for match in _EXPLICIT_TIER_RE.finditer(text):
        tier = _ROMAN_TIERS[match.group(1).upper()]
        requirements[tier] = int(match.group(2)) if match.group(2) else None
    for match in _SHORT_TIER_RE.finditer(text):
        tier = int(match.group(1))
        value = match.group(2).casefold().replace(" ", "")
        requirements[tier] = None if value == "none" else int(value.removeprefix("lv"))
    return tuple(sorted(requirements.items()))


def _minstrel_weapon_restrictions(text: str) -> tuple[str, ...]:
    match = _MINSTREL_WEAPON_RE.search(text)
    if not match:
        return ()
    value = match.group(1).strip().rstrip(".")
    return tuple(part.strip() for part in re.split(r",\s*|\s+and\s+", value) if part.strip())


def parse_standard_skill_file(source: SkillSource) -> ParsedSkillFile:
    matches = list(_SKILL_BLOCK_RE.finditer(source.text))
    general_text = source.text[: matches[0].start()].rstrip() if matches else source.text.rstrip()
    tree_id = skill_tree_id(source.relative_path)
    tree_name = _tree_name(source)
    issues: list[ParseIssue] = []
    if len(matches) != source.marker_count:
        issues.append(
            ParseIssue(
                level="error",
                code="skill_marker_count_mismatch",
                source_file=source.relative_path,
                skill_name=None,
                message=(
                    f"Found {source.marker_count} SKILL markers but parsed {len(matches)} standard blocks"
                ),
            )
        )
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
        skill = SkillDraft(
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
        skill = _normalize_skill_draft(skill, source.relative_path)
        skills.append(skill)
        issues.extend(skill.issues)

    tree = SkillTreeDraft(
        id=tree_id,
        name=tree_name,
        normalized_name=normalize_skill_name(tree_name),
        tree_group=source.tree_group,
        source_file=source.relative_path,
        general_text=general_text,
        tier_requirements=_parse_tier_requirements(source.text),
    )
    return ParsedSkillFile(
        tree=tree,
        skills=tuple(skills),
        issues=tuple(issues),
        discovered_skill_blocks=len(matches),
    )


def _parse_minstrel_roster(source: SkillSource) -> ParsedSkillFile:
    roster_start = _MINSTREL_ROSTER_START_RE.search(source.text)
    tree_id = skill_tree_id(source.relative_path)
    tree_name = _tree_name(source)
    issues: list[ParseIssue] = []
    if roster_start is None:
        issue = ParseIssue(
            level="error",
            code="unresolved_roster_skill",
            source_file=source.relative_path,
            skill_name=None,
            message="Minstrel tier roster was not found",
        )
        tree = SkillTreeDraft(
            id=tree_id,
            name=tree_name,
            normalized_name=normalize_skill_name(tree_name),
            tree_group=source.tree_group,
            source_file=source.relative_path,
            general_text=source.text.rstrip(),
            tier_requirements=_parse_tier_requirements(source.text),
            weapon_restrictions=_minstrel_weapon_restrictions(source.text),
            issues=(issue,),
        )
        return ParsedSkillFile(tree=tree, skills=(), issues=(issue,), discovered_skill_blocks=0)

    roster_entries: list[tuple[str, int]] = []
    current_tier: int | None = None
    for raw_line in source.text[roster_start.end() :].splitlines():
        line = raw_line.strip()
        if not line:
            continue
        tier_match = _MINSTREL_TIER_RE.fullmatch(line)
        if tier_match:
            current_tier = _ROMAN_TIERS[tier_match.group(1).upper()]
            continue
        if current_tier is not None:
            roster_entries.append((line, current_tier))

    source_before_roster = source.text[: roster_start.start()]
    located: list[tuple[int, SkillDraft]] = []
    for source_order, (name, tier) in enumerate(roster_entries):
        heading_re = re.compile(rf"(?mi)^{re.escape(name)}\s*$")
        tag_re = re.compile(rf"(?mi)^{re.escape(name)}\s*\|\s*#.*$")
        headings = list(heading_re.finditer(source_before_roster))
        tags = list(tag_re.finditer(source_before_roster))
        valid_pairs = [
            (heading, tag)
            for heading in headings
            for tag in tags
            if tag.start() > heading.end()
        ]
        if len(headings) != 1 or len(tags) != 1 or len(valid_pairs) != 1:
            issues.append(
                ParseIssue(
                    level="error",
                    code="unresolved_roster_skill",
                    source_file=source.relative_path,
                    skill_name=name,
                    message=f"Could not locate exactly one source block for roster skill: {name}",
                )
            )
            continue
        heading, tag = valid_pairs[0]
        raw_text = source_before_roster[heading.start() : tag.end()].strip()
        body = source_before_roster[heading.end() : tag.start()].strip()
        sections = _extract_sections(body)
        skill = SkillDraft(
            id=skill_id(tree_id, name),
            tree_id=tree_id,
            source_order=source_order,
            name=name,
            normalized_name=normalize_skill_name(name),
            tier=tier,
            sections=sections,
            description=_section_body(sections, "Description"),
            game_description=_section_body(sections, "Game Description"),
            raw_text=raw_text,
        )
        skill = _normalize_skill_draft(skill, source.relative_path)
        issues.extend(skill.issues)
        located.append((heading.start(), skill))

    located.sort(key=lambda item: item[0])
    first_skill_start = located[0][0] if located else roster_start.start()
    general_text = source.text[:first_skill_start].rstrip()
    skills = tuple(skill for _, skill in located)
    tree = SkillTreeDraft(
        id=tree_id,
        name=tree_name,
        normalized_name=normalize_skill_name(tree_name),
        tree_group=source.tree_group,
        source_file=source.relative_path,
        general_text=general_text,
        tier_requirements=_parse_tier_requirements(roster_start.group(0)),
        weapon_restrictions=_minstrel_weapon_restrictions(source.text),
        issues=tuple(issues),
    )
    return ParsedSkillFile(
        tree=tree,
        skills=skills,
        issues=tuple(issues),
        discovered_skill_blocks=len(roster_entries),
    )


def parse_skill_file(source: SkillSource) -> ParsedSkillFile:
    if source.marker_count > 0:
        return parse_standard_skill_file(source)
    if source.relative_path == _MINSTREL_PATH:
        return _parse_minstrel_roster(source)

    tree_id = skill_tree_id(source.relative_path)
    tree_name = _tree_name(source)
    issue = ParseIssue(
        level="error",
        code="unsupported_source_format",
        source_file=source.relative_path,
        skill_name=None,
        message="No deterministic parser is registered for this source format",
    )
    tree = SkillTreeDraft(
        id=tree_id,
        name=tree_name,
        normalized_name=normalize_skill_name(tree_name),
        tree_group=source.tree_group,
        source_file=source.relative_path,
        general_text=source.text.rstrip(),
        tier_requirements=_parse_tier_requirements(source.text),
        issues=(issue,),
    )
    return ParsedSkillFile(tree=tree, skills=(), issues=(issue,), discovered_skill_blocks=0)
