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
    def _run(self, query: str, *extra_answers: str) -> str:
        repository = CliRepository()
        answers = iter([query, *extra_answers, "quit"])
        output: list[str] = []
        result = core.interactive_search(
            repository,
            input_fn=lambda _prompt: next(answers),
            output_fn=output.append,
            llm_client=MustNotCallLLM(),
        )
        self.assertEqual(result, 0)
        return "\n".join(output)

    def _assert_complete_chain(self, rendered: str, selected_name: str):
        self.assertIn(f"Upgrade chain for {selected_name}", rendered)
        self.assertIn("Don", rendered)
        self.assertIn("Don Upgrade A", rendered)
        self.assertIn("Don Upgrade B", rendered)
        self.assertNotIn("No direct upgrade crystas found", rendered)

    def test_first_upgrade_item_shows_complete_chain(self):
        self._assert_complete_chain(self._run("upgrade Don"), "Don")

    def test_middle_upgrade_item_shows_complete_chain(self):
        self._assert_complete_chain(
            self._run("upgrade Don Upgrade A"),
            "Don Upgrade A",
        )

    def test_last_upgrade_item_still_shows_complete_chain(self):
        self._assert_complete_chain(
            self._run("upgrade Don Upgrade B"),
            "Don Upgrade B",
        )

    def test_natural_upgrade_query_uses_complete_chain_without_qwen(self):
        self._assert_complete_chain(
            self._run("what upgrades from Don?"),
            "Don",
        )

    def test_fuzzy_selected_last_item_shows_complete_chain(self):
        rendered = self._run("upgrade Don Upgrad B", "1")
        self._assert_complete_chain(rendered, "Don Upgrade B")


if __name__ == "__main__":
    unittest.main()
