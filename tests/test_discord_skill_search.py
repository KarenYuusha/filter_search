from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from toram_discord.app import process_tagged_query
from toram_discord.config import DiscordBotConfig
from toram_discord.sessions import DiscordSessionManager
from toram_discord.skill_detail_pages import build_skill_detail_pages
from toram_discord.skill_ui import (
    SkillDetailView,
    SkillResultsView,
    build_skill_detail_embed,
    build_skill_payload_message,
    build_skill_results_embed,
    render_skill_detail_page,
)
from toram_search.service import ServiceOutcome
from toram_skill_search.models import (
    SkillDetailPayload,
    SkillHelpPayload,
    SkillResultItem,
    SkillResultsPayload,
)
from toram_skills.models import SkillSection
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


class FakeInteractionResponse:
    def __init__(self) -> None:
        self.edits = []
        self.messages = []

    async def edit_message(self, **kwargs) -> None:
        self.edits.append(kwargs)

    async def send_message(self, *args, **kwargs) -> None:
        self.messages.append((args, kwargs))


class FakeInteraction:
    def __init__(self, user_id: int = 20) -> None:
        self.user = SimpleNamespace(id=user_id)
        self.response = FakeInteractionResponse()


def _button(view, label: str):
    return next(
        child
        for child in view.children
        if getattr(child, "label", None) == label
    )


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

    async def test_skill_query_preserves_existing_failed_item_context(self):
        sessions = DiscordSessionManager()
        key = (10, 30, 20)
        previous = sessions.start_query(key, "bad item query")
        previous.failed_context.record_failure("bad item query")
        message = FakeMessage("<@99> skill magic finale")

        with patch(
            "toram_discord.app.run_skill_query_sync",
            return_value=SkillHelpPayload("skill help"),
        ), patch(
            "toram_discord.app.run_query_sync",
            side_effect=AssertionError("item path must not run"),
        ):
            await process_tagged_query(
                message,
                bot_user=self.bot_user,
                config=self.config,
                sessions=sessions,
            )

        current = sessions.get(key)
        self.assertIsNotNone(current)
        attempts = current.failed_context.snapshot()
        self.assertEqual(
            [attempt.original_query for attempt in attempts],
            ["bad item query"],
        )

    async def test_item_query_executes_without_opening_skill_path(self):
        message = FakeMessage("<@99> cr bow")
        with patch(
            "toram_discord.app.run_skill_query_sync",
            side_effect=AssertionError("skill path must not run"),
        ), patch(
            "toram_discord.app.run_query_sync",
            return_value=ServiceOutcome("help", text="item path"),
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

    def test_compact_skill_results_show_metadata_and_short_preview(self):
        skill = replace(
            self.results_payload.results[0].skill,
            tier=4,
            mp_cost_text="1600MP",
            mp_cost_value=1600,
            damage_type="Magic",
        )
        payload = SkillResultsPayload(
            "finale",
            (
                SkillResultItem(
                    skill,
                    self.results_payload.results[0].tree,
                    "preview word " * 50,
                ),
            ),
        )
        embed = build_skill_results_embed(payload, page=0)
        lines = (embed.description or "").splitlines()
        self.assertIn(f"1. **{skill.name}**", lines)
        metadata = next(line.strip() for line in lines if " • " in line)
        self.assertEqual(
            metadata,
            f"{payload.results[0].tree.name} • Tier 4 • MP 1600 • Magic",
        )
        preview = next(
            line.strip()
            for line in lines
            if line.strip().startswith("preview word")
        )
        self.assertLessEqual(len(preview), 160)
        self.assertTrue(preview.endswith("…"))

    def test_compact_skill_results_fall_back_to_skill_type(self):
        base = self.results_payload.results[0]
        skill = replace(
            base.skill,
            tier=None,
            mp_cost_text=None,
            mp_cost_value=None,
            damage_type=None,
            skill_type="Support",
        )
        payload = SkillResultsPayload(
            "support",
            (SkillResultItem(skill, base.tree, "support preview"),),
        )
        visible = build_skill_results_embed(payload, page=0).description or ""
        self.assertIn(f"{base.tree.name} • Support", visible)
        self.assertNotIn("None", visible)
        self.assertNotIn("•  •", visible)

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


class DiscordSkillDetailInteractionTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        with SkillRepository(SKILL_DATABASE) as repo:
            skill = repo.resolve_skill_name("magic: finale")[0]
            tree = repo.get_tree(skill.tree_id)
        cls.base_payload = SkillDetailPayload(skill, tree)
        cls.long_payload = SkillDetailPayload(
            replace(
                skill,
                description=None,
                game_description=None,
                sections=(
                    SkillSection(
                        position=0,
                        label="Long Mechanics",
                        normalized_label="long mechanics",
                        body="\n".join(
                            f"line-{index}: " + "x" * 220
                            for index in range(80)
                        ),
                    ),
                ),
            ),
            tree,
        )
        cls.long_pages = build_skill_detail_pages(cls.long_payload)
        cls.short_payload = SkillDetailPayload(
            replace(
                skill,
                description="Short detail.",
                game_description=None,
                sections=(),
            ),
            tree,
        )
        results = []
        with SkillRepository(SKILL_DATABASE) as repo:
            rows = tuple(
                repo.connection.execute(
                    "SELECT id FROM skills ORDER BY tree_id, source_order, id LIMIT 6"
                )
            )
            for row in rows:
                result_skill = repo.get_skill(str(row["id"]))
                result_tree = repo.get_tree(result_skill.tree_id)
                results.append(
                    SkillResultItem(
                        result_skill,
                        result_tree,
                        result_skill.description
                        or result_skill.game_description
                        or result_skill.name,
                    )
                )
        cls.results_payload = SkillResultsPayload("skill query", tuple(results))

    def test_rendered_detail_page_has_footer(self):
        self.assertGreater(len(self.long_pages), 1)
        embed = render_skill_detail_page(
            self.long_pages[1],
            page_index=1,
            total_pages=len(self.long_pages),
        )
        self.assertEqual(embed.footer.text, f"Page 2 / {len(self.long_pages)}")

    def test_detail_view_button_boundaries_and_back(self):
        sessions = DiscordSessionManager()
        key = (10, 30, 20)
        session = sessions.start_query(key, "skill query")

        first = SkillDetailView(
            sessions=sessions,
            key=key,
            generation=session.generation,
            pages=self.long_pages,
            page_index=0,
            results_payload=self.results_payload,
        )
        self.assertTrue(_button(first, "Previous").disabled)
        self.assertFalse(_button(first, "Next").disabled)
        self.assertIsNotNone(_button(first, "Back to Results"))

        middle_index = min(1, len(self.long_pages) - 2)
        middle = SkillDetailView(
            sessions=sessions,
            key=key,
            generation=session.generation,
            pages=self.long_pages,
            page_index=middle_index,
            results_payload=self.results_payload,
        )
        if len(self.long_pages) > 2:
            self.assertFalse(_button(middle, "Previous").disabled)
            self.assertFalse(_button(middle, "Next").disabled)

        last = SkillDetailView(
            sessions=sessions,
            key=key,
            generation=session.generation,
            pages=self.long_pages,
            page_index=len(self.long_pages) - 1,
            results_payload=self.results_payload,
        )
        self.assertTrue(_button(last, "Next").disabled)

    def test_direct_detail_has_only_needed_navigation(self):
        sessions = DiscordSessionManager()
        key = (10, 30, 20)
        session = sessions.start_query(key, "skill query")
        _, long_view = build_skill_payload_message(
            self.long_payload,
            bot_example_prefix="@bot",
            sessions=sessions,
            key=key,
            generation=session.generation,
        )
        self.assertIsInstance(long_view, SkillDetailView)
        labels = [getattr(child, "label", None) for child in long_view.children]
        self.assertIn("Previous", labels)
        self.assertIn("Next", labels)
        self.assertNotIn("Back to Results", labels)

        _, short_view = build_skill_payload_message(
            self.short_payload,
            bot_example_prefix="@bot",
            sessions=sessions,
            key=key,
            generation=session.generation,
        )
        self.assertIsNone(short_view)

    async def test_next_previous_and_back_preserve_result_page(self):
        sessions = DiscordSessionManager()
        key = (10, 30, 20)
        session = sessions.start_query(key, "skill query")
        session.page = 1
        first = SkillDetailView(
            sessions=sessions,
            key=key,
            generation=session.generation,
            pages=self.long_pages,
            page_index=0,
            results_payload=self.results_payload,
        )

        next_interaction = FakeInteraction()
        await first._next(next_interaction)
        next_edit = next_interaction.response.edits[-1]
        self.assertEqual(next_edit["embed"].footer.text, f"Page 2 / {len(self.long_pages)}")
        self.assertEqual(session.page, 1)

        second_view = next_edit["view"]
        previous_interaction = FakeInteraction()
        await second_view._previous(previous_interaction)
        previous_edit = previous_interaction.response.edits[-1]
        self.assertEqual(previous_edit["embed"].footer.text, f"Page 1 / {len(self.long_pages)}")
        self.assertEqual(session.page, 1)

        back_interaction = FakeInteraction()
        await second_view._back(back_interaction)
        back_edit = back_interaction.response.edits[-1]
        self.assertIn("Showing 6–6 of 6", back_edit["embed"].description or "")
        self.assertEqual(session.page, 1)

    async def test_detail_view_preserves_owner_and_generation_protections(self):
        sessions = DiscordSessionManager()
        key = (10, 30, 20)
        session = sessions.start_query(key, "skill query")
        view = SkillDetailView(
            sessions=sessions,
            key=key,
            generation=session.generation,
            pages=self.long_pages,
            page_index=0,
            results_payload=self.results_payload,
        )

        wrong_user = FakeInteraction(user_id=999)
        self.assertFalse(await view.interaction_check(wrong_user))
        self.assertIn(
            "Only the person who started this search",
            wrong_user.response.messages[-1][0][0],
        )

        sessions.start_query(key, "newer query")
        stale = FakeInteraction(user_id=20)
        self.assertFalse(await view.interaction_check(stale))
        self.assertIn(
            "no longer active",
            stale.response.messages[-1][0][0],
        )


if __name__ == "__main__":
    unittest.main()
