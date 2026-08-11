from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from types import SimpleNamespace

import discord_bot
from toram_search.service import ServiceOutcome
from toram_search.session import PendingItemSearch
from toram_search.understanding import ConfirmedItemChoice, understand_item_query


STATS = ["Critical Rate", "Critical Damage", "MaxHP"]
TYPES = {
    "Weapon Crysta",
    "Enhancer Crysta (Red)",
    "Armor",
    "Bow",
}
DATABASE = Path("coryn_data/database/items.sqlite")


def understand(query: str, choices: tuple[ConfirmedItemChoice, ...] = ()):
    return understand_item_query(
        query,
        available_stats=STATS,
        available_item_types=TYPES,
        confirmed_choices=choices,
    )


class FakeResponse:
    def __init__(self) -> None:
        self.edited: dict[str, object] | None = None

    async def edit_message(self, **kwargs) -> None:
        self.edited = kwargs


class DiscordItemUnderstandingLifecycleTests(unittest.TestCase):
    def make_interaction(self):
        return SimpleNamespace(
            response=FakeResponse(),
            guild=SimpleNamespace(get_member=lambda _user_id: None),
            client=SimpleNamespace(
                user=SimpleNamespace(id=99, display_name="Toram Search", name="Toram Search")
            ),
        )

    def make_view(self, query: str, pending: PendingItemSearch):
        sessions = discord_bot.DiscordSessionManager()
        key = (10, 30, 20)
        session = sessions.start_query(key, query)
        session.pending_item_search = pending
        view = discord_bot.ItemUnderstandingView(
            sessions=sessions,
            key=key,
            generation=session.generation,
            database_path=DATABASE,
            pending=pending,
        )
        return sessions, key, view

    def test_cancel_clears_pending_item_search(self):
        query = "highest xtall cr weapon"
        pending = PendingItemSearch(query, understand(query))
        sessions, key, view = self.make_view(query, pending)
        interaction = self.make_interaction()

        asyncio.run(view._cancel(interaction))

        self.assertIsNone(sessions.get(key).pending_item_search)
        self.assertEqual(interaction.response.edited["embed"].title, "Search cancelled")
        self.assertEqual(
            interaction.response.edited["embed"].description,
            "No search was executed.",
        )

    def test_fuzzy_no_uses_cancel_path_and_clears_pending(self):
        query = "cr wepon xtal"
        pending = PendingItemSearch(query, understand(query))
        sessions, key, view = self.make_view(query, pending)
        interaction = self.make_interaction()

        labels = [child.label for child in view.children if hasattr(child, "label")]
        self.assertIn("No", labels)
        asyncio.run(view._cancel(interaction))

        self.assertIsNone(sessions.get(key).pending_item_search)

    def test_edit_clears_final_confirmation_and_prompts_new_query(self):
        query = "crit wepon xtal"
        first = understand(query)
        stat_issue = first.uncertainties[0]
        after_stat_choices = (
            ConfirmedItemChoice(stat_issue.issue_id, "Critical Rate"),
        )
        after_stat = understand(query, after_stat_choices)
        filter_issue = after_stat.uncertainties[0]
        all_choices = (
            *after_stat_choices,
            ConfirmedItemChoice(filter_issue.issue_id, filter_issue.choices[0].value),
        )
        final = understand(query, all_choices)
        pending = PendingItemSearch(query, final, all_choices)
        sessions, key, view = self.make_view(query, pending)
        interaction = self.make_interaction()

        asyncio.run(view._edit(interaction))

        self.assertIsNone(sessions.get(key).pending_item_search)
        embed = interaction.response.edited["embed"]
        self.assertEqual(embed.title, "Search not executed")
        self.assertIn("Send a new @Toram Search query", embed.description)

    def test_outcome_rendering_stores_pending_item_search(self):
        query = "crit wepon xtal"
        pending = PendingItemSearch(query, understand(query))
        sessions = discord_bot.DiscordSessionManager()
        key = (10, 30, 20)
        session = sessions.start_query(key, query)

        embed, view, file = discord_bot.build_service_outcome_message(
            ServiceOutcome("item_understanding", pending_item_search=pending),
            bot_example_prefix="@Toram Search",
            sessions=sessions,
            key=key,
            generation=session.generation,
            database_path=DATABASE,
        )

        self.assertIs(sessions.get(key).pending_item_search, pending)
        self.assertIsInstance(view, discord_bot.ItemUnderstandingView)
        self.assertIsNone(file)
        self.assertEqual(embed.title, 'What does "crit" mean?')


if __name__ == "__main__":
    unittest.main()
