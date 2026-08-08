# Structured LLM Search Intent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Qwen translate natural-language Toram searches into a strict structured intent that is validated and rendered into the existing deterministic search grammar, without generating SQL or database answers.

**Architecture:** Keep deterministic routing first. Only fallback queries reach Qwen. Qwen returns one to three structured search candidates (or an allowed database/help/refusal action); `QwenFallbackService` validates every field, renders candidates into canonical search strings, and passes those strings through the existing deterministic validator before exposing them to the UI. The existing suggestion screen remains the confirmation boundary for ambiguous interpretations.

**Tech Stack:** Python 3.12, dataclasses, Ollama Python client, httpx, unittest, existing deterministic stat parser and SQLite repository.

## Global Constraints

- Keep `qwen3.5:2b` as the default local model.
- Never allow Qwen to generate or execute SQL.
- Never let Qwen answer Toram facts from general knowledge; database facts come from deterministic database actions.
- Preserve deterministic search as the fast path; Qwen is fallback only.
- Current supported search scope remains item/stat search, database metadata/count questions, and search help; tank/DPS/build semantics remain unsupported.
- Every Qwen-produced search must pass deterministic validation before it can be used.

---

### Task 1: Strict structured search contract

**Files:**
- Modify: `toram_search/fallback.py`
- Create: `tests/test_structured_fallback.py`

**Interfaces:**
- Produces: `SearchStatIntent`, `SearchIntentRequest`, and strict parsing of `{"intent":"search","candidates":[...]}`.
- Preserves: `FallbackOutcome("suggestions")` so the existing terminal UI keeps working.

- [x] **Step 1: Write failing tests** for valid structured candidates, ambiguity, comparisons, invalid fields/operators, deterministic validation, and SQL rejection.
- [x] **Step 2: Run the focused tests** and confirm the previous free-form contract fails them.
- [x] **Step 3: Implement the structured dataclasses and payload parser.** Render validated fields into deterministic grammar and validate each rendered query with the existing callback.
- [x] **Step 4: Update the Qwen prompt** so search output uses structured candidates and never emits free-form search strings.
- [x] **Step 5: Add a deterministic `best`/`highest` rewrite** so simple ranking queries can bypass Qwen when the existing parser already understands the remainder.

### Task 2: Ollama structured-output and timeout boundary

**Files:**
- Modify: `toram_search/llm.py`
- Modify: `tests/test_llm.py`

**Interfaces:**
- `OllamaQwenClient.complete(system_prompt, user_prompt, schema=None) -> dict[str, object]`
- Qwen fallback can supply a JSON schema to `ollama.Client.chat(format=...)`.

- [x] **Step 1: Add failing tests** for schema forwarding and `httpx.ReadTimeout` handling.
- [x] **Step 2: Implement schema forwarding**, disable model thinking for the router, reduce output budget to 128 tokens, increase timeout to 30 seconds, and translate `httpx.TransportError` into `LLMUnavailableError`.
- [x] **Step 3: Preserve malformed-output handling** as `LLMResponseError`.

### Task 3: Regression verification

**Files:**
- Test: `tests/test_structured_fallback.py`
- Test: `tests/test_llm.py`

- [x] **Step 1: Run the focused structured-fallback and LLM tests** in the isolated verification workspace.
- [x] **Step 2: Verify the LLM layer has no SQL generation/execution path.**
- [ ] **Step 3: Verify the clean branch diff against current `main` and open the PR.**
