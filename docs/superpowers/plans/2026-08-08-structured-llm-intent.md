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

- [ ] **Step 1: Write failing tests** for one valid stat candidate, multiple ambiguity candidates, numeric comparisons, unknown keys, invalid operators, invalid match modes, invalid filters/stats, and SQL rejection.
- [ ] **Step 2: Run the focused tests** and confirm they fail against the current free-form `search_rewrite` contract.
- [ ] **Step 3: Implement the structured dataclasses and payload parser.** Render only validated fields into deterministic grammar (`AND`/`OR`, comparisons, optional item filter) and validate each rendered query with the existing callback.
- [ ] **Step 4: Update the Qwen prompt** so search output uses structured candidates and never emits free-form search strings.
- [ ] **Step 5: Run focused tests** and confirm all structured-fallback cases pass.

### Task 2: Ollama structured-output and timeout boundary

**Files:**
- Modify: `toram_search/llm.py`
- Modify: `tests/test_llm.py`

**Interfaces:**
- `OllamaQwenClient.complete(system_prompt, user_prompt, schema=None) -> dict[str, object]`
- Qwen fallback can supply a JSON schema to `ollama.Client.chat(format=...)`.

- [ ] **Step 1: Add failing tests** that verify an explicit JSON schema is forwarded as `format`, `httpx.ReadTimeout` becomes `LLMUnavailableError`, and malformed model output remains `LLMResponseError`.
- [ ] **Step 2: Run tests** and confirm the timeout/schema cases fail on the current adapter.
- [ ] **Step 3: Implement schema forwarding**, reduce output budget to 128 tokens, and translate `httpx.TimeoutException` / `httpx.NetworkError` into `LLMUnavailableError`.
- [ ] **Step 4: Run the LLM tests** and confirm they pass.

### Task 3: Regression verification

**Files:**
- Test: `tests/test_structured_fallback.py`
- Test: `tests/test_llm.py`

**Interfaces:**
- No new production interface.

- [ ] **Step 1: Run the full available unit-test suite** with `python -m unittest discover -s tests -v`.
- [ ] **Step 2: Verify PR diff** contains no SQL generation path and no direct database access from the LLM layer.
- [ ] **Step 3: Update PR #2 description** to describe both the official Ollama client refactor and structured fallback contract.
