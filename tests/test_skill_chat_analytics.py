from __future__ import annotations

from pathlib import Path
import unittest

from toram_skill_chat.analytics import SkillAnalytics
from toram_skill_chat.models import SkillChatFilter
from toram_skills.repository import SkillRepository


ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "coryn_data" / "database" / "skills.sqlite"


class SkillChatAnalyticsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = SkillRepository(DATABASE)
        self.analytics = SkillAnalytics(self.repo)

    def tearDown(self) -> None:
        self.repo.close()

    def test_repository_lists_known_ailments_in_stable_order(self):
        ailments = self.repo.list_known_ailments()
        self.assertIn("Stun", ailments)
        self.assertIn("Tumble", ailments)
        self.assertEqual(ailments, tuple(sorted(ailments, key=str.casefold)))

    def test_highest_mp_skill_is_ranked_from_numeric_field(self):
        results = self.analytics.rank("mp_cost_value", "desc", limit=1)
        self.assertEqual(len(results), 1)
        self.assertIsNotNone(results[0].mp_cost_value)

        all_ranked = self.analytics.rank("mp_cost_value", "desc", limit=10000)
        self.assertEqual(results[0].mp_cost_value, max(s.mp_cost_value for s in all_ranked))

    def test_lowest_mp_can_be_restricted_to_one_tree(self):
        shield = self.repo.resolve_tree_name("Shield Skills")[0]
        results = self.analytics.rank(
            "mp_cost_value",
            "asc",
            filters=SkillChatFilter(tree_ids=(shield.id,)),
            limit=3,
        )

        self.assertGreater(len(results), 0)
        self.assertTrue(all(skill.tree_id == shield.id for skill in results))
        self.assertEqual(
            [skill.mp_cost_value for skill in results],
            sorted(skill.mp_cost_value for skill in results),
        )

    def test_ailment_filter_accepts_structured_or_explicit_positive_evidence(self):
        ignite = self.analytics.filter_skills(SkillChatFilter(ailments=("Ignite",)))
        self.assertGreater(len(ignite), 0)
        for skill in ignite:
            if "Ignite" in skill.ailments:
                continue
            rows = self.repo.connection.execute(
                "SELECT text FROM skill_search_documents WHERE skill_id = ?",
                (skill.id,),
            )
            documented = "\n".join(str(row["text"]) for row in rows).casefold()
            self.assertTrue(
                any(
                    phrase in documented
                    for phrase in (
                        "inflict ignite",
                        "inflicts ignite",
                        "cause ignite",
                        "causes ignite",
                    )
                ),
                skill.name,
            )

        stun = self.analytics.filter_skills(SkillChatFilter(ailments=("Stun",)))
        self.assertEqual(
            self.analytics.count(SkillChatFilter(ailments=("Stun",))),
            len(stun),
        )

    def test_compare_field_preserves_requested_skill_order(self):
        guardian = self.repo.resolve_skill_name("Guardian")[0]
        protection = self.repo.resolve_skill_name("Protection")[0]
        values = self.analytics.compare_field(
            (guardian.id, protection.id),
            "mp_cost_value",
        )

        self.assertEqual(tuple(value.skill.id for value in values), (guardian.id, protection.id))
        self.assertEqual(tuple(value.value for value in values), (guardian.mp_cost_value, protection.mp_cost_value))

    def test_null_numeric_values_are_excluded_from_ranking(self):
        ranked = self.analytics.rank("mp_cost_value", "asc", limit=10000)
        self.assertGreater(len(ranked), 0)
        self.assertTrue(all(skill.mp_cost_value is not None for skill in ranked))

    def test_only_allowlisted_fields_can_be_ranked_or_compared(self):
        with self.assertRaises(ValueError):
            self.analytics.rank("description", "desc")
        with self.assertRaises(ValueError):
            self.analytics.compare_field(("x",), "description")


if __name__ == "__main__":
    unittest.main()
