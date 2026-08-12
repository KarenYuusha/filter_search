from __future__ import annotations

import math
from pathlib import Path
import shutil
import tempfile
import unittest

from toram_skills.repository import SkillRepository
from toram_skills.semantic_search import (
    EmbeddingIndexError,
    EmbeddingUnavailable,
    SemanticSkillIndex,
    build_embedding_index,
)


ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "coryn_data" / "database" / "skills.sqlite"
SHADOW_WALK_ID = "sub_weapon_skills/assassin_skills/shadow-walk"


class KeywordProvider:
    def __init__(self, model_name: str = "fake-keyword") -> None:
        self.model_name = model_name

    def embed(self, texts):
        vectors = []
        for text in texts:
            folded = text.casefold()
            if folded == "avoid attacks" or "shadow walk" in folded:
                vectors.append((1.0, 0.0))
            elif "poison" in folded or "venom" in folded:
                vectors.append((0.0, 1.0))
            else:
                vectors.append((0.1, 0.9))
        return tuple(vectors)


class BrokenProvider:
    def __init__(self, mode: str, model_name: str = "broken") -> None:
        self.mode = mode
        self.model_name = model_name

    def embed(self, texts):
        if self.mode == "unavailable":
            raise EmbeddingUnavailable("offline")
        vectors = []
        for index, _ in enumerate(texts):
            if self.mode == "zero":
                vectors.append((0.0, 0.0))
            elif self.mode == "nan":
                vectors.append((math.nan, 1.0))
            elif self.mode == "mixed-dim":
                vectors.append((1.0, 0.0) if index == 0 else (1.0, 0.0, 0.0))
            else:
                raise AssertionError(self.mode)
        return tuple(vectors)


class SkillSemanticSearchTests(unittest.TestCase):
    def _copy_database(self, root: Path) -> Path:
        copied = root / "skills.sqlite"
        shutil.copy2(DATABASE, copied)
        return copied

    def test_build_and_search_semantic_index_with_fake_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = self._copy_database(Path(tmp))
            provider = KeywordProvider()
            with SkillRepository(db_path) as repo:
                expected = repo.connection.execute(
                    "SELECT COUNT(*) FROM skill_search_documents"
                ).fetchone()[0]
                self.assertEqual(build_embedding_index(repo, provider, batch_size=64), expected)
                index = SemanticSkillIndex.from_repository(repo, provider)
                hits = index.search("avoid attacks", limit=3)

            self.assertGreater(len(hits), 0)
            self.assertEqual(hits[0].skill_id, SHADOW_WALK_ID)
            self.assertEqual(hits[0].channels[0].channel, "semantic")
            self.assertEqual(hits[0].channels[0].rank, 1)
            self.assertTrue(hits[0].evidence_document_ids)

    def test_eligible_skill_ids_restrict_semantic_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = self._copy_database(Path(tmp))
            provider = KeywordProvider()
            with SkillRepository(db_path) as repo:
                build_embedding_index(repo, provider)
                index = SemanticSkillIndex.from_repository(repo, provider)
                hits = index.search(
                    "avoid attacks",
                    eligible_skill_ids=(SHADOW_WALK_ID,),
                    limit=5,
                )
            self.assertEqual([hit.skill_id for hit in hits], [SHADOW_WALK_ID])

    def test_invalid_vectors_fail_closed(self):
        for mode in ("zero", "nan", "mixed-dim"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                db_path = self._copy_database(Path(tmp))
                with SkillRepository(db_path) as repo:
                    with self.assertRaises(EmbeddingIndexError):
                        build_embedding_index(repo, BrokenProvider(mode), batch_size=64)
                    self.assertEqual(
                        repo.connection.execute(
                            "SELECT COUNT(*) FROM skill_embedding_vectors"
                        ).fetchone()[0],
                        0,
                    )

    def test_failed_rebuild_keeps_previous_valid_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = self._copy_database(Path(tmp))
            provider = KeywordProvider("stable-model")
            with SkillRepository(db_path) as repo:
                count = build_embedding_index(repo, provider)
                before = repo.connection.execute(
                    "SELECT COUNT(*) FROM skill_embedding_vectors"
                ).fetchone()[0]
                before_model = repo.get_metadata("embedding_model")
                with self.assertRaises(EmbeddingUnavailable):
                    build_embedding_index(repo, BrokenProvider("unavailable", "new-model"))
                after = repo.connection.execute(
                    "SELECT COUNT(*) FROM skill_embedding_vectors"
                ).fetchone()[0]
                self.assertEqual(after, before)
                self.assertEqual(after, count)
                self.assertEqual(repo.get_metadata("embedding_model"), before_model)

    def test_stale_document_manifest_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = self._copy_database(Path(tmp))
            provider = KeywordProvider()
            with SkillRepository(db_path) as repo:
                build_embedding_index(repo, provider)
                repo.set_metadata("search_document_manifest_hash", "stale")
                with self.assertRaises(EmbeddingIndexError):
                    SemanticSkillIndex.from_repository(repo, provider)

    def test_model_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = self._copy_database(Path(tmp))
            with SkillRepository(db_path) as repo:
                build_embedding_index(repo, KeywordProvider("model-a"))
                with self.assertRaises(EmbeddingIndexError):
                    SemanticSkillIndex.from_repository(repo, KeywordProvider("model-b"))

    def test_invalid_query_vector_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = self._copy_database(Path(tmp))
            provider = KeywordProvider()
            with SkillRepository(db_path) as repo:
                build_embedding_index(repo, provider)
                index = SemanticSkillIndex.from_repository(repo, BrokenProvider("zero", provider.model_name))
                with self.assertRaises(EmbeddingIndexError):
                    index.search("anything", limit=3)


if __name__ == "__main__":
    unittest.main()
