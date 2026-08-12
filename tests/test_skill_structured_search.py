from __future__ import annotations

from pathlib import Path
import unittest

from toram_skills.repository import SkillRepository
from toram_skills.search_models import SkillFilters
from toram_skills.structured_search import structured_skill_ids


ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "coryn_data" / "database" / "skills.sqlite"


class SkillStructuredSearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = SkillRepository(DATABASE)

    def tearDown(self) -> None:
        self.repo.close()

    def test_empty_filters_return_all_skills_in_stable_order(self):
        ids = structured_skill_ids(self.repo, SkillFilters())
        self.assertEqual(len(ids), self.repo.count_skills())
        self.assertEqual(ids, tuple(sorted(ids)))

    def test_tree_and_tier_use_and_semantics(self):
        filters = SkillFilters(
            tree_ids=("weapon_class_skills/magic_skills",),
            tiers=(4,),
        )
        ids = structured_skill_ids(self.repo, filters)
        self.assertIn("weapon_class_skills/magic_skills/magic-finale", ids)
        for skill_id in ids:
            skill = self.repo.get_skill(skill_id)
            self.assertEqual(skill.tree_id, "weapon_class_skills/magic_skills")
            self.assertEqual(skill.tier, 4)

    def test_multiple_tiers_use_or_semantics_inside_field(self):
        ids = structured_skill_ids(self.repo, SkillFilters(tiers=(1, 4)))
        self.assertGreater(len(ids), 0)
        self.assertTrue(all(self.repo.get_skill(skill_id).tier in {1, 4} for skill_id in ids))

    def test_mp_cost_max_excludes_unknown_costs(self):
        ids = structured_skill_ids(self.repo, SkillFilters(mp_cost_max=100))
        self.assertGreater(len(ids), 0)
        self.assertTrue(
            all(
                (cost := self.repo.get_skill(skill_id).mp_cost_value) is not None and cost <= 100
                for skill_id in ids
            )
        )

    def test_explicit_ailment_filter_is_case_insensitive(self):
        ids = structured_skill_ids(self.repo, SkillFilters(ailments=("tUmBlE",)))
        self.assertGreater(len(ids), 0)
        self.assertTrue(
            all(
                any(ailment.casefold() == "tumble" for ailment in self.repo.get_skill(skill_id).ailments)
                for skill_id in ids
            )
        )

    def test_skill_type_and_damage_type_filters(self):
        active_ids = structured_skill_ids(self.repo, SkillFilters(skill_types=("active",)))
        self.assertIn("sub_weapon_skills/assassin_skills/assassin-stab", active_ids)
        self.assertTrue(
            all(self.repo.get_skill(skill_id).skill_type.casefold() == "active" for skill_id in active_ids)
        )

        magic_ids = structured_skill_ids(self.repo, SkillFilters(damage_types=("magic",)))
        self.assertIn("weapon_class_skills/magic_skills/magic-finale", magic_ids)
        self.assertTrue(
            all(self.repo.get_skill(skill_id).damage_type.casefold() == "magic" for skill_id in magic_ids)
        )

    def test_weapon_filter_uses_explicit_skill_or_tree_restrictions(self):
        ids = structured_skill_ids(self.repo, SkillFilters(weapons=("DAGGER",)))
        self.assertIn("sub_weapon_skills/assassin_skills/arcane-strike", ids)

    def test_required_level_max_excludes_unknown_levels(self):
        ids = structured_skill_ids(self.repo, SkillFilters(required_level_max=15))
        self.assertGreater(len(ids), 0)
        self.assertTrue(
            all(
                (level := self.repo.get_skill(skill_id).required_level) is not None and level <= 15
                for skill_id in ids
            )
        )


if __name__ == "__main__":
    unittest.main()
