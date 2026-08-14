from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import unittest

from toram_skill_chat.llm import SkillRagUnavailableError
from toram_skill_chat.models import SkillEvidence
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


class _Client:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        if self.fail:
            raise SkillRagUnavailableError("offline")
        return "grounded"


class SkillChatDebugLoggingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = SkillRepository(DATABASE)

    def tearDown(self) -> None:
        self.repo.close()

    def test_service_logs_selected_structured_operation(self):
        service = SkillChatService(self.repo, rag=GroundedSkillRag(_Client()))
        with self.assertLogs("toram_skill_chat.service", level="DEBUG") as captured:
            service.answer("which skill has the highest MP cost?", context=_Context())

        visible = "\n".join(captured.output)
        self.assertIn("intent=rank", visible)
        self.assertIn("field=mp_cost_value", visible)

    def test_retriever_logs_semantic_choice_and_evidence_document_ids(self):
        hard_hit = self.repo.resolve_skill_name("Hard Hit")[0]
        retriever = SkillEvidenceRetriever(self.repo, semantic_runtime=None)

        with self.assertLogs("toram_skill_chat.retrieval", level="DEBUG") as captured:
            evidence = retriever.retrieve(
                "how does Hard Hit work?",
                skill_ids=(hard_hit.id,),
                limit=3,
            )

        visible = "\n".join(captured.output)
        self.assertIn("semantic_needed=False", visible)
        self.assertIn(evidence[0].document_id, visible)

    def test_rag_logs_model_call_and_degradation_reason(self):
        evidence = (
            SkillEvidence(
                document_id="hard-hit#summary",
                skill_id="hard-hit",
                skill_name="Hard Hit",
                tree_name="Blade Skills",
                text="Hard Hit may inflict Flinch.",
                source_kind="summary",
            ),
        )
        rag = GroundedSkillRag(_Client(fail=True))

        with self.assertLogs("toram_skill_chat.rag", level="DEBUG") as captured:
            rag.answer(
                "how does Hard Hit work?",
                evidence=evidence,
                required_skill_ids=("hard-hit",),
            )

        visible = "\n".join(captured.output)
        self.assertIn("gemma_called=True", visible)
        self.assertIn("fallback=llm_unavailable", visible)
        self.assertIn("hard-hit#summary", visible)


if __name__ == "__main__":
    unittest.main()
