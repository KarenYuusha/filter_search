from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
from typing import Literal


@dataclass(frozen=True)
class FailedQueryAttempt:
    original_query: str
    suggested_query: str | None = None


class FailedQueryContext:
    def __init__(self, max_entries: int = 3) -> None:
        self._entries: deque[FailedQueryAttempt] = deque(maxlen=max_entries)

    def record_failure(self, query: str) -> None:
        self._entries.append(FailedQueryAttempt(query.strip()))

    def set_latest_suggestion(self, suggestion: str) -> None:
        if not self._entries:
            return
        self._entries[-1] = replace(self._entries[-1], suggested_query=suggestion.strip())

    def clear(self) -> None:
        self._entries.clear()

    def snapshot(self) -> tuple[FailedQueryAttempt, ...]:
        return tuple(self._entries)


ScreenInputKind = Literal[
    "select", "next", "prev", "filter", "new", "quit", "empty", "new_query"
]


@dataclass(frozen=True)
class ScreenInput:
    kind: ScreenInputKind
    value: str = ""
    selection: int | None = None


def classify_screen_input(
    text: str,
    *,
    allow_filter: bool,
    allow_new_command: bool,
) -> ScreenInput:
    raw = text.strip()
    lowered = raw.casefold()
    if not raw:
        return ScreenInput("empty")
    if raw.isdigit():
        return ScreenInput("select", value=raw, selection=int(raw))
    if lowered in {"q", "quit", "exit"}:
        return ScreenInput("quit", value=raw)
    if lowered in {"n", "next"}:
        return ScreenInput("next", value=raw)
    if lowered in {"p", "prev", "previous"}:
        return ScreenInput("prev", value=raw)
    if allow_new_command and lowered == "new":
        return ScreenInput("new", value=raw)
    if allow_filter and (lowered == "filter" or lowered.startswith("filter ")):
        return ScreenInput("filter", value=raw)
    return ScreenInput("new_query", value=raw)
