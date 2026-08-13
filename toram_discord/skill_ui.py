from __future__ import annotations

from pathlib import Path
from typing import Sequence

import discord

from toram_discord.render import PAGE_SIZE, truncate_discord_text
from toram_discord.sessions import DiscordSessionManager, SessionKey
from toram_discord.skill_detail_pages import (
    SkillDetailPage,
    build_skill_detail_pages,
)
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


def render_skill_detail_page(
    page: SkillDetailPage,
    *,
    page_index: int,
    total_pages: int,
) -> discord.Embed:
    embed = discord.Embed(title=page.title, description=page.description)
    for field in page.fields:
        embed.add_field(
            name=field.name,
            value=field.value,
            inline=field.inline,
        )
    if total_pages > 1:
        embed.set_footer(text=f"Page {page_index + 1} / {total_pages}")
    return embed


def build_skill_detail_embed(payload: SkillDetailPayload) -> discord.Embed:
    pages = build_skill_detail_pages(payload)
    return render_skill_detail_page(
        pages[0],
        page_index=0,
        total_pages=len(pages),
    )


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
        pages = build_skill_detail_pages(detail)
        session = self.sessions.get(self.key)
        if session is not None:
            session.selected_index = index
        await interaction.response.edit_message(
            embed=render_skill_detail_page(
                pages[0],
                page_index=0,
                total_pages=len(pages),
            ),
            view=SkillDetailView(
                sessions=self.sessions,
                key=self.key,
                generation=self.generation,
                pages=pages,
                page_index=0,
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
        pages: tuple[SkillDetailPage, ...],
        page_index: int = 0,
        results_payload: SkillResultsPayload | None = None,
    ) -> None:
        if not pages:
            raise ValueError("Skill detail view requires at least one page")
        super().__init__(
            sessions=sessions,
            key=key,
            generation=generation,
            owner_id=key[2],
        )
        self.pages = pages
        self.page_index = min(max(page_index, 0), len(pages) - 1)
        self.results_payload = results_payload

        if len(pages) > 1:
            self.previous_button = ActionButton(
                label="Previous",
                style=discord.ButtonStyle.secondary,
                disabled=self.page_index == 0,
                handler=self._previous,
                row=0,
            )
            self.next_button = ActionButton(
                label="Next",
                style=discord.ButtonStyle.secondary,
                disabled=self.page_index >= len(pages) - 1,
                handler=self._next,
                row=0,
            )
            self.add_item(self.previous_button)
            self.add_item(self.next_button)
        else:
            self.previous_button = None
            self.next_button = None

        if results_payload is not None:
            self.back_button = ActionButton(
                label="Back to Results",
                style=discord.ButtonStyle.primary,
                handler=self._back,
                row=1,
            )
            self.add_item(self.back_button)
        else:
            self.back_button = None

    def _replacement_view(self, page_index: int) -> SkillDetailView:
        return SkillDetailView(
            sessions=self.sessions,
            key=self.key,
            generation=self.generation,
            pages=self.pages,
            page_index=page_index,
            results_payload=self.results_payload,
        )

    async def _previous(self, interaction: discord.Interaction) -> None:
        next_index = max(self.page_index - 1, 0)
        await interaction.response.edit_message(
            embed=render_skill_detail_page(
                self.pages[next_index],
                page_index=next_index,
                total_pages=len(self.pages),
            ),
            view=self._replacement_view(next_index),
        )

    async def _next(self, interaction: discord.Interaction) -> None:
        next_index = min(self.page_index + 1, len(self.pages) - 1)
        await interaction.response.edit_message(
            embed=render_skill_detail_page(
                self.pages[next_index],
                page_index=next_index,
                total_pages=len(self.pages),
            ),
            view=self._replacement_view(next_index),
        )

    async def _back(self, interaction: discord.Interaction) -> None:
        if self.results_payload is None:
            return
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
        pages = build_skill_detail_pages(payload)
        view = None
        if len(pages) > 1:
            view = SkillDetailView(
                sessions=sessions,
                key=key,
                generation=generation,
                pages=pages,
                page_index=0,
                results_payload=None,
            )
        return (
            render_skill_detail_page(
                pages[0],
                page_index=0,
                total_pages=len(pages),
            ),
            view,
        )

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
    "render_skill_detail_page",
    "run_skill_query_sync",
]
