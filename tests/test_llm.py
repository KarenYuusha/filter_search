import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import ollama

from toram_search.llm import LLMResponseError, LLMUnavailableError, OllamaQwenClient


class FakeClient:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


class OllamaQwenClientTests(unittest.TestCase):
    def test_uses_official_client_with_explicit_host(self):
        built = {}

        def fake_factory(**kwargs):
            built.update(kwargs)
            return FakeClient()

        client = OllamaQwenClient(
            host="http://ollama.test:11434",
            client_factory=fake_factory,
        )

        self.assertEqual(
            built,
            {"host": "http://ollama.test:11434", "timeout": 30.0},
        )
        self.assertIsNotNone(client)

    def test_omits_host_so_library_can_use_ollama_host_environment(self):
        built = {}

        def fake_factory(**kwargs):
            built.update(kwargs)
            return FakeClient()

        with patch.dict(
            os.environ,
            {"OLLAMA_HOST": "http://env-host:11434"},
            clear=False,
        ):
            OllamaQwenClient(client_factory=fake_factory)

        self.assertEqual(built, {"timeout": 30.0})

    def test_complete_uses_chat_json_mode_and_parses_object(self):
        fake = FakeClient(
            response=SimpleNamespace(
                message=SimpleNamespace(content='{"intent":"refuse"}')
            )
        )
        client = OllamaQwenClient(client=fake)

        result = client.complete("system", "user")

        self.assertEqual(result, {"intent": "refuse"})
        self.assertEqual(len(fake.calls), 1)
        call = fake.calls[0]
        self.assertEqual(call["model"], "qwen3.5:2b")
        self.assertEqual(call["format"], "json")
        self.assertFalse(call["think"])
        self.assertEqual(call["keep_alive"], "10m")
        self.assertEqual(
            call["options"],
            {"temperature": 0, "num_predict": 128},
        )
        self.assertEqual(
            call["messages"],
            [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "user"},
            ],
        )

    def test_complete_forwards_explicit_json_schema(self):
        schema = {
            "type": "object",
            "properties": {"intent": {"type": "string"}},
        }
        fake = FakeClient(
            response=SimpleNamespace(
                message=SimpleNamespace(content='{"intent":"refuse"}')
            )
        )
        client = OllamaQwenClient(client=fake)

        client.complete("system", "user", schema=schema)

        self.assertEqual(fake.calls[0]["format"], schema)

    def test_read_timeout_becomes_unavailable(self):
        fake = FakeClient(error=httpx.ReadTimeout("timed out"))
        client = OllamaQwenClient(client=fake)

        with self.assertRaises(LLMUnavailableError) as raised:
            client.complete("system", "user")

        self.assertIn("timed out", str(raised.exception))

    def test_response_error_becomes_unavailable_with_server_detail(self):
        fake = FakeClient(
            error=ollama.ResponseError("model qwen3.5:2b not found", 404)
        )
        client = OllamaQwenClient(client=fake)

        with self.assertRaises(LLMUnavailableError) as raised:
            client.complete("system", "user")

        self.assertIn("model qwen3.5:2b not found", str(raised.exception))
        self.assertIn("404", str(raised.exception))

    def test_connection_error_becomes_unavailable(self):
        fake = FakeClient(error=ConnectionError("connection refused"))
        client = OllamaQwenClient(client=fake)

        with self.assertRaises(LLMUnavailableError) as raised:
            client.complete("system", "user")

        self.assertIn("connection refused", str(raised.exception))

    def test_malformed_message_content_becomes_response_error(self):
        fake = FakeClient(
            response=SimpleNamespace(
                message=SimpleNamespace(content="not json")
            )
        )
        client = OllamaQwenClient(client=fake)

        with self.assertRaises(LLMResponseError):
            client.complete("system", "user")


if __name__ == "__main__":
    unittest.main()
