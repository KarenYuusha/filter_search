from __future__ import annotations

import unittest

from tests.test_search_service import FakeLLM, FakeRepository, MustNotCallLLM
from toram_search.service import SearchService
from toram_search.session import FailedQueryContext
from toram_search.understanding import understand_item_query


def expression_signature(call):
    expression, item_types, ascending = call
    groups = tuple(
        tuple(
            (clause.stat_name, clause.operator, str(clause.value))
            for clause in group.clauses
        )
        for group in expression.groups
    )
    return groups, tuple(item_types or ()), ascending


class ItemQuerySafetyTests(unittest.TestCase):
    def canonical_signature(self):
        repository = FakeRepository()
        service = SearchService(repository, llm_client=MustNotCallLLM())
        outcome = service.handle_query("cr wp xtal", FailedQueryContext(max_entries=3))
        self.assertEqual(outcome.kind, "search")
        self.assertTrue(repository.expression_calls)
        return expression_signature(repository.expression_calls[-1])

    def test_unknown_meaning_never_disappears_on_auto_execution_path(self):
        for query in (
            "cr weapon xtal blah",
            "blah cr weapon xtal",
            "cr blah weapon xtal",
        ):
            with self.subTest(query=query):
                repository = FakeRepository()
                service = SearchService(repository, llm_client=MustNotCallLLM())
                context = FailedQueryContext(max_entries=3)

                outcome = service.handle_query(query, context)

                self.assertEqual(outcome.kind, "item_understanding")
                self.assertIn(
                    "blah",
                    outcome.pending_item_search.understanding.unresolved_tokens,
                )
                self.assertEqual(repository.expression_calls, [])
                self.assertEqual(repository.stat_calls, [])
                self.assertEqual(context.snapshot(), ())

    def test_reconstructed_query_matches_canonical_semantics(self):
        expected = self.canonical_signature()
        repository = FakeRepository()
        service = SearchService(repository, llm_client=MustNotCallLLM())

        outcome = service.handle_query(
            "xtall cr weapon",
            FailedQueryContext(max_entries=3),
        )

        self.assertEqual(outcome.kind, "search")
        self.assertEqual(expression_signature(repository.expression_calls[-1]), expected)

    def test_confirmed_fuzzy_filter_matches_canonical_semantics(self):
        expected = self.canonical_signature()
        repository = FakeRepository()
        service = SearchService(repository, llm_client=MustNotCallLLM())
        context = FailedQueryContext(max_entries=3)

        first = service.handle_query("cr wepon xtal", context)
        issue = first.pending_item_search.understanding.uncertainties[0]
        final = service.continue_item_understanding(
            first.pending_item_search,
            issue.issue_id,
            issue.choices[0].value,
            context,
        )

        self.assertEqual(final.kind, "search")
        self.assertEqual(expression_signature(repository.expression_calls[-1]), expected)

    def test_multi_correction_flow_matches_canonical_semantics(self):
        expected = self.canonical_signature()
        repository = FakeRepository()
        service = SearchService(repository, llm_client=MustNotCallLLM())
        context = FailedQueryContext(max_entries=3)

        first = service.handle_query("crit wepon xtal", context)
        stat_issue = first.pending_item_search.understanding.uncertainties[0]
        second = service.continue_item_understanding(
            first.pending_item_search,
            stat_issue.issue_id,
            "Critical Rate",
            context,
        )
        filter_issue = second.pending_item_search.understanding.uncertainties[0]
        third = service.continue_item_understanding(
            second.pending_item_search,
            filter_issue.issue_id,
            filter_issue.choices[0].value,
            context,
        )
        self.assertEqual(third.kind, "item_understanding")
        self.assertEqual(third.pending_item_search.understanding.uncertainties, ())
        self.assertEqual(repository.expression_calls, [])

        final = service.confirm_pending_item_search(third.pending_item_search, context)

        self.assertEqual(final.kind, "search")
        self.assertEqual(expression_signature(repository.expression_calls[-1]), expected)

    def test_highest_ambiguous_stat_is_clarified_before_safe_suggestion(self):
        repository = FakeRepository()
        service = SearchService(repository, llm_client=MustNotCallLLM())
        context = FailedQueryContext(max_entries=3)

        first = service.handle_query("highest crit xtal weapon", context)
        self.assertEqual(first.kind, "item_understanding")
        first_understanding = first.pending_item_search.understanding
        self.assertEqual(first_understanding.decision, "clarify")
        self.assertEqual(first_understanding.uncertainties[0].reason, "AMBIGUOUS_STAT")
        self.assertIn("highest", first_understanding.unresolved_tokens)

        issue = first_understanding.uncertainties[0]
        second = service.continue_item_understanding(
            first.pending_item_search,
            issue.issue_id,
            "Critical Rate",
            context,
        )
        second_understanding = second.pending_item_search.understanding
        self.assertEqual(second_understanding.decision, "suggest")
        self.assertEqual(second_understanding.suggested_query, "cr wp xtal")
        self.assertIn("highest", second_understanding.unresolved_tokens)
        self.assertEqual(repository.expression_calls, [])
        self.assertEqual(context.snapshot(), ())

    def test_true_unresolved_structure_calls_qwen_once_and_records_failure(self):
        repository = FakeRepository()
        llm = FakeLLM({"intent": "search", "candidates": []})
        service = SearchService(repository, llm_client=llm)
        context = FailedQueryContext(max_entries=3)

        outcome = service.handle_query("cr hp weapon xtal", context)

        self.assertEqual(outcome.kind, "failed")
        self.assertEqual(llm.calls, 1)
        self.assertEqual(len(context.snapshot()), 1)
        self.assertEqual(repository.expression_calls, [])
        self.assertEqual(repository.stat_calls, [])

    def test_qwen_search_candidate_never_executes_before_confirmation(self):
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
        context = FailedQueryContext(max_entries=3)

        outcome = service.handle_query(
            "could you locate protective equipment that increases health",
            context,
        )

        self.assertEqual(outcome.kind, "confirm_search")
        self.assertEqual(llm.calls, 1)
        self.assertEqual(repository.expression_calls, [])
        self.assertEqual(repository.stat_calls, [])
        self.assertEqual(len(context.snapshot()), 1)

    def test_rejected_qwen_search_candidate_never_executes(self):
        repository = FakeRepository()
        llm = FakeLLM(
            {
                "intent": "search",
                "candidates": [
                    {"item_filter": "armor", "stats": [{"name": "Not A Real Stat"}]}
                ],
            }
        )
        service = SearchService(repository, llm_client=llm)
        context = FailedQueryContext(max_entries=3)

        outcome = service.handle_query("find protective gear with mystery power", context)

        self.assertEqual(outcome.kind, "failed")
        self.assertEqual(llm.calls, 1)
        self.assertEqual(repository.expression_calls, [])
        self.assertEqual(repository.stat_calls, [])

    def test_oversized_understanding_fails_closed(self):
        query = " ".join(["show"] * 30 + ["cr", "weapon", "xtal"])
        result = understand_item_query(
            query,
            available_stats=["Critical Rate", "Critical Damage", "MaxHP"],
            available_item_types={
                "Weapon Crysta",
                "Enhancer Crysta (Red)",
                "Armor",
                "Bow",
            },
        )
        self.assertEqual(result.decision, "fallback")
        self.assertIn("UNSAFE_SHAPE", result.reasons)
        self.assertIsNone(result.canonical_query)


if __name__ == "__main__":
    unittest.main()
