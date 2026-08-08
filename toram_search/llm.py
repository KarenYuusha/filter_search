from __future__ import annotations

import json
from typing import Callable
from urllib import error, request


class LLMUnavailableError(RuntimeError):
    """Raised when the local Ollama endpoint/model cannot be reached."""


class LLMResponseError(RuntimeError):
    """Raised when Ollama returns a response outside the required JSON contract."""


class OllamaQwenClient:
    def __init__(
        self,
        model: str = "qwen3.5:2b",
        endpoint: str = "http://127.0.0.1:11434/api/chat",
        timeout_seconds: float = 12.0,
        *,
        urlopen_fn: Callable | None = None,
    ) -> None:
        self.model = model
        self.endpoint = endpoint
        self.timeout_seconds = timeout_seconds
        self._urlopen = urlopen_fn or request.urlopen

    def complete(self, system_prompt: str, user_prompt: str) -> dict[str, object]:
        payload = {
            "model": self.model,
            "stream": False,
            "keep_alive": "10m",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "options": {
                "temperature": 0,
                "num_predict": 192,
            },
        }
        body = json.dumps(payload).encode("utf-8")
        req = request.Request(
            self.endpoint,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self._urlopen(req, timeout=self.timeout_seconds) as response:
                raw = response.read()
        except (OSError, TimeoutError, error.URLError, error.HTTPError) as exc:
            raise LLMUnavailableError(str(exc)) from exc

        try:
            envelope = json.loads(raw.decode("utf-8"))
            content = envelope["message"]["content"]
            if not isinstance(content, str):
                raise TypeError("message.content is not text")
            decoded = json.loads(content.strip())
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise LLMResponseError("Ollama returned malformed structured output") from exc

        if not isinstance(decoded, dict):
            raise LLMResponseError("Qwen output must be one JSON object")
        return decoded
