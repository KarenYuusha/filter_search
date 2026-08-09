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
        self.assertIn(f"Upgrade Tree — {selected_name}", rendered)
        self.assertIn("Selected paths", rendered)
        self.assertIn("Full tree", rendered)
        self.assertIn("Don", rendered)
        self.assertIn("Don Upgrade A", rendered)
        self.assertIn("Don Upgrade B", rendered)
        self.assertIn(f"{selected_name}  ◀ selected", rendered)
        self.assertNotIn("No direct upgrade crystas found", rendered)
        self.assertNotIn("ID:", rendered)
        self.assertNotIn("— ID ", rendered)

    def test_first_upgrade_item_shows_complete_chain(self):
        rendered = self._run("upgrade Don")
        self._assert_complete_chain(rendered, "Don")
        self.assertIn("1. Don", rendered)

    def test_middle_upgrade_item_shows_complete_chain(self):
        rendered = self._run("upgrade Don Upgrade A")
        self._assert_complete_chain(rendered, "Don Upgrade A")
        self.assertIn("1. Don → Don Upgrade A", rendered)

    def test_last_upgrade_item_still_shows_complete_chain(self):
        rendered = self._run("upgrade Don Upgrade B")
        self._assert_complete_chain(rendered, "Don Upgrade B")
        self.assertIn("1. Don → Don Upgrade A → Don Upgrade B", rendered)

    def test_natural_upgrade_query_uses_complete_chain_without_qwen(self):
        self._assert_complete_chain(
            self._run("what upgrades from Don?"),
            "Don",
        )

    def test_fuzzy_selected_last_item_shows_complete_chain(self):
        rendered = self._run("upgrade Don Upgrad B", "2")
        self._assert_complete_chain(rendered, "Don Upgrade B")

    def test_terminal_render_shows_all_paths_and_collapses_shared_node(self):
        don = core.ItemSummary(10, "Don", "Normal Crysta")
        alternative = core.ItemSummary(11, "Don Alternative", "Enhancer Crysta (Blue)")
        upgrade_a = core.ItemSummary(12, "Don Upgrade A", "Enhancer Crysta (Blue)")
        upgrade_c = core.ItemSummary(13, "Don Upgrade C", "Enhancer Crysta (Blue)")
        upgrade_f = core.ItemSummary(14, "Don Upgrade F", "Enhancer Crysta (Blue)")
        graph = core.UpgradeGraph(
            nodes={item.id: item for item in (don, alternative, upgrade_a, upgrade_c, upgrade_f)},
            edges={
                don.id: (upgrade_a.id, alternative.id),
                alternative.id: (upgrade_c.id,),
                upgrade_a.id: (upgrade_c.id,),
                upgrade_c.id: (upgrade_f.id,),
                upgrade_f.id: (),
            },
            missing_nodes={},
        )

        rendered = core.render_upgrade_terminal(graph, upgrade_f.id)

        self.assertIn("Upgrade Tree — Don Upgrade F", rendered)
        self.assertIn(
            "1. Don → Don Alternative → Don Upgrade C → Don Upgrade F",
            rendered,
        )
        self.assertIn(
            "2. Don → Don Upgrade A → Don Upgrade C → Don Upgrade F",
            rendered,
        )
        self.assertIn("Don Upgrade F  ◀ selected", rendered)
        self.assertIn("Don Upgrade C  ↩ already shown", rendered)
        self.assertNotIn("ID:", rendered)
        self.assertNotIn("— ID ", rendered)

    def test_plain_natural_multi_stat_query_stays_deterministic_in_cli(self):
        rendered = self._run("bow has cr and ampr")
        self.assertIn(
            "Expression: Critical Rate >= 1 AND Attack MP Recovery >= 1",
            rendered,
        )
        self.assertIn("Filter: Bow", rendered)
        self.assertNotIn("I couldn't interpret that search", rendered)
        self.assertNotIn("Automatic interpretation unavailable", rendered)


if __name__ == "__main__":
    unittest.main()
