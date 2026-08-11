from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from toram_data.aliases import is_crysta_item_type, normalize_name, normalize_stat_text
from toram_data.search_models import (
    ClauseMatch,
    ItemDetail,
    ItemSummary,
    RankedExpressionItem,
    RankedStatItem,
    StatRow,
    UpgradeGraph,
)
from toram_data.stat_query import ResolvedStatExpression, compare_amount


class ItemRepository:
    def __init__(self, database_path: Path) -> None:
        if not database_path.is_file():
            raise FileNotFoundError(f"SQLite database not found: {database_path}")
        self.database_path = database_path
        self.db = sqlite3.connect(database_path)
        self.db.row_factory = sqlite3.Row
        self._verify_schema()

    def _verify_schema(self) -> None:
        required = {"items", "item_stats", "item_sources", "item_images"}
        rows = self.db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
        existing = {str(row["name"]) for row in rows}
        missing = sorted(required - existing)
        if missing:
            raise RuntimeError("Database is missing required table(s): " + ", ".join(missing))

    def close(self) -> None:
        self.db.close()

    def list_items(self) -> list[ItemSummary]:
        rows = self.db.execute(
            """
            SELECT id, name, item_type
            FROM items
            ORDER BY name COLLATE NOCASE, id
            """
        ).fetchall()
        return [
            ItemSummary(int(row["id"]), str(row["name"]), str(row["item_type"]))
            for row in rows
        ]

    def list_upgrade_items(self) -> list[ItemSummary]:
        """Return only crysta items eligible for upgrade-mode lookup."""
        return [
            item for item in self.list_items()
            if is_crysta_item_type(item.item_type)
        ]

    def exact_upgrade_name_matches(self, query: str) -> list[ItemSummary]:
        """Return exact name matches restricted to crysta item types."""
        normalized_query = normalize_name(query)
        if not normalized_query:
            return []
        return [
            item for item in self.list_upgrade_items()
            if normalize_name(item.name) == normalized_query
        ]

    def list_item_types(self) -> set[str]:
        rows = self.db.execute(
            "SELECT DISTINCT item_type FROM items ORDER BY item_type COLLATE NOCASE"
        ).fetchall()
        return {str(row["item_type"]) for row in rows}

    def list_stat_names(self) -> list[str]:
        rows = self.db.execute(
            """
            SELECT DISTINCT stat_name
            FROM item_stats
            WHERE stat_name <> 'Upgrade for'
            ORDER BY stat_name COLLATE NOCASE
            """
        ).fetchall()
        return [str(row["stat_name"]) for row in rows]

    def count_items_total(self) -> int:
        row = self.db.execute("SELECT COUNT(*) AS count FROM items").fetchone()
        return int(row["count"])

    def count_items_by_types(self, item_types: tuple[str, ...]) -> int:
        if not item_types:
            return 0
        placeholders = ", ".join("?" for _ in item_types)
        row = self.db.execute(
            f"SELECT COUNT(*) AS count FROM items WHERE item_type IN ({placeholders})",
            item_types,
        ).fetchone()
        return int(row["count"])

    def count_items_with_stat(self, stat_name: str) -> int:
        row = self.db.execute(
            """
            SELECT COUNT(DISTINCT item_id) AS count
            FROM item_stats
            WHERE stat_name = ?
              AND stat_name <> 'Upgrade for'
            """,
            (stat_name,),
        ).fetchone()
        return int(row["count"])

    def exact_name_matches(self, query: str) -> list[ItemSummary]:
        normalized_query = normalize_name(query)
        if not normalized_query:
            return []
        return [
            item for item in self.list_items()
            if normalize_name(item.name) == normalized_query
        ]

    def get_item(self, item_id: int) -> ItemDetail:
        item = self.db.execute(
            """
            SELECT id, name, item_type, sell_price, process_material,
                   process_amount, badge, note, page_url
            FROM items
            WHERE id = ?
            """,
            (item_id,),
        ).fetchone()
        if item is None:
            raise KeyError(f"Item {item_id} does not exist")

        stats = [
            dict(row)
            for row in self.db.execute(
                """
                SELECT stat_name, amount, conditions_json, condition_text,
                       coryn_applies_to, needs_condition_review
                FROM item_stats
                WHERE item_id = ?
                  AND stat_name <> 'Upgrade for'
                ORDER BY position, id
                """,
                (item_id,),
            ).fetchall()
        ]
        source_columns = {
            str(row["name"])
            for row in self.db.execute("PRAGMA table_info(item_sources)").fetchall()
        }
        source_fields = (
            "source_id",
            "source_name",
            "level",
            "map",
            "dye",
            "source_url",
            "lookup_error",
        )
        source_select = ", ".join(
            field if field in source_columns else f"NULL AS {field}"
            for field in source_fields
        )
        sources = [
            dict(row)
            for row in self.db.execute(
                f"""
                SELECT {source_select}
                FROM item_sources
                WHERE item_id = ?
                ORDER BY position, id
                """,
                (item_id,),
            ).fetchall()
        ]
        images = [
            dict(row)
            for row in self.db.execute(
                """
                SELECT category, gender, variant, local_path, source_url
                FROM item_images
                WHERE item_id = ?
                ORDER BY position, id
                """,
                (item_id,),
            ).fetchall()
        ]
        return ItemDetail(
            summary=ItemSummary(int(item["id"]), str(item["name"]), str(item["item_type"])),
            sell_price=item["sell_price"],
            process_material=item["process_material"],
            process_amount=item["process_amount"],
            badge=item["badge"],
            note=item["note"],
            page_url=item["page_url"],
            stats=stats,
            sources=sources,
            images=images,
            upgrade_predecessors=self.get_upgrade_predecessors(item_id),
            upgrade_successors=self.get_upgrade_successors(item_id),
        )

    @staticmethod
    def _coerce_upgrade_item_id(value: Any) -> int | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return None
        if not numeric.is_integer() or numeric <= 0:
            return None
        return int(numeric)

    def _item_summary(self, item_id: int) -> ItemSummary | None:
        row = self.db.execute(
            "SELECT id, name, item_type FROM items WHERE id = ?",
            (item_id,),
        ).fetchone()
        if row is None:
            return None
        return ItemSummary(int(row["id"]), str(row["name"]), str(row["item_type"]))

    def _upgrade_predecessor_ids(self, item_id: int) -> list[int]:
        rows = self.db.execute(
            """
            SELECT amount
            FROM item_stats
            WHERE item_id = ?
              AND stat_name = 'Upgrade for'
            ORDER BY position, id
            """,
            (item_id,),
        ).fetchall()
        output: list[int] = []
        for row in rows:
            target_id = self._coerce_upgrade_item_id(row["amount"])
            if target_id is not None and target_id not in output:
                output.append(target_id)
        return output

    def get_upgrade_predecessors(self, item_id: int) -> list[ItemSummary]:
        output: list[ItemSummary] = []
        for target_id in self._upgrade_predecessor_ids(item_id):
            summary = self._item_summary(target_id)
            if summary is None:
                summary = ItemSummary(target_id, "Unknown item", "Unknown")
            output.append(summary)
        return sorted(output, key=lambda item: (item.name.casefold(), item.id))

    def get_upgrade_successors(self, item_id: int) -> list[ItemSummary]:
        rows = self.db.execute(
            """
            SELECT i.id, i.name, i.item_type, s.amount
            FROM item_stats AS s
            JOIN items AS i ON i.id = s.item_id
            WHERE s.stat_name = 'Upgrade for'
            ORDER BY i.name COLLATE NOCASE, i.id, s.position
            """
        ).fetchall()
        output: list[ItemSummary] = []
        seen: set[int] = set()
        for row in rows:
            target_id = self._coerce_upgrade_item_id(row["amount"])
            successor_id = int(row["id"])
            if target_id != item_id or successor_id in seen:
                continue
            seen.add(successor_id)
            output.append(
                ItemSummary(successor_id, str(row["name"]), str(row["item_type"]))
            )
        return output

    def get_upgrade_component(self, item_id: int) -> UpgradeGraph:
        selected = self._item_summary(item_id)
        if selected is None:
            raise KeyError(f"Item {item_id} does not exist")

        nodes: dict[int, ItemSummary] = {item_id: selected}
        missing_nodes: dict[int, ItemSummary] = {}
        mutable_edges: dict[int, set[int]] = {}
        pending = [item_id]
        visited: set[int] = set()

        while pending:
            current_id = pending.pop()
            if current_id in visited:
                continue
            visited.add(current_id)

            for predecessor_id in self._upgrade_predecessor_ids(current_id):
                mutable_edges.setdefault(predecessor_id, set()).add(current_id)
                predecessor = self._item_summary(predecessor_id)
                if predecessor is None:
                    missing_nodes.setdefault(
                        predecessor_id,
                        ItemSummary(predecessor_id, "Unknown item", "Unknown"),
                    )
                else:
                    nodes.setdefault(predecessor_id, predecessor)
                    if predecessor_id not in visited:
                        pending.append(predecessor_id)

            for successor in self.get_upgrade_successors(current_id):
                mutable_edges.setdefault(current_id, set()).add(successor.id)
                nodes.setdefault(successor.id, successor)
                if successor.id not in visited:
                    pending.append(successor.id)

        all_summaries = {**missing_nodes, **nodes}
        edges: dict[int, tuple[int, ...]] = {}
        for source_id in set(all_summaries) | set(mutable_edges):
            targets = mutable_edges.get(source_id, set())
            edges[source_id] = tuple(
                sorted(
                    targets,
                    key=lambda target_id: (
                        all_summaries.get(
                            target_id,
                            ItemSummary(target_id, "Unknown item", "Unknown"),
                        ).name.casefold(),
                        target_id,
                    ),
                )
            )
        return UpgradeGraph(nodes=nodes, edges=edges, missing_nodes=missing_nodes)

    def search_by_stat(
        self,
        stat_name: str,
        item_types: tuple[str, ...] | None,
    ) -> list[RankedStatItem]:
        if normalize_stat_text(stat_name) == "upgrade for":
            return []
        parameters: list[Any] = [stat_name]
        filter_sql = ""
        if item_types:
            placeholders = ", ".join("?" for _ in item_types)
            filter_sql = f" AND i.item_type IN ({placeholders})"
            parameters.extend(item_types)
        rows = self.db.execute(
            f"""
            SELECT i.id, i.name, i.item_type,
                   s.stat_name, s.amount, s.conditions_json,
                   s.condition_text, s.coryn_applies_to,
                   s.needs_condition_review, s.position
            FROM item_stats AS s
            JOIN items AS i ON i.id = s.item_id
            WHERE s.stat_name = ?
              AND s.stat_name <> 'Upgrade for'
              AND s.amount IS NOT NULL
              {filter_sql}
            ORDER BY s.amount DESC,
                     i.name COLLATE NOCASE,
                     i.id,
                     s.position
            """,
            parameters,
        ).fetchall()

        grouped: dict[int, tuple[ItemSummary, list[StatRow]]] = {}
        order: list[int] = []
        for row in rows:
            item_id = int(row["id"])
            stat_row = StatRow(
                stat_name=str(row["stat_name"]),
                amount=float(row["amount"]),
                conditions_json=str(row["conditions_json"] or "[]"),
                condition_text=row["condition_text"],
                coryn_applies_to=row["coryn_applies_to"],
                needs_condition_review=bool(row["needs_condition_review"]),
                position=int(row["position"]),
            )
            if item_id not in grouped:
                grouped[item_id] = (
                    ItemSummary(item_id, str(row["name"]), str(row["item_type"])),
                    [],
                )
                order.append(item_id)
            grouped[item_id][1].append(stat_row)

        return [
            RankedStatItem(
                item=grouped[item_id][0],
                primary=grouped[item_id][1][0],
                alternatives=tuple(grouped[item_id][1][1:]),
            )
            for item_id in order
        ]

    def search_by_expression(
        self,
        expression: ResolvedStatExpression,
        item_types: tuple[str, ...] | None,
        *,
        primary_sort_ascending: bool = False,
    ) -> list[RankedExpressionItem]:
        indexed_groups: list[list[tuple[int, ResolvedClause]]] = []
        stat_names: list[str] = []
        clause_index = 0
        for group in expression.groups:
            indexed_group: list[tuple[int, ResolvedClause]] = []
            for clause in group.clauses:
                if normalize_stat_text(clause.stat_name) == "upgrade for":
                    raise ValueError("Upgrade for is not a searchable numeric stat")
                indexed_group.append((clause_index, clause))
                clause_index += 1
                if clause.stat_name not in stat_names:
                    stat_names.append(clause.stat_name)
            indexed_groups.append(indexed_group)
        if not stat_names:
            return []

        stat_placeholders = ", ".join("?" for _ in stat_names)
        parameters: list[Any] = list(stat_names)
        filter_sql = ""
        if item_types:
            type_placeholders = ", ".join("?" for _ in item_types)
            filter_sql = f" AND i.item_type IN ({type_placeholders})"
            parameters.extend(item_types)
        rows = self.db.execute(
            f"""
            SELECT i.id, i.name, i.item_type,
                   s.stat_name, s.amount, s.conditions_json,
                   s.condition_text, s.coryn_applies_to,
                   s.needs_condition_review, s.position
            FROM item_stats AS s
            JOIN items AS i ON i.id = s.item_id
            WHERE s.stat_name IN ({stat_placeholders})
              AND s.stat_name <> 'Upgrade for'
              AND s.amount IS NOT NULL
              {filter_sql}
            ORDER BY i.name COLLATE NOCASE, i.id, s.position
            """,
            parameters,
        ).fetchall()

        items: dict[int, ItemSummary] = {}
        rows_by_item: dict[int, dict[str, list[StatRow]]] = {}
        for row in rows:
            item_id = int(row["id"])
            items[item_id] = ItemSummary(item_id, str(row["name"]), str(row["item_type"]))
            stat_row = StatRow(
                stat_name=str(row["stat_name"]),
                amount=float(row["amount"]),
                conditions_json=str(row["conditions_json"] or "[]"),
                condition_text=row["condition_text"],
                coryn_applies_to=row["coryn_applies_to"],
                needs_condition_review=bool(row["needs_condition_review"]),
                position=int(row["position"]),
            )
            rows_by_item.setdefault(item_id, {}).setdefault(stat_row.stat_name, []).append(stat_row)

        results: list[RankedExpressionItem] = []
        for item_id, rows_by_stat in rows_by_item.items():
            retained: dict[tuple[int, int, str, float], ClauseMatch] = {}
            matched_any_group = False
            primary_rows: list[StatRow] = []
            for indexed_group in indexed_groups:
                group_matches: list[ClauseMatch] = []
                group_satisfied = True
                for current_index, clause in indexed_group:
                    satisfying = tuple(
                        row
                        for row in rows_by_stat.get(clause.stat_name, ())
                        if compare_amount(row.amount, clause.operator, clause.value)
                    )
                    if not satisfying:
                        group_satisfied = False
                        break
                    group_matches.append(ClauseMatch(current_index, clause, satisfying))
                if not group_satisfied:
                    continue
                matched_any_group = True
                for match in group_matches:
                    for row in match.rows:
                        key = (match.clause_index, row.position, row.stat_name, row.amount)
                        retained[key] = ClauseMatch(match.clause_index, match.clause, (row,))
                    if match.clause_index == 0:
                        primary_rows.extend(match.rows)
            if not matched_any_group:
                continue

            grouped_matches: dict[int, tuple[ResolvedClause, list[StatRow]]] = {}
            for match in retained.values():
                clause, matched_rows = grouped_matches.setdefault(
                    match.clause_index, (match.clause, [])
                )
                matched_rows.extend(match.rows)
            matches = tuple(
                ClauseMatch(
                    index,
                    clause,
                    tuple(
                        sorted(
                            matched_rows,
                            key=(
                                (lambda row: (row.amount, row.position))
                                if primary_sort_ascending and index == 0
                                else (lambda row: (-row.amount, row.position))
                            ),
                        )
                    ),
                )
                for index, (clause, matched_rows) in sorted(grouped_matches.items())
            )
            if primary_sort_ascending:
                primary_amount = min((row.amount for row in primary_rows), default=None)
            else:
                primary_amount = max((row.amount for row in primary_rows), default=None)
            results.append(RankedExpressionItem(items[item_id], matches, primary_amount))

        if primary_sort_ascending:
            results.sort(
                key=lambda result: (
                    result.primary_amount is None,
                    result.primary_amount if result.primary_amount is not None else 0.0,
                    result.item.name.casefold(),
                    result.item.id,
                )
            )
        else:
            results.sort(
                key=lambda result: (
                    result.primary_amount is None,
                    -(result.primary_amount or 0.0),
                    result.item.name.casefold(),
                    result.item.id,
                )
            )
        return results
