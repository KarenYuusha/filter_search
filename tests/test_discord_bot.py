from __future__ import annotations

import inspect
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import discord_bot
import search_items as core
from discord_bot import (
    DiscordSessionManager,
    SearchResultsView,
    build_intents,
    build_item_detail_embed,
    extract_mentioned_query,
    is_allowed_message,
    load_config,
    load_project_environment,
    truncate_discord_text,
    valid_local_image_paths,
    visible_attachment_name,
)
from toram_search.service import ItemResultsPayload, UpgradeResultsPayload


class DiscordConfigTests(unittest.TestCase):
    def test_dotenv_supplies_token_and_guild_id(self):
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.write_text(
                "DISCORD_BOT_TOKEN=from-file\n"
                "DISCORD_GUILD_ID=123456789\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {}, clear=True):
                used_path = load_project_environment(env_path)
                config = load_config()

            self.assertEqual(used_path, env_path)
            self.assertEqual(config.token, "from-file")
            self.assertEqual(config.guild_id, 123456789)

    def test_existing_environment_overrides_dotenv(self):
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.write_text(
                "DISCORD_BOT_TOKEN=from-file\n"
                "DISCORD_GUILD_ID=111\n",
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {
                    "DISCORD_BOT_TOKEN": "from-shell",
                    "DISCORD_GUILD_ID": "222",
                },
                clear=True,
            ):
                load_project_environment(env_path)
                config = load_config()

            self.assertEqual(config.token, "from-shell")
            self.assertEqual(config.guild_id, 222)

    def test_load_config_keeps_explicit_mapping_validation(self):
        config = load_config(
            {
                "DISCORD_BOT_TOKEN": "explicit-token",
                "DISCORD_GUILD_ID": "333",
            }
        )
        self.assertEqual(config.token, "explicit-token")
        self.assertEqual(config.guild_id, 333)

    def test_env_example_has_only_safe_template_values(self):
        text = (Path(__file__).resolve().parents[1] / ".env.example").read_text(
            encoding="utf-8"
        )
        self.assertIn("DISCORD_BOT_TOKEN=", text)
        self.assertIn("DISCORD_GUILD_ID=", text)
        self.assertIn("OLLAMA_MODEL=qwen3.5:2b", text)
        self.assertNotIn("from-file", text)
        self.assertNotIn("from-shell", text)


class DiscordBotGateTests(unittest.TestCase):
    def make_message(
        self,
        *,
        guild_id=10,
        author_id=20,
        author_bot=False,
        webhook_id=None,
        mentions=(99,),
    ):
        guild = None if guild_id is None else SimpleNamespace(id=guild_id)
        author = SimpleNamespace(id=author_id, bot=author_bot)
        mentioned_users = [SimpleNamespace(id=value) for value in mentions]
        return SimpleNamespace(
            guild=guild,
            author=author,
            webhook_id=webhook_id,
            mentions=mentioned_users,
        )

    def test_wrong_guild_is_ignored(self):
        message = self.make_message(guild_id=11)
        self.assertFalse(is_allowed_message(message, bot_user_id=99, guild_id=10))

    def test_dm_is_ignored(self):
        message = self.make_message(guild_id=None)
        self.assertFalse(is_allowed_message(message, bot_user_id=99, guild_id=10))

    def test_unmentioned_message_is_ignored(self):
        message = self.make_message(mentions=())
        self.assertFalse(is_allowed_message(message, bot_user_id=99, guild_id=10))

    def test_bot_and_webhook_messages_are_ignored(self):
        self.assertFalse(
            is_allowed_message(
                self.make_message(author_bot=True),
                bot_user_id=99,
                guild_id=10,
            )
        )
        self.assertFalse(
            is_allowed_message(
                self.make_message(webhook_id=123),
                bot_user_id=99,
                guild_id=10,
            )
        )

    def test_allowed_mention_is_processed(self):
        self.assertTrue(
            is_allowed_message(self.make_message(), bot_user_id=99, guild_id=10)
        )

    def test_both_mention_forms_are_removed(self):
        self.assertEqual(
            extract_mentioned_query("<@99> can you find armor with hp", 99),
            "can you find armor with hp",
        )
        self.assertEqual(
            extract_mentioned_query("<@!99> hp armor", 99),
            "hp armor",
        )

    def test_minimum_intents_do_not_enable_message_content(self):
        intents = build_intents()
        self.assertTrue(intents.guilds)
        self.assertTrue(intents.guild_messages)
        self.assertFalse(intents.message_content)


class DiscordSessionTests(unittest.TestCase):
    def test_new_query_invalidates_old_generation(self):
        sessions = DiscordSessionManager()
        key = (10, 30, 20)
        first = sessions.start_query(key, "hp armor")
        second = sessions.start_query(key, "cr bow")

        self.assertFalse(sessions.is_current(key, first.generation))
        self.assertTrue(sessions.is_current(key, second.generation))
        self.assertEqual(second.generation, first.generation + 1)

    def test_other_user_has_independent_session(self):
        sessions = DiscordSessionManager()
        alice = sessions.start_query((10, 30, 20), "hp armor")
        bob = sessions.start_query((10, 30, 21), "cr bow")

        self.assertTrue(sessions.is_current((10, 30, 20), alice.generation))
        self.assertTrue(sessions.is_current((10, 30, 21), bob.generation))

    def test_failed_query_context_is_cloned_with_suggestion(self):
        sessions = DiscordSessionManager()
        key = (10, 30, 20)
        first = sessions.start_query(key, "bad query")
        first.failed_context.record_failure("bad query")
        first.failed_context.set_latest_suggestion("hp armor")

        second = sessions.start_query(key, "another query")
        attempts = second.failed_context.snapshot()

        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0].original_query, "bad query")
        self.assertEqual(attempts[0].suggested_query, "hp armor")
        self.assertIsNot(first.failed_context, second.failed_context)


class DiscordFormattingTests(unittest.TestCase):
    def test_truncate_discord_text(self):
        self.assertEqual(truncate_discord_text("abc", 4), "abc")
        self.assertEqual(truncate_discord_text("abcdef", 4), "abc…")

    def test_visible_attachment_filename_has_no_database_id(self):
        name = visible_attachment_name("A Very Cool Armor", 1, ".jpg")
        self.assertEqual(name, "a-very-cool-armor-2.jpg")

    def test_local_image_paths_skip_missing_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database_path = root / "database" / "items.sqlite"
            database_path.parent.mkdir()
            database_path.touch()
            image = root / "appearance" / "armor" / "sample" / "item_01.jpg"
            image.parent.mkdir(parents=True)
            image.write_bytes(b"image")

            paths = valid_local_image_paths(
                database_path,
                [
                    {"local_path": "../appearance/armor/sample/item_01.jpg"},
                    {"local_path": "../appearance/armor/sample/missing.jpg"},
                ],
            )

            self.assertEqual(paths, (image.resolve(),))

    def test_item_detail_hides_database_id_and_coryn_url(self):
        detail = core.ItemDetail(
            summary=core.ItemSummary(987654321, "Test Armor", "Armor"),
            sell_price=None,
            process_material=None,
            process_amount=None,
            badge=None,
            note="Safe note",
            page_url="https://example.invalid/coryn-item",
            stats=[
                {
                    "stat_name": "MaxHP",
                    "amount": 5000,
                    "conditions_json": "[]",
                    "condition_text": None,
                    "needs_condition_review": False,
                }
            ],
            sources=[],
            images=[],
            upgrade_predecessors=[],
            upgrade_successors=[],
        )

        embed = build_item_detail_embed(detail)
        visible = "\n".join(
            [
                embed.title or "",
                embed.description or "",
                *(field.name + "\n" + field.value for field in embed.fields),
            ]
        )

        self.assertNotIn("987654321", visible)
        self.assertNotIn(detail.page_url, visible)
        self.assertIn("Test Armor", visible)
        self.assertIn("MaxHP", visible)

    def test_result_dropdown_values_are_indexes_not_item_ids(self):
        payload = ItemResultsPayload(
            "armor",
            tuple(
                core.RankedItem(
                    core.ItemSummary(9000 + index, f"Armor {index}", "Armor"),
                    90.0,
                    "fuzzy",
                )
                for index in range(6)
            ),
        )
        sessions = DiscordSessionManager()
        key = (10, 30, 20)
        session = sessions.start_query(key, "armor")
        view = SearchResultsView(
            sessions=sessions,
            key=key,
            generation=session.generation,
            database_path=Path("coryn_data/database/items.sqlite"),
            payload=payload,
        )

        self.assertIsNotNone(view.item_select)
        self.assertEqual([option.value for option in view.item_select.options], ["0", "1", "2", "3", "4"])
        self.assertNotIn("9000", [option.value for option in view.item_select.options])

    def test_upgrade_suggestion_payload_is_distinguished_from_direct_results(self):
        self.assertTrue(
            hasattr(discord_bot, "is_upgrade_suggestion_payload"),
            "Discord UI needs an upgrade-suggestion classifier",
        )
        fuzzy = UpgradeResultsPayload(
            "Dno",
            (core.RankedItem(core.ItemSummary(1, "Don", "Normal Crysta"), 90.0, "fuzzy"),),
        )
        direct = UpgradeResultsPayload(
            "Don",
            (
                core.RankedItem(
                    core.ItemSummary(2, "Don Upgrade A", "Enhancer Crysta (Blue)"),
                    100.0,
                    "upgrade",
                ),
            ),
        )
        self.assertTrue(discord_bot.is_upgrade_suggestion_payload(fuzzy))
        self.assertFalse(discord_bot.is_upgrade_suggestion_payload(direct))

    def test_bot_example_prefix_prefers_guild_display_name(self):
        self.assertTrue(
            hasattr(discord_bot, "bot_example_prefix"),
            "Discord output needs a guild display-name example helper",
        )
        member = SimpleNamespace(display_name="Toram Search")
        guild = SimpleNamespace(get_member=lambda user_id: member if user_id == 99 else None)
        bot_user = SimpleNamespace(id=99, display_name="GlobalBot", name="GlobalBot")
        self.assertEqual(discord_bot.bot_example_prefix(guild, bot_user), "@Toram Search")

    def test_bot_example_prefix_falls_back_to_account_name(self):
        self.assertTrue(hasattr(discord_bot, "bot_example_prefix"))
        guild = SimpleNamespace(get_member=lambda _user_id: None)
        bot_user = SimpleNamespace(id=99, display_name="GlobalBot", name="GlobalBot")
        self.assertEqual(discord_bot.bot_example_prefix(guild, bot_user), "@GlobalBot")

    def test_help_examples_use_plain_display_name_not_raw_mention(self):
        embed = discord_bot.build_help_embed("@Toram Search")
        visible = "\n".join(field.value for field in embed.fields)
        self.assertIn("@Toram Search hp armor", visible)
        self.assertIn("@Toram Search upgrade <crysta name>", visible)
        self.assertNotIn("<@", visible)

    def test_service_message_builder_uses_display_name_parameter(self):
        parameters = inspect.signature(discord_bot.build_service_outcome_message).parameters
        self.assertIn("bot_example_prefix", parameters)
        self.assertNotIn("bot_mention", parameters)


if __name__ == "__main__":
    unittest.main()
