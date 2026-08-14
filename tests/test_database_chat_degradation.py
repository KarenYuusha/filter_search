from __future__ import annotations

from pathlib import Path
import unittest

from toram_discord.database_chat import run_database_chat_sync
from toram_discord.sessions import DatabaseChatContext
from toram_skill_chat.llm import SkillRagUnavailableError


ROOT = Path(__file__).resolve().parents[1]
ITEM_DATABASE = ROOT / "coryn_data" / "database" / "items.sqlite"
SKILL_DATABASE = ROOT / "coryn_data" / "database" / "skills.sqlite"


class _OfflineRagClient:
    def complete(self, *args, **kwargs):
        raise SkillRagUnavailableError("offline")


class DatabaseChatDegradationTests(unittest.TestCase):
    def test_missing_skill_database_keeps_item_side_of_mixed_discovery(self):
        outcome = run_database_chat_sync(
            ITEM_DATABASE,
            ROOT / "missing-skills.sqlite",
            "what gives crit rate?",
            DatabaseChatContext(),
            skill_semantic_runtime=None,
        )

        self.assertEqual(outcome.kind, "item")
        self.assertGreater(len(outcome.item_ids), 0)
        self.assertIsNone(outcome.skill_result)

    def test_missing_item_database_keeps_structured_skill_question_working(self):
        outcome = run_database_chat_sync(
            ROOT / "missing-items.sqlite",
            SKILL_DATABASE,
            "which skill has the highest MP cost?",
            DatabaseChatContext(),
            skill_semantic_runtime=None,
        )

        self.assertEqual(outcome.kind, "skill")
        self.assertIsNotNone(outcome.skill_result)
        self.assertGreater(len(outcome.skill_ids), 0)

    def test_gemma_unavailable_returns_retrieved_evidence_fallback(self):
        outcome = run_database_chat_sync(
            ITEM_DATABASE,
            SKILL_DATABASE,
            "how does Hard Hit work?",
            DatabaseChatContext(),
            skill_semantic_runtime=None,
            rag_client=_OfflineRagClient(),
        )

        self.assertEqual(outcome.kind, "skill")
        self.assertIsNotNone(outcome.skill_result)
        assert outcome.skill_result is not None
        self.assertEqual(outcome.skill_result.kind, "answer")
        self.assertIn("Synthesized explanation is unavailable", outcome.skill_result.text or "")
        self.assertGreater(len(outcome.skill_result.evidence), 0)


if __name__ == "__main__":
    unittest.main()
