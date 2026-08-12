from pathlib import Path
import tomllib
import unittest


ROOT = Path(__file__).resolve().parents[1]


class SkillEmbeddingDependencyTests(unittest.TestCase):
    def test_optional_embedding_runtime_pins_transformers_below_v5(self):
        payload = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        dependencies = payload["project"]["optional-dependencies"]["embeddings"]
        self.assertIn("sentence-transformers>=5,<6", dependencies)
        self.assertIn("transformers>=4.41,<5", dependencies)


if __name__ == "__main__":
    unittest.main()
