from __future__ import annotations

import json
from pathlib import Path
import sqlite3

from .models import ParseIssue, SkillDraft, SkillSection, SkillTreeDraft
from .parsing import normalize_skill_name
from .schema import SchemaError, verify_schema


def _json_dump(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _issues_to_json(issues: tuple[ParseIssue, ...]) -> str:
    return _json_dump([
        {
            "level": issue.level,
            "code": issue.code,
            "source_file": issue.source_file,
            "skill_name": issue.skill_name,
            "message": issue.message,
        }
        for issue in issues
    ])


def _issues_from_json(text: str) -> tuple[ParseIssue, ...]:
    values = json.loads(text)
    return tuple(ParseIssue(**value) for value in values)


class SkillRepository:
    def __init__(self, database_path: Path) -> None:
        self.database_path = Path(database_path).expanduser().resolve()
        if not self.database_path.is_file():
            raise FileNotFoundError(f"Database not found: {self.database_path}")
        self.connection = sqlite3.connect(self.database_path, timeout=5.0, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        try:
            verify_schema(self.connection)
        except Exception:
            self.connection.close()
            raise

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "SkillRepository":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def count_trees(self) -> int:
        return int(self.connection.execute("SELECT COUNT(*) FROM skill_trees").fetchone()[0])

    def count_skills(self) -> int:
        return int(self.connection.execute("SELECT COUNT(*) FROM skills").fetchone()[0])

    def list_tree_names(self) -> list[str]:
        rows = self.connection.execute(
            "SELECT name FROM skill_trees ORDER BY name COLLATE NOCASE, id"
        )
        return [str(row["name"]) for row in rows]

    def _tree_from_row(self, row: sqlite3.Row) -> SkillTreeDraft:
        tier_values = json.loads(str(row["tier_requirements_json"]))
        restrictions = json.loads(str(row["weapon_restrictions_json"]))
        return SkillTreeDraft(
            id=str(row["id"]),
            name=str(row["name"]),
            normalized_name=str(row["normalized_name"]),
            tree_group=str(row["tree_group"]),
            source_file=str(row["source_file"]),
            general_text=str(row["general_text"]),
            tier_requirements=tuple(
                (int(value[0]), None if value[1] is None else int(value[1]))
                for value in tier_values
            ),
            weapon_restrictions=tuple(str(value) for value in restrictions),
            issues=_issues_from_json(str(row["issues_json"])),
        )

    def get_tree(self, tree_id: str) -> SkillTreeDraft:
        row = self.connection.execute(
            "SELECT * FROM skill_trees WHERE id = ?",
            (tree_id,),
        ).fetchone()
        if row is None:
            raise KeyError(tree_id)
        return self._tree_from_row(row)

    def resolve_tree_name(self, name: str) -> tuple[SkillTreeDraft, ...]:
        normalized = normalize_skill_name(name)
        rows = self.connection.execute(
            """
            SELECT *
            FROM skill_trees
            WHERE normalized_name = ?
            ORDER BY id
            """,
            (normalized,),
        )
        return tuple(self._tree_from_row(row) for row in rows)

    def list_skills_in_tree(self, tree_id: str) -> tuple[SkillDraft, ...]:
        rows = self.connection.execute(
            """
            SELECT id
            FROM skills
            WHERE tree_id = ?
            ORDER BY source_order, id
            """,
            (tree_id,),
        )
        return tuple(self.get_skill(str(row["id"])) for row in rows)

    def resolve_skill_name(
        self,
        name: str,
        *,
        tree_id: str | None = None,
    ) -> tuple[SkillDraft, ...]:
        normalized = normalize_skill_name(name)
        canonical_tree_clause = " AND s.tree_id = ?" if tree_id is not None else ""
        alias_tree_clause = " AND s.tree_id = ?" if tree_id is not None else ""
        params: list[str] = [normalized]
        if tree_id is not None:
            params.append(tree_id)
        params.extend([normalized, normalized])
        if tree_id is not None:
            params.append(tree_id)
        rows = self.connection.execute(
            f"""
            WITH matches AS (
                SELECT s.id AS id, 0 AS match_kind
                FROM skills AS s
                WHERE s.normalized_name = ?{canonical_tree_clause}

                UNION ALL

                SELECT s.id AS id, 1 AS match_kind
                FROM skills AS s
                JOIN skill_aliases AS a ON a.skill_id = s.id
                WHERE a.normalized_alias = ?
                  AND s.normalized_name <> ?{alias_tree_clause}
            )
            SELECT s.id, MIN(matches.match_kind) AS match_kind
            FROM matches
            JOIN skills AS s ON s.id = matches.id
            GROUP BY s.id
            ORDER BY match_kind, s.tree_id, s.source_order, s.id
            """,
            tuple(params),
        )
        return tuple(self.get_skill(str(row["id"])) for row in rows)

    def set_metadata(self, key: str, value: str) -> None:
        self.connection.execute(
            "INSERT INTO metadata(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    def get_metadata(self, key: str) -> str | None:
        row = self.connection.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
        return None if row is None else str(row["value"])

    def insert_tree(self, tree: SkillTreeDraft) -> None:
        self.connection.execute(
            """
            INSERT INTO skill_trees(
                id, name, normalized_name, tree_group, source_file, general_text,
                tier_requirements_json, weapon_restrictions_json, issues_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tree.id,
                tree.name,
                tree.normalized_name,
                tree.tree_group,
                tree.source_file,
                tree.general_text,
                _json_dump(tree.tier_requirements),
                _json_dump(tree.weapon_restrictions),
                _issues_to_json(tree.issues),
            ),
        )
        self.connection.executemany(
            """
            INSERT INTO skill_tree_weapon_restrictions(
                tree_id, position, weapon, normalized_weapon
            ) VALUES (?, ?, ?, ?)
            """,
            [
                (tree.id, position, weapon, normalize_skill_name(weapon))
                for position, weapon in enumerate(tree.weapon_restrictions)
            ],
        )

    def insert_skill(self, skill: SkillDraft) -> None:
        self.connection.execute(
            """
            INSERT INTO skills(
                id, tree_id, source_order, name, normalized_name, tier, required_level,
                skill_type, mp_cost_text, mp_cost_value, damage_type, element,
                cast_range_text, hit_range_text, cast_time_text, hit_count_text,
                description, game_description, raw_text, issues_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                skill.id,
                skill.tree_id,
                skill.source_order,
                skill.name,
                skill.normalized_name,
                skill.tier,
                skill.required_level,
                skill.skill_type,
                skill.mp_cost_text,
                skill.mp_cost_value,
                skill.damage_type,
                skill.element,
                skill.cast_range_text,
                skill.hit_range_text,
                skill.cast_time_text,
                skill.hit_count_text,
                skill.description,
                skill.game_description,
                skill.raw_text,
                _issues_to_json(skill.issues),
            ),
        )
        self.connection.executemany(
            "INSERT INTO skill_aliases(skill_id, position, alias, normalized_alias) VALUES (?, ?, ?, ?)",
            [
                (skill.id, position, alias, normalize_skill_name(alias))
                for position, alias in enumerate(skill.aliases)
            ],
        )
        self.connection.executemany(
            "INSERT INTO skill_sections(skill_id, position, label, normalized_label, body) VALUES (?, ?, ?, ?, ?)",
            [
                (skill.id, section.position, section.label, section.normalized_label, section.body)
                for section in skill.sections
            ],
        )
        self.connection.executemany(
            """
            INSERT INTO skill_ailments(skill_id, position, name, normalized_name)
            VALUES (?, ?, ?, ?)
            """,
            [
                (skill.id, position, name, normalize_skill_name(name))
                for position, name in enumerate(skill.ailments)
            ],
        )
        self.connection.executemany(
            """
            INSERT INTO skill_weapon_requirements(skill_id, position, weapon, normalized_name)
            VALUES (?, ?, ?, ?)
            """,
            [
                (skill.id, position, weapon, normalize_skill_name(weapon))
                for position, weapon in enumerate(skill.weapon_requirements)
            ],
        )
        self.connection.executemany(
            """
            INSERT INTO skill_weapon_restrictions(skill_id, position, weapon, normalized_name)
            VALUES (?, ?, ?, ?)
            """,
            [
                (skill.id, position, weapon, normalize_skill_name(weapon))
                for position, weapon in enumerate(skill.weapon_restrictions)
            ],
        )

    def _ordered_values(self, table: str, column: str, skill_id: str) -> tuple[str, ...]:
        rows = self.connection.execute(
            f"SELECT {column} AS value FROM {table} WHERE skill_id = ? ORDER BY position",
            (skill_id,),
        )
        return tuple(str(row["value"]) for row in rows)

    def get_skill(self, skill_id: str) -> SkillDraft:
        row = self.connection.execute("SELECT * FROM skills WHERE id = ?", (skill_id,)).fetchone()
        if row is None:
            raise KeyError(skill_id)
        aliases = self._ordered_values("skill_aliases", "alias", skill_id)
        ailments = self._ordered_values("skill_ailments", "name", skill_id)
        weapon_requirements = self._ordered_values("skill_weapon_requirements", "weapon", skill_id)
        weapon_restrictions = self._ordered_values("skill_weapon_restrictions", "weapon", skill_id)
        section_rows = self.connection.execute(
            "SELECT position, label, normalized_label, body FROM skill_sections WHERE skill_id = ? ORDER BY position",
            (skill_id,),
        )
        sections = tuple(
            SkillSection(
                position=int(section["position"]),
                label=str(section["label"]),
                normalized_label=str(section["normalized_label"]),
                body=str(section["body"]),
            )
            for section in section_rows
        )
        return SkillDraft(
            id=str(row["id"]),
            tree_id=str(row["tree_id"]),
            source_order=int(row["source_order"]),
            name=str(row["name"]),
            normalized_name=str(row["normalized_name"]),
            aliases=aliases,
            tier=None if row["tier"] is None else int(row["tier"]),
            required_level=None if row["required_level"] is None else int(row["required_level"]),
            skill_type=None if row["skill_type"] is None else str(row["skill_type"]),
            mp_cost_text=None if row["mp_cost_text"] is None else str(row["mp_cost_text"]),
            mp_cost_value=None if row["mp_cost_value"] is None else int(row["mp_cost_value"]),
            damage_type=None if row["damage_type"] is None else str(row["damage_type"]),
            element=None if row["element"] is None else str(row["element"]),
            cast_range_text=None if row["cast_range_text"] is None else str(row["cast_range_text"]),
            hit_range_text=None if row["hit_range_text"] is None else str(row["hit_range_text"]),
            cast_time_text=None if row["cast_time_text"] is None else str(row["cast_time_text"]),
            hit_count_text=None if row["hit_count_text"] is None else str(row["hit_count_text"]),
            ailments=ailments,
            weapon_requirements=weapon_requirements,
            weapon_restrictions=weapon_restrictions,
            sections=sections,
            description=None if row["description"] is None else str(row["description"]),
            game_description=None if row["game_description"] is None else str(row["game_description"]),
            raw_text=str(row["raw_text"]),
            issues=_issues_from_json(str(row["issues_json"])),
        )

    def get_skill_by_name(self, tree_id: str, name: str) -> SkillDraft:
        normalized = normalize_skill_name(name)
        row = self.connection.execute(
            "SELECT id FROM skills WHERE tree_id = ? AND normalized_name = ?",
            (tree_id, normalized),
        ).fetchone()
        if row is None:
            raise KeyError((tree_id, name))
        return self.get_skill(str(row["id"]))


__all__ = ["SchemaError", "SkillRepository"]
