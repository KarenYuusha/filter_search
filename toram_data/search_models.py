from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from toram_data.stat_query import ResolvedClause


@dataclass(frozen=True)
class ItemSummary:
    id: int
    name: str
    item_type: str


@dataclass(frozen=True)
class ItemDetail:
    summary: ItemSummary
    sell_price: float | None
    process_material: str | None
    process_amount: float | None
    badge: str | None
    note: str | None
    page_url: str | None
    stats: list[dict[str, Any]]
    sources: list[dict[str, Any]]
    images: list[dict[str, Any]]
    upgrade_predecessors: list[ItemSummary]
    upgrade_successors: list[ItemSummary]


@dataclass(frozen=True)
class StatRow:
    stat_name: str
    amount: float
    conditions_json: str
    condition_text: str | None
    coryn_applies_to: int | None
    needs_condition_review: bool
    position: int


@dataclass(frozen=True)
class RankedStatItem:
    item: ItemSummary
    primary: StatRow
    alternatives: tuple[StatRow, ...]


@dataclass(frozen=True)
class ClauseMatch:
    clause_index: int
    clause: ResolvedClause
    rows: tuple[StatRow, ...]


@dataclass(frozen=True)
class RankedExpressionItem:
    item: ItemSummary
    matches: tuple[ClauseMatch, ...]
    primary_amount: float | None


@dataclass(frozen=True)
class UpgradeGraph:
    nodes: dict[int, ItemSummary]
    edges: dict[int, tuple[int, ...]]
    missing_nodes: dict[int, ItemSummary]
