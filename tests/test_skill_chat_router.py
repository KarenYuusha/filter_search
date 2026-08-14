from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import unittest

from toram_skill_chat.router import SkillChatRouter
from toram_skills.repository import SkillRepository


ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "coryn_data" / "database" / "skills.sqlite"


class SkillChatRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = SkillRepository(DATABASE)
        self.router = SkillChatRouter(self.repo)

    def tearDown(self) -> None:
        self.repo.close()

    def skill_id(self, name: str) -> str:
        values = self.repo.resolve_skill_name(name)
        self.assertEqual(len(values), 1, name)
        return values[0].id

    def test_direct_structured_lookup_shapes(self):
        guardian = self.skill_id("Guardian")
        finale = self.skill_id("Magic: Finale")
        hard_hit = self.skill_id("Hard Hit")

        tree = self.router.route("what tree is Guardian in?")
        self.assertEqual((tree.intent, tree.field, tree.skill_ids), ("lookup", "tree", (guardian,)))

        mp = self.router.route("what is Magic: Finale's MP cost?")
        self.assertEqual((mp.intent, mp.field, mp.skill_ids), ("lookup", "mp_cost", (finale,)))

        tier = self.router.route("what tier is Hard Hit?")
        self.assertEqual((tier.intent, tier.field, tier.skill_ids), ("lookup", "tier", (hard_hit,)))

    def test_rank_and_tree_filtered_rank_shapes(self):
        plan = self.router.route("which skill has the highest MP cost?")
        self.assertEqual((plan.intent, plan.field, plan.direction), ("rank", "mp_cost_value", "desc"))

        shield = self.repo.resolve_tree_name("Shield Skills")[0]
        plan = self.router.route("which Shield skill has the lowest MP cost?")
        self.assertEqual((plan.intent, plan.field, plan.direction), ("rank", "mp_cost_value", "asc"))
        self.assertEqual(plan.filters.tree_ids, (shield.id,))

    def test_ailment_filter_and_count_shapes_use_database_concepts(self):
        ignite = self.router.route("which skills inflict ignition?")
        self.assertEqual(ignite.intent, "filter")
        self.assertEqual(ignite.filters.ailments, ("Ignite",))

        stun = self.router.route("how many skills cause Stun?")
        self.assertEqual(stun.intent, "count")
        self.assertEqual(stun.filters.ailments, ("Stun",))

    def test_objective_two_skill_field_comparisons(self):
        hard_hit = self.skill_id("Hard Hit")
        sonic_blade = self.skill_id("Sonic Blade")
        guardian = self.skill_id("Guardian")
        protection = self.skill_id("Protection")

        unlock = self.router.route("which unlocks earlier, Hard Hit or Sonic Blade?")
        self.assertEqual(unlock.intent, "compare_field")
        self.assertEqual(unlock.field, "required_level")
        self.assertEqual(unlock.direction, "asc")
        self.assertEqual(unlock.skill_ids, (hard_hit, sonic_blade))

        mp = self.router.route("which costs more MP, Guardian or Protection?")
        self.assertEqual(mp.intent, "compare_field")
        self.assertEqual(mp.field, "mp_cost_value")
        self.assertEqual(mp.direction, "desc")
        self.assertEqual(mp.skill_ids, (guardian, protection))

    def test_explanation_comparison_and_general_mechanic_shapes(self):
        hard_hit = self.skill_id("Hard Hit")
        protection = self.skill_id("Protection")
        aegis = self.skill_id("Aegis")

        explain = self.router.route("how does Hard Hit work?")
        self.assertEqual((explain.intent, explain.skill_ids), ("explain", (hard_hit,)))

        explain = self.router.route("what does Hard Hit do?")
        self.assertEqual((explain.intent, explain.skill_ids), ("explain", (hard_hit,)))

        compare = self.router.route("compare Protection and Aegis")
        self.assertEqual((compare.intent, compare.skill_ids), ("compare", (protection, aegis)))

        mechanic = self.router.route("what is Flinch?")
        self.assertEqual(mechanic.intent, "general_mechanic")
        self.assertEqual(mechanic.mechanic_query, "Flinch")

    def test_follow_up_filter_preserves_existing_structured_filter(self):
        shield = self.repo.resolve_tree_name("Shield Skills")[0]
        context = SimpleNamespace(
            active_skill_ids=(self.skill_id("Hard Hit"),),
            selected_skill_id=None,
            active_skill_filters={"ailments": ("Stun",)},
        )

        plan = self.router.route("only shield skills", context=context)

        self.assertEqual(plan.intent, "filter")
        self.assertEqual(plan.filters.tree_ids, (shield.id,))
        self.assertEqual(plan.filters.ailments, ("Stun",))

    def test_follow_up_rank_restricts_to_active_skill_ids(self):
        active = (self.skill_id("Guardian"), self.skill_id("Protection"))
        context = SimpleNamespace(
            active_skill_ids=active,
            selected_skill_id=None,
            active_skill_filters={},
        )

        plan = self.router.route("which one costs the least MP?", context=context)

        self.assertEqual(plan.intent, "rank")
        self.assertEqual(plan.skill_ids, active)
        self.assertEqual(plan.field, "mp_cost_value")
        self.assertEqual(plan.direction, "asc")

    def test_follow_up_selected_skill_field_lookup(self):
        guardian = self.skill_id("Guardian")
        context = SimpleNamespace(
            active_skill_ids=(guardian,),
            selected_skill_id=guardian,
            active_skill_filters={},
        )

        plan = self.router.route("what about its MP cost?", context=context)

        self.assertEqual(plan.intent, "lookup")
        self.assertEqual(plan.skill_ids, (guardian,))
        self.assertEqual(plan.field, "mp_cost")

    def test_pronoun_explanation_never_silently_selects_first_result(self):
        hard_hit = self.skill_id("Hard Hit")
        sonic_blade = self.skill_id("Sonic Blade")
        ambiguous = SimpleNamespace(
            active_skill_ids=(hard_hit, sonic_blade),
            selected_skill_id=None,
            active_skill_filters={},
        )
        selected = SimpleNamespace(
            active_skill_ids=(hard_hit, sonic_blade),
            selected_skill_id=sonic_blade,
            active_skill_filters={},
        )

        plan = self.router.route("how does it work?", context=ambiguous)
        self.assertEqual(plan.intent, "unknown")
        self.assertEqual(plan.skill_ids, (hard_hit, sonic_blade))
        self.assertEqual(plan.mechanic_query, "ambiguous_reference")

        plan = self.router.route("how does it work?", context=selected)
        self.assertEqual((plan.intent, plan.skill_ids), ("explain", (sonic_blade,)))

    def test_item_style_query_is_not_hijacked(self):
        plan = self.router.route("cr bow")
        self.assertEqual(plan.intent, "unknown")

    def test_subjective_and_damage_rankings_are_refused_early(self):
        for query in (
            "best skill for DPS",
            "best tank skill",
            "best mage skill",
            "strongest skill",
            "best combo",
            "highest DPS skill",
            "which skill deals the most damage?",
        ):
            with self.subTest(query=query):
                plan = self.router.route(query)
                self.assertEqual(plan.intent, "refuse")
                self.assertIsNotNone(plan.refusal_reason)


if __name__ == "__main__":
    unittest.main()
