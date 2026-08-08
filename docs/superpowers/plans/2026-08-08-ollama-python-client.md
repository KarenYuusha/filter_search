# Ollama Python Client Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the manual `urllib` Ollama transport with the official `ollama` Python client while preserving the Qwen fallback contract.

**Architecture:** Keep `OllamaQwenClient` as the boundary used by the fallback service. Construct or inject an `ollama.Client`, delegate chat transport and host handling to that library, then validate the returned message content as the existing JSON-object contract requires.

**Tech Stack:** Python 3.12+, `ollama>=0.6.2`, `unittest`.

## Global Constraints

- Default model remains `qwen3.5:2b`.
- No Ollama/network call occurs merely by importing `toram_search`.
- When no explicit host is supplied, let the Ollama library handle its normal `OLLAMA_HOST` configuration.
- Fallback output must remain one JSON object.

---

### Task 1: Official Ollama transport

**Files:**
- Modify: `toram_search/llm.py`
- Test: `tests/test_llm.py`

**Interfaces:**
- Consumes: `ollama.Client.chat(...)`, `ollama.ResponseError`, `ollama.RequestError`.
- Produces: `OllamaQwenClient.complete(system_prompt: str, user_prompt: str) -> dict[str, object]`.

- [x] **Step 1: Write failing tests**

Add tests proving an explicit host is forwarded to `ollama.Client`, no host is passed when omitted, `chat` receives JSON mode/options/messages, library errors are translated, and malformed content is rejected.

- [x] **Step 2: Verify tests fail against the urllib implementation**

Run: `python -m unittest discover -s tests -v`

Expected: failures because the existing constructor accepts `endpoint`/`urlopen_fn` rather than `host`/injected Ollama clients.

- [x] **Step 3: Implement the minimal library adapter**

Use `ollama.Client`, pass `host` only when explicitly supplied, call `chat` with `format="json"`, existing options and `keep_alive`, translate library exceptions, and parse `response.message.content` as JSON.

- [x] **Step 4: Verify focused tests pass**

Run: `python -m unittest discover -s tests -v`

Expected: 6 tests pass.

- [x] **Step 5: Verify branch contents and PR diff**

Fetch the changed files from `refactor/ollama-python-client`, confirm no `urllib` transport remains, and review the PR diff for unrelated changes.
