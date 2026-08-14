from __future__ import annotations

from typing import Callable

import httpx
import ollama


class SkillRagUnavailableError(RuntimeError):
    """Raised when the local skill RAG model cannot be reached."""


class SkillRagResponseError(RuntimeError):
    """Raised when the skill RAG model returns unusable text."""


class OllamaSkillRagClient:
    def __init__(
        self,
        model: str,
        host: str | None,
        timeout_seconds: float = 30.0,
        max_output_tokens: int = 256,
        keep_alive: str = "10m",
        *,
        client: object | None = None,
        client_factory: Callable[..., object] = ollama.Client,
    ) -> None:
        self.model = model
        self.max_output_tokens = max_output_tokens
        self.keep_alive = keep_alive
        if client is not None:
            self._client = client
            return

        options: dict[str, object] = {"timeout": timeout_seconds}
        if host is not None:
            options["host"] = host
        self._client = client_factory(**options)

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        try:
            response = self._client.chat(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                options={
                    "temperature": 0,
                    "num_predict": self.max_output_tokens,
                },
                keep_alive=self.keep_alive,
            )
        except ollama.ResponseError as exc:
            detail = getattr(exc, "error", str(exc))
            raise SkillRagUnavailableError(f"Ollama request failed: {detail}") from exc
        except (ConnectionError, httpx.TransportError) as exc:
            raise SkillRagUnavailableError(str(exc)) from exc
        except ollama.RequestError as exc:
            raise SkillRagResponseError(str(exc)) from exc

        try:
            content = response.message.content
        except AttributeError as exc:
            raise SkillRagResponseError("Ollama returned malformed text output") from exc
        if not isinstance(content, str) or not content.strip():
            raise SkillRagResponseError("Ollama returned empty or non-text output")
        return content.strip()


__all__ = [
    "OllamaSkillRagClient",
    "SkillRagResponseError",
    "SkillRagUnavailableError",
]
