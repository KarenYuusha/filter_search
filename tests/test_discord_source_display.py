import unittest

import discord_bot
import search_items as core


class DiscordSourceDisplayTests(unittest.TestCase):
    def _detail(self, sources):
        return core.ItemDetail(
            summary=core.ItemSummary(513, "Mage Robe", "Armor"),
            sell_price=None,
            process_material=None,
            process_amount=None,
            badge=None,
            note=None,
            page_url=None,
            stats=[],
            sources=sources,
            images=[],
            upgrade_predecessors=[],
            upgrade_successors=[],
        )

    def _obtained_from(self, detail):
        embed = discord_bot.build_item_detail_embed(detail)
        field = next(field for field in embed.fields if field.name == "Obtained From")
        return field.value

    def test_matching_level_already_in_source_name_is_not_repeated(self):
        value = self._obtained_from(
            self._detail(
                [
                    {
                        "source_name": "Fire Ghost (Lv 28)",
                        "level": 28,
                        "map": "Fiery Volcano: Area 1",
                        "dye": None,
                    }
                ]
            )
        )

        self.assertEqual(value, "Fire Ghost (Lv 28) — Fiery Volcano: Area 1")

    def test_level_is_kept_when_source_name_does_not_already_show_it(self):
        value = self._obtained_from(
            self._detail(
                [
                    {
                        "source_name": "Fire Ghost",
                        "level": 28,
                        "map": "Fiery Volcano: Area 1",
                        "dye": None,
                    }
                ]
            )
        )

        self.assertEqual(value, "Fire Ghost — Lv 28 — Fiery Volcano: Area 1")

    def test_dye_and_map_are_preserved_when_level_is_deduplicated(self):
        value = self._obtained_from(
            self._detail(
                [
                    {
                        "source_name": "Jeila (Lv 50)",
                        "level": 50,
                        "map": "Halloween Castle",
                        "dye": "41, 40, 85",
                    }
                ]
            )
        )

        self.assertEqual(value, "Jeila (Lv 50) — Halloween Castle — Dye: 41, 40, 85")


if __name__ == "__main__":
    unittest.main()
