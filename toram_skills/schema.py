from __future__ import annotations

import sqlite3


REQUIRED_COLUMNS: dict[str, frozenset[str]] = {
    "metadata": frozenset({"key", "value"}),
    "skill_trees": frozenset({
        "id", "name", "normalized_name", "tree_group", "source_file",
        "general_text", "tier_requirements_json", "weapon_restrictions_json", "issues_json",
    }),
    "skills": frozenset({
        "id", "tree_id", "source_order", "name", "normalized_name", "tier",
        "required_level", "skill_type", "mp_cost_text", "mp_cost_value",
        "damage_type", "element", "cast_range_text", "hit_range_text",
        "cast_time_text", "hit_count_text", "description", "game_description",
        "raw_text", "issues_json",
    }),
    "skill_aliases": frozenset({"skill_id", "position", "alias", "normalized_alias"}),
    "skill_sections": frozenset({"id", "skill_id", "position", "label", "normalized_label", "body"}),
    "skill_ailments": frozenset({"skill_id", "position", "name", "normalized_name"}),
    "skill_weapon_requirements": frozenset({"skill_id", "position", "weapon", "normalized_name"}),
    "skill_weapon_restrictions": frozenset({"skill_id", "position", "weapon", "normalized_name"}),
    "skill_tree_weapon_restrictions": frozenset({"tree_id", "position", "weapon", "normalized_weapon"}),
    "skill_search_documents": frozenset({
        "id", "skill_id", "position", "kind", "label", "text", "text_hash",
    }),
    "skill_fts": frozenset({"document_id", "skill_id", "name", "tree_name", "text"}),
    "skill_embedding_vectors": frozenset({
        "document_id", "model", "dimensions", "text_hash", "vector",
    }),
}
REQUIRED_TABLES = frozenset(REQUIRED_COLUMNS)


class SchemaError(RuntimeError):
    pass


def create_schema(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(
        """
        CREATE TABLE metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE skill_trees (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            normalized_name TEXT NOT NULL,
            tree_group TEXT NOT NULL,
            source_file TEXT NOT NULL UNIQUE,
            general_text TEXT NOT NULL,
            tier_requirements_json TEXT NOT NULL,
            weapon_restrictions_json TEXT NOT NULL,
            issues_json TEXT NOT NULL
        );

        CREATE TABLE skills (
            id TEXT PRIMARY KEY,
            tree_id TEXT NOT NULL REFERENCES skill_trees(id) ON DELETE CASCADE,
            source_order INTEGER NOT NULL,
            name TEXT NOT NULL,
            normalized_name TEXT NOT NULL,
            tier INTEGER,
            required_level INTEGER,
            skill_type TEXT,
            mp_cost_text TEXT,
            mp_cost_value INTEGER,
            damage_type TEXT,
            element TEXT,
            cast_range_text TEXT,
            hit_range_text TEXT,
            cast_time_text TEXT,
            hit_count_text TEXT,
            description TEXT,
            game_description TEXT,
            raw_text TEXT NOT NULL,
            issues_json TEXT NOT NULL,
            UNIQUE(tree_id, normalized_name),
            UNIQUE(tree_id, source_order)
        );

        CREATE TABLE skill_aliases (
            skill_id TEXT NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
            position INTEGER NOT NULL,
            alias TEXT NOT NULL,
            normalized_alias TEXT NOT NULL,
            PRIMARY KEY(skill_id, position)
        );

        CREATE TABLE skill_sections (
            id INTEGER PRIMARY KEY,
            skill_id TEXT NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
            position INTEGER NOT NULL,
            label TEXT NOT NULL,
            normalized_label TEXT NOT NULL,
            body TEXT NOT NULL,
            UNIQUE(skill_id, position)
        );

        CREATE TABLE skill_ailments (
            skill_id TEXT NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
            position INTEGER NOT NULL,
            name TEXT NOT NULL,
            normalized_name TEXT NOT NULL,
            PRIMARY KEY(skill_id, position)
        );

        CREATE TABLE skill_weapon_requirements (
            skill_id TEXT NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
            position INTEGER NOT NULL,
            weapon TEXT NOT NULL,
            normalized_name TEXT NOT NULL,
            PRIMARY KEY(skill_id, position)
        );

        CREATE TABLE skill_weapon_restrictions (
            skill_id TEXT NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
            position INTEGER NOT NULL,
            weapon TEXT NOT NULL,
            normalized_name TEXT NOT NULL,
            PRIMARY KEY(skill_id, position)
        );

        CREATE TABLE skill_tree_weapon_restrictions (
            tree_id TEXT NOT NULL REFERENCES skill_trees(id) ON DELETE CASCADE,
            position INTEGER NOT NULL,
            weapon TEXT NOT NULL,
            normalized_weapon TEXT NOT NULL,
            PRIMARY KEY(tree_id, position)
        );

        CREATE TABLE skill_search_documents (
            id TEXT PRIMARY KEY,
            skill_id TEXT NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
            position INTEGER NOT NULL,
            kind TEXT NOT NULL,
            label TEXT,
            text TEXT NOT NULL,
            text_hash TEXT NOT NULL,
            UNIQUE(skill_id, position)
        );

        CREATE VIRTUAL TABLE skill_fts USING fts5(
            document_id UNINDEXED,
            skill_id UNINDEXED,
            name,
            tree_name,
            text,
            tokenize='unicode61 remove_diacritics 2'
        );

        CREATE TABLE skill_embedding_vectors (
            document_id TEXT PRIMARY KEY REFERENCES skill_search_documents(id) ON DELETE CASCADE,
            model TEXT NOT NULL,
            dimensions INTEGER NOT NULL,
            text_hash TEXT NOT NULL,
            vector BLOB NOT NULL
        );
        """
    )


def verify_schema(connection: sqlite3.Connection) -> None:
    errors: list[str] = []
    for table, required in REQUIRED_COLUMNS.items():
        actual = {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}
        missing = sorted(required - actual)
        if missing:
            errors.append(f"{table}: missing {', '.join(missing)}")
    if errors:
        raise SchemaError("Invalid skill database schema: " + "; ".join(errors))
