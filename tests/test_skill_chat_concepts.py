from __future__ import annotations

import unittest

from toram_skill_chat.concepts import ConceptResolution, resolve_ailment


class SkillChatConceptTests(unittest.TestCase):
    def test_exact_canonical_match_is_case_insensitive_and_normalized(self):
        known = ("Stun", "Ignite", "Armor Break")

        self.assertEqual(
            resolve_ailment("  armor   break ", known),
            ConceptResolution("Armor Break", "exact"),
        )
        self.assertEqual(
            resolve_ailment("STUN", known),
            ConceptResolution("Stun", "exact"),
        )

    def test_ignition_alias_resolves_only_when_ignite_exists(self):
        self.assertEqual(
            resolve_ailment("ignition", ("Ignite", "Stun")),
            ConceptResolution("Ignite", "alias"),
        )
        self.assertEqual(
            resolve_ailment("ignition", ("Stun",)),
            ConceptResolution(None, "none"),
        )

    def test_unrelated_phrase_does_not_invent_a_concept(self):
        self.assertEqual(
            resolve_ailment("set enemies on fire", ("Ignite", "Stun")),
            ConceptResolution(None, "none"),
        )


if __name__ == "__main__":
    unittest.main()
