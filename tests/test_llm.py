import io
import json
import os
import unittest
from unittest.mock import patch
from urllib import error

from toram_search.llm import LLMUnavailableError, OllamaQwenClient


class FakeResponse:
    def __init__(self, payload: dict[str, object]):
        self._raw = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self._raw


class OllamaQwenClientTests(unittest.TestCase):
    def test_uses_ollama_host_environment(self):
        with patch.dict(os.environ, {"OLLAMA_HOST": "host.docker.internal:11434"}, clear=False):
            client = OllamaQwenClient()
        self.assertEqual(client.endpoint, "http://host.docker.internal:11434/api/chat")

    def test_explicit_endpoint_wins_over_environment(self):
        with patch.dict(os.environ, {"OLLAMA_HOST": "http://wrong-host:11434"}, clear=False):
            client = OllamaQwenClient(endpoint="http://right-host:11434/api/chat")
        self.assertEqual(client.endpoint, "http://right-host:11434/api/chat")

    def test_complete_posts_to_configured_ollama_endpoint(self):
        seen = {}

        def fake_urlopen(req, timeout):
            seen["url"] = req.full_url
            seen["timeout"] = timeout
            seen["payload"] = json.loads(req.data.decode("utf-8"))
            return FakeResponse({"message": {"content": '{"intent":"refuse"}'}})

        client = OllamaQwenClient(
            endpoint="http://ollama.test:11434",
            urlopen_fn=fake_urlopen,
        )
        result = client.complete("system", "user")

        self.assertEqual(result, {"intent": "refuse"})
        self.assertEqual(seen["url"], "http://ollama.test:11434/api/chat")
        self.assertEqual(seen["payload"]["model"], "qwen3.5:2b")

    def test_http_error_reports_ollama_error_body(self):
        def fake_urlopen(req, timeout):
            raise error.HTTPError(
                req.full_url,
                404,
                "Not Found",
                hdrs=None,
                fp=io.BytesIO(b'{"error":"model qwen3.5:2b not found"}'),
            )

        client = OllamaQwenClient(urlopen_fn=fake_urlopen)
        with self.assertRaises(LLMUnavailableError) as raised:
            client.complete("system", "user")

        self.assertIn("HTTP 404", str(raised.exception))
        self.assertIn("model qwen3.5:2b not found", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
