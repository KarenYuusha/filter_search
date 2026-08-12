from __future__ import annotations

import math
import struct
from typing import Protocol, Sequence

from .repository import SkillRepository
from .search_models import ChannelScore, SkillSearchHit


class EmbeddingUnavailable(RuntimeError):
    pass


class EmbeddingIndexError(RuntimeError):
    pass


class EmbeddingProvider(Protocol):
    model_name: str

    def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]: ...


def _normalize_vector(
    values: Sequence[float],
    *,
    expected_dimensions: int | None = None,
) -> tuple[float, ...]:
    try:
        vector = tuple(float(value) for value in values)
    except (TypeError, ValueError) as exc:
        raise EmbeddingIndexError("Embedding contains a non-numeric value") from exc
    if not vector:
        raise EmbeddingIndexError("Embedding vector is empty")
    if expected_dimensions is not None and len(vector) != expected_dimensions:
        raise EmbeddingIndexError(
            f"Embedding dimension mismatch: expected {expected_dimensions}, got {len(vector)}"
        )
    if any(not math.isfinite(value) for value in vector):
        raise EmbeddingIndexError("Embedding contains NaN or infinity")
    norm = math.sqrt(sum(value * value for value in vector))
    if not math.isfinite(norm) or norm <= 0.0:
        raise EmbeddingIndexError("Embedding vector has zero or invalid norm")
    return tuple(value / norm for value in vector)


def _encode_vector(vector: tuple[float, ...]) -> bytes:
    return struct.pack(f"<{len(vector)}f", *vector)


def _decode_vector(data: bytes, dimensions: int) -> tuple[float, ...]:
    if dimensions <= 0 or len(data) != dimensions * 4:
        raise EmbeddingIndexError("Stored embedding vector has invalid byte length")
    try:
        values = struct.unpack(f"<{dimensions}f", data)
    except struct.error as exc:
        raise EmbeddingIndexError("Stored embedding vector is malformed") from exc
    return _normalize_vector(values, expected_dimensions=dimensions)


def _embed_batch(
    provider: EmbeddingProvider,
    texts: tuple[str, ...],
) -> tuple[tuple[float, ...], ...]:
    result = provider.embed(texts)
    vectors = tuple(tuple(vector) for vector in result)
    if len(vectors) != len(texts):
        raise EmbeddingIndexError(
            f"Embedding provider returned {len(vectors)} vectors for {len(texts)} inputs"
        )
    return vectors


def build_embedding_index(
    repository: SkillRepository,
    provider: EmbeddingProvider,
    *,
    batch_size: int = 32,
) -> int:
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    model_name = str(provider.model_name).strip()
    if not model_name:
        raise EmbeddingIndexError("Embedding model name is empty")

    rows = tuple(
        repository.connection.execute(
            """
            SELECT id, text, text_hash
            FROM skill_search_documents
            ORDER BY id
            """
        )
    )
    if not rows:
        raise EmbeddingIndexError("No skill search documents are available to embed")

    prepared: list[tuple[str, str, tuple[float, ...]]] = []
    dimensions: int | None = None
    for start in range(0, len(rows), batch_size):
        batch = rows[start : start + batch_size]
        texts = tuple(str(row["text"]) for row in batch)
        vectors = _embed_batch(provider, texts)
        for row, vector_values in zip(batch, vectors, strict=True):
            vector = _normalize_vector(
                vector_values,
                expected_dimensions=dimensions,
            )
            if dimensions is None:
                dimensions = len(vector)
            prepared.append((str(row["id"]), str(row["text_hash"]), vector))

    assert dimensions is not None
    document_manifest = repository.get_metadata("search_document_manifest_hash")
    if not document_manifest:
        raise EmbeddingIndexError("Search document manifest metadata is missing")

    repository.connection.execute("BEGIN")
    try:
        repository.connection.execute("DELETE FROM skill_embedding_vectors")
        repository.connection.executemany(
            """
            INSERT INTO skill_embedding_vectors(
                document_id, model, dimensions, text_hash, vector
            ) VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    document_id,
                    model_name,
                    dimensions,
                    text_hash,
                    _encode_vector(vector),
                )
                for document_id, text_hash, vector in prepared
            ],
        )
        repository.set_metadata("embedding_model", model_name)
        repository.set_metadata("embedding_dimensions", str(dimensions))
        repository.set_metadata("embedding_document_manifest_hash", document_manifest)
        repository.connection.execute("COMMIT")
    except Exception:
        repository.connection.execute("ROLLBACK")
        raise

    return len(prepared)


class SemanticSkillIndex:
    def __init__(
        self,
        provider: EmbeddingProvider,
        dimensions: int,
        documents: tuple[tuple[str, str, tuple[float, ...]], ...],
    ) -> None:
        self.provider = provider
        self.dimensions = dimensions
        self.documents = documents

    @classmethod
    def from_repository(
        cls,
        repository: SkillRepository,
        provider: EmbeddingProvider,
    ) -> "SemanticSkillIndex":
        model = repository.get_metadata("embedding_model")
        dimensions_text = repository.get_metadata("embedding_dimensions")
        embedding_manifest = repository.get_metadata("embedding_document_manifest_hash")
        document_manifest = repository.get_metadata("search_document_manifest_hash")

        if not model or not dimensions_text or not embedding_manifest or not document_manifest:
            raise EmbeddingIndexError("Embedding index metadata is incomplete")
        if model != provider.model_name:
            raise EmbeddingIndexError(
                f"Embedding model mismatch: database uses {model!r}, provider uses {provider.model_name!r}"
            )
        if embedding_manifest != document_manifest:
            raise EmbeddingIndexError("Embedding index is stale for the current search documents")
        try:
            dimensions = int(dimensions_text)
        except ValueError as exc:
            raise EmbeddingIndexError("Embedding dimension metadata is invalid") from exc
        if dimensions <= 0:
            raise EmbeddingIndexError("Embedding dimension metadata is invalid")

        document_count = int(
            repository.connection.execute("SELECT COUNT(*) FROM skill_search_documents").fetchone()[0]
        )
        rows = tuple(
            repository.connection.execute(
                """
                SELECT
                    v.document_id,
                    d.skill_id,
                    v.model,
                    v.dimensions,
                    v.text_hash AS vector_text_hash,
                    d.text_hash AS document_text_hash,
                    v.vector
                FROM skill_embedding_vectors AS v
                JOIN skill_search_documents AS d ON d.id = v.document_id
                ORDER BY v.document_id
                """
            )
        )
        if len(rows) != document_count:
            raise EmbeddingIndexError("Embedding index does not cover all search documents")

        documents: list[tuple[str, str, tuple[float, ...]]] = []
        for row in rows:
            if str(row["model"]) != model:
                raise EmbeddingIndexError("Embedding index contains mixed model names")
            if int(row["dimensions"]) != dimensions:
                raise EmbeddingIndexError("Embedding index contains mixed dimensions")
            if str(row["vector_text_hash"]) != str(row["document_text_hash"]):
                raise EmbeddingIndexError("Embedding vector text hash is stale")
            vector = _decode_vector(bytes(row["vector"]), dimensions)
            documents.append(
                (str(row["document_id"]), str(row["skill_id"]), vector)
            )
        return cls(provider, dimensions, tuple(documents))

    def search(
        self,
        query: str,
        *,
        eligible_skill_ids: tuple[str, ...] | None = None,
        limit: int = 20,
    ) -> tuple[SkillSearchHit, ...]:
        if limit <= 0:
            return ()
        if eligible_skill_ids is not None and not eligible_skill_ids:
            return ()

        query_vectors = _embed_batch(self.provider, (query,))
        query_vector = _normalize_vector(
            query_vectors[0],
            expected_dimensions=self.dimensions,
        )
        eligible = None if eligible_skill_ids is None else set(eligible_skill_ids)

        best_by_skill: dict[str, tuple[str, float]] = {}
        for document_id, skill_id, vector in self.documents:
            if eligible is not None and skill_id not in eligible:
                continue
            score = sum(left * right for left, right in zip(query_vector, vector, strict=True))
            current = best_by_skill.get(skill_id)
            if current is None or (-score, document_id) < (-current[1], current[0]):
                best_by_skill[skill_id] = (document_id, score)

        ordered = sorted(
            (
                (skill_id, document_id, score)
                for skill_id, (document_id, score) in best_by_skill.items()
            ),
            key=lambda value: (-value[2], value[0], value[1]),
        )[:limit]

        return tuple(
            SkillSearchHit(
                skill_id=skill_id,
                score=score,
                channels=(ChannelScore("semantic", rank, score),),
                evidence_document_ids=(document_id,),
            )
            for rank, (skill_id, document_id, score) in enumerate(ordered, start=1)
        )


__all__ = [
    "EmbeddingIndexError",
    "EmbeddingProvider",
    "EmbeddingUnavailable",
    "SemanticSkillIndex",
    "build_embedding_index",
]
