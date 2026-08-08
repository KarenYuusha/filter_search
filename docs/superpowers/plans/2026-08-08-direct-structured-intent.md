# Direct Structured Intent Execution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Carry Qwen `SearchIntentRequest` objects directly into deterministic search execution without rendering and reparsing query strings.

**Architecture:** `QwenFallbackService` returns typed search requests and validates them through a typed callback. `search_items.py` converts validated requests directly into `ParsedSearch`/`ResolvedStatExpression`, and the confirmation UI selects typed requests rather than rewritten strings.

**Tech Stack:** Python 3.12+, dataclasses, `Decimal`, `unittest`, existing Toram parser/repository modules.

## Global Constraints

- Deterministic routing stays first and remains unchanged for already-supported queries.
- Qwen never emits SQL or database facts.
- No fuzzy stat matching is allowed when converting model-generated structured intents.
- Existing bare-stat semantics remain `>= 1` when a stat is represented inside a resolved expression.
- Existing user confirmation for LLM interpretations is preserved.
- No new build-role semantics or new public search syntax in this change.

---

### Task 1: Typed fallback output

**Files:**
- Modify: `toram_search/fallback.py`
- Test: `tests/test_structured_fallback.py`

**Interfaces:**
- Consumes: Qwen JSON candidates parsed by `_search_candidate_from_payload()`.
- Produces: `FallbackOutcome.search_requests: tuple[SearchIntentRequest, ...]` and constructor callback `validate_search_request: Callable[[SearchIntentRequest], bool]`.

- [ ] **Step 1: Write failing tests**

Add tests asserting `interpret()` returns typed `SearchIntentRequest` objects, preserves multiple candidates, rejects candidates through the typed validator, and keeps the simple-ranking fast path represented as a typed request rather than a string suggestion.

- [ ] **Step 2: Verify RED**

Run: `python -m unittest tests.test_structured_fallback -v`

Expected: failures because `FallbackOutcome` currently exposes `suggestions` and `QwenFallbackService` expects `validate_search_rewrite`.

- [ ] **Step 3: Implement typed fallback output**

Replace the search rewrite callback/strings with typed requests. Remove `_render_search_candidate()` as an execution bridge. Keep any human-readable formatting outside the fallback service.

- [ ] **Step 4: Verify GREEN**

Run: `python -m unittest tests.test_structured_fallback -v`

Expected: structured fallback tests pass.

---

### Task 2: Direct request-to-ParsedSearch conversion

**Files:**
- Modify: `search_items.py`
- Create: `tests/test_direct_structured_intent.py`

**Interfaces:**
- Consumes: `SearchIntentRequest`, repository stat names/item types.
- Produces: `parse_structured_search_request(request, repository) -> ParsedSearch | None`.

- [ ] **Step 1: Write failing conversion tests**

Cover one bare stat, item filters, explicit comparisons, AND, OR, `sort_stat`, ambiguous/unknown stats, and invalid filters. Tests must assert `resolved_expression` directly rather than comparing generated query text.

- [ ] **Step 2: Verify RED**

Run: `python -m unittest tests.test_direct_structured_intent -v`

Expected: failure because `parse_structured_search_request` does not exist.

- [ ] **Step 3: Implement direct conversion**

Resolve filters with complete consumption. Resolve stats with `allow_fuzzy=False`. For one bare stat return `ParsedSearch(intent="stat_search", ...)`. Otherwise construct `ResolvedClause` values directly, defaulting bare clauses to `>= Decimal("1")`, group by `match`, and return `ParsedSearch(intent="stat_expression", resolved_expression=...)`.

- [ ] **Step 4: Make resolved expressions bypass interactive resolution**

At the top of `resolve_expression_interactively`, return `parsed` immediately when `parsed.resolved_expression` is already present.

- [ ] **Step 5: Verify GREEN**

Run: `python -m unittest tests.test_direct_structured_intent -v`

Expected: direct conversion tests pass.

---

### Task 3: Typed confirmation and execution

**Files:**
- Modify: `search_items.py`
- Test: `tests/test_direct_structured_intent.py`

**Interfaces:**
- Consumes: `FallbackOutcome.search_requests`.
- Produces: a selection outcome carrying `SearchIntentRequest | None`, plus presentation-only formatting for confirmation text.

- [ ] **Step 1: Write failing interaction tests**

Test single confirmation, numbered ambiguous selection, and typed-new-query behavior. Assert selected structured requests are returned directly and no generated search string is used as the execution payload.

- [ ] **Step 2: Verify RED**

Run: `python -m unittest tests.test_direct_structured_intent -v`

Expected: failure because `_interactive_query_suggestions` only handles strings.

- [ ] **Step 3: Implement typed selection**

Replace the suggestion-specific fallback UI with a typed-intent selector. Formatting is presentation-only. On selection, convert the request using `parse_structured_search_request()` and execute the resulting `ParsedSearch` in the same main-loop iteration or via a pending parsed-search slot.

- [ ] **Step 4: Wire typed validator into fallback construction**

`_build_fallback_service()` passes `validate_search_request=lambda request: parse_structured_search_request(request, repository) is not None` and no longer routes model candidates back through `validate_rewrite_query()`.

- [ ] **Step 5: Verify GREEN**

Run: `python -m unittest tests.test_direct_structured_intent tests.test_structured_fallback -v`

Expected: all direct-intent and fallback tests pass.

---

### Task 4: Regression verification

**Files:**
- Test: `tests/test_llm.py`
- Test: all repository tests available in the execution workspace.

**Interfaces:**
- Consumes: final branch state.
- Produces: verification evidence for the PR.

- [ ] **Step 1: Run focused suite**

Run: `python -m unittest tests.test_structured_fallback tests.test_direct_structured_intent tests.test_llm -v`

Expected: all focused tests pass.

- [ ] **Step 2: Run full available suite**

Run: `python -m unittest discover -s tests -v`

Expected: zero failures.

- [ ] **Step 3: Review diff**

Confirm no text rewrite is used for Qwen search execution, no SQL path is introduced, and no unrelated parser/database behavior changed.

- [ ] **Step 4: Open PR**

Open a PR from `refactor/direct-structured-intent` to `main` summarizing the removed text round-trip and verification results.
