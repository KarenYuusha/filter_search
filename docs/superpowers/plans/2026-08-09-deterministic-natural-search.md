# Deterministic Natural-Language Search Normalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route simple conversational item/stat requests through the existing deterministic parser before invoking Qwen.

**Architecture:** Add conversational-shell normalization to `toram_data/stat_query.py`, the parser already used by `parse_search_query()` before Qwen routing. Both `looks_like_stat_expression()` and `parse_stat_expression()` use the same normalization, so detection and execution cannot disagree. Existing item-filter/stat resolution remains authoritative.

**Tech Stack:** Python 3.10+, `unittest`, existing `toram_data.aliases` and stat-query parser.

## Global Constraints

- Do not add semantic build/role interpretation such as `tank xtal` or `dps gear`.
- Do not add SQL generation or LLM factual authority.
- Preserve existing ambiguity behavior and existing deterministic syntax.
- Qwen remains fallback only when deterministic normalization cannot safely produce a supported search.
- Natural `highest` / `best` / `most` wording may reuse the existing highest-first ordering; do not add `lowest` / ascending semantics here.

---

### Task 1: Add regression tests for conversational parsing

**Files:**
- Modify: `tests/test_direct_structured_intent.py`

**Interfaces:**
- Consumes: `parse_search_query()`, `resolve_expression_interactively()`, and `route_deterministically()`.
- Produces: regression coverage proving natural queries enter the normal deterministic stat-expression path before fallback.

- [x] **Step 1: Add tests for supported forms**

Cover:

```text
can you find armor with hp
show me bow with cr
which bow has the highest critical rate
which bows have critical rate
armor having hp
```

Assert the parsed expression keeps the original raw query, resolves the expected item filter, and deterministically resolves aliases such as `hp -> MaxHP` and `cr -> Critical Rate`.

- [x] **Step 2: Add tests for routing boundaries**

Assert unsupported conversational prose still routes to fallback, while `find tank armor with hp` follows the existing refusal path rather than being reduced to a plain Armor search.

---

### Task 2: Implement shared conversational-shell normalization

**Files:**
- Modify: `toram_data/stat_query.py`
- Test: `tests/test_direct_structured_intent.py`

**Interfaces:**
- Produces: `normalize_natural_stat_query(text: str, available_item_types: set[str]) -> str`

- [x] **Step 1: Recognize only approved conversational forms**

Recognize:

```text
can you find X with Y
find X with Y
show me X with Y
give me X with Y
I want X with Y
which X has Y
which X have Y
X that has Y
X that have Y
X having Y
```

- [x] **Step 2: Require a complete item-filter match**

Use the existing filter candidate machinery. A rewrite is produced only if the entire `X` side resolves to a known item filter. Extra words are never discarded.

A one-word plural may be singularized only when the singular form then resolves completely, e.g. `bows -> bow`.

- [x] **Step 3: Normalize existing highest-first wording**

Inside a supported conversational shell, remove a leading `the highest`, `highest`, `best`, or `most` from the stat side. The existing stat search already sorts highest-first, so this only maps conversational wording onto current behavior. Leave `lowest` untouched.

- [x] **Step 4: Rewrite into existing grammar**

Return `f"{stat_text} {item_phrase}"`; otherwise return the original text unchanged.

- [x] **Step 5: Share normalization between detection and parsing**

Call the helper from both `looks_like_stat_expression()` and `parse_stat_expression()` so the query detected as deterministic is parsed using exactly the same normalized form.

- [x] **Step 6: Run focused verification**

A focused reconstructed harness covered supported wrappers, safe plural handling, highest-stat wording, refusal boundaries, and unchanged direct syntax.

- [ ] **Step 7: Run the full suite when available**

```bash
uv run python -m unittest discover -s tests -v
```

Do not claim this passed unless the command was actually executed successfully.

---

### Task 3: Review routing boundaries and open PR

**Files:**
- Review: `toram_data/stat_query.py`
- Review: `tests/test_direct_structured_intent.py`
- Review: design/plan docs

- [x] **Step 1: Verify direct syntax unchanged**

Focused checks confirmed normalizer leaves `hp armor`, `cr bow`, comparisons, and existing boolean syntax untouched.

- [x] **Step 2: Verify build/refusal boundary**

Focused checks confirmed incomplete item-filter sides such as `tank armor` are not normalized away.

- [ ] **Step 3: Compare branch with `main`**

Confirm branch is not behind and only intended files changed.

- [ ] **Step 4: Open a PR without merging**

Document focused verification and the full-suite limitation. Do not merge without explicit user instruction.
