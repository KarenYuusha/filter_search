# Skill Display Pagination Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Discord skill search results compact and make every skill detail field/section viewable through automatic pagination without silently truncating detail content.

**Architecture:** Add a focused, Discord-display-only page builder that converts `SkillDetailPayload` into immutable logical pages under Discord embed limits. Keep `toram_discord.skill_ui` responsible for rendering those logical pages and for Previous/Next/Back interactions; result-page state remains in the existing session while detail-page state lives in the detail view so the two page indexes cannot overwrite each other.

**Tech Stack:** Python 3, `discord.py`, immutable dataclasses, existing `toram_skill_search` payload models, `unittest`/existing repository test suite.

## Global Constraints

- Search results remain paginated at 5 results per page.
- Compact result metadata order is: skill tree, tier, MP cost, damage type; use skill type only when damage type is absent.
- Missing metadata is omitted; never render placeholder text such as `None`.
- Full detail content order is: Overview, Range / Timing, Ailments, Weapon requirements, Weapon restrictions, Description, Game description, then parsed skill sections in source order.
- Keep a detail section intact when it fits; otherwise move it whole to the next page when possible.
- Split an individually oversized section into continuation chunks labeled `<Section name> (continued)`.
- Prefer split boundaries in this order: newline/paragraph, sentence, whitespace, hard character boundary.
- No non-empty text selected for the detail view may be silently dropped because of Discord field, description, field-count, or 6000-character total embed limits.
- Truncation is allowed only for intentionally compact UI text such as result previews and select labels/descriptions, not full detail content.
- Existing owner/current-generation interaction protections must remain unchanged.
- This change is display-only: do not change skill retrieval/ranking, embeddings, parser/database schema, Qwen behavior, item-search display, or implement Multiple Hunt variants.

---

## File Structure

- Create `toram_discord/skill_detail_pages.py` — pure logical detail-section extraction, safe text splitting, page packing, and immutable page models. It must not own Discord interaction state.
- Modify `toram_discord/skill_ui.py` — compact result rendering, logical-page-to-embed rendering, and skill detail navigation.
- Create `tests/test_discord_skill_detail_pages.py` — focused unit tests for content preservation, splitting, ordering, and Discord budgets.
- Modify `tests/test_discord_skill_search.py` — compact result formatting plus Previous/Next/Back/direct-detail interaction behavior.
- Modify `tests/test_discord_skill_review_regressions.py` — replace the old “single embed <= 6000” regression with multi-page no-loss assertions while retaining the skill-specific error regression.

---

### Task 1: Build a pure skill-detail page packer

**Files:**
- Create: `toram_discord/skill_detail_pages.py`
- Create: `tests/test_discord_skill_detail_pages.py`

**Interfaces:**
- Consumes: `toram_skill_search.models.SkillDetailPayload` and the existing `SkillDraft`/`SkillSection` values reachable from it.
- Produces:
  - `SkillDetailField(name: str, value: str, inline: bool = False)` frozen dataclass.
  - `SkillDetailPage(title: str, description: str, fields: tuple[SkillDetailField, ...])` frozen dataclass.
  - `build_skill_detail_pages(payload: SkillDetailPayload) -> tuple[SkillDetailPage, ...]`.
  - `detail_page_char_count(page: SkillDetailPage, *, footer_text: str | None = None) -> int` for exact testable budget accounting.

- [ ] **Step 1: Write failing tests for header metadata, section order, and missing-value omission**

Create `tests/test_discord_skill_detail_pages.py` with a fixture based on the canonical `magic: finale` payload and assertions equivalent to:

```python
pages = build_skill_detail_pages(payload)
self.assertGreaterEqual(len(pages), 1)
first = pages[0]
self.assertEqual(first.title, payload.skill.name)
self.assertIn(payload.tree.name, first.description)
self.assertIn(f"Tier {payload.skill.tier}", first.description)
if payload.skill.required_level is not None:
    self.assertIn(f"Required Lv {payload.skill.required_level}", first.description)
visible = "\n".join(
    field.name + "\n" + field.value
    for page in pages
    for field in page.fields
)
self.assertNotIn("None", visible)
self.assertLess(visible.index("Overview"), visible.index("Range / Timing"))
```

Add a synthetic payload with optional values replaced by `None`/empty tuples and assert those labels do not appear.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
python -m unittest tests.test_discord_skill_detail_pages -v
```

Expected: import failure because `toram_discord.skill_detail_pages` does not exist.

- [ ] **Step 3: Add immutable page models and logical section extraction**

Implement the new module with these constants and models:

```python
DISCORD_EMBED_TOTAL_LIMIT = 6000
DISCORD_EMBED_PACK_LIMIT = 5960  # reserve footer/page-label space
DISCORD_TITLE_LIMIT = 256
DISCORD_DESCRIPTION_LIMIT = 4096
DISCORD_FIELD_NAME_LIMIT = 256
DISCORD_FIELD_VALUE_LIMIT = 1024
DISCORD_FIELD_COUNT_LIMIT = 25

@dataclass(frozen=True)
class SkillDetailField:
    name: str
    value: str
    inline: bool = False

@dataclass(frozen=True)
class SkillDetailPage:
    title: str
    description: str
    fields: tuple[SkillDetailField, ...]
```

Build header metadata as `tree name • Tier N • Required Lv N`, omitting absent pieces. Extract logical fields in the approved order. Overview must contain only `Skill type`, `MP cost`, `Damage type`, and `Element`; tier/required level belong in the header metadata. Range/Timing keeps cast range, hit range, cast time, and hit count. Tuple-valued ailment/weapon fields use comma-separated values.

- [ ] **Step 4: Write failing tests for oversized sections and no-text-loss behavior**

Add a synthetic payload using `dataclasses.replace()` and `SkillSection` with:

```python
description = "description-token " * 180
long_section_body = "\n".join(f"line-{i}: " + "z" * 180 for i in range(80))
```

Assert:

```python
pages = build_skill_detail_pages(payload)
self.assertGreater(len(pages), 1)
self.assertTrue(any("(continued)" in f.name for p in pages for f in p.fields))
combined_values = "\n".join(f.value for p in pages for f in p.fields)
for marker in ("description-token", "line-0:", "line-79:"):
    self.assertIn(marker, combined_values)
for page in pages:
    self.assertLessEqual(len(page.fields), 25)
    for field in page.fields:
        self.assertLessEqual(len(field.name), 256)
        self.assertLessEqual(len(field.value), 1024)
    self.assertLessEqual(detail_page_char_count(page, footer_text="Page 99 / 99"), 6000)
```

Also add a test where a medium section does not fit the remaining current-page budget but does fit on an empty page; assert its first chunk starts on the next page rather than being unnecessarily split across the boundary.

- [ ] **Step 5: Run the new tests and verify the new cases fail**

Run:

```bash
python -m unittest tests.test_discord_skill_detail_pages -v
```

Expected: failures around multi-page packing/continuation handling until the packer is implemented.

- [ ] **Step 6: Implement natural-boundary splitting and page packing**

Use helpers with explicit responsibilities:

```python
def _split_text(text: str, limit: int) -> tuple[str, ...]: ...
def _section_chunks(name: str, value: str) -> tuple[SkillDetailField, ...]: ...
def _can_fit(page: SkillDetailPage, fields: tuple[SkillDetailField, ...]) -> bool: ...
def build_skill_detail_pages(payload: SkillDetailPayload) -> tuple[SkillDetailPage, ...]: ...
```

`_split_text` must choose the last valid boundary at or before `limit`: first `\n`, then sentence-ending punctuation followed by whitespace, then whitespace, then `limit` exactly. Strip only boundary whitespace; do not discard non-whitespace content.

For each logical section:

1. Convert it to one or more field chunks no longer than 1024 characters.
2. Use the original label for the first chunk and `<label> (continued)` for later chunks.
3. If all chunks fit on an empty page but not the current page, start a new page before adding any chunk.
4. If the section is larger than one page, add chunks sequentially and open new pages only when the next chunk would violate field-count or total-budget limits.
5. Keep `title` and header `description` identical on every detail page.
6. Guarantee at least one page even when a skill has no optional fields.

Use `DISCORD_EMBED_PACK_LIMIT` during packing; `detail_page_char_count(..., footer_text="Page 99 / 99")` must remain <=6000 for all normal generated pages.

- [ ] **Step 7: Run the page-builder tests and verify GREEN**

Run:

```bash
python -m unittest tests.test_discord_skill_detail_pages -v
```

Expected: all page-builder tests pass.

- [ ] **Step 8: Commit Task 1**

```bash
git add toram_discord/skill_detail_pages.py tests/test_discord_skill_detail_pages.py
git commit -m "feat: add lossless skill detail page builder"
```

---

### Task 2: Make skill search results compact and deterministic

**Files:**
- Modify: `toram_discord/skill_ui.py`
- Modify: `tests/test_discord_skill_search.py`

**Interfaces:**
- Consumes: existing `SkillResultsPayload` and `SkillResultItem`.
- Produces internal helpers:
  - `_compact_skill_metadata(result: SkillResultItem) -> str`
  - `_compact_skill_preview(text: str, limit: int = 160) -> str`
- Preserves public `build_skill_results_embed(payload: SkillResultsPayload, page: int) -> discord.Embed`.

- [ ] **Step 1: Write failing compact-result tests**

In `DiscordSkillRenderingTests`, add a synthetic `SkillResultItem` whose skill has tier, numeric MP cost, damage type, and a >300-character snippet. Assert the rendered result contains lines equivalent to:

```text
1. **Magic: Finale**
Magic Skills • Tier 4 • MP 1600 • Magic
```

and assert the normalized preview is <=160 characters before indentation, ends with an ellipsis when truncated, and does not include retrieval diagnostics.

Add another result with absent damage type and present skill type; assert skill type is used. Add a result with missing tier/MP/type; assert there are no doubled separators and no `None` text.

- [ ] **Step 2: Run the focused rendering tests and verify RED**

Run:

```bash
python -m unittest tests.test_discord_skill_search.DiscordSkillRenderingTests -v
```

Expected: current renderer still uses `**name** — tree` and a 300-character snippet, so compact-format assertions fail.

- [ ] **Step 3: Implement compact result helpers and layout**

In `toram_discord/skill_ui.py`, import `SkillResultItem` and implement:

```python
def _compact_skill_metadata(result: SkillResultItem) -> str:
    skill = result.skill
    parts = [result.tree.name]
    if skill.tier is not None:
        parts.append(f"Tier {skill.tier}")
    if skill.mp_cost_value is not None:
        parts.append(f"MP {skill.mp_cost_value}")
    elif skill.mp_cost_text and skill.mp_cost_text.strip():
        value = skill.mp_cost_text.strip()
        parts.append(value if value.casefold().startswith("mp ") else f"MP {value}")
    display_type = skill.damage_type or skill.skill_type
    if display_type and display_type.strip():
        parts.append(display_type.strip())
    return " • ".join(parts)
```

Normalize preview whitespace with `" ".join(text.split())`, then call `truncate_discord_text(..., 160)`. Render each result as three compact lines: numbered bold name, indented metadata if non-empty, indented preview if non-empty. Keep the existing 5-result page size and query title.

- [ ] **Step 4: Run rendering tests and verify GREEN**

Run:

```bash
python -m unittest tests.test_discord_skill_search.DiscordSkillRenderingTests -v
```

Expected: all rendering tests pass.

- [ ] **Step 5: Commit Task 2**

```bash
git add toram_discord/skill_ui.py tests/test_discord_skill_search.py
git commit -m "feat: compact Discord skill results"
```

---

### Task 3: Render logical detail pages and add Previous/Next navigation

**Files:**
- Modify: `toram_discord/skill_ui.py`
- Modify: `tests/test_discord_skill_search.py`

**Interfaces:**
- Consumes from Task 1: `SkillDetailPage`, `build_skill_detail_pages()`.
- Produces:
  - `render_skill_detail_page(page: SkillDetailPage, *, page_index: int, total_pages: int) -> discord.Embed`.
  - Backward-compatible `build_skill_detail_embed(payload: SkillDetailPayload) -> discord.Embed` returning the first rendered detail page.
  - `SkillDetailView(..., pages: tuple[SkillDetailPage, ...], page_index: int = 0, results_payload: SkillResultsPayload | None = None)`.

- [ ] **Step 1: Write failing tests for rendered page footers and direct-detail controls**

Add tests that build a synthetic multi-page payload and assert:

```python
pages = build_skill_detail_pages(payload)
embed = render_skill_detail_page(pages[1], page_index=1, total_pages=len(pages))
self.assertEqual(embed.footer.text, f"Page 2 / {len(pages)}")
```

Call `build_skill_payload_message()` with a direct `SkillDetailPayload`; assert a multi-page detail returns a `SkillDetailView` whose labels contain Previous and Next but not Back to Results. For a single-page direct detail, assert the returned view is `None`.

- [ ] **Step 2: Write failing tests for result-selected detail navigation**

Update `test_skill_detail_view_has_back_to_results` to construct real logical pages and pass them into the new view signature. Assert:

- first detail page: Previous disabled, Next enabled when multiple pages exist, Back to Results present;
- middle detail page: Previous/Next enabled;
- last detail page: Next disabled;
- single-page result-selected detail: Back to Results present, no Previous/Next buttons.

Use a tiny fake interaction response object with an async `edit_message(**kwargs)` method and call the view’s `_next`, `_previous`, and `_back` handlers directly. Assert `_next` edits to page 2, `_previous` returns to page 1, and `_back` renders `build_skill_results_embed(results_payload, session.page)` without changing `session.page`.

- [ ] **Step 3: Run interaction tests and verify RED**

Run:

```bash
python -m unittest tests.test_discord_skill_search.DiscordSkillInteractionTests -v
```

Expected: constructor/signature/footer/direct-detail-navigation assertions fail under the current single-embed detail implementation.

- [ ] **Step 4: Add logical-page rendering**

Replace the old `_add_skill_field`-driven detail construction in `skill_ui.py` with:

```python
def render_skill_detail_page(
    page: SkillDetailPage,
    *,
    page_index: int,
    total_pages: int,
) -> discord.Embed:
    embed = discord.Embed(title=page.title, description=page.description)
    for field in page.fields:
        embed.add_field(name=field.name, value=field.value, inline=field.inline)
    if total_pages > 1:
        embed.set_footer(text=f"Page {page_index + 1} / {total_pages}")
    return embed


def build_skill_detail_embed(payload: SkillDetailPayload) -> discord.Embed:
    pages = build_skill_detail_pages(payload)
    return render_skill_detail_page(pages[0], page_index=0, total_pages=len(pages))
```

Do not reintroduce truncation for detail fields.

- [ ] **Step 5: Implement `SkillDetailView` page state independently from result-page state**

Change the view to store immutable `pages`, `page_index`, and optional `results_payload`. Clamp `page_index` to valid bounds. Add Previous and Next buttons only when `len(pages) > 1`; disable them on boundary pages. Add Back to Results only when `results_payload is not None`.

`_previous` and `_next` must render from the already-built `self.pages` tuple and construct the replacement view with the same tuple. They must not mutate `session.page`.

`_back` must leave `session.page` unchanged, clear only `session.selected_index`, and restore the result embed/view using the original results payload.

- [ ] **Step 6: Wire result selection and direct exact-detail payloads to the paginator**

In `SkillResultsView._select_skill`:

```python
detail = SkillDetailPayload(result.skill, result.tree)
pages = build_skill_detail_pages(detail)
await interaction.response.edit_message(
    embed=render_skill_detail_page(pages[0], page_index=0, total_pages=len(pages)),
    view=SkillDetailView(
        sessions=self.sessions,
        key=self.key,
        generation=self.generation,
        pages=pages,
        page_index=0,
        results_payload=self.payload,
    ),
)
```

In `build_skill_payload_message`, direct `SkillDetailPayload` must build pages once. Return page 1 plus a `SkillDetailView(results_payload=None)` only when there is more than one page; return `(embed, None)` for a one-page direct detail.

- [ ] **Step 7: Run interaction/rendering tests and verify GREEN**

Run:

```bash
python -m unittest tests.test_discord_skill_search -v
```

Expected: routing, rendering, result pagination, detail pagination, direct-detail behavior, and Back-to-Results tests all pass.

- [ ] **Step 8: Commit Task 3**

```bash
git add toram_discord/skill_ui.py tests/test_discord_skill_search.py
git commit -m "feat: paginate Discord skill details"
```

---

### Task 4: Replace the old truncation regression with a lossless pagination regression

**Files:**
- Modify: `tests/test_discord_skill_review_regressions.py`

**Interfaces:**
- Consumes: `build_skill_detail_pages`, `detail_page_char_count`, and `render_skill_detail_page`.
- Produces no new runtime API.

- [ ] **Step 1: Rewrite the long-detail regression before changing assertions**

Replace `test_skill_detail_stays_within_discord_total_embed_limit` with a test that creates the same very long synthetic payload, then asserts all pages are valid and late content survives:

```python
pages = build_skill_detail_pages(payload)
self.assertGreater(len(pages), 1)
self.assertTrue(any("Section 19" in f.name for p in pages for f in p.fields))
self.assertTrue(any("z" * 100 in f.value for p in pages for f in p.fields))
for index, page in enumerate(pages):
    footer = f"Page {index + 1} / {len(pages)}" if len(pages) > 1 else None
    self.assertLessEqual(detail_page_char_count(page, footer_text=footer), 6000)
    embed = render_skill_detail_page(page, page_index=index, total_pages=len(pages))
    self.assertLessEqual(len(embed.fields), 25)
```

Add unique start/end markers to the synthetic description, game description, and final section and assert all markers appear across the generated fields. This catches the original silent-loss failure mode rather than merely checking the first embed’s size.

- [ ] **Step 2: Run the review regression tests**

Run:

```bash
python -m unittest tests.test_discord_skill_review_regressions -v
```

Expected: both the skill-specific error regression and the new lossless-pagination regression pass.

- [ ] **Step 3: Commit Task 4**

```bash
git add tests/test_discord_skill_review_regressions.py
git commit -m "test: require lossless skill detail pagination"
```

---

### Task 5: Full regression verification and public-surface sanity check

**Files:**
- Modify only if required by failing tests: `discord_bot.py` or existing module-boundary tests. Do not broaden the public surface unless a current facade test requires it.
- Test: `tests/test_discord_module_boundaries.py`
- Test: entire repository test suite.

**Interfaces:**
- No new feature behavior; this task validates that the display-only change did not alter routing/module ownership.

- [ ] **Step 1: Run focused skill/Discord regression suites**

Run:

```bash
python -m unittest \
  tests.test_discord_skill_detail_pages \
  tests.test_discord_skill_search \
  tests.test_discord_skill_review_regressions \
  tests.test_discord_module_boundaries -v
```

Expected: all tests pass.

- [ ] **Step 2: Run the complete repository test suite**

Run:

```bash
python -m unittest discover -s tests -p 'test_*.py' -v
```

Expected: all tests pass with no item-search, skill-search, parser, repository, embedding, or Discord regressions.

- [ ] **Step 3: Inspect the final diff against the approved scope**

Run:

```bash
git diff main...HEAD -- \
  toram_discord/skill_detail_pages.py \
  toram_discord/skill_ui.py \
  tests/test_discord_skill_detail_pages.py \
  tests/test_discord_skill_search.py \
  tests/test_discord_skill_review_regressions.py \
  docs/superpowers/specs/2026-08-13-skill-display-pagination-design.md \
  docs/superpowers/plans/2026-08-13-skill-display-pagination.md
```

Verify the diff contains no retrieval/ranking, embeddings, parser/schema, Qwen, item-display, or Multiple Hunt variant implementation changes.

- [ ] **Step 4: Commit any verification-only compatibility adjustment if one was necessary**

Only if Step 1 exposed a required facade/module-boundary update:

```bash
git add discord_bot.py tests/test_discord_module_boundaries.py
git commit -m "chore: keep skill display public surface consistent"
```

If no compatibility adjustment was needed, do not create an empty commit.

- [ ] **Step 5: Record final verification evidence before claiming completion**

Capture the exact branch HEAD SHA and the successful outputs from the focused suite and full suite. Do not merge to `main` until the implementation has passed review and the user chooses the integration option.
