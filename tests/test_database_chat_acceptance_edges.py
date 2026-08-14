from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import unittest

from toram_discord.database_chat import run_database_chat_sync
from toram_discord.sessions import DatabaseChatContext
from toram_skill_chat.analytics import SkillAnalytics
from toram_skill_chat.models import SkillChatFilter
from toram_skill_chat.rag import GroundedSkillRag
from toram_skill_chat.retrieval import SkillEvidenceRetriever
from toram_skill_chat.service import SkillChatService
from toram_skills.repository import SkillRepository


ROOT = Path(__file__).resolve().parents[1]
ITEM_DATABASE = ROOT / "coryn_data" / "database" / "items.sqlite"
SKILL_DATABASE = ROOT / "coryn_data" / "database" / "skills.sqlite"


class _NoopRagClient:
    def complete(self, system_prompt: str, user_prompt: str) -> str:
        return "grounded"


class DatabaseChatAcceptanceEdgeTests(unittest.TestCase):
    def test_switching_to_item_clears_stale_skill_context(self):
        context = DatabaseChatContext(
            active_domain="mixed",
            active_skill_ids=("old-skill",),
            selected_skill_id="old-skill",
            active_tree_id="old-tree",
            active_skill_filters={"ailments": ("Stun",)},
        )

        outcome = run_database_chat_sync(
            ITEM_DATABASE,
            SKILL_DATABASE,
            "cr bow",
            context,
        )

        self.assertEqual(outcome.kind, "item")
        self.assertEqual(context.active_domain, "item")
        self.assertEqual(context.active_skill_ids, ())
        self.assertIsNone(context.selected_skill_id)
        self.assertIsNone(context.active_tree_id)
        self.assertEqual(context.active_skill_filters, {})

    def test_switching_to_skill_clears_stale_item_context_and_old_filters(self):
        repo = SkillRepository(SKILL_DATABASE)
        try:
            context = DatabaseChatContext(
                active_domain="mixed",
                active_item_ids=(123, 456),
                selected_item_id=123,
                active_skill_filters={"ailments": ("Stun",)},
                active_tree_id="old-tree",
            )
            service = SkillChatService(
                repo,
                retriever=SkillEvidenceRetriever(repo, semantic_runtime=None),
                rag=GroundedSkillRag(_NoopRagClient()),
            )

            result = service.answer("what is Guardian's MP cost?", context=context)

            self.assertEqual(result.kind, "structured")
            self.assertEqual(context.active_domain, "skill")
            self.assertEqual(context.active_item_ids, ())
            self.assertIsNone(context.selected_item_id)
            self.assertEqual(context.active_skill_filters, {})
            self.assertIsNone(context.active_tree_id)
        finally:
            repo.close()

    def test_documented_stun_includes_shield_bash_even_without_structured_ailment(self):
        repo = SkillRepository(SKILL_DATABASE)
        try:
            analytics = SkillAnalytics(repo)
            shield_bash = repo.resolve_skill_name("Shield Bash")[0]

            skills = analytics.filter_skills(SkillChatFilter(ailments=("Stun",)))

            self.assertIn(shield_bash.id, {skill.id for skill in skills})
        finally:
            repo.close()

    def test_stun_then_only_shield_follow_up_has_results(self):
        repo = SkillRepository(SKILL_DATABASE)
        try:
            context = DatabaseChatContext()
            service = SkillChatService(
                repo,
                retriever=SkillEvidenceRetriever(repo, semantic_runtime=None),
                rag=GroundedSkillRag(_NoopRagClient()),
            )

            first = service.answer("which skills inflict Stun?", context=context)
            second = service.answer("only shield skills", context=context)

            self.assertEqual(first.kind, "results")
            self.assertEqual(second.kind, "results")
            self.assertGreater(len(second.skill_ids), 0)
        finally:
            repo.close()


if __name__ == "__main__":
    unittest.main()
