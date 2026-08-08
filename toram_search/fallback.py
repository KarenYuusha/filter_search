from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Callable, Literal, Protocol

from .help_db import ALLOWED_DATABASE_ACTIONS, DatabaseActionRequest
from .llm import LLMResponseError, LLMUnavailableError
from .session import FailedQueryAttempt


FallbackKind = Literal[
    "suggestions", "database_action", "help", "refuse", "unavailable", "failed"
]


@dataclass(frozen=True)
class FallbackOutcome:
    kind: FallbackKind
    suggestions: tuple[str, ...] = ()
    database_request: DatabaseActionRequest | None = None
    help_topic: str | None = None
    message: str | None = None


class LLMClient(Protocol):
    def complete(self, system_prompt: str, user_prompt: str) -> dict[str, object]: ...


_BUILD_TERMS = {
    "tank",
    "dps",
    "build",
    "mage",
    "support build",
    "tank build",
    "dps build",
}

_SQL_KEYS = {"sql"}
_HELP_TOPICS = {"syntax", "operators", "examples"}


class QwenFallbackService:
    def __init__(
        self,
        llm_client: LLMClient,
        *,
        validate_search_rewrite: Callable[[str], bool],
        validate_database_action: Callable[[DatabaseActionRequest], bool],
        stat_catalog: tuple[str, ...],
        alias_catalog: tuple[str, ...],
        item_filter_catalog: tuple[str, ...],
    ) -> None:
        self.llm_client = llm_client
        self.validate_search_rewrite = validate_search_rewrite
        self.validate_database_action = validate_database_action
        self.stat_catalog = stat_catalog
        self.alias_catalog = alias_catalog
        self.item_filter_catalog = item_filter_catalog

    @staticmethod
    def _contains_out_of_scope_build_concept(text: str) -> bool:
        normalized = " ".join(text.casefold().split())
        tokens = set(re.findall(r"[a-z0-9%]+", normalized))
        if tokens & {"tank", "dps", "build", "mage"}:
            return True
        return any(term in normalized for term in _BUILD_TERMS)

    def _system_prompt(self) -> str:
        return (
            "You are a constrained router for a local Toram item database search program. "
            "You do not answer from general knowledge. The current phase supports only item-name searches, "
            "actual stat searches, search syntax help, and factual database metadata/count questions. "
            "Never answer tank/DPS/build/gameplay recommendations or general questions. Never write SQL. "
            "Return exactly one JSON object and no prose. Allowed shapes: "
            '{"intent":"search_rewrite","suggestions":["..."]}; '
            '{"intent":"database_action","action":"count_items_by_type","item_type":"Bow"}; '
            '{"intent":"database_action","action":"count_items_with_stat","stat":"Critical Rate"}; '
            '{"intent":"database_action","action":"list_stats"}; '
            '{"intent":"help","topic":"syntax"}; '
            '{"intent":"refuse"}. '
            "Use at most 3 search suggestions. Preserve numeric operators, signs, percentages, AND/OR meaning. "
            "Only use canonical stats/aliases/item filters supplied by the user prompt."
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
                "Last failed attempts:",
                history_text,
                "Canonical stats:",
                ", ".join(self.stat_catalog),
                "Aliases:",
                ", ".join(self.alias_catalog),
                "Item filters:",
                ", ".join(self.item_filter_catalog),
                "Supported grammar: item name; bare stat; -stat; > >= < <= = ==; AND; OR; one global item filter; highest/lowest only when expressible as a stat search.",
                "Database actions: " + ", ".join(sorted(ALLOWED_DATABASE_ACTIONS)),
                "Help topics: syntax, operators, examples",
            ]
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

    def interpret(
        self,
        current_input: str,
        history: tuple[FailedQueryAttempt, ...],
    ) -> FallbackOutcome:
        if self._contains_out_of_scope_build_concept(current_input):
            return FallbackOutcome("refuse")
        try:
            payload = self.llm_client.complete(
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
        if intent == "search_rewrite":
            if set(payload) != {"intent", "suggestions"}:
                return FallbackOutcome("failed")
            suggestions_obj = payload.get("suggestions")
            if not isinstance(suggestions_obj, list):
                return FallbackOutcome("failed")
            valid: list[str] = []
            seen: set[str] = set()
            for candidate in suggestions_obj:
                if len(valid) >= 3:
                    break
                if not isinstance(candidate, str):
                    continue
                candidate = candidate.strip()
                key = candidate.casefold()
                if not candidate or key in seen:
                    continue
                seen.add(key)
                if self._contains_out_of_scope_build_concept(candidate):
                    continue
                if self.validate_search_rewrite(candidate):
                    valid.append(candidate)
            if valid:
                return FallbackOutcome("suggestions", suggestions=tuple(valid))
            return FallbackOutcome("failed")
        return FallbackOutcome("failed")
