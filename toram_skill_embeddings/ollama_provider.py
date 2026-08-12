from __future__ import annotations

from typing import Callable, Sequence

import httpx
import ollama

from toram_skills.semantic_search import EmbeddingIndexError, EmbeddingUnavailable


class OllamaEmbeddingProvider:
    provider_name = "ollama"
    config_id = "default"

    def __init__(
        self,
        model_name: str,
        host: str | None = None,
        timeout_seconds: float = 30.0,
        *,
        client: object | None = None,
        client_factory: Callable[..., object] = ollama.Client,
    ) -> None:
        self.model_name = model_name
        if client is not None:
            self._client = client
            return

        options: dict[str, object] = {"timeout": timeout_seconds}
        if host is not None:
            options["host"] = host
        self._client = client_factory(**options)

    def _embed_inputs(self, inputs: list[str]) -> tuple[tuple[float, ...], ...]:
        try:
            response = self._client.embed(model=self.model_name, input=inputs)
        except ollama.ResponseError as exc:
            detail = getattr(exc, "error", str(exc))
            status_code = getattr(exc, "status_code", -1)
            message = f"Ollama embedding request failed: {detail}"
            if isinstance(status_code, int) and status_code >= 0:
                message += f" (HTTP {status_code})"
            raise EmbeddingUnavailable(message) from exc
        except (ConnectionError, httpx.TransportError) as exc:
            raise EmbeddingUnavailable(str(exc)) from exc
        except ollama.RequestError as exc:
            raise EmbeddingUnavailable(str(exc)) from exc

        embeddings = getattr(response, "embeddings", None)
        if embeddings is None and isinstance(response, dict):
            embeddings = response.get("embeddings")
        if embeddings is None:
            raise EmbeddingIndexError("Ollama embedding response has no embeddings field")

        try:
            vectors = tuple(tuple(float(value) for value in vector) for vector in embeddings)
        except (TypeError, ValueError) as exc:
            raise EmbeddingIndexError("Ollama embedding response is malformed") from exc
        if len(vectors) != len(inputs):
            raise EmbeddingIndexError(
                f"Ollama returned {len(vectors)} vectors for {len(inputs)} inputs"
            )
        return vectors

    def embed_documents(
        self,
        texts: Sequence[str],
    ) -> tuple[tuple[float, ...], ...]:
        return self._embed_inputs([str(text) for text in texts])

    def embed_query(self, text: str) -> tuple[float, ...]:
        vectors = self._embed_inputs([str(text)])
        if len(vectors) != 1:
            raise EmbeddingIndexError(
                f"Ollama returned {len(vectors)} vectors for one query"
            )
        return vectors[0]


__all__ = ["OllamaEmbeddingProvider"]
