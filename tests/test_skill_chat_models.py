from __future__ import annotations

from dataclasses import FrozenInstanceError
import unittest

from toram_skill_chat.models import (
    SkillChatFilter,
    SkillChatPlan,
    SkillChatResult,
    SkillEvidence,
)


class SkillChatModelTests(unittest.TestCase):
    def test_plan_has_safe_immutable_defaults(self):
        plan = SkillChatPlan(intent="rank")

        self.assertEqual(plan.skill_ids, ())
        self.assertEqual(plan.filters, SkillChatFilter())
        self.assertEqual(plan.limit, 5)
        self.assertIsNone(plan.field)
        with self.assertRaises(FrozenInstanceError):
            plan.limit = 10  # type: ignore[misc]

    def test_filter_carries_only_allowlisted_structured_dimensions(self):
        filters = SkillChatFilter(
            tree_ids=("shield-skills",),
            tiers=(3,),
            skill_types=("Active",),
            ailments=("Stun",),
            weapons=("Shield",),
            required_level_max=150,
            mp_cost_max=500,
        )

        self.assertEqual(filters.ailments, ("Stun",))
        self.assertEqual(filters.mp_cost_max, 500)

    def test_result_and_evidence_are_typed_value_objects(self):
        evidence = SkillEvidence(
            document_id="hard-hit#summary",
            skill_id="hard-hit",
            skill_name="Hard Hit",
            tree_name="Blade Skills",
            text="Skill: Hard Hit",
            source_kind="summary",
        )
        result = SkillChatResult(
            kind="answer",
            text="Grounded answer",
            skill_ids=("hard-hit",),
            evidence=(evidence,),
        )

        self.assertEqual(result.evidence[0].skill_name, "Hard Hit")
        self.assertIsNone(result.evidence[0].label)


if __name__ == "__main__":
    unittest.main()
