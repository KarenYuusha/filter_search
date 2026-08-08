import unittest

from toram_search.fallback import QwenFallbackService


class FakeLLM:
    def __init__(self, payload):
        self.payload = payload
        self.schemas = []

    def complete(self, system_prompt, user_prompt, *, schema=None):
        self.schemas.append(schema)
        return self.payload


def service(payload, validator=lambda query: True):
    return QwenFallbackService(
        FakeLLM(payload),
        validate_search_rewrite=validator,
        validate_database_action=lambda request: True,
        stat_catalog=("Critical Rate", "Critical Damage", "MaxHP"),
        alias_catalog=(
            "cr -> critical rate",
            "crit -> Critical Rate / Critical Damage",
            "hp -> maxhp",
        ),
        item_filter_catalog=("bow -> Bow", "xtal -> All Crysta"),
    )


class StructuredFallbackTests(unittest.TestCase):
    def test_structured_candidate_renders_to_deterministic_query(self):
        seen = []
        fallback = service(
            {
                "intent": "search",
                "candidates": [
                    {
                        "item_filter": "bow",
                        "stats": [{"name": "Critical Rate"}],
                        "sort_stat": "Critical Rate",
                    }
                ],
            },
            lambda query: seen.append(query) or True,
        )

        outcome = fallback.interpret("which bow has the most crit rate", ())

        self.assertEqual(outcome.kind, "suggestions")
        self.assertEqual(outcome.suggestions, ("Critical Rate bow",))
        self.assertEqual(seen, ["Critical Rate bow"])

    def test_multiple_structured_candidates_preserve_ambiguity(self):
        fallback = service(
            {
                "intent": "search",
                "candidates": [
                    {"item_filter": "bow", "stats": [{"name": "Critical Rate"}]},
                    {"item_filter": "bow", "stats": [{"name": "Critical Damage"}]},
                ],
            }
        )

        outcome = fallback.interpret("find a bow with crit", ())

        self.assertEqual(
            outcome.suggestions,
            ("Critical Rate bow", "Critical Damage bow"),
        )

    def test_numeric_and_query_renders(self):
        fallback = service(
            {
                "intent": "search",
                "candidates": [
                    {
                        "item_filter": "bow",
                        "match": "all",
                        "stats": [
                            {"name": "MaxHP", "operator": ">=", "value": 5000},
                            {"name": "Critical Rate"},
                        ],
                    }
                ],
            }
        )

        outcome = fallback.interpret("bow with 5000 hp and crit rate", ())

        self.assertEqual(
            outcome.suggestions,
            ("MaxHP >= 5000 AND Critical Rate bow",),
        )

    def test_invalid_operator_is_rejected(self):
        fallback = service(
            {
                "intent": "search",
                "candidates": [
                    {
                        "stats": [
                            {"name": "MaxHP", "operator": "LIKE", "value": 5}
                        ]
                    }
                ],
            }
        )
        self.assertEqual(fallback.interpret("x", ()).kind, "failed")

    def test_unknown_candidate_key_is_rejected(self):
        fallback = service(
            {
                "intent": "search",
                "candidates": [
                    {
                        "stats": [{"name": "MaxHP"}],
                        "sql": "select * from items",
                    }
                ],
            }
        )
        self.assertEqual(fallback.interpret("x", ()).kind, "failed")

    def test_candidate_must_pass_deterministic_validator(self):
        fallback = service(
            {
                "intent": "search",
                "candidates": [{"stats": [{"name": "Made Up Stat"}]}],
            },
            lambda query: False,
        )
        self.assertEqual(fallback.interpret("x", ()).kind, "failed")

    def test_simple_best_query_uses_deterministic_rewrite_before_llm(self):
        class MustNotRunLLM:
            def complete(self, *args, **kwargs):
                raise AssertionError("LLM should not be called")

        fallback = QwenFallbackService(
            MustNotRunLLM(),
            validate_search_rewrite=lambda query: query == "bow cr",
            validate_database_action=lambda request: True,
            stat_catalog=("Critical Rate",),
            alias_catalog=("cr -> critical rate",),
            item_filter_catalog=("bow -> Bow",),
        )

        outcome = fallback.interpret("best bow cr", ())

        self.assertEqual(outcome.suggestions, ("bow cr",))

    def test_schema_is_sent_to_llm_client(self):
        llm = FakeLLM({"intent": "refuse"})
        fallback = QwenFallbackService(
            llm,
            validate_search_rewrite=lambda query: True,
            validate_database_action=lambda request: True,
            stat_catalog=("Critical Rate",),
            alias_catalog=(),
            item_filter_catalog=("bow -> Bow",),
        )

        fallback.interpret("hello", ())

        self.assertIsInstance(llm.schemas[0], dict)
        self.assertEqual(
            llm.schemas[0]["properties"]["intent"]["enum"],
            ["search", "database_action", "help", "refuse"],
        )


if __name__ == "__main__":
    unittest.main()
