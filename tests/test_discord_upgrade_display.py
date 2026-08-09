from __future__ import annotations

import unittest
from pathlib import Path

import discord_bot
import search_items as core
from discord_bot import DiscordSessionManager
from toram_search.service import ServiceOutcome, UpgradeDetailPayload


def make_payload() -> UpgradeDetailPayload:
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
    return UpgradeDetailPayload(graph=graph, selected_item_id=upgrade_f.id)


class DiscordUpgradeDisplayTests(unittest.TestCase):
    def test_upgrade_embed_uses_selected_paths_and_monospace_full_tree(self):
        embed = discord_bot.build_upgrade_detail_embed(make_payload())

        self.assertEqual(embed.title, "Upgrade Tree — Don Upgrade F")
        self.assertIsNone(embed.description)
        fields = {field.name: field.value for field in embed.fields}
        self.assertEqual(set(fields), {"Selected paths", "Full tree"})
        self.assertIn(
            "1. Don → Don Alternative → Don Upgrade C → Don Upgrade F",
            fields["Selected paths"],
        )
        self.assertIn(
            "2. Don → Don Upgrade A → Don Upgrade C → Don Upgrade F",
            fields["Selected paths"],
        )
        self.assertTrue(fields["Full tree"].startswith("```text\n"))
        self.assertTrue(fields["Full tree"].endswith("\n```"))
        self.assertIn("Don Upgrade F  ◀ selected", fields["Full tree"])
        self.assertIn("Don Upgrade C  ↩ already shown", fields["Full tree"])

    def test_upgrade_detail_outcome_has_no_pagination_controls(self):
        payload = make_payload()
        sessions = DiscordSessionManager()
        key = (10, 30, 20)
        session = sessions.start_query(key, "upgrade Don Upgrade F")

        embed, view, file = discord_bot.build_service_outcome_message(
            ServiceOutcome("search", payload=payload),
            bot_example_prefix="@Toram Search",
            sessions=sessions,
            key=key,
            generation=session.generation,
            database_path=Path("coryn_data/database/items.sqlite"),
        )

        self.assertEqual(embed.title, "Upgrade Tree — Don Upgrade F")
        self.assertIsNone(view)
        self.assertIsNone(file)


if __name__ == "__main__":
    unittest.main()
