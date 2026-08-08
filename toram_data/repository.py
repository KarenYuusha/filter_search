from __future__ import annotations

import json
import math
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from rapidfuzz import fuzz

from .aliases import is_crysta_item_type, normalize_name
from .models import ConditionDraft, ImageDraft, ItemDraft, SourceDraft, StatDraft


REQUIRED_COLUMNS: dict[str, frozenset[str]] = {
    "items": frozenset({
        "id", "schema_version", "name", "item_type", "sell_price",
        "process_material", "process_amount", "badge", "note",
        "api_url", "page_url", "json_path",
    }),
    "item_stats": frozenset({
        "id", "item_id", "position", "stat_name", "amount",
        "conditions_json", "condition_text", "coryn_applies_to",
        "needs_condition_review",
    }),
    "item_sources": frozenset({
        "id", "item_id", "position", "source_id", "source_name", "level",
        "map", "dye", "source_url", "lookup_error", "raw_cells_json",
    }),
    "item_images": frozenset({
        "id", "item_id", "position", "category", "gender", "variant",
        "local_path", "source_url",
    }),
    "item_image_errors": frozenset({"id", "item_id", "position", "error_json"}),
    "metadata": frozenset({"key", "value"}),
}


class SchemaError(RuntimeError):
    pass


class ConfigurationError(RuntimeError):
    pass


class DatabaseBusyError(RuntimeError):
    pass


@dataclass(frozen=True)
class ItemLookup:
    id: int
    name: str
    item_type: str
    score: float = 100.0


@dataclass(frozen=True)
class DeleteCounts:
    stats: int
    sources: int
    images: int
    image_errors: int
    incoming_upgrade_rows: int


class ItemRepository:
    def __init__(self, database_path: Path) -> None:
        self.database_path = Path(database_path).expanduser().resolve()
        if not self.database_path.is_file():
            raise FileNotFoundError(f"Database not found: {self.database_path}")
        self.connection = sqlite3.connect(
            self.database_path,
            timeout=5.0,
            isolation_level=None,
        )
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.verify_schema()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "ItemRepository":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def verify_schema(self) -> None:
        errors: list[str] = []
        for table, required in REQUIRED_COLUMNS.items():
            actual = {row["name"] for row in self.connection.execute(f"PRAGMA table_info({table})")}
            missing = sorted(required - actual)
            if missing:
                errors.append(f"{table}: missing {', '.join(missing)}")
        if errors:
            self.connection.close()
            raise SchemaError("Invalid Toram database schema: " + "; ".join(errors))

    def count_items(self) -> int:
        return int(self.connection.execute("SELECT COUNT(*) FROM items").fetchone()[0])

    def next_item_id(self) -> int:
        value = self.connection.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM items").fetchone()[0]
        return int(value)

    def default_schema_version(self) -> int:
        row = self.connection.execute(
            """
            SELECT schema_version, COUNT(*) AS frequency
            FROM items
            WHERE schema_version IS NOT NULL
            GROUP BY schema_version
            ORDER BY frequency DESC, schema_version DESC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            raise ConfigurationError("No non-null schema_version exists in items")
        return int(row["schema_version"])

    def item_id_exists(self, item_id: int) -> bool:
        return self.connection.execute("SELECT 1 FROM items WHERE id = ?", (item_id,)).fetchone() is not None

    def _distinct(self, table: str, column: str) -> list[str]:
        rows = self.connection.execute(
            f"SELECT DISTINCT {column} AS value FROM {table} WHERE {column} IS NOT NULL AND TRIM({column}) <> '' ORDER BY {column} COLLATE NOCASE"
        )
        return [str(row["value"]) for row in rows]

    def list_item_types(self) -> list[str]:
        return self._distinct("items", "item_type")

    def list_stat_names(self) -> list[str]:
        return [value for value in self._distinct("item_stats", "stat_name") if normalize_name(value) != "upgrade for"]

    def list_process_materials(self) -> list[str]:
        return self._distinct("items", "process_material")

    def list_source_names(self) -> list[str]:
        return self._distinct("item_sources", "source_name")

    def list_source_maps(self) -> list[str]:
        return self._distinct("item_sources", "map")

    def list_items(self) -> list[ItemLookup]:
        rows = self.connection.execute("SELECT id, name, item_type FROM items ORDER BY name COLLATE NOCASE, id")
        return [ItemLookup(int(row["id"]), str(row["name"]), str(row["item_type"])) for row in rows]

    def find_items(self, query: str, *, crysta_only: bool = False, limit: int = 5) -> list[ItemLookup]:
        normalized = normalize_name(query)
        candidates = [item for item in self.list_items() if not crysta_only or is_crysta_item_type(item.item_type)]
        exact = [item for item in candidates if normalize_name(item.name) == normalized]
        if exact:
            return exact[:limit]
        ranked: list[ItemLookup] = []
        for item in candidates:
            name = normalize_name(item.name)
            score = max(float(fuzz.WRatio(normalized, name)), float(fuzz.token_sort_ratio(normalized, name)))
            if normalized and normalized in name:
                score = max(score, 96.0 if name.startswith(normalized) else 93.0)
            ranked.append(ItemLookup(item.id, item.name, item.item_type, score))
        ranked.sort(key=lambda item: (-item.score, normalize_name(item.name), item.id))
        return ranked[:limit]

    @staticmethod
    def _condition_from_row(row: sqlite3.Row) -> ConditionDraft:
        try:
            parsed = json.loads(row["conditions_json"])
            valid = isinstance(parsed, list) and all(isinstance(value, str) for value in parsed)
        except (TypeError, json.JSONDecodeError):
            parsed = []
            valid = False
        if not valid:
            return ConditionDraft(
                mode="preserve",
                slugs=(),
                text=row["condition_text"],
                coryn_applies_to=row["coryn_applies_to"],
                needs_review=True,
            )
        slugs = tuple(parsed)
        if not slugs and not row["condition_text"] and row["coryn_applies_to"] is None:
            mode = "none"
        elif len(slugs) == 1 and not bool(row["needs_condition_review"]):
            mode = "known"
        elif bool(row["needs_condition_review"]):
            mode = "free_text"
        else:
            mode = "preserve"
        return ConditionDraft(
            mode=mode,
            slugs=slugs,
            text=row["condition_text"],
            coryn_applies_to=row["coryn_applies_to"],
            needs_review=bool(row["needs_condition_review"]),
        )

    def load_item_draft(self, item_id: int) -> ItemDraft:
        row = self.connection.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
        if row is None:
            raise KeyError(f"Item {item_id} does not exist")
        draft = ItemDraft(
            id=int(row["id"]),
            schema_version=int(row["schema_version"]) if row["schema_version"] is not None else 0,
            name=str(row["name"]),
            item_type=str(row["item_type"]),
            sell_price=row["sell_price"],
            process_material=row["process_material"],
            process_amount=row["process_amount"],
            badge=row["badge"],
            note=row["note"],
            api_url=row["api_url"],
            page_url=row["page_url"],
            json_path=str(row["json_path"]),
            is_new=False,
        )
        upgrade_ids: list[int] = []
        malformed: list[dict[str, object]] = []
        stat_rows = self.connection.execute(
            "SELECT * FROM item_stats WHERE item_id = ? ORDER BY position, id", (item_id,)
        )
        for stat in stat_rows:
            if normalize_name(str(stat["stat_name"])) == "upgrade for":
                amount = stat["amount"]
                try:
                    numeric = float(amount)
                except (TypeError, ValueError):
                    numeric = math.nan
                if math.isfinite(numeric) and numeric.is_integer():
                    upgrade_ids.append(int(numeric))
                else:
                    malformed.append(dict(stat))
                continue
            draft.stats.append(
                StatDraft(
                    stat_name=str(stat["stat_name"]),
                    amount=stat["amount"],
                    condition=self._condition_from_row(stat),
                    raw_conditions_json=str(stat["conditions_json"]),
                    original_position=int(stat["position"]),
                )
            )
        draft.existing_upgrade_item_ids = tuple(upgrade_ids)
        draft.previous_upgrade_item_id = upgrade_ids[0] if len(upgrade_ids) == 1 else None
        draft.malformed_upgrade_rows = tuple(malformed)

        for source in self.connection.execute(
            "SELECT * FROM item_sources WHERE item_id = ? ORDER BY position, id", (item_id,)
        ):
            draft.sources.append(SourceDraft(
                source_id=source["source_id"], source_name=source["source_name"], level=source["level"],
                map=source["map"], dye=source["dye"], source_url=source["source_url"],
                lookup_error=source["lookup_error"], raw_cells_json=source["raw_cells_json"],
                original_position=int(source["position"]),
            ))
        for image in self.connection.execute(
            "SELECT * FROM item_images WHERE item_id = ? ORDER BY position, id", (item_id,)
        ):
            draft.images.append(ImageDraft(
                category=image["category"], gender=image["gender"], variant=image["variant"],
                local_path=image["local_path"], source_url=image["source_url"],
                original_local_path=image["local_path"], original_position=int(image["position"]),
            ))
        draft.image_error_rows = tuple(
            dict(error) for error in self.connection.execute(
                "SELECT * FROM item_image_errors WHERE item_id = ? ORDER BY position, id", (item_id,)
            )
        )
        return draft

    def incoming_upgrade_item_ids(self, item_id: int) -> list[int]:
        rows = self.connection.execute(
            """
            SELECT DISTINCT item_id
            FROM item_stats
            WHERE stat_name = 'Upgrade for'
              AND amount IS NOT NULL
              AND CAST(amount AS INTEGER) = ?
              AND amount = CAST(amount AS INTEGER)
            ORDER BY item_id
            """,
            (item_id,),
        )
        return [int(row["item_id"]) for row in rows]

    def would_create_upgrade_cycle(self, item_id: int, predecessor_id: int) -> bool:
        if item_id == predecessor_id:
            return True
        visited: set[int] = set()
        pending = [predecessor_id]
        while pending:
            current = pending.pop()
            if current == item_id:
                return True
            if current in visited:
                continue
            visited.add(current)
            rows = self.connection.execute(
                "SELECT amount FROM item_stats WHERE item_id = ? AND stat_name = 'Upgrade for' AND amount IS NOT NULL",
                (current,),
            )
            for row in rows:
                try:
                    value = float(row["amount"])
                except (TypeError, ValueError):
                    continue
                if math.isfinite(value) and value.is_integer():
                    pending.append(int(value))
        return False

    @contextmanager
    def immediate_transaction(self) -> Iterator[None]:
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            yield
        except sqlite3.OperationalError as exc:
            self.connection.rollback()
            if "locked" in str(exc).casefold() or "busy" in str(exc).casefold():
                raise DatabaseBusyError("Database is locked by another process") from exc
            raise
        except Exception:
            self.connection.rollback()
            raise
        else:
            self.connection.commit()

    def _insert_item_row(self, draft: ItemDraft) -> None:
        self.connection.execute(
            """
            INSERT INTO items (
                id, schema_version, name, item_type, sell_price, process_material,
                process_amount, badge, note, api_url, page_url, json_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (draft.id, draft.schema_version, draft.name, draft.item_type, draft.sell_price,
             draft.process_material, draft.process_amount, draft.badge, draft.note,
             draft.api_url, draft.page_url, draft.json_path),
        )

    def _write_item_children(self, draft: ItemDraft) -> None:
        for position, stat in enumerate(draft.stats):
            self.connection.execute(
                """
                INSERT INTO item_stats (
                    item_id, position, stat_name, amount, conditions_json,
                    condition_text, coryn_applies_to, needs_condition_review
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (draft.id, position, stat.stat_name, stat.amount,
                 (stat.raw_conditions_json
                  if stat.condition.mode == "preserve" and stat.raw_conditions_json is not None
                  else json.dumps(list(stat.condition.slugs), separators=(",", ":"))),
                 stat.condition.text, stat.condition.coryn_applies_to,
                 1 if stat.condition.needs_review else 0),
            )
        if draft.previous_upgrade_item_id is not None:
            self.connection.execute(
                """
                INSERT INTO item_stats (
                    item_id, position, stat_name, amount, conditions_json,
                    condition_text, coryn_applies_to, needs_condition_review
                ) VALUES (?, ?, 'Upgrade for', ?, '[]', NULL, NULL, 0)
                """,
                (draft.id, len(draft.stats), draft.previous_upgrade_item_id),
            )
        for position, source in enumerate(draft.sources):
            self.connection.execute(
                """
                INSERT INTO item_sources (
                    item_id, position, source_id, source_name, level, map,
                    dye, source_url, lookup_error, raw_cells_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (draft.id, position, source.source_id, source.source_name, source.level,
                 source.map, source.dye, source.source_url, source.lookup_error, source.raw_cells_json),
            )
        for position, image in enumerate(draft.images):
            self.connection.execute(
                """
                INSERT INTO item_images (
                    item_id, position, category, gender, variant, local_path, source_url
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (draft.id, position, image.category, image.gender, image.variant,
                 image.local_path, image.source_url),
            )

    def _refresh_metadata(self) -> None:
        count = self.connection.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        modified = datetime.now(timezone.utc).isoformat()
        self.connection.executemany(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)",
            (("item_count", str(count)), ("last_modified_at", modified)),
        )

    def add_item(self, draft: ItemDraft) -> None:
        with self.immediate_transaction():
            if self.item_id_exists(draft.id):
                raise ValueError(f"Item ID {draft.id} already exists")
            self._insert_item_row(draft)
            self._write_item_children(draft)
            self._refresh_metadata()

    def update_item(self, draft: ItemDraft) -> None:
        with self.immediate_transaction():
            cursor = self.connection.execute(
                """
                UPDATE items SET
                    schema_version = ?, name = ?, item_type = ?, sell_price = ?,
                    process_material = ?, process_amount = ?, badge = ?, note = ?,
                    api_url = ?, page_url = ?, json_path = ?
                WHERE id = ?
                """,
                (draft.schema_version, draft.name, draft.item_type, draft.sell_price,
                 draft.process_material, draft.process_amount, draft.badge, draft.note,
                 draft.api_url, draft.page_url, draft.json_path, draft.id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"Item {draft.id} does not exist")
            self.connection.execute("DELETE FROM item_stats WHERE item_id = ?", (draft.id,))
            self.connection.execute("DELETE FROM item_sources WHERE item_id = ?", (draft.id,))
            self.connection.execute("DELETE FROM item_images WHERE item_id = ?", (draft.id,))
            self._write_item_children(draft)
            self._refresh_metadata()

    def delete_counts(self, item_id: int) -> DeleteCounts:
        def owned(table: str) -> int:
            return int(self.connection.execute(f"SELECT COUNT(*) FROM {table} WHERE item_id = ?", (item_id,)).fetchone()[0])
        incoming = int(self.connection.execute(
            """
            SELECT COUNT(*) FROM item_stats
            WHERE stat_name = 'Upgrade for' AND amount IS NOT NULL
              AND CAST(amount AS INTEGER) = ? AND amount = CAST(amount AS INTEGER)
            """, (item_id,)
        ).fetchone()[0])
        return DeleteCounts(owned("item_stats"), owned("item_sources"), owned("item_images"), owned("item_image_errors"), incoming)

    def delete_item(self, item_id: int) -> DeleteCounts:
        counts = self.delete_counts(item_id)
        with self.immediate_transaction():
            self.connection.execute(
                """
                DELETE FROM item_stats
                WHERE stat_name = 'Upgrade for'
                  AND amount IS NOT NULL
                  AND CAST(amount AS INTEGER) = ?
                  AND amount = CAST(amount AS INTEGER)
                """, (item_id,)
            )
            cursor = self.connection.execute("DELETE FROM items WHERE id = ?", (item_id,))
            if cursor.rowcount != 1:
                raise KeyError(f"Item {item_id} does not exist")
            self._refresh_metadata()
        return counts
