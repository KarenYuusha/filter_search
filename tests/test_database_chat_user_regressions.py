from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import unittest

from toram_discord.database_chat import is_database_chat_candidate, run_database_chat_sync
from toram_discord.sessions import DatabaseChatContext
from toram_skill_chat.rag import GroundedSkillRag
from toram_skill_chat.retrieval import SkillEvidenceRetriever
from toram_skill_chat.router import SkillChatRouter
from toram_skill_chat.service import SkillChatService
from toram_skills.repository import SkillRepository


ROOT = Path(__file__).resolve().parents[1]
ITEM_DATABASE = ROOT / "coryn_data" / "database" / "items.sqlite"
SKILL_DATABASE = ROOT / "coryn_data" / "database" / "skills.sqlite"


class _ForbiddenLlm:
    def complete(self, *args, **kwargs):
        raise AssertionError("LLM must not be called for deterministic skill queries")


@dataclass
class _Context:
    active_domain: str | None = None
    active_skill_ids: tuple[str, ...] = ()
    active_item_ids: tuple[int, ...] = ()
    selected_skill_id: str | None = None
    selected_item_id: int | None = None
    active_tree_id: str | None = None
    active_skill_filters: dict[str, object] = field(default_factory=dict)
    last_operation: str | None = None
    last_metric: str | None = None
    last_user_query: str | None = None


class DatabaseChatUserRegressionTests(unittest.TestCase):
    def test_compact_skill_queries_enter_database_chat(self):
        context = DatabaseChatContext()
        self.assertTrue(is_database_chat_candidate("guardian mp cost", context))
        self.assertTrue(is_database_chat_candidate("shield skill tree", context))

    def test_guardian_mp_cost_compact_query_returns_skill_answer_without_llm(self):
        context = DatabaseChatContext()
        outcome = run_database_chat_sync(
            ITEM_DATABASE,
            SKILL_DATABASE,
            "guardian mp cost",
            context,
            skill_semantic_runtime=None,
            rag_client=_ForbiddenLlm(),
        )

        self.assertEqual(outcome.kind, "skill")
        self.assertIsNotNone(outcome.skill_result)
        rendered = (outcome.skill_result.text or "").casefold()
        self.assertIn("guardian", rendered)
        self.assertIn("mp", rendered)

    def test_bare_shield_skill_tree_routes_to_tree_filter(self):
        repo = SkillRepository(SKILL_DATABASE)
        try:
            router = SkillChatRouter(repo)
            shield = repo.resolve_tree_name("Shield Skills")[0]
            plan = router.route("shield skill tree")
        finally:
            repo.close()

        self.assertEqual(plan.intent, "filter")
        self.assertEqual(plan.filters.tree_ids, (shield.id,))

    def test_plain_compare_is_structured_side_by_side_and_does_not_call_llm(self):
        repo = SkillRepository(SKILL_DATABASE)
        try:
            service = SkillChatService(
                repo,
                retriever=SkillEvidenceRetriever(repo, semantic_runtime=None),
                rag=GroundedSkillRag(_ForbiddenLlm()),
            )
            result = service.answer("compare Protection and Aegis", context=_Context())
        finally:
            repo.close()

        self.assertEqual(result.kind, "structured")
        text = (result.text or "").casefold()
        self.assertIn("protection", text)
        self.assertIn("aegis", text)
        self.assertIn("tree", text)
        self.assertIn("mp", text)
        self.assertIn("tier", text)


if __name__ == "__main__":
    unittest.main()
