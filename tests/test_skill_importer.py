from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest

from toram_skills.importer import import_skill_corpus
from toram_skills.models import ImportReport, ParseIssue
from toram_skills.repository import SkillRepository
from toram_skills.source_inventory import discover_skill_sources, source_manifest_hash


ROOT = Path(__file__).resolve().parents[1]


class SkillImporterTests(unittest.TestCase):
    def _write_valid_corpus(self, root: Path) -> Path:
        raw_root = root / "raw_skills"
        (raw_root / "assist_skills").mkdir(parents=True)
        (raw_root / "assist_skills" / "test_skills.txt").write_text(
            """Category: Test Skills

==================================================
SKILL: TEST SKILL
==================================================

Category: Test Skills
Tier: I
Required Level: Level 1

MP Cost: 100
Damage Type: Magic
Description: Test description.
""",
            encoding="utf-8",
        )
        return raw_root

    def test_import_builds_database_and_manifest_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw_root = self._write_valid_corpus(root)
            db_path = root / "database" / "skills.sqlite"

            report = import_skill_corpus(raw_root, db_path)

            self.assertTrue(report.is_valid)
            self.assertEqual(
                report.manifest_hash,
                source_manifest_hash(discover_skill_sources(raw_root)),
            )
            with SkillRepository(db_path) as repo:
                self.assertEqual(repo.count_trees(), 1)
                self.assertEqual(repo.count_skills(), 1)
                self.assertEqual(repo.get_metadata("schema_version"), "1")
                self.assertEqual(repo.get_metadata("source_manifest_hash"), report.manifest_hash)
                self.assertEqual(repo.get_metadata("source_file_count"), "1")

    def test_failed_import_does_not_replace_existing_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw_root = root / "raw_skills"
            (raw_root / "assist_skills").mkdir(parents=True)
            (raw_root / "assist_skills" / "bad.txt").write_text(
                "Category: Bad Skills\nNo registered deterministic format here.\n",
                encoding="utf-8",
            )
            db_path = root / "skills.sqlite"
            db_path.write_bytes(b"keep-me")

            report = import_skill_corpus(raw_root, db_path)

            self.assertFalse(report.is_valid)
            self.assertIn("unsupported_source_format", report.error_codes)
            self.assertEqual(db_path.read_bytes(), b"keep-me")

    def test_real_corpus_import_has_zero_errors_and_accounts_for_every_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "skills.sqlite"
            report = import_skill_corpus(ROOT / "raw_skills", db_path)

            self.assertEqual(report.errors, ())
            self.assertEqual(report.files_discovered, report.trees_created)
            self.assertEqual(report.skill_blocks_discovered, report.skills_created)

            with SkillRepository(db_path) as repo:
                self.assertEqual(repo.count_trees(), report.trees_created)
                self.assertEqual(repo.count_skills(), report.skills_created)
                self.assertEqual(repo.get_metadata("source_manifest_hash"), report.manifest_hash)

                expected = (
                    ("weapon_class_skills/magic_skills/magic-finale", "MAGIC: FINALE", 1600),
                    ("sub_weapon_skills/assassin_skills/assassin-stab", "ASSASSIN STAB", 300),
                    ("assist_skills/minstrel_skills/healing-song", "Healing Song", 100),
                    ("other_skill_trees/alchemist_skills/process-material", "PROCESS MATERIAL", None),
                )
                for skill_id, name, mp_cost in expected:
                    skill = repo.get_skill(skill_id)
                    self.assertEqual(skill.name, name)
                    self.assertEqual(skill.mp_cost_value, mp_cost)
                    self.assertTrue(skill.raw_text.strip())

    def test_report_rendering_and_json_are_deterministic(self):
        from toram_skills.report import render_import_report, report_to_json

        report = ImportReport(
            files_discovered=2,
            trees_created=2,
            skill_blocks_discovered=3,
            skills_created=3,
            manifest_hash="abc123",
            issues=(
                ParseIssue("warning", "zeta", "b.txt", "B", "second"),
                ParseIssue("error", "alpha", "a.txt", None, "first"),
            ),
        )

        text = render_import_report(report)
        self.assertIn("Files discovered: 2", text)
        self.assertIn("Errors: 1", text)
        self.assertIn("Warnings: 1", text)
        self.assertLess(text.index("ERROR alpha"), text.index("WARNING zeta"))

        payload = json.loads(report_to_json(report))
        self.assertEqual(payload["manifest_hash"], "abc123")
        self.assertEqual([issue["code"] for issue in payload["issues"]], ["alpha", "zeta"])

    def test_build_cli_returns_zero_and_writes_json_report_for_valid_import(self):
        import build_skills

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw_root = self._write_valid_corpus(root)
            db_path = root / "db" / "skills.sqlite"
            json_path = root / "report.json"
            stdout = StringIO()
            with redirect_stdout(stdout):
                code = build_skills.main([
                    "--source", str(raw_root),
                    "--database", str(db_path),
                    "--json-report", str(json_path),
                ])

            self.assertEqual(code, 0)
            self.assertTrue(db_path.is_file())
            self.assertTrue(json_path.is_file())
            self.assertIn("Errors: 0", stdout.getvalue())

    def test_build_cli_returns_one_for_import_errors(self):
        import build_skills

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw_root = root / "raw_skills"
            (raw_root / "assist_skills").mkdir(parents=True)
            (raw_root / "assist_skills" / "bad.txt").write_text(
                "Category: Bad Skills\nNo registered deterministic format here.\n",
                encoding="utf-8",
            )
            stdout = StringIO()
            with redirect_stdout(stdout):
                code = build_skills.main([
                    "--source", str(raw_root),
                    "--database", str(root / "skills.sqlite"),
                ])
            self.assertEqual(code, 1)
            self.assertIn("Errors: 1", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
