from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace
import unittest

from toram_skills.semantic_search import EmbeddingIndexError, EmbeddingUnavailable


class FakeSentenceTransformer:
    def __init__(self, *, error: Exception | None = None, malformed: bool = False):
        self.error = error
        self.malformed = malformed
        self.document_calls = []
        self.query_calls = []

    def encode_document(self, texts, **kwargs):
        self.document_calls.append((list(texts), dict(kwargs)))
        if self.error is not None:
            raise self.error
        if self.malformed:
            return [["bad", 1.0] for _ in texts]
        vectors = [[1.0, 0.0], [0.0, 1.0]]
        return vectors[: len(texts)]

    def encode_query(self, text, **kwargs):
        self.query_calls.append((text, dict(kwargs)))
        if self.error is not None:
            raise self.error
        if self.malformed:
            return ["bad", 1.0]
        return [0.5, 0.5]


class SentenceTransformerEmbeddingProviderTests(unittest.TestCase):
    def _provider_class(self):
        try:
            module = importlib.import_module(
                "toram_skill_embeddings.sentence_transformer_provider"
            )
        except ModuleNotFoundError as exc:
            self.fail(f"Sentence Transformer provider module is missing: {exc}")
        return module.SentenceTransformerEmbeddingProvider

    def test_module_import_does_not_require_sentence_transformers_runtime(self):
        sys.modules.pop("sentence_transformers", None)
        provider_class = self._provider_class()
        provider = provider_class(
            "sentence-transformers/all-MiniLM-L6-v2",
            model=FakeSentenceTransformer(),
        )
        self.assertNotIn("sentence_transformers", sys.modules)
        self.assertEqual(provider.provider_name, "sentence-transformers")
        self.assertEqual(provider.config_id, "ir-default")

    def test_document_and_query_encoders_use_ir_specific_public_methods(self):
        fake = FakeSentenceTransformer()
        provider = self._provider_class()(
            "sentence-transformers/all-MiniLM-L6-v2",
            model=fake,
        )

        self.assertEqual(
            provider.embed_documents(("alpha", "beta")),
            ((1.0, 0.0), (0.0, 1.0)),
        )
        self.assertEqual(provider.embed_query("question"), (0.5, 0.5))
        self.assertEqual(
            fake.document_calls,
            [
                (
                    ["alpha", "beta"],
                    {"convert_to_numpy": True, "normalize_embeddings": False},
                )
            ],
        )
        self.assertEqual(
            fake.query_calls,
            [
                (
                    "question",
                    {"convert_to_numpy": True, "normalize_embeddings": False},
                )
            ],
        )

    def test_malformed_sentence_transformer_output_is_rejected(self):
        provider = self._provider_class()(
            "sentence-transformers/all-MiniLM-L6-v2",
            model=FakeSentenceTransformer(malformed=True),
        )
        with self.assertRaises(EmbeddingIndexError):
            provider.embed_documents(("alpha",))
        with self.assertRaises(EmbeddingIndexError):
            provider.embed_query("alpha")

    def test_sentence_transformer_inference_failure_becomes_unavailable(self):
        provider = self._provider_class()(
            "sentence-transformers/all-MiniLM-L6-v2",
            model=FakeSentenceTransformer(error=RuntimeError("device failed")),
        )
        with self.assertRaises(EmbeddingUnavailable):
            provider.embed_documents(("alpha",))
        with self.assertRaises(EmbeddingUnavailable):
            provider.embed_query("alpha")

    def test_sentence_transformer_factory_failure_becomes_unavailable(self):
        def factory(*args, **kwargs):
            raise RuntimeError("model unavailable")

        with self.assertRaises(EmbeddingUnavailable):
            self._provider_class()(
                "sentence-transformers/all-MiniLM-L6-v2",
                model_factory=factory,
            )


class EmbeddingProviderSpecTests(unittest.TestCase):
    def _providers_module(self):
        try:
            return importlib.import_module("toram_skill_embeddings.providers")
        except ModuleNotFoundError as exc:
            self.fail(f"Embedding provider factory module is missing: {exc}")

    def test_provider_specs_preserve_model_colons_and_canonicalize_aliases(self):
        parse = self._providers_module().parse_provider_spec
        values = {
            "st:sentence-transformers/all-MiniLM-L6-v2": (
                "sentence-transformers",
                "sentence-transformers/all-MiniLM-L6-v2",
            ),
            "sentence-transformers:Alibaba-NLP/gte-multilingual-base": (
                "sentence-transformers",
                "Alibaba-NLP/gte-multilingual-base",
            ),
            "st:BAAI/bge-m3": ("sentence-transformers", "BAAI/bge-m3"),
            "ollama:qwen3-embedding:0.6b": ("ollama", "qwen3-embedding:0.6b"),
        }
        for text, expected in values.items():
            with self.subTest(text=text):
                spec = parse(text)
                self.assertEqual((spec.provider, spec.model), expected)

    def test_invalid_provider_specs_are_rejected(self):
        parse = self._providers_module().parse_provider_spec
        for value in ("", "st:", ":model", "unknown:model", "model-only"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse(value)

    def test_build_provider_routes_sentence_transformers_without_ollama_options(self):
        module = self._providers_module()
        spec = module.parse_provider_spec("st:BAAI/bge-m3")
        calls = []

        class FakeProvider:
            def __init__(self, *args, **kwargs):
                calls.append((args, kwargs))

        original = module.SentenceTransformerEmbeddingProvider
        module.SentenceTransformerEmbeddingProvider = FakeProvider
        try:
            module.build_provider(
                spec,
                host="http://ollama.invalid",
                timeout_seconds=12.0,
                device="cpu",
            )
        finally:
            module.SentenceTransformerEmbeddingProvider = original
        self.assertEqual(calls, [(('BAAI/bge-m3',), {'device': 'cpu'})])


if __name__ == "__main__":
    unittest.main()
