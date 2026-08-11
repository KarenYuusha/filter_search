from __future__ import annotations

from dataclasses import dataclass, field

from toram_search.fallback import SearchIntentRequest
from toram_search.service import ItemDetailPayload, SearchPayload
from toram_search.session import FailedQueryContext, PendingItemSearch

SessionKey = tuple[int, int, int]


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
    pending_item_search: PendingItemSearch | None = None


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
