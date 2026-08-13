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
from toram_skill_search import run_skill_search, run_skill_tree_by_id
from toram_skill_search.models import (
    SkillDetailPayload,
    SkillHelpPayload,
    SkillPayload,
    SkillResultItem,
    SkillResultsPayload,
    SkillTreeChoicesPayload,
    SkillTreeConfirmationPayload,
    SkillTreeHelpPayload,
    SkillTreeNotFoundPayload,
    SkillTreeResultsPayload,
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
            f"• `{bot_example_prefix} skill inflict tumble`\n"
            f"• `{bot_example_prefix} skill tree shield`"
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


def _compact_tree_skill_metadata(result: SkillResultItem) -> str:
    skill = result.skill
    parts: list[str] = []
    if skill.tier is not None:
        parts.append(f"Tier {skill.tier}")
    if skill.required_level is not None:
        parts.append(f"Required Lv {skill.required_level}")
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


def build_skill_tree_results_embed(
    payload: SkillTreeResultsPayload,
    page: int,
) -> discord.Embed:
    total = len(payload.results)
    if total == 0:
        return discord.Embed(
            title=truncate_discord_text(payload.tree.name, 256),
            description="0 skills\n\nNo skills are currently stored for this tree.",
        )

    max_page = max((total - 1) // PAGE_SIZE, 0)
    page = min(max(page, 0), max_page)
    start = page * PAGE_SIZE
    end = min(start + PAGE_SIZE, total)
    lines = [f"{total} skills", f"Showing {start + 1}–{end} of {total}", ""]
    for index in range(start, end):
        result = payload.results[index]
        lines.append(f"{index + 1}. **{result.skill.name}**")
        metadata = _compact_tree_skill_metadata(result)
        if metadata:
            lines.append(f"   {metadata}")
        preview = _compact_skill_preview(result.snippet)
        if preview:
            lines.append(f"   {preview}")
        lines.append("")
    return discord.Embed(
        title=truncate_discord_text(payload.tree.name, 256),
        description=truncate_discord_text("\n".join(lines).rstrip(), 4096),
    )


def build_skill_tree_help_embed(
    payload: SkillTreeHelpPayload,
    *,
    bot_example_prefix: str,
) -> discord.Embed:
    intro = (
        f"Use `{bot_example_prefix} skill tree <tree name>`.\n"
        f"Example: `{bot_example_prefix} skill tree shield`\n\n"
    )
    tree_lines = [f"• {name}" for name in payload.tree_names]
    catalog = "Available skill trees:\n" + "\n".join(tree_lines)
    if len(intro) + len(catalog) <= 4096:
        return discord.Embed(
            title="Toram Skill Trees",
            description=intro + catalog,
        )

    embed = discord.Embed(
        title="Toram Skill Trees",
        description=intro.rstrip(),
    )
    chunks: list[str] = []
    current: list[str] = []
    current_length = 0
    for line in tree_lines:
        added = len(line) + (1 if current else 0)
        if current and current_length + added > 1024:
            chunks.append("\n".join(current))
            current = [line]
            current_length = len(line)
        else:
            current.append(line)
            current_length += added
    if current:
        chunks.append("\n".join(current))

    for index, chunk in enumerate(chunks):
        embed.add_field(
            name=(
                "Available skill trees"
                if index == 0
                else "Available skill trees (continued)"
            ),
            value=chunk,
            inline=False,
        )
    return embed


def build_skill_tree_not_found_embed(
    payload: SkillTreeNotFoundPayload,
) -> discord.Embed:
    lines = [f'I couldn\'t find a skill tree matching "{payload.query}".']
    if payload.suggestions:
        lines.extend(
            [
                "",
                "Closest skill trees:",
                *(f"• {name}" for name in payload.suggestions),
            ]
        )
    return discord.Embed(
        title="Skill tree not found",
        description=truncate_discord_text("\n".join(lines), 4096),
    )


def build_skill_tree_confirmation_embed(
    payload: SkillTreeConfirmationPayload,
) -> discord.Embed:
    return discord.Embed(
        title="Confirm skill tree",
        description=f"Did you mean **{payload.suggested_tree.name}**?",
    )


def build_skill_tree_choices_embed(
    payload: SkillTreeChoicesPayload,
) -> discord.Embed:
    lines = [
        "Which skill tree did you mean?",
        "",
        *(f"• {tree.name}" for tree in payload.candidates),
    ]
    return discord.Embed(
        title="Choose a skill tree",
        description=truncate_discord_text("\n".join(lines), 4096),
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


class SkillTreeResultsView(SessionBoundView):
    def __init__(
        self,
        *,
        sessions: DiscordSessionManager,
        key: SessionKey,
        generation: int,
        payload: SkillTreeResultsPayload,
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
                description=truncate_discord_text(
                    _compact_tree_skill_metadata(payload.results[index])
                    or payload.tree.name,
                    100,
                ),
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
            embed=build_skill_tree_results_embed(self.payload, session.page),
            view=SkillTreeResultsView(
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
            embed=build_skill_tree_results_embed(self.payload, session.page),
            view=SkillTreeResultsView(
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
        results_payload: SkillResultsPayload | SkillTreeResultsPayload | None = None,
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
        if isinstance(self.results_payload, SkillTreeResultsPayload):
            embed = build_skill_tree_results_embed(self.results_payload, session.page)
            view = SkillTreeResultsView(
                sessions=self.sessions,
                key=self.key,
                generation=self.generation,
                payload=self.results_payload,
            )
        else:
            embed = build_skill_results_embed(self.results_payload, session.page)
            view = SkillResultsView(
                sessions=self.sessions,
                key=self.key,
                generation=self.generation,
                payload=self.results_payload,
            )
        await interaction.response.edit_message(embed=embed, view=view)


class SkillTreeConfirmationView(SessionBoundView):
    def __init__(
        self,
        *,
        sessions: DiscordSessionManager,
        key: SessionKey,
        generation: int,
        payload: SkillTreeConfirmationPayload,
        database_path: Path,
    ) -> None:
        super().__init__(
            sessions=sessions,
            key=key,
            generation=generation,
            owner_id=key[2],
        )
        self.payload = payload
        self.database_path = Path(database_path)
        self.show_button = ActionButton(
            label="Show skills",
            style=discord.ButtonStyle.primary,
            handler=self._show,
            row=0,
        )
        self.cancel_button = ActionButton(
            label="Cancel",
            style=discord.ButtonStyle.secondary,
            handler=self._cancel,
            row=0,
        )
        self.add_item(self.show_button)
        self.add_item(self.cancel_button)

    async def _show(self, interaction: discord.Interaction) -> None:
        payload = run_skill_tree_by_id(
            self.database_path,
            self.payload.suggested_tree.id,
        )
        session = self.sessions.get(self.key)
        if session is None:
            return
        session.page = 0
        session.selected_index = None
        await interaction.response.edit_message(
            embed=build_skill_tree_results_embed(payload, 0),
            view=(
                SkillTreeResultsView(
                    sessions=self.sessions,
                    key=self.key,
                    generation=self.generation,
                    payload=payload,
                )
                if payload.results
                else None
            ),
        )

    async def _cancel(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="Skill tree search cancelled",
                description="No skill tree was opened.",
            ),
            view=None,
        )


class SkillTreeChoicesView(SessionBoundView):
    def __init__(
        self,
        *,
        sessions: DiscordSessionManager,
        key: SessionKey,
        generation: int,
        payload: SkillTreeChoicesPayload,
        database_path: Path,
    ) -> None:
        super().__init__(
            sessions=sessions,
            key=key,
            generation=generation,
            owner_id=key[2],
        )
        self.payload = payload
        self.database_path = Path(database_path)
        self.tree_select = ActionSelect(
            placeholder="Select a skill tree",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label=truncate_discord_text(tree.name, 100),
                    value=str(index),
                )
                for index, tree in enumerate(payload.candidates)
            ],
            handler=self._select,
            row=0,
        )
        self.add_item(self.tree_select)

    async def _select(
        self,
        interaction: discord.Interaction,
        values: Sequence[str],
    ) -> None:
        if not values or not values[0].isdigit():
            await interaction.response.send_message(
                "Invalid skill tree selection.",
                ephemeral=True,
            )
            return
        index = int(values[0])
        if not (0 <= index < len(self.payload.candidates)):
            await interaction.response.send_message(
                "Invalid skill tree selection.",
                ephemeral=True,
            )
            return

        payload = run_skill_tree_by_id(
            self.database_path,
            self.payload.candidates[index].id,
        )
        session = self.sessions.get(self.key)
        if session is None:
            return
        session.page = 0
        session.selected_index = None
        await interaction.response.edit_message(
            embed=build_skill_tree_results_embed(payload, 0),
            view=(
                SkillTreeResultsView(
                    sessions=self.sessions,
                    key=self.key,
                    generation=self.generation,
                    payload=payload,
                )
                if payload.results
                else None
            ),
        )


def build_skill_payload_message(
    payload: SkillPayload,
    *,
    bot_example_prefix: str,
    sessions: DiscordSessionManager,
    key: SessionKey,
    generation: int,
    database_path: Path,
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
    if isinstance(payload, SkillTreeHelpPayload):
        return (
            build_skill_tree_help_embed(
                payload,
                bot_example_prefix=bot_example_prefix,
            ),
            None,
        )
    if isinstance(payload, SkillTreeNotFoundPayload):
        return build_skill_tree_not_found_embed(payload), None
    if isinstance(payload, SkillTreeConfirmationPayload):
        return (
            build_skill_tree_confirmation_embed(payload),
            SkillTreeConfirmationView(
                sessions=sessions,
                key=key,
                generation=generation,
                payload=payload,
                database_path=database_path,
            ),
        )
    if isinstance(payload, SkillTreeChoicesPayload):
        return (
            build_skill_tree_choices_embed(payload),
            SkillTreeChoicesView(
                sessions=sessions,
                key=key,
                generation=generation,
                payload=payload,
                database_path=database_path,
            ),
        )
    if isinstance(payload, SkillTreeResultsPayload):
        session = sessions.get(key)
        page = session.page if session is not None else 0
        view = None
        if payload.results:
            view = SkillTreeResultsView(
                sessions=sessions,
                key=key,
                generation=generation,
                payload=payload,
            )
        return build_skill_tree_results_embed(payload, page), view
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
    "SkillTreeChoicesView",
    "SkillTreeConfirmationView",
    "SkillTreeResultsView",
    "build_skill_detail_embed",
    "build_skill_help_embed",
    "build_skill_payload_message",
    "build_skill_results_embed",
    "build_skill_tree_help_embed",
    "build_skill_tree_not_found_embed",
    "build_skill_tree_results_embed",
    "render_skill_detail_page",
    "run_skill_query_sync",
]
