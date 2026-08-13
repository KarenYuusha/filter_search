from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from toram_skill_search import SkillUnavailablePayload, run_skill_search
from toram_skill_search.runtime import SemanticRuntimeCache
from toram_skill_search.service import SkillSearchService, parse_skill_command
from toram_skills.repository import SkillRepository
from toram_skills.semantic_search import EmbeddingIndexError, EmbeddingUnavailable

ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "coryn_data" / "database" / "skills.sqlite"


class ExplodingSemanticRuntime:
    def get_index(self, repository):
        raise AssertionError("exact lookup must not initialize semantic runtime")


class FakeRepository:
    def __init__(self, skills):
        self.skills = tuple(skills)
        self.trees = {
            skill.tree_id: SimpleNamespace(id=skill.tree_id, name=f"Tree {index}")
            for index, skill in enumerate(self.skills, start=1)
        }

    def resolve_skill_name(self, name):
        return self.skills

    def get_tree(self, tree_id):
        return self.trees[tree_id]


class FakeSemanticIndex:
    def __init__(self, hits=(), error=None):
        self.hits = tuple(hits)
        self.error = error
        self.calls = []

    def search(self, query, *, eligible_skill_ids=None, limit=20):
        self.calls.append(query)
        if self.error is not None:
            raise self.error
        return self.hits[:limit]


class FixedRuntime:
    def __init__(self, index):
        self.index = index
        self.calls = 0

    def get_index(self, repository):
        self.calls += 1
        return self.index


class FakeRuntimeRepository:
    def __init__(self, path: Path, manifest: str = "manifest"):
        self.database_path = path
        self.manifest = manifest

    def get_metadata(self, key):
        if key == "search_document_manifest_hash":
            return self.manifest
        return None


class SkillCommandParserTests(unittest.TestCase):
    def test_token_bounded_case_insensitive_prefix(self):
        self.assertEqual(parse_skill_command("skill magic finale"), "magic finale")
        self.assertEqual(
            parse_skill_command("  SKILL   attack while moving  "),
            "attack while moving",
        )
        self.assertEqual(parse_skill_command("skill"), "")
        self.assertIsNone(parse_skill_command("skills magic finale"))
        self.assertIsNone(parse_skill_command("skillful magic finale"))
        self.assertIsNone(parse_skill_command("magic finale"))


class SkillSearchServiceTests(unittest.TestCase):
    def setUp(self):
        self.repo = SkillRepository(DATABASE)

    def tearDown(self):
        self.repo.close()

    def test_bare_skill_remainder_returns_help(self):
        payload = SkillSearchService(
            self.repo,
            semantic_runtime=ExplodingSemanticRuntime(),
        ).handle("")
        self.assertEqual(type(payload).__name__, "SkillHelpPayload")
        self.assertIn("skill magic finale", payload.text.casefold())

    def test_exact_canonical_name_returns_detail_without_semantic_runtime(self):
        payload = SkillSearchService(
            self.repo,
            semantic_runtime=ExplodingSemanticRuntime(),
        ).handle("magic: finale")
        self.assertEqual(type(payload).__name__, "SkillDetailPayload")
        self.assertEqual(
            payload.skill.id,
            "weapon_class_skills/magic_skills/magic-finale",
        )
        self.assertEqual(payload.tree.id, payload.skill.tree_id)

    def test_repository_alias_resolution_path_returns_detail_without_semantic_runtime(self):
        skill = SimpleNamespace(
            id="tree-one/canonical-skill",
            tree_id="tree-one",
            name="Canonical Skill",
            description="Alias target",
            game_description=None,
            tier=1,
            skill_type="Active",
            mp_cost_text="100",
            damage_type="Physical",
        )
        payload = SkillSearchService(
            FakeRepository((skill,)),
            semantic_runtime=ExplodingSemanticRuntime(),
        ).handle("legacy alias")
        self.assertEqual(type(payload).__name__, "SkillDetailPayload")
        self.assertEqual(payload.skill.id, skill.id)

    def test_multiple_exact_matches_return_results_in_repository_order(self):
        skills = (
            SimpleNamespace(
                id="tree-one/alpha",
                tree_id="tree-one",
                name="Alpha",
                description="First",
                game_description=None,
                tier=1,
                skill_type="Active",
                mp_cost_text="100",
                damage_type="Physical",
            ),
            SimpleNamespace(
                id="tree-two/alpha",
                tree_id="tree-two",
                name="Alpha",
                description="Second",
                game_description=None,
                tier=2,
                skill_type="Passive",
                mp_cost_text=None,
                damage_type=None,
            ),
        )
        payload = SkillSearchService(
            FakeRepository(skills),
            semantic_runtime=ExplodingSemanticRuntime(),
        ).handle("alpha")
        self.assertEqual(type(payload).__name__, "SkillResultsPayload")
        self.assertEqual([item.skill.id for item in payload.results], [s.id for s in skills])

    def test_free_text_returns_canonical_ranked_results(self):
        payload = SkillSearchService(self.repo, semantic_runtime=None).handle("inflict tumble")
        self.assertEqual(type(payload).__name__, "SkillResultsPayload")
        self.assertTrue(payload.results)
        self.assertTrue(
            all(item.skill.tree_id == item.tree.id for item in payload.results)
        )
        self.assertTrue(all(item.snippet for item in payload.results))

    def test_free_text_accepts_filter_like_words_as_plain_text(self):
        payload = SkillSearchService(self.repo, semantic_runtime=None).handle("sword tier 4")
        self.assertEqual(type(payload).__name__, "SkillResultsPayload")

    def test_non_exact_query_requests_semantic_runtime(self):
        runtime = FixedRuntime(None)
        SkillSearchService(
            self.repo,
            semantic_runtime=runtime,
        ).handle("attack while moving")
        self.assertEqual(runtime.calls, 1)

    def test_semantic_query_failure_retries_lexical_only(self):
        semantic = FakeSemanticIndex(error=EmbeddingUnavailable("offline"))
        payload = SkillSearchService(
            self.repo,
            semantic_runtime=FixedRuntime(semantic),
        ).handle("inflict tumble")
        self.assertEqual(type(payload).__name__, "SkillResultsPayload")
        self.assertTrue(payload.results)

    def test_stale_index_failure_does_not_break_later_exact_lookup(self):
        runtime = SemanticRuntimeCache(
            provider_factory=lambda: type(
                "Provider",
                (),
                {"config_id": "symmetric-encode-v1"},
            )(),
            index_factory=lambda repository, provider: (_ for _ in ()).throw(
                EmbeddingIndexError("stale")
            ),
        )
        free_text = SkillSearchService(
            self.repo,
            semantic_runtime=runtime,
        ).handle("inflict tumble")
        exact = SkillSearchService(
            self.repo,
            semantic_runtime=runtime,
        ).handle("magic: finale")
        self.assertEqual(type(free_text).__name__, "SkillResultsPayload")
        self.assertEqual(type(exact).__name__, "SkillDetailPayload")

    def test_missing_skill_database_returns_unavailable(self):
        payload = run_skill_search(
            Path("/definitely/missing/skills.sqlite"),
            "magic finale",
        )
        self.assertIsInstance(payload, SkillUnavailablePayload)
        self.assertIn("unavailable", payload.text.casefold())

    def test_corrupt_skill_database_returns_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "skills.sqlite"
            path.write_bytes(b"not a sqlite database")
            payload = run_skill_search(path, "magic finale")
        self.assertIsInstance(payload, SkillUnavailablePayload)


class SemanticRuntimeCacheTests(unittest.TestCase):
    def test_runtime_cache_builds_one_index_for_same_database_manifest(self):
        build_calls = []
        sentinel = FakeSemanticIndex()

        class Provider:
            provider_name = "sentence-transformers"
            model_name = "sentence-transformers/all-MiniLM-L6-v2"
            config_id = "symmetric-encode-v1"

        repository = FakeRuntimeRepository(Path("/tmp/skills.sqlite"))
        runtime = SemanticRuntimeCache(
            provider_factory=lambda: Provider(),
            index_factory=lambda repo, provider: (
                build_calls.append(repo.database_path) or sentinel
            ),
        )
        with ThreadPoolExecutor(max_workers=4) as executor:
            values = list(
                executor.map(lambda unused: runtime.get_index(repository), range(4))
            )
        self.assertTrue(all(value is sentinel for value in values))
        self.assertEqual(len(build_calls), 1)


if __name__ == "__main__":
    unittest.main()
