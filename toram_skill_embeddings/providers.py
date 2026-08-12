from __future__ import annotations

from dataclasses import dataclass

from toram_skills.semantic_search import EmbeddingProvider

from .ollama_provider import OllamaEmbeddingProvider
from .sentence_transformer_provider import SentenceTransformerEmbeddingProvider


@dataclass(frozen=True)
class EmbeddingProviderSpec:
    provider: str
    model: str


def parse_provider_spec(value: str) -> EmbeddingProviderSpec:
    raw = str(value).strip()
    if ":" not in raw:
        raise ValueError("Embedding provider spec must be '<provider>:<model>'")
    provider_text, model_text = raw.split(":", 1)
    provider_key = provider_text.strip().casefold()
    model = model_text.strip()
    aliases = {
        "st": "sentence-transformers",
        "sentence-transformers": "sentence-transformers",
        "ollama": "ollama",
    }
    provider = aliases.get(provider_key)
    if provider is None:
        raise ValueError(f"Unsupported embedding provider: {provider_text.strip()!r}")
    if not model:
        raise ValueError("Embedding model name must not be empty")
    return EmbeddingProviderSpec(provider=provider, model=model)


def build_provider(
    spec: EmbeddingProviderSpec,
    *,
    host: str | None = None,
    timeout_seconds: float = 120.0,
    device: str | None = None,
) -> EmbeddingProvider:
    if spec.provider == "sentence-transformers":
        return SentenceTransformerEmbeddingProvider(spec.model, device=device)
    if spec.provider == "ollama":
        return OllamaEmbeddingProvider(
            spec.model,
            host=host,
            timeout_seconds=timeout_seconds,
        )
    raise ValueError(f"Unsupported embedding provider: {spec.provider!r}")


__all__ = [
    "EmbeddingProviderSpec",
    "build_provider",
    "parse_provider_spec",
]
