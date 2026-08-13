from __future__ import annotations

from pathlib import Path
import unittest

from toram_discord.skill_ui import build_skill_detail_embed, build_skill_results_embed
from toram_skill_search.models import SkillDetailPayload, SkillResultItem, SkillResultsPayload
from toram_skills.repository import SkillRepository

ROOT = Path(__file__).resolve().parents[1]
SKILL_DATABASE = ROOT / "coryn_data" / "database" / "skills.sqlite"


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


if __name__ == "__main__":
    unittest.main()
