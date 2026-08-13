from __future__ import annotations

from pathlib import Path
from typing import Sequence

import discord

from toram_discord.render import PAGE_SIZE, truncate_discord_text
from toram_discord.sessions import DiscordSessionManager, SessionKey
from toram_discord.views import ActionButton, ActionSelect, SessionBoundView
from toram_skill_search import run_skill_search
from toram_skill_search.models import (
    SkillDetailPayload,
    SkillHelpPayload,
    SkillPayload,
    SkillResultItem,
    SkillResultsPayload,
    SkillUnavailablePayload,
)


_DISCORD_EMBED_TOTAL_LIMIT = 6000


def _embed_char_count(embed: discord.Embed) -> int:
    total = len(embed.title or "") + len(embed.description or "")
    total += sum(len(field.name) + len(field.value) for field in embed.fields)
    footer_text = getattr(embed.footer, "text", None)
    author_name = getattr(embed.author, "name", None)
    if footer_text:
        total += len(footer_text)
    if author_name:
        total += len(author_name)
    return total


def _add_skill_field(
    embed: discord.Embed,
    name: str,
    value: str,
    *,
    inline: bool = False,
) -> bool:
    if len(embed.fields) >= 25:
        return False
    cleaned = str(value).strip()
    if not cleaned:
        return False
    safe_name = truncate_discord_text(str(name).strip() or "Info", 256)
    remaining = _DISCORD_EMBED_TOTAL_LIMIT - _embed_char_count(embed) - len(safe_name)
    if remaining <= 0:
        return False
    safe_value = truncate_discord_text(cleaned, min(1024, remaining))
    if not safe_value:
        return False
    embed.add_field(name=safe_name, value=safe_value, inline=inline)
    return True


def run_skill_query_sync(
    database_path: Path,
    query: str,
    *,
    skill_runner=run_skill_search,
) -> SkillPayload:
    return skill_runner(database_path.resolve(), query)


def build_skill_help_embed(
    payload: SkillHelpPayload,
    *,
    bot_example_prefix: str,
) -> discord.Embed:
    return discord.Embed(
        title="Toram Skill Search",
        description=(
            f"Search skills with `{bot_example_prefix} skill <words>`.\n\n"
            "Examples:\n"
            f"• `{bot_example_prefix} skill magic finale`\n"
            f"• `{bot_example_prefix} skill attack while moving`\n"
            f"• `{bot_example_prefix} skill inflict tumble`"
        ),
    )


def _compact_skill_metadata(result: SkillResultItem) -> str:
    skill = result.skill
    parts = [result.tree.name]
    if skill.tier is not None:
        parts.append(f"Tier {skill.tier}")
    if skill.mp_cost_value is not None:
        parts.append(f"MP {skill.mp_cost_value}")
    elif skill.mp_cost_text and skill.mp_cost_text.strip():
        raw = skill.mp_cost_text.strip()
        folded = raw.casefold()
        if folded.startswith("mp "):
            parts.append(raw)
        elif folded.endswith("mp") and raw[:-2].strip():
            parts.append(f"MP {raw[:-2].strip()}")
        else:
            parts.append(f"MP {raw}")
    display_type = skill.damage_type or skill.skill_type
    if display_type and display_type.strip():
        parts.append(display_type.strip())
    return " • ".join(parts)


def _compact_skill_preview(text: str, limit: int = 160) -> str:
    normalized = " ".join(str(text).split())
    return truncate_discord_text(normalized, limit) if normalized else ""


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
        lines.append(f"{index + 1}. **{result.skill.name}**")
        metadata = _compact_skill_metadata(result)
        if metadata:
            lines.append(f"   {metadata}")
        preview = _compact_skill_preview(result.snippet)
        if preview:
            lines.append(f"   {preview}")
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
        _add_skill_field(embed, "Overview", "\n".join(overview))

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
        _add_skill_field(embed, "Range / Timing", "\n".join(timing))

    for label, values in (
        ("Ailments", skill.ailments),
        ("Weapon requirements", skill.weapon_requirements),
        ("Weapon restrictions", skill.weapon_restrictions),
    ):
        if values:
            _add_skill_field(embed, label, ", ".join(values))

    if skill.description and skill.description.strip():
        _add_skill_field(embed, "Description", skill.description)
    if skill.game_description and skill.game_description.strip():
        _add_skill_field(embed, "Game description", skill.game_description)

    for section in skill.sections:
        if section.body.strip() and not _add_skill_field(embed, section.label, section.body):
            break

    return embed


class SkillResultsView(SessionBoundView):
    def __init__(
        self,
        *,
        sessions: DiscordSessionManager,
        key: SessionKey,
        generation: int,
        payload: SkillResultsPayload,
    ) -> None:
        super().__init__(
            sessions=sessions,
            key=key,
            generation=generation,
            owner_id=key[2],
        )
        self.payload = payload
        session = sessions.get(key)
        page = session.page if session is not None else 0
        total = len(payload.results)
        max_page = max((total - 1) // PAGE_SIZE, 0)
        page = min(max(page, 0), max_page)
        if session is not None:
            session.page = page
        start = page * PAGE_SIZE
        end = min(start + PAGE_SIZE, total)

        options = [
            discord.SelectOption(
                label=truncate_discord_text(payload.results[index].skill.name, 100),
                value=str(index),
                description=truncate_discord_text(payload.results[index].tree.name, 100),
            )
            for index in range(start, end)
        ]
        self.skill_select = None
        if options:
            self.skill_select = ActionSelect(
                placeholder="Select a skill",
                min_values=1,
                max_values=1,
                options=options,
                handler=self._select_skill,
                row=0,
            )
            self.add_item(self.skill_select)

        if total > PAGE_SIZE:
            self.previous_button = ActionButton(
                label="Previous",
                style=discord.ButtonStyle.secondary,
                disabled=page == 0,
                handler=self._previous,
                row=1,
            )
            self.next_button = ActionButton(
                label="Next",
                style=discord.ButtonStyle.secondary,
                disabled=page >= max_page,
                handler=self._next,
                row=1,
            )
            self.add_item(self.previous_button)
            self.add_item(self.next_button)
        else:
            self.previous_button = None
            self.next_button = None

    async def _previous(self, interaction: discord.Interaction) -> None:
        session = self.sessions.get(self.key)
        if session is None:
            return
        session.page = max(session.page - 1, 0)
        await interaction.response.edit_message(
            embed=build_skill_results_embed(self.payload, session.page),
            view=SkillResultsView(
                sessions=self.sessions,
                key=self.key,
                generation=self.generation,
                payload=self.payload,
            ),
        )

    async def _next(self, interaction: discord.Interaction) -> None:
        session = self.sessions.get(self.key)
        if session is None:
            return
        max_page = max((len(self.payload.results) - 1) // PAGE_SIZE, 0)
        session.page = min(session.page + 1, max_page)
        await interaction.response.edit_message(
            embed=build_skill_results_embed(self.payload, session.page),
            view=SkillResultsView(
                sessions=self.sessions,
                key=self.key,
                generation=self.generation,
                payload=self.payload,
            ),
        )

    async def _select_skill(
        self,
        interaction: discord.Interaction,
        values: Sequence[str],
    ) -> None:
        if not values or not values[0].isdigit():
            await interaction.response.send_message(
                "Invalid skill selection.",
                ephemeral=True,
            )
            return
        index = int(values[0])
        if not (0 <= index < len(self.payload.results)):
            await interaction.response.send_message(
                "Invalid skill selection.",
                ephemeral=True,
            )
            return
        result = self.payload.results[index]
        detail = SkillDetailPayload(result.skill, result.tree)
        session = self.sessions.get(self.key)
        if session is not None:
            session.selected_index = index
        await interaction.response.edit_message(
            embed=build_skill_detail_embed(detail),
            view=SkillDetailView(
                sessions=self.sessions,
                key=self.key,
                generation=self.generation,
                results_payload=self.payload,
            ),
        )


class SkillDetailView(SessionBoundView):
    def __init__(
        self,
        *,
        sessions: DiscordSessionManager,
        key: SessionKey,
        generation: int,
        results_payload: SkillResultsPayload,
    ) -> None:
        super().__init__(
            sessions=sessions,
            key=key,
            generation=generation,
            owner_id=key[2],
        )
        self.results_payload = results_payload
        self.add_item(
            ActionButton(
                label="Back to Results",
                style=discord.ButtonStyle.primary,
                handler=self._back,
            )
        )

    async def _back(self, interaction: discord.Interaction) -> None:
        session = self.sessions.get(self.key)
        if session is None:
            return
        session.selected_index = None
        await interaction.response.edit_message(
            embed=build_skill_results_embed(self.results_payload, session.page),
            view=SkillResultsView(
                sessions=self.sessions,
                key=self.key,
                generation=self.generation,
                payload=self.results_payload,
            ),
        )


def build_skill_payload_message(
    payload: SkillPayload,
    *,
    bot_example_prefix: str,
    sessions: DiscordSessionManager,
    key: SessionKey,
    generation: int,
) -> tuple[discord.Embed, discord.ui.View | None]:
    if isinstance(payload, SkillHelpPayload):
        return (
            build_skill_help_embed(
                payload,
                bot_example_prefix=bot_example_prefix,
            ),
            None,
        )
    if isinstance(payload, SkillUnavailablePayload):
        return (
            discord.Embed(
                title="Skill search unavailable",
                description=truncate_discord_text(payload.text, 4096),
            ),
            None,
        )
    if isinstance(payload, SkillDetailPayload):
        return build_skill_detail_embed(payload), None

    session = sessions.get(key)
    page = session.page if session is not None else 0
    view = None
    if payload.results:
        view = SkillResultsView(
            sessions=sessions,
            key=key,
            generation=generation,
            payload=payload,
        )
    return build_skill_results_embed(payload, page), view


__all__ = [
    "SkillDetailView",
    "SkillResultsView",
    "build_skill_detail_embed",
    "build_skill_help_embed",
    "build_skill_payload_message",
    "build_skill_results_embed",
    "run_skill_query_sync",
]
