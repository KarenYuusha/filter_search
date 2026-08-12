from __future__ import annotations

from typing import Callable, Sequence

from toram_skills.semantic_search import EmbeddingIndexError, EmbeddingUnavailable


_GTE_MULTILINGUAL_BASE = "alibaba-nlp/gte-multilingual-base"
_SYMMETRIC_ENCODE_MODELS = {
    "sentence-transformers/all-minilm-l6-v2",
    _GTE_MULTILINGUAL_BASE,
    "baai/bge-m3",
}


class SentenceTransformerEmbeddingProvider:
    provider_name = "sentence-transformers"

    def __init__(
        self,
        model_name: str,
        *,
        device: str | None = None,
        model: object | None = None,
        model_factory: Callable[..., object] | None = None,
    ) -> None:
        self.model_name = model_name
        normalized_model = model_name.strip().casefold()
        self._symmetric_encode = normalized_model in _SYMMETRIC_ENCODE_MODELS
        self.config_id = (
            "symmetric-encode-v1"
            if self._symmetric_encode
            else "ir-query-document-v1"
        )
        if model is not None:
            self._model = model
            return

        if model_factory is None:
            try:
                from sentence_transformers import SentenceTransformer
            except Exception as exc:
                raise EmbeddingUnavailable(
                    "Sentence Transformers runtime is unavailable"
                ) from exc
            model_factory = SentenceTransformer

        kwargs: dict[str, object] = {}
        if device is not None:
            kwargs["device"] = device
        if normalized_model == _GTE_MULTILINGUAL_BASE:
            kwargs["trust_remote_code"] = True
        try:
            self._model = model_factory(model_name, **kwargs)
        except Exception as exc:
            raise EmbeddingUnavailable(
                f"Could not load Sentence Transformer model {model_name!r}: {exc}"
            ) from exc

    @staticmethod
    def _documents_from_output(
        output: object,
        expected_count: int,
    ) -> tuple[tuple[float, ...], ...]:
        try:
            vectors = tuple(
                tuple(float(value) for value in vector)
                for vector in output  # type: ignore[union-attr]
            )
        except (TypeError, ValueError) as exc:
            raise EmbeddingIndexError(
                "Sentence Transformer document embeddings are malformed"
            ) from exc
        if len(vectors) != expected_count:
            raise EmbeddingIndexError(
                "Sentence Transformer returned "
                f"{len(vectors)} vectors for {expected_count} documents"
            )
        return vectors

    @staticmethod
    def _query_from_output(output: object) -> tuple[float, ...]:
        try:
            return tuple(float(value) for value in output)  # type: ignore[union-attr]
        except (TypeError, ValueError) as exc:
            raise EmbeddingIndexError(
                "Sentence Transformer query embedding is malformed"
            ) from exc

    def _encode_symmetric(
        self,
        texts: Sequence[str],
    ) -> tuple[tuple[float, ...], ...]:
        inputs = [str(text) for text in texts]
        try:
            output = self._model.encode(
                inputs,
                convert_to_numpy=True,
                normalize_embeddings=False,
            )
        except Exception as exc:
            raise EmbeddingUnavailable(
                f"Sentence Transformer embedding failed: {exc}"
            ) from exc
        return self._documents_from_output(output, len(inputs))

    def embed_documents(
        self,
        texts: Sequence[str],
    ) -> tuple[tuple[float, ...], ...]:
        inputs = [str(text) for text in texts]
        if self._symmetric_encode:
            return self._encode_symmetric(inputs)
        try:
            output = self._model.encode_document(
                inputs,
                convert_to_numpy=True,
                normalize_embeddings=False,
            )
        except Exception as exc:
            raise EmbeddingUnavailable(
                f"Sentence Transformer document embedding failed: {exc}"
            ) from exc
        return self._documents_from_output(output, len(inputs))

    def embed_query(self, text: str) -> tuple[float, ...]:
        query = str(text)
        if self._symmetric_encode:
            vectors = self._encode_symmetric((query,))
            if len(vectors) != 1:
                raise EmbeddingIndexError(
                    f"Sentence Transformer returned {len(vectors)} vectors for one query"
                )
            return vectors[0]
        try:
            output = self._model.encode_query(
                query,
                convert_to_numpy=True,
                normalize_embeddings=False,
            )
        except Exception as exc:
            raise EmbeddingUnavailable(
                f"Sentence Transformer query embedding failed: {exc}"
            ) from exc
        return self._query_from_output(output)


__all__ = ["SentenceTransformerEmbeddingProvider"]
