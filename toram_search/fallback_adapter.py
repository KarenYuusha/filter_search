from __future__ import annotations

from toram_data.aliases import STAT_ALIASES, STAT_AMBIGUOUS_GROUPS
from toram_data.item_filters import list_item_filter_phrases
from toram_data.search_repository import ItemRepository
from toram_search.fallback import QwenFallbackService
from toram_search.help_db import DatabaseQuestionService
from toram_search.parser import SEARCH_ONLY_STAT_ALIASES, parse_structured_search_request


def build_fallback_service(
    repository: ItemRepository,
    database_service: DatabaseQuestionService,
    llm_client: object,
) -> QwenFallbackService:
    aliases: list[str] = []
    for alias, target in sorted(STAT_ALIASES.items()):
        aliases.append(f"{alias} -> {target}")
    for alias, targets in sorted(STAT_AMBIGUOUS_GROUPS.items()):
        aliases.append(f"{alias} -> {' / '.join(targets)}")
    for alias, target in sorted(SEARCH_ONLY_STAT_ALIASES.items()):
        aliases.append(f"{alias} -> {target}")

    filter_labels: list[str] = []
    seen: set[str] = set()
    for row in list_item_filter_phrases(repository.list_item_types()):
        value = f"{row.phrase} -> {row.label}"
        if value in seen:
            continue
        seen.add(value)
        filter_labels.append(value)

    return QwenFallbackService(
        llm_client,
        validate_search_request=lambda request: parse_structured_search_request(
            request,
            repository,
        ) is not None,
        validate_database_action=database_service.validate_request,
        ground_database_action=database_service.is_request_grounded,
        stat_catalog=tuple(repository.list_stat_names()),
        alias_catalog=tuple(aliases),
        item_filter_catalog=tuple(filter_labels),
    )
