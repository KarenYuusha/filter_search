from __future__ import annotations

from pathlib import Path
import shutil
import tempfile
import unittest

from toram_skills.repository import SkillRepository


ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "coryn_data" / "database" / "skills.sqlite"


class SkillExactLookupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = SkillRepository(DATABASE)

    def tearDown(self) -> None:
        self.repo.close()

    def test_exact_name_resolution_is_case_insensitive_and_cross_tree(self):
        matches = self.repo.resolve_skill_name("magic: finale")
        self.assertEqual(
            [skill.id for skill in matches],
            ["weapon_class_skills/magic_skills/magic-finale"],
        )

    def test_tree_qualified_resolution_never_leaks_other_trees(self):
        matches = self.repo.resolve_skill_name(
            "magic: finale",
            tree_id="weapon_class_skills/magic_skills",
        )
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].tree_id, "weapon_class_skills/magic_skills")

    def test_unknown_exact_name_returns_empty_tuple(self):
        self.assertEqual(self.repo.resolve_skill_name("definitely not a skill"), ())

    def test_tree_lookup_and_listing_are_deterministic(self):
        tree = self.repo.get_tree("weapon_class_skills/magic_skills")
        self.assertEqual(tree.name, "Magic Skills")
        skills = self.repo.list_skills_in_tree(tree.id)
        self.assertGreater(len(skills), 0)
        self.assertEqual(
            [(skill.source_order, skill.id) for skill in skills],
            sorted((skill.source_order, skill.id) for skill in skills),
        )
        self.assertEqual(
            [match.id for match in self.repo.resolve_tree_name("magic skills")],
            [tree.id],
        )

    def test_canonical_name_sorts_before_alias_only_match(self):
        self.repo.close()
        with tempfile.TemporaryDirectory() as tmp:
            copied = Path(tmp) / "skills.sqlite"
            shutil.copy2(DATABASE, copied)
            with SkillRepository(copied) as repo:
                alias_skill_id = "sub_weapon_skills/assassin_skills/assassin-stab"
                repo.connection.execute(
                    """
                    INSERT INTO skill_aliases(skill_id, position, alias, normalized_alias)
                    VALUES (?, ?, ?, ?)
                    """,
                    (alias_skill_id, 999, "Magic: Finale", "magic: finale"),
                )
                matches = repo.resolve_skill_name("MAGIC: FINALE")
                self.assertEqual(
                    [skill.id for skill in matches[:2]],
                    [
                        "weapon_class_skills/magic_skills/magic-finale",
                        alias_skill_id,
                    ],
                )
        self.repo = SkillRepository(DATABASE)


if __name__ == "__main__":
    unittest.main()
