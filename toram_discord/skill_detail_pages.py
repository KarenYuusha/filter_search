from __future__ import annotations

from dataclasses import dataclass
import re

from toram_skill_search.models import SkillDetailPayload


DISCORD_EMBED_TOTAL_LIMIT = 6000
DISCORD_EMBED_PACK_LIMIT = 5960
DISCORD_TITLE_LIMIT = 256
DISCORD_DESCRIPTION_LIMIT = 4096
DISCORD_FIELD_NAME_LIMIT = 256
DISCORD_FIELD_VALUE_LIMIT = 1024
DISCORD_FIELD_COUNT_LIMIT = 25


@dataclass(frozen=True)
class SkillDetailField:
    name: str
    value: str
    inline: bool = False


@dataclass(frozen=True)
class SkillDetailPage:
    title: str
    description: str
    fields: tuple[SkillDetailField, ...]


def detail_page_char_count(
    page: SkillDetailPage,
    *,
    footer_text: str | None = None,
) -> int:
    total = len(page.title) + len(page.description)
    total += sum(len(field.name) + len(field.value) for field in page.fields)
    if footer_text:
        total += len(footer_text)
    return total


def _split_text(text: str, limit: int) -> tuple[str, ...]:
    remaining = str(text).strip()
    chunks: list[str] = []
    while len(remaining) > limit:
        window = remaining[: limit + 1]
        cut = window.rfind("\n", 1, limit + 1)
        if cut <= 0:
            sentence_matches = list(re.finditer(r"[.!?](?=\s)", window[:limit]))
            cut = sentence_matches[-1].end() if sentence_matches else -1
        if cut <= 0:
            cut = window.rfind(" ", 1, limit + 1)
        if cut <= 0:
            cut = limit
        chunk = remaining[:cut].strip()
        if not chunk:
            chunk = remaining[:limit]
            cut = limit
        chunks.append(chunk)
        remaining = remaining[cut:].lstrip()
    if remaining:
        chunks.append(remaining)
    return tuple(chunks)


def _field_cost(field: SkillDetailField) -> int:
    return len(field.name) + len(field.value)


def _base_page(title: str, description: str) -> SkillDetailPage:
    return SkillDetailPage(title=title, description=description, fields=())


def _can_add(
    page: SkillDetailPage,
    fields: tuple[SkillDetailField, ...],
) -> bool:
    if len(page.fields) + len(fields) > DISCORD_FIELD_COUNT_LIMIT:
        return False
    added = sum(_field_cost(field) for field in fields)
    return detail_page_char_count(page) + added <= DISCORD_EMBED_PACK_LIMIT


def _with_fields(
    page: SkillDetailPage,
    fields: tuple[SkillDetailField, ...],
) -> SkillDetailPage:
    return SkillDetailPage(
        title=page.title,
        description=page.description,
        fields=(*page.fields, *fields),
    )


def _logical_fields(payload: SkillDetailPayload) -> tuple[tuple[str, str], ...]:
    skill = payload.skill
    fields: list[tuple[str, str]] = []

    overview = []
    for label, value in (
        ("Skill type", skill.skill_type),
        ("MP cost", skill.mp_cost_text),
        ("Damage type", skill.damage_type),
        ("Element", skill.element),
    ):
        if value is not None and str(value).strip():
            overview.append(f"{label}: {value}")
    if overview:
        fields.append(("Overview", "\n".join(overview)))

    timing = []
    for label, value in (
        ("Cast range", skill.cast_range_text),
        ("Hit range", skill.hit_range_text),
        ("Cast time", skill.cast_time_text),
        ("Hit count", skill.hit_count_text),
    ):
        if value is not None and str(value).strip():
            timing.append(f"{label}: {value}")
    if timing:
        fields.append(("Range / Timing", "\n".join(timing)))

    for label, values in (
        ("Ailments", skill.ailments),
        ("Weapon requirements", skill.weapon_requirements),
        ("Weapon restrictions", skill.weapon_restrictions),
    ):
        if values:
            fields.append((label, ", ".join(values)))

    if skill.description and skill.description.strip():
        fields.append(("Description", skill.description.strip()))
    if skill.game_description and skill.game_description.strip():
        fields.append(("Game description", skill.game_description.strip()))

    for section in skill.sections:
        if section.body.strip():
            fields.append((section.label.strip() or "Info", section.body.strip()))

    return tuple(fields)


def _section_chunks(name: str, value: str) -> tuple[SkillDetailField, ...]:
    chunks = _split_text(value, DISCORD_FIELD_VALUE_LIMIT)
    return tuple(
        SkillDetailField(
            name=name if index == 0 else f"{name} (continued)",
            value=chunk,
        )
        for index, chunk in enumerate(chunks)
    )


def _validate_page(page: SkillDetailPage) -> None:
    if len(page.title) > DISCORD_TITLE_LIMIT:
        raise ValueError("Skill detail title exceeds Discord limit")
    if len(page.description) > DISCORD_DESCRIPTION_LIMIT:
        raise ValueError("Skill detail description exceeds Discord limit")
    if len(page.fields) > DISCORD_FIELD_COUNT_LIMIT:
        raise ValueError("Skill detail field count exceeds Discord limit")
    for field in page.fields:
        if len(field.name) > DISCORD_FIELD_NAME_LIMIT:
            raise ValueError("Skill detail field name exceeds Discord limit")
        if len(field.value) > DISCORD_FIELD_VALUE_LIMIT:
            raise ValueError("Skill detail field value exceeds Discord limit")
    if detail_page_char_count(page) > DISCORD_EMBED_PACK_LIMIT:
        raise ValueError("Skill detail page exceeds packing budget")


def build_skill_detail_pages(
    payload: SkillDetailPayload,
) -> tuple[SkillDetailPage, ...]:
    skill = payload.skill
    title = skill.name.strip()
    header_parts = [payload.tree.name.strip()]
    if skill.tier is not None:
        header_parts.append(f"Tier {skill.tier}")
    if skill.required_level is not None:
        header_parts.append(f"Required Lv {skill.required_level}")
    description = " • ".join(part for part in header_parts if part)

    base = _base_page(title, description)
    pages: list[SkillDetailPage] = []
    current = base

    for label, body in _logical_fields(payload):
        chunks = _section_chunks(label, body)
        if not chunks:
            continue

        if _can_add(base, chunks):
            if not _can_add(current, chunks) and current.fields:
                pages.append(current)
                current = base
            current = _with_fields(current, chunks)
            continue

        for chunk in chunks:
            single = (chunk,)
            if not _can_add(current, single):
                if current.fields:
                    pages.append(current)
                    current = base
                if not _can_add(current, single):
                    raise ValueError("Skill detail field cannot fit on an empty page")
            current = _with_fields(current, single)

    if current.fields or not pages:
        pages.append(current)

    result = tuple(pages)
    for page in result:
        _validate_page(page)
    return result


__all__ = [
    "DISCORD_DESCRIPTION_LIMIT",
    "DISCORD_EMBED_PACK_LIMIT",
    "DISCORD_EMBED_TOTAL_LIMIT",
    "DISCORD_FIELD_COUNT_LIMIT",
    "DISCORD_FIELD_NAME_LIMIT",
    "DISCORD_FIELD_VALUE_LIMIT",
    "DISCORD_TITLE_LIMIT",
    "SkillDetailField",
    "SkillDetailPage",
    "build_skill_detail_pages",
    "detail_page_char_count",
]
