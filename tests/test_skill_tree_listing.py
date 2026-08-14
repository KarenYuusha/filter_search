from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from toram_discord.sessions import DiscordSessionManager
from toram_discord.skill_ui import (
    SkillDetailView,
    SkillTreeChoicesView,
    SkillTreeConfirmationView,
    SkillTreeResultsView,
    build_skill_payload_message,
    build_skill_tree_results_embed,
    build_skill_tree_results_message,
)
from toram_skill_search.models import (
    SkillDetailPayload,
    SkillResultItem,
    SkillTreeChoicesPayload,
    SkillTreeConfirmationPayload,
    SkillTreeHelpPayload,
    SkillTreeNotFoundPayload,
    SkillTreeResultsPayload,
)
from toram_skill_search.service import SkillSearchService, run_skill_search
from toram_skills.models import SkillDraft, SkillTreeDraft
from toram_skills.repository import SkillRepository

ROOT = Path(__file__).resolve().parents[1]
SKILL_DATABASE = ROOT / "coryn_data" / "database" / "skills.sqlite"


def _tree(name: str) -> SkillTreeDraft:
    return SkillTreeDraft(
        id=name.casefold().replace(" ", "-"),
        name=name,
        normalized_name=name.casefold(),
        tree_group="test",
        source_file="test.txt",
        general_text="",
    )


def _skill(tree: SkillTreeDraft, index: int) -> SkillDraft:
    return SkillDraft(
        id=f"{tree.id}/skill-{index}",
        tree_id=tree.id,
        source_order=index,
        name=f"Skill {index}",
        normalized_name=f"skill {index}",
        tier=2,
        required_level=20,
        skill_type="Active",
        mp_cost_text="100",
        mp_cost_value=100,
        damage_type="Physical",
        description=f"Preview {index}",
    )


class FakeTreeRepository:
    def __init__(self) -> None:
        trees = (
            _tree("Shield Skills"),
            _tree("Magic Skills"),
            _tree("Magic Warrior Skills"),
        )
        self.trees_by_name = {tree.name.casefold(): tree for tree in trees}
        self.trees_by_id = {tree.id: tree for tree in trees}
        self.skills_by_tree = {tree.id: () for tree in trees}
        self.closed = False

    def list_tree_names(self):
        return sorted(tree.name for tree in self.trees_by_name.values())

    def resolve_tree_name(self, name):
        tree = self.trees_by_name.get(" ".join(str(name).casefold().split()))
        return () if tree is None else (tree,)

    def get_tree(self, tree_id):
        return self.trees_by_id[tree_id]

    def list_skills_in_tree(self, tree_id):
        return self.skills_by_tree[tree_id]

    def resolve_skill_name(self, name):
        return ()

    def get_skill(self, skill_id):
        for skills in self.skills_by_tree.values():
            for skill in skills:
                if skill.id == skill_id:
                    return skill
        raise KeyError(skill_id)

    def close(self):
        self.closed = True


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


class SkillTreeServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = FakeTreeRepository()
        self.service = SkillSearchService(self.repo, semantic_runtime=None)

    def test_bare_tree_returns_help(self):
        payload = self.service.handle("tree")
        self.assertIsInstance(payload, SkillTreeHelpPayload)
        self.assertIn("Shield Skills", payload.tree_names)

    def test_exact_and_unique_shorthand_execute(self):
        exact = self.service.handle("tree SHIELD SKILLS")
        shorthand = self.service.handle("tree shield")
        self.assertIsInstance(exact, SkillTreeResultsPayload)
        self.assertIsInstance(shorthand, SkillTreeResultsPayload)
        self.assertEqual(exact.tree.name, "Shield Skills")
        self.assertEqual(shorthand.tree.name, "Shield Skills")

    def test_typo_requires_confirmation(self):
        payload = self.service.handle("tree sheild")
        self.assertIsInstance(payload, SkillTreeConfirmationPayload)
        self.assertEqual(payload.suggested_tree.name, "Shield Skills")

    def test_close_fuzzy_matches_return_choices(self):
        payload = self.service.handle("tree magic skillz")
        self.assertIsInstance(payload, SkillTreeChoicesPayload)
        self.assertEqual(
            [tree.name for tree in payload.candidates[:2]],
            ["Magic Skills", "Magic Warrior Skills"],
        )

    def test_low_confidence_junk_returns_not_found(self):
        payload = self.service.handle("tree xyzabc")
        self.assertIsInstance(payload, SkillTreeNotFoundPayload)
        self.assertLessEqual(len(payload.suggestions), 3)

    def test_treehouse_remains_free_text(self):
        with patch.object(self.service, "_search_free_text", return_value=Mock()) as search:
            result = self.service.handle("treehouse")
        search.assert_called_once_with("treehouse")
        self.assertIs(result, search.return_value)

    def test_tree_path_does_not_touch_semantic_runtime(self):
        runtime = Mock()
        SkillSearchService(self.repo, semantic_runtime=runtime).handle("tree shield")
        runtime.get_index.assert_not_called()

    def test_tree_listing_returns_every_skill_in_source_order(self):
        tree = self.repo.resolve_tree_name("Shield Skills")[0]
        self.repo.skills_by_tree[tree.id] = tuple(_skill(tree, index) for index in range(23))
        payload = self.service.handle("tree shield")
        self.assertEqual(len(payload.results), 23)
        self.assertEqual(
            [result.skill.source_order for result in payload.results],
            list(range(23)),
        )


class _NoIconCatalog:
    def resolve(self, tree_name: str, skill_name: str):
        return None


class SkillTreeDiscordTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.sessions = DiscordSessionManager()
        self.key = (10, 30, 20)
        self.session = self.sessions.start_query(self.key, "skill tree shield")
        self.tree = _tree("Shield Skills")
        self.payload = SkillTreeResultsPayload(
            self.tree,
            tuple(
                SkillResultItem(
                    _skill(self.tree, index),
                    self.tree,
                    f"Preview {index}",
                )
                for index in range(6)
            ),
        )

    def test_tree_results_are_five_per_page_without_repeated_tree_name(self):
        first = build_skill_tree_results_embed(self.payload, 0)
        second = build_skill_tree_results_embed(self.payload, 1)
        self.assertEqual(first.title, "Shield Skills")
        self.assertIn("6 skills", first.description or "")
        self.assertIn("Showing 1–5 of 6", first.description or "")
        self.assertNotIn("Closest matches first", first.description or "")
        self.assertNotIn("Shield Skills •", first.description or "")
        self.assertIn("6. **Skill 5**", second.description or "")

    def test_tree_results_message_uses_header_plus_skill_cards(self):
        rendered = build_skill_tree_results_message(
            self.payload,
            0,
            icon_catalog=_NoIconCatalog(),
        )
        self.assertEqual(len(rendered.embeds), 6)
        self.assertEqual(rendered.embeds[0].title, "Shield Skills")
        self.assertIn("6 skills", rendered.embeds[0].description or "")
        self.assertEqual(rendered.embeds[1].title, "1. Skill 0")
        self.assertNotIn("Shield Skills", rendered.embeds[1].description or "")
        self.assertIn("Tier 2", rendered.embeds[1].description or "")
        self.assertIn("Required Lv 20", rendered.embeds[1].description or "")
        self.assertEqual(rendered.files, ())

    def test_bare_tree_help_and_not_found_are_noninteractive(self):
        help_embed, help_view = build_skill_payload_message(
            SkillTreeHelpPayload(("Shield Skills", "Magic Skills")),
            bot_example_prefix="@bot",
            sessions=self.sessions,
            key=self.key,
            generation=self.session.generation,
            database_path=SKILL_DATABASE,
        )
        self.assertIsNone(help_view)
        self.assertIn("skill tree <tree name>", help_embed.description or "")
        self.assertIn("Shield Skills", help_embed.description or "")

        missing_embed, missing_view = build_skill_payload_message(
            SkillTreeNotFoundPayload("xyzabc", ("Shield Skills",)),
            bot_example_prefix="@bot",
            sessions=self.sessions,
            key=self.key,
            generation=self.session.generation,
            database_path=SKILL_DATABASE,
        )
        self.assertIsNone(missing_view)
        self.assertIn("couldn't find", (missing_embed.description or "").casefold())

    async def test_tree_result_select_opens_detail_and_back_returns_to_tree(self):
        view = SkillTreeResultsView(
            sessions=self.sessions,
            key=self.key,
            generation=self.session.generation,
            payload=self.payload,
        )
        interaction = FakeInteraction()
        await view._select_skill(interaction, ["0"])
        detail_view = interaction.response.edits[-1]["view"]
        self.assertIsInstance(detail_view, SkillDetailView)
        self.assertIs(detail_view.results_payload, self.payload)

        back = FakeInteraction()
        await detail_view._back(back)
        back_edit = back.response.edits[-1]
        self.assertIsInstance(back_edit["view"], SkillTreeResultsView)
        self.assertIn("attachments", back_edit)
        self.assertEqual(back_edit["embeds"][0].title, "Shield Skills")

    async def test_confirmation_and_choices_use_trusted_tree_ids(self):
        confirmation = SkillTreeConfirmationPayload("sheild", self.tree)
        _, confirmation_view = build_skill_payload_message(
            confirmation,
            bot_example_prefix="@bot",
            sessions=self.sessions,
            key=self.key,
            generation=self.session.generation,
            database_path=SKILL_DATABASE,
        )
        self.assertIsInstance(confirmation_view, SkillTreeConfirmationView)
        with patch(
            "toram_discord.skill_ui.run_skill_tree_by_id",
            return_value=self.payload,
        ) as trusted:
            interaction = FakeInteraction()
            await confirmation_view._show(interaction)
        trusted.assert_called_once_with(SKILL_DATABASE, self.tree.id)

        magic = _tree("Magic Skills")
        magic_warrior = _tree("Magic Warrior Skills")
        choices = SkillTreeChoicesPayload("magic skillz", (magic, magic_warrior))
        _, choices_view = build_skill_payload_message(
            choices,
            bot_example_prefix="@bot",
            sessions=self.sessions,
            key=self.key,
            generation=self.session.generation,
            database_path=SKILL_DATABASE,
        )
        self.assertIsInstance(choices_view, SkillTreeChoicesView)
        with patch(
            "toram_discord.skill_ui.run_skill_tree_by_id",
            return_value=self.payload,
        ) as trusted:
            interaction = FakeInteraction()
            await choices_view._select(interaction, ["1"])
        trusted.assert_called_once_with(SKILL_DATABASE, magic_warrior.id)


class RealSkillTreeAcceptanceTests(unittest.TestCase):
    def test_real_database_tree_resolution_and_order(self):
        payload = run_skill_search(
            SKILL_DATABASE,
            "tree shield",
            semantic_runtime=None,
        )
        self.assertIsInstance(payload, SkillTreeResultsPayload)
        self.assertEqual(payload.tree.name, "Shield Skills")
        with SkillRepository(SKILL_DATABASE) as repo:
            expected = [
                skill.id
                for skill in repo.list_skills_in_tree(payload.tree.id)
            ]
        self.assertEqual(
            [result.skill.id for result in payload.results],
            expected,
        )

    def test_real_database_tree_examples_and_skill_regression(self):
        cases = (
            ("tree Shield Skills", SkillTreeResultsPayload, "Shield Skills"),
            ("tree shield", SkillTreeResultsPayload, "Shield Skills"),
            ("tree sheild", SkillTreeConfirmationPayload, "Shield Skills"),
            ("tree magic", SkillTreeResultsPayload, "Magic Skills"),
            ("tree xyzabc", SkillTreeNotFoundPayload, None),
        )
        for query, payload_type, tree_name in cases:
            with self.subTest(query=query):
                payload = run_skill_search(
                    SKILL_DATABASE,
                    query,
                    semantic_runtime=None,
                )
                self.assertIsInstance(payload, payload_type)
                if isinstance(payload, SkillTreeResultsPayload):
                    self.assertEqual(payload.tree.name, tree_name)
                elif isinstance(payload, SkillTreeConfirmationPayload):
                    self.assertEqual(payload.suggested_tree.name, tree_name)

        ordinary = run_skill_search(
            SKILL_DATABASE,
            "shield bash",
            semantic_runtime=None,
        )
        self.assertIsInstance(ordinary, SkillDetailPayload)
        treehouse = run_skill_search(
            SKILL_DATABASE,
            "treehouse",
            semantic_runtime=None,
        )
        self.assertNotIsInstance(
            treehouse,
            (
                SkillTreeResultsPayload,
                SkillTreeConfirmationPayload,
                SkillTreeChoicesPayload,
                SkillTreeNotFoundPayload,
                SkillTreeHelpPayload,
            ),
        )


if __name__ == "__main__":
    unittest.main()
