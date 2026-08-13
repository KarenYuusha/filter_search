from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import unittest

from toram_skill_search.service import SkillSearchService, parse_skill_command
from toram_skills.repository import SkillRepository

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
        payload = SkillSearchService(self.repo).handle("inflict tumble")
        self.assertEqual(type(payload).__name__, "SkillResultsPayload")
        self.assertTrue(payload.results)
        self.assertTrue(
            all(item.skill.tree_id == item.tree.id for item in payload.results)
        )
        self.assertTrue(all(item.snippet for item in payload.results))

    def test_free_text_accepts_filter_like_words_as_plain_text(self):
        payload = SkillSearchService(self.repo).handle("sword tier 4")
        self.assertEqual(type(payload).__name__, "SkillResultsPayload")


if __name__ == "__main__":
    unittest.main()
