from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace

import search_items as core
from discord_bot import is_allowed_message, load_config
from toram_search.service import SearchService, UpgradeResultsPayload
from toram_search.session import FailedQueryContext


class MustNotCallLLM:
    def complete(self, *args, **kwargs):
        raise AssertionError("deterministic query must not call Qwen")


class RecordingLLM:
    def __init__(self):
        self.calls = 0

    def complete(self, *args, **kwargs):
        self.calls += 1
        return {"intent": "refuse"}


class FakeRepository:
    def __init__(self):
        self.don = core.ItemSummary(1, "Don", "Normal Crysta")
        self.upgrade_a = core.ItemSummary(2, "Don Upgrade A", "Enhancer Crysta (Blue)")
        self.upgrade_b = core.ItemSummary(3, "Don Upgrade B", "Enhancer Crysta (Blue)")
        self.other = core.ItemSummary(4, "Other Crysta", "Normal Crysta")

    def list_items(self):
        return [self.don, self.upgrade_a, self.upgrade_b, self.other]

    def list_item_types(self):
        return {item.item_type for item in self.list_items()}

    def list_stat_names(self):
        return ["Critical Rate", "MaxHP"]

    def exact_name_matches(self, query):
        normalized = core.normalize_name(query)
        return [item for item in self.list_items() if core.normalize_name(item.name) == normalized]

    def exact_upgrade_name_matches(self, query):
        normalized = core.normalize_name(query)
        return [
            item
            for item in self.list_items()
            if core.is_crysta_item_type(item.item_type)
            and core.normalize_name(item.name) == normalized
        ]

    def get_upgrade_successors(self, item_id):
        if item_id == self.don.id:
            return [self.upgrade_a, self.upgrade_b]
        return []

    def get_upgrade_component(self, item_id):
        return core.UpgradeGraph(
            nodes={
                self.don.id: self.don,
                self.upgrade_a.id: self.upgrade_a,
                self.upgrade_b.id: self.upgrade_b,
            },
            edges={self.don.id: (self.upgrade_a.id, self.upgrade_b.id)},
            missing_nodes={},
        )

    def search_by_expression(self, expression, item_types, *, primary_sort_ascending=False):
        return []

    def search_by_stat(self, stat_name, item_types):
        return []

    def count_items_total(self):
        return len(self.list_items())

    def count_items_by_types(self, item_types):
        return sum(item.item_type in item_types for item in self.list_items())

    def count_items_with_stat(self, stat_name):
        return 0


class MultipleGuildConfigTests(unittest.TestCase):
    def test_comma_separated_guild_allowlist(self):
        config = load_config(
            {
                "DISCORD_BOT_TOKEN": "token",
                "DISCORD_GUILD_IDS": "111, 222,333",
            }
        )
        self.assertEqual(config.guild_ids, frozenset({111, 222, 333}))

    def test_plural_guild_setting_wins_over_legacy_setting(self):
        config = load_config(
            {
                "DISCORD_BOT_TOKEN": "token",
                "DISCORD_GUILD_IDS": "111,222",
                "DISCORD_GUILD_ID": "999",
            }
        )
        self.assertEqual(config.guild_ids, frozenset({111, 222}))

    def test_legacy_single_guild_setting_still_works(self):
        config = load_config(
            {
                "DISCORD_BOT_TOKEN": "token",
                "DISCORD_GUILD_ID": "444",
            }
        )
        self.assertEqual(config.guild_ids, frozenset({444}))

    def test_invalid_guild_id_fails_startup(self):
        with self.assertRaisesRegex(RuntimeError, "DISCORD_GUILD_IDS"):
            load_config(
                {
                    "DISCORD_BOT_TOKEN": "token",
                    "DISCORD_GUILD_IDS": "111,abc",
                }
            )

    def test_gate_accepts_any_configured_guild(self):
        message = SimpleNamespace(
            guild=SimpleNamespace(id=222),
            author=SimpleNamespace(bot=False),
            webhook_id=None,
            mentions=[SimpleNamespace(id=99)],
        )
        self.assertTrue(
            is_allowed_message(
                message,
                bot_user_id=99,
                guild_ids=frozenset({111, 222}),
            )
        )


class DeterministicHelpTests(unittest.TestCase):
    def test_how_to_use_it_is_help_without_qwen(self):
        service = SearchService(FakeRepository(), llm_client=MustNotCallLLM())

        outcome = service.handle_query(
            "how to use it",
            FailedQueryContext(max_entries=3),
        )

        self.assertEqual(outcome.kind, "help")
        self.assertIn("Search syntax", outcome.text or "")


class UpgradeLookupTests(unittest.TestCase):
    def test_upgrade_exact_name_returns_direct_successors(self):
        repository = FakeRepository()
        service = SearchService(repository, llm_client=MustNotCallLLM())

        outcome = service.handle_query(
            "upgrade don",
            FailedQueryContext(max_entries=3),
        )

        self.assertEqual(outcome.kind, "search")
        self.assertIsInstance(outcome.payload, UpgradeResultsPayload)
        self.assertEqual(
            [result.item.name for result in outcome.payload.results],
            ["Don Upgrade A", "Don Upgrade B"],
        )

    def test_natural_upgrade_forms_are_deterministic(self):
        queries = (
            "upgrade Don",
            "upgrade for Don",
            "upgrades for Don",
            "show upgrades for Don",
            "find upgrades for Don",
            "what upgrades from Don",
            "what can upgrade Don",
            "what comes after Don",
            "next xtal after Don",
            "WHAT UPGRADES FROM DON?",
            "show upgrades for Don.",
        )

        for query in queries:
            with self.subTest(query=query):
                llm = RecordingLLM()
                service = SearchService(FakeRepository(), llm_client=llm)
                outcome = service.handle_query(
                    query,
                    FailedQueryContext(max_entries=3),
                )
                self.assertEqual(outcome.kind, "search")
                self.assertIsInstance(outcome.payload, UpgradeResultsPayload)
                self.assertEqual(
                    [result.item.name for result in outcome.payload.results],
                    ["Don Upgrade A", "Don Upgrade B"],
                )
                self.assertEqual(llm.calls, 0)

    def test_subjective_upgrade_wording_is_not_upgrade_intent(self):
        repository = FakeRepository()
        for query in (
            "best upgrade for Don",
            "strongest upgrade for Don",
            "better xtal after Don",
        ):
            with self.subTest(query=query):
                parsed = core.parse_search_query(query, repository)
                self.assertNotIn(parsed.intent, {"exact_upgrade", "upgrade_search"})


if __name__ == "__main__":
    unittest.main()
