from __future__ import annotations

from types import SimpleNamespace
import unittest

import httpx

from toram_skill_chat.llm import (
    OllamaSkillRagClient,
    SkillRagResponseError,
    SkillRagUnavailableError,
)
from toram_skill_chat.models import SkillEvidence
from toram_skill_chat.rag import GroundedSkillRag


class _FakeClient:
    def __init__(self, *, content: object = "answer", error: Exception | None = None) -> None:
        self.content = content
        self.error = error
        self.calls: list[dict[str, object]] = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(message=SimpleNamespace(content=self.content))


class _RecordingRagClient:
    def __init__(self, *, answer: str = "grounded answer", error: Exception | None = None) -> None:
        self.answer = answer
        self.error = error
        self.calls: list[tuple[str, str]] = []

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        self.calls.append((system_prompt, user_prompt))
        if self.error is not None:
            raise self.error
        return self.answer


def _evidence(skill_id: str, skill_name: str, text: str) -> SkillEvidence:
    return SkillEvidence(
        document_id=f"{skill_id}#summary",
        skill_id=skill_id,
        skill_name=skill_name,
        tree_name="Test Tree",
        text=text,
        source_kind="summary",
    )


class OllamaSkillRagClientTests(unittest.TestCase):
    def test_complete_uses_configured_model_output_cap_and_keep_alive(self):
        fake = _FakeClient(content="  grounded answer  ")
        client = OllamaSkillRagClient(
            model="gemma4:e4b",
            host=None,
            max_output_tokens=321,
            keep_alive="7m",
            client=fake,
        )

        answer = client.complete("system rules", "question and evidence")

        self.assertEqual(answer, "grounded answer")
        self.assertEqual(len(fake.calls), 1)
        call = fake.calls[0]
        self.assertEqual(call["model"], "gemma4:e4b")
        self.assertEqual(call["keep_alive"], "7m")
        self.assertEqual(call["options"], {"temperature": 0, "num_predict": 321})
        self.assertEqual(
            call["messages"],
            [
                {"role": "system", "content": "system rules"},
                {"role": "user", "content": "question and evidence"},
            ],
        )
        self.assertNotIn("format", call)

    def test_transport_error_becomes_typed_unavailable(self):
        request = httpx.Request("POST", "http://ollama.test/api/chat")
        fake = _FakeClient(error=httpx.ConnectError("offline", request=request))
        client = OllamaSkillRagClient(model="gemma4:e4b", host=None, client=fake)

        with self.assertRaises(SkillRagUnavailableError):
            client.complete("system", "user")

    def test_empty_or_non_text_output_becomes_typed_response_error(self):
        for content in ("   ", None, 123):
            with self.subTest(content=content):
                client = OllamaSkillRagClient(
                    model="gemma4:e4b",
                    host=None,
                    client=_FakeClient(content=content),
                )
                with self.assertRaises(SkillRagResponseError):
                    client.complete("system", "user")

    def test_official_client_receives_explicit_host_and_timeout(self):
        captured: dict[str, object] = {}

        def factory(**kwargs):
            captured.update(kwargs)
            return _FakeClient()

        OllamaSkillRagClient(
            model="gemma4:e4b",
            host="http://localhost:11434",
            timeout_seconds=12.5,
            client_factory=factory,
        )

        self.assertEqual(captured["host"], "http://localhost:11434")
        self.assertEqual(captured["timeout"], 12.5)


class GroundedSkillRagTests(unittest.TestCase):
    def test_no_evidence_returns_insufficient_without_gemma_call(self):
        client = _RecordingRagClient()
        rag = GroundedSkillRag(client)

        result = rag.answer("how does Hard Hit work?", evidence=(), required_skill_ids=("hard-hit",))

        self.assertEqual(result.kind, "not_found")
        self.assertIn("does not contain enough information", result.text or "")
        self.assertEqual(client.calls, [])

    def test_comparison_requires_evidence_for_both_skills(self):
        client = _RecordingRagClient()
        rag = GroundedSkillRag(client)
        evidence = (_evidence("protection", "Protection", "Protection reduces physical damage."),)

        result = rag.answer(
            "compare Protection and Aegis",
            evidence=evidence,
            required_skill_ids=("protection", "aegis"),
        )

        self.assertEqual(result.kind, "not_found")
        self.assertEqual(client.calls, [])

    def test_gemma_receives_only_question_and_supplied_database_evidence(self):
        client = _RecordingRagClient(answer="Protection is documented as reducing physical damage.")
        rag = GroundedSkillRag(client)
        evidence = (
            _evidence("protection", "Protection", "Protection reduces physical damage."),
            _evidence("aegis", "Aegis", "Aegis reduces magic damage."),
        )

        result = rag.answer(
            "compare Protection and Aegis",
            evidence=evidence,
            required_skill_ids=("protection", "aegis"),
        )

        self.assertEqual(result.kind, "answer")
        self.assertEqual(len(client.calls), 1)
        system_prompt, user_prompt = client.calls[0]
        self.assertIn("Use only DATABASE CONTEXT", system_prompt)
        self.assertIn("Do not use outside knowledge", system_prompt)
        self.assertIn("QUESTION:\ncompare Protection and Aegis", user_prompt)
        self.assertIn("Protection reduces physical damage.", user_prompt)
        self.assertIn("Aegis reduces magic damage.", user_prompt)
        self.assertNotIn("unrelated outside fact", user_prompt)

    def test_general_mechanic_requires_relevant_evidence_overlap(self):
        client = _RecordingRagClient()
        rag = GroundedSkillRag(client)

        weak = rag.answer(
            "what is Flinch?",
            evidence=(_evidence("foo", "Foo", "This skill restores MP."),),
            general_mechanic=True,
        )
        self.assertEqual(weak.kind, "not_found")
        self.assertEqual(client.calls, [])

        strong = rag.answer(
            "what is Flinch?",
            evidence=(_evidence("bar", "Bar", "This skill may inflict Flinch on the target."),),
            general_mechanic=True,
        )
        self.assertEqual(strong.kind, "answer")
        self.assertEqual(len(client.calls), 1)

    def test_gemma_unavailable_degrades_to_database_evidence(self):
        client = _RecordingRagClient(error=SkillRagUnavailableError("offline"))
        rag = GroundedSkillRag(client)
        evidence = (_evidence("hard-hit", "Hard Hit", "Hard Hit may inflict Flinch."),)

        result = rag.answer(
            "how does Hard Hit work?",
            evidence=evidence,
            required_skill_ids=("hard-hit",),
        )

        self.assertEqual(result.kind, "answer")
        self.assertIn("Synthesized explanation is unavailable", result.text or "")
        self.assertIn("Hard Hit may inflict Flinch", result.text or "")
        self.assertEqual(result.evidence, evidence)

    def test_unsupported_best_dps_never_reaches_gemma(self):
        client = _RecordingRagClient()
        rag = GroundedSkillRag(client)
        evidence = (_evidence("hard-hit", "Hard Hit", "Hard Hit may inflict Flinch."),)

        result = rag.answer("best skill for DPS", evidence=evidence)

        self.assertEqual(result.kind, "refuse")
        self.assertEqual(client.calls, [])


if __name__ == "__main__":
    unittest.main()
