from __future__ import annotations

from dataclasses import dataclass, field

from toram_search.fallback import SearchIntentRequest
from toram_search.service import ItemDetailPayload, SearchPayload
from toram_search.session import FailedQueryContext, PendingItemSearch

SessionKey = tuple[int, int, int]


@dataclass
class DatabaseChatContext:
    active_domain: str | None = None
    active_skill_ids: tuple[str, ...] = ()
    active_item_ids: tuple[int, ...] = ()
    selected_skill_id: str | None = None
    selected_item_id: int | None = None
    active_tree_id: str | None = None
    active_skill_filters: dict[str, object] = field(default_factory=dict)
    last_operation: str | None = None
    last_metric: str | None = None
    last_user_query: str | None = None

    def __post_init__(self) -> None:
        self._normalize_domain_state()

    def __setattr__(self, name: str, value: object) -> None:
        if name != "active_domain":
            object.__setattr__(self, name, value)
            return

        previous = self.__dict__.get("active_domain")
        object.__setattr__(self, name, value)
        if previous is None or previous == value:
            return
        if value == "item":
            object.__setattr__(self, "active_skill_ids", ())
            object.__setattr__(self, "selected_skill_id", None)
            object.__setattr__(self, "active_tree_id", None)
            object.__setattr__(self, "active_skill_filters", {})
        elif value == "skill":
            object.__setattr__(self, "active_item_ids", ())
            object.__setattr__(self, "selected_item_id", None)
        elif value == "mixed":
            object.__setattr__(self, "selected_skill_id", None)
            object.__setattr__(self, "selected_item_id", None)
            object.__setattr__(self, "active_tree_id", None)
            object.__setattr__(self, "active_skill_filters", {})

    def _normalize_domain_state(self) -> None:
        if self.active_domain == "item":
            self.active_skill_ids = ()
            self.selected_skill_id = None
            self.active_tree_id = None
            self.active_skill_filters = {}
        elif self.active_domain == "skill":
            self.active_item_ids = ()
            self.selected_item_id = None
        elif self.active_domain == "mixed":
            self.selected_skill_id = None
            self.selected_item_id = None

    def clear(self) -> None:
        self.active_domain = None
        self.active_skill_ids = ()
        self.active_item_ids = ()
        self.selected_skill_id = None
        self.selected_item_id = None
        self.active_tree_id = None
        self.active_skill_filters.clear()
        self.last_operation = None
        self.last_metric = None
        self.last_user_query = None


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
    chat_context: DatabaseChatContext = field(default_factory=DatabaseChatContext)


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

    @staticmethod
    def _clone_chat_context(previous: DatabaseChatContext | None) -> DatabaseChatContext:
        if previous is None:
            return DatabaseChatContext()
        return DatabaseChatContext(
            active_domain=previous.active_domain,
            active_skill_ids=previous.active_skill_ids,
            active_item_ids=previous.active_item_ids,
            selected_skill_id=previous.selected_skill_id,
            selected_item_id=previous.selected_item_id,
            active_tree_id=previous.active_tree_id,
            active_skill_filters=dict(previous.active_skill_filters),
            last_operation=previous.last_operation,
            last_metric=previous.last_metric,
            last_user_query=previous.last_user_query,
        )

    def start_query(self, key: SessionKey, query: str) -> DiscordSearchSession:
        previous = self._sessions.get(key)
        generation = 1 if previous is None else previous.generation + 1
        session = DiscordSearchSession(
            generation=generation,
            query=query,
            failed_context=self._clone_failed_context(
                previous.failed_context if previous is not None else None
            ),
            chat_context=self._clone_chat_context(
                previous.chat_context if previous is not None else None
            ),
        )
        self._sessions[key] = session
        return session

    def get(self, key: SessionKey) -> DiscordSearchSession | None:
        return self._sessions.get(key)

    def clear_chat_context(self, key: SessionKey) -> bool:
        session = self._sessions.get(key)
        if session is None:
            return False
        session.chat_context.clear()
        return True

    def is_current(self, key: SessionKey, generation: int) -> bool:
        session = self._sessions.get(key)
        return session is not None and session.generation == generation
