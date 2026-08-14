from __future__ import annotations

from types import SimpleNamespace
import unittest

import httpx

from toram_skill_chat.llm import (
    OllamaSkillRagClient,
    SkillRagResponseError,
    SkillRagUnavailableError,
)


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


if __name__ == "__main__":
    unittest.main()
