# Rich Skill Chat Presentation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make exact single-skill natural-language explanation queries such as `how does Hard Hit work` use the same rich Discord skill-detail presentation as `skill hard hit`, with the grounded RAG explanation added as a companion `Explanation` embed.

**Architecture:** Keep routing and RAG behavior unchanged. Detect only successful single-skill chat answers at the Discord application boundary, load that skill deterministically by `skill_id`, render it with the existing skill-detail renderer, and append the grounded explanation. Preserve the existing plain chat embed for multi-skill, general-mechanic, refusal, clarification, and not-found responses.

**Tech Stack:** Python 3.12, discord.py, SQLite skill repository, unittest, GitHub Actions.

## Global Constraints

- `skill <name>` behavior and rich detail rendering must remain unchanged.
- Exact single-skill natural explanations must reuse existing detail pages, icon resolution, and navigation controls.
- The RAG explanation must remain grounded output already produced by `SkillChatService`; rendering must not call another LLM.
- Structured single-field queries such as `guardian mp cost` must remain concise and deterministic rather than expanding into full detail.
- Multi-skill comparisons and general mechanic questions must keep their existing presentation.
- If deterministic detail loading fails after a successful chat answer, fall back to the existing plain skill-chat embed instead of failing the Discord reply.

---

### Task 1: Define the rich exact-skill explanation rendering contract

**Files:**
- Modify: `tests/test_discord_database_chat_app.py`
- Modify: `tests/test_discord_skill_search.py`

**Interfaces:**
- Consumes: `SkillChatResult(kind="answer", skill_ids=(...))`, existing `SkillDetailPayload`, existing `build_skill_payload_message()` behavior.
- Produces: regression tests proving exact explanation answers render rich detail plus an `Explanation` embed and non-explanation skill-chat results retain the old path.

- [ ] **Step 1: Write a failing app-level test**

Add a test that supplies `DatabaseChatOutcome(kind="skill", skill_result=SkillChatResult(kind="answer", text="Grounded explanation", skill_ids=("hard-hit-id",)))`, patches a deterministic detail loader to return a `SkillDetailPayload`, and asserts the reply uses `embeds` rather than the single plain `embed` path, with the first embed being the normal skill detail and the final embed titled `Explanation`.

- [ ] **Step 2: Write a failing renderer test**

Add a focused test for the helper that combines the existing detail rendering with a companion explanation while preserving files and navigation view from the detail renderer.

- [ ] **Step 3: Run the focused tests and confirm RED**

Run:

```bash
uv run python -m unittest tests.test_discord_database_chat_app tests.test_discord_skill_search -v
```

Expected: failure because the rich natural-explanation adapter/helper does not exist and the app still calls `build_skill_chat_embed()`.

---

### Task 2: Reuse rich detail rendering for exact natural explanations

**Files:**
- Modify: `toram_discord/skill_ui.py`
- Modify: `toram_discord/app.py`

**Interfaces:**
- Produces: `run_skill_detail_by_id_sync(database_path: Path, skill_id: str) -> SkillDetailPayload`.
- Produces: `build_skill_chat_detail_message(payload: SkillDetailPayload, explanation: str, *, sessions: DiscordSessionManager, key: SessionKey, generation: int) -> SkillRenderedMessage`.

- [ ] **Step 1: Add deterministic detail loading by skill ID**

Implement `run_skill_detail_by_id_sync()` using `SkillRepository`: load the skill with `get_skill(skill_id)`, load its tree with `get_tree(skill.tree_id)`, and return `SkillDetailPayload(skill, tree)`.

- [ ] **Step 2: Add a rich chat-detail message helper**

Implement `build_skill_chat_detail_message()` by constructing the same detail pages/view used by `build_skill_payload_message()` for `SkillDetailPayload`, rendering page 0 with the existing `build_skill_detail_message()`, and appending a second `discord.Embed(title="Explanation", description=truncate_discord_text(explanation, 4096))`. Preserve any detail icon file and navigation view.

- [ ] **Step 3: Route only exact answer-shaped skill chat responses to the rich helper**

In `process_tagged_query()`, when `chat_outcome.kind == "skill"`, use the rich path only when `skill_result.kind == "answer"` and `len(skill_result.skill_ids) == 1`. Load the detail by that ID in `asyncio.to_thread()`. On detail-load failure, log at DEBUG and use `build_skill_chat_embed()` as the fallback. All other skill chat result kinds keep the existing plain embed path.

- [ ] **Step 4: Run focused tests and confirm GREEN**

Run:

```bash
uv run python -m unittest tests.test_discord_database_chat_app tests.test_discord_skill_search -v
```

Expected: all tests pass.

---

### Task 3: Regression verification

**Files:**
- No behavior changes expected.

**Interfaces:**
- Confirms the change does not alter item routing, explicit skill commands, comparison rendering, or skill-detail navigation.

- [ ] **Step 1: Run database-chat focused suites**

```bash
uv run python -m unittest \
  tests.test_discord_database_chat \
  tests.test_discord_database_chat_app \
  tests.test_database_chat_user_regressions \
  tests.test_skill_chat_service -v
```

- [ ] **Step 2: Run high-risk Discord skill suites**

```bash
uv run python -m unittest \
  tests.test_discord_skill_search \
  tests.test_discord_skill_detail_pages \
  tests.test_skill_tree_listing \
  tests.test_discord_skill_review_regressions -v
```

- [ ] **Step 3: Run the full test suite and static sanity**

```bash
uv run python -m unittest discover -s tests -p "test_*.py" -v
git diff --check origin/main...HEAD
uv run python -m py_compile toram_discord/app.py toram_discord/skill_ui.py
```

Expected: all tests and static checks pass.
