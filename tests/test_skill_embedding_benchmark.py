from __future__ import annotations

import inspect
import math
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx
import ollama

from toram_skill_embeddings.ollama_provider import OllamaEmbeddingProvider
from toram_skills.hybrid_search import FusionConfig
from toram_skills.retrieval_benchmark import (
    ModelBenchmarkResult,
    RetrievalMetrics,
    render_retrieval_config,
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
    def test_provider_identity_is_explicit(self):
        provider = OllamaEmbeddingProvider(
            "embeddinggemma:300m",
            client=FakeEmbeddingClient(),
        )
        self.assertEqual(provider.provider_name, "ollama")
        self.assertEqual(provider.model_name, "embeddinggemma:300m")
        self.assertEqual(provider.config_id, "default")

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

    def test_document_batches_use_official_api(self):
        fake = FakeEmbeddingClient(
            response=SimpleNamespace(embeddings=[[1.0, 2.0], [3.0, 4.0]])
        )
        provider = OllamaEmbeddingProvider("nomic-embed-text:v1.5", client=fake)

        result = provider.embed_documents(("alpha", "beta"))

        self.assertEqual(result, ((1.0, 2.0), (3.0, 4.0)))
        self.assertEqual(
            fake.calls,
            [{"model": "nomic-embed-text:v1.5", "input": ["alpha", "beta"]}],
        )

    def test_query_encoding_requires_exactly_one_vector(self):
        fake = FakeEmbeddingClient(response=SimpleNamespace(embeddings=[[1.0, 2.0]]))
        provider = OllamaEmbeddingProvider("embeddinggemma:300m", client=fake)

        self.assertEqual(provider.embed_query("alpha"), (1.0, 2.0))
        self.assertEqual(
            fake.calls,
            [{"model": "embeddinggemma:300m", "input": ["alpha"]}],
        )

    def test_response_count_mismatch_is_rejected(self):
        fake = FakeEmbeddingClient(response=SimpleNamespace(embeddings=[[1.0, 2.0]]))
        provider = OllamaEmbeddingProvider("embeddinggemma:300m", client=fake)
        with self.assertRaises(EmbeddingIndexError):
            provider.embed_documents(("alpha", "beta"))

    def test_query_response_count_mismatch_is_rejected(self):
        fake = FakeEmbeddingClient(
            response=SimpleNamespace(embeddings=[[1.0, 2.0], [3.0, 4.0]])
        )
        provider = OllamaEmbeddingProvider("embeddinggemma:300m", client=fake)
        with self.assertRaises(EmbeddingIndexError):
            provider.embed_query("alpha")

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
                    provider.embed_documents(("alpha",))


class EmbeddingBenchmarkSelectionTests(unittest.TestCase):
    @staticmethod
    def _result(
        provider: str,
        model: str,
        dimensions: int,
        metrics: RetrievalMetrics,
    ) -> ModelBenchmarkResult:
        """Construct against old or new API so RED tests reach selection behavior."""
        try:
            return ModelBenchmarkResult(provider, model, dimensions, metrics)
        except TypeError:
            return ModelBenchmarkResult(f"{provider}:{model}", metrics)

    @staticmethod
    def _model_name(result: ModelBenchmarkResult) -> str:
        value = result.model
        return value.split(":", 1)[1] if ":" in value and not hasattr(result, "provider") else value

    def test_lightweight_model_wins_inside_two_point_top5_window(self):
        results = (
            self._result(
                "sentence-transformers",
                "mini",
                384,
                RetrievalMetrics(0.70, 0.88, 0.96, 7.0, 10.0),
            ),
            self._result(
                "sentence-transformers",
                "bge",
                1024,
                RetrievalMetrics(0.73, 0.90, 0.98, 70.0, 90.0),
            ),
        )
        selected = select_embedding_model(results)
        self.assertEqual(self._model_name(selected), "mini")

    def test_quality_gain_over_two_points_excludes_lightweight_model(self):
        results = (
            self._result(
                "sentence-transformers",
                "mini",
                384,
                RetrievalMetrics(0.70, 0.88, 0.94, 7.0, 10.0),
            ),
            self._result(
                "sentence-transformers",
                "gte",
                768,
                RetrievalMetrics(0.76, 0.92, 0.97, 20.0, 30.0),
            ),
        )
        selected = select_embedding_model(results)
        self.assertEqual(self._model_name(selected), "gte")

    def test_exact_two_point_boundary_is_inside_quality_window(self):
        results = (
            self._result(
                "sentence-transformers",
                "mini",
                384,
                RetrievalMetrics(0.70, 0.88, 0.96, 5.0, 8.0),
            ),
            self._result(
                "sentence-transformers",
                "bge",
                1024,
                RetrievalMetrics(0.80, 0.95, 0.98, 50.0, 70.0),
            ),
        )
        self.assertEqual(self._model_name(select_embedding_model(results)), "mini")

    def test_p95_breaks_median_latency_tie(self):
        results = (
            self._result(
                "sentence-transformers",
                "spiky",
                384,
                RetrievalMetrics(0.80, 0.90, 0.97, 10.0, 40.0),
            ),
            self._result(
                "sentence-transformers",
                "steady",
                768,
                RetrievalMetrics(0.75, 0.88, 0.96, 10.0, 20.0),
            ),
        )
        self.assertEqual(self._model_name(select_embedding_model(results)), "steady")

    def test_relevance_breaks_equal_latency_after_quality_admission(self):
        results = (
            self._result(
                "sentence-transformers",
                "lower",
                384,
                RetrievalMetrics(0.70, 0.88, 0.97, 10.0, 20.0),
            ),
            self._result(
                "sentence-transformers",
                "higher",
                768,
                RetrievalMetrics(0.75, 0.91, 0.96, 10.0, 20.0),
            ),
        )
        self.assertEqual(self._model_name(select_embedding_model(results)), "higher")

    def test_smaller_dimensions_break_full_metric_tie(self):
        results = (
            self._result(
                "sentence-transformers",
                "large",
                1024,
                RetrievalMetrics(0.80, 0.90, 0.97, 10.0, 20.0),
            ),
            self._result(
                "sentence-transformers",
                "small",
                384,
                RetrievalMetrics(0.80, 0.90, 0.97, 10.0, 20.0),
            ),
        )
        self.assertEqual(self._model_name(select_embedding_model(results)), "small")

    def test_result_identity_contains_provider_and_dimensions(self):
        result = self._result(
            "sentence-transformers",
            "mini",
            384,
            RetrievalMetrics(1.0, 1.0, 1.0, 1.0, 1.0),
        )
        self.assertEqual(getattr(result, "provider", None), "sentence-transformers")
        self.assertEqual(getattr(result, "dimensions", None), 384)

    def test_invalid_selection_inputs_are_rejected(self):
        invalid = (
            self._result("", "model", 384, RetrievalMetrics(1.0, 1.0, 1.0, 1.0, 1.0)),
            self._result("st", "", 384, RetrievalMetrics(1.0, 1.0, 1.0, 1.0, 1.0)),
            self._result("st", "model", 0, RetrievalMetrics(1.0, 1.0, 1.0, 1.0, 1.0)),
            self._result("st", "model", 384, RetrievalMetrics(1.0, 1.0, math.nan, 1.0, 1.0)),
        )
        for result in invalid:
            with self.subTest(result=result), self.assertRaises(ValueError):
                select_embedding_model((result,))
        with self.assertRaises(ValueError):
            select_embedding_model((), quality_window=-0.01)

    def test_empty_results_are_rejected(self):
        with self.assertRaises(ValueError):
            select_embedding_model(())

    def test_retrieval_config_signature_and_output_include_provider_provenance(self):
        parameters = tuple(inspect.signature(render_retrieval_config).parameters)
        self.assertEqual(
            parameters,
            ("selected_provider", "selected_model", "selected_config_id", "fusion"),
        )
        if parameters == ("selected_provider", "selected_model", "selected_config_id", "fusion"):
            text = render_retrieval_config(
                "sentence-transformers",
                "sentence-transformers/all-MiniLM-L6-v2",
                "ir-default",
                FusionConfig(rrf_k=20, lexical_weight=1.5, semantic_weight=0.5),
            )
            self.assertEqual(
                text,
                "from toram_skills.hybrid_search import FusionConfig\n\n"
                "DEFAULT_EMBEDDING_PROVIDER = 'sentence-transformers'\n"
                "DEFAULT_EMBEDDING_MODEL = 'sentence-transformers/all-MiniLM-L6-v2'\n"
                "DEFAULT_EMBEDDING_CONFIG_ID = 'ir-default'\n"
                "DEFAULT_FUSION_CONFIG = FusionConfig(\n"
                "    rrf_k=20,\n"
                "    lexical_weight=1.5,\n"
                "    semantic_weight=0.5,\n"
                ")\n",
            )


if __name__ == "__main__":
    unittest.main()
