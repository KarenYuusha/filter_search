from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from toram_discord.app import process_tagged_query
from toram_discord.config import DiscordBotConfig
from toram_discord.sessions import DatabaseChatContext, DiscordSessionManager


class FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content
        self.guild = SimpleNamespace(id=10, get_member=lambda user_id: None)
        self.channel = SimpleNamespace(id=30)
        self.author = SimpleNamespace(id=20, bot=False)
        self.replies: list[dict[str, object]] = []

    async def reply(self, **kwargs) -> None:
        self.replies.append(kwargs)


class DatabaseChatContextTests(unittest.TestCase):
    def test_clear_resets_every_context_field(self):
        context = DatabaseChatContext(
            active_domain="mixed",
            active_skill_ids=("skill-a", "skill-b"),
            active_item_ids=(1, 2),
            selected_skill_id="skill-a",
            selected_item_id=1,
            active_tree_id="tree-a",
            active_skill_filters={"ailments": ("Stun",)},
            last_operation="rank",
            last_metric="mp_cost_value",
            last_user_query="which one costs less mp",
        )

        context.clear()

        self.assertIsNone(context.active_domain)
        self.assertEqual(context.active_skill_ids, ())
        self.assertEqual(context.active_item_ids, ())
        self.assertIsNone(context.selected_skill_id)
        self.assertIsNone(context.selected_item_id)
        self.assertIsNone(context.active_tree_id)
        self.assertEqual(context.active_skill_filters, {})
        self.assertIsNone(context.last_operation)
        self.assertIsNone(context.last_metric)
        self.assertIsNone(context.last_user_query)

    def test_chat_context_survives_start_query_as_independent_copy(self):
        sessions = DiscordSessionManager()
        key = (10, 30, 20)
        first = sessions.start_query(key, "which skills inflict stun")
        first.chat_context.active_domain = "skill"
        first.chat_context.active_skill_ids = ("a", "b")
        first.chat_context.active_skill_filters["ailments"] = ("Stun",)
        first.chat_context.last_operation = "filter"

        second = sessions.start_query(key, "only shield skills")

        self.assertEqual(second.chat_context.active_domain, "skill")
        self.assertEqual(second.chat_context.active_skill_ids, ("a", "b"))
        self.assertEqual(second.chat_context.active_skill_filters, {"ailments": ("Stun",)})
        self.assertEqual(second.chat_context.last_operation, "filter")
        self.assertIsNot(first.chat_context, second.chat_context)
        self.assertIsNot(
            first.chat_context.active_skill_filters,
            second.chat_context.active_skill_filters,
        )

    def test_failed_item_context_still_clones_independently(self):
        sessions = DiscordSessionManager()
        key = (10, 30, 20)
        first = sessions.start_query(key, "bad query")
        first.failed_context.record_failure("bad query")
        first.failed_context.set_latest_suggestion("hp armor")
        first.chat_context.active_domain = "skill"

        second = sessions.start_query(key, "another query")
        attempts = second.failed_context.snapshot()

        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0].original_query, "bad query")
        self.assertEqual(attempts[0].suggested_query, "hp armor")
        self.assertIsNot(first.failed_context, second.failed_context)
        self.assertEqual(second.chat_context.active_domain, "skill")

    def test_clear_chat_context_reports_missing_or_existing_session(self):
        sessions = DiscordSessionManager()
        key = (10, 30, 20)
        self.assertFalse(sessions.clear_chat_context(key))

        session = sessions.start_query(key, "query")
        session.chat_context.active_domain = "skill"

        self.assertTrue(sessions.clear_chat_context(key))
        self.assertIsNone(session.chat_context.active_domain)


class DatabaseChatResetTests(unittest.IsolatedAsyncioTestCase):
    async def test_reset_commands_clear_chat_and_failed_context_without_search(self):
        config = DiscordBotConfig(
            token="x",
            guild_ids=frozenset({10}),
            database_path=Path("items.sqlite"),
            skill_database_path=Path("skills.sqlite"),
        )
        bot_user = SimpleNamespace(id=99, display_name="Toram Search", name="Toram Search")

        for command in ("reset", "clear", "new search"):
            with self.subTest(command=command):
                sessions = DiscordSessionManager()
                key = (10, 30, 20)
                previous = sessions.start_query(key, "bad query")
                previous.failed_context.record_failure("bad query")
                previous.failed_context.set_latest_suggestion("hp armor")
                previous.chat_context.active_domain = "mixed"
                previous.chat_context.active_skill_ids = ("skill-a",)
                previous.chat_context.active_item_ids = (123,)
                previous.chat_context.selected_skill_id = "skill-a"
                previous.chat_context.selected_item_id = 123
                previous.chat_context.active_tree_id = "tree-a"
                previous.chat_context.active_skill_filters["ailments"] = ("Stun",)
                previous.chat_context.last_operation = "filter"
                previous.chat_context.last_metric = "mp_cost_value"
                previous.chat_context.last_user_query = "bad query"
                message = FakeMessage(f"<@99> {command}")

                with patch(
                    "toram_discord.app.run_skill_query_sync",
                    side_effect=AssertionError("skill search must not run"),
                ), patch(
                    "toram_discord.app.run_query_sync",
                    side_effect=AssertionError("item search must not run"),
                ):
                    await process_tagged_query(
                        message,
                        bot_user=bot_user,
                        config=config,
                        sessions=sessions,
                    )

                current = sessions.get(key)
                self.assertIsNotNone(current)
                assert current is not None
                self.assertEqual(current.failed_context.snapshot(), ())
                self.assertEqual(current.chat_context, DatabaseChatContext())
                self.assertEqual(len(message.replies), 1)
                self.assertIn("Search context cleared", str(message.replies[0]["content"]))


if __name__ == "__main__":
    unittest.main()
