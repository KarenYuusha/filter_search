# Skill Tree Listing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `skill tree <tree name>` so Discord users can list every skill in a canonical tree, with deterministic shorthand/fuzzy tree resolution and confirmation for typos.

**Architecture:** Keep the feature inside the existing skill path. `SkillSearchService` recognizes token-bounded `tree`, resolves against the repository tree catalog with exact/shorthand/RapidFuzz matching, and returns explicit tree payloads. `toram_discord.skill_ui` renders tree help/results/confirmation/choices using the existing session-bound pagination and skill detail views; no app routing, database schema, semantic search, Qwen, or item/stat logic changes are required.

**Tech Stack:** Python 3.12, `unittest`, `rapidfuzz.fuzz.WRatio`, `discord.py`, SQLite-backed `SkillRepository`.

## Global Constraints

- Primary syntax is `skill tree <tree name>`.
- Existing ordinary `skill <words>` semantics must remain unchanged.
- `tree` matching is case-insensitive and token-bounded; `treehouse` is not a tree command.
- Exact canonical tree names execute immediately.
- Exact unique shorthand is formed by removing a trailing `Skills` token and executes immediately.
- Fuzzy matching uses RapidFuzz `WRatio`.
- Minimum actionable fuzzy score is `80`.
- Candidates within `10` points of the best actionable score are ambiguous and must be shown as choices.
- A fuzzy typo never executes silently; a single actionable candidate requires confirmation.
- Low-confidence input returns not-found guidance with up to three closest canonical tree names.
- `skill tree` with no tree name returns tree help and available canonical tree names.
- Tree resolution/listing must not initialize or query semantic search and must never call Qwen.
- Tree results preserve `SkillRepository.list_skills_in_tree()` source order and are never hybrid-ranked.
- Tree result pagination uses existing `PAGE_SIZE` (5).
- Selecting a listed skill opens the existing skill detail pages; Back returns to the same tree page.
- Existing owner/generation protection remains active.
- Item/stat behavior and item failed-query context are unchanged.

---

## File Structure

- Modify `toram_skill_search/models.py` — add explicit tree-list payload dataclasses and extend `SkillPayload`.
- Modify `toram_skill_search/service.py` — detect `tree`, resolve tree names deterministically, and build tree payloads without semantic search.
- Modify `toram_discord/skill_ui.py` — render tree results/help/errors and add session-bound confirmation/choice interactions while reusing existing detail navigation.
- Create `tests/test_skill_tree_listing.py` — focused service and Discord behavior tests for the new feature.
- Modify `tests/test_discord_skill_search.py` only if the general skill-help snapshot/assertions require the new `skill tree shield` example.
- No changes to `toram_discord/app.py`, `toram_skills/repository.py`, schema/build scripts, raw skill data, or item-search modules.

---

### Task 1: Add tree payload contracts and deterministic service resolution

**Files:**
- Modify: `toram_skill_search/models.py`
- Modify: `toram_skill_search/service.py`
- Create/Test: `tests/test_skill_tree_listing.py`

**Interfaces:**
- Consumes: `SkillRepository.list_tree_names()`, `SkillRepository.resolve_tree_name(name)`, `SkillRepository.list_skills_in_tree(tree_id)`, existing `_result_item()`.
- Produces:
  - `SkillTreeResultsPayload(tree: SkillTreeDraft, results: tuple[SkillResultItem, ...])`
  - `SkillTreeConfirmationPayload(query: str, suggested_tree: SkillTreeDraft)`
  - `SkillTreeChoicesPayload(query: str, candidates: tuple[SkillTreeDraft, ...])`
  - `SkillTreeNotFoundPayload(query: str, suggestions: tuple[str, ...])`
  - `SkillTreeHelpPayload(tree_names: tuple[str, ...])`
  - `SkillSearchService.handle_tree_request(query: str) -> SkillPayload`

- [ ] **Step 1: Write failing payload/service tests**

Create `tests/test_skill_tree_listing.py` with focused fakes. Use real model objects only where payload rendering needs them; service-resolution tests may use `Mock` trees/skills as long as they expose the attributes accessed by `_result_item()`.

```python
from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from toram_skill_search.models import (
    SkillTreeChoicesPayload,
    SkillTreeConfirmationPayload,
    SkillTreeHelpPayload,
    SkillTreeNotFoundPayload,
    SkillTreeResultsPayload,
)
from toram_skill_search.service import SkillSearchService


class FakeTreeRepository:
    def __init__(self):
        self.trees = {}
        self.skills_by_tree = {}

    def add_tree(self, name, *, skills=()):
        tree = Mock()
        tree.id = name.casefold().replace(" ", "-")
        tree.name = name
        self.trees[name.casefold()] = tree
        self.skills_by_tree[tree.id] = tuple(skills)
        return tree

    def list_tree_names(self):
        return sorted(tree.name for tree in self.trees.values())

    def resolve_tree_name(self, name):
        tree = self.trees.get(" ".join(name.casefold().split()))
        return () if tree is None else (tree,)

    def list_skills_in_tree(self, tree_id):
        return self.skills_by_tree[tree_id]

    def get_tree(self, tree_id):
        for tree in self.trees.values():
            if tree.id == tree_id:
                return tree
        raise KeyError(tree_id)

    def resolve_skill_name(self, name):
        return ()


class SkillTreeServiceTests(unittest.TestCase):
    def setUp(self):
        self.repo = FakeTreeRepository()
        self.shield = self.repo.add_tree("Shield Skills")
        self.magic = self.repo.add_tree("Magic Skills")
        self.magic_warrior = self.repo.add_tree("Magic Warrior Skills")
        self.service = SkillSearchService(self.repo, semantic_runtime=None)

    def test_bare_tree_returns_tree_help(self):
        payload = self.service.handle("tree")
        self.assertIsInstance(payload, SkillTreeHelpPayload)
        self.assertIn("Shield Skills", payload.tree_names)

    def test_exact_canonical_tree_executes(self):
        payload = self.service.handle("tree SHIELD SKILLS")
        self.assertIsInstance(payload, SkillTreeResultsPayload)
        self.assertEqual(payload.tree.name, "Shield Skills")

    def test_unique_shorthand_executes(self):
        payload = self.service.handle("tree shield")
        self.assertIsInstance(payload, SkillTreeResultsPayload)
        self.assertEqual(payload.tree.name, "Shield Skills")

    def test_typo_requires_confirmation(self):
        payload = self.service.handle("tree sheild")
        self.assertIsInstance(payload, SkillTreeConfirmationPayload)
        self.assertEqual(payload.suggested_tree.name, "Shield Skills")

    def test_ambiguous_fuzzy_input_returns_choices(self):
        payload = self.service.handle("tree magic skill")
        self.assertIsInstance(payload, SkillTreeChoicesPayload)
        self.assertGreaterEqual(len(payload.candidates), 2)

    def test_low_confidence_junk_returns_not_found(self):
        payload = self.service.handle("tree xyzabc")
        self.assertIsInstance(payload, SkillTreeNotFoundPayload)
        self.assertLessEqual(len(payload.suggestions), 3)

    def test_treehouse_stays_on_normal_skill_search_path(self):
        with patch.object(self.service, "_search_free_text", return_value=Mock()) as search:
            result = self.service.handle("treehouse")
        search.assert_called_once_with("treehouse")
        self.assertIs(result, search.return_value)

    def test_tree_path_never_requests_semantic_runtime(self):
        runtime = Mock()
        service = SkillSearchService(self.repo, semantic_runtime=runtime)
        service.handle("tree shield")
        runtime.get_index.assert_not_called()
```

Also add a source-order/all-results test by creating more than 20 fake skills whose `tree_id` matches Shield Skills and asserting all are returned in insertion order.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
python -m unittest tests.test_skill_tree_listing -v
```

Expected: import failures for the new payload classes and/or behavior failures because `SkillSearchService.handle()` does not recognize `tree` yet.

- [ ] **Step 3: Add the payload dataclasses**

In `toram_skill_search/models.py`, add:

```python
@dataclass(frozen=True)
class SkillTreeResultsPayload:
    tree: SkillTreeDraft
    results: tuple[SkillResultItem, ...]


@dataclass(frozen=True)
class SkillTreeConfirmationPayload:
    query: str
    suggested_tree: SkillTreeDraft


@dataclass(frozen=True)
class SkillTreeChoicesPayload:
    query: str
    candidates: tuple[SkillTreeDraft, ...]


@dataclass(frozen=True)
class SkillTreeNotFoundPayload:
    query: str
    suggestions: tuple[str, ...]


@dataclass(frozen=True)
class SkillTreeHelpPayload:
    tree_names: tuple[str, ...]
```

Extend `SkillPayload` and `__all__` with all five types.

- [ ] **Step 4: Implement deterministic tree resolution**

In `toram_skill_search/service.py`, import `rapidfuzz.fuzz` and the new payloads. Add constants:

```python
TREE_PREFIX = "tree"
TREE_FUZZY_MIN_SCORE = 80.0
TREE_FUZZY_AMBIGUITY_MARGIN = 10.0
```

Add helpers with exact signatures:

```python
def _tree_shorthand(name: str) -> str:
    normalized = normalize_skill_name(name)
    return normalized[:-7].strip() if normalized.endswith(" skills") else normalized


def _is_tree_request(cleaned: str) -> tuple[bool, str]:
    parts = cleaned.split(maxsplit=1)
    if not parts or parts[0].casefold() != TREE_PREFIX:
        return False, ""
    return True, "" if len(parts) == 1 else parts[1].strip()
```

Inside `SkillSearchService`, implement:

```python
def _resolve_tree_exact(self, query: str):
    exact = self.repository.resolve_tree_name(query)
    if len(exact) == 1:
        return exact[0]

    normalized = normalize_skill_name(query)
    shorthand_matches = []
    for name in self.repository.list_tree_names():
        if _tree_shorthand(name) == normalized:
            shorthand_matches.extend(self.repository.resolve_tree_name(name))
    return shorthand_matches[0] if len(shorthand_matches) == 1 else None


def _tree_results(self, tree):
    return SkillTreeResultsPayload(
        tree=tree,
        results=tuple(
            _result_item(self.repository, skill)
            for skill in self.repository.list_skills_in_tree(tree.id)
        ),
    )
```

Then implement `handle_tree_request(query)`:

```python
def handle_tree_request(self, query: str) -> SkillPayload:
    cleaned = " ".join(query.split())
    names = tuple(self.repository.list_tree_names())
    if not cleaned:
        return SkillTreeHelpPayload(names)

    exact = self._resolve_tree_exact(cleaned)
    if exact is not None:
        return self._tree_results(exact)

    scored = []
    normalized_query = normalize_skill_name(cleaned)
    for name in names:
        canonical = normalize_skill_name(name)
        score = max(
            fuzz.WRatio(normalized_query, canonical),
            fuzz.WRatio(normalized_query, _tree_shorthand(name)),
        )
        scored.append((float(score), name))
    scored.sort(key=lambda item: (-item[0], item[1].casefold(), item[1]))

    suggestions = tuple(name for _, name in scored[:3])
    if not scored or scored[0][0] < TREE_FUZZY_MIN_SCORE:
        return SkillTreeNotFoundPayload(cleaned, suggestions)

    best_score = scored[0][0]
    actionable_names = [
        name
        for score, name in scored
        if score >= TREE_FUZZY_MIN_SCORE
        and score >= best_score - TREE_FUZZY_AMBIGUITY_MARGIN
    ]
    candidates = tuple(
        self.repository.resolve_tree_name(name)[0]
        for name in actionable_names
    )
    if len(candidates) == 1:
        return SkillTreeConfirmationPayload(cleaned, candidates[0])
    return SkillTreeChoicesPayload(cleaned, candidates)
```

At the beginning of `handle()`, after whitespace cleanup and before exact skill lookup:

```python
is_tree_request, tree_query = _is_tree_request(cleaned)
if is_tree_request:
    return self.handle_tree_request(tree_query)
```

Do not call `_search_free_text()` anywhere in `handle_tree_request()`.

- [ ] **Step 5: Run service tests and verify GREEN**

Run:

```bash
python -m unittest tests.test_skill_tree_listing.SkillTreeServiceTests -v
```

Expected: PASS.

- [ ] **Step 6: Commit the service layer**

```bash
git add toram_skill_search/models.py toram_skill_search/service.py tests/test_skill_tree_listing.py
git commit -m "feat: add deterministic skill tree resolution"
```

---

### Task 2: Render paginated tree results and reuse skill detail navigation

**Files:**
- Modify: `toram_discord/skill_ui.py`
- Modify/Test: `tests/test_skill_tree_listing.py`

**Interfaces:**
- Consumes: `SkillTreeResultsPayload`, existing `PAGE_SIZE`, `SkillDetailView`, `SkillResultItem`, session page state.
- Produces:
  - `build_skill_tree_results_embed(payload: SkillTreeResultsPayload, page: int) -> discord.Embed`
  - `SkillTreeResultsView(SessionBoundView)`

- [ ] **Step 1: Add failing rendering tests**

Add tests that build a `SkillTreeResultsPayload` with at least six results and assert:

```python
embed = build_skill_tree_results_embed(payload, 0)
self.assertEqual(embed.title, "Shield Skills")
self.assertIn("6 skills", embed.description)
self.assertIn("Showing 1–5 of 6", embed.description)
self.assertNotIn("Closest matches first", embed.description)
self.assertNotIn("Shield Skills •", embed.description)
```

Also assert page 1 contains only the sixth result, no literal `None`, and description length is `<= 4096`.

- [ ] **Step 2: Run the rendering tests and verify RED**

```bash
python -m unittest tests.test_skill_tree_listing -v
```

Expected: FAIL because the tree embed/view does not exist.

- [ ] **Step 3: Add tree-specific compact metadata**

In `toram_discord/skill_ui.py`, keep `_compact_skill_metadata()` unchanged for ordinary search. Add:

```python
def _compact_tree_skill_metadata(result: SkillResultItem) -> str:
    skill = result.skill
    parts = []
    if skill.tier is not None:
        parts.append(f"Tier {skill.tier}")
    if skill.required_level is not None:
        parts.append(f"Required Lv {skill.required_level}")
    if skill.mp_cost_value is not None:
        parts.append(f"MP {skill.mp_cost_value}")
    elif skill.mp_cost_text and skill.mp_cost_text.strip():
        raw = skill.mp_cost_text.strip()
        parts.append(raw if raw.casefold().startswith("mp ") else f"MP {raw}")
    display_type = skill.damage_type or skill.skill_type
    if display_type and display_type.strip():
        parts.append(display_type.strip())
    return " • ".join(parts)
```

- [ ] **Step 4: Implement the tree results embed**

```python
def build_skill_tree_results_embed(
    payload: SkillTreeResultsPayload,
    page: int,
) -> discord.Embed:
    total = len(payload.results)
    max_page = max((total - 1) // PAGE_SIZE, 0)
    page = min(max(page, 0), max_page)
    start = page * PAGE_SIZE
    end = min(start + PAGE_SIZE, total)

    lines = [f"{total} skills", f"Showing {start + 1}–{end} of {total}", ""]
    for index in range(start, end):
        result = payload.results[index]
        lines.append(f"{index + 1}. **{result.skill.name}**")
        metadata = _compact_tree_skill_metadata(result)
        if metadata:
            lines.append(f"   {metadata}")
        preview = _compact_skill_preview(result.snippet)
        if preview:
            lines.append(f"   {preview}")
        lines.append("")

    return discord.Embed(
        title=truncate_discord_text(payload.tree.name, 256),
        description=truncate_discord_text("\n".join(lines).rstrip(), 4096),
    )
```

For an empty canonical tree, render `0 skills` and avoid a misleading `Showing 1–0 of 0`; use `No skills are currently stored for this tree.` instead.

- [ ] **Step 5: Implement `SkillTreeResultsView` by mirroring result navigation**

Create a separate view rather than overloading `SkillResultsView` semantics. It should:

```python
class SkillTreeResultsView(SessionBoundView):
    ...
```

Requirements:

- Same owner/generation constructor fields as `SkillResultsView`.
- Five select options for the current page.
- Previous/Next mutate `session.page` and rerender with `build_skill_tree_results_embed()`.
- `_select_skill()` creates `SkillDetailPayload`, builds existing detail pages, sets `session.selected_index`, and opens `SkillDetailView`.
- Pass the tree payload into `SkillDetailView` through a generalized back-target field so Back can return to a tree listing.

Generalize `SkillDetailView` from:

```python
results_payload: SkillResultsPayload | None
```

to:

```python
results_payload: SkillResultsPayload | SkillTreeResultsPayload | None
```

In `_back()`, branch only on payload type:

```python
if isinstance(self.results_payload, SkillTreeResultsPayload):
    embed = build_skill_tree_results_embed(self.results_payload, session.page)
    view = SkillTreeResultsView(...)
else:
    embed = build_skill_results_embed(self.results_payload, session.page)
    view = SkillResultsView(...)
```

Do not change detail page construction itself.

- [ ] **Step 6: Run focused tree UI and existing skill detail tests**

```bash
python -m unittest tests.test_skill_tree_listing -v
python -m unittest tests.test_discord_skill_detail_pages -v
python -m unittest tests.test_discord_skill_review_regressions -v
```

Expected: PASS.

- [ ] **Step 7: Commit paginated tree results**

```bash
git add toram_discord/skill_ui.py tests/test_skill_tree_listing.py
git commit -m "feat: add paginated skill tree results"
```

---

### Task 3: Add fuzzy confirmation, ambiguous choices, not-found, and tree help UI

**Files:**
- Modify: `toram_discord/skill_ui.py`
- Modify/Test: `tests/test_skill_tree_listing.py`
- Possibly modify: `tests/test_discord_skill_search.py`

**Interfaces:**
- Consumes: `SkillTreeConfirmationPayload`, `SkillTreeChoicesPayload`, `SkillTreeNotFoundPayload`, `SkillTreeHelpPayload`.
- Produces:
  - `SkillTreeConfirmationView(SessionBoundView)`
  - `SkillTreeChoicesView(SessionBoundView)`
  - helper embed builders for help/not-found/confirmation/choices.

- [ ] **Step 1: Add failing help/error/interaction tests**

Cover these exact behaviors:

```python
# bare tree
payload = SkillTreeHelpPayload(("Magic Skills", "Shield Skills"))
embed, view = build_skill_payload_message(...)
self.assertIn("skill tree <tree name>", embed.description)
self.assertIn("Magic Skills", embed.description)
self.assertIsNone(view)

# typo confirmation
payload = SkillTreeConfirmationPayload("sheild", shield_tree)
embed, view = build_skill_payload_message(...)
self.assertIn("Did you mean", embed.description)
self.assertIsInstance(view, SkillTreeConfirmationView)

# ambiguous choices
payload = SkillTreeChoicesPayload("magic skill", (magic_tree, magic_warrior_tree))
embed, view = build_skill_payload_message(...)
self.assertIn("Which skill tree", embed.description)
self.assertIsInstance(view, SkillTreeChoicesView)

# not found
payload = SkillTreeNotFoundPayload("xyzabc", ("Magic Skills", "Shield Skills"))
embed, view = build_skill_payload_message(...)
self.assertIn("couldn't find", embed.description.casefold())
self.assertIsNone(view)
```

Interaction tests should invoke the view handlers with the existing fake interaction pattern from Discord skill tests and verify:

- Show Skills opens the already suggested canonical tree without re-running fuzzy resolution.
- Cancel edits the message to a non-interactive cancellation embed/view `None`.
- Candidate select opens the selected canonical tree.
- Wrong owner/stale generation remains rejected by inherited `SessionBoundView` behavior.

- [ ] **Step 2: Run focused tests and verify RED**

```bash
python -m unittest tests.test_skill_tree_listing -v
```

Expected: FAIL because payload routing/views are missing.

- [ ] **Step 3: Add a canonical tree-to-results helper for interactions**

Avoid calling `run_skill_search()` again from button callbacks. Add a small sync helper in `skill_ui.py` that opens `SkillRepository` and lists a trusted tree ID directly:

```python
def run_skill_tree_by_id_sync(
    database_path: Path,
    tree_id: str,
    *,
    repository_factory=SkillRepository,
) -> SkillTreeResultsPayload:
    with repository_factory(database_path.resolve()) as repository:
        tree = repository.get_tree(tree_id)
        return SkillTreeResultsPayload(
            tree=tree,
            results=tuple(
                SkillResultItem(
                    skill=skill,
                    tree=tree,
                    snippet=_snippet_for_ui(skill),
                )
                for skill in repository.list_skills_in_tree(tree.id)
            ),
        )
```

Prefer moving/reusing the existing service `_snippet()` as a public helper if importing a private service function would otherwise be necessary. A clean option is to add `skill_result_item(repository, skill)` as a public/internal module helper in `toram_skill_search.service` and reuse it from both service and Discord callbacks.

- [ ] **Step 4: Implement tree help/not-found embeds**

Tree help copy:

```text
Use `skill tree <tree name>`.
Example: `skill tree shield`

Available skill trees:
• ...
```

Use `truncate_discord_text(..., 4096)`. If the canonical tree catalog exceeds the limit, split it across embed fields where each field is <= 1024 characters; do not silently omit tree names.

Not-found copy:

```text
I couldn't find a skill tree matching "xyzabc".

Closest skill trees:
• Magic Skills
• Shield Skills
```

No interactive view for low-confidence not-found.

- [ ] **Step 5: Implement confirmation view**

`SkillTreeConfirmationView` stores the trusted `suggested_tree.id` and `database_path` passed from `build_skill_payload_message()`.

Buttons:

```python
ActionButton(label="Show skills", style=discord.ButtonStyle.primary, ...)
ActionButton(label="Cancel", style=discord.ButtonStyle.secondary, ...)
```

`Show skills`:

1. Load by trusted tree ID.
2. Reset `session.page = 0` and `session.selected_index = None`.
3. Edit message to tree results + `SkillTreeResultsView`.

`Cancel`:

```python
await interaction.response.edit_message(
    embed=discord.Embed(
        title="Skill tree search cancelled",
        description="No skill tree was opened.",
    ),
    view=None,
)
```

No fuzzy resolution is rerun.

- [ ] **Step 6: Implement ambiguous choice view**

Use one `ActionSelect` with canonical names as labels and candidate indexes as values. Keep trusted `SkillTreeDraft` candidates on the view instance.

On selection:

1. Validate index.
2. Load the selected tree by its trusted ID.
3. Reset result-page state to zero.
4. Edit to `build_skill_tree_results_embed()` + `SkillTreeResultsView`.

- [ ] **Step 7: Route all new payloads in `build_skill_payload_message()`**

Before the existing detail/results fallthrough, handle each new payload explicitly. Pass `database_path` into `build_skill_payload_message()` so confirmation/choice callbacks can load the canonical tree directly.

Update the only caller in `toram_discord/app.py` to pass:

```python
database_path=config.skill_database_path
```

This is the only intended `app.py` change; command routing remains untouched.

- [ ] **Step 8: Update general skill help**

Add one example:

```text
skill tree shield
```

Keep ordinary skill search examples and wording primary. Update `tests/test_discord_skill_search.py` only if its current help assertions require it.

- [ ] **Step 9: Run focused Discord tests**

```bash
python -m unittest tests.test_skill_tree_listing -v
python -m unittest tests.test_discord_skill_search -v
python -m unittest tests.test_discord_skill_detail_pages -v
python -m unittest tests.test_discord_skill_review_regressions -v
```

Expected: PASS.

- [ ] **Step 10: Commit interaction/help behavior**

```bash
git add toram_discord/skill_ui.py toram_discord/app.py tests/test_skill_tree_listing.py tests/test_discord_skill_search.py
git commit -m "feat: add skill tree confirmation and choices"
```

---

### Task 4: Validate against the real skill database and lock regression behavior

**Files:**
- Modify/Test: `tests/test_skill_tree_listing.py`
- No production changes unless a real-data failure identifies a defect.

**Interfaces:**
- Consumes: checked-in `coryn_data/database/skills.sqlite`, `run_skill_search()`.
- Produces: real-corpus acceptance coverage for canonical/shorthand/typo/list-all behavior.

- [ ] **Step 1: Add real-database acceptance tests**

Use the repository's checked-in database path and verify at least:

```python
payload = run_skill_search(DB_PATH, "tree shield", semantic_runtime=None)
self.assertIsInstance(payload, SkillTreeResultsPayload)
self.assertEqual(payload.tree.name, "Shield Skills")
self.assertEqual(
    [result.skill.id for result in payload.results],
    [skill.id for skill in repository.list_skills_in_tree(payload.tree.id)],
)

payload = run_skill_search(DB_PATH, "tree sheild", semantic_runtime=None)
self.assertIsInstance(payload, SkillTreeConfirmationPayload)
self.assertEqual(payload.suggested_tree.name, "Shield Skills")
```

Also verify `tree magic` resolves exact shorthand to `Magic Skills`, while a deliberately ambiguous fuzzy phrase covered by the unit fixture returns choices.

- [ ] **Step 2: Verify no semantic runtime is needed with the real database**

Run exact/shorthand/typo tree cases with `semantic_runtime=None`. They must all return valid tree payloads without embedding-index access.

- [ ] **Step 3: Run all skill-focused tests**

```bash
python -m unittest tests.test_skill_tree_listing -v
python -m unittest tests.test_discord_skill_search -v
python -m unittest tests.test_discord_skill_detail_pages -v
python -m unittest tests.test_discord_skill_review_regressions -v
```

Expected: PASS.

- [ ] **Step 4: Commit real-corpus acceptance coverage**

```bash
git add tests/test_skill_tree_listing.py
git commit -m "test: cover real skill tree listing"
```

---

### Task 5: Full verification and integration readiness

**Files:**
- No planned source changes.
- Review all commits against `docs/superpowers/specs/2026-08-13-skill-tree-listing-design.md`.

**Interfaces:**
- Produces: a verified feature branch ready for review/integration.

- [ ] **Step 1: Run the complete repository suite**

```bash
python -m unittest discover -s tests -p 'test_*.py' -v
```

Expected: all tests PASS.

- [ ] **Step 2: Run representative command smoke checks**

Execute a small Python script against `coryn_data/database/skills.sqlite` with `semantic_runtime=None` for:

```text
tree Shield Skills
tree shield
tree sheild
tree magic
tree xyzabc
```

Assert the payload classes are respectively:

```text
SkillTreeResultsPayload
SkillTreeResultsPayload
SkillTreeConfirmationPayload
SkillTreeResultsPayload
SkillTreeNotFoundPayload
```

Also assert a normal query such as `shield bash` still produces the existing skill detail/search payload and `treehouse` does not enter tree handling.

- [ ] **Step 3: Inspect the final diff for scope creep**

Expected production changes are limited to:

```text
toram_skill_search/models.py
toram_skill_search/service.py
toram_discord/skill_ui.py
toram_discord/app.py
```

Expected test/doc changes are limited to:

```text
tests/test_skill_tree_listing.py
tests/test_discord_skill_search.py   # only if help assertion changes
docs/superpowers/specs/2026-08-13-skill-tree-listing-design.md
docs/superpowers/plans/2026-08-13-skill-tree-listing.md
```

There must be no raw skill, database, embedding, item-search, or schema changes.

- [ ] **Step 4: Verify git status is clean after commits**

```bash
git status --short
```

Expected: no output.

- [ ] **Step 5: Prepare integration summary**

Report:

- exact canonical and shorthand behavior,
- fuzzy confirmation/ambiguity/not-found behavior,
- five-per-page tree listing + detail/back navigation,
- no semantic/Qwen usage on tree path,
- focused test count/result,
- full repository suite result,
- exact branch/head SHA.

Do not merge until the verified branch state and test results are known.
