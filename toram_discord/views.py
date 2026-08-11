from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Mapping, Sequence

import discord

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
from toram_search.session import FailedQueryContext, PendingItemSearch

from toram_discord.config import bot_example_prefix
from toram_discord.render import (
    PAGE_SIZE,
    _build_text_embed,
    _result_count,
    _result_item,
    build_clarification_embed,
    build_help_embed,
    build_item_detail_embed,
    build_item_understanding_embed,
    build_qwen_confirmation_embed,
    build_search_results_embed,
    build_upgrade_detail_embed,
    is_upgrade_suggestion_payload,
    truncate_discord_text,
    valid_local_image_paths,
    visible_attachment_name,
)
from toram_discord.sessions import DiscordSessionManager, SessionKey


VIEW_TIMEOUT_SECONDS = 900


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

def run_item_understanding_choice_sync(
    database_path: Path,
    pending: PendingItemSearch,
    issue_id: str,
    selected_value: str,
    context: FailedQueryContext,
    *,
    repository_factory=core.ItemRepository,
) -> ServiceOutcome:
    repository = repository_factory(database_path.resolve())
    try:
        return SearchService(repository).continue_item_understanding(
            pending,
            issue_id,
            selected_value,
            context,
        )
    finally:
        repository.close()

def run_pending_item_search_confirmation_sync(
    database_path: Path,
    pending: PendingItemSearch,
    context: FailedQueryContext,
    *,
    repository_factory=core.ItemRepository,
) -> ServiceOutcome:
    repository = repository_factory(database_path.resolve())
    try:
        return SearchService(repository).confirm_pending_item_search(
            pending,
            context,
        )
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

class ItemUnderstandingView(SessionBoundView):
    def __init__(
        self,
        *,
        sessions: DiscordSessionManager,
        key: SessionKey,
        generation: int,
        database_path: Path,
        pending: PendingItemSearch,
    ) -> None:
        super().__init__(
            sessions=sessions,
            key=key,
            generation=generation,
            owner_id=key[2],
        )
        self.database_path = database_path
        self.pending = pending
        understanding = pending.understanding

        if understanding.uncertainties:
            issue = understanding.uncertainties[0]
            if issue.mode == "choose":
                for choice in issue.choices:
                    self.add_item(
                        ActionButton(
                            label=truncate_discord_text(choice.value, 80),
                            style=discord.ButtonStyle.primary,
                            handler=lambda interaction, value=choice.value: self._choose(
                                interaction, value
                            ),
                        )
                    )
                self.add_item(
                    ActionButton(
                        label="Cancel",
                        style=discord.ButtonStyle.secondary,
                        handler=self._cancel,
                    )
                )
            else:
                choice = issue.choices[0]
                self.add_item(
                    ActionButton(
                        label=truncate_discord_text(f"Use {choice.value}", 80),
                        style=discord.ButtonStyle.success,
                        handler=lambda interaction, value=choice.value: self._choose(
                            interaction, value
                        ),
                    )
                )
                self.add_item(
                    ActionButton(
                        label="No",
                        style=discord.ButtonStyle.secondary,
                        handler=self._cancel,
                    )
                )
        elif understanding.decision == "suggest":
            self.add_item(
                ActionButton(
                    label="Use suggestion",
                    style=discord.ButtonStyle.success,
                    handler=self._confirm_pending,
                )
            )
            self.add_item(
                ActionButton(
                    label="Cancel",
                    style=discord.ButtonStyle.secondary,
                    handler=self._cancel,
                )
            )
        elif understanding.decision == "confirm":
            self.add_item(
                ActionButton(
                    label="Search",
                    style=discord.ButtonStyle.success,
                    handler=self._confirm_pending,
                )
            )
            self.add_item(
                ActionButton(
                    label="Edit",
                    style=discord.ButtonStyle.secondary,
                    handler=self._edit,
                )
            )

    async def _choose(self, interaction: discord.Interaction, selected: str) -> None:
        session = self.sessions.get(self.key)
        if session is None or not self.pending.understanding.uncertainties:
            return
        issue = self.pending.understanding.uncertainties[0]
        await interaction.response.defer()
        outcome = await asyncio.to_thread(
            run_item_understanding_choice_sync,
            self.database_path,
            self.pending,
            issue.issue_id,
            selected,
            session.failed_context,
        )
        if not self.sessions.is_current(self.key, self.generation):
            return
        session = self.sessions.get(self.key)
        if session is None:
            return
        if outcome.kind == "search":
            session.pending_item_search = None
            if not isinstance(outcome.payload, StatClarificationPayload):
                session.failed_context.clear()
        elif outcome.kind == "item_understanding":
            session.pending_item_search = outcome.pending_item_search
        await edit_service_outcome(
            interaction,
            outcome,
            sessions=self.sessions,
            key=self.key,
            generation=self.generation,
            database_path=self.database_path,
        )

    async def _confirm_pending(self, interaction: discord.Interaction) -> None:
        session = self.sessions.get(self.key)
        if session is None:
            return
        await interaction.response.defer()
        outcome = await asyncio.to_thread(
            run_pending_item_search_confirmation_sync,
            self.database_path,
            self.pending,
            session.failed_context,
        )
        if not self.sessions.is_current(self.key, self.generation):
            return
        session = self.sessions.get(self.key)
        if session is None:
            return
        if outcome.kind == "search":
            session.pending_item_search = None
            if not isinstance(outcome.payload, StatClarificationPayload):
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
        session = self.sessions.get(self.key)
        if session is not None:
            session.pending_item_search = None
        await interaction.response.edit_message(
            embed=_build_text_embed("Search cancelled", "No search was executed."),
            view=None,
            attachments=[],
        )

    async def _edit(self, interaction: discord.Interaction) -> None:
        session = self.sessions.get(self.key)
        if session is not None:
            session.pending_item_search = None
        prefix = bot_example_prefix(interaction.guild, interaction.client.user)
        await interaction.response.edit_message(
            embed=_build_text_embed(
                "Search not executed",
                f"Send a new {prefix} query with the changes you want.",
            ),
            view=None,
            attachments=[],
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
        if outcome.suggested_query:
            description = (
                "Did you mean: "
                f"`{bot_example_prefix} {outcome.suggested_query}`"
            )
        else:
            description = (
                "Try an explicit item/stat query, for example:\n"
                f"• `{bot_example_prefix} hp armor`\n"
                f"• `{bot_example_prefix} cr bow`\n"
                f"• `{bot_example_prefix} hp > 5000 and cr bow`"
            )
        return (
            _build_text_embed(
                "I couldn't interpret that search",
                description,
            ),
            None,
            None,
        )

    if outcome.kind == "item_understanding":
        pending = outcome.pending_item_search
        if pending is None:
            return _build_text_embed("Search failed", "No item interpretation was produced."), None, None
        if session is not None:
            session.pending_item_search = pending
        return (
            build_item_understanding_embed(pending),
            ItemUnderstandingView(
                sessions=sessions,
                key=key,
                generation=generation,
                database_path=database_path,
                pending=pending,
            ),
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
