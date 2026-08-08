import unittest

from toram_search.fallback import (
    QwenFallbackService,
    SearchIntentRequest,
    SearchStatIntent,
)


class FakeLLM:
    def __init__(self, payload):
        self.payload = payload
        self.schemas = []

    def complete(self, system_prompt, user_prompt, *, schema=None):
        self.schemas.append(schema)
        return self.payload


def service(payload, validator=lambda request: True):
    return QwenFallbackService(
        FakeLLM(payload),
        validate_search_request=validator,
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
    def test_structured_candidate_returns_typed_request(self):
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
            lambda request: seen.append(request) or True,
        )

        outcome = fallback.interpret("which bow has the most crit rate", ())

        expected = SearchIntentRequest(
            stats=(SearchStatIntent("Critical Rate"),),
            item_filter="bow",
            sort_stat="Critical Rate",
        )
        self.assertEqual(outcome.kind, "search_requests")
        self.assertEqual(outcome.search_requests, (expected,))
        self.assertEqual(seen, [expected])

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
            outcome.search_requests,
            (
                SearchIntentRequest(
                    stats=(SearchStatIntent("Critical Rate"),),
                    item_filter="bow",
                ),
                SearchIntentRequest(
                    stats=(SearchStatIntent("Critical Damage"),),
                    item_filter="bow",
                ),
            ),
        )

    def test_numeric_and_candidate_stays_structured(self):
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
            outcome.search_requests,
            (
                SearchIntentRequest(
                    stats=(
                        SearchStatIntent("MaxHP", ">=", 5000),
                        SearchStatIntent("Critical Rate"),
                    ),
                    item_filter="bow",
                    match="all",
                ),
            ),
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

    def test_candidate_must_pass_typed_validator(self):
        seen = []
        fallback = service(
            {
                "intent": "search",
                "candidates": [{"stats": [{"name": "Made Up Stat"}]}],
            },
            lambda request: seen.append(request) or False,
        )

        outcome = fallback.interpret("x", ())

        self.assertEqual(outcome.kind, "failed")
        self.assertEqual(
            seen,
            [SearchIntentRequest(stats=(SearchStatIntent("Made Up Stat"),))],
        )

    def test_duplicate_typed_candidates_are_deduplicated(self):
        fallback = service(
            {
                "intent": "search",
                "candidates": [
                    {"stats": [{"name": "Critical Rate"}]},
                    {"stats": [{"name": "Critical Rate"}]},
                ],
            }
        )

        outcome = fallback.interpret("crit", ())

        self.assertEqual(
            outcome.search_requests,
            (SearchIntentRequest(stats=(SearchStatIntent("Critical Rate"),)),),
        )

    def test_schema_is_sent_to_llm_client(self):
        llm = FakeLLM({"intent": "refuse"})
        fallback = QwenFallbackService(
            llm,
            validate_search_request=lambda request: True,
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
