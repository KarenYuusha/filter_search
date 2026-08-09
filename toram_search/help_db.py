from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Protocol


ALLOWED_DATABASE_ACTIONS = {
    "list_stats",
    "stat_exists",
    "list_item_types",
    "item_type_exists",
    "count_items_total",
    "count_items_by_type",
    "count_items_with_stat",
}


class RepositoryLike(Protocol):
    def list_item_types(self): ...
    def list_stat_names(self): ...
    def count_items_total(self): ...
    def count_items_by_types(self, item_types): ...
    def count_items_with_stat(self, stat_name): ...


@dataclass(frozen=True)
class DatabaseActionRequest:
    action: str
    item_type: str | None = None
    stat: str | None = None


class HelpService:
    _SYNTAX = (
        "Search syntax:\n"
        "- Item name: venena\n"
        "- Stat: cr xtal\n"
        "- Numeric: hp >= 5000 armor\n"
        "- Boolean: hp > 5000 AND cr bow\n"
        "- Negative stat: -aggro xtal\n"
        "- Operators: >, >=, <, <=, =, ==; AND has priority over OR."
    )
    _EXAMPLES = (
        "Examples:\n"
        "cr xtal\n"
        "hp >= 5000 armor\n"
        "hp > 5000 and cr bow\n"
        "-aggro xtal"
    )
    _OPERATORS = "Supported operators: >, >=, <, <=, =, ==, AND, OR. Use -stat for negative values, e.g. -aggro xtal."

    def answer_topic(self, topic: str) -> str | None:
        topic = topic.strip().casefold()
        if topic == "syntax":
            return self._SYNTAX
        if topic == "examples":
            return self._EXAMPLES
        if topic == "operators":
            return self._OPERATORS
        return None

    def answer_direct(self, text: str) -> str | None:
        q = " ".join(text.strip().casefold().strip("?.!").split())
        if q in {
            "help",
            "help me",
            "how to use",
            "how to use it",
            "how do i use it",
            "how do i use this",
            "how to search",
            "how do i search",
            "search help",
            "search syntax",
            "usage",
        }:
            return self._SYNTAX
        if q in {"examples", "search examples", "give me search examples"}:
            return self._EXAMPLES
        if q in {"operators", "search operators", "what operators can i use", "what operators are supported"}:
            return self._OPERATORS
        return None


class DatabaseQuestionService:
    def __init__(
        self,
        repository: RepositoryLike,
        *,
        resolve_item_type: Callable[[str], tuple[str, tuple[str, ...]] | None] | None = None,
        resolve_stat: Callable[[str], str | None] | None = None,
    ) -> None:
        self.repository = repository
        self._resolve_item_type = resolve_item_type
        self._resolve_stat = resolve_stat

    @staticmethod
    def _clean(text: str) -> str:
        return " ".join(text.strip().strip("?.!").split())

    def _canonical_item_type(self, text: str) -> tuple[str, tuple[str, ...]] | None:
        cleaned = self._clean(text)
        if self._resolve_item_type is not None:
            resolved = self._resolve_item_type(cleaned)
            if resolved is not None:
                return resolved
        for item_type in sorted(self.repository.list_item_types(), key=len, reverse=True):
            if item_type.casefold() == cleaned.casefold():
                return item_type, (item_type,)
        return None

    def _canonical_stat(self, text: str) -> str | None:
        cleaned = self._clean(text)
        if self._resolve_stat is not None:
            resolved = self._resolve_stat(cleaned)
            if resolved is not None:
                return resolved
        for stat in self.repository.list_stat_names():
            if stat.casefold() == cleaned.casefold():
                return stat
        return None

    def match_direct(self, text: str) -> DatabaseActionRequest | None:
        raw = self._clean(text)
        q = raw.casefold()
        if q in {
            "what stats are in the database",
            "list stats",
            "what stats can i search",
            "which stats are in the database",
        }:
            return DatabaseActionRequest("list_stats")
        if q in {"what item types are in the database", "list item types", "what item types are available"}:
            return DatabaseActionRequest("list_item_types")
        if q in {
            "how many total items are there",
            "how many items are there",
            "how many items are in the database",
            "total item count",
        }:
            return DatabaseActionRequest("count_items_total")

        m = re.fullmatch(r"(?:does|is) (.+?) (?:exist|in the database)", raw, flags=re.I)
        if m:
            value = m.group(1).strip()
            stat = self._canonical_stat(value)
            if stat is not None:
                return DatabaseActionRequest("stat_exists", stat=stat)
            item = self._canonical_item_type(value)
            if item is not None:
                return DatabaseActionRequest("item_type_exists", item_type=item[0])

        m = re.fullmatch(r"how many (.+?) items (?:are there|are in the database)", raw, flags=re.I)
        if m:
            item = self._canonical_item_type(m.group(1))
            if item is not None:
                return DatabaseActionRequest("count_items_by_type", item_type=item[0])

        m = re.fullmatch(r"how many items (?:have|with) (.+)", raw, flags=re.I)
        if m:
            stat = self._canonical_stat(m.group(1))
            if stat is not None:
                return DatabaseActionRequest("count_items_with_stat", stat=stat)
        return None

    def validate_request(self, request: DatabaseActionRequest) -> bool:
        if request.action not in ALLOWED_DATABASE_ACTIONS:
            return False
        if request.action in {"list_stats", "list_item_types", "count_items_total"}:
            return request.item_type is None and request.stat is None
        if request.action in {"count_items_by_type", "item_type_exists"}:
            return request.item_type is not None and self._canonical_item_type(request.item_type) is not None and request.stat is None
        if request.action in {"count_items_with_stat", "stat_exists"}:
            return request.stat is not None and self._canonical_stat(request.stat) is not None and request.item_type is None
        return False

    def execute(self, request: DatabaseActionRequest) -> str:
        if not self.validate_request(request):
            raise ValueError(f"Invalid database action: {request}")
        if request.action == "list_stats":
            stats = sorted(self.repository.list_stat_names(), key=str.casefold)
            return f"Stats in the database ({len(stats)}):\n" + "\n".join(stats)
        if request.action == "list_item_types":
            types = sorted(self.repository.list_item_types(), key=str.casefold)
            return f"Item types in the database ({len(types)}):\n" + "\n".join(types)
        if request.action == "count_items_total":
            return f"There are {self.repository.count_items_total()} items in the database."
        if request.action in {"count_items_by_type", "item_type_exists"}:
            assert request.item_type is not None
            resolved = self._canonical_item_type(request.item_type)
            assert resolved is not None
            label, item_types = resolved
            if request.action == "item_type_exists":
                return f"Yes. {label} is an available item type."
            count = self.repository.count_items_by_types(item_types)
            return f"There are {count} {label} items in the database."
        assert request.stat is not None
        stat = self._canonical_stat(request.stat)
        assert stat is not None
        if request.action == "stat_exists":
            return f"Yes. {stat} exists in the database."
        count = self.repository.count_items_with_stat(stat)
        return f"There are {count} items with {stat}."
