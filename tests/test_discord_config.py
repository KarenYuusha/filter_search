from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from toram_discord.config import load_config
from toram_search.llm import OllamaQwenClient


BASE_ENV = {
    "DISCORD_BOT_TOKEN": "token",
    "DISCORD_GUILD_ID": "333",
}


class DatabaseChatConfigTests(unittest.TestCase):
    def test_model_and_rag_defaults(self):
        config = load_config(BASE_ENV)

        self.assertEqual(config.item_llm_model, "qwen3.5:2b")
        self.assertEqual(config.skill_rag_model, "gemma4:e4b")
        self.assertIsNone(config.ollama_host)
        self.assertEqual(config.skill_rag_top_k, 5)
        self.assertEqual(config.skill_rag_max_context_chars, 12000)
        self.assertEqual(config.skill_rag_max_output_tokens, 256)
        self.assertEqual(config.skill_rag_keep_alive, "10m")

    def test_model_and_rag_overrides_are_independent(self):
        config = load_config(
            {
                **BASE_ENV,
                "ITEM_LLM_MODEL": "item-model:1b",
                "SKILL_RAG_MODEL": "skill-model:4b",
                "OLLAMA_HOST": "  http://ollama.test:11434  ",
                "SKILL_RAG_TOP_K": "7",
                "SKILL_RAG_MAX_CONTEXT_CHARS": "9000",
                "SKILL_RAG_MAX_OUTPUT_TOKENS": "128",
                "SKILL_RAG_KEEP_ALIVE": "5m",
            }
        )

        self.assertEqual(config.item_llm_model, "item-model:1b")
        self.assertEqual(config.skill_rag_model, "skill-model:4b")
        self.assertEqual(config.ollama_host, "http://ollama.test:11434")
        self.assertEqual(config.skill_rag_top_k, 7)
        self.assertEqual(config.skill_rag_max_context_chars, 9000)
        self.assertEqual(config.skill_rag_max_output_tokens, 128)
        self.assertEqual(config.skill_rag_keep_alive, "5m")

    def test_empty_ollama_host_becomes_none(self):
        config = load_config({**BASE_ENV, "OLLAMA_HOST": "   "})
        self.assertIsNone(config.ollama_host)

    def test_rag_integer_settings_must_be_positive(self):
        for name in (
            "SKILL_RAG_TOP_K",
            "SKILL_RAG_MAX_CONTEXT_CHARS",
            "SKILL_RAG_MAX_OUTPUT_TOKENS",
        ):
            with self.subTest(name=name):
                with self.assertRaisesRegex(RuntimeError, name):
                    load_config({**BASE_ENV, name: "0"})

    def test_item_client_prefers_item_model_environment(self):
        with patch.dict(
            os.environ,
            {"ITEM_LLM_MODEL": "item-new:2b", "OLLAMA_MODEL": "legacy:2b"},
            clear=True,
        ):
            client = OllamaQwenClient(client=object())

        self.assertEqual(client.model, "item-new:2b")

    def test_item_client_keeps_legacy_ollama_model_fallback(self):
        with patch.dict(os.environ, {"OLLAMA_MODEL": "legacy:2b"}, clear=True):
            client = OllamaQwenClient(client=object())

        self.assertEqual(client.model, "legacy:2b")

    def test_item_model_environment_does_not_change_skill_default(self):
        config = load_config({**BASE_ENV, "ITEM_LLM_MODEL": "item-only:2b"})

        self.assertEqual(config.item_llm_model, "item-only:2b")
        self.assertEqual(config.skill_rag_model, "gemma4:e4b")


if __name__ == "__main__":
    unittest.main()
