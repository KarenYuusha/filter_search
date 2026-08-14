from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import discord

from toram_discord.app import process_tagged_query
from toram_discord.config import DiscordBotConfig
from toram_discord.database_chat import DatabaseChatOutcome
from toram_discord.sessions import DiscordSessionManager
from toram_discord.skill_chat_ui import (
    build_skill_chat_detail_message,
    run_skill_detail_by_id_sync,
)
from toram_discord.skill_ui import SkillRenderedMessage
from toram_skill_chat.models import SkillChatResult
from toram_skill_search.models import SkillDetailPayload
from toram_skills.repository import SkillRepository


ROOT = Path(__file__).resolve().parents[1]
SKILL_DATABASE = ROOT / "coryn_data" / "database" / "skills.sqlite"


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


class RichSkillChatPresentationTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        with SkillRepository(SKILL_DATABASE) as repo:
            skill = repo.resolve_skill_name("hard hit")[0]
            tree = repo.get_tree(skill.tree_id)
        cls.detail_payload = SkillDetailPayload(skill, tree)
        cls.skill_id = skill.id

    def setUp(self) -> None:
        self.bot_user = SimpleNamespace(
            id=99,
            display_name="Toram Search",
            name="Toram Search",
        )
        self.config = DiscordBotConfig(
            token="x",
            guild_ids=frozenset({10}),
            database_path=Path("items.sqlite"),
            skill_database_path=SKILL_DATABASE,
        )

    def test_detail_loader_resolves_exact_skill_id(self):
        payload = run_skill_detail_by_id_sync(SKILL_DATABASE, self.skill_id)
        self.assertEqual(payload.skill.id, self.skill_id)
        self.assertEqual(payload.skill.name.casefold(), "hard hit")
        self.assertEqual(payload.tree.id, payload.skill.tree_id)

    def test_rich_chat_message_reuses_detail_and_adds_explanation(self):
        sessions = DiscordSessionManager()
        key = (10, 30, 20)
        session = sessions.start_query(key, "how does Hard Hit work")

        rendered = build_skill_chat_detail_message(
            self.detail_payload,
            "Grounded Hard Hit explanation.",
            sessions=sessions,
            key=key,
            generation=session.generation,
        )

        self.assertIsInstance(rendered, SkillRenderedMessage)
        self.assertGreaterEqual(len(rendered.embeds), 2)
        self.assertIn("hard hit", (rendered.embeds[0].title or "").casefold())
        self.assertEqual(rendered.embeds[-1].title, "Explanation")
        self.assertEqual(
            rendered.embeds[-1].description,
            "Grounded Hard Hit explanation.",
        )

    async def test_exact_natural_explanation_uses_rich_detail_reply(self):
        message = _Message("<@99> how does Hard Hit work")
        outcome = DatabaseChatOutcome(
            kind="skill",
            skill_result=SkillChatResult(
                kind="answer",
                text="Grounded Hard Hit explanation.",
                skill_ids=(self.skill_id,),
            ),
            skill_ids=(self.skill_id,),
        )

        with patch(
            "toram_discord.app.run_database_chat_sync",
            return_value=outcome,
        ), patch(
            "toram_discord.app.run_skill_detail_by_id_sync",
            return_value=self.detail_payload,
        ) as detail_loader, patch(
            "toram_discord.app.run_query_sync",
            side_effect=AssertionError("item fallback must not run"),
        ):
            await process_tagged_query(
                message,
                bot_user=self.bot_user,
                config=self.config,
                sessions=DiscordSessionManager(),
            )

        detail_loader.assert_called_once_with(SKILL_DATABASE, self.skill_id)
        self.assertEqual(len(message.replies), 1)
        reply = message.replies[0]
        self.assertNotIn("embed", reply)
        self.assertIn("embeds", reply)
        embeds = reply["embeds"]
        self.assertGreaterEqual(len(embeds), 2)
        self.assertIn("hard hit", (embeds[0].title or "").casefold())
        self.assertEqual(embeds[-1].title, "Explanation")

    async def test_structured_single_skill_answer_keeps_compact_chat_embed(self):
        message = _Message("<@99> guardian mp cost")
        outcome = DatabaseChatOutcome(
            kind="skill",
            skill_result=SkillChatResult(
                kind="structured",
                text="Guardian: MP 600",
                skill_ids=("guardian-id",),
            ),
            skill_ids=("guardian-id",),
        )

        with patch(
            "toram_discord.app.run_database_chat_sync",
            return_value=outcome,
        ), patch(
            "toram_discord.app.run_skill_detail_by_id_sync",
            side_effect=AssertionError("structured lookup must stay compact"),
        ), patch(
            "toram_discord.app.run_query_sync",
            side_effect=AssertionError("item fallback must not run"),
        ):
            await process_tagged_query(
                message,
                bot_user=self.bot_user,
                config=self.config,
                sessions=DiscordSessionManager(),
            )

        self.assertEqual(len(message.replies), 1)
        reply = message.replies[0]
        self.assertIn("embed", reply)
        self.assertNotIn("embeds", reply)
        self.assertIsInstance(reply["embed"], discord.Embed)


if __name__ == "__main__":
    unittest.main()
