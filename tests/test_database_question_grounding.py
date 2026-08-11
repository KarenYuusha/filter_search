import unittest

from toram_search.help_db import DatabaseActionRequest, DatabaseQuestionService
from toram_search.service import SearchService
from toram_search.session import FailedQueryContext


class FakeRepository:
    def __init__(self):
        self.count_type_calls = []

    def list_items(self):
        return []

    def list_item_types(self):
        return {"Bow", "Armor", "Knuckles"}

    def list_stat_names(self):
        return ["Critical Rate", "% stronger against Dark"]

    def exact_name_matches(self, query):
        return []

    def exact_upgrade_name_matches(self, query):
        return []

    def count_items_total(self):
        return 100

    def count_items_by_types(self, item_types):
        item_types = tuple(item_types)
        self.count_type_calls.append(item_types)
        return 12 if item_types == ("Bow",) else 0

    def count_items_with_stat(self, stat_name):
        return 5


class MustNotCallLLM:
    def complete(self, *args, **kwargs):
        raise AssertionError("deterministic database query must not call Qwen")


def resolve_item_type(text):
    normalized = " ".join(text.casefold().split())
    mapping = {
        "bow": ("Bow", ("Bow",)),
        "armor": ("Armor", ("Armor",)),
        "knuckles": ("Knuckles", ("Knuckles",)),
    }
    return mapping.get(normalized)


def resolve_stat(text):
    mapping = {
        "cr": "Critical Rate",
        "critical rate": "Critical Rate",
        "% stronger against dark": "% stronger against Dark",
    }
    return mapping.get(" ".join(text.casefold().split()))


class DatabaseQuestionGroundingTests(unittest.TestCase):
    def setUp(self):
        self.repository = FakeRepository()
        self.service = DatabaseQuestionService(
            self.repository,
            resolve_item_type=resolve_item_type,
            resolve_stat=resolve_stat,
        )

    def test_natural_item_count_forms_resolve_to_item_type_action(self):
        expected = DatabaseActionRequest("count_items_by_type", item_type="Bow")
        for query in (
            "how many bow do you have",
            "how many bows do you have",
            "how many bow items do you have",
            "how many bows are there",
            "how many Bow items are there",
        ):
            with self.subTest(query=query):
                self.assertEqual(self.service.match_direct(query), expected)

    def test_existing_plural_canonical_type_is_tried_before_singularizing(self):
        self.assertEqual(
            self.service.match_direct("how many knuckles do you have"),
            DatabaseActionRequest("count_items_by_type", item_type="Knuckles"),
        )

    def test_unknown_item_count_does_not_guess(self):
        self.assertIsNone(self.service.match_direct("how many mysterys do you have"))

    def test_natural_bow_count_never_calls_qwen(self):
        service = SearchService(self.repository, llm_client=MustNotCallLLM())
        context = FailedQueryContext(max_entries=3)

        outcome = service.handle_query("how many bow do you have", context)

        self.assertEqual(outcome.kind, "database")
        self.assertEqual(outcome.text, "There are 12 Bow items in the database.")
        self.assertEqual(self.repository.count_type_calls, [("Bow",)])
        self.assertEqual(context.snapshot(), ())


if __name__ == "__main__":
    unittest.main()
