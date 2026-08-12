from __future__ import annotations

import json
from pathlib import Path
import unittest
from unittest.mock import patch

from toram_skills.hybrid_search import (
    FusionConfig,
    HybridSkillSearcher,
    fuse_ranked_hits,
    tune_fusion,
)
from toram_skills.repository import SkillRepository
from toram_skills.search_models import ChannelScore, SkillFilters, SkillSearchHit
from toram_skills.semantic_search import EmbeddingUnavailable


ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "coryn_data" / "database" / "skills.sqlite"
GOLDEN = ROOT / "tests" / "fixtures" / "skill_retrieval_golden.json"


class FakeSemanticIndex:
    def __init__(self, hits=(), error=None):
        self.hits = tuple(hits)
        self.error = error
        self.calls = []

    def search(self, query, *, eligible_skill_ids=None, limit=20):
        self.calls.append((query, eligible_skill_ids, limit))
        if self.error is not None:
            raise self.error
        eligible = None if eligible_skill_ids is None else set(eligible_skill_ids)
        hits = self.hits
        if eligible is not None:
            hits = tuple(hit for hit in hits if hit.skill_id in eligible)
        return hits[:limit]


def _semantic_hit(skill_id: str, rank: int, score: float = 0.8) -> SkillSearchHit:
    return SkillSearchHit(
        skill_id=skill_id,
        score=score,
        channels=(ChannelScore("semantic", rank, score),),
        evidence_document_ids=(f"{skill_id}#summary",),
    )


def _lexical_hit(skill_id: str, rank: int, score: float = 5.0) -> SkillSearchHit:
    return SkillSearchHit(
        skill_id=skill_id,
        score=score,
        channels=(ChannelScore("lexical", rank, score),),
        evidence_document_ids=(f"{skill_id}#summary",),
    )


class GoldenFixtureTests(unittest.TestCase):
    def test_golden_fixture_has_required_distribution_and_valid_ids(self):
        payload = json.loads(GOLDEN.read_text(encoding="utf-8"))
        cases = payload["cases"]
        self.assertGreaterEqual(len(cases), 60)
        self.assertEqual(len({case["id"] for case in cases}), len(cases))
        counts = {kind: 0 for kind in ("exact", "lexical", "semantic", "combined")}
        with SkillRepository(DATABASE) as repo:
            existing = {
                str(row["id"])
                for row in repo.connection.execute("SELECT id FROM skills")
            }
        for case in cases:
            self.assertIn(case["kind"], counts)
            counts[case["kind"]] += 1
            self.assertTrue(case["query"].strip())
            self.assertTrue(case["expected_skill_ids"])
            self.assertGreaterEqual(case["top_k"], 1)
            for skill_id in case["expected_skill_ids"]:
                self.assertIn(skill_id, existing, msg=f"{case['id']}: {skill_id}")
        self.assertGreaterEqual(counts["exact"], 15)
        self.assertGreaterEqual(counts["lexical"], 15)
        self.assertGreaterEqual(counts["semantic"], 20)
        self.assertGreaterEqual(counts["combined"], 10)


class HybridSkillSearcherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = SkillRepository(DATABASE)

    def tearDown(self) -> None:
        self.repo.close()

    def test_exact_canonical_name_bypasses_lexical_and_semantic(self):
        semantic = FakeSemanticIndex()
        searcher = HybridSkillSearcher(self.repo, semantic_index=semantic)
        with patch("toram_skills.hybrid_search.lexical_search") as lexical:
            hits = searcher.search("magic: finale", limit=5)
        self.assertEqual(
            [hit.skill_id for hit in hits],
            ["weapon_class_skills/magic_skills/magic-finale"],
        )
        lexical.assert_not_called()
        self.assertEqual(semantic.calls, [])

    def test_exact_match_that_violates_filters_does_not_bypass(self):
        semantic = FakeSemanticIndex()
        searcher = HybridSkillSearcher(self.repo, semantic_index=semantic)
        filters = SkillFilters(tree_ids=("sub_weapon_skills/assassin_skills",))
        with patch("toram_skills.hybrid_search.lexical_search", return_value=()) as lexical:
            hits = searcher.search("magic: finale", filters=filters, limit=5)
        self.assertEqual(hits, ())
        lexical.assert_called_once()
        self.assertEqual(len(semantic.calls), 1)

    def test_typed_filters_restrict_both_channels(self):
        impact = "weapon_class_skills/magic_skills/magic-impact"
        unrelated = "sub_weapon_skills/assassin_skills/shadow-walk"
        semantic = FakeSemanticIndex((_semantic_hit(unrelated, 1), _semantic_hit(impact, 2)))
        searcher = HybridSkillSearcher(self.repo, semantic_index=semantic)
        filters = SkillFilters(tree_ids=("weapon_class_skills/magic_skills",), tiers=(3,))
        with patch(
            "toram_skills.hybrid_search.lexical_search",
            return_value=(_lexical_hit(impact, 1),),
        ) as lexical:
            hits = searcher.search("Tumble", filters=filters, limit=5)

        self.assertEqual([hit.skill_id for hit in hits], [impact])
        eligible = semantic.calls[0][1]
        self.assertIsNotNone(eligible)
        self.assertIn(impact, eligible)
        self.assertNotIn(unrelated, eligible)
        self.assertEqual(lexical.call_args.kwargs["eligible_skill_ids"], eligible)

    def test_semantic_unavailable_falls_back_to_lexical(self):
        impact = "weapon_class_skills/magic_skills/magic-impact"
        semantic = FakeSemanticIndex(error=EmbeddingUnavailable("offline"))
        searcher = HybridSkillSearcher(self.repo, semantic_index=semantic)
        with patch(
            "toram_skills.hybrid_search.lexical_search",
            return_value=(_lexical_hit(impact, 1),),
        ):
            hits = searcher.search("Tumble", limit=5)
        self.assertEqual([hit.skill_id for hit in hits], [impact])
        self.assertEqual([c.channel for c in hits[0].channels], ["lexical"])

    def test_multiple_document_or_channel_hits_collapse_to_one_skill(self):
        skill_id = "sub_weapon_skills/assassin_skills/shadow-walk"
        fused = fuse_ranked_hits(
            (_lexical_hit(skill_id, 2),),
            (_semantic_hit(skill_id, 1),),
            config=FusionConfig(rrf_k=60, lexical_weight=1.0, semantic_weight=1.0),
            limit=10,
        )
        self.assertEqual(len(fused), 1)
        self.assertEqual(fused[0].skill_id, skill_id)
        self.assertEqual({channel.channel for channel in fused[0].channels}, {"lexical", "semantic"})

    def test_equal_fusion_scores_sort_by_skill_id(self):
        first = "assist_skills/battle_skills/critical-up"
        second = "weapon_class_skills/magic_skills/magic-impact"
        fused = fuse_ranked_hits(
            (_lexical_hit(second, 1), _lexical_hit(first, 1)),
            (),
            config=FusionConfig(rrf_k=60, lexical_weight=1.0, semantic_weight=1.0),
            limit=10,
        )
        self.assertEqual([hit.skill_id for hit in fused], sorted((first, second)))

    def test_no_semantic_index_is_valid_lexical_only_mode(self):
        skill_id = "assist_skills/battle_skills/critical-up"
        searcher = HybridSkillSearcher(self.repo, semantic_index=None)
        with patch(
            "toram_skills.hybrid_search.lexical_search",
            return_value=(_lexical_hit(skill_id, 1),),
        ):
            hits = searcher.search("critical rate", limit=5)
        self.assertEqual([hit.skill_id for hit in hits], [skill_id])


class FusionTuningTests(unittest.TestCase):
    def test_tuning_uses_documented_metric_and_tie_break_order(self):
        cases = (
            {"id": "one", "expected_skill_ids": ("a",)},
            {"id": "two", "expected_skill_ids": ("b",)},
        )
        lexical = {
            "one": (_lexical_hit("a", 1),),
            "two": (_lexical_hit("b", 1),),
        }
        semantic = {
            "one": (_semantic_hit("x", 1), _semantic_hit("a", 2)),
            "two": (_semantic_hit("y", 1), _semantic_hit("b", 2)),
        }
        selected = tune_fusion(cases, lexical, semantic)
        self.assertEqual(selected.semantic_weight, 0.5)
        self.assertEqual(selected.rrf_k, 20)


if __name__ == "__main__":
    unittest.main()
