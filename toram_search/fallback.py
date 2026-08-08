from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Callable, Literal, Protocol

from .help_db import ALLOWED_DATABASE_ACTIONS, DatabaseActionRequest
from .llm import LLMResponseError, LLMUnavailableError
from .session import FailedQueryAttempt


FallbackKind = Literal[
    "search_requests", "database_action", "help", "refuse", "unavailable", "failed"
]
SearchMatch = Literal["all", "any"]
ComparisonOperator = Literal[">", ">=", "<", "<=", "=", "=="]


@dataclass(frozen=True)
class SearchStatIntent:
    name: str
    operator: ComparisonOperator | None = None
    value: float | int | None = None


@dataclass(frozen=True)
class SearchIntentRequest:
    stats: tuple[SearchStatIntent, ...]
    item_filter: str | None = None
    match: SearchMatch = "all"
    sort_stat: str | None = None


@dataclass(frozen=True)
class FallbackOutcome:
    kind: FallbackKind
    search_requests: tuple[SearchIntentRequest, ...] = ()
    database_request: DatabaseActionRequest | None = None
    help_topic: str | None = None
    message: str | None = None


class LLMClient(Protocol):
    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        schema: dict[str, object] | None = None,
    ) -> dict[str, object]: ...


_BUILD_TERMS = {
    "tank",
    "dps",
    "build",
    "mage",
}
_HELP_TOPICS = {"syntax", "operators", "examples"}
_COMPARISON_OPERATORS = {">", ">=", "<", "<=", "=", "=="}


class QwenFallbackService:
    def __init__(
        self,
        llm_client: LLMClient,
        *,
        validate_search_request: Callable[[SearchIntentRequest], bool],
        validate_database_action: Callable[[DatabaseActionRequest], bool],
        stat_catalog: tuple[str, ...],
        alias_catalog: tuple[str, ...],
        item_filter_catalog: tuple[str, ...],
    ) -> None:
        self.llm_client = llm_client
        self.validate_search_request = validate_search_request
        self.validate_database_action = validate_database_action
        self.stat_catalog = stat_catalog
        self.alias_catalog = alias_catalog
        self.item_filter_catalog = item_filter_catalog

    @staticmethod
    def _contains_out_of_scope_build_concept(text: str) -> bool:
        tokens = set(re.findall(r"[a-z0-9%]+", text.casefold()))
        return bool(tokens & _BUILD_TERMS)

    @staticmethod
    def _response_schema() -> dict[str, object]:
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "intent": {
                    "type": "string",
                    "enum": ["search", "database_action", "help", "refuse"],
                },
                "candidates": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 3,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "item_filter": {"type": "string"},
                            "match": {"type": "string", "enum": ["all", "any"]},
                            "sort_stat": {"type": "string"},
                            "stats": {
                                "type": "array",
                                "minItems": 1,
                                "maxItems": 3,
                                "items": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "properties": {
                                        "name": {"type": "string"},
                                        "operator": {
                                            "type": "string",
                                            "enum": [">", ">=", "<", "<=", "=", "=="],
                                        },
                                        "value": {"type": "number"},
                                    },
                                    "required": ["name"],
                                },
                            },
                        },
                        "required": ["stats"],
                    },
                },
                "action": {"type": "string"},
                "item_type": {"type": "string"},
                "stat": {"type": "string"},
                "topic": {"type": "string"},
            },
            "required": ["intent"],
        }

    def _system_prompt(self) -> str:
        return (
            "Convert the user's request into one constrained JSON object. "
            "You are only an interpreter for a local Toram item database; never answer facts from memory and never write SQL. "
            "For item/stat searches return intent=search with 1-3 structured candidates. "
            "Each candidate has stats, optional item_filter, optional match=all|any, and optional sort_stat. "
            "A stat entry has name and optionally operator plus numeric value. "
            "Use sort_stat only for highest/best ranking and make it one of the candidate's stats. "
            "If wording is ambiguous, return multiple candidates rather than guessing. "
            "For factual database metadata/count requests use intent=database_action. "
            "For search instructions use intent=help. For unsupported/general/build questions use intent=refuse."
        )

    def _user_prompt(
        self,
        current_input: str,
        history: tuple[FailedQueryAttempt, ...],
    ) -> str:
        history_lines = []
        for index, entry in enumerate(history[-3:], start=1):
            line = f"{index}. {entry.original_query}"
            if entry.suggested_query:
                line += f" | prior suggestion: {entry.suggested_query}"
            history_lines.append(line)
        history_text = "\n".join(history_lines) if history_lines else "(none)"
        return "\n".join(
            [
                f"Current input: {current_input}",
                "Recent failed inputs:",
                history_text,
                "Canonical stats (prefer these exact names):",
                ", ".join(self.stat_catalog),
                "Known stat aliases:",
                ", ".join(self.alias_catalog),
                "Allowed item-filter phrases (use the phrase before '->'):",
                ", ".join(self.item_filter_catalog),
                "Database actions: " + ", ".join(sorted(ALLOWED_DATABASE_ACTIONS)),
                "Help topics: syntax, operators, examples",
            ]
        )

    @staticmethod
    def _normalize_key(value: str) -> str:
        return " ".join(value.casefold().split())

    def _search_candidate_from_payload(
        self,
        payload: object,
    ) -> SearchIntentRequest | None:
        if not isinstance(payload, dict):
            return None
        allowed_keys = {"stats", "item_filter", "match", "sort_stat"}
        if not set(payload).issubset(allowed_keys) or "stats" not in payload:
            return None

        stats_obj = payload.get("stats")
        if not isinstance(stats_obj, list) or not 1 <= len(stats_obj) <= 3:
            return None

        stats: list[SearchStatIntent] = []
        for stat_obj in stats_obj:
            if not isinstance(stat_obj, dict):
                return None
            if not set(stat_obj).issubset({"name", "operator", "value"}) or "name" not in stat_obj:
                return None
            name = stat_obj.get("name")
            if not isinstance(name, str) or not name.strip():
                return None
            operator = stat_obj.get("operator")
            value = stat_obj.get("value")
            if operator is None:
                if value is not None:
                    return None
            else:
                if not isinstance(operator, str) or operator not in _COMPARISON_OPERATORS:
                    return None
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    return None
                if not math.isfinite(float(value)):
                    return None
            stats.append(SearchStatIntent(name.strip(), operator, value))

        item_filter_obj = payload.get("item_filter")
        if item_filter_obj is not None:
            if not isinstance(item_filter_obj, str) or not item_filter_obj.strip():
                return None
            item_filter = item_filter_obj.strip()
        else:
            item_filter = None

        match_obj = payload.get("match", "all")
        if match_obj not in {"all", "any"}:
            return None
        match: SearchMatch = match_obj

        sort_obj = payload.get("sort_stat")
        if sort_obj is not None:
            if not isinstance(sort_obj, str) or not sort_obj.strip():
                return None
            sort_stat = sort_obj.strip()
            sort_key = self._normalize_key(sort_stat)
            if not any(self._normalize_key(stat.name) == sort_key for stat in stats):
                return None
        else:
            sort_stat = None

        return SearchIntentRequest(
            stats=tuple(stats),
            item_filter=item_filter,
            match=match,
            sort_stat=sort_stat,
        )

    def _database_request_from_payload(self, payload: dict[str, object]) -> DatabaseActionRequest | None:
        if "sql" in payload:
            return None
        action = payload.get("action")
        if not isinstance(action, str) or action not in ALLOWED_DATABASE_ACTIONS:
            return None

        no_arg_actions = {"list_stats", "list_item_types", "count_items_total"}
        item_actions = {"count_items_by_type", "item_type_exists"}
        stat_actions = {"count_items_with_stat", "stat_exists"}
        if action in no_arg_actions:
            if set(payload) != {"intent", "action"}:
                return None
            request = DatabaseActionRequest(action)
        elif action in item_actions:
            if set(payload) != {"intent", "action", "item_type"}:
                return None
            item_type = payload.get("item_type")
            if not isinstance(item_type, str) or not item_type.strip():
                return None
            request = DatabaseActionRequest(action, item_type=item_type.strip())
        elif action in stat_actions:
            if set(payload) != {"intent", "action", "stat"}:
                return None
            stat = payload.get("stat")
            if not isinstance(stat, str) or not stat.strip():
                return None
            request = DatabaseActionRequest(action, stat=stat.strip())
        else:
            return None
        return request if self.validate_database_action(request) else None

    def _complete(self, system_prompt: str, user_prompt: str) -> dict[str, object]:
        schema = self._response_schema()
        try:
            return self.llm_client.complete(system_prompt, user_prompt, schema=schema)
        except TypeError as exc:
            if "schema" not in str(exc):
                raise
            return self.llm_client.complete(system_prompt, user_prompt)

    def interpret(
        self,
        current_input: str,
        history: tuple[FailedQueryAttempt, ...],
    ) -> FallbackOutcome:
        if self._contains_out_of_scope_build_concept(current_input):
            return FallbackOutcome("refuse")
        try:
            payload = self._complete(
                self._system_prompt(),
                self._user_prompt(current_input, history),
            )
        except LLMUnavailableError as exc:
            return FallbackOutcome("unavailable", message=str(exc))
        except LLMResponseError as exc:
            return FallbackOutcome("failed", message=str(exc))

        if not isinstance(payload, dict) or "sql" in payload:
            return FallbackOutcome("failed")
        intent = payload.get("intent")
        if intent == "refuse":
            if set(payload) != {"intent"}:
                return FallbackOutcome("failed")
            return FallbackOutcome("refuse")
        if intent == "help":
            if set(payload) != {"intent", "topic"}:
                return FallbackOutcome("failed")
            topic = payload.get("topic")
            if isinstance(topic, str) and topic in _HELP_TOPICS:
                return FallbackOutcome("help", help_topic=topic)
            return FallbackOutcome("failed")
        if intent == "database_action":
            request = self._database_request_from_payload(payload)
            if request is None:
                return FallbackOutcome("failed")
            return FallbackOutcome("database_action", database_request=request)
        if intent == "search":
            if set(payload) != {"intent", "candidates"}:
                return FallbackOutcome("failed")
            candidates_obj = payload.get("candidates")
            if not isinstance(candidates_obj, list) or not 1 <= len(candidates_obj) <= 3:
                return FallbackOutcome("failed")

            valid: list[SearchIntentRequest] = []
            seen: set[SearchIntentRequest] = set()
            for candidate_obj in candidates_obj:
                request = self._search_candidate_from_payload(candidate_obj)
                if request is None or request in seen:
                    continue
                seen.add(request)
                if self.validate_search_request(request):
                    valid.append(request)
            if valid:
                return FallbackOutcome("search_requests", search_requests=tuple(valid))
            return FallbackOutcome("failed")
        return FallbackOutcome("failed")
