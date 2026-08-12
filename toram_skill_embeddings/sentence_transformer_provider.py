from __future__ import annotations

from typing import Callable, Sequence

from toram_skills.semantic_search import EmbeddingIndexError, EmbeddingUnavailable


_GTE_MULTILINGUAL_BASE = "alibaba-nlp/gte-multilingual-base"


class SentenceTransformerEmbeddingProvider:
    provider_name = "sentence-transformers"
    config_id = "ir-default"

    def __init__(
        self,
        model_name: str,
        *,
        device: str | None = None,
        model: object | None = None,
        model_factory: Callable[..., object] | None = None,
    ) -> None:
        self.model_name = model_name
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
        if model_name.strip().casefold() == _GTE_MULTILINGUAL_BASE:
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

    def embed_documents(
        self,
        texts: Sequence[str],
    ) -> tuple[tuple[float, ...], ...]:
        inputs = [str(text) for text in texts]
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
        try:
            output = self._model.encode_query(
                str(text),
                convert_to_numpy=True,
                normalize_embeddings=False,
            )
        except Exception as exc:
            raise EmbeddingUnavailable(
                f"Sentence Transformer query embedding failed: {exc}"
            ) from exc
        return self._query_from_output(output)


__all__ = ["SentenceTransformerEmbeddingProvider"]
