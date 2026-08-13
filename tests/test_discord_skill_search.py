from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from toram_discord.app import process_tagged_query
from toram_discord.config import DiscordBotConfig
from toram_discord.sessions import DiscordSessionManager
from toram_discord.skill_ui import (
    SkillDetailView,
    SkillResultsView,
    build_skill_detail_embed,
    build_skill_results_embed,
)
from toram_search.service import ServiceOutcome
from toram_skill_search.models import (
    SkillDetailPayload,
    SkillHelpPayload,
    SkillResultItem,
    SkillResultsPayload,
)
from toram_skills.repository import SkillRepository

ROOT = Path(__file__).resolve().parents[1]
SKILL_DATABASE = ROOT / "coryn_data" / "database" / "skills.sqlite"


class FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content
        self.guild = SimpleNamespace(id=10, get_member=lambda user_id: None)
        self.channel = SimpleNamespace(id=30)
        self.author = SimpleNamespace(id=20, bot=False)
        self.mentions = [SimpleNamespace(id=99)]
        self.webhook_id = None
        self.replies = []

    async def reply(self, **kwargs) -> None:
        self.replies.append(kwargs)


class DiscordSkillRoutingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.bot_user = SimpleNamespace(
            id=99,
            display_name="Toram Search",
            name="Toram Search",
        )
        self.config = DiscordBotConfig(
            token="x",
            guild_ids=frozenset({10}),
            database_path=Path("items.sqlite"),
            skill_database_path=Path("skills.sqlite"),
        )
        self.sessions = DiscordSessionManager()

    async def test_explicit_skill_prefix_uses_skill_path_not_item_path(self):
        message = FakeMessage("<@99> skill magic finale")
        calls = []

        def fake_skill_runner(database_path, query):
            calls.append((database_path, query))
            return SkillHelpPayload("skill help")

        with patch(
            "toram_discord.app.run_skill_query_sync",
            side_effect=fake_skill_runner,
        ), patch(
            "toram_discord.app.run_query_sync",
            side_effect=AssertionError("item path must not run"),
        ):
            await process_tagged_query(
                message,
                bot_user=self.bot_user,
                config=self.config,
                sessions=self.sessions,
            )

        self.assertEqual(calls, [(Path("skills.sqlite"), "magic finale")])
        self.assertEqual(len(message.replies), 1)

    async def test_non_skill_query_never_calls_skill_path(self):
        message = FakeMessage("<@99> hp armor")
        with patch(
            "toram_discord.app.run_skill_query_sync",
            side_effect=AssertionError("skill path must not run"),
        ), patch(
            "toram_discord.app.run_query_sync",
            return_value=ServiceOutcome("help", text="item help"),
        ) as item_runner:
            await process_tagged_query(
                message,
                bot_user=self.bot_user,
                config=self.config,
                sessions=self.sessions,
            )

        item_runner.assert_called_once()
        self.assertEqual(len(message.replies), 1)

    async def test_non_token_prefixes_remain_item_queries(self):
        for query in ("skills magic finale", "skillful magic finale"):
            with self.subTest(query=query):
                message = FakeMessage(f"<@99> {query}")
                with patch(
                    "toram_discord.app.run_skill_query_sync",
                    side_effect=AssertionError("skill path must not run"),
                ), patch(
                    "toram_discord.app.run_query_sync",
                    return_value=ServiceOutcome("help", text="item help"),
                ) as item_runner:
                    await process_tagged_query(
                        message,
                        bot_user=self.bot_user,
                        config=self.config,
                        sessions=DiscordSessionManager(),
                    )
                item_runner.assert_called_once()


class DiscordSkillRenderingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with SkillRepository(SKILL_DATABASE) as repo:
            skill = repo.resolve_skill_name("magic: finale")[0]
            tree = repo.get_tree(skill.tree_id)
        cls.detail_payload = SkillDetailPayload(skill, tree)
        cls.results_payload = SkillResultsPayload(
            "magic finale",
            (SkillResultItem(skill, tree, skill.game_description or skill.name),),
        )

    def test_skill_detail_omits_internal_ids_and_none_text(self):
        embed = build_skill_detail_embed(self.detail_payload)
        visible = "\n".join([
            embed.title or "",
            embed.description or "",
            *(field.name + "\n" + field.value for field in embed.fields),
        ])
        self.assertIn(self.detail_payload.skill.name, visible)
        self.assertIn(self.detail_payload.tree.name, visible)
        self.assertNotIn(self.detail_payload.skill.id, visible)
        self.assertNotIn("None", visible)

    def test_skill_results_hide_retrieval_diagnostics(self):
        embed = build_skill_results_embed(self.results_payload, page=0)
        visible = embed.description or ""
        self.assertIn(self.results_payload.results[0].skill.name, visible)
        self.assertNotIn("semantic", visible.casefold())
        self.assertNotIn("rrf", visible.casefold())

    def test_empty_skill_results_are_deterministic(self):
        embed = build_skill_results_embed(SkillResultsPayload("nope", ()), page=0)
        visible = (embed.description or "").casefold()
        self.assertEqual(embed.title, "No skill results")
        self.assertIn("skill magic finale", visible)
        self.assertNotIn("qwen", visible)


class DiscordSkillInteractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        results = []
        with SkillRepository(SKILL_DATABASE) as repo:
            rows = tuple(
                repo.connection.execute(
                    "SELECT id FROM skills ORDER BY tree_id, source_order, id LIMIT 6"
                )
            )
            for row in rows:
                skill = repo.get_skill(str(row["id"]))
                tree = repo.get_tree(skill.tree_id)
                results.append(
                    SkillResultItem(
                        skill,
                        tree,
                        skill.description or skill.game_description or skill.name,
                    )
                )
        cls.six_result_payload = SkillResultsPayload("skill query", tuple(results))

    def test_skill_result_dropdown_uses_indexes_not_skill_ids(self):
        sessions = DiscordSessionManager()
        key = (10, 30, 20)
        session = sessions.start_query(key, "skill query")
        view = SkillResultsView(
            sessions=sessions,
            key=key,
            generation=session.generation,
            payload=self.six_result_payload,
        )
        self.assertIsNotNone(view.skill_select)
        self.assertEqual(
            [option.value for option in view.skill_select.options],
            ["0", "1", "2", "3", "4"],
        )
        skill_ids = {item.skill.id for item in self.six_result_payload.results}
        self.assertTrue(
            skill_ids.isdisjoint(
                {option.value for option in view.skill_select.options}
            )
        )

    def test_six_skill_results_have_pagination_controls(self):
        sessions = DiscordSessionManager()
        key = (10, 30, 20)
        session = sessions.start_query(key, "skill query")
        view = SkillResultsView(
            sessions=sessions,
            key=key,
            generation=session.generation,
            payload=self.six_result_payload,
        )
        labels = [child.label for child in view.children if hasattr(child, "label")]
        self.assertIn("Previous", labels)
        self.assertIn("Next", labels)

    def test_skill_detail_view_has_back_to_results(self):
        sessions = DiscordSessionManager()
        key = (10, 30, 20)
        session = sessions.start_query(key, "skill query")
        view = SkillDetailView(
            sessions=sessions,
            key=key,
            generation=session.generation,
            results_payload=self.six_result_payload,
        )
        labels = [child.label for child in view.children if hasattr(child, "label")]
        self.assertIn("Back to Results", labels)


if __name__ == "__main__":
    unittest.main()
