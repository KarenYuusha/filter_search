from pathlib import Path
import tempfile
import unittest

from toram_skills.source_inventory import discover_skill_sources, source_manifest_hash


class SkillSourceInventoryTests(unittest.TestCase):
    def test_discovers_supported_groups_in_stable_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "assist_skills").mkdir()
            (root / "weapon_class_skills").mkdir()
            (root / "assist_skills" / "battle_skills.txt").write_text(
                "Category: Battle Skills\nSKILL: MAGIC UP\n", encoding="utf-8"
            )
            (root / "weapon_class_skills" / "magic_skills.txt").write_text(
                "Category: Magic Skills\nSKILL: MAGIC: ARROWS\n", encoding="utf-8"
            )

            sources = discover_skill_sources(root)

            self.assertEqual(
                [source.relative_path for source in sources],
                ["assist_skills/battle_skills.txt", "weapon_class_skills/magic_skills.txt"],
            )
            self.assertEqual(sources[0].tree_group, "assist")
            self.assertEqual(sources[1].tree_group, "weapon-class")
            self.assertEqual(sources[0].declared_category, "Battle Skills")
            self.assertEqual(sources[0].marker_count, 1)
            self.assertEqual(source_manifest_hash(sources), source_manifest_hash(sources))

    def test_rejects_files_outside_known_groups(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "unknown").mkdir()
            (root / "unknown" / "x.txt").write_text("x", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Unsupported raw skill group: unknown"):
                discover_skill_sources(root)

    def test_real_corpus_is_nonempty_and_all_four_groups_exist(self):
        raw_root = Path(__file__).resolve().parents[1] / "raw_skills"
        sources = discover_skill_sources(raw_root)
        self.assertGreater(len(sources), 0)
        self.assertTrue(all(source.text.strip() for source in sources))
        self.assertEqual(
            {source.tree_group for source in sources},
            {"assist", "other", "sub-weapon", "weapon-class"},
        )


if __name__ == "__main__":
    unittest.main()
