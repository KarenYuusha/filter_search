from __future__ import annotations

from pathlib import Path
import unittest

from toram_skill_chat.retrieval import SkillEvidenceRetriever
from toram_skills.repository import SkillRepository
from toram_skills.search_models import ChannelScore, SkillSearchHit
from toram_skills.semantic_search import EmbeddingUnavailable


ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "coryn_data" / "database" / "skills.sqlite"


class _UnavailableSemanticIndex:
    def search(self, *args, **kwargs):
        raise EmbeddingUnavailable("offline")


class _Runtime:
    def __init__(self, index) -> None:
        self.index = index

    def get_index(self, repository):
        return self.index


class _DuplicateSearcher:
    def __init__(self, repository, semantic_index=None, **kwargs) -> None:
        self.repository = repository

    def search(self, query: str, *, limit: int):
        hard_hit = self.repository.resolve_skill_name("Hard Hit")[0]
        document_id = f"{hard_hit.id}#summary"
        return (
            SkillSearchHit(
                skill_id=hard_hit.id,
                score=1.0,
                channels=(ChannelScore("lexical", 1, 1.0),),
                evidence_document_ids=(document_id, document_id),
            ),
            SkillSearchHit(
                skill_id=hard_hit.id,
                score=0.9,
                channels=(ChannelScore("semantic", 2, 0.9),),
                evidence_document_ids=(document_id,),
            ),
        )


class SkillChatRetrievalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = SkillRepository(DATABASE)

    def tearDown(self) -> None:
        self.repo.close()

    def skill_id(self, name: str) -> str:
        values = self.repo.resolve_skill_name(name)
        self.assertEqual(len(values), 1, name)
        return values[0].id

    def test_known_skill_id_never_retrieves_unrelated_skills(self):
        hard_hit = self.skill_id("Hard Hit")
        retriever = SkillEvidenceRetriever(
            self.repo,
            semantic_runtime=_Runtime(_UnavailableSemanticIndex()),
        )

        evidence = retriever.retrieve(
            "how does Hard Hit work?",
            skill_ids=(hard_hit,),
            limit=5,
        )

        self.assertGreater(len(evidence), 0)
        self.assertTrue(all(chunk.skill_id == hard_hit for chunk in evidence))
        self.assertEqual(evidence[0].source_kind, "summary")

    def test_two_known_skill_ids_include_evidence_for_both(self):
        protection = self.skill_id("Protection")
        aegis = self.skill_id("Aegis")
        retriever = SkillEvidenceRetriever(self.repo, semantic_runtime=None)

        evidence = retriever.retrieve(
            "compare Protection and Aegis",
            skill_ids=(protection, aegis),
            limit=5,
        )

        self.assertEqual({chunk.skill_id for chunk in evidence}, {protection, aegis})

    def test_duplicate_document_ids_are_removed(self):
        retriever = SkillEvidenceRetriever(
            self.repo,
            semantic_runtime=None,
            searcher_factory=_DuplicateSearcher,
        )

        evidence = retriever.retrieve("hard hit mechanics", limit=5)

        ids = tuple(chunk.document_id for chunk in evidence)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(ids), 1)

    def test_character_budget_is_enforced(self):
        hard_hit = self.skill_id("Hard Hit")
        retriever = SkillEvidenceRetriever(self.repo, semantic_runtime=None)

        evidence = retriever.retrieve(
            "how does Hard Hit work?",
            skill_ids=(hard_hit,),
            limit=5,
            max_chars=120,
        )

        self.assertGreater(len(evidence), 0)
        self.assertLessEqual(sum(len(chunk.text) for chunk in evidence), 120)

    def test_semantic_unavailable_still_returns_lexical_evidence(self):
        retriever = SkillEvidenceRetriever(
            self.repo,
            semantic_runtime=_Runtime(_UnavailableSemanticIndex()),
        )

        evidence = retriever.retrieve("inflict tumble", limit=5)

        self.assertGreater(len(evidence), 0)
        self.assertTrue(any("tumble" in chunk.text.casefold() for chunk in evidence))

    def test_empty_evidence_is_normal_result(self):
        retriever = SkillEvidenceRetriever(self.repo, semantic_runtime=None)

        evidence = retriever.retrieve("zzzzzzzzzzzzzzzzzzzz", limit=5)

        self.assertEqual(evidence, ())


if __name__ == "__main__":
    unittest.main()
