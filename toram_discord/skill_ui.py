from __future__ import annotations

import discord

from toram_discord.render import PAGE_SIZE, _safe_field, truncate_discord_text
from toram_skill_search.models import SkillDetailPayload, SkillResultsPayload


def build_skill_results_embed(
    payload: SkillResultsPayload,
    page: int,
) -> discord.Embed:
    total = len(payload.results)
    if total == 0:
        return discord.Embed(
            title="No skill results",
            description=(
                "No matching skills were found.\n\n"
                "Try:\n"
                "• `skill magic finale`\n"
                "• `skill attack while moving`\n"
                "• `skill inflict tumble`"
            ),
        )

    max_page = max((total - 1) // PAGE_SIZE, 0)
    page = min(max(page, 0), max_page)
    start = page * PAGE_SIZE
    end = min(start + PAGE_SIZE, total)
    lines = ["Closest matches first", f"Showing {start + 1}–{end} of {total}", ""]
    for index in range(start, end):
        result = payload.results[index]
        lines.append(f"{index + 1}. **{result.skill.name}** — {result.tree.name}")
        if result.snippet.strip():
            lines.append(f"   {truncate_discord_text(result.snippet, 300)}")
        lines.append("")
    return discord.Embed(
        title=truncate_discord_text(f"Skill search: {payload.query}", 256),
        description=truncate_discord_text("\n".join(lines).rstrip(), 4096),
    )


def build_skill_detail_embed(payload: SkillDetailPayload) -> discord.Embed:
    skill = payload.skill
    embed = discord.Embed(
        title=truncate_discord_text(skill.name, 256),
        description=truncate_discord_text(payload.tree.name, 4096),
    )

    overview = []
    for label, value in (
        ("Tier", skill.tier),
        ("Required level", skill.required_level),
        ("Skill type", skill.skill_type),
        ("MP cost", skill.mp_cost_text),
        ("Damage type", skill.damage_type),
        ("Element", skill.element),
    ):
        if value is not None and str(value).strip():
            overview.append(f"{label}: {value}")
    if overview:
        _safe_field(embed, "Overview", "\n".join(overview))

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
        _safe_field(embed, "Range / Timing", "\n".join(timing))

    for label, values in (
        ("Ailments", skill.ailments),
        ("Weapon requirements", skill.weapon_requirements),
        ("Weapon restrictions", skill.weapon_restrictions),
    ):
        if values:
            _safe_field(embed, label, ", ".join(values))

    if skill.description and skill.description.strip():
        _safe_field(embed, "Description", skill.description)
    if skill.game_description and skill.game_description.strip():
        _safe_field(embed, "Game description", skill.game_description)

    for section in skill.sections:
        if section.body.strip() and len(embed.fields) < 25:
            _safe_field(embed, section.label, section.body)

    return embed


__all__ = ["build_skill_detail_embed", "build_skill_results_embed"]
