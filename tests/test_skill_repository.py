from pathlib import Path
import sqlite3
import tempfile
import unittest

from toram_skills.models import ParseIssue, SkillDraft, SkillSection, SkillTreeDraft
from toram_skills.repository import SchemaError, SkillRepository
from toram_skills.schema import REQUIRED_TABLES, create_schema


class SkillRepositoryTests(unittest.TestCase):
    def test_create_schema_contains_all_required_tables(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "skills.sqlite"
            connection = sqlite3.connect(db_path)
            create_schema(connection)
            names = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            connection.close()
            self.assertTrue(REQUIRED_TABLES <= names)

    def test_missing_table_database_raises_schema_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "bad.sqlite"
            sqlite3.connect(db_path).close()
            with self.assertRaises(SchemaError):
                SkillRepository(db_path)

    def test_skill_round_trip_preserves_lossless_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "skills.sqlite"
            connection = sqlite3.connect(db_path)
            create_schema(connection)
            connection.close()

            tree = SkillTreeDraft(
                id="assist_skills/test_skills",
                name="Test Skills",
                normalized_name="test skills",
                tree_group="assist",
                source_file="assist_skills/test_skills.txt",
                general_text="General source text",
                tier_requirements=((1, None), (2, 30)),
                weapon_restrictions=("Bow",),
                issues=(
                    ParseIssue("warning", "tree_note", "assist_skills/test_skills.txt", None, "note"),
                ),
            )
            skill = SkillDraft(
                id="assist_skills/test_skills/test-skill",
                tree_id=tree.id,
                source_order=0,
                name="TEST SKILL",
                normalized_name="test skill",
                aliases=("TS", "Test"),
                tier=2,
                required_level=30,
                skill_type="Active",
                mp_cost_text="200",
                mp_cost_value=200,
                damage_type="Magic",
                element="Neutral",
                cast_range_text="12m",
                hit_range_text="3m",
                cast_time_text="2 seconds",
                hit_count_text="1 hit",
                ailments=("Tumble",),
                weapon_requirements=("Staff",),
                weapon_restrictions=("Bowgun",),
                sections=(
                    SkillSection(0, "Skill Effect", "skill effect", "Exact section body"),
                    SkillSection(1, "Notes", "notes", "Second body"),
                ),
                description="Description text",
                game_description="Game description text",
                raw_text="RAW\nSOURCE\nTEXT",
                issues=(
                    ParseIssue("warning", "source_uncertainty", tree.source_file, "TEST SKILL", "uncertain"),
                ),
            )

            with SkillRepository(db_path) as repo:
                repo.insert_tree(tree)
                repo.insert_skill(skill)
                loaded = repo.get_skill(skill.id)
                self.assertEqual(loaded, skill)
                self.assertEqual(repo.get_skill_by_name(tree.id, "test skill"), skill)
                self.assertEqual(repo.count_trees(), 1)
                self.assertEqual(repo.count_skills(), 1)
                self.assertEqual(repo.list_tree_names(), ["Test Skills"])


if __name__ == "__main__":
    unittest.main()
