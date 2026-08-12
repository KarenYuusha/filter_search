from __future__ import annotations

from .parsing import normalize_skill_name
from .repository import SkillRepository
from .search_models import SkillFilters


def _placeholders(count: int) -> str:
    return ", ".join("?" for _ in range(count))


def _normalized_values(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(normalize_skill_name(value) for value in values)


def structured_skill_ids(
    repository: SkillRepository,
    filters: SkillFilters,
) -> tuple[str, ...]:
    clauses: list[str] = []
    params: list[object] = []

    if filters.tree_ids:
        clauses.append(f"s.tree_id IN ({_placeholders(len(filters.tree_ids))})")
        params.extend(filters.tree_ids)

    if filters.tree_groups:
        values = _normalized_values(filters.tree_groups)
        clauses.append(f"LOWER(t.tree_group) IN ({_placeholders(len(values))})")
        params.extend(values)

    if filters.tiers:
        clauses.append(f"s.tier IN ({_placeholders(len(filters.tiers))})")
        params.extend(filters.tiers)

    if filters.required_level_max is not None:
        clauses.append("s.required_level IS NOT NULL AND s.required_level <= ?")
        params.append(filters.required_level_max)

    if filters.skill_types:
        values = _normalized_values(filters.skill_types)
        clauses.append(f"LOWER(s.skill_type) IN ({_placeholders(len(values))})")
        params.extend(values)

    if filters.mp_cost_max is not None:
        clauses.append("s.mp_cost_value IS NOT NULL AND s.mp_cost_value <= ?")
        params.append(filters.mp_cost_max)

    if filters.damage_types:
        values = _normalized_values(filters.damage_types)
        clauses.append(f"LOWER(s.damage_type) IN ({_placeholders(len(values))})")
        params.extend(values)

    if filters.ailments:
        values = _normalized_values(filters.ailments)
        clauses.append(
            f"""
            EXISTS (
                SELECT 1
                FROM skill_ailments AS a
                WHERE a.skill_id = s.id
                  AND a.normalized_name IN ({_placeholders(len(values))})
            )
            """.strip()
        )
        params.extend(values)

    if filters.weapons:
        values = _normalized_values(filters.weapons)
        placeholders = _placeholders(len(values))
        clauses.append(
            f"""
            (
                EXISTS (
                    SELECT 1
                    FROM skill_weapon_requirements AS wr
                    WHERE wr.skill_id = s.id
                      AND wr.normalized_name IN ({placeholders})
                )
                OR EXISTS (
                    SELECT 1
                    FROM skill_weapon_restrictions AS ws
                    WHERE ws.skill_id = s.id
                      AND ws.normalized_name IN ({placeholders})
                )
                OR EXISTS (
                    SELECT 1
                    FROM skill_tree_weapon_restrictions AS twr
                    WHERE twr.tree_id = s.tree_id
                      AND twr.normalized_weapon IN ({placeholders})
                )
            )
            """.strip()
        )
        params.extend(values)
        params.extend(values)
        params.extend(values)

    where = " AND ".join(f"({clause})" for clause in clauses)
    sql = "SELECT s.id FROM skills AS s JOIN skill_trees AS t ON t.id = s.tree_id"
    if where:
        sql += " WHERE " + where
    sql += " ORDER BY s.id"

    rows = repository.connection.execute(sql, tuple(params))
    return tuple(str(row["id"]) for row in rows)


__all__ = ["structured_skill_ids"]
