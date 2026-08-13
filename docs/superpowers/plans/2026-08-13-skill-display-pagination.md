# Skill Display Pagination Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Discord skill search results compact and make every selected skill-detail value viewable through automatic pagination without silent truncation.

**Architecture:** Add a focused logical page-builder under `toram_discord` that converts `SkillDetailPayload` into immutable pages that already satisfy Discord embed limits. Keep `toram_discord.skill_ui` responsible for turning those logical pages into embeds and for Previous/Next/Back interactions. Result-list pagination remains in `DiscordSearchSession.page`; detail pagination stays inside `SkillDetailView`, so navigating details cannot overwrite the remembered results page.

**Tech Stack:** Python 3, `discord.py`, frozen dataclasses, existing `toram_skill_search` payload models, existing `unittest` suite.

## Global Constraints

- Search results remain 5 per page.
- Result metadata order: tree, tier, MP cost, damage type; use skill type only when damage type is absent.
- Omit missing metadata; never render `None` placeholders.
- Detail order: Overview, Range / Timing, Ailments, Weapon requirements, Weapon restrictions, Description, Game description, parsed source sections in source order.
- Keep a logical section intact when it fits. If it does not fit the remaining current page but fits an empty page, move it whole to the next page.
- Split only sections that cannot fit on one page/field. Continuation labels are `<Section name> (continued)`.
- Split preference: newline, sentence boundary, whitespace, hard character boundary.
- No non-empty detail value may be silently dropped because of Discord title, description, field, field-count, or 6000-character total embed limits.
- Truncation remains allowed only for compact UI text such as result previews and select labels/descriptions.
- Existing owner and stale-generation protections remain unchanged.
- Do not change retrieval/ranking, embeddings, parser/schema, Qwen, item display, or implement Multiple Hunt variants.

---

## File Structure

- Create `toram_discord/skill_detail_pages.py`: immutable detail-page models, logical section extraction, safe text splitting, and page packing.
- Modify `toram_discord/skill_ui.py`: compact result formatting, logical-page rendering, and detail navigation.
- Create `tests/test_discord_skill_detail_pages.py`: page-builder ordering, split, no-loss, and budget tests.
- Modify `tests/test_discord_skill_search.py`: compact results and interaction tests.
- Modify `tests/test_discord_skill_review_regressions.py`: replace the old single-embed budget regression with a multi-page no-loss regression.

---

### Task 1: Add a lossless logical detail-page builder

**Files:**
- Create: `toram_discord/skill_detail_pages.py`
- Create: `tests/test_discord_skill_detail_pages.py`

**Interfaces:**
- Consumes: `SkillDetailPayload`.
- Produces:
  - `SkillDetailField`
  - `SkillDetailPage`
  - `build_skill_detail_pages(payload: SkillDetailPayload) -> tuple[SkillDetailPage, ...]`
  - `detail_page_char_count(page: SkillDetailPage, *, footer_text: str | None = None) -> int`

- [ ] **Step 1: Write the RED tests for header metadata, ordering, omission, splitting, and budgets**

Create `tests/test_discord_skill_detail_pages.py`. Use the canonical `magic: finale` payload plus synthetic payloads made with `dataclasses.replace()` and `SkillSection`.

The core assertions must include:

```python
pages = build_skill_detail_pages(payload)
self.assertGreaterEqual(len(pages), 1)
self.assertEqual(pages[0].title, payload.skill.name)
self.assertIn(payload.tree.name, pages[0].description)
self.assertIn(f"Tier {payload.skill.tier}", pages[0].description)

visible = "\n".join(
    field.name + "\n" + field.value
    for page in pages
    for field in page.fields
)
self.assertNotIn("None", visible)
self.assertLess(visible.index("Overview"), visible.index("Range / Timing"))
```

For a synthetic long section, use unique first/last markers:

```python
long_body = "START-MARKER\n" + "\n".join(
    f"line-{index}: " + "z" * 180
    for index in range(80)
) + "\nEND-MARKER"
```

Then assert:

```python
pages = build_skill_detail_pages(long_payload)
self.assertGreater(len(pages), 1)
all_fields = tuple(field for page in pages for field in page.fields)
self.assertTrue(any("(continued)" in field.name for field in all_fields))
combined = "\n".join(field.value for field in all_fields)
self.assertIn("START-MARKER", combined)
self.assertIn("END-MARKER", combined)

for page in pages:
    self.assertLessEqual(len(page.title), 256)
    self.assertLessEqual(len(page.description), 4096)
    self.assertLessEqual(len(page.fields), 25)
    for field in page.fields:
        self.assertLessEqual(len(field.name), 256)
        self.assertLessEqual(len(field.value), 1024)
    self.assertLessEqual(
        detail_page_char_count(page, footer_text="Page 99 / 99"),
        6000,
    )
```

Also add one medium-section test: fill most of page 1 with an earlier section, then add a section that fits on an empty page but not the remaining space. Assert the medium section starts on page 2 with its original label, not a `(continued)` label.

- [ ] **Step 2: Run the new tests and verify RED**

```bash
python -m unittest tests.test_discord_skill_detail_pages -v
```

Expected: import failure because `toram_discord.skill_detail_pages` does not exist.

- [ ] **Step 3: Implement the immutable models, content extraction, splitter, and packer**

Create `toram_discord/skill_detail_pages.py` with these public constants/models:

```python
from __future__ import annotations

from dataclasses import dataclass
import re

from toram_skill_search.models import SkillDetailPayload

DISCORD_EMBED_TOTAL_LIMIT = 6000
DISCORD_EMBED_PACK_LIMIT = 5960
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


def detail_page_char_count(
    page: SkillDetailPage,
    *,
    footer_text: str | None = None,
) -> int:
    total = len(page.title) + len(page.description)
    total += sum(len(field.name) + len(field.value) for field in page.fields)
    if footer_text:
        total += len(footer_text)
    return total
```

Use this exact splitting behavior:

```python
def _split_text(text: str, limit: int) -> tuple[str, ...]:
    remaining = text.strip()
    chunks: list[str] = []
    while len(remaining) > limit:
        window = remaining[: limit + 1]
        cut = window.rfind("\n", 1, limit + 1)
        if cut <= 0:
            sentence_matches = list(re.finditer(r"[.!?](?=\s)", window[:limit]))
            cut = sentence_matches[-1].end() if sentence_matches else -1
        if cut <= 0:
            cut = window.rfind(" ", 1, limit + 1)
        if cut <= 0:
            cut = limit
        chunk = remaining[:cut].strip()
        if not chunk:
            chunk = remaining[:limit]
            cut = limit
        chunks.append(chunk)
        remaining = remaining[cut:].lstrip()
    if remaining:
        chunks.append(remaining)
    return tuple(chunks)
```

Build the header description from available values only:

```python
header_parts = [payload.tree.name]
if payload.skill.tier is not None:
    header_parts.append(f"Tier {payload.skill.tier}")
if payload.skill.required_level is not None:
    header_parts.append(f"Required Lv {payload.skill.required_level}")
header = " • ".join(header_parts)
```

Build logical fields in the approved order. Tier and required level move out of Overview because they are in the header. Overview contains skill type, MP cost, damage type, and element. Range / Timing contains cast range, hit range, cast time, and hit count. Add tuple-valued ailment/weapon data only when non-empty. Add Description and Game description when non-empty, then append every non-empty parsed source section in source order.

Convert each logical `(label, body)` into field chunks using `_split_text(body, 1024)`. The first chunk keeps `label`; later chunks use `f"{label} (continued)"`.

Pack chunks with these rules:

```python
def _field_cost(field: SkillDetailField) -> int:
    return len(field.name) + len(field.value)


def _base_page(title: str, description: str) -> SkillDetailPage:
    return SkillDetailPage(title=title, description=description, fields=())


def _can_add(page: SkillDetailPage, fields: tuple[SkillDetailField, ...]) -> bool:
    if len(page.fields) + len(fields) > DISCORD_FIELD_COUNT_LIMIT:
        return False
    added = sum(_field_cost(field) for field in fields)
    return detail_page_char_count(page) + added <= DISCORD_EMBED_PACK_LIMIT
```

For each logical section, first calculate all of its field chunks. If all chunks fit on an empty page but not the current page, open a new page before adding the section. If all chunks cannot fit on one page, add chunks one by one and open a new page whenever the next chunk would exceed the field count or pack limit. Keep title/header identical across pages. Return at least one page.

Before returning, validate generated pages; raise `ValueError` if a generated title exceeds 256, description exceeds 4096, field name exceeds 256, field value exceeds 1024, field count exceeds 25, or packed character count exceeds 5960. The current corpus uses short skill/tree/section labels, so this protects against programmer regressions without truncating detail content.

- [ ] **Step 4: Run the page-builder tests and verify GREEN**

```bash
python -m unittest tests.test_discord_skill_detail_pages -v
```

Expected: all page-builder tests pass.

- [ ] **Step 5: Commit Task 1**

```bash
git add toram_discord/skill_detail_pages.py tests/test_discord_skill_detail_pages.py
git commit -m "feat: add lossless skill detail page builder"
```

---

### Task 2: Make search results compact

**Files:**
- Modify: `toram_discord/skill_ui.py`
- Modify: `tests/test_discord_skill_search.py`

**Interfaces:**
- Preserves: `build_skill_results_embed(payload: SkillResultsPayload, page: int) -> discord.Embed`.
- Adds internal helpers `_compact_skill_metadata()` and `_compact_skill_preview()`.

- [ ] **Step 1: Write RED tests for compact result formatting**

Add rendering tests using `dataclasses.replace()` to create one result with tier, `mp_cost_value=1600`, damage type `Magic`, and a long snippet. Assert the result contains:

```text
1. **Magic: Finale**
Magic Skills • Tier 4 • MP 1600 • Magic
```

Assert the preview text is at most 160 characters and is truncated with an ellipsis. Add one fallback case where `damage_type=None` and `skill_type="Support"`; assert `Support` is displayed. Add one sparse result and assert no `None` and no doubled `•` separator appears.

- [ ] **Step 2: Run the rendering tests and verify RED**

```bash
python -m unittest tests.test_discord_skill_search.DiscordSkillRenderingTests -v
```

Expected: current `**name** — tree` plus 300-character snippet format fails the new assertions.

- [ ] **Step 3: Implement deterministic compact metadata and preview formatting**

In `toram_discord/skill_ui.py`, import `SkillResultItem` and add:

```python
def _compact_skill_metadata(result: SkillResultItem) -> str:
    skill = result.skill
    parts = [result.tree.name]
    if skill.tier is not None:
        parts.append(f"Tier {skill.tier}")
    if skill.mp_cost_value is not None:
        parts.append(f"MP {skill.mp_cost_value}")
    elif skill.mp_cost_text and skill.mp_cost_text.strip():
        raw = skill.mp_cost_text.strip()
        folded = raw.casefold()
        if folded.startswith("mp "):
            parts.append(raw)
        elif folded.endswith("mp") and raw[:-2].strip():
            parts.append(f"MP {raw[:-2].strip()}")
        else:
            parts.append(f"MP {raw}")
    display_type = skill.damage_type or skill.skill_type
    if display_type and display_type.strip():
        parts.append(display_type.strip())
    return " • ".join(parts)


def _compact_skill_preview(text: str, limit: int = 160) -> str:
    normalized = " ".join(str(text).split())
    return truncate_discord_text(normalized, limit) if normalized else ""
```

Change each result block to:

```python
lines.append(f"{index + 1}. **{result.skill.name}**")
metadata = _compact_skill_metadata(result)
if metadata:
    lines.append(f"   {metadata}")
preview = _compact_skill_preview(result.snippet)
if preview:
    lines.append(f"   {preview}")
lines.append("")
```

Keep the existing query title and 5-result pagination.

- [ ] **Step 4: Run rendering tests and verify GREEN**

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

### Task 3: Wire automatic detail pagination into Discord interactions

**Files:**
- Modify: `toram_discord/skill_ui.py`
- Modify: `tests/test_discord_skill_search.py`

**Interfaces:**
- Consumes from Task 1: `SkillDetailPage`, `build_skill_detail_pages()`.
- Adds: `render_skill_detail_page(page: SkillDetailPage, *, page_index: int, total_pages: int) -> discord.Embed`.
- Preserves: `build_skill_detail_embed(payload: SkillDetailPayload) -> discord.Embed` as a first-page compatibility wrapper.
- Changes `SkillDetailView` to receive `pages: tuple[SkillDetailPage, ...]`, `page_index: int = 0`, and `results_payload: SkillResultsPayload | None = None`.

- [ ] **Step 1: Write RED tests for page footers, button boundaries, Back, and direct exact-detail behavior**

Add tests for a synthetic multi-page payload. Assert page 2 renders `Page 2 / N` in the footer.

Construct `SkillDetailView` at first/middle/last page and assert:

```python
first_previous.disabled is True
first_next.disabled is False
middle_previous.disabled is False
middle_next.disabled is False
last_next.disabled is True
```

For result-selected details, assert `Back to Results` exists even for a single detail page. For direct `SkillDetailPayload` returned by `build_skill_payload_message`, assert:

- multi-page direct detail: Previous/Next exist, Back does not;
- single-page direct detail: returned view is `None`.

Use a fake interaction response:

```python
class FakeInteractionResponse:
    def __init__(self):
        self.edits = []

    async def edit_message(self, **kwargs):
        self.edits.append(kwargs)


class FakeInteraction:
    def __init__(self):
        self.response = FakeInteractionResponse()
```

Call `_next`, `_previous`, and `_back` directly. Assert next/previous render the expected footer and `_back` restores the original result page while leaving `session.page` unchanged.

Add an explicit protection regression using the inherited `interaction_check`: a wrong user receives the existing owner-only message, and a view whose generation is stale receives the existing stale-search message. This test must use `SkillDetailView`; do not modify `SessionBoundView` behavior.

- [ ] **Step 2: Run the interaction tests and verify RED**

```bash
python -m unittest tests.test_discord_skill_search.DiscordSkillInteractionTests -v
```

Expected: current detail view has only Back and direct exact details return no pagination view.

- [ ] **Step 3: Render logical pages without detail truncation**

Replace the current `_add_skill_field` detail construction with:

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
    return render_skill_detail_page(
        pages[0],
        page_index=0,
        total_pages=len(pages),
    )
```

Delete `_add_skill_field` if nothing else uses it. Keep `_embed_char_count` only if another test/runtime path still needs it; otherwise remove it as dead code.

- [ ] **Step 4: Implement detail-page navigation state inside `SkillDetailView`**

The constructor must clamp `page_index` and keep the provided `pages` tuple unchanged. Add Previous/Next only when `len(pages) > 1`; add Back only when `results_payload is not None`.

The navigation handlers must follow this shape:

```python
async def _next(self, interaction: discord.Interaction) -> None:
    next_index = min(self.page_index + 1, len(self.pages) - 1)
    await interaction.response.edit_message(
        embed=render_skill_detail_page(
            self.pages[next_index],
            page_index=next_index,
            total_pages=len(self.pages),
        ),
        view=SkillDetailView(
            sessions=self.sessions,
            key=self.key,
            generation=self.generation,
            pages=self.pages,
            page_index=next_index,
            results_payload=self.results_payload,
        ),
    )
```

`_previous` is symmetric with `max(self.page_index - 1, 0)`.

`_back` must:

```python
session = self.sessions.get(self.key)
if session is None or self.results_payload is None:
    return
session.selected_index = None
await interaction.response.edit_message(
    embed=build_skill_results_embed(self.results_payload, session.page),
    view=SkillResultsView(
        sessions=self.sessions,
        key=self.key,
        generation=self.generation,
        payload=self.results_payload,
    ),
)
```

Do not read or write `session.page` in `_next` or `_previous`.

- [ ] **Step 5: Wire result selection and direct detail payloads**

In `SkillResultsView._select_skill`, build pages once and pass the same tuple into the detail view:

```python
detail = SkillDetailPayload(result.skill, result.tree)
pages = build_skill_detail_pages(detail)
await interaction.response.edit_message(
    embed=render_skill_detail_page(
        pages[0],
        page_index=0,
        total_pages=len(pages),
    ),
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

In `build_skill_payload_message`, direct detail handling must be:

```python
if isinstance(payload, SkillDetailPayload):
    pages = build_skill_detail_pages(payload)
    embed = render_skill_detail_page(
        pages[0],
        page_index=0,
        total_pages=len(pages),
    )
    if len(pages) == 1:
        return embed, None
    return (
        embed,
        SkillDetailView(
            sessions=sessions,
            key=key,
            generation=generation,
            pages=pages,
            page_index=0,
            results_payload=None,
        ),
    )
```

- [ ] **Step 6: Run the skill Discord tests and verify GREEN**

```bash
python -m unittest tests.test_discord_skill_search -v
```

Expected: routing, rendering, result pagination, owner/generation protection, detail pagination, direct detail, and Back-to-Results tests all pass.

- [ ] **Step 7: Commit Task 3**

```bash
git add toram_discord/skill_ui.py tests/test_discord_skill_search.py
git commit -m "feat: paginate Discord skill details"
```

---

### Task 4: Replace the truncation regression and run full verification

**Files:**
- Modify: `tests/test_discord_skill_review_regressions.py`
- Test: `tests/test_discord_module_boundaries.py`
- Test: full repository suite.

**Interfaces:**
- No new runtime API.

- [ ] **Step 1: Replace the old single-embed budget regression with a no-loss pagination regression**

Keep `test_explicit_skill_exception_gets_skill_specific_error` unchanged.

Replace `test_skill_detail_stays_within_discord_total_embed_limit` with a synthetic long payload containing unique markers in description, game description, and the final source section. Build all logical pages and assert all markers survive:

```python
pages = build_skill_detail_pages(payload)
self.assertGreater(len(pages), 1)
combined = "\n".join(
    field.value
    for page in pages
    for field in page.fields
)
self.assertIn("DESCRIPTION-END", combined)
self.assertIn("GAME-DESCRIPTION-END", combined)
self.assertIn("FINAL-SECTION-END", combined)

for index, page in enumerate(pages):
    footer = f"Page {index + 1} / {len(pages)}"
    self.assertLessEqual(detail_page_char_count(page, footer_text=footer), 6000)
    embed = render_skill_detail_page(
        page,
        page_index=index,
        total_pages=len(pages),
    )
    self.assertLessEqual(len(embed.fields), 25)
```

This regression must fail if later sections disappear, even when page 1 itself is under 6000 characters.

- [ ] **Step 2: Run focused skill/Discord verification**

```bash
python -m unittest \
  tests.test_discord_skill_detail_pages \
  tests.test_discord_skill_search \
  tests.test_discord_skill_review_regressions \
  tests.test_discord_module_boundaries -v
```

Expected: all focused tests pass.

- [ ] **Step 3: Run the complete repository test suite**

```bash
python -m unittest discover -s tests -p 'test_*.py' -v
```

Expected: all tests pass with no item-search, skill-search, parser, repository, embedding, or Discord regressions.

- [ ] **Step 4: Review the final diff against scope**

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

Confirm there are no retrieval/ranking, embedding, parser/schema, Qwen, item-display, or Multiple Hunt variant changes.

- [ ] **Step 5: Commit the regression update**

```bash
git add tests/test_discord_skill_review_regressions.py
git commit -m "test: require lossless skill detail pagination"
```

If the focused/full suite exposed an existing facade/module-boundary expectation that requires exporting `render_skill_detail_page`, update only that required export/test in the same commit; otherwise do not broaden the public API.

- [ ] **Step 6: Record completion evidence**

Record the exact branch HEAD SHA plus the successful focused-suite and full-suite outputs before claiming implementation completion. Do not merge to `main` until review is complete and the user chooses the integration option.
