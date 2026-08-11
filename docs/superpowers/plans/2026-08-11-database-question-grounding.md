# Database Question Grounding Bugfix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make natural item-count questions such as `how many bow do you have` deterministic and prevent Qwen from executing database actions whose stat/item-type argument is not grounded in the current user query.

**Architecture:** `DatabaseQuestionService` remains the owner of canonical stat/item-type interpretation. It gains natural count matching plus a phrase-based grounding predicate. `QwenFallbackService` keeps schema/action validation separate, then calls that grounding predicate before accepting an argument-bearing database action; `fallback_adapter.py` wires the service into production.

**Tech Stack:** Python 3.12, `unittest`, existing repository/parser helpers, existing Qwen JSON-schema fallback.

## Global Constraints

- Targeted bugfix only; no Discord UI/session, ranking, search syntax, database schema, Qwen model configuration, or supported database-action changes.
- No new runtime dependencies.
- Deterministic item-count questions must not call Qwen or record failed-query context.
- Grounding must use existing canonical resolvers with no fuzzy free-form guessing and no raw substring-only check.
- No-argument database actions (`list_stats`, `list_item_types`, `count_items_total`) remain valid.
- An ungrounded Qwen database action must become a failed fallback outcome before database execution.

---

## File Structure

- Modify `toram_search/help_db.py` — natural count parsing, conservative plural handling, canonical phrase grounding.
- Modify `toram_search/fallback.py` — accept/use a database grounding callback.
- Modify `toram_search/fallback_adapter.py` — wire production grounding.
- Create `tests/test_database_question_grounding.py` — direct matcher/grounding regressions.
- Modify `tests/test_search_service.py` — reported-query no-Qwen integration regression.
- Modify `tests/test_structured_fallback.py` — Qwen grounding regressions and constructor updates.

---

### Task 1: Deterministic Natural Item Counts

**Files:**
- Modify: `toram_search/help_db.py`
- Create: `tests/test_database_question_grounding.py`
- Modify: `tests/test_search_service.py`

**Interfaces:**
- Consumes: `DatabaseQuestionService._canonical_item_type(text)`.
- Produces: `_canonical_item_type_count_phrase(text) -> tuple[str, tuple[str, ...]] | None`.
- Produces: `match_direct()` support for natural count-by-type questions.

- [ ] **Step 1: Write direct RED tests**

Create a minimal `FakeRepository` and resolver callbacks in `tests/test_database_question_grounding.py`, then assert:

```python
expected = DatabaseActionRequest("count_items_by_type", item_type="Bow")
for query in (
    "how many bow do you have",
    "how many bows do you have",
    "how many bow items do you have",
    "how many bows are there",
    "how many Bow items are there",
):
    self.assertEqual(service.match_direct(query), expected)

self.assertEqual(
    service.match_direct("how many knuckles do you have"),
    DatabaseActionRequest("count_items_by_type", item_type="Knuckles"),
)
self.assertIsNone(service.match_direct("how many mysterys do you have"))
```

The fake item resolver must recognize only `bow`, `armor`, and canonical plural `knuckles`; this proves plural fallback is conservative.

- [ ] **Step 2: Run RED test**

```bash
python -m unittest tests.test_database_question_grounding -v
```

Expected: natural variants fail because current matching requires `how many <type> items are there|are in the database`.

- [ ] **Step 3: Implement conservative plural resolution**

In `DatabaseQuestionService`:

```python
def _canonical_item_type_count_phrase(self, text: str):
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
    return self._canonical_item_type(" ".join((*words[:-1], last[:-1])))
```

Try the phrase unchanged first so canonical plural types such as `Knuckles` are never damaged by singularization.

- [ ] **Step 4: Expand `match_direct()`**

Keep total-item exact forms before the type-count pattern, then accept:

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

Do not guess when the captured phrase does not resolve.

- [ ] **Step 5: Run direct GREEN test**

```bash
python -m unittest tests.test_database_question_grounding -v
```

Expected: all direct count tests pass.

- [ ] **Step 6: Add SearchService no-Qwen regression**

Update `tests/test_search_service.py` `FakeRepository` with `count_type_calls` and return `12` for `("Bow",)`. Add:

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

- [ ] **Step 7: Run focused service tests**

```bash
python -m unittest tests.test_search_service.SearchServiceTests.test_natural_bow_count_never_calls_qwen -v
python -m unittest tests.test_search_service -v
```

Expected: pass; Qwen is never invoked for the reported query.

- [ ] **Step 8: Commit Task 1**

```bash
git add toram_search/help_db.py tests/test_database_question_grounding.py tests/test_search_service.py
git commit -m "fix: parse natural item count questions"
```

---

### Task 2: Ground Qwen Database Actions

**Files:**
- Modify: `toram_search/help_db.py`
- Modify: `toram_search/fallback.py`
- Modify: `toram_search/fallback_adapter.py`
- Modify: `tests/test_database_question_grounding.py`
- Modify: `tests/test_structured_fallback.py`

**Interfaces:**
- Produces: `DatabaseQuestionService.is_request_grounded(request: DatabaseActionRequest, text: str) -> bool`.
- Changes: `QwenFallbackService.__init__` gains required `ground_database_action: Callable[[DatabaseActionRequest, str], bool]`.
- Preserves: `validate_database_action(request)` as the separate shape/existence validator.

- [ ] **Step 1: Write direct grounding RED tests**

Add:

```python
bow = DatabaseActionRequest("count_items_by_type", item_type="Bow")
armor = DatabaseActionRequest("count_items_by_type", item_type="Armor")
self.assertTrue(service.is_request_grounded(bow, "could you count bows for me"))
self.assertFalse(service.is_request_grounded(armor, "could you count bows for me"))

cr = DatabaseActionRequest("count_items_with_stat", stat="Critical Rate")
dark = DatabaseActionRequest("count_items_with_stat", stat="% stronger against Dark")
self.assertTrue(service.is_request_grounded(cr, "how many items have cr"))
self.assertFalse(service.is_request_grounded(dark, "how many bow do you have"))

for action in ("list_stats", "list_item_types", "count_items_total"):
    self.assertTrue(service.is_request_grounded(DatabaseActionRequest(action), "natural wording"))
```

- [ ] **Step 2: Run RED grounding tests**

```bash
python -m unittest tests.test_database_question_grounding -v
```

Expected: fail because `is_request_grounded()` does not exist.

- [ ] **Step 3: Implement canonical phrase scanning**

In `DatabaseQuestionService`:

```python
def _input_phrases(self, text: str):
    tokens = self._clean(text).split()
    for width in range(len(tokens), 0, -1):
        for start in range(len(tokens) - width + 1):
            yield " ".join(tokens[start : start + width])
```

Implement grounding:

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
        return any(
            resolved is not None and resolved[1] == target[1]
            for phrase in self._input_phrases(text)
            for resolved in (self._canonical_item_type_count_phrase(phrase),)
        )

    if request.action in {"count_items_with_stat", "stat_exists"}:
        if request.stat is None:
            return False
        target = self._canonical_stat(request.stat)
        if target is None:
            return False
        return any(self._canonical_stat(phrase) == target for phrase in self._input_phrases(text))

    return False
```

No fuzzy matching. Ambiguous aliases that do not resolve uniquely remain ungrounded.

- [ ] **Step 4: Run grounding GREEN tests**

```bash
python -m unittest tests.test_database_question_grounding -v
```

Expected: all direct count and grounding tests pass.

- [ ] **Step 5: Write fallback RED tests and update every constructor call**

In `tests/test_structured_fallback.py`, update the shared `service()` helper to accept:

```python
database_grounder=lambda request, text: True
```

and pass:

```python
ground_database_action=database_grounder
```

Also update every direct `QwenFallbackService(...)` constructor in this file (including `test_schema_is_sent_to_llm_client`) with:

```python
ground_database_action=lambda request, text: True,
```

Add:

```python
def test_ungrounded_database_entity_is_rejected(self):
    fallback = service(
        {
            "intent": "database_action",
            "action": "count_items_with_stat",
            "stat": "% stronger against Dark",
        },
        database_grounder=lambda request, text: False,
    )
    self.assertEqual(fallback.interpret("how many bow do you have", ()).kind, "failed")


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
    self.assertEqual(fallback.interpret("which stats can i search", ()).kind, "database_action")
```

- [ ] **Step 6: Run fallback RED tests**

```bash
python -m unittest tests.test_structured_fallback -v
```

Expected: fail until `QwenFallbackService` accepts and uses the grounding callback.

- [ ] **Step 7: Add grounding gate to `QwenFallbackService`**

Constructor:

```python
self.ground_database_action = ground_database_action
```

with signature:

```python
ground_database_action: Callable[[DatabaseActionRequest, str], bool],
```

Keep `_database_request_from_payload()` responsible for payload shape and `validate_database_action()` only. In `interpret()`:

```python
if intent == "database_action":
    request = self._database_request_from_payload(payload)
    if request is None:
        return self._failed("database action is invalid", payload)
    if not self.ground_database_action(request, current_input):
        return self._failed("database action is not grounded in current input", payload)
    return FallbackOutcome("database_action", database_request=request)
```

- [ ] **Step 8: Wire production grounding**

In `fallback_adapter.py`:

```python
return QwenFallbackService(
    llm_client,
    validate_search_request=lambda request: parse_structured_search_request(request, repository) is not None,
    validate_database_action=database_service.validate_request,
    ground_database_action=database_service.is_request_grounded,
    stat_catalog=tuple(repository.list_stat_names()),
    alias_catalog=tuple(aliases),
    item_filter_catalog=tuple(filter_labels),
)
```

- [ ] **Step 9: Run focused GREEN tests**

```bash
python -m unittest tests.test_database_question_grounding -v
python -m unittest tests.test_structured_fallback -v
python -m unittest tests.test_search_service -v
```

Expected: all pass; ungrounded valid database entities are rejected before execution.

- [ ] **Step 10: Commit Task 2**

```bash
git add toram_search/help_db.py toram_search/fallback.py toram_search/fallback_adapter.py tests/test_database_question_grounding.py tests/test_structured_fallback.py
git commit -m "fix: ground fallback database actions"
```

---

### Task 3: Full Verification

**Files:**
- No production changes expected.

**Interfaces:**
- Consumes: Tasks 1-2 completed behavior.
- Produces: fresh compile, focused-test, and full-suite evidence.

- [ ] **Step 1: Compile**

```bash
python -m compileall -q toram_data toram_search toram_discord search_items.py discord_bot.py
```

Expected: exit 0.

- [ ] **Step 2: Focused regression suite**

```bash
python -m unittest tests.test_database_question_grounding -v
python -m unittest tests.test_structured_fallback -v
python -m unittest tests.test_search_service -v
```

Expected: zero failures/errors.

- [ ] **Step 3: Full suite**

```bash
python -m unittest discover -s tests -v
```

Expected: zero failures/errors.

- [ ] **Step 4: Requirements review**

Confirm from the final diff and tests:

```text
- `how many bow do you have` routes deterministically and bypasses Qwen.
- `bows` works through conservative singularization; canonical plural item types are resolved before singularization.
- unknown count nouns are not guessed.
- Qwen database stat/item-type actions must be canonically grounded in current input.
- `% stronger against Dark` cannot be accepted for a query that only mentions Bow.
- valid grounded actions remain allowed.
- no-argument metadata actions remain allowed.
- no unrelated Discord/search/ranking/schema/model behavior changed.
```

- [ ] **Step 5: Commit only if verification adds a directly relevant regression**

If no files changed, do not make an empty commit. If a missing bug-specific test was added:

```bash
git add tests/
git commit -m "test: cover database question grounding regressions"
```
