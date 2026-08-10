import unittest

from toram_search.service import (
    ExpressionResultsPayload,
    SearchService,
    StatClarificationPayload,
    StatResultsPayload,
)
from toram_search.session import FailedQueryContext


class FakeRepository:
    def __init__(self):
        self.stats = ["Critical Rate", "Critical Damage", "MaxHP"]
        self.item_types = {
            "Bow",
            "Armor",
            "Weapon Crysta",
            "Enhancer Crysta (Red)",
        }
        self.expression_calls = []
        self.stat_calls = []

    def list_items(self):
        return []

    def list_item_types(self):
        return set(self.item_types)

    def list_stat_names(self):
        return list(self.stats)

    def exact_name_matches(self, query):
        return []

    def exact_upgrade_name_matches(self, query):
        return []

    def search_by_expression(self, expression, item_types, *, primary_sort_ascending=False):
        self.expression_calls.append((expression, item_types, primary_sort_ascending))
        return []

    def search_by_stat(self, stat_name, item_types):
        self.stat_calls.append((stat_name, item_types))
        return []

    def count_items_total(self):
        return 0

    def count_items_by_types(self, item_types):
        return 0

    def count_items_with_stat(self, stat_name):
        return 0


class MustNotCallLLM:
    def complete(self, *args, **kwargs):
        raise AssertionError("deterministic query must not call Qwen")


class FakeLLM:
    def __init__(self, payload):
        self.payload = payload

    def complete(self, *args, **kwargs):
        return self.payload


class SearchServiceTests(unittest.TestCase):
    def test_deterministic_armor_hp_never_calls_qwen(self):
        repository = FakeRepository()
        service = SearchService(repository, llm_client=MustNotCallLLM())

        outcome = service.handle_query(
            "can you find armor with hp",
            FailedQueryContext(max_entries=3),
        )

        self.assertEqual(outcome.kind, "search")
        self.assertIsInstance(outcome.payload, ExpressionResultsPayload)
        self.assertEqual(len(repository.expression_calls), 1)
        self.assertEqual(repository.expression_calls[0][1], ("Armor",))
        clause = repository.expression_calls[0][0].groups[0].clauses[0]
        self.assertEqual(clause.stat_name, "MaxHP")

    def test_natural_highest_critical_rate_bow_never_calls_qwen(self):
        repository = FakeRepository()
        service = SearchService(repository, llm_client=MustNotCallLLM())

        outcome = service.handle_query(
            "which bow has the highest critical rate",
            FailedQueryContext(max_entries=3),
        )

        self.assertEqual(outcome.kind, "search")
        self.assertIsInstance(outcome.payload, ExpressionResultsPayload)
        self.assertEqual(len(repository.expression_calls), 1)
        expression, item_types, ascending = repository.expression_calls[0]
        self.assertEqual(item_types, ("Bow",))
        self.assertFalse(ascending)
        self.assertEqual(expression.groups[0].clauses[0].stat_name, "Critical Rate")

    def test_ambiguous_crit_returns_clarification_without_input(self):
        repository = FakeRepository()
        service = SearchService(repository, llm_client=MustNotCallLLM())

        outcome = service.handle_query("crit bow", FailedQueryContext(max_entries=3))

        self.assertEqual(outcome.kind, "search")
        self.assertIsInstance(outcome.payload, StatClarificationPayload)
        self.assertEqual(
            outcome.payload.clarification.candidates,
            ("Critical Rate", "Critical Damage"),
        )
        self.assertEqual(repository.expression_calls, [])

    def test_reconstructed_weapon_xtal_never_calls_qwen_or_records_failure(self):
        repository = FakeRepository()
        service = SearchService(repository, llm_client=MustNotCallLLM())
        context = FailedQueryContext(max_entries=3)

        outcome = service.handle_query("xtall cr weapon", context)

        self.assertEqual(outcome.kind, "search")
        self.assertIsInstance(outcome.payload, ExpressionResultsPayload)
        self.assertEqual(context.snapshot(), ())
        expression, item_types, ascending = repository.expression_calls[-1]
        self.assertEqual(item_types, ("Weapon Crysta", "Enhancer Crysta (Red)"))
        self.assertFalse(ascending)
        self.assertEqual(expression.groups[0].clauses[0].stat_name, "Critical Rate")

    def test_reconstructed_crit_uses_existing_clarification_without_qwen(self):
        repository = FakeRepository()
        service = SearchService(repository, llm_client=MustNotCallLLM())
        context = FailedQueryContext(max_entries=3)

        outcome = service.handle_query("crit xtal weapon", context)

        self.assertEqual(outcome.kind, "search")
        self.assertIsInstance(outcome.payload, StatClarificationPayload)
        self.assertEqual(
            outcome.payload.clarification.candidates,
            ("Critical Rate", "Critical Damage"),
        )
        self.assertEqual(context.snapshot(), ())
        self.assertEqual(repository.expression_calls, [])

    def test_clarification_choice_executes_deterministic_search(self):
        repository = FakeRepository()
        service = SearchService(repository, llm_client=MustNotCallLLM())
        first = service.handle_query("crit bow", FailedQueryContext(max_entries=3))
        clarification = first.payload.clarification

        outcome = service.continue_clarification(
            first.payload.parsed,
            {(clarification.group_index, clarification.clause_index): "Critical Rate"},
        )

        self.assertEqual(outcome.kind, "search")
        self.assertIsInstance(outcome.payload, ExpressionResultsPayload)
        self.assertEqual(
            repository.expression_calls[-1][0].groups[0].clauses[0].stat_name,
            "Critical Rate",
        )

    def test_qwen_search_requires_confirmation_before_execution(self):
        repository = FakeRepository()
        llm = FakeLLM(
            {
                "intent": "search",
                "candidates": [
                    {"item_filter": "armor", "stats": [{"name": "MaxHP"}]}
                ],
            }
        )
        service = SearchService(repository, llm_client=llm)

        outcome = service.handle_query(
            "could you locate protective equipment that increases health",
            FailedQueryContext(max_entries=3),
        )

        self.assertEqual(outcome.kind, "confirm_search")
        self.assertEqual(len(outcome.search_requests), 1)
        self.assertEqual(repository.stat_calls, [])
        self.assertEqual(repository.expression_calls, [])

    def test_confirmed_qwen_request_is_validated_then_executed(self):
        repository = FakeRepository()
        service = SearchService(repository, llm_client=MustNotCallLLM())

        from toram_search.fallback import SearchIntentRequest, SearchStatIntent

        request = SearchIntentRequest(
            stats=(SearchStatIntent("MaxHP"),),
            item_filter="armor",
        )
        outcome = service.confirm_search_request(
            request,
            "natural wording",
            FailedQueryContext(max_entries=3),
        )

        self.assertEqual(outcome.kind, "search")
        self.assertIsInstance(outcome.payload, StatResultsPayload)
        self.assertEqual(repository.stat_calls, [("MaxHP", ("Armor",))])


if __name__ == "__main__":
    unittest.main()
