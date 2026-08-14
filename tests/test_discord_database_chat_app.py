from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from toram_discord.app import process_tagged_query
from toram_discord.config import DiscordBotConfig
from toram_discord.database_chat import DatabaseChatOutcome
from toram_discord.sessions import DiscordSessionManager
from toram_skill_chat.models import SkillChatResult


class _Message:
    def __init__(self, content: str) -> None:
        self.content = content
        self.guild = SimpleNamespace(id=10, get_member=lambda user_id: None)
        self.channel = SimpleNamespace(id=30)
        self.author = SimpleNamespace(id=20, bot=False)
        self.mentions = [SimpleNamespace(id=99)]
        self.webhook_id = None
        self.replies: list[dict[str, object]] = []

    async def reply(self, **kwargs) -> None:
        self.replies.append(kwargs)


class DiscordDatabaseChatAppTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.bot_user = SimpleNamespace(id=99, display_name="Toram Search", name="Toram Search")
        self.config = DiscordBotConfig(
            token="x",
            guild_ids=frozenset({10}),
            database_path=Path("items.sqlite"),
            skill_database_path=Path("skills.sqlite"),
        )

    async def test_help_uses_database_chat_help_without_search(self):
        message = _Message("<@99>")
        with patch(
            "toram_discord.app.run_database_chat_sync",
            side_effect=AssertionError("database chat search must not run for help"),
        ), patch(
            "toram_discord.app.run_query_sync",
            side_effect=AssertionError("item search must not run for help"),
        ):
            await process_tagged_query(
                message,
                bot_user=self.bot_user,
                config=self.config,
                sessions=DiscordSessionManager(),
            )

        self.assertEqual(len(message.replies), 1)
        embed = message.replies[0]["embed"]
        visible = "\n".join(field.value for field in embed.fields)
        self.assertIn("skill hard hit", visible)
        self.assertIn("how does Hard Hit work?", visible)

    async def test_natural_skill_question_uses_database_chat_not_item_fallback(self):
        message = _Message("<@99> which skill has the highest MP cost?")
        outcome = DatabaseChatOutcome(
            kind="skill",
            skill_result=SkillChatResult(kind="results", text="1. TEST SKILL", skill_ids=("test",)),
            skill_ids=("test",),
        )
        with patch("toram_discord.app.run_database_chat_sync", return_value=outcome) as chat_runner, patch(
            "toram_discord.app.run_query_sync",
            side_effect=AssertionError("item Qwen fallback must not run"),
        ):
            await process_tagged_query(
                message,
                bot_user=self.bot_user,
                config=self.config,
                sessions=DiscordSessionManager(),
            )

        chat_runner.assert_called_once()
        self.assertEqual(message.replies[0]["embed"].title, "Skill results")

    async def test_database_chat_fallback_defers_to_existing_item_path(self):
        message = _Message("<@99> what should I search for")
        with patch(
            "toram_discord.app.run_database_chat_sync",
            return_value=DatabaseChatOutcome(kind="fallback"),
        ), patch(
            "toram_discord.app.run_query_sync",
            return_value=SimpleNamespace(kind="help", text="item help", payload=None),
        ) as item_runner:
            await process_tagged_query(
                message,
                bot_user=self.bot_user,
                config=self.config,
                sessions=DiscordSessionManager(),
            )

        item_runner.assert_called_once()
        self.assertEqual(len(message.replies), 1)


if __name__ == "__main__":
    unittest.main()
