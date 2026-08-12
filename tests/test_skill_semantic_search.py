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
    def __init__(
        self,
        model_name: str = "fake-keyword",
        *,
        provider_name: str = "fake",
        config_id: str = "default",
    ) -> None:
        self.provider_name = provider_name
        self.model_name = model_name
        self.config_id = config_id
        self.document_calls: list[tuple[str, ...]] = []
        self.query_calls: list[str] = []
        self.legacy_calls: list[tuple[str, ...]] = []

    @staticmethod
    def _vector(text: str) -> tuple[float, float]:
        folded = text.casefold()
        if folded == "avoid attacks" or "shadow walk" in folded:
            return (1.0, 0.0)
        if "poison" in folded or "venom" in folded:
            return (0.0, 1.0)
        return (0.1, 0.9)

    def embed_documents(self, texts):
        values = tuple(str(text) for text in texts)
        self.document_calls.append(values)
        return tuple(self._vector(text) for text in values)

    def embed_query(self, text):
        value = str(text)
        self.query_calls.append(value)
        return self._vector(value)

    # Temporary compatibility path that lets the RED tests exercise the old
    # implementation and fail on the intended method-separation assertions.
    def embed(self, texts):
        values = tuple(str(text) for text in texts)
        self.legacy_calls.append(values)
        return tuple(self._vector(text) for text in values)


class BrokenProvider(KeywordProvider):
    def __init__(
        self,
        mode: str,
        model_name: str = "broken",
        *,
        provider_name: str = "fake",
        config_id: str = "default",
    ) -> None:
        super().__init__(
            model_name,
            provider_name=provider_name,
            config_id=config_id,
        )
        self.mode = mode

    def _broken_vectors(self, texts):
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

    def embed_documents(self, texts):
        values = tuple(str(text) for text in texts)
        self.document_calls.append(values)
        return self._broken_vectors(values)

    def embed_query(self, text):
        value = str(text)
        self.query_calls.append(value)
        return self._broken_vectors((value,))[0]

    def embed(self, texts):
        values = tuple(str(text) for text in texts)
        self.legacy_calls.append(values)
        return self._broken_vectors(values)


class SkillSemanticSearchTests(unittest.TestCase):
    def _copy_database(self, root: Path) -> Path:
        copied = root / "skills.sqlite"
        shutil.copy2(DATABASE, copied)
        return copied

    @staticmethod
    def _embedding_state(repo: SkillRepository) -> tuple[int, str | None, str | None, str | None]:
        return (
            int(
                repo.connection.execute(
                    "SELECT COUNT(*) FROM skill_embedding_vectors"
                ).fetchone()[0]
            ),
            repo.get_metadata("embedding_provider"),
            repo.get_metadata("embedding_model"),
            repo.get_metadata("embedding_config_id"),
        )

    def test_build_uses_document_encoder_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = self._copy_database(Path(tmp))
            provider = KeywordProvider()
            with SkillRepository(db_path) as repo:
                build_embedding_index(repo, provider, batch_size=64)

            self.assertGreater(len(provider.document_calls), 0)
            self.assertEqual(provider.query_calls, [])
            self.assertEqual(provider.legacy_calls, [])

    def test_search_uses_query_encoder_only_after_index_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = self._copy_database(Path(tmp))
            provider = KeywordProvider()
            with SkillRepository(db_path) as repo:
                build_embedding_index(repo, provider, batch_size=64)
                provider.document_calls.clear()
                provider.legacy_calls.clear()
                index = SemanticSkillIndex.from_repository(repo, provider)
                hits = index.search("avoid attacks", limit=3)

            self.assertGreater(len(hits), 0)
            self.assertEqual(provider.document_calls, [])
            self.assertEqual(provider.query_calls, ["avoid attacks"])
            self.assertEqual(provider.legacy_calls, [])

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

    def test_build_persists_provider_model_and_config_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = self._copy_database(Path(tmp))
            provider = KeywordProvider(
                "model-a",
                provider_name="provider-a",
                config_id="query-document-v1",
            )
            with SkillRepository(db_path) as repo:
                build_embedding_index(repo, provider)
                self.assertEqual(repo.get_metadata("embedding_provider"), "provider-a")
                self.assertEqual(repo.get_metadata("embedding_model"), "model-a")
                self.assertEqual(
                    repo.get_metadata("embedding_config_id"),
                    "query-document-v1",
                )

    def test_provider_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = self._copy_database(Path(tmp))
            with SkillRepository(db_path) as repo:
                build_embedding_index(
                    repo,
                    KeywordProvider("same-model", provider_name="provider-a"),
                )
                with self.assertRaises(EmbeddingIndexError):
                    SemanticSkillIndex.from_repository(
                        repo,
                        KeywordProvider("same-model", provider_name="provider-b"),
                    )

    def test_config_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = self._copy_database(Path(tmp))
            with SkillRepository(db_path) as repo:
                build_embedding_index(
                    repo,
                    KeywordProvider("same-model", config_id="config-a"),
                )
                with self.assertRaises(EmbeddingIndexError):
                    SemanticSkillIndex.from_repository(
                        repo,
                        KeywordProvider("same-model", config_id="config-b"),
                    )

    def test_blank_provider_identity_is_rejected_before_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = self._copy_database(Path(tmp))
            with SkillRepository(db_path) as repo:
                before = self._embedding_state(repo)
                with self.assertRaises(EmbeddingIndexError):
                    build_embedding_index(
                        repo,
                        KeywordProvider(provider_name="   "),
                    )
                self.assertEqual(self._embedding_state(repo), before)

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
                    before = self._embedding_state(repo)
                    with self.assertRaises(EmbeddingIndexError):
                        build_embedding_index(repo, BrokenProvider(mode), batch_size=64)
                    self.assertEqual(self._embedding_state(repo), before)

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
                before_provider = repo.get_metadata("embedding_provider")
                before_config = repo.get_metadata("embedding_config_id")
                with self.assertRaises(EmbeddingUnavailable):
                    build_embedding_index(repo, BrokenProvider("unavailable", "new-model"))
                after = repo.connection.execute(
                    "SELECT COUNT(*) FROM skill_embedding_vectors"
                ).fetchone()[0]
                self.assertEqual(after, before)
                self.assertEqual(after, count)
                self.assertEqual(repo.get_metadata("embedding_model"), before_model)
                self.assertEqual(repo.get_metadata("embedding_provider"), before_provider)
                self.assertEqual(repo.get_metadata("embedding_config_id"), before_config)

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
                index = SemanticSkillIndex.from_repository(
                    repo,
                    BrokenProvider("zero", provider.model_name),
                )
                with self.assertRaises(EmbeddingIndexError):
                    index.search("anything", limit=3)


if __name__ == "__main__":
    unittest.main()
