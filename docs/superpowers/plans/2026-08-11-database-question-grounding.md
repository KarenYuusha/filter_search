# Database Question Grounding Bugfix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make natural item-count database questions such as `how many bow do you have` deterministic and prevent Qwen from executing database actions whose stat/item-type argument is not grounded in the current user query.

**Architecture:** Keep entity interpretation in `DatabaseQuestionService`, because it already owns canonical item-type/stat resolution. Extend its direct count matcher and add a grounding predicate that resolves contiguous phrases from the current input with the same canonical resolvers. Keep `QwenFallbackService` responsible only for schema/intent orchestration: after ordinary action validation, it calls the grounding predicate before returning a database action.

**Tech Stack:** Python 3.12, `unittest`, existing Toram repository/parser helpers, existing Qwen fallback JSON schema.

## Global Constraints

- This is a targeted bugfix only; do not change search syntax, ranking, Discord UI, Qwen model configuration, database schema, or the supported database action set.
- No session, parser-routing, Discord rendering, or database-schema changes.
- No new runtime dependencies.
- Deterministic item-count questions must not call Qwen.
- Grounding must use existing canonical item-type/stat resolution, not fuzzy free-form guessing or raw substring equality.
- No-argument database actions (`list_stats`, `list_item_types`, `count_items_total`) remain unaffected by grounding.
- An ungrounded Qwen database action must return a failed fallback outcome and must never execute the database action.

---

## File Structure

- Modify `toram_search/help_db.py`: natural count-by-item-type matching, conservative plural handling, phrase scanning, and `DatabaseQuestionService.is_request_grounded()`.
- Modify `toram_search/fallback.py`: accept a grounding callback and reject ungrounded database actions after structural/existence validation.
- Modify `toram_search/fallback_adapter.py`: wire `DatabaseQuestionService.is_request_grounded` into `QwenFallbackService`.
- Create `tests/test_database_question_grounding.py`: direct deterministic/grounding unit regressions.
- Modify `tests/test_search_service.py`: end-to-end service regression proving `how many bow do you have` bypasses Qwen.
- Modify `tests/test_structured_fallback.py`: fallback-level regressions proving ungrounded Qwen database entities are rejected while grounded/no-argument actions remain valid.

---

### Task 1: Deterministic Natural Item Counts

**Files:**
- Modify: `toram_search/help_db.py`
- Create: `tests/test_database_question_grounding.py`
- Modify: `tests/test_search_service.py`

**Interfaces:**
- Consumes: existing `DatabaseQuestionService._canonical_item_type(text) -> tuple[str, tuple[str, ...]] | None`.
- Produces: `DatabaseQuestionService.match_direct(text) -> DatabaseActionRequest | None` recognizing natural item-count variants without Qwen.
- Produces: private conservative helper for trying an item phrase as-is before singularizing only the final token when it ends in `s`.

- [ ] **Step 1: Write direct matcher regression tests**

Create `tests/test_database_question_grounding.py` with a minimal repository fake and assertions equivalent to:

```python
import unittest

from toram_search.help_db import DatabaseActionRequest, DatabaseQuestionService


class FakeRepository:
    def list_item_types(self):
        return {"Bow", "Armor", "Knuckles"}

    def list_stat_names(self):
        return ["Critical Rate", "% stronger against Dark"]

    def count_items_total(self):
        return 100

    def count_items_by_types(self, item_types):
        return {("Bow",): 12, ("Armor",): 20, ("Knuckles",): 7}.get(tuple(item_types), 0)

    def count_items_with_stat(self, stat_name):
        return 5


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
        self.service = DatabaseQuestionService(
            FakeRepository(),
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
```

- [ ] **Step 2: Run the direct tests and confirm RED**

Run:

```bash
python -m unittest tests.test_database_question_grounding -v
```

Expected: the new natural variants fail because current `match_direct()` only accepts `how many <type> items are there|are in the database`.

- [ ] **Step 3: Implement conservative item-count phrase resolution**

In `DatabaseQuestionService`, add a private helper with behavior equivalent to:

```python
def _canonical_item_type_count_phrase(self, text: str) -> tuple[str, tuple[str, ...]] | None:
    cleaned = self._clean(text)
    resolved = self._canonical_item_type(cleaned)
    if resolved is not None:
        return resolved

    words = cleaned.split()
    if not words:
        return None
    last = words[-1]
    if len(last) <= 1 or not last.casefold().endswith("s"):
        return None
    singular = " ".join((*words[:-1], last[:-1]))
    return self._canonical_item_type(singular)
```

Keep as-is resolution first so canonical plural types such as `Knuckles` remain authoritative.

Extend `match_direct()` with a count pattern that accepts both the existing and new forms without changing total-count precedence:

```python
m = re.fullmatch(
    r"how many (.+?)(?: items)? (?:do you have|are there|are in the database)",
    raw,
    flags=re.I,
)
if m:
    item = self._canonical_item_type_count_phrase(m.group(1))
    if item is not None:
        return DatabaseActionRequest("count_items_by_type", item_type=item[0])
```

Retain the existing total-item exact matches before this pattern so `how many items are there` remains `count_items_total`.

- [ ] **Step 4: Run direct tests and confirm GREEN**

Run:

```bash
python -m unittest tests.test_database_question_grounding -v
```

Expected: all Task 1 direct tests pass.

- [ ] **Step 5: Add SearchService no-Qwen regression**

In `tests/test_search_service.py`, make `FakeRepository.count_items_by_types()` record calls and return a nonzero Bow count, then add:

```python
def test_natural_bow_count_never_calls_qwen(self):
    repository = FakeRepository()
    service = SearchService(repository, llm_client=MustNotCallLLM())
    context = FailedQueryContext(max_entries=3)

    outcome = service.handle_query("how many bow do you have", context)

    self.assertEqual(outcome.kind, "database")
    self.assertEqual(outcome.text, "There are 12 Bow items in the database.")
    self.assertEqual(repository.count_type_calls, [("Bow",)])
    self.assertEqual(context.snapshot(), ())
```

Update the fake only as needed:

```python
self.count_type_calls = []

def count_items_by_types(self, item_types):
    item_types = tuple(item_types)
    self.count_type_calls.append(item_types)
    return 12 if item_types == ("Bow",) else 0
```

- [ ] **Step 6: Run SearchService regression and relevant existing database tests**

Run:

```bash
python -m unittest tests.test_search_service.SearchServiceTests.test_natural_bow_count_never_calls_qwen -v
python -m unittest tests.test_search_service -v
```

Expected: pass, with no Qwen invocation for the reported query.

- [ ] **Step 7: Commit Task 1**

```bash
git add toram_search/help_db.py tests/test_database_question_grounding.py tests/test_search_service.py
git commit -m "fix: parse natural item count questions"
```

---

### Task 2: Ground Qwen Database Actions in User Input

**Files:**
- Modify: `toram_search/help_db.py`
- Modify: `toram_search/fallback.py`
- Modify: `toram_search/fallback_adapter.py`
- Modify: `tests/test_database_question_grounding.py`
- Modify: `tests/test_structured_fallback.py`

**Interfaces:**
- Produces: `DatabaseQuestionService.is_request_grounded(request: DatabaseActionRequest, text: str) -> bool`.
- Produces: `QwenFallbackService(..., ground_database_action: Callable[[DatabaseActionRequest, str], bool], ...)`.
- Consumes: existing `validate_database_action(request)` for ordinary schema/entity validity before grounding.

- [ ] **Step 1: Write grounding unit tests in `tests/test_database_question_grounding.py`**

Add tests equivalent to:

```python
def test_item_action_must_be_grounded_in_query(self):
    bow = DatabaseActionRequest("count_items_by_type", item_type="Bow")
    armor = DatabaseActionRequest("count_items_by_type", item_type="Armor")
    self.assertTrue(self.service.is_request_grounded(bow, "could you count bows for me"))
    self.assertFalse(self.service.is_request_grounded(armor, "could you count bows for me"))


def test_stat_action_accepts_alias_but_rejects_unrelated_real_stat(self):
    critical_rate = DatabaseActionRequest("count_items_with_stat", stat="Critical Rate")
    dark = DatabaseActionRequest("count_items_with_stat", stat="% stronger against Dark")
    self.assertTrue(self.service.is_request_grounded(critical_rate, "how many items have cr"))
    self.assertFalse(self.service.is_request_grounded(dark, "how many bow do you have"))


def test_no_argument_actions_are_always_grounded(self):
    for action in ("list_stats", "list_item_types", "count_items_total"):
        with self.subTest(action=action):
            self.assertTrue(
                self.service.is_request_grounded(DatabaseActionRequest(action), "natural wording")
            )
```

- [ ] **Step 2: Run grounding unit tests and confirm RED**

Run:

```bash
python -m unittest tests.test_database_question_grounding -v
```

Expected: grounding tests fail because `is_request_grounded()` does not exist.

- [ ] **Step 3: Implement phrase-based canonical grounding in `DatabaseQuestionService`**

Add a phrase iterator that scans contiguous input-token windows, longest first, without fuzzy matching:

```python
def _input_phrases(self, text: str):
    tokens = self._clean(text).split()
    for width in range(len(tokens), 0, -1):
        for start in range(0, len(tokens) - width + 1):
            yield " ".join(tokens[start : start + width])
```

Then implement:

```python
def is_request_grounded(self, request: DatabaseActionRequest, text: str) -> bool:
    if request.action in {"list_stats", "list_item_types", "count_items_total"}:
        return True

    if request.action in {"count_items_by_type", "item_type_exists"}:
        if request.item_type is None:
            return False
        target = self._canonical_item_type(request.item_type)
        if target is None:
            return False
        for phrase in self._input_phrases(text):
            resolved = self._canonical_item_type_count_phrase(phrase)
            if resolved is not None and resolved[1] == target[1]:
                return True
        return False

    if request.action in {"count_items_with_stat", "stat_exists"}:
        if request.stat is None:
            return False
        target = self._canonical_stat(request.stat)
        if target is None:
            return False
        return any(self._canonical_stat(phrase) == target for phrase in self._input_phrases(text))

    return False
```

Do not use fuzzy resolution. Ambiguous terms that do not resolve uniquely remain ungrounded.

- [ ] **Step 4: Run grounding unit tests and confirm GREEN**

Run:

```bash
python -m unittest tests.test_database_question_grounding -v
```

Expected: all grounding and deterministic-count tests pass.

- [ ] **Step 5: Write Qwen fallback RED tests**

Update the `service()` helper in `tests/test_structured_fallback.py` to accept a grounding callback:

```python
def service(
    payload,
    validator=lambda request: True,
    database_validator=lambda request: True,
    database_grounder=lambda request, text: True,
):
    return QwenFallbackService(
        FakeLLM(payload),
        validate_search_request=validator,
        validate_database_action=database_validator,
        ground_database_action=database_grounder,
        stat_catalog=("Critical Rate", "Critical Damage", "MaxHP", "% stronger against Dark"),
        alias_catalog=(
            "cr -> critical rate",
            "crit -> Critical Rate / Critical Damage",
            "hp -> maxhp",
        ),
        item_filter_catalog=("bow -> Bow", "armor -> Armor", "xtal -> All Crysta"),
    )
```

Add regressions equivalent to:

```python
def test_ungrounded_database_entity_is_rejected(self):
    seen = []
    fallback = service(
        {
            "intent": "database_action",
            "action": "count_items_with_stat",
            "stat": "% stronger against Dark",
        },
        database_grounder=lambda request, text: seen.append((request, text)) or False,
    )

    outcome = fallback.interpret("how many bow do you have", ())

    self.assertEqual(outcome.kind, "failed")
    self.assertEqual(len(seen), 1)


def test_grounded_database_entity_is_accepted(self):
    fallback = service(
        {
            "intent": "database_action",
            "action": "count_items_by_type",
            "item_type": "Bow",
        },
        database_grounder=lambda request, text: text == "how many bows do you have",
    )

    outcome = fallback.interpret("how many bows do you have", ())

    self.assertEqual(outcome.kind, "database_action")
    self.assertEqual(outcome.database_request.item_type, "Bow")


def test_no_argument_database_action_remains_accepted(self):
    fallback = service(
        {"intent": "database_action", "action": "list_stats"},
        database_grounder=lambda request, text: True,
    )
    self.assertEqual(fallback.interpret("which stats can I search", ()).kind, "database_action")
```

- [ ] **Step 6: Run fallback tests and confirm RED**

Run:

```bash
python -m unittest tests.test_structured_fallback -v
```

Expected: constructor/tests fail because `QwenFallbackService` does not yet accept/use `ground_database_action`.

- [ ] **Step 7: Add the grounding callback to `QwenFallbackService`**

In `toram_search/fallback.py`:

```python
def __init__(
    self,
    llm_client: LLMClient,
    *,
    validate_search_request: Callable[[SearchIntentRequest], bool],
    validate_database_action: Callable[[DatabaseActionRequest], bool],
    ground_database_action: Callable[[DatabaseActionRequest, str], bool],
    stat_catalog: tuple[str, ...],
    alias_catalog: tuple[str, ...],
    item_filter_catalog: tuple[str, ...],
) -> None:
    ...
    self.ground_database_action = ground_database_action
```

Keep `_database_request_from_payload()` responsible for shape plus `validate_database_action()` only. In `interpret()`, after it returns a request:

```python
if intent == "database_action":
    request = self._database_request_from_payload(payload)
    if request is None:
        return self._failed("database action is invalid", payload)
    if not self.ground_database_action(request, current_input):
        return self._failed("database action is not grounded in current input", payload)
    return FallbackOutcome("database_action", database_request=request)
```

This preserves the distinction between "valid database entity" and "supported by what the user actually said".

- [ ] **Step 8: Wire production grounding in `fallback_adapter.py`**

Update `build_fallback_service()`:

```python
return QwenFallbackService(
    llm_client,
    validate_search_request=lambda request: parse_structured_search_request(
        request,
        repository,
    ) is not None,
    validate_database_action=database_service.validate_request,
    ground_database_action=database_service.is_request_grounded,
    stat_catalog=tuple(repository.list_stat_names()),
    alias_catalog=tuple(aliases),
    item_filter_catalog=tuple(filter_labels),
)
```

- [ ] **Step 9: Run fallback and service tests and confirm GREEN**

Run:

```bash
python -m unittest tests.test_structured_fallback -v
python -m unittest tests.test_search_service -v
python -m unittest tests.test_database_question_grounding -v
```

Expected: all pass; ungrounded database entity actions are rejected before execution.

- [ ] **Step 10: Commit Task 2**

```bash
git add toram_search/help_db.py toram_search/fallback.py toram_search/fallback_adapter.py tests/test_database_question_grounding.py tests/test_structured_fallback.py
git commit -m "fix: ground fallback database actions"
```

---

### Task 3: Full Regression Verification

**Files:**
- No production changes expected.
- Modify tests only if verification reveals a missing regression directly related to this bugfix.

**Interfaces:**
- Consumes: completed deterministic matcher and Qwen grounding behavior from Tasks 1-2.
- Produces: verification evidence that the bugfix preserves the rest of the project.

- [ ] **Step 1: Run syntax compilation**

```bash
python -m compileall -q toram_data toram_search toram_discord search_items.py discord_bot.py
```

Expected: exit code 0.

- [ ] **Step 2: Run focused regression suite**

```bash
python -m unittest tests.test_database_question_grounding -v
python -m unittest tests.test_structured_fallback -v
python -m unittest tests.test_search_service -v
```

Expected: all focused tests pass.

- [ ] **Step 3: Run the full repository suite**

```bash
python -m unittest discover -s tests -v
```

Expected: zero failures and zero errors.

- [ ] **Step 4: Review the diff against the design**

Verify explicitly:

```text
- `how many bow do you have` is handled by `DatabaseQuestionService.match_direct()`.
- plural `bows` resolves conservatively; canonical plural item types are tried first.
- deterministic item-count route does not invoke Qwen or record failed-query context.
- Qwen cannot execute a stat/item-type database action when that entity is absent from the current input under canonical resolution.
- valid grounded entity actions remain allowed.
- list/count-total metadata actions remain unaffected.
- no Discord, ranking, search semantics, database schema, or Qwen model configuration changed.
```

- [ ] **Step 5: Commit any verification-only regression additions, if needed**

If no files changed, do not create an empty commit. If a directly relevant missing regression was added:

```bash
git add tests/
git commit -m "test: cover database question grounding regressions"
```
