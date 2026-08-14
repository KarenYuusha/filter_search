from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import unittest

from toram_skill_chat.llm import SkillRagUnavailableError
from toram_skill_chat.rag import GroundedSkillRag
from toram_skill_chat.retrieval import SkillEvidenceRetriever
from toram_skill_chat.service import SkillChatService
from toram_skills.repository import SkillRepository


ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "coryn_data" / "database" / "skills.sqlite"


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


class _CompletionClient:
    def __init__(self, answer: str = "Grounded database explanation.", *, fail: bool = False) -> None:
        self.answer = answer
        self.fail = fail
        self.calls: list[tuple[str, str]] = []

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        self.calls.append((system_prompt, user_prompt))
        if self.fail:
            raise SkillRagUnavailableError("offline")
        return self.answer


class SkillChatServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = SkillRepository(DATABASE)
        self.client = _CompletionClient()
        self.service = SkillChatService(
            self.repo,
            retriever=SkillEvidenceRetriever(self.repo, semantic_runtime=None),
            rag=GroundedSkillRag(self.client),
        )
        self.context = _Context()

    def tearDown(self) -> None:
        self.repo.close()

    def test_highest_mp_is_structured_and_never_calls_rag(self):
        result = self.service.answer(
            "which skill has the highest MP cost?",
            context=self.context,
        )

        self.assertEqual(result.kind, "results")
        self.assertGreater(len(result.skill_ids), 0)
        self.assertIn("MP", result.text or "")
        self.assertEqual(self.client.calls, [])
        self.assertEqual(self.context.active_domain, "skill")
        self.assertEqual(self.context.last_operation, "rank")
        self.assertEqual(self.context.last_metric, "mp_cost_value")

    def test_ignition_alias_filters_by_canonical_ignite_without_rag(self):
        result = self.service.answer(
            "which skills inflict ignition?",
            context=self.context,
        )

        self.assertEqual(result.kind, "results")
        self.assertGreater(len(result.skill_ids), 0)
        self.assertEqual(self.context.active_skill_filters["ailments"], ("Ignite",))
        self.assertEqual(self.client.calls, [])

    def test_filtered_follow_up_keeps_prior_ailment_and_adds_tree(self):
        first = self.service.answer("which skills inflict Stun?", context=self.context)
        self.assertEqual(first.kind, "results")
        self.assertGreater(len(first.skill_ids), 1)

        second = self.service.answer("only shield skills", context=self.context)

        self.assertEqual(second.kind, "results")
        self.assertGreater(len(second.skill_ids), 0)
        self.assertEqual(self.context.active_skill_filters["ailments"], ("Stun",))
        self.assertEqual(len(self.context.active_skill_filters["tree_ids"]), 1)
        self.assertIsNotNone(self.context.active_tree_id)
        self.assertEqual(self.client.calls, [])

    def test_field_comparison_is_deterministic(self):
        result = self.service.answer(
            "which costs more MP, Guardian or Protection?",
            context=self.context,
        )

        self.assertEqual(result.kind, "structured")
        self.assertEqual(len(result.skill_ids), 2)
        self.assertIn("Guardian", result.text or "")
        self.assertIn("Protection", result.text or "")
        self.assertIn("MP", result.text or "")
        self.assertEqual(self.client.calls, [])
        self.assertEqual(self.context.active_skill_ids, result.skill_ids)
        self.assertIsNone(self.context.selected_skill_id)

    def test_full_comparison_uses_one_grounded_rag_call(self):
        result = self.service.answer(
            "compare Protection and Aegis",
            context=self.context,
        )

        self.assertEqual(result.kind, "answer")
        self.assertEqual(result.text, "Grounded database explanation.")
        self.assertEqual(len(result.skill_ids), 2)
        self.assertEqual(len(self.client.calls), 1)
        self.assertEqual(self.context.active_skill_ids, result.skill_ids)
        self.assertIsNone(self.context.selected_skill_id)

    def test_general_mechanic_with_evidence_uses_one_rag_call(self):
        result = self.service.answer("what is Flinch?", context=self.context)

        self.assertEqual(result.kind, "answer")
        self.assertEqual(len(self.client.calls), 1)
        self.assertGreater(len(result.evidence), 0)
        self.assertEqual(self.context.active_domain, "skill")
        self.assertEqual(self.context.last_operation, "general_mechanic")

    def test_general_mechanic_without_evidence_does_not_call_rag_model(self):
        result = self.service.answer("what is Zorbification?", context=self.context)

        self.assertEqual(result.kind, "not_found")
        self.assertEqual(self.client.calls, [])
        self.assertIsNone(self.context.active_domain)

    def test_refusal_does_not_call_rag_or_overwrite_useful_context(self):
        self.context.active_domain = "skill"
        self.context.active_skill_ids = ("keep-me",)
        self.context.selected_skill_id = "keep-me"
        self.context.last_operation = "lookup"
        self.context.last_user_query = "previous useful query"

        result = self.service.answer("best skill for DPS", context=self.context)

        self.assertEqual(result.kind, "refuse")
        self.assertEqual(self.client.calls, [])
        self.assertEqual(self.context.active_skill_ids, ("keep-me",))
        self.assertEqual(self.context.selected_skill_id, "keep-me")
        self.assertEqual(self.context.last_operation, "lookup")
        self.assertEqual(self.context.last_user_query, "previous useful query")

    def test_ambiguous_pronoun_returns_clarification_without_rag(self):
        guardian = self.repo.resolve_skill_name("Guardian")[0]
        protection = self.repo.resolve_skill_name("Protection")[0]
        self.context.active_domain = "skill"
        self.context.active_skill_ids = (guardian.id, protection.id)
        self.context.selected_skill_id = None

        result = self.service.answer("how does it work?", context=self.context)

        self.assertEqual(result.kind, "clarify")
        self.assertIn("Guardian", result.text or "")
        self.assertIn("Protection", result.text or "")
        self.assertEqual(self.client.calls, [])

    def test_single_rank_result_becomes_selected_for_pronoun_follow_up(self):
        first = self.service.answer("which skills inflict Stun?", context=self.context)
        self.assertEqual(first.kind, "results")

        ranked = self.service.answer("which one costs the least MP?", context=self.context)
        self.assertEqual(ranked.kind, "results")
        self.assertEqual(len(ranked.skill_ids), 1)
        self.assertEqual(self.context.selected_skill_id, ranked.skill_ids[0])

        explained = self.service.answer("how does it work?", context=self.context)
        self.assertEqual(explained.kind, "answer")
        self.assertEqual(len(self.client.calls), 1)


if __name__ == "__main__":
    unittest.main()
