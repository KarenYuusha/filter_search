from __future__ import annotations

import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx
import ollama

from toram_skill_embeddings.ollama_provider import OllamaEmbeddingProvider
from toram_skills.retrieval_benchmark import (
    ModelBenchmarkResult,
    RetrievalMetrics,
    select_embedding_model,
)
from toram_skills.semantic_search import EmbeddingIndexError, EmbeddingUnavailable


class FakeEmbeddingClient:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def embed(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


class OllamaEmbeddingProviderTests(unittest.TestCase):
    def test_explicit_host_uses_official_client_options(self):
        built = {}

        def factory(**kwargs):
            built.update(kwargs)
            return FakeEmbeddingClient()

        OllamaEmbeddingProvider(
            "embeddinggemma:300m",
            host="http://ollama.test:11434",
            client_factory=factory,
        )
        self.assertEqual(
            built,
            {"host": "http://ollama.test:11434", "timeout": 30.0},
        )

    def test_omits_host_so_ollama_environment_can_apply(self):
        built = {}

        def factory(**kwargs):
            built.update(kwargs)
            return FakeEmbeddingClient()

        with patch.dict(os.environ, {"OLLAMA_HOST": "http://env:11434"}, clear=False):
            OllamaEmbeddingProvider("qwen3-embedding:0.6b", client_factory=factory)
        self.assertEqual(built, {"timeout": 30.0})

    def test_embed_batches_inputs_through_official_api(self):
        fake = FakeEmbeddingClient(
            response=SimpleNamespace(embeddings=[[1.0, 2.0], [3.0, 4.0]])
        )
        provider = OllamaEmbeddingProvider("nomic-embed-text:v1.5", client=fake)

        result = provider.embed(("alpha", "beta"))

        self.assertEqual(result, ((1.0, 2.0), (3.0, 4.0)))
        self.assertEqual(
            fake.calls,
            [{"model": "nomic-embed-text:v1.5", "input": ["alpha", "beta"]}],
        )

    def test_response_count_mismatch_is_rejected(self):
        fake = FakeEmbeddingClient(response=SimpleNamespace(embeddings=[[1.0, 2.0]]))
        provider = OllamaEmbeddingProvider("embeddinggemma:300m", client=fake)
        with self.assertRaises(EmbeddingIndexError):
            provider.embed(("alpha", "beta"))

    def test_transport_and_server_errors_become_unavailable(self):
        errors = (
            httpx.ReadTimeout("timed out"),
            ConnectionError("connection refused"),
            ollama.ResponseError("model missing", 404),
        )
        for error in errors:
            with self.subTest(error=type(error).__name__):
                provider = OllamaEmbeddingProvider(
                    "embeddinggemma:300m",
                    client=FakeEmbeddingClient(error=error),
                )
                with self.assertRaises(EmbeddingUnavailable):
                    provider.embed(("alpha",))


class EmbeddingBenchmarkSelectionTests(unittest.TestCase):
    def test_selection_prefers_top5_then_top3_then_top1_then_latency(self):
        results = (
            ModelBenchmarkResult(
                "a",
                RetrievalMetrics(top1=0.70, top3=0.85, top5=0.95, median_ms=20.0, p95_ms=30.0),
            ),
            ModelBenchmarkResult(
                "b",
                RetrievalMetrics(top1=0.75, top3=0.90, top5=0.95, median_ms=30.0, p95_ms=40.0),
            ),
            ModelBenchmarkResult(
                "c",
                RetrievalMetrics(top1=0.80, top3=0.88, top5=0.94, median_ms=10.0, p95_ms=15.0),
            ),
        )
        self.assertEqual(select_embedding_model(results).model, "b")

    def test_latency_breaks_complete_relevance_tie(self):
        results = (
            ModelBenchmarkResult("slow", RetrievalMetrics(1.0, 1.0, 1.0, 30.0, 40.0)),
            ModelBenchmarkResult("fast", RetrievalMetrics(1.0, 1.0, 1.0, 10.0, 20.0)),
        )
        self.assertEqual(select_embedding_model(results).model, "fast")

    def test_empty_results_are_rejected(self):
        with self.assertRaises(ValueError):
            select_embedding_model(())


if __name__ == "__main__":
    unittest.main()
