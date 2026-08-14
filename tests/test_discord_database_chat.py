from __future__ import annotations

from pathlib import Path
import unittest

import search_items as core

from toram_discord.database_chat import (
    DatabaseChatOutcome,
    build_database_chat_help_embed,
    build_mixed_chat_message,
    build_skill_chat_embed,
    is_database_chat_candidate,
    probe_item_deterministically,
    run_database_chat_sync,
)
from toram_discord.sessions import DatabaseChatContext, DiscordSessionManager
from toram_search.service import ItemResultsPayload, ServiceOutcome
from toram_skill_chat.models import SkillChatResult, SkillEvidence


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

    def test_candidate_detection_keeps_short_item_queries_on_existing_fast_path(self):
        self.assertFalse(is_database_chat_candidate("cr bow", DatabaseChatContext()))
        self.assertFalse(is_database_chat_candidate("hp armor", DatabaseChatContext()))
        self.assertTrue(is_database_chat_candidate("which skill has the highest MP cost?", DatabaseChatContext()))
        self.assertTrue(is_database_chat_candidate("what gives crit rate?", DatabaseChatContext()))
        context = DatabaseChatContext(active_domain="skill", active_skill_ids=("x",))
        self.assertTrue(is_database_chat_candidate("only shield skills", context))


class DatabaseChatRenderingTests(unittest.TestCase):
    def test_top_level_help_contains_item_skill_natural_and_reset_examples(self):
        embed = build_database_chat_help_embed("@Toram Search")
        visible = "\n".join(
            [embed.title or "", embed.description or ""]
            + [field.name + "\n" + field.value for field in embed.fields]
        )
        self.assertIn("Item Search", visible)
        self.assertIn("Skill Search", visible)
        self.assertIn("Ask Naturally", visible)
        self.assertIn("@Toram Search skill hard hit", visible)
        self.assertIn("@Toram Search how does Hard Hit work?", visible)
        self.assertIn("@Toram Search reset", visible)

    def test_skill_refusal_explains_available_objective_comparisons(self):
        embed = build_skill_chat_embed(
            SkillChatResult(kind="refuse", text="No subjective recommendation.")
        )
        visible = (embed.description or "").casefold()
        self.assertIn("objective", visible)
        self.assertIn("mp cost", visible)
        self.assertIn("ailments", visible)

    def test_mixed_render_shows_three_per_section_and_more_buttons(self):
        item_rows = tuple(
            core.RankedItem(
                core.ItemSummary(index, f"Item {index}", "Armor"),
                100.0 - index,
                "exact",
            )
            for index in range(1, 6)
        )
        item_outcome = ServiceOutcome(
            "search",
            payload=ItemResultsPayload("crit rate", item_rows),
        )
        evidence = tuple(
            SkillEvidence(
                document_id=f"skill-{index}#summary",
                skill_id=f"skill-{index}",
                skill_name=f"Skill {index}",
                tree_name="Test Tree",
                text=f"Skill {index} context",
                source_kind="summary",
            )
            for index in range(1, 6)
        )
        skill_result = SkillChatResult(
            kind="results",
            text="skills",
            skill_ids=tuple(f"skill-{index}" for index in range(1, 6)),
            evidence=evidence,
        )
        outcome = DatabaseChatOutcome(
            kind="mixed",
            item_outcome=item_outcome,
            skill_result=skill_result,
            item_ids=tuple(range(1, 6)),
            skill_ids=skill_result.skill_ids,
        )
        sessions = DiscordSessionManager()
        key = (10, 30, 20)
        session = sessions.start_query(key, "what gives crit rate?")

        embed, view = build_mixed_chat_message(
            outcome,
            sessions=sessions,
            key=key,
            generation=session.generation,
        )

        fields = {field.name: field.value for field in embed.fields}
        self.assertIn("Items", fields)
        self.assertIn("Skills", fields)
        self.assertIn("Item 3", fields["Items"])
        self.assertNotIn("Item 4", fields["Items"])
        self.assertIn("Skill 3", fields["Skills"])
        self.assertNotIn("Skill 4", fields["Skills"])
        labels = {getattr(child, "label", None) for child in view.children}
        self.assertIn("More Items", labels)
        self.assertIn("More Skills", labels)


if __name__ == "__main__":
    unittest.main()
