from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha1
from pathlib import Path
from typing import Sequence

import discord

from toram_discord.render import PAGE_SIZE, truncate_discord_text
from toram_discord.skill_icons import DEFAULT_SKILL_ICON_CATALOG, SkillIconCatalog
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


@dataclass(frozen=True)
class SkillRenderedMessage:
    embeds: tuple[discord.Embed, ...]
    files: tuple[discord.File, ...] = ()
    view: discord.ui.View | None = None

    @property
    def primary_embed(self) -> discord.Embed:
        if not self.embeds:
            raise ValueError("Skill rendered message requires at least one embed")
        return self.embeds[0]

    def __iter__(self):
        """Keep legacy `(embed, view)` unpacking working for existing callers/tests."""
        yield self.primary_embed
        yield self.view


def _page_bounds(total: int, page: int) -> tuple[int, int, int, int]:
    max_page = max((total - 1) // PAGE_SIZE, 0)
    page = min(max(page, 0), max_page)
    start = page * PAGE_SIZE
    end = min(start + PAGE_SIZE, total)
    return page, max_page, start, end


def _attachment_name(path: Path, slot: int) -> str:
    digest = sha1(str(path).encode("utf-8")).hexdigest()[:10]
    suffix = path.suffix.casefold() if path.suffix else ".png"
    return f"skill-{slot}-{digest}{suffix}"


def _attach_skill_icon(
    embed: discord.Embed,
    *,
    path: Path | None,
    slot: int,
) -> discord.File | None:
    if path is None or not path.is_file():
        return None
    filename = _attachment_name(path, slot)
    file = discord.File(path, filename=filename)
    embed.set_thumbnail(url=f"attachment://{filename}")
    return file


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


def _compact_search_card_metadata(result: SkillResultItem) -> str:
    detail = _compact_tree_skill_metadata(result)
    return " • ".join(part for part in (result.tree.name, detail) if part)


def _compact_skill_preview(text: str, limit: int = 160) -> str:
    normalized = " ".join(str(text).split())
    return truncate_discord_text(normalized, limit) if normalized else ""


def _build_skill_card(
    result: SkillResultItem,
    *,
    absolute_index: int,
    tree_listing: bool,
    slot: int,
    icon_catalog: SkillIconCatalog,
) -> tuple[discord.Embed, discord.File | None]:
    metadata = (
        _compact_tree_skill_metadata(result)
        if tree_listing
        else _compact_search_card_metadata(result)
    )
    preview = _compact_skill_preview(result.snippet)
    lines = [line for line in (metadata, preview) if line]
    embed = discord.Embed(
        title=truncate_discord_text(
            f"{absolute_index + 1}. {result.skill.name}",
            256,
        ),
        description=(
            truncate_discord_text("\n".join(lines), 4096)
            if lines
            else None
        ),
    )
    file = _attach_skill_icon(
        embed,
        path=icon_catalog.resolve(result.tree.name, result.skill.name),
        slot=slot,
    )
    return embed, file


def build_skill_results_message(
    payload: SkillResultsPayload,
    page: int,
    *,
    view: discord.ui.View | None = None,
    icon_catalog: SkillIconCatalog = DEFAULT_SKILL_ICON_CATALOG,
) -> SkillRenderedMessage:
    total = len(payload.results)
    if total == 0:
        return SkillRenderedMessage(
            embeds=(build_skill_results_embed(payload, page),),
            view=view,
        )

    page, max_page, start, end = _page_bounds(total, page)
    header = discord.Embed(
        title=truncate_discord_text(f"Skill search: {payload.query}", 256),
        description=f"Closest matches first · Showing {start + 1}–{end} of {total}",
    )
    if max_page > 0:
        header.set_footer(text=f"Page {page + 1} / {max_page + 1}")

    embeds: list[discord.Embed] = [header]
    files: list[discord.File] = []
    for slot, index in enumerate(range(start, end), start=1):
        card, file = _build_skill_card(
            payload.results[index],
            absolute_index=index,
            tree_listing=False,
            slot=slot,
            icon_catalog=icon_catalog,
        )
        embeds.append(card)
        if file is not None:
            files.append(file)
    return SkillRenderedMessage(tuple(embeds), tuple(files), view)


def build_skill_tree_results_message(
    payload: SkillTreeResultsPayload,
    page: int,
    *,
    view: discord.ui.View | None = None,
    icon_catalog: SkillIconCatalog = DEFAULT_SKILL_ICON_CATALOG,
) -> SkillRenderedMessage:
    total = len(payload.results)
    if total == 0:
        return SkillRenderedMessage(
            embeds=(build_skill_tree_results_embed(payload, page),),
            view=view,
        )

    page, max_page, start, end = _page_bounds(total, page)
    header = discord.Embed(
        title=truncate_discord_text(payload.tree.name, 256),
        description=f"{total} skills · Showing {start + 1}–{end} of {total}",
    )
    if max_page > 0:
        header.set_footer(text=f"Page {page + 1} / {max_page + 1}")

    embeds: list[discord.Embed] = [header]
    files: list[discord.File] = []
    for slot, index in enumerate(range(start, end), start=1):
        card, file = _build_skill_card(
            payload.results[index],
            absolute_index=index,
            tree_listing=True,
            slot=slot,
            icon_catalog=icon_catalog,
        )
        embeds.append(card)
        if file is not None:
            files.append(file)
    return SkillRenderedMessage(tuple(embeds), tuple(files), view)


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


def build_skill_detail_message(
    payload: SkillDetailPayload,
    page_index: int,
    *,
    view: discord.ui.View | None = None,
    icon_catalog: SkillIconCatalog = DEFAULT_SKILL_ICON_CATALOG,
) -> SkillRenderedMessage:
    pages = build_skill_detail_pages(payload)
    page_index = min(max(page_index, 0), len(pages) - 1)
    embed = render_skill_detail_page(
        pages[page_index],
        page_index=page_index,
        total_pages=len(pages),
    )
    file = _attach_skill_icon(
        embed,
        path=icon_catalog.resolve(payload.tree.name, payload.skill.name),
        slot=0,
    )
    return SkillRenderedMessage(
        embeds=(embed,),
        files=(file,) if file is not None else (),
        view=view,
    )


async def _edit_skill_message(
    interaction: discord.Interaction,
    rendered: SkillRenderedMessage,
) -> None:
    await interaction.response.edit_message(
        embeds=list(rendered.embeds),
        attachments=list(rendered.files),
        view=rendered.view,
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
        view = SkillResultsView(
            sessions=self.sessions,
            key=self.key,
            generation=self.generation,
            payload=self.payload,
        )
        await _edit_skill_message(
            interaction,
            build_skill_results_message(self.payload, session.page, view=view),
        )

    async def _next(self, interaction: discord.Interaction) -> None:
        session = self.sessions.get(self.key)
        if session is None:
            return
        max_page = max((len(self.payload.results) - 1) // PAGE_SIZE, 0)
        session.page = min(session.page + 1, max_page)
        view = SkillResultsView(
            sessions=self.sessions,
            key=self.key,
            generation=self.generation,
            payload=self.payload,
        )
        await _edit_skill_message(
            interaction,
            build_skill_results_message(self.payload, session.page, view=view),
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
        view = SkillDetailView(
            sessions=self.sessions,
            key=self.key,
            generation=self.generation,
            pages=pages,
            page_index=0,
            detail_payload=detail,
            results_payload=self.payload,
        )
        await _edit_skill_message(
            interaction,
            build_skill_detail_message(detail, 0, view=view),
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
        view = SkillTreeResultsView(
            sessions=self.sessions,
            key=self.key,
            generation=self.generation,
            payload=self.payload,
        )
        await _edit_skill_message(
            interaction,
            build_skill_tree_results_message(self.payload, session.page, view=view),
        )

    async def _next(self, interaction: discord.Interaction) -> None:
        session = self.sessions.get(self.key)
        if session is None:
            return
        max_page = max((len(self.payload.results) - 1) // PAGE_SIZE, 0)
        session.page = min(session.page + 1, max_page)
        view = SkillTreeResultsView(
            sessions=self.sessions,
            key=self.key,
            generation=self.generation,
            payload=self.payload,
        )
        await _edit_skill_message(
            interaction,
            build_skill_tree_results_message(self.payload, session.page, view=view),
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
        view = SkillDetailView(
            sessions=self.sessions,
            key=self.key,
            generation=self.generation,
            pages=pages,
            page_index=0,
            detail_payload=detail,
            results_payload=self.payload,
        )
        await _edit_skill_message(
            interaction,
            build_skill_detail_message(detail, 0, view=view),
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
        detail_payload: SkillDetailPayload | None = None,
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
        self.detail_payload = detail_payload
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
            detail_payload=self.detail_payload,
            results_payload=self.results_payload,
        )

    async def _previous(self, interaction: discord.Interaction) -> None:
        next_index = max(self.page_index - 1, 0)
        view = self._replacement_view(next_index)
        if self.detail_payload is not None:
            rendered = build_skill_detail_message(
                self.detail_payload,
                next_index,
                view=view,
            )
        else:
            rendered = SkillRenderedMessage(
                embeds=(
                    render_skill_detail_page(
                        self.pages[next_index],
                        page_index=next_index,
                        total_pages=len(self.pages),
                    ),
                ),
                view=view,
            )
        await _edit_skill_message(interaction, rendered)

    async def _next(self, interaction: discord.Interaction) -> None:
        next_index = min(self.page_index + 1, len(self.pages) - 1)
        view = self._replacement_view(next_index)
        if self.detail_payload is not None:
            rendered = build_skill_detail_message(
                self.detail_payload,
                next_index,
                view=view,
            )
        else:
            rendered = SkillRenderedMessage(
                embeds=(
                    render_skill_detail_page(
                        self.pages[next_index],
                        page_index=next_index,
                        total_pages=len(self.pages),
                    ),
                ),
                view=view,
            )
        await _edit_skill_message(interaction, rendered)

    async def _back(self, interaction: discord.Interaction) -> None:
        if self.results_payload is None:
            return
        session = self.sessions.get(self.key)
        if session is None:
            return
        session.selected_index = None
        if isinstance(self.results_payload, SkillTreeResultsPayload):
            view = SkillTreeResultsView(
                sessions=self.sessions,
                key=self.key,
                generation=self.generation,
                payload=self.results_payload,
            )
            rendered = build_skill_tree_results_message(
                self.results_payload,
                session.page,
                view=view,
            )
        else:
            view = SkillResultsView(
                sessions=self.sessions,
                key=self.key,
                generation=self.generation,
                payload=self.results_payload,
            )
            rendered = build_skill_results_message(
                self.results_payload,
                session.page,
                view=view,
            )
        await _edit_skill_message(interaction, rendered)


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
        view = (
            SkillTreeResultsView(
                sessions=self.sessions,
                key=self.key,
                generation=self.generation,
                payload=payload,
            )
            if payload.results
            else None
        )
        await _edit_skill_message(
            interaction,
            build_skill_tree_results_message(payload, 0, view=view),
        )

    async def _cancel(self, interaction: discord.Interaction) -> None:
        await _edit_skill_message(
            interaction,
            SkillRenderedMessage(
                embeds=(
                    discord.Embed(
                        title="Skill tree search cancelled",
                        description="No skill tree was opened.",
                    ),
                ),
            ),
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
        view = (
            SkillTreeResultsView(
                sessions=self.sessions,
                key=self.key,
                generation=self.generation,
                payload=payload,
            )
            if payload.results
            else None
        )
        await _edit_skill_message(
            interaction,
            build_skill_tree_results_message(payload, 0, view=view),
        )


def _require_skill_database_path(database_path: Path | None) -> Path:
    if database_path is None:
        raise ValueError("database_path is required for interactive skill-tree choices")
    return Path(database_path)


def build_skill_payload_message(
    payload: SkillPayload,
    *,
    bot_example_prefix: str,
    sessions: DiscordSessionManager,
    key: SessionKey,
    generation: int,
    database_path: Path | None = None,
) -> SkillRenderedMessage:
    if isinstance(payload, SkillHelpPayload):
        return SkillRenderedMessage(
            embeds=(
                build_skill_help_embed(
                    payload,
                    bot_example_prefix=bot_example_prefix,
                ),
            ),
        )
    if isinstance(payload, SkillUnavailablePayload):
        return SkillRenderedMessage(
            embeds=(
                discord.Embed(
                    title="Skill search unavailable",
                    description=truncate_discord_text(payload.text, 4096),
                ),
            ),
        )
    if isinstance(payload, SkillTreeHelpPayload):
        return SkillRenderedMessage(
            embeds=(
                build_skill_tree_help_embed(
                    payload,
                    bot_example_prefix=bot_example_prefix,
                ),
            ),
        )
    if isinstance(payload, SkillTreeNotFoundPayload):
        return SkillRenderedMessage(embeds=(build_skill_tree_not_found_embed(payload),))
    if isinstance(payload, SkillTreeConfirmationPayload):
        return SkillRenderedMessage(
            embeds=(build_skill_tree_confirmation_embed(payload),),
            view=SkillTreeConfirmationView(
                sessions=sessions,
                key=key,
                generation=generation,
                payload=payload,
                database_path=_require_skill_database_path(database_path),
            ),
        )
    if isinstance(payload, SkillTreeChoicesPayload):
        return SkillRenderedMessage(
            embeds=(build_skill_tree_choices_embed(payload),),
            view=SkillTreeChoicesView(
                sessions=sessions,
                key=key,
                generation=generation,
                payload=payload,
                database_path=_require_skill_database_path(database_path),
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
        return build_skill_tree_results_message(payload, page, view=view)
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
                detail_payload=payload,
                results_payload=None,
            )
        return build_skill_detail_message(payload, 0, view=view)

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
    return build_skill_results_message(payload, page, view=view)


__all__ = [
    "SkillDetailView",
    "SkillRenderedMessage",
    "SkillResultsView",
    "SkillTreeChoicesView",
    "SkillTreeConfirmationView",
    "SkillTreeResultsView",
    "build_skill_detail_embed",
    "build_skill_detail_message",
    "build_skill_help_embed",
    "build_skill_payload_message",
    "build_skill_results_embed",
    "build_skill_results_message",
    "build_skill_tree_help_embed",
    "build_skill_tree_not_found_embed",
    "build_skill_tree_results_embed",
    "build_skill_tree_results_message",
    "render_skill_detail_page",
    "run_skill_query_sync",
]
