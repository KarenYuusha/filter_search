from __future__ import annotations

import json
import os
from typing import Callable
from urllib import error, request


DEFAULT_MODEL = "qwen3.5:2b"
DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"


class LLMUnavailableError(RuntimeError):
    """Raised when the local Ollama endpoint/model cannot be reached."""


class LLMResponseError(RuntimeError):
    """Raised when Ollama returns a response outside the required JSON contract."""


def _chat_endpoint(value: str) -> str:
    endpoint = value.strip().rstrip("/")
    if not endpoint:
        endpoint = DEFAULT_OLLAMA_HOST
    if "://" not in endpoint:
        endpoint = "http://" + endpoint
    if endpoint.endswith("/api/chat"):
        return endpoint
    return endpoint + "/api/chat"


class OllamaQwenClient:
    def __init__(
        self,
        model: str | None = None,
        endpoint: str | None = None,
        timeout_seconds: float = 12.0,
        *,
        urlopen_fn: Callable | None = None,
    ) -> None:
        self.model = model or os.environ.get("OLLAMA_MODEL", DEFAULT_MODEL)
        configured_endpoint = endpoint or os.environ.get("OLLAMA_HOST", DEFAULT_OLLAMA_HOST)
        self.endpoint = _chat_endpoint(configured_endpoint)
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
        except error.HTTPError as exc:
            detail = ""
            try:
                raw_error = exc.read()
                if raw_error:
                    decoded_error = json.loads(raw_error.decode("utf-8"))
                    if isinstance(decoded_error, dict) and isinstance(decoded_error.get("error"), str):
                        detail = decoded_error["error"].strip()
            except (UnicodeDecodeError, json.JSONDecodeError, OSError):
                pass
            message = f"Ollama returned HTTP {exc.code} from {self.endpoint}"
            if detail:
                message += f": {detail}"
            raise LLMUnavailableError(message) from exc
        except (OSError, TimeoutError, error.URLError) as exc:
            raise LLMUnavailableError(
                f"Could not reach Ollama at {self.endpoint}: {exc}"
            ) from exc

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
