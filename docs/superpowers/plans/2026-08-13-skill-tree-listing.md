# Skill Tree Listing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `skill tree <tree name>` so Discord users can list every skill in a canonical tree, with deterministic shorthand/fuzzy resolution and explicit confirmation for typos.

**Architecture:** Keep tree listing inside the existing skill path. `SkillSearchService` recognizes token-bounded `tree`, resolves only against the repository tree catalog using exact/shorthand/RapidFuzz matching, and returns explicit tree payloads. Discord renders those payloads with existing session-bound pagination/detail navigation; only confirmation/choice callbacks need the skill database path so they can load an already-trusted tree ID without re-running fuzzy resolution.

**Tech Stack:** Python 3.12, `unittest`, `rapidfuzz.fuzz.WRatio`, `discord.py`, SQLite-backed `SkillRepository`.

## Global Constraints

- Primary syntax: `skill tree <tree name>`.
- Ordinary `skill <words>` behavior stays unchanged.
- `tree` is case-insensitive and token-bounded; `treehouse` remains ordinary skill search.
- Exact canonical tree name executes immediately.
- Exact unique shorthand is the canonical name with one trailing `Skills` token removed; it executes immediately.
- Fuzzy matching uses RapidFuzz `WRatio`.
- Minimum actionable fuzzy score: `80`.
- Any actionable candidate within `10` points of the best actionable score is ambiguous.
- One fuzzy candidate requires confirmation; fuzzy input never executes silently.
- Multiple plausible candidates show choices; low-confidence input shows not-found guidance with up to three closest names.
- Bare `skill tree` shows usage plus the canonical tree catalog.
- Tree resolution/listing never uses semantic retrieval or Qwen.
- Tree results preserve `SkillRepository.list_skills_in_tree()` source order and include every skill, not the hybrid-search 20-result cap.
- Tree pages use existing `PAGE_SIZE` = 5.
- Selecting a tree result opens existing detail pages; Back returns to the same tree page.
- Owner/generation protection remains active.
- Item/stat behavior and item failed-query context remain unchanged.

---

## File Structure

- Modify `toram_skill_search/models.py` — tree payload dataclasses and `SkillPayload` union.
- Modify `toram_skill_search/service.py` — deterministic tree resolution and trusted tree-ID listing helper.
- Modify `toram_skill_search/__init__.py` — export the trusted tree-ID helper used by Discord interactions.
- Modify `toram_discord/skill_ui.py` — tree embeds, pagination, confirmation/choice views, Back routing.
- Modify `toram_discord/app.py` — pass `config.skill_database_path` into the existing skill payload renderer; no command-routing change.
- Create `tests/test_skill_tree_listing.py` — service, real-database, rendering, and interaction coverage.
- Modify `tests/test_discord_skill_search.py` only if its help assertions need the new tree example.
- Do not modify repository schema, raw skills, generated skill DB/embeddings, semantic retrieval, Qwen logic, or item/stat modules.

---

### Task 1: Add tree payloads and deterministic service resolution

**Files:**
- Modify: `toram_skill_search/models.py`
- Modify: `toram_skill_search/service.py`
- Modify: `toram_skill_search/__init__.py`
- Create/Test: `tests/test_skill_tree_listing.py`

**Interfaces:**
- Consumes: `SkillRepository.list_tree_names()`, `resolve_tree_name()`, `get_tree()`, `list_skills_in_tree()`.
- Produces:
  - `SkillTreeResultsPayload(tree, results)`
  - `SkillTreeConfirmationPayload(query, suggested_tree)`
  - `SkillTreeChoicesPayload(query, candidates)`
  - `SkillTreeNotFoundPayload(query, suggestions)`
  - `SkillTreeHelpPayload(tree_names)`
  - `SkillSearchService.handle_tree_request(query: str) -> SkillPayload`
  - `run_skill_tree_by_id(database_path: Path, tree_id: str, *, repository_factory=SkillRepository) -> SkillTreeResultsPayload`

- [ ] **Step 1: Write failing service tests**

Create `tests/test_skill_tree_listing.py`. Define a small fake repository with `list_tree_names`, `resolve_tree_name`, `get_tree`, `list_skills_in_tree`, `get_skill`, and `resolve_skill_name`. Use `Mock` tree/skill objects with the attributes touched by `_snippet()` and `_result_item()`.

Add these tests:

```python
class SkillTreeServiceTests(unittest.TestCase):
    def test_bare_tree_returns_help(self):
        payload = self.service.handle("tree")
        self.assertIsInstance(payload, SkillTreeHelpPayload)
        self.assertIn("Shield Skills", payload.tree_names)

    def test_exact_canonical_executes(self):
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

    def test_close_fuzzy_matches_return_choices(self):
        payload = self.service.handle("tree magic skillz")
        self.assertIsInstance(payload, SkillTreeChoicesPayload)
        self.assertEqual(
            [tree.name for tree in payload.candidates[:2]],
            ["Magic Skills", "Magic Warrior Skills"],
        )

    def test_low_confidence_junk_returns_not_found(self):
        payload = self.service.handle("tree xyzabc")
        self.assertIsInstance(payload, SkillTreeNotFoundPayload)
        self.assertLessEqual(len(payload.suggestions), 3)

    def test_treehouse_remains_free_text(self):
        with patch.object(self.service, "_search_free_text", return_value=Mock()) as search:
            result = self.service.handle("treehouse")
        search.assert_called_once_with("treehouse")
        self.assertIs(result, search.return_value)

    def test_tree_path_does_not_touch_semantic_runtime(self):
        runtime = Mock()
        service = SkillSearchService(self.repo, semantic_runtime=runtime)
        service.handle("tree shield")
        runtime.get_index.assert_not_called()
```

Add one test with 23 fake skills and assert `len(payload.results) == 23` and IDs remain in `list_skills_in_tree()` order.

- [ ] **Step 2: Run RED tests**

```bash
python -m unittest tests.test_skill_tree_listing.SkillTreeServiceTests -v
```

Expected: imports/behavior fail because tree payloads and routing do not exist yet.

- [ ] **Step 3: Add payload dataclasses**

In `toram_skill_search/models.py`:

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

Extend `SkillPayload` and `__all__` with all five classes.

- [ ] **Step 4: Add tree request parsing helpers**

In `toram_skill_search/service.py` import `fuzz` and `normalize_skill_name`, then add:

```python
from rapidfuzz import fuzz
from toram_skills.parsing import normalize_skill_name

TREE_PREFIX = "tree"
TREE_FUZZY_MIN_SCORE = 80.0
TREE_FUZZY_AMBIGUITY_MARGIN = 10.0


def _tree_shorthand(name: str) -> str:
    normalized = normalize_skill_name(name)
    suffix = " skills"
    return normalized[:-len(suffix)].strip() if normalized.endswith(suffix) else normalized


def _tree_request(cleaned: str) -> tuple[bool, str]:
    parts = cleaned.split(maxsplit=1)
    if not parts or parts[0].casefold() != TREE_PREFIX:
        return False, ""
    return True, "" if len(parts) == 1 else parts[1].strip()
```

This makes `tree`, `TREE shield`, and `tree shield` tree requests, but not `treehouse`.

- [ ] **Step 5: Implement exact/shorthand/fuzzy resolution**

Inside `SkillSearchService` add:

```python
def _tree_results(self, tree: SkillTreeDraft) -> SkillTreeResultsPayload:
    return SkillTreeResultsPayload(
        tree=tree,
        results=tuple(
            _result_item(self.repository, skill)
            for skill in self.repository.list_skills_in_tree(tree.id)
        ),
    )


def _resolve_exact_tree(self, query: str) -> SkillTreeDraft | None:
    exact = self.repository.resolve_tree_name(query)
    if len(exact) == 1:
        return exact[0]

    normalized_query = normalize_skill_name(query)
    shorthand_names = [
        name
        for name in self.repository.list_tree_names()
        if _tree_shorthand(name) == normalized_query
    ]
    if len(shorthand_names) != 1:
        return None
    resolved = self.repository.resolve_tree_name(shorthand_names[0])
    return resolved[0] if len(resolved) == 1 else None


def handle_tree_request(self, query: str) -> SkillPayload:
    cleaned = " ".join(query.split())
    names = tuple(self.repository.list_tree_names())
    if not cleaned:
        return SkillTreeHelpPayload(names)

    exact = self._resolve_exact_tree(cleaned)
    if exact is not None:
        return self._tree_results(exact)

    normalized_query = normalize_skill_name(cleaned)
    scored = []
    for name in names:
        score = max(
            fuzz.WRatio(normalized_query, normalize_skill_name(name)),
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

At the start of `handle()`, after whitespace normalization and before exact skill-name resolution:

```python
is_tree_request, tree_query = _tree_request(cleaned)
if is_tree_request:
    return self.handle_tree_request(tree_query)
```

Do not call `_search_free_text()` or `semantic_runtime.get_index()` from this path.

- [ ] **Step 6: Add trusted tree-ID listing helper**

In `service.py` add:

```python
def run_skill_tree_by_id(
    database_path: Path,
    tree_id: str,
    *,
    repository_factory=SkillRepository,
) -> SkillTreeResultsPayload:
    repository = None
    try:
        repository = repository_factory(Path(database_path).expanduser().resolve())
        service = SkillSearchService(repository, semantic_runtime=None)
        return service._tree_results(repository.get_tree(tree_id))
    finally:
        if repository is not None:
            repository.close()
```

Export it from `toram_skill_search/__init__.py`. This helper accepts only a bot-held tree ID and never reruns fuzzy matching.

- [ ] **Step 7: Run GREEN service tests**

```bash
python -m unittest tests.test_skill_tree_listing.SkillTreeServiceTests -v
```

Expected: PASS.

- [ ] **Step 8: Commit Task 1**

```bash
git add toram_skill_search/models.py toram_skill_search/service.py toram_skill_search/__init__.py tests/test_skill_tree_listing.py
git commit -m "feat: add deterministic skill tree resolution"
```

---

### Task 2: Add paginated tree results and detail Back navigation

**Files:**
- Modify: `toram_discord/skill_ui.py`
- Modify/Test: `tests/test_skill_tree_listing.py`

**Interfaces:**
- Consumes: `SkillTreeResultsPayload`, `PAGE_SIZE`, existing `SkillDetailView`/detail pages/session state.
- Produces:
  - `build_skill_tree_results_embed(payload, page)`
  - `SkillTreeResultsView`
  - `SkillDetailView` Back support for both ordinary results and tree results.

- [ ] **Step 1: Write failing tree rendering tests**

Build a payload with six results and assert:

```python
embed = build_skill_tree_results_embed(payload, 0)
self.assertEqual(embed.title, "Shield Skills")
self.assertIn("6 skills", embed.description)
self.assertIn("Showing 1–5 of 6", embed.description)
self.assertNotIn("Closest matches first", embed.description)
self.assertNotIn("Shield Skills •", embed.description)
self.assertNotIn("None", embed.description)
```

Page 1 must contain only result 6. Add an empty-tree test that says `0 skills` and `No skills are currently stored for this tree.` rather than `Showing 1–0 of 0`.

- [ ] **Step 2: Run RED rendering tests**

```bash
python -m unittest tests.test_skill_tree_listing -v
```

Expected: FAIL because tree embed/view are absent.

- [ ] **Step 3: Add tree-specific compact metadata**

Keep ordinary `_compact_skill_metadata()` unchanged. Add:

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

- [ ] **Step 4: Implement tree embed pagination**

```python
def build_skill_tree_results_embed(payload: SkillTreeResultsPayload, page: int) -> discord.Embed:
    total = len(payload.results)
    if total == 0:
        return discord.Embed(
            title=truncate_discord_text(payload.tree.name, 256),
            description="0 skills\n\nNo skills are currently stored for this tree.",
        )

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

- [ ] **Step 5: Add `SkillTreeResultsView`**

Mirror `SkillResultsView` behavior but render tree pages. Requirements:

- same `SessionBoundView` owner/generation parameters;
- current page from `session.page`;
- one select option per visible skill;
- Previous/Next update `session.page`;
- selecting a skill builds existing detail pages and stores `session.selected_index`;
- pass the original `SkillTreeResultsPayload` to `SkillDetailView` as the Back target.

- [ ] **Step 6: Generalize detail Back target**

Change:

```python
results_payload: SkillResultsPayload | None
```

to:

```python
results_payload: SkillResultsPayload | SkillTreeResultsPayload | None
```

In `_back()`:

```python
if isinstance(self.results_payload, SkillTreeResultsPayload):
    embed = build_skill_tree_results_embed(self.results_payload, session.page)
    view = SkillTreeResultsView(
        sessions=self.sessions,
        key=self.key,
        generation=self.generation,
        payload=self.results_payload,
    )
else:
    embed = build_skill_results_embed(self.results_payload, session.page)
    view = SkillResultsView(
        sessions=self.sessions,
        key=self.key,
        generation=self.generation,
        payload=self.results_payload,
    )
await interaction.response.edit_message(embed=embed, view=view)
```

- [ ] **Step 7: Run focused UI regressions**

```bash
python -m unittest tests.test_skill_tree_listing -v
python -m unittest tests.test_discord_skill_detail_pages -v
python -m unittest tests.test_discord_skill_review_regressions -v
```

Expected: PASS.

- [ ] **Step 8: Commit Task 2**

```bash
git add toram_discord/skill_ui.py tests/test_skill_tree_listing.py
git commit -m "feat: add paginated skill tree results"
```

---

### Task 3: Add help, fuzzy confirmation, ambiguous choices, and not-found UI

**Files:**
- Modify: `toram_discord/skill_ui.py`
- Modify: `toram_discord/app.py`
- Modify/Test: `tests/test_skill_tree_listing.py`
- Possibly modify: `tests/test_discord_skill_search.py`

**Interfaces:**
- Consumes: `run_skill_tree_by_id()`, all four non-result tree payloads, `config.skill_database_path`.
- Produces: `SkillTreeConfirmationView`, `SkillTreeChoicesView`, and tree-specific embeds.

- [ ] **Step 1: Write failing help/error/view tests**

Add assertions for:

```python
# bare tree help
self.assertIn("skill tree <tree name>", embed.description)
self.assertIn("Shield Skills", embed.description)
self.assertIsNone(view)

# typo
self.assertIn("Did you mean", embed.description)
self.assertIsInstance(view, SkillTreeConfirmationView)

# ambiguous
self.assertIn("Which skill tree", embed.description)
self.assertIsInstance(view, SkillTreeChoicesView)

# junk
self.assertIn("couldn't find", embed.description.casefold())
self.assertIsNone(view)
```

Use the existing fake interaction pattern from Discord skill tests to verify Show Skills, Cancel, and candidate select.

- [ ] **Step 2: Run RED interaction tests**

```bash
python -m unittest tests.test_skill_tree_listing -v
```

Expected: FAIL because the new views/payload routing do not exist.

- [ ] **Step 3: Add tree help and not-found embeds**

Help copy begins:

```text
Use `skill tree <tree name>`.
Example: `skill tree shield`

Available skill trees:
```

Append every canonical tree name. Respect Discord limits: if the full list exceeds the embed description limit, put the catalog into sequential embed fields, each field value `<= 1024`, without dropping names.

Not-found copy:

```text
I couldn't find a skill tree matching "xyzabc".

Closest skill trees:
• ...
```

No interactive view for low-confidence not-found.

- [ ] **Step 4: Implement `SkillTreeConfirmationView`**

Constructor stores `database_path: Path` and the trusted `suggested_tree.id`. Add session-bound buttons:

```python
ActionButton(label="Show skills", style=discord.ButtonStyle.primary, handler=self._show)
ActionButton(label="Cancel", style=discord.ButtonStyle.secondary, handler=self._cancel)
```

`_show()` calls `run_skill_tree_by_id(self.database_path, self.suggested_tree.id)`, resets `session.page = 0` and `session.selected_index = None`, and edits the message to `build_skill_tree_results_embed(..., 0)` + `SkillTreeResultsView`.

`_cancel()` edits to:

```python
discord.Embed(
    title="Skill tree search cancelled",
    description="No skill tree was opened.",
)
```

with `view=None`. It never reruns fuzzy matching.

- [ ] **Step 5: Implement `SkillTreeChoicesView`**

Create one `ActionSelect` whose labels are canonical tree names and whose values are indexes into the trusted `payload.candidates` tuple. On selection:

1. validate the index;
2. call `run_skill_tree_by_id(database_path, selected_tree.id)`;
3. reset session page/selection;
4. edit to tree results + `SkillTreeResultsView`.

Inherited `SessionBoundView` continues to enforce owner/generation checks.

- [ ] **Step 6: Route new payloads in `build_skill_payload_message()`**

Add a required keyword parameter:

```python
database_path: Path
```

Handle `SkillTreeHelpPayload`, `SkillTreeNotFoundPayload`, `SkillTreeConfirmationPayload`, `SkillTreeChoicesPayload`, and `SkillTreeResultsPayload` before the ordinary detail/results fallthrough.

For tree results, build `SkillTreeResultsView` only when results exist.

- [ ] **Step 7: Pass the skill DB path from `app.py`**

Change only the existing call:

```python
embed, view = build_skill_payload_message(
    payload,
    bot_example_prefix=bot_example_prefix(message.guild, bot_user),
    sessions=sessions,
    key=key,
    generation=session.generation,
    database_path=config.skill_database_path,
)
```

No other app routing changes.

- [ ] **Step 8: Add the tree example to general skill help**

Keep ordinary search examples first; append:

```text
skill tree shield
```

Update `tests/test_discord_skill_search.py` only if its existing help assertions require the new line.

- [ ] **Step 9: Run focused Discord tests**

```bash
python -m unittest tests.test_skill_tree_listing -v
python -m unittest tests.test_discord_skill_search -v
python -m unittest tests.test_discord_skill_detail_pages -v
python -m unittest tests.test_discord_skill_review_regressions -v
```

Expected: PASS.

- [ ] **Step 10: Commit Task 3**

```bash
git add toram_discord/skill_ui.py toram_discord/app.py tests/test_skill_tree_listing.py tests/test_discord_skill_search.py
git commit -m "feat: add skill tree confirmation and choices"
```

---

### Task 4: Validate against the real skill database

**Files:**
- Modify/Test: `tests/test_skill_tree_listing.py`

**Interfaces:**
- Consumes: checked-in `coryn_data/database/skills.sqlite`, `run_skill_search()`, `SkillRepository`.
- Produces: real-corpus acceptance coverage without semantic runtime.

- [ ] **Step 1: Add real-database tests**

Against `coryn_data/database/skills.sqlite` with `semantic_runtime=None`, assert:

```python
payload = run_skill_search(DB_PATH, "tree shield", semantic_runtime=None)
self.assertIsInstance(payload, SkillTreeResultsPayload)
self.assertEqual(payload.tree.name, "Shield Skills")

with SkillRepository(DB_PATH) as repo:
    expected_ids = [skill.id for skill in repo.list_skills_in_tree(payload.tree.id)]
self.assertEqual([result.skill.id for result in payload.results], expected_ids)
```

Also assert:

```text
tree Shield Skills -> SkillTreeResultsPayload / Shield Skills
tree shield        -> SkillTreeResultsPayload / Shield Skills
tree sheild        -> SkillTreeConfirmationPayload / Shield Skills
tree magic         -> SkillTreeResultsPayload / Magic Skills
tree xyzabc        -> SkillTreeNotFoundPayload
```

Do not require semantic embeddings for any case.

- [ ] **Step 2: Verify ordinary skill behavior alongside tree behavior**

Add regression assertions that `shield bash` still returns the existing exact skill/detail payload and `treehouse` remains ordinary free-text handling.

- [ ] **Step 3: Run all skill-focused tests**

```bash
python -m unittest tests.test_skill_tree_listing -v
python -m unittest tests.test_discord_skill_search -v
python -m unittest tests.test_discord_skill_detail_pages -v
python -m unittest tests.test_discord_skill_review_regressions -v
```

Expected: PASS.

- [ ] **Step 4: Commit Task 4**

```bash
git add tests/test_skill_tree_listing.py
git commit -m "test: cover real skill tree listing"
```

---

### Task 5: Full regression verification and integration readiness

**Files:**
- No planned source changes.

**Interfaces:**
- Produces: a clean, verified feature branch ready to integrate.

- [ ] **Step 1: Run the complete repository suite**

```bash
python -m unittest discover -s tests -p 'test_*.py' -v
```

Expected: all tests PASS.

- [ ] **Step 2: Run representative smoke checks**

Run a small Python check against the real database with `semantic_runtime=None` for:

```text
tree Shield Skills
tree shield
tree sheild
tree magic
tree xyzabc
shield bash
treehouse
```

Expected tree payload sequence:

```text
SkillTreeResultsPayload
SkillTreeResultsPayload
SkillTreeConfirmationPayload
SkillTreeResultsPayload
SkillTreeNotFoundPayload
```

`shield bash` must retain existing skill detail/search behavior. `treehouse` must not return any tree payload type.

- [ ] **Step 3: Inspect final diff for scope creep**

Expected production files:

```text
toram_skill_search/models.py
toram_skill_search/service.py
toram_skill_search/__init__.py
toram_discord/skill_ui.py
toram_discord/app.py
```

Expected test/docs files:

```text
tests/test_skill_tree_listing.py
tests/test_discord_skill_search.py   # only if help assertion changed
docs/superpowers/specs/2026-08-13-skill-tree-listing-design.md
docs/superpowers/plans/2026-08-13-skill-tree-listing.md
```

There must be no raw skill, `skills.sqlite`, embedding, schema, item-search, or Qwen changes.

- [ ] **Step 4: Verify clean branch state**

```bash
git status --short
```

Expected: no output.

- [ ] **Step 5: Record verification evidence**

Report the exact branch/head SHA, focused skill-tree test result, existing skill-display regression result, full-suite result, and representative command outcomes. Do not integrate a failing or unverified head.
