from __future__ import annotations

from pathlib import Path
import unittest

from toram_skills.lexical_search import lexical_search
from toram_skills.repository import SkillRepository
from toram_skills.search_documents import build_search_documents


ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "coryn_data" / "database" / "skills.sqlite"


class SkillLexicalSearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = SkillRepository(DATABASE)

    def tearDown(self) -> None:
        self.repo.close()

    def _skill(self, tree_id: str, name: str):
        return self.repo.get_skill_by_name(tree_id, name)

    def _evidence_text(self, document_id: str) -> str:
        row = self.repo.connection.execute(
            "SELECT text FROM skill_search_documents WHERE id = ?",
            (document_id,),
        ).fetchone()
        self.assertIsNotNone(row)
        return str(row["text"])

    def test_finale_documents_are_skill_and_section_bounded(self):
        tree = self.repo.get_tree("weapon_class_skills/magic_skills")
        finale = self._skill(tree.id, "MAGIC: FINALE")

        docs = build_search_documents(tree, finale)

        self.assertGreater(len(docs), 0)
        self.assertEqual(docs[0].id, f"{finale.id}#summary")
        self.assertEqual(docs[0].kind, "summary")
        self.assertTrue(all(doc.skill_id == finale.id for doc in docs))
        self.assertTrue(all(doc.text.strip() for doc in docs))
        self.assertEqual(len({doc.id for doc in docs}), len(docs))

    def test_fts_finds_exact_skill_name(self):
        hits = lexical_search(self.repo, "Magic Finale", limit=5)
        self.assertGreater(len(hits), 0)
        self.assertEqual(
            hits[0].skill_id,
            "weapon_class_skills/magic_skills/magic-finale",
        )
        self.assertEqual(hits[0].channels[0].channel, "lexical")
        self.assertEqual(hits[0].channels[0].rank, 1)

    def test_fts_finds_source_backed_toram_terms(self):
        cases = (
            (
                "Tumble",
                self._skill("weapon_class_skills/magic_skills", "MAGIC: IMPACT").id,
                "tumble",
            ),
            (
                "Venom",
                self._skill("sub_weapon_skills/assassin_skills", "VENOM INJECTION").id,
                "venom",
            ),
            (
                "AMPR",
                self._skill("sub_weapon_skills/assassin_skills", "ARCANE STRIKE").id,
                "ampr",
            ),
            (
                "remaining MP",
                self._skill("sub_weapon_skills/assassin_skills", "ARCANE STRIKE").id,
                "remaining mp",
            ),
        )
        for query, expected_id, evidence_term in cases:
            with self.subTest(query=query):
                hits = lexical_search(self.repo, query, limit=100)
                by_id = {hit.skill_id: hit for hit in hits}
                self.assertIn(expected_id, by_id)
                evidence_id = by_id[expected_id].evidence_document_ids[0]
                self.assertIn(evidence_term, self._evidence_text(evidence_id).casefold())

    def test_fts_query_syntax_is_not_user_controlled(self):
        hits = lexical_search(self.repo, '\" OR * NEAR(', limit=5)
        self.assertIsInstance(hits, tuple)

    def test_eligible_skill_ids_restrict_results(self):
        impact_id = self._skill("weapon_class_skills/magic_skills", "MAGIC: IMPACT").id
        hits = lexical_search(
            self.repo,
            "Tumble",
            eligible_skill_ids=(impact_id,),
            limit=10,
        )
        self.assertEqual([hit.skill_id for hit in hits], [impact_id])

    def test_search_document_manifest_is_stored(self):
        manifest = self.repo.get_metadata("search_document_manifest_hash")
        self.assertIsNotNone(manifest)
        self.assertEqual(len(manifest), 64)


if __name__ == "__main__":
    unittest.main()
