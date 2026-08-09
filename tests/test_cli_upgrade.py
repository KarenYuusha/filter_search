from __future__ import annotations

import unittest

import search_items as core
from tests.test_discord_followup_regressions import FakeRepository, MustNotCallLLM


class CliRepository(FakeRepository):
    def get_item(self, item_id: int) -> core.ItemDetail:
        summary = next(item for item in self.list_items() if item.id == item_id)
        return core.ItemDetail(
            summary=summary,
            sell_price=None,
            process_material=None,
            process_amount=None,
            badge=None,
            note=None,
            page_url=None,
            stats=[],
            sources=[],
            images=[],
            upgrade_predecessors=[],
            upgrade_successors=[],
        )


class CliUpgradeTests(unittest.TestCase):
    def test_exact_upgrade_lists_direct_successors_not_full_chain(self):
        repository = CliRepository()
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

    def test_selecting_direct_upgrade_shows_item_detail(self):
        repository = CliRepository()
        answers = iter(["upgrade Don", "1", "quit"])
        output: list[str] = []

        result = core.interactive_search(
            repository,
            input_fn=lambda _prompt: next(answers),
            output_fn=output.append,
            llm_client=MustNotCallLLM(),
        )

        rendered = "\n".join(output)
        self.assertEqual(result, 0)
        self.assertIn("Don Upgrade A\n=============", rendered)
        self.assertIn("Type: Enhancer Crysta (Blue)", rendered)


if __name__ == "__main__":
    unittest.main()
