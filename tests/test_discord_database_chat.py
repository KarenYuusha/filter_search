from __future__ import annotations

from pathlib import Path
import unittest

from toram_discord.database_chat import (
    probe_item_deterministically,
    run_database_chat_sync,
)
from toram_discord.sessions import DatabaseChatContext


ROOT = Path(__file__).resolve().parents[1]
ITEM_DATABASE = ROOT / "coryn_data" / "database" / "items.sqlite"
SKILL_DATABASE = ROOT / "coryn_data" / "database" / "skills.sqlite"


class _ForbiddenLlm:
    def complete(self, *args, **kwargs):
        raise AssertionError("LLM must not be called for database-chat routing")


class _ForbiddenSkillRepository:
    def __init__(self, *args, **kwargs):
        raise AssertionError("skill database must not open for deterministic item query")


class DatabaseChatRoutingTests(unittest.TestCase):
    def test_deterministic_item_probe_never_calls_llm(self):
        import search_items as core

        repo = core.ItemRepository(ITEM_DATABASE)
        try:
            item = probe_item_deterministically(repo, "cr bow")
            unknown = probe_item_deterministically(repo, "tell me a bedtime story")
        finally:
            repo.close()

        self.assertIsNotNone(item)
        self.assertIsNone(unknown)

    def test_item_query_does_not_open_skill_path(self):
        context = DatabaseChatContext()
        outcome = run_database_chat_sync(
            ITEM_DATABASE,
            SKILL_DATABASE,
            "cr bow",
            context,
            skill_repository_factory=_ForbiddenSkillRepository,
        )

        self.assertEqual(outcome.kind, "item")
        self.assertIsNotNone(outcome.item_outcome)
        self.assertIsNone(outcome.skill_result)
        self.assertEqual(context.active_domain, "item")

    def test_natural_structured_skill_question_uses_skill_side(self):
        context = DatabaseChatContext()
        outcome = run_database_chat_sync(
            ITEM_DATABASE,
            SKILL_DATABASE,
            "which skill has the highest MP cost?",
            context,
            skill_semantic_runtime=None,
            rag_client=_ForbiddenLlm(),
        )

        self.assertEqual(outcome.kind, "skill")
        self.assertIsNone(outcome.item_outcome)
        self.assertIsNotNone(outcome.skill_result)
        self.assertGreater(len(outcome.skill_result.skill_ids), 0)
        self.assertEqual(context.active_domain, "skill")

    def test_cross_database_crit_rate_returns_mixed_without_llm(self):
        context = DatabaseChatContext()
        outcome = run_database_chat_sync(
            ITEM_DATABASE,
            SKILL_DATABASE,
            "what gives crit rate?",
            context,
            skill_semantic_runtime=None,
            rag_client=_ForbiddenLlm(),
        )

        self.assertEqual(outcome.kind, "mixed")
        self.assertIsNotNone(outcome.item_outcome)
        self.assertIsNotNone(outcome.skill_result)
        self.assertGreater(len(outcome.item_ids), 0)
        self.assertGreater(len(outcome.skill_ids), 0)
        self.assertEqual(context.active_domain, "mixed")
        self.assertEqual(context.active_item_ids, outcome.item_ids)
        self.assertEqual(context.active_skill_ids, outcome.skill_ids)

    def test_unknown_query_defers_to_existing_item_fallback(self):
        context = DatabaseChatContext()
        outcome = run_database_chat_sync(
            ITEM_DATABASE,
            SKILL_DATABASE,
            "tell me a bedtime story",
            context,
            skill_semantic_runtime=None,
            rag_client=_ForbiddenLlm(),
        )

        self.assertEqual(outcome.kind, "fallback")
        self.assertIsNone(outcome.item_outcome)
        self.assertIsNone(outcome.skill_result)

    def test_missing_skill_database_does_not_break_deterministic_item_query(self):
        context = DatabaseChatContext()
        outcome = run_database_chat_sync(
            ITEM_DATABASE,
            ROOT / "missing-skills.sqlite",
            "hp armor",
            context,
        )
        self.assertEqual(outcome.kind, "item")


if __name__ == "__main__":
    unittest.main()
