from __future__ import annotations

import json
import os
from typing import Callable

import httpx
import ollama


DEFAULT_MODEL = "qwen3.5:2b"


class LLMUnavailableError(RuntimeError):
    """Raised when the local Ollama endpoint/model cannot be reached in time."""


class LLMResponseError(RuntimeError):
    """Raised when Ollama returns a response outside the required JSON contract."""


class OllamaQwenClient:
    def __init__(
        self,
        model: str | None = None,
        host: str | None = None,
        timeout_seconds: float = 30.0,
        *,
        client: object | None = None,
        client_factory: Callable[..., object] = ollama.Client,
    ) -> None:
        self.model = model or os.environ.get("OLLAMA_MODEL", DEFAULT_MODEL)
        if client is not None:
            self._client = client
            return

        client_options: dict[str, object] = {"timeout": timeout_seconds}
        if host is not None:
            client_options["host"] = host
        self._client = client_factory(**client_options)

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        schema: dict[str, object] | None = None,
    ) -> dict[str, object]:
        response_format: object = schema if schema is not None else "json"
        try:
            response = self._client.chat(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                format=response_format,
                think=False,
                options={
                    "temperature": 0,
                    "num_predict": 128,
                },
                keep_alive="10m",
            )
        except ollama.ResponseError as exc:
            detail = getattr(exc, "error", str(exc))
            status_code = getattr(exc, "status_code", -1)
            message = f"Ollama request failed: {detail}"
            if isinstance(status_code, int) and status_code >= 0:
                message += f" (HTTP {status_code})"
            raise LLMUnavailableError(message) from exc
        except (ConnectionError, httpx.TransportError) as exc:
            raise LLMUnavailableError(str(exc)) from exc
        except ollama.RequestError as exc:
            raise LLMResponseError(str(exc)) from exc

        try:
            content = response.message.content
            if not isinstance(content, str):
                raise TypeError("message.content is not text")
            decoded = json.loads(content.strip())
        except (AttributeError, json.JSONDecodeError, TypeError) as exc:
            raise LLMResponseError("Ollama returned malformed structured output") from exc

        if not isinstance(decoded, dict):
            raise LLMResponseError("Qwen output must be one JSON object")
        return decoded
