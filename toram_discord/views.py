from __future__ import annotations

from pathlib import Path
from typing import Mapping

import search_items as core
from toram_search.fallback import SearchIntentRequest
from toram_search.llm import OllamaQwenClient
from toram_search.service import ItemDetailPayload, SearchService, ServiceOutcome
from toram_search.session import FailedQueryContext, PendingItemSearch

from toram_discord.sessions import DiscordSessionManager, SessionKey


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
