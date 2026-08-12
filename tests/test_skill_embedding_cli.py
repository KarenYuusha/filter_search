from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import patch

import benchmark_skill_embeddings
import build_skill_embeddings
from toram_skills.retrieval_benchmark import ModelBenchmarkResult, RetrievalMetrics


class FakeRepository:
    def __init__(self, path):
        self.path = path

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class BuildSkillEmbeddingsCliTests(unittest.TestCase):
    def test_parser_uses_provider_model_and_optional_device(self):
        parser = build_skill_embeddings._parser()
        destinations = {action.dest for action in parser._actions}
        self.assertIn("provider_model", destinations)
        self.assertIn("device", destinations)
        self.assertNotIn("model", destinations)

    def test_main_constructs_provider_from_provider_qualified_spec(self):
        self.assertTrue(hasattr(build_skill_embeddings, "build_provider"))
        self.assertTrue(hasattr(build_skill_embeddings, "parse_provider_spec"))

        calls = []
        provider = object()

        def fake_build_provider(spec, **kwargs):
            calls.append((spec, kwargs))
            return provider

        with (
            patch.object(build_skill_embeddings, "build_provider", fake_build_provider),
            patch.object(build_skill_embeddings, "SkillRepository", FakeRepository),
            patch.object(build_skill_embeddings, "build_embedding_index", return_value=42) as build_index,
        ):
            code = build_skill_embeddings.main(
                [
                    "--database",
                    "fake.sqlite",
                    "--provider-model",
                    "st:sentence-transformers/all-MiniLM-L6-v2",
                    "--device",
                    "cpu",
                ]
            )

        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 1)
        spec, kwargs = calls[0]
        self.assertEqual(spec.provider, "sentence-transformers")
        self.assertEqual(spec.model, "sentence-transformers/all-MiniLM-L6-v2")
        self.assertEqual(kwargs["device"], "cpu")
        self.assertIsNone(kwargs["host"])
        self.assertEqual(kwargs["timeout_seconds"], 120.0)
        build_index.assert_called_once_with(
            unittest.mock.ANY,
            provider,
            batch_size=32,
        )


class BenchmarkSkillEmbeddingsCliTests(unittest.TestCase):
    def test_default_candidates_are_the_three_approved_local_tiers(self):
        self.assertEqual(
            benchmark_skill_embeddings.DEFAULT_MODELS,
            (
                "st:sentence-transformers/all-MiniLM-L6-v2",
                "st:Alibaba-NLP/gte-multilingual-base",
                "st:BAAI/bge-m3",
            ),
        )

    def test_parser_supports_sentence_transformer_device(self):
        parser = benchmark_skill_embeddings._parser()
        destinations = {action.dest for action in parser._actions}
        self.assertIn("device", destinations)
        args = parser.parse_args(["--device", "cpu"])
        self.assertEqual(args.device, "cpu")
        self.assertEqual(tuple(args.models), benchmark_skill_embeddings.DEFAULT_MODELS)

    def test_result_payload_records_provider_model_and_dimensions(self):
        result = ModelBenchmarkResult(
            "sentence-transformers",
            "sentence-transformers/all-MiniLM-L6-v2",
            384,
            RetrievalMetrics(0.7, 0.8, 0.9, 4.0, 7.0),
        )
        payload = benchmark_skill_embeddings._result_payload(result)
        self.assertEqual(payload["provider"], "sentence-transformers")
        self.assertEqual(
            payload["model"],
            "sentence-transformers/all-MiniLM-L6-v2",
        )
        self.assertEqual(payload["dimensions"], 384)

    def test_provider_helper_uses_qualified_spec_and_device(self):
        self.assertTrue(hasattr(benchmark_skill_embeddings, "build_provider"))
        self.assertTrue(hasattr(benchmark_skill_embeddings, "parse_provider_spec"))
        self.assertTrue(hasattr(benchmark_skill_embeddings, "_provider"))

        calls = []
        expected = object()

        def fake_build_provider(spec, **kwargs):
            calls.append((spec, kwargs))
            return expected

        with patch.object(benchmark_skill_embeddings, "build_provider", fake_build_provider):
            actual = benchmark_skill_embeddings._provider(
                "st:BAAI/bge-m3",
                host="http://ollama.invalid",
                timeout_seconds=99.0,
                device="cpu",
            )

        self.assertIs(actual, expected)
        spec, kwargs = calls[0]
        self.assertEqual((spec.provider, spec.model), ("sentence-transformers", "BAAI/bge-m3"))
        self.assertEqual(kwargs["device"], "cpu")
        self.assertEqual(kwargs["host"], "http://ollama.invalid")
        self.assertEqual(kwargs["timeout_seconds"], 99.0)


if __name__ == "__main__":
    unittest.main()
