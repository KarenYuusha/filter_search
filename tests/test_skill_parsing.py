from pathlib import Path
import unittest

from toram_skills.source_inventory import SkillSource, discover_skill_sources
from toram_skills.parsing import parse_skill_file, parse_standard_skill_file


ROOT = Path(__file__).resolve().parents[1]


class SkillParsingTests(unittest.TestCase):
    def _source(self, path: str):
        return next(
            source
            for source in discover_skill_sources(ROOT / "raw_skills")
            if source.relative_path == path
        )

    def test_battle_blocks_preserve_raw_source(self):
        parsed = parse_standard_skill_file(
            self._source("assist_skills/battle_skills.txt")
        )
        magic_up = next(skill for skill in parsed.skills if skill.name == "MAGIC UP")
        self.assertEqual(magic_up.id, "assist_skills/battle_skills/magic-up")
        self.assertIn("MATK Increase", magic_up.raw_text)
        self.assertTrue(parsed.tree.general_text.startswith("Category: Battle Skills"))
        self.assertGreater(parsed.discovered_skill_blocks, 0)

    def test_magic_source_details_are_preserved_as_section(self):
        parsed = parse_standard_skill_file(
            self._source("weapon_class_skills/magic_skills.txt")
        )
        arrows = next(skill for skill in parsed.skills if skill.name == "MAGIC: ARROWS")
        self.assertIn("source details", [section.normalized_label for section in arrows.sections])
        self.assertIn("Base Skill Multiplier", arrows.raw_text)

    def test_duplicate_normalized_name_is_reported_without_overwrite(self):
        text = """Category: Test Skills

==================================================
SKILL: TEST SKILL
==================================================

Description:
First.

==================================================
SKILL: Test Skill
==================================================

Description:
Second.
"""
        source = SkillSource(
            path=Path("test.txt"),
            relative_path="assist_skills/test.txt",
            tree_group="assist",
            text=text,
            declared_category="Test Skills",
            marker_count=2,
        )
        parsed = parse_standard_skill_file(source)
        self.assertEqual(len(parsed.skills), 2)
        self.assertIn("duplicate_skill_name", {issue.code for issue in parsed.issues})

    def test_minstrel_uses_embedded_tier_roster(self):
        parsed = parse_skill_file(self._source("assist_skills/minstrel_skills.txt"))
        names = [skill.name for skill in parsed.skills]
        self.assertIn("Healing Song", names)
        self.assertIn("Beat Blast", names)
        self.assertIn("Battle Anthem", names)
        self.assertEqual(next(s for s in parsed.skills if s.name == "Healing Song").tier, 1)
        self.assertEqual(next(s for s in parsed.skills if s.name == "Battle Anthem").tier, 3)
        self.assertIn("Acoustic Buff", parsed.tree.general_text)
        self.assertEqual(parsed.tree.tier_requirements, ((1, None), (2, 60), (3, 120)))
        self.assertIn("bow", {item.casefold() for item in parsed.tree.weapon_restrictions})

    def test_every_real_source_has_registered_deterministic_strategy(self):
        parsed = [parse_skill_file(s) for s in discover_skill_sources(ROOT / "raw_skills")]
        unhandled = [
            result.tree.source_file
            for result in parsed
            if any(issue.code == "unsupported_source_format" for issue in result.issues)
        ]
        self.assertEqual(unhandled, [])


if __name__ == "__main__":
    unittest.main()
