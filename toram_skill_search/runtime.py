from __future__ import annotations

from pathlib import Path
import threading

from toram_skills.repository import SkillRepository
from toram_skills.retrieval_config import (
    DEFAULT_EMBEDDING_CONFIG_ID,
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_EMBEDDING_PROVIDER,
)
from toram_skills.semantic_search import (
    EmbeddingIndexError,
    EmbeddingUnavailable,
    SemanticSkillIndex,
)


class SemanticRuntimeCache:
    def __init__(self, *, provider_factory=None, index_factory=None) -> None:
        self._lock = threading.Lock()
        self._provider_factory = provider_factory
        self._index_factory = index_factory or SemanticSkillIndex.from_repository
        self._provider = None
        self._indexes: dict[tuple[str, str], object] = {}

    def _make_provider(self):
        if self._provider_factory is not None:
            return self._provider_factory()
        from toram_skill_embeddings.providers import EmbeddingProviderSpec, build_provider

        return build_provider(
            EmbeddingProviderSpec(
                provider=DEFAULT_EMBEDDING_PROVIDER,
                model=DEFAULT_EMBEDDING_MODEL,
            )
        )

    def get_index(self, repository: SkillRepository) -> SemanticSkillIndex | None:
        manifest = repository.get_metadata("search_document_manifest_hash") or ""
        key = (str(Path(repository.database_path).resolve()), manifest)
        cached = self._indexes.get(key)
        if cached is not None:
            return cached  # type: ignore[return-value]
        try:
            with self._lock:
                cached = self._indexes.get(key)
                if cached is not None:
                    return cached  # type: ignore[return-value]
                provider = self._provider
                if provider is None:
                    provider = self._make_provider()
                    if str(provider.config_id) != DEFAULT_EMBEDDING_CONFIG_ID:
                        raise EmbeddingIndexError(
                            "Configured embedding runtime ID does not match retrieval config"
                        )
                    self._provider = provider
                index = self._index_factory(repository, provider)
                self._indexes[key] = index
                return index
        except (EmbeddingUnavailable, EmbeddingIndexError, ImportError):
            return None


DEFAULT_SEMANTIC_RUNTIME = SemanticRuntimeCache()


__all__ = ["DEFAULT_SEMANTIC_RUNTIME", "SemanticRuntimeCache"]
