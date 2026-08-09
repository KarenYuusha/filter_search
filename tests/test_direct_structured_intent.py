import unittest
from decimal import Decimal

from search_items import (
    ParsedSearch,
    _interactive_search_requests,
    _try_natural_search_normalization,
    _try_simple_ranking_search,
    parse_structured_search_request,
    resolve_expression_interactively,
    route_deterministically,
)
from toram_data.stat_query import ResolvedAndGroup, ResolvedClause, ResolvedStatExpression
from toram_search.fallback import FallbackOutcome, SearchIntentRequest, SearchStatIntent


class FakeRepository:
    def __init__(self):
        self.stats = ["Critical Rate", "Critical Damage", "MaxHP"]
        self.item_types = {"Bow", "Armor"}

    def list_stat_names(self):
        return list(self.stats)

    def list_item_types(self):
        return set(self.item_types)

    def exact_name_matches(self, query):
        return []

    def exact_upgrade_name_matches(self, query):
        return []


class NoHelpService:
    def answer_direct(self, query):
        return None


class NoDatabaseService:
    def match_direct(self, query):
        return None


class DirectStructuredIntentTests(unittest.TestCase):
    def setUp(self):
        self.repository = FakeRepository()

    def test_one_bare_stat_becomes_direct_stat_search(self):
        request = SearchIntentRequest(
            stats=(SearchStatIntent("Critical Rate"),),
            item_filter="bow",
        )

        parsed = parse_structured_search_request(
            request,
            self.repository,
            raw_query="which bow has crit",
        )

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.intent, "stat_search")
        self.assertEqual(parsed.raw_query, "which bow has crit")
        self.assertEqual(parsed.stat.stat_name, "Critical Rate")
        self.assertEqual(parsed.filter.label, "Bow")
        self.assertEqual(parsed.filter.item_types, ("Bow",))

    def test_explicit_comparison_becomes_resolved_expression(self):
        request = SearchIntentRequest(
            stats=(SearchStatIntent("MaxHP", ">=", 5000),),
            item_filter="bow",
        )

        parsed = parse_structured_search_request(request, self.repository)

        self.assertEqual(parsed.intent, "stat_expression")
        clause = parsed.resolved_expression.groups[0].clauses[0]
        self.assertEqual(clause.stat_name, "MaxHP")
        self.assertEqual(clause.operator, ">=")
        self.assertEqual(clause.value, Decimal("5000"))

    def test_all_request_uses_one_and_group_and_bare_stat_semantics(self):
        request = SearchIntentRequest(
            stats=(
                SearchStatIntent("MaxHP", ">=", 5000),
                SearchStatIntent("Critical Rate"),
            ),
            match="all",
        )

        parsed = parse_structured_search_request(request, self.repository)

        self.assertEqual(len(parsed.resolved_expression.groups), 1)
        clauses = parsed.resolved_expression.groups[0].clauses
        self.assertEqual([clause.stat_name for clause in clauses], ["MaxHP", "Critical Rate"])
        self.assertEqual(clauses[1].operator, ">=")
        self.assertEqual(clauses[1].value, Decimal("1"))

    def test_any_request_uses_separate_or_groups(self):
        request = SearchIntentRequest(
            stats=(SearchStatIntent("MaxHP"), SearchStatIntent("Critical Rate")),
            match="any",
        )

        parsed = parse_structured_search_request(request, self.repository)

        groups = parsed.resolved_expression.groups
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0].clauses[0].stat_name, "MaxHP")
        self.assertEqual(groups[1].clauses[0].stat_name, "Critical Rate")

    def test_sort_stat_is_moved_to_primary_clause(self):
        request = SearchIntentRequest(
            stats=(SearchStatIntent("MaxHP"), SearchStatIntent("Critical Rate")),
            match="all",
            sort_stat="Critical Rate",
        )

        parsed = parse_structured_search_request(request, self.repository)

        clauses = parsed.resolved_expression.groups[0].clauses
        self.assertEqual([clause.stat_name for clause in clauses], ["Critical Rate", "MaxHP"])

    def test_unknown_stat_is_rejected_without_fuzzy_matching(self):
        request = SearchIntentRequest(stats=(SearchStatIntent("Made Up Stat"),))
        self.assertIsNone(parse_structured_search_request(request, self.repository))

    def test_ambiguous_stat_is_rejected_instead_of_guessed(self):
        request = SearchIntentRequest(stats=(SearchStatIntent("crit"),))
        self.assertIsNone(parse_structured_search_request(request, self.repository))

    def test_partial_item_filter_is_rejected(self):
        request = SearchIntentRequest(
            stats=(SearchStatIntent("Critical Rate"),),
            item_filter="bow extra",
        )
        self.assertIsNone(parse_structured_search_request(request, self.repository))

    def test_resolved_expression_bypasses_interactive_resolution(self):
        expression = ResolvedStatExpression(
            (
                ResolvedAndGroup(
                    (ResolvedClause("Critical Rate", "Critical Rate", ">=", Decimal("1")),)
                ),
            )
        )
        parsed = ParsedSearch(
            intent="stat_expression",
            raw_query="natural language",
            resolved_expression=expression,
        )

        class MustNotReadRepository:
            def list_stat_names(self):
                raise AssertionError("already-resolved intent must not resolve stats again")

        result = resolve_expression_interactively(parsed, MustNotReadRepository())
        self.assertIs(result, parsed)

    def test_simple_best_query_is_parsed_before_qwen(self):
        parsed = _try_simple_ranking_search("best bow cr", self.repository)
        self.assertIsNotNone(parsed)
        self.assertIn(parsed.intent, {"stat_search", "stat_choices", "stat_expression"})

    def test_natural_armor_hp_is_normalized_without_qwen(self):
        parsed = _try_natural_search_normalization(
            "can you find armor with hp",
            self.repository,
        )

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.intent, "stat_search")
        self.assertEqual(parsed.raw_query, "can you find armor with hp")
        self.assertEqual(parsed.stat.stat_name, "MaxHP")
        self.assertEqual(parsed.filter.label, "Armor")

    def test_natural_bow_cr_is_normalized_without_qwen(self):
        parsed = _try_natural_search_normalization(
            "show me bow with cr",
            self.repository,
        )

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.intent, "stat_search")
        self.assertEqual(parsed.stat.stat_name, "Critical Rate")
        self.assertEqual(parsed.filter.label, "Bow")

    def test_which_plural_item_have_stat_is_normalized(self):
        parsed = _try_natural_search_normalization(
            "which bows have critical rate",
            self.repository,
        )

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.stat.stat_name, "Critical Rate")
        self.assertEqual(parsed.filter.label, "Bow")

    def test_having_form_is_normalized(self):
        parsed = _try_natural_search_normalization(
            "armor having hp",
            self.repository,
        )

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.stat.stat_name, "MaxHP")
        self.assertEqual(parsed.filter.label, "Armor")

    def test_unsupported_conversation_is_not_rewritten(self):
        self.assertIsNone(
            _try_natural_search_normalization(
                "tell me something interesting about armor",
                self.repository,
            )
        )

    def test_natural_query_routes_to_search_before_fallback(self):
        route = route_deterministically(
            "can you find armor with hp",
            self.repository,
            [],
            NoHelpService(),
            NoDatabaseService(),
        )

        self.assertEqual(route.kind, "search")
        self.assertIsNotNone(route.parsed)
        self.assertEqual(route.parsed.stat.stat_name, "MaxHP")
        self.assertEqual(route.parsed.filter.label, "Armor")

    def test_single_confirmation_returns_typed_request(self):
        request = SearchIntentRequest(
            stats=(SearchStatIntent("Critical Rate"),),
            item_filter="bow",
        )
        outcome = FallbackOutcome("search_requests", search_requests=(request,))
        output = []

        result = _interactive_search_requests(
            "which bow has crit",
            outcome,
            input_fn=lambda prompt: "y",
            output_fn=output.append,
        )

        self.assertEqual(result.kind, "selected")
        self.assertIs(result.search_request, request)
        self.assertIsNone(result.query)

    def test_multiple_confirmation_returns_selected_typed_request(self):
        first = SearchIntentRequest(stats=(SearchStatIntent("Critical Rate"),))
        second = SearchIntentRequest(stats=(SearchStatIntent("Critical Damage"),))
        outcome = FallbackOutcome("search_requests", search_requests=(first, second))

        result = _interactive_search_requests(
            "find crit",
            outcome,
            input_fn=lambda prompt: "2",
            output_fn=lambda text: None,
        )

        self.assertEqual(result.kind, "selected")
        self.assertIs(result.search_request, second)

    def test_confirmation_can_return_a_completely_new_text_query(self):
        request = SearchIntentRequest(stats=(SearchStatIntent("Critical Rate"),))
        outcome = FallbackOutcome("search_requests", search_requests=(request,))

        result = _interactive_search_requests(
            "find crit",
            outcome,
            input_fn=lambda prompt: "hp armor",
            output_fn=lambda text: None,
        )

        self.assertEqual(result.kind, "new_query")
        self.assertEqual(result.query, "hp armor")
        self.assertIsNone(result.search_request)


if __name__ == "__main__":
    unittest.main()
