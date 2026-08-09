from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from discord_bot import (
    DiscordSessionManager,
    build_intents,
    extract_mentioned_query,
    is_allowed_message,
    truncate_discord_text,
    valid_local_image_paths,
    visible_attachment_name,
)


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


if __name__ == "__main__":
    unittest.main()
