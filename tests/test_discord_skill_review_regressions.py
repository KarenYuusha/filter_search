from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from toram_discord.app import process_tagged_query
from toram_discord.config import DiscordBotConfig
from toram_discord.sessions import DiscordSessionManager
from toram_discord.skill_detail_pages import (
    build_skill_detail_pages,
    detail_page_char_count,
)
from toram_discord.skill_ui import render_skill_detail_page
from toram_skill_search.models import SkillDetailPayload
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
        self.replies = []

    async def reply(self, **kwargs) -> None:
        self.replies.append(kwargs)


class SkillReviewRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_skill_exception_gets_skill_specific_error(self):
        message = FakeMessage("<@99> skill magic finale")
        config = DiscordBotConfig(
            token="x",
            guild_ids=frozenset({10}),
            database_path=Path("items.sqlite"),
            skill_database_path=Path("skills.sqlite"),
        )
        bot_user = SimpleNamespace(id=99, display_name="Toram Search", name="Toram Search")

        with patch(
            "toram_discord.app.run_skill_query_sync",
            side_effect=RuntimeError("boom"),
        ), patch(
            "toram_discord.app.run_query_sync",
            side_effect=AssertionError("item path must not run"),
        ):
            await process_tagged_query(
                message,
                bot_user=bot_user,
                config=config,
                sessions=DiscordSessionManager(),
            )

        self.assertEqual(len(message.replies), 1)
        self.assertEqual(message.replies[0]["embed"].title, "Skill search unavailable")


class SkillEmbedBudgetTests(unittest.TestCase):
    def test_skill_detail_pagination_preserves_late_text_and_embed_limits(self):
        with SkillRepository(SKILL_DATABASE) as repo:
            skill = repo.resolve_skill_name("magic: finale")[0]
            tree = repo.get_tree(skill.tree_id)

        long_sections = tuple(
            SkillSection(
                position=index,
                label=f"Section {index}",
                normalized_label=f"section {index}",
                body=(
                    ("z" * 1900) + " FINAL-SECTION-END"
                    if index == 19
                    else "z" * 2000
                ),
            )
            for index in range(20)
        )
        payload = SkillDetailPayload(
            replace(
                skill,
                description=("x" * 1900) + " DESCRIPTION-END",
                game_description=("y" * 1900) + " GAME-DESCRIPTION-END",
                sections=long_sections,
            ),
            tree,
        )

        pages = build_skill_detail_pages(payload)
        self.assertGreater(len(pages), 1)
        combined = "\n".join(
            field.value
            for page in pages
            for field in page.fields
        )
        self.assertIn("DESCRIPTION-END", combined)
        self.assertIn("GAME-DESCRIPTION-END", combined)
        self.assertIn("FINAL-SECTION-END", combined)

        for index, page in enumerate(pages):
            footer = f"Page {index + 1} / {len(pages)}"
            self.assertLessEqual(
                detail_page_char_count(page, footer_text=footer),
                6000,
            )
            embed = render_skill_detail_page(
                page,
                page_index=index,
                total_pages=len(pages),
            )
            self.assertLessEqual(len(embed.fields), 25)


if __name__ == "__main__":
    unittest.main()
