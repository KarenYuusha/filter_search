from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "toram_discord" / "skill_icons.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("skill_icons_under_test", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load skill_icons module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SkillIconCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = _load_module()
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _icon(self, folder: str, name: str) -> Path:
        directory = self.root / folder
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / name
        path.write_bytes(b"png")
        return path

    def test_normalize_icon_key_ignores_punctuation_spacing_and_underscore(self):
        normalize = self.module.normalize_icon_key
        self.assertEqual(normalize("MAGIC: FINALE"), "magicfinale")
        self.assertEqual(normalize("Magic_ Finale"), "magicfinale")
        self.assertEqual(normalize("  Shield-Bash  "), "shieldbash")

    def test_resolves_normalized_skill_inside_tree_folder(self):
        expected = self._icon("Shield", "Shield Bash.png")
        catalog = self.module.SkillIconCatalog(self.root)
        self.assertEqual(
            catalog.resolve("Shield Skills", "SHIELD: BASH"),
            expected,
        )

    def test_tree_folder_aliases_cover_magic_warrior_and_blacksmith(self):
        magic = self._icon("MagicBlade", "Enchant Sword.png")
        smith = self._icon("Smith", "Create Equipment.png")
        catalog = self.module.SkillIconCatalog(self.root)
        self.assertEqual(
            catalog.resolve("Magic Warrior Skills", "Enchant Sword"),
            magic,
        )
        self.assertEqual(
            catalog.resolve("Blacksmith Skills", "Create Equipment"),
            smith,
        )

    def test_unique_global_fallback_is_allowed(self):
        expected = self._icon("UnexpectedFolder", "Only Here.png")
        catalog = self.module.SkillIconCatalog(self.root)
        self.assertEqual(
            catalog.resolve("Unknown Skills", "Only Here"),
            expected,
        )

    def test_ambiguous_global_fallback_returns_none(self):
        self._icon("One", "Same Skill.png")
        self._icon("Two", "Same-Skill.png")
        catalog = self.module.SkillIconCatalog(self.root)
        self.assertIsNone(catalog.resolve("Unknown Skills", "Same Skill"))

    def test_missing_root_returns_none(self):
        catalog = self.module.SkillIconCatalog(self.root / "missing")
        self.assertIsNone(catalog.resolve("Shield Skills", "Shield Bash"))

    def test_catalog_is_cached_after_first_resolve(self):
        expected = self._icon("Shield", "Shield Bash.png")
        catalog = self.module.SkillIconCatalog(self.root)
        self.assertEqual(catalog.resolve("Shield Skills", "Shield Bash"), expected)
        self._icon("Shield", "Added Later.png")
        self.assertIsNone(catalog.resolve("Shield Skills", "Added Later"))

    def test_current_checked_in_representative_icons_resolve(self):
        icon_root = ROOT / "coryn_skill_icons"
        if not icon_root.is_dir():
            self.skipTest("checked-in icon corpus is not present in this local harness")
        catalog = self.module.SkillIconCatalog(icon_root)
        self.assertEqual(
            catalog.resolve("Shield Skills", "Shield Bash"),
            icon_root / "Shield" / "Shield Bash.png",
        )
        self.assertEqual(
            catalog.resolve("Shield Skills", "Shield Cannon"),
            icon_root / "Shield" / "Shield Cannon.png",
        )
        self.assertEqual(
            catalog.resolve("Magic Skills", "MAGIC: FINALE"),
            icon_root / "Magic" / "Magic_ Finale.png",
        )

    def test_real_skill_corpus_icon_lookup_never_raises(self):
        icon_root = ROOT / "coryn_skill_icons"
        database = ROOT / "coryn_data" / "database" / "skills.sqlite"
        if not icon_root.is_dir() or not database.is_file():
            self.skipTest("real skill/icon corpus is not present in this local harness")

        from toram_skills.repository import SkillRepository

        catalog = self.module.SkillIconCatalog(icon_root)
        resolved = 0
        total = 0
        with SkillRepository(database) as repository:
            for tree_name in repository.list_tree_names():
                trees = repository.resolve_tree_name(tree_name)
                for tree in trees:
                    for skill in repository.list_skills_in_tree(tree.id):
                        total += 1
                        if catalog.resolve(tree.name, skill.name) is not None:
                            resolved += 1
        self.assertGreater(total, 0)
        self.assertGreater(resolved, 0)


if __name__ == "__main__":
    unittest.main()
