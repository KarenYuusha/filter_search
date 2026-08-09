from __future__ import annotations

import unittest

import search_items as core
from tests.test_discord_followup_regressions import FakeRepository, MustNotCallLLM


class CliUpgradeTests(unittest.TestCase):
    def test_exact_upgrade_lists_direct_successors_not_full_chain(self):
        repository = FakeRepository()
        answers = iter(["upgrade Don", "quit"])
        output: list[str] = []

        result = core.interactive_search(
            repository,
            input_fn=lambda _prompt: next(answers),
            output_fn=output.append,
            llm_client=MustNotCallLLM(),
        )

        rendered = "\n".join(output)
        self.assertEqual(result, 0)
        self.assertIn("Upgrades from Don", rendered)
        self.assertIn("1. Don Upgrade A — Enhancer Crysta (Blue)", rendered)
        self.assertIn("2. Don Upgrade B — Enhancer Crysta (Blue)", rendered)
        self.assertNotIn("Upgrade chain for Don", rendered)


if __name__ == "__main__":
    unittest.main()
