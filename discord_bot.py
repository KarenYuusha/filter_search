from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import discord
from dotenv import load_dotenv

import search_items as core
from toram_search.fallback import SearchIntentRequest
from toram_search.llm import OllamaQwenClient
from toram_search.service import (
    ExpressionResultsPayload,
    GuidedStatPayload,
    ItemDetailPayload,
    ItemResultsPayload,
    SearchPayload,
    SearchService,
    ServiceOutcome,
    StatClarificationPayload,
    StatResultsPayload,
    UpgradeDetailPayload,
    UpgradeResultsPayload,
    format_search_request,
    item_id_from_payload,
)
from toram_search.session import FailedQueryContext


logger = logging.getLogger(__name__)
PAGE_SIZE = 5
VIEW_TIMEOUT_SECONDS = 900
PROJECT_ROOT = Path(__file__).resolve().parent

SessionKey = tuple[int, int, int]


@dataclass(frozen=True)
class DiscordBotConfig:
    token: str
    guild_ids: frozenset[int]
    database_path: Path = core.DEFAULT_DATABASE

    @property
    def guild_id(self) -> int:
        """Legacy single-guild accessor kept for compatibility."""
        if len(self.guild_ids) != 1:
            raise RuntimeError("guild_id is only available when exactly one guild is configured")
        return next(iter(self.guild_ids))


@dataclass
class DiscordSearchSession:
    generation: int
    query: str
    failed_context: FailedQueryContext
    page: int = 0
    payload: SearchPayload | None = None
    detail_payload: ItemDetailPayload | None = None
    resolution_choices: dict[tuple[int, int], str] = field(default_factory=dict)
    selected_index: int | None = None
    image_index: int = 0
    pending_requests: tuple[SearchIntentRequest, ...] = ()
    selected_request_index: int = 0


class DiscordSessionManager:
    def __init__(self) -> None:
        self._sessions: dict[SessionKey, DiscordSearchSession] = {}

    @staticmethod
    def _clone_failed_context(previous: FailedQueryContext | None) -> FailedQueryContext:
        cloned = FailedQueryContext(max_entries=3)
        if previous is None:
            return cloned
        for attempt in previous.snapshot():
            cloned.record_failure(attempt.original_query)
            if attempt.suggested_query:
                cloned.set_latest_suggestion(attempt.suggested_query)
        return cloned

    def start_query(self, key: SessionKey, query: str) -> DiscordSearchSession:
        previous = self._sessions.get(key)
        generation = 1 if previous is None else previous.generation + 1
        session = DiscordSearchSession(
            generation=generation,
            query=query,
            failed_context=self._clone_failed_context(
                previous.failed_context if previous is not None else None
            ),
        )
        self._sessions[key] = session
        return session

    def get(self, key: SessionKey) -> DiscordSearchSession | None:
        return self._sessions.get(key)

    def is_current(self, key: SessionKey, generation: int) -> bool:
        session = self._sessions.get(key)
        return session is not None and session.generation == generation


def load_project_environment(env_path: Path | None = None) -> Path:
    path = env_path if env_path is not None else PROJECT_ROOT / ".env"
    load_dotenv(dotenv_path=path, override=False)
    return path


def load_config(environ: Mapping[str, str] = os.environ) -> DiscordBotConfig:
    token = environ.get("DISCORD_BOT_TOKEN", "").strip()
    plural_guild_text = environ.get("DISCORD_GUILD_IDS", "").strip()
    legacy_guild_text = environ.get("DISCORD_GUILD_ID", "").strip()
    guild_setting = "DISCORD_GUILD_IDS" if plural_guild_text else "DISCORD_GUILD_ID"
    guild_text = plural_guild_text or legacy_guild_text

    if not token:
        raise RuntimeError("DISCORD_BOT_TOKEN is required")
    if not guild_text:
        raise RuntimeError("DISCORD_GUILD_IDS or DISCORD_GUILD_ID is required")

    guild_parts = [part.strip() for part in guild_text.split(",")]
    if any(not part.isdigit() for part in guild_parts):
        raise RuntimeError(f"{guild_setting} must contain valid Discord server IDs")
    guild_ids = frozenset(int(part) for part in guild_parts)
    if not guild_ids:
        raise RuntimeError(f"{guild_setting} must contain at least one Discord server ID")
    return DiscordBotConfig(token=token, guild_ids=guild_ids)


def build_intents() -> discord.Intents:
    intents = discord.Intents.none()
    intents.guilds = True
    intents.guild_messages = True
    return intents


def is_allowed_message(
    message,
    *,
    bot_user_id: int,
    guild_ids: Iterable[int] | None = None,
    guild_id: int | None = None,
) -> bool:
    allowed_guild_ids = guild_ids if guild_ids is not None else (() if guild_id is None else (guild_id,))
    return (
        message.guild is not None
        and message.guild.id in allowed_guild_ids
        and not getattr(message.author, "bot", False)
        and getattr(message, "webhook_id", None) is None
        and any(user.id == bot_user_id for user in getattr(message, "mentions", ()))
    )


def extract_mentioned_query(content: str, bot_user_id: int) -> str:
    cleaned = re.sub(rf"<@!?{bot_user_id}>", " ", content)
    return " ".join(cleaned.split())


def bot_example_prefix(guild, bot_user) -> str:
    member = None
    get_member = getattr(guild, "get_member", None) if guild is not None else None
    if bot_user is not None and callable(get_member):
        member = get_member(bot_user.id)
    display_name = (
        getattr(member, "display_name", None)
        or getattr(bot_user, "display_name", None)
        or getattr(bot_user, "name", None)
        or "Bot"
    )
    return f"@{display_name}"


def truncate_discord_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit <= 1:
        return "…"[:limit]
    return text[: limit - 1].rstrip() + "…"


def visible_attachment_name(item_name: str, image_index: int, suffix: str) -> str:
    stem = re.sub(r"[^a-z0-9]+", "-", item_name.casefold()).strip("-") or "item"
    safe_suffix = (
        suffix.lower()
        if suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
        else ".jpg"
    )
    return f"{stem}-{image_index + 1}{safe_suffix}"


def valid_local_image_paths(
    database_path: Path,
    images: Iterable[dict[str, object]],
) -> tuple[Path, ...]:
    output: list[Path] = []
    for image in images:
        local_path = image.get("local_path")
        if not local_path:
            continue
        path = Path(str(local_path))
        if path.is_absolute():
            resolved_path = path
        elif path.parts and path.parts[0].casefold() == "appearance":
            resolved_path = (database_path.parent.parent / path).resolve()
        else:
            resolved_path = (database_path.parent / path).resolve()
        if resolved_path.is_file():
            output.append(resolved_path)
    return tuple(output)


def _format_number(value: object, *, signed: bool = False) -> str:
    return core._format_number(value, signed=signed)


def _condition_label(value: object) -> str | None:
    return core._condition_label(value)


def _filter_label(parsed: core.ParsedSearch) -> str:
    return parsed.filter.label if parsed.filter else "All item types"


def _safe_field(embed: discord.Embed, name: str, value: str, *, inline: bool = False) -> None:
    cleaned = value.strip() or "None"
    embed.add_field(
        name=truncate_discord_text(name, 256),
        value=truncate_discord_text(cleaned, 1024),
        inline=inline,
    )


def _result_count(payload: SearchPayload) -> int:
    if isinstance(
        payload,
        (ItemResultsPayload, UpgradeResultsPayload, StatResultsPayload, ExpressionResultsPayload),
    ):
        return len(payload.results)
    return 0


def _result_item(payload: SearchPayload, index: int):
    if isinstance(
        payload,
        (ItemResultsPayload, UpgradeResultsPayload, StatResultsPayload, ExpressionResultsPayload),
    ):
        if 0 <= index < len(payload.results):
            return payload.results[index].item
    return None


def is_upgrade_suggestion_payload(payload: SearchPayload) -> bool:
    return (
        isinstance(payload, UpgradeResultsPayload)
        and bool(payload.results)
        and not all(result.match_kind == "upgrade" for result in payload.results)
    )


def _result_lines(payload: SearchPayload, index: int) -> list[str]:
    if isinstance(payload, ItemResultsPayload):
        result = payload.results[index]
        return [f"**{result.item.name}** — {result.item.item_type}"]

    if isinstance(payload, UpgradeResultsPayload):
        result = payload.results[index]
        return [f"**{result.item.name}** — {result.item.item_type}"]

    if isinstance(payload, StatResultsPayload):
        result = payload.results[index]
        parsed = payload.parsed
        stat_name = parsed.stat.stat_name if parsed.stat else result.primary.stat_name
        lines = [
            f"**{result.item.name}** — {result.item.item_type}",
            f"{stat_name} {_format_number(result.primary.amount, signed=True)}",
        ]
        condition = _condition_label(result.primary)
        if condition:
            lines.append(f"Condition: {condition}")
        return lines

    if isinstance(payload, ExpressionResultsPayload):
        result = payload.results[index]
        lines = [f"**{result.item.name}** — {result.item.item_type}"]
        for match in result.matches:
            for row_index, row in enumerate(match.rows):
                prefix = "" if row_index == 0 else "Also: "
                text = (
                    f"{prefix}{match.clause.stat_name} "
                    f"{_format_number(row.amount, signed=True)}"
                )
                condition = _condition_label(row)
                if condition:
                    text += f" [{condition}]"
                lines.append(text)
        return lines

    return []


def build_search_results_embed(payload: SearchPayload, page: int) -> discord.Embed:
    total = _result_count(payload)
    max_page = max((total - 1) // PAGE_SIZE, 0)
    page = min(max(page, 0), max_page)
    start = page * PAGE_SIZE
    end = min(start + PAGE_SIZE, total)

    if isinstance(payload, ItemResultsPayload):
        title = f'Item search: {payload.query}'
        order = "Closest matches first"
    elif isinstance(payload, UpgradeResultsPayload):
        direct_upgrade_results = bool(payload.results) and all(
            result.match_kind == "upgrade" for result in payload.results
        )
        if direct_upgrade_results:
            title = f"Upgrades from {payload.query}"
            order = "Direct upgrades"
        else:
            title = f'Upgrade search: {payload.query}'
            order = "Closest matches first"
    elif isinstance(payload, StatResultsPayload):
        stat_name = payload.parsed.stat.stat_name if payload.parsed.stat else "Stat"
        title = f"{stat_name} — {_filter_label(payload.parsed)}"
        order = "Highest first"
    elif isinstance(payload, ExpressionResultsPayload):
        parsed = payload.parsed
        if parsed.resolved_expression is not None:
            expression = core.format_resolved_expression(parsed.resolved_expression)
            primary = parsed.resolved_expression.groups[0].clauses[0].stat_name
        else:
            expression = parsed.raw_query
            primary = "primary stat"
        title = f"Stat search — {_filter_label(parsed)}"
        order = f"{primary} {'lowest' if parsed.primary_sort_ascending else 'highest'} first"
    else:
        return discord.Embed(title="Search results")

    if total == 0:
        embed = discord.Embed(title="No results")
        if isinstance(payload, StatResultsPayload) and payload.parsed.stat is not None:
            embed.description = (
                "No matching items were found.\n\n"
                f"Item type: {_filter_label(payload.parsed)}\n"
                f"Stat: {payload.parsed.stat.stat_name}"
            )
        elif isinstance(payload, ExpressionResultsPayload):
            embed.description = (
                "No matching items were found.\n\n"
                f"Item type: {_filter_label(payload.parsed)}\n"
                f"Query: {payload.parsed.raw_query}"
            )
        elif isinstance(payload, UpgradeResultsPayload):
            embed.description = f"No direct upgrade crystas were found for **{payload.query}**."
        else:
            embed.description = "No matching items were found."
        return embed

    description_lines = [order, f"Showing {start + 1}–{end} of {total}", ""]
    if isinstance(payload, ExpressionResultsPayload):
        if payload.parsed.resolved_expression is not None:
            description_lines.insert(
                1,
                core.format_resolved_expression(payload.parsed.resolved_expression),
            )
    for result_index in range(start, end):
        description_lines.append(f"{result_index + 1}. " + _result_lines(payload, result_index)[0])
        for extra in _result_lines(payload, result_index)[1:]:
            description_lines.append(f"   {extra}")
        description_lines.append("")
    return discord.Embed(
        title=truncate_discord_text(title, 256),
        description=truncate_discord_text("\n".join(description_lines).rstrip(), 4096),
    )


def build_upgrade_detail_embed(payload: UpgradeDetailPayload) -> discord.Embed:
    display = core.build_upgrade_display(payload.graph, payload.selected_item_id)
    embed = discord.Embed(
        title=truncate_discord_text(f"Upgrade Tree — {display.selected_name}", 256)
    )
    _safe_field(
        embed,
        "Selected paths",
        "\n".join(display.selected_paths),
    )
    _safe_field(
        embed,
        "Full tree",
        "```text\n" + "\n".join(display.tree_lines) + "\n```",
    )
    return embed


def build_item_detail_embed(
    detail: core.ItemDetail,
    *,
    image_count: int = 0,
    image_index: int = 0,
    attachment_name: str | None = None,
) -> discord.Embed:
    embed = discord.Embed(
        title=truncate_discord_text(detail.summary.name, 256),
        description=truncate_discord_text(detail.summary.item_type, 4096),
    )

    overview: list[str] = []
    if detail.sell_price is not None:
        overview.append(f"Sell price: {_format_number(detail.sell_price)}")
    if detail.process_material or detail.process_amount is not None:
        overview.append(
            f"Process: {detail.process_material or 'Unknown'} × "
            f"{_format_number(detail.process_amount)}"
        )
    if detail.badge:
        overview.append(f"Badge: {detail.badge}")
    if overview:
        _safe_field(embed, "Info", "\n".join(overview))

    stat_lines: list[str] = []
    for stat in detail.stats:
        line = (
            f"{stat.get('stat_name') or 'Unknown stat'} "
            f"{_format_number(stat.get('amount'), signed=True)}"
        )
        condition = _condition_label(stat)
        if condition:
            line += f" [{condition}]"
        stat_lines.append(line)
    _safe_field(embed, "Stats", "\n".join(stat_lines) if stat_lines else "None")

    source_lines: list[str] = []
    for source in detail.sources:
        source_name = str(source.get("source_name") or "Unknown")
        details: list[str] = []
        level = source.get("level")
        if level is not None:
            level_text = _format_number(level)
            level_pattern = rf"\blv\.?\s*{re.escape(level_text)}(?:\b|(?=\)))"
            if re.search(level_pattern, source_name, flags=re.IGNORECASE) is None:
                details.append(f"Lv {level_text}")
        if source.get("map"):
            details.append(str(source["map"]))
        if source.get("dye"):
            details.append(f"Dye: {source['dye']}")
        source_lines.append(
            source_name + (" — " + " — ".join(details) if details else "")
        )
    _safe_field(
        embed,
        "Obtained From",
        "\n".join(source_lines) if source_lines else "Unknown",
    )

    upgrade_lines: list[str] = []
    upgrade_lines.extend(f"Previous: {item.name}" for item in detail.upgrade_predecessors)
    upgrade_lines.extend(f"Next: {item.name}" for item in detail.upgrade_successors)
    if upgrade_lines:
        _safe_field(embed, "Upgrade", "\n".join(upgrade_lines))

    if detail.note:
        _safe_field(embed, "Notes", str(detail.note))

    if attachment_name is not None:
        embed.set_image(url=f"attachment://{attachment_name}")
    if image_count:
        embed.set_footer(text=f"Image {image_index + 1} of {image_count}")
    return embed


def build_help_embed(bot_example_prefix: str) -> discord.Embed:
    embed = discord.Embed(
        title="Toram Item Search",
        description=(
            "Search explicit item names, item types, and stats. "
            "Build-role recommendations such as tank/DPS evaluation are not supported yet."
        ),
    )
    _safe_field(
        embed,
        "Examples",
        "\n".join(
            [
                f"{bot_example_prefix} hp armor",
                f"{bot_example_prefix} find armor with hp",
                f"{bot_example_prefix} hp > 5000 and cr bow",
                f"{bot_example_prefix} item Rapier",
                f"{bot_example_prefix} upgrade <crysta name>",
                f"{bot_example_prefix} what upgrades from Don",
            ]
        ),
    )
    return embed


def _build_text_embed(title: str, text: str) -> discord.Embed:
    return discord.Embed(
        title=truncate_discord_text(title, 256),
        description=truncate_discord_text(text, 4096),
    )


def run_query_sync(
    database_path: Path,
    query: str,
    context: FailedQueryContext,
    *,
    repository_factory=core.ItemRepository,
    llm_client_factory=OllamaQwenClient,
) -> ServiceOutcome:
    repository = repository_factory(database_path.resolve())
    try:
        service = SearchService(repository, llm_client=llm_client_factory())
        return service.handle_query(query, context)
    finally:
        repository.close()


def run_confirmed_request_sync(
    database_path: Path,
    request: SearchIntentRequest,
    raw_query: str,
    context: FailedQueryContext,
    *,
    repository_factory=core.ItemRepository,
) -> ServiceOutcome:
    repository = repository_factory(database_path.resolve())
    try:
        service = SearchService(repository)
        return service.confirm_search_request(request, raw_query, context)
    finally:
        repository.close()


def run_clarification_sync(
    database_path: Path,
    parsed: core.ParsedSearch,
    choices: Mapping[tuple[int, int], str],
    *,
    repository_factory=core.ItemRepository,
) -> ServiceOutcome:
    repository = repository_factory(database_path.resolve())
    try:
        service = SearchService(repository)
        return service.continue_clarification(parsed, choices)
    finally:
        repository.close()


def run_item_detail_sync(
    database_path: Path,
    item_id: int,
    *,
    repository_factory=core.ItemRepository,
) -> ItemDetailPayload:
    repository = repository_factory(database_path.resolve())
    try:
        return ItemDetailPayload(repository.get_item(item_id))
    finally:
        repository.close()


def run_upgrade_selection_sync(
    database_path: Path,
    item_id: int,
    item_name: str,
    *,
    repository_factory=core.ItemRepository,
) -> ServiceOutcome:
    repository = repository_factory(database_path.resolve())
    try:
        return SearchService(repository).continue_upgrade_selection(item_id, item_name)
    finally:
        repository.close()


async def send_if_current(
    sessions: DiscordSessionManager,
    key: SessionKey,
    generation: int,
    value: object,
    send_callback,
) -> None:
    if sessions.is_current(key, generation):
        await send_callback(value)


class SessionBoundView(discord.ui.View):
    def __init__(
        self,
        *,
        sessions: DiscordSessionManager,
        key: SessionKey,
        generation: int,
        owner_id: int,
        timeout: float = VIEW_TIMEOUT_SECONDS,
    ) -> None:
        super().__init__(timeout=timeout)
        self.sessions = sessions
        self.key = key
        self.generation = generation
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Only the person who started this search can use these controls.",
                ephemeral=True,
            )
            return False
        if not self.sessions.is_current(self.key, self.generation):
            await interaction.response.send_message(
                "This search is no longer active. Use the controls on your latest search.",
                ephemeral=True,
            )
            return False
        return True


class ActionButton(discord.ui.Button):
    def __init__(self, *, handler, **kwargs) -> None:
        super().__init__(**kwargs)
        self._handler = handler

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._handler(interaction)


class ActionSelect(discord.ui.Select):
    def __init__(self, *, handler, **kwargs) -> None:
        super().__init__(**kwargs)
        self._handler = handler

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._handler(interaction, self.values)


async def _edit_component_message(
    interaction: discord.Interaction,
    *,
    embed: discord.Embed,
    view: discord.ui.View | None,
    file: discord.File | None = None,
) -> None:
    attachments: list[discord.File] = [] if file is None else [file]
    if interaction.response.is_done():
        await interaction.edit_original_response(
            embed=embed,
            view=view,
            attachments=attachments,
        )
    else:
        await interaction.response.edit_message(
            embed=embed,
            view=view,
            attachments=attachments,
        )


class SearchResultsView(SessionBoundView):
    def __init__(
        self,
        *,
        sessions: DiscordSessionManager,
        key: SessionKey,
        generation: int,
        database_path: Path,
        payload: SearchPayload,
    ) -> None:
        super().__init__(
            sessions=sessions,
            key=key,
            generation=generation,
            owner_id=key[2],
        )
        self.database_path = database_path
        self.payload = payload
        session = sessions.get(key)
        page = session.page if session is not None else 0
        total = _result_count(payload)
        max_page = max((total - 1) // PAGE_SIZE, 0)
        page = min(max(page, 0), max_page)
        if session is not None:
            session.page = page
        start = page * PAGE_SIZE
        end = min(start + PAGE_SIZE, total)

        options: list[discord.SelectOption] = []
        for index in range(start, end):
            item = _result_item(payload, index)
            if item is None:
                continue
            options.append(
                discord.SelectOption(
                    label=truncate_discord_text(item.name, 100),
                    value=str(index),
                    description=truncate_discord_text(item.item_type, 100),
                )
            )
        if options:
            self.item_select = ActionSelect(
                placeholder="Select an item",
                min_values=1,
                max_values=1,
                options=options,
                handler=self._select_item,
                row=0,
            )
            self.add_item(self.item_select)
        else:
            self.item_select = None

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
        view = SearchResultsView(
            sessions=self.sessions,
            key=self.key,
            generation=self.generation,
            database_path=self.database_path,
            payload=self.payload,
        )
        await interaction.response.edit_message(
            embed=build_search_results_embed(self.payload, session.page),
            view=view,
        )

    async def _next(self, interaction: discord.Interaction) -> None:
        session = self.sessions.get(self.key)
        if session is None:
            return
        max_page = max((_result_count(self.payload) - 1) // PAGE_SIZE, 0)
        session.page = min(session.page + 1, max_page)
        view = SearchResultsView(
            sessions=self.sessions,
            key=self.key,
            generation=self.generation,
            database_path=self.database_path,
            payload=self.payload,
        )
        await interaction.response.edit_message(
            embed=build_search_results_embed(self.payload, session.page),
            view=view,
        )

    async def _select_item(
        self,
        interaction: discord.Interaction,
        values: Sequence[str],
    ) -> None:
        if not values or not values[0].isdigit():
            await interaction.response.send_message("Invalid item selection.", ephemeral=True)
            return
        result_index = int(values[0])
        item_id = item_id_from_payload(self.payload, result_index)
        if item_id is None:
            await interaction.response.send_message("Invalid item selection.", ephemeral=True)
            return
        if is_upgrade_suggestion_payload(self.payload):
            item = _result_item(self.payload, result_index)
            if item is None:
                await interaction.response.send_message("Invalid item selection.", ephemeral=True)
                return
            await interaction.response.defer()
            outcome = await asyncio.to_thread(
                run_upgrade_selection_sync,
                self.database_path,
                item.id,
                item.name,
            )
            if not self.sessions.is_current(self.key, self.generation):
                return
            session = self.sessions.get(self.key)
            if session is None:
                return
            session.selected_index = result_index
            session.page = 0
            session.detail_payload = None
            session.image_index = 0
            await edit_service_outcome(
                interaction,
                outcome,
                sessions=self.sessions,
                key=self.key,
                generation=self.generation,
                database_path=self.database_path,
            )
            return
        await interaction.response.defer()
        detail_payload = await asyncio.to_thread(
            run_item_detail_sync,
            self.database_path,
            item_id,
        )
        if not self.sessions.is_current(self.key, self.generation):
            return
        session = self.sessions.get(self.key)
        if session is None:
            return
        session.selected_index = result_index
        session.detail_payload = detail_payload
        session.image_index = 0
        embed, file, view = build_item_detail_message(
            detail_payload,
            database_path=self.database_path,
            sessions=self.sessions,
            key=self.key,
            generation=self.generation,
            allow_back=True,
        )
        await _edit_component_message(
            interaction,
            embed=embed,
            view=view,
            file=file,
        )


class ItemDetailView(SessionBoundView):
    def __init__(
        self,
        *,
        sessions: DiscordSessionManager,
        key: SessionKey,
        generation: int,
        database_path: Path,
        detail_payload: ItemDetailPayload,
        image_paths: tuple[Path, ...],
        allow_back: bool,
    ) -> None:
        super().__init__(
            sessions=sessions,
            key=key,
            generation=generation,
            owner_id=key[2],
        )
        self.database_path = database_path
        self.detail_payload = detail_payload
        self.image_paths = image_paths
        self.allow_back = allow_back
        session = sessions.get(key)
        image_index = session.image_index if session is not None else 0

        if len(image_paths) > 1:
            self.add_item(
                ActionButton(
                    label="Previous Image",
                    style=discord.ButtonStyle.secondary,
                    disabled=image_index <= 0,
                    handler=self._previous_image,
                    row=0,
                )
            )
            self.add_item(
                ActionButton(
                    label="Next Image",
                    style=discord.ButtonStyle.secondary,
                    disabled=image_index >= len(image_paths) - 1,
                    handler=self._next_image,
                    row=0,
                )
            )
        if allow_back:
            self.add_item(
                ActionButton(
                    label="Back to Results",
                    style=discord.ButtonStyle.primary,
                    handler=self._back,
                    row=1,
                )
            )

    async def _move_image(self, interaction: discord.Interaction, delta: int) -> None:
        session = self.sessions.get(self.key)
        if session is None:
            return
        session.image_index = min(
            max(session.image_index + delta, 0),
            max(len(self.image_paths) - 1, 0),
        )
        embed, file, view = build_item_detail_message(
            self.detail_payload,
            database_path=self.database_path,
            sessions=self.sessions,
            key=self.key,
            generation=self.generation,
            allow_back=self.allow_back,
        )
        await _edit_component_message(interaction, embed=embed, view=view, file=file)

    async def _previous_image(self, interaction: discord.Interaction) -> None:
        await self._move_image(interaction, -1)

    async def _next_image(self, interaction: discord.Interaction) -> None:
        await self._move_image(interaction, 1)

    async def _back(self, interaction: discord.Interaction) -> None:
        session = self.sessions.get(self.key)
        if session is None or session.payload is None:
            return
        session.detail_payload = None
        session.selected_index = None
        session.image_index = 0
        view = SearchResultsView(
            sessions=self.sessions,
            key=self.key,
            generation=self.generation,
            database_path=self.database_path,
            payload=session.payload,
        )
        await interaction.response.edit_message(
            embed=build_search_results_embed(session.payload, session.page),
            view=view,
            attachments=[],
        )


def build_item_detail_message(
    payload: ItemDetailPayload,
    *,
    database_path: Path,
    sessions: DiscordSessionManager,
    key: SessionKey,
    generation: int,
    allow_back: bool,
) -> tuple[discord.Embed, discord.File | None, ItemDetailView]:
    detail = payload.detail
    image_paths = valid_local_image_paths(database_path, detail.images)
    session = sessions.get(key)
    image_index = session.image_index if session is not None else 0
    image_index = min(max(image_index, 0), max(len(image_paths) - 1, 0))
    if session is not None:
        session.image_index = image_index

    file: discord.File | None = None
    attachment_name: str | None = None
    if image_paths:
        image_path = image_paths[image_index]
        attachment_name = visible_attachment_name(
            detail.summary.name,
            image_index,
            image_path.suffix,
        )
        file = discord.File(image_path, filename=attachment_name)
    embed = build_item_detail_embed(
        detail,
        image_count=len(image_paths),
        image_index=image_index,
        attachment_name=attachment_name,
    )
    view = ItemDetailView(
        sessions=sessions,
        key=key,
        generation=generation,
        database_path=database_path,
        detail_payload=payload,
        image_paths=image_paths,
        allow_back=allow_back,
    )
    return embed, file, view


class StatClarificationView(SessionBoundView):
    def __init__(
        self,
        *,
        sessions: DiscordSessionManager,
        key: SessionKey,
        generation: int,
        database_path: Path,
        payload: StatClarificationPayload,
    ) -> None:
        super().__init__(
            sessions=sessions,
            key=key,
            generation=generation,
            owner_id=key[2],
        )
        self.database_path = database_path
        self.payload = payload
        clarification = payload.clarification
        if clarification.mode == "confirm":
            label = clarification.display_labels[0] if clarification.display_labels else "suggestion"
            candidate = clarification.candidates[0]
            self.add_item(
                ActionButton(
                    label=truncate_discord_text(f"Use {label}", 80),
                    style=discord.ButtonStyle.success,
                    handler=lambda interaction, value=candidate: self._choose(interaction, value),
                )
            )
        else:
            for candidate, label in zip(
                clarification.candidates,
                clarification.display_labels,
            ):
                self.add_item(
                    ActionButton(
                        label=truncate_discord_text(label, 80),
                        style=discord.ButtonStyle.primary,
                        handler=lambda interaction, value=candidate: self._choose(interaction, value),
                    )
                )
        self.add_item(
            ActionButton(
                label="Cancel",
                style=discord.ButtonStyle.secondary,
                handler=self._cancel,
            )
        )

    async def _choose(self, interaction: discord.Interaction, selected: str) -> None:
        session = self.sessions.get(self.key)
        if session is None:
            return
        clarification = self.payload.clarification
        key = (clarification.group_index, clarification.clause_index)
        choices = dict(session.resolution_choices)
        choices[key] = selected
        await interaction.response.defer()
        outcome = await asyncio.to_thread(
            run_clarification_sync,
            self.database_path,
            self.payload.parsed,
            choices,
        )
        if not self.sessions.is_current(self.key, self.generation):
            return
        session = self.sessions.get(self.key)
        if session is None:
            return
        session.resolution_choices = choices
        if outcome.kind == "search" and not isinstance(outcome.payload, StatClarificationPayload):
            session.failed_context.clear()
        await edit_service_outcome(
            interaction,
            outcome,
            sessions=self.sessions,
            key=self.key,
            generation=self.generation,
            database_path=self.database_path,
        )

    async def _cancel(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(
            embed=_build_text_embed("Search cancelled", "No search was executed."),
            view=None,
            attachments=[],
        )


def build_clarification_embed(payload: StatClarificationPayload) -> discord.Embed:
    clarification = payload.clarification
    if clarification.mode == "confirm":
        suggestion = clarification.display_labels[0] if clarification.display_labels else "the suggested stat"
        return discord.Embed(
            title="Confirm stat",
            description=f'Did you mean **{suggestion}** for "{clarification.typed_stat}"?',
        )
    labels = "\n".join(f"• {label}" for label in clarification.display_labels)
    return discord.Embed(
        title=f'What does "{clarification.typed_stat}" mean?',
        description=labels,
    )


class QwenConfirmationView(SessionBoundView):
    def __init__(
        self,
        *,
        sessions: DiscordSessionManager,
        key: SessionKey,
        generation: int,
        database_path: Path,
        requests: tuple[SearchIntentRequest, ...],
    ) -> None:
        super().__init__(
            sessions=sessions,
            key=key,
            generation=generation,
            owner_id=key[2],
        )
        self.database_path = database_path
        self.requests = requests
        session = sessions.get(key)
        selected = session.selected_request_index if session is not None else 0
        if len(requests) > 1:
            options = [
                discord.SelectOption(
                    label=truncate_discord_text(format_search_request(request), 100),
                    value=str(index),
                    default=index == selected,
                )
                for index, request in enumerate(requests)
            ]
            self.add_item(
                ActionSelect(
                    placeholder="Choose an interpretation",
                    min_values=1,
                    max_values=1,
                    options=options,
                    handler=self._select_request,
                    row=0,
                )
            )
        self.add_item(
            ActionButton(
                label="Search",
                style=discord.ButtonStyle.success,
                handler=self._search,
                row=1,
            )
        )
        self.add_item(
            ActionButton(
                label="Cancel",
                style=discord.ButtonStyle.secondary,
                handler=self._cancel,
                row=1,
            )
        )

    async def _select_request(
        self,
        interaction: discord.Interaction,
        values: Sequence[str],
    ) -> None:
        session = self.sessions.get(self.key)
        if session is None or not values or not values[0].isdigit():
            return
        index = int(values[0])
        if not (0 <= index < len(self.requests)):
            return
        session.selected_request_index = index
        view = QwenConfirmationView(
            sessions=self.sessions,
            key=self.key,
            generation=self.generation,
            database_path=self.database_path,
            requests=self.requests,
        )
        await interaction.response.edit_message(
            embed=build_qwen_confirmation_embed(self.requests, index),
            view=view,
        )

    async def _search(self, interaction: discord.Interaction) -> None:
        session = self.sessions.get(self.key)
        if session is None or not self.requests:
            return
        index = min(max(session.selected_request_index, 0), len(self.requests) - 1)
        request = self.requests[index]
        await interaction.response.defer()
        outcome = await asyncio.to_thread(
            run_confirmed_request_sync,
            self.database_path,
            request,
            session.query,
            session.failed_context,
        )
        if not self.sessions.is_current(self.key, self.generation):
            return
        session = self.sessions.get(self.key)
        if session is None:
            return
        if outcome.kind == "search" and not isinstance(outcome.payload, StatClarificationPayload):
            session.failed_context.clear()
        await edit_service_outcome(
            interaction,
            outcome,
            sessions=self.sessions,
            key=self.key,
            generation=self.generation,
            database_path=self.database_path,
        )

    async def _cancel(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(
            embed=_build_text_embed("Search cancelled", "No search was executed."),
            view=None,
            attachments=[],
        )


def build_qwen_confirmation_embed(
    requests: tuple[SearchIntentRequest, ...],
    selected_index: int,
) -> discord.Embed:
    if not requests:
        return _build_text_embed("Interpretation failed", "No valid interpretation was produced.")
    selected_index = min(max(selected_index, 0), len(requests) - 1)
    selected = requests[selected_index]
    description = format_search_request(selected)
    if len(requests) > 1:
        description += f"\n\nInterpretation {selected_index + 1} of {len(requests)}"
    return discord.Embed(
        title="Is this what you meant?",
        description=truncate_discord_text(description, 4096),
    )


def _make_search_view(
    *,
    sessions: DiscordSessionManager,
    key: SessionKey,
    generation: int,
    database_path: Path,
    payload: SearchPayload,
) -> discord.ui.View | None:
    if isinstance(
        payload,
        (ItemResultsPayload, UpgradeResultsPayload, StatResultsPayload, ExpressionResultsPayload),
    ):
        return SearchResultsView(
            sessions=sessions,
            key=key,
            generation=generation,
            database_path=database_path,
            payload=payload,
        )
    if isinstance(payload, StatClarificationPayload):
        return StatClarificationView(
            sessions=sessions,
            key=key,
            generation=generation,
            database_path=database_path,
            payload=payload,
        )
    return None


def build_service_outcome_message(
    outcome: ServiceOutcome,
    *,
    bot_example_prefix: str,
    sessions: DiscordSessionManager,
    key: SessionKey,
    generation: int,
    database_path: Path,
) -> tuple[discord.Embed, discord.ui.View | None, discord.File | None]:
    session = sessions.get(key)

    if outcome.kind == "help":
        if outcome.text:
            return _build_text_embed("Search help", outcome.text), None, None
        return build_help_embed(bot_example_prefix), None, None

    if outcome.kind == "database":
        return _build_text_embed("Database", outcome.text or "No answer available."), None, None

    if outcome.kind == "refuse":
        return (
            _build_text_embed(
                "Unsupported request",
                "I can search explicit item types and stats, but I can't evaluate tank/DPS/build roles yet. "
                f"Try `{bot_example_prefix} hp armor` or `{bot_example_prefix} physical resistance xtal`.",
            ),
            None,
            None,
        )

    if outcome.kind == "unavailable":
        return (
            _build_text_embed(
                "Automatic interpretation unavailable",
                "I couldn't interpret that automatically right now. Explicit item/stat searches are still available.",
            ),
            None,
            None,
        )

    if outcome.kind == "failed":
        return (
            _build_text_embed(
                "I couldn't interpret that search",
                "Try an explicit item/stat query, for example:\n"
                f"• `{bot_example_prefix} hp armor`\n"
                f"• `{bot_example_prefix} cr bow`\n"
                f"• `{bot_example_prefix} hp > 5000 and cr bow`",
            ),
            None,
            None,
        )

    if outcome.kind == "confirm_search":
        requests = outcome.search_requests
        if session is not None:
            session.pending_requests = requests
            session.selected_request_index = 0
        return (
            build_qwen_confirmation_embed(requests, 0),
            QwenConfirmationView(
                sessions=sessions,
                key=key,
                generation=generation,
                database_path=database_path,
                requests=requests,
            ),
            None,
        )

    payload = outcome.payload
    if payload is None:
        return _build_text_embed("Search failed", "No search result was produced."), None, None

    if session is not None:
        if isinstance(payload, ItemDetailPayload):
            session.detail_payload = payload
        else:
            session.payload = payload

    if isinstance(payload, ItemDetailPayload):
        embed, file, view = build_item_detail_message(
            payload,
            database_path=database_path,
            sessions=sessions,
            key=key,
            generation=generation,
            allow_back=False,
        )
        return embed, view, file

    if isinstance(payload, UpgradeDetailPayload):
        return build_upgrade_detail_embed(payload), None, None

    if isinstance(payload, GuidedStatPayload):
        return _build_text_embed("Search", payload.message), None, None

    if isinstance(payload, StatClarificationPayload):
        return (
            build_clarification_embed(payload),
            StatClarificationView(
                sessions=sessions,
                key=key,
                generation=generation,
                database_path=database_path,
                payload=payload,
            ),
            None,
        )

    return (
        build_search_results_embed(payload, session.page if session is not None else 0),
        _make_search_view(
            sessions=sessions,
            key=key,
            generation=generation,
            database_path=database_path,
            payload=payload,
        ),
        None,
    )


async def edit_service_outcome(
    interaction: discord.Interaction,
    outcome: ServiceOutcome,
    *,
    sessions: DiscordSessionManager,
    key: SessionKey,
    generation: int,
    database_path: Path,
) -> None:
    prefix = bot_example_prefix(interaction.guild, interaction.client.user)
    embed, view, file = build_service_outcome_message(
        outcome,
        bot_example_prefix=prefix,
        sessions=sessions,
        key=key,
        generation=generation,
        database_path=database_path,
    )
    await _edit_component_message(interaction, embed=embed, view=view, file=file)


async def process_tagged_query(
    message: discord.Message,
    *,
    bot_user,
    config: DiscordBotConfig,
    sessions: DiscordSessionManager,
) -> None:
    bot_user_id = bot_user.id
    query = extract_mentioned_query(message.content, bot_user_id)
    if not query:
        query = "help"
    key: SessionKey = (message.guild.id, message.channel.id, message.author.id)
    session = sessions.start_query(key, query)

    outcome = await asyncio.to_thread(
        run_query_sync,
        config.database_path,
        query,
        session.failed_context,
    )
    if not sessions.is_current(key, session.generation):
        return
    session = sessions.get(key)
    if session is None:
        return
    if outcome.kind == "search" and not isinstance(outcome.payload, StatClarificationPayload):
        session.failed_context.clear()
    embed, view, file = build_service_outcome_message(
        outcome,
        bot_example_prefix=bot_example_prefix(message.guild, bot_user),
        sessions=sessions,
        key=key,
        generation=session.generation,
        database_path=config.database_path,
    )
    kwargs = {
        "embed": embed,
        "view": view,
        "mention_author": False,
        "allowed_mentions": discord.AllowedMentions.none(),
    }
    if file is not None:
        kwargs["file"] = file
    await message.reply(**kwargs)


def create_client(config: DiscordBotConfig) -> discord.Client:
    client = discord.Client(
        intents=build_intents(),
        allowed_mentions=discord.AllowedMentions.none(),
    )
    sessions = DiscordSessionManager()

    @client.event
    async def on_ready() -> None:
        logger.info(
            "Discord bot connected as %s for guilds %s",
            client.user,
            ", ".join(str(guild_id) for guild_id in sorted(config.guild_ids)),
        )

    @client.event
    async def on_message(message: discord.Message) -> None:
        if client.user is None:
            return
        if not is_allowed_message(
            message,
            bot_user_id=client.user.id,
            guild_ids=config.guild_ids,
        ):
            return
        try:
            await process_tagged_query(
                message,
                bot_user=client.user,
                config=config,
                sessions=sessions,
            )
        except Exception:
            logger.exception("Discord search failed")
            try:
                await message.reply(
                    "The search failed due to an internal error.",
                    mention_author=False,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except Exception:
                logger.exception("Failed to send Discord error response")

    return client


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    load_project_environment()
    config = load_config()
    client = create_client(config)
    client.run(config.token, log_handler=None)


if __name__ == "__main__":
    main()
