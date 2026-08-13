from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import unittest

from toram_discord.skill_detail_pages import (
    build_skill_detail_pages,
    detail_page_char_count,
)
from toram_skill_search.models import SkillDetailPayload
from toram_skills.models import SkillSection
from toram_skills.repository import SkillRepository

ROOT = Path(__file__).resolve().parents[1]
SKILL_DATABASE = ROOT / "coryn_data" / "database" / "skills.sqlite"


class SkillDetailPageBuilderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with SkillRepository(SKILL_DATABASE) as repo:
            skill = repo.resolve_skill_name("magic: finale")[0]
            tree = repo.get_tree(skill.tree_id)
        cls.payload = SkillDetailPayload(skill, tree)

    def test_header_order_and_missing_values_are_clean(self):
        pages = build_skill_detail_pages(self.payload)
        self.assertGreaterEqual(len(pages), 1)
        first = pages[0]
        self.assertEqual(first.title, self.payload.skill.name)
        self.assertIn(self.payload.tree.name, first.description)
        self.assertIn(f"Tier {self.payload.skill.tier}", first.description)
        if self.payload.skill.required_level is not None:
            self.assertIn(
                f"Required Lv {self.payload.skill.required_level}",
                first.description,
            )

        visible = "\n".join(
            field.name + "\n" + field.value
            for page in pages
            for field in page.fields
        )
        self.assertNotIn("None", visible)
        if "Overview" in visible and "Range / Timing" in visible:
            self.assertLess(visible.index("Overview"), visible.index("Range / Timing"))

        sparse = SkillDetailPayload(
            replace(
                self.payload.skill,
                skill_type=None,
                mp_cost_text=None,
                mp_cost_value=None,
                damage_type=None,
                element=None,
                cast_range_text=None,
                hit_range_text=None,
                cast_time_text=None,
                hit_count_text=None,
                ailments=(),
                weapon_requirements=(),
                weapon_restrictions=(),
                description=None,
                game_description=None,
                sections=(),
            ),
            self.payload.tree,
        )
        sparse_pages = build_skill_detail_pages(sparse)
        sparse_visible = "\n".join(
            field.name + "\n" + field.value
            for page in sparse_pages
            for field in page.fields
        )
        self.assertNotIn("None", sparse_visible)
        self.assertNotIn("Overview", sparse_visible)
        self.assertNotIn("Range / Timing", sparse_visible)

    def test_long_section_is_split_without_losing_markers_or_budget(self):
        long_body = "START-MARKER\n" + "\n".join(
            f"line-{index}: " + "z" * 180
            for index in range(80)
        ) + "\nEND-MARKER"
        long_payload = SkillDetailPayload(
            replace(
                self.payload.skill,
                description="description-token " * 180,
                game_description=None,
                sections=(
                    SkillSection(
                        position=0,
                        label="Long Mechanics",
                        normalized_label="long mechanics",
                        body=long_body,
                    ),
                ),
            ),
            self.payload.tree,
        )

        pages = build_skill_detail_pages(long_payload)
        self.assertGreater(len(pages), 1)
        all_fields = tuple(field for page in pages for field in page.fields)
        self.assertTrue(any("(continued)" in field.name for field in all_fields))
        combined = "\n".join(field.value for field in all_fields)
        self.assertIn("description-token", combined)
        self.assertIn("START-MARKER", combined)
        self.assertIn("END-MARKER", combined)

        for page in pages:
            self.assertLessEqual(len(page.title), 256)
            self.assertLessEqual(len(page.description), 4096)
            self.assertLessEqual(len(page.fields), 25)
            for field in page.fields:
                self.assertLessEqual(len(field.name), 256)
                self.assertLessEqual(len(field.value), 1024)
            self.assertLessEqual(
                detail_page_char_count(page, footer_text="Page 99 / 99"),
                6000,
            )

    def test_medium_section_moves_whole_to_next_page(self):
        first_body = "a" * 900
        first_sections = tuple(
            SkillSection(
                position=index,
                label=f"Filler {index}",
                normalized_label=f"filler {index}",
                body=first_body,
            )
            for index in range(5)
        )
        medium = SkillSection(
            position=5,
            label="Medium Section",
            normalized_label="medium section",
            body="m" * 900,
        )
        payload = SkillDetailPayload(
            replace(
                self.payload.skill,
                skill_type=None,
                mp_cost_text=None,
                mp_cost_value=None,
                damage_type=None,
                element=None,
                cast_range_text=None,
                hit_range_text=None,
                cast_time_text=None,
                hit_count_text=None,
                ailments=(),
                weapon_requirements=(),
                weapon_restrictions=(),
                description=None,
                game_description=None,
                sections=(*first_sections, medium),
            ),
            self.payload.tree,
        )

        pages = build_skill_detail_pages(payload)
        self.assertGreaterEqual(len(pages), 2)
        medium_locations = [
            (page_index, field.name)
            for page_index, page in enumerate(pages)
            for field in page.fields
            if field.name.startswith("Medium Section")
        ]
        self.assertEqual(medium_locations, [(1, "Medium Section")])


if __name__ == "__main__":
    unittest.main()
