from pathlib import Path
import unittest

from toram_skills.parsing import parse_skill_file
from toram_skills.source_inventory import discover_skill_sources


ROOT = Path(__file__).resolve().parents[1]


class SkillFieldNormalizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sources = {
            source.relative_path: source
            for source in discover_skill_sources(ROOT / "raw_skills")
        }

    def _skill(self, path: str, name: str):
        parsed = parse_skill_file(self.sources[path])
        return next(skill for skill in parsed.skills if skill.name == name)

    def test_magic_finale_common_fields(self):
        finale = self._skill("weapon_class_skills/magic_skills.txt", "MAGIC: FINALE")
        self.assertEqual(finale.tier, 4)
        self.assertEqual(finale.required_level, 150)
        self.assertEqual(finale.mp_cost_value, 1600)
        self.assertEqual(finale.mp_cost_text, "1600")
        self.assertEqual(finale.damage_type, "Magic")
        self.assertEqual(finale.cast_range_text, "12m")

    def test_assassin_stab_legacy_explicit_lines(self):
        stab = self._skill("sub_weapon_skills/assassin_skills.txt", "ASSASSIN STAB")
        self.assertEqual(stab.tier, 1)
        self.assertEqual(stab.required_level, 15)
        self.assertEqual(stab.mp_cost_value, 300)
        self.assertEqual(stab.skill_type, "Active")
        self.assertEqual(stab.damage_type, "Physical")
        self.assertIn("Skill multiplier varies", stab.raw_text)

    def test_expression_mp_cost_stays_text_only(self):
        strike = self._skill("sub_weapon_skills/assassin_skills.txt", "ARCANE STRIKE")
        self.assertIsNone(strike.mp_cost_value)
        self.assertIn("remaining MP", strike.mp_cost_text)
        self.assertEqual(strike.weapon_requirements, ("Dagger", "Scroll"))

    def test_explicit_ailment_is_normalized(self):
        impact = self._skill("weapon_class_skills/magic_skills.txt", "MAGIC: IMPACT")
        self.assertEqual(impact.ailments, ("Tumble",))

    def test_explicit_source_uncertainty_is_preserved_and_warned(self):
        skill = self._skill("other_skill_trees/alchemist_skills.txt", "MID-CLASS SYNTHESIS")
        self.assertIn("Full effect is unknown in the source.", skill.raw_text)
        self.assertIn("source_uncertainty", {issue.code for issue in skill.issues})


if __name__ == "__main__":
    unittest.main()
