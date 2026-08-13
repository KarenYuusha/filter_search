# Clean Skill UI Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace text-heavy skill search/tree/detail messages with clean Discord-native cards that use local `coryn_skill_icons` thumbnails while preserving all existing search, pagination, navigation, and detail content behavior.

**Architecture:** Add one cached icon-resolver module. Refactor skill Discord rendering so a rendered message can carry multiple embeds plus fresh local file attachments; result pages become one header plus up to five skill cards, while detail pages remain one embed with structured inline metadata and the selected skill thumbnail. Search services, parser/DB, tree matching, item search, and Qwen remain unchanged.

**Tech Stack:** Python >=3.12, `discord.py>=2.7.1,<3`, `unittest`, local PNG assets.

## Global Constraints

- Clean/minimal Discord-native style; no RPG color/badge system.
- Preserve `PAGE_SIZE = 5` and all current search/tree semantics.
- Preserve select, Previous/Next, Back, owner, and generation behavior.
- Use checked-in icons as-is; never modify image assets.
- Icon matching is deterministic only; no fuzzy/semantic/Qwen matching.
- Missing or ambiguous icons silently fall back to iconless cards/details.
- Result pages: maximum 6 embeds and 5 icon files.
- Detail pages: 1 embed and maximum 1 icon file.
- Preserve every non-empty detail value/section and existing Discord packing limits.
- Do not change raw skills, `skills.sqlite`, embeddings, parser, schema, item/stat, or Qwen code.

---

## File Structure

- Create `toram_discord/skill_icons.py` — icon indexing/resolution.
- Modify `toram_discord/skill_ui.py` — card rendering, attachment transport, detail thumbnails.
- Modify `toram_discord/skill_detail_pages.py` — inline fixed metadata layout.
- Modify `toram_discord/app.py` — send `embeds/files/view` for skill responses.
- Create `tests/test_discord_skill_icons.py`.
- Modify `tests/test_skill_tree_listing.py`.
- Modify `tests/test_discord_skill_search.py`.
- Modify `tests/test_discord_skill_detail_pages.py`.
- Modify `tests/test_discord_skill_review_regressions.py` only for skill reply-shape assertions.

---

### Task 1: Deterministic skill-icon catalog

**Files:**
- Create: `toram_discord/skill_icons.py`
- Create/Test: `tests/test_discord_skill_icons.py`

**Interfaces:**
- Produces `normalize_icon_key(value: str) -> str`.
- Produces `SkillIconCatalog(root: Path).resolve(tree_name, skill_name) -> Path | None`.
- Produces `DEFAULT_SKILL_ICON_CATALOG` rooted at `<project>/coryn_skill_icons`.

- [ ] **Step 1: Write failing resolver tests**

```python
self.assertEqual(normalize_icon_key("MAGIC: FINALE"), "magicfinale")
self.assertEqual(normalize_icon_key("Magic_ Finale"), "magicfinale")
self.assertEqual(
    catalog.resolve("Shield Skills", "Shield Bash"),
    root / "Shield" / "Shield Bash.png",
)
```

Also cover `Magic Warrior Skills -> MagicBlade`, `Blacksmith Skills -> Smith`, unique global fallback, ambiguous global match -> `None`, missing root -> `None`, and repeated resolves without rescanning.

- [ ] **Step 2: Verify RED**

```bash
python -m unittest tests.test_discord_skill_icons -v
```

Expected: module/import failure.

- [ ] **Step 3: Implement normalization/indexing**

Core normalization:

```python
def normalize_icon_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value)).casefold()
    return "".join(ch for ch in normalized if ch.isalnum())
```

Tree aliases:

```python
TREE_FOLDER_ALIASES = {
    "magicwarrior": "magicblade",
    "blacksmith": "smith",
}
```

Build folder-local and global filename-stem indexes once, lazily. Strip trailing `Skills` before tree-folder lookup. Resolution order is exact normalized match in the resolved folder, then globally unique normalized filename stem, otherwise `None`.

- [ ] **Step 4: Add real-asset assertions**

Require these current checked-in assets to resolve:

```text
Shield Skills / Shield Bash -> Shield/Shield Bash.png
Magic Skills / MAGIC: FINALE -> Magic/Magic_ Finale.png
```

- [ ] **Step 5: Verify GREEN and commit**

```bash
python -m unittest tests.test_discord_skill_icons -v
git add toram_discord/skill_icons.py tests/test_discord_skill_icons.py
git commit -m "feat: add deterministic skill icon resolver"
```

---

### Task 2: Render clean result cards with icons

**Files:**
- Modify: `toram_discord/skill_ui.py`
- Modify: `toram_discord/app.py`
- Modify/Test: `tests/test_skill_tree_listing.py`
- Modify/Test: `tests/test_discord_skill_search.py`

**Interfaces:**
- Produces `SkillRenderedMessage(embeds, files, view)`.
- Produces `build_skill_results_message(...)` and `build_skill_tree_results_message(...)`.
- Changes `build_skill_payload_message(...)` to return `SkillRenderedMessage`.

- [ ] **Step 1: Write RED rendering tests**

For a six-result tree payload, page 0 must be one header + five cards:

```python
rendered = build_skill_tree_results_message(payload, 0, icon_catalog=fake_catalog)
self.assertEqual(len(rendered.embeds), 6)
self.assertEqual(rendered.embeds[0].title, "Shield Skills")
self.assertEqual(rendered.embeds[1].title, "1. Skill 0")
self.assertNotIn("Shield Skills", rendered.embeds[1].description or "")
self.assertEqual(len(rendered.files), 5)
```

For ordinary search, assert each card includes its tree name and the header alone contains `Closest matches first`.

- [ ] **Step 2: Verify RED**

```bash
python -m unittest tests.test_skill_tree_listing tests.test_discord_skill_search -v
```

- [ ] **Step 3: Add rendered-message contract**

```python
@dataclass(frozen=True)
class SkillRenderedMessage:
    embeds: tuple[discord.Embed, ...]
    files: tuple[discord.File, ...] = ()
    view: discord.ui.View | None = None
```

Create safe per-render attachment names using the card slot plus a short SHA-1 of the path:

```python
skill-{slot}-{digest}.png
```

Never use the raw skill name as an attachment filename.

- [ ] **Step 4: Build shared compact skill cards**

Each card uses:

```text
Title: <absolute number>. <skill name>
Description line 1: compact metadata
Description line 2: <=160-char preview when present
Thumbnail: attachment://<safe filename> when resolved
```

Ordinary metadata includes tree name; tree-list metadata omits it. Both include Tier, Required Lv, MP, and damage/skill type when present.

- [ ] **Step 5: Build list headers**

Tree header:

```text
Title: Shield Skills
Description: 13 skills · Showing 1–5 of 13
Footer: Page 1 / 3   # only if >1 page
```

Search header:

```text
Title: Skill search: <query>
Description: Closest matches first · Showing 1–5 of N
Footer: Page X / Y   # only if >1 page
```

Keep empty-result messages deterministic and iconless.

- [ ] **Step 6: Change initial Discord reply transport**

`build_skill_payload_message()` returns `SkillRenderedMessage` for every skill payload. In `app.py`:

```python
kwargs = {
    "embeds": list(rendered.embeds),
    "view": rendered.view,
    "mention_author": False,
    "allowed_mentions": discord.AllowedMentions.none(),
}
if rendered.files:
    kwargs["files"] = list(rendered.files)
await message.reply(**kwargs)
```

Do not touch the item/stat path.

- [ ] **Step 7: Verify and commit**

```bash
python -m unittest tests.test_skill_tree_listing -v
python -m unittest tests.test_discord_skill_search -v
python -m unittest tests.test_discord_skill_review_regressions -v
git add toram_discord/skill_ui.py toram_discord/app.py tests/test_skill_tree_listing.py tests/test_discord_skill_search.py tests/test_discord_skill_review_regressions.py
git commit -m "feat: render skill results as clean cards"
```

---

### Task 3: Attachment-aware interactions and clean detail layout

**Files:**
- Modify: `toram_discord/skill_ui.py`
- Modify: `toram_discord/skill_detail_pages.py`
- Modify/Test: `tests/test_skill_tree_listing.py`
- Modify/Test: `tests/test_discord_skill_search.py`
- Modify/Test: `tests/test_discord_skill_detail_pages.py`

**Interfaces:**
- Produces `_edit_skill_message(interaction, rendered)`.
- Produces `build_skill_detail_message(...) -> SkillRenderedMessage`.
- `SkillDetailView` carries the selected `SkillDetailPayload` through page changes.

- [ ] **Step 1: Write failing interaction tests**

Pagination edits must replace both visual content and icon files:

```python
await view._next(interaction)
edit = interaction.response.edits[-1]
self.assertIn("embeds", edit)
self.assertIn("attachments", edit)
self.assertNotIn("embed", edit)
```

For six tree results, page 2 should upload only the sixth skill's icon. Back from detail must restore the prior result page's card icons.

- [ ] **Step 2: Centralize interaction editing**

```python
async def _edit_skill_message(interaction, rendered):
    await interaction.response.edit_message(
        embeds=list(rendered.embeds),
        attachments=list(rendered.files),
        view=rendered.view,
    )
```

Always pass `attachments`, including `[]`, so stale files are removed.

Route ordinary/tree Previous/Next, select, Back, tree confirmation, tree choices, and cancel through this helper.

- [ ] **Step 3: Write RED detail-layout tests**

When present, fixed fields must appear as inline fields in this order:

```text
Type
MP Cost
Damage
Element
Cast Range
Hit Range
Cast Time
Hit Count
```

Missing fields are omitted. Ailments, weapon requirements/restrictions, Description, Game description, and parsed sections remain full-width and ordered after the fixed metadata.

- [ ] **Step 4: Change detail logical fields**

Change `_logical_fields()` to yield `(name, value, inline)` instead of grouped `Overview`/`Range / Timing` strings.

For values that split into multiple 1024-character chunks, force all chunks to `inline=False`; single-chunk fixed values preserve `inline=True`.

Do not change the existing page packing budgets or text splitting algorithm.

- [ ] **Step 5: Add detail thumbnail rendering**

`build_skill_detail_message()`:

1. renders the selected `SkillDetailPage`;
2. resolves `payload.tree.name + payload.skill.name` through the icon catalog;
3. adds one fresh `discord.File` and `attachment://...` thumbnail when available;
4. otherwise returns the exact same detail embed without a file.

The header description remains tree name + Tier + Required Lv when present.

- [ ] **Step 6: Carry detail payload through navigation**

`SkillDetailView` stores `detail_payload` alongside pages/page index and optional result Back target. Previous/Next rebuild a fresh detail message so local files are never reused after sending.

Direct exact detail:

- one page -> no view;
- multiple pages -> Previous/Next only.

Selected detail keeps Back to Results.

- [ ] **Step 7: Preserve lossless-detail regressions**

Existing markers in Description, Game description, and late parsed sections must still be present across pages. Keep assertions for field count/value length and total embed budget.

- [ ] **Step 8: Verify and commit**

```bash
python -m unittest tests.test_discord_skill_detail_pages -v
python -m unittest tests.test_discord_skill_search -v
python -m unittest tests.test_skill_tree_listing -v
python -m unittest tests.test_discord_skill_review_regressions -v
git add toram_discord/skill_ui.py toram_discord/skill_detail_pages.py tests/test_discord_skill_detail_pages.py tests/test_discord_skill_search.py tests/test_skill_tree_listing.py tests/test_discord_skill_review_regressions.py
git commit -m "feat: refresh skill detail presentation"
```

---

### Task 4: Real-corpus audit and full regression verification

**Files:**
- Modify/Test: `tests/test_discord_skill_icons.py`
- Modify/Test: `tests/test_skill_tree_listing.py`
- Modify/Test: `tests/test_discord_skill_search.py`

**Interfaces:**
- Consumes checked-in `skills.sqlite` and `coryn_skill_icons`.
- Produces integration confidence only; no new production behavior.

- [ ] **Step 1: Exercise icon lookup across the real skill corpus**

Iterate every repository tree/skill and call `DEFAULT_SKILL_ICON_CATALOG.resolve(...)`. Assert at least one match and no exceptions. Do **not** require 100% icon coverage because missing icons are an accepted fallback.

- [ ] **Step 2: Keep hard representative asset checks**

Require:

```text
Shield Skills / Shield Bash
Shield Skills / Shield Cannon
Magic Skills / MAGIC: FINALE
```

- [ ] **Step 3: Assert Discord transport limits**

For five icon-bearing results:

```python
self.assertLessEqual(len(rendered.embeds), 6)
self.assertLessEqual(len(rendered.files), 5)
self.assertEqual(len({file.filename for file in rendered.files}), len(rendered.files))
```

Detail pages must remain within the existing title/description/field/count/6000-character budgets.

- [ ] **Step 4: Run all focused skill tests**

```bash
python -m unittest tests.test_discord_skill_icons -v
python -m unittest tests.test_skill_tree_listing -v
python -m unittest tests.test_discord_skill_search -v
python -m unittest tests.test_discord_skill_detail_pages -v
python -m unittest tests.test_discord_skill_review_regressions -v
```

- [ ] **Step 5: Run the complete repository suite**

```bash
python -m unittest discover -s tests -p 'test_*.py' -v
```

Expected: all tests PASS.

- [ ] **Step 6: Visual Discord smoke test**

```bash
python discord_bot.py
```

Test:

```text
@Bot skill tree shield
@Bot skill shield
@Bot skill shield bash
@Bot skill magic finale
```

Confirm list/search pages are header + cards, icons appear when present, selecting a skill gives one clean detail embed with its icon, and Back restores the exact prior card page.

- [ ] **Step 7: Scope/cleanliness check**

```bash
git status --short
git diff --check main...HEAD
git log --oneline main..HEAD
```

Expected production changes only under:

```text
toram_discord/skill_icons.py
toram_discord/skill_ui.py
toram_discord/skill_detail_pages.py
toram_discord/app.py
```

No changes to `coryn_skill_icons`, raw skills, skill DB, parser, search service, item/stat, or Qwen code.

- [ ] **Step 8: Final review gate**

Review against `docs/superpowers/specs/2026-08-13-skill-ui-refresh-design.md`; all acceptance criteria must be satisfied before integration.
