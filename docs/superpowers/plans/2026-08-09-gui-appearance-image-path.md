# GUI Appearance Image Path Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make images selected in `edit_items_gui.py` save into the same `coryn_data/appearance/<item-type>/<item-id>-<item-name>/` hierarchy used by the existing appearance dataset.

**Architecture:** Keep image placement owned by `ManagedImageStore`. Derive `coryn_data` from the SQLite path (`coryn_data/database/items.sqlite`), slug item type/name with the existing filename-safe normalization style, and have `EditorService` pass item metadata to the store. Store `ImageDraft.local_path` relative to `coryn_data`, e.g. `appearance/1-handed-sword/1044-rapier/00-photo.jpg`.

**Tech Stack:** Python 3.10+, pathlib, unittest, existing `toram_data` models/services.

## Global Constraints

- Do not change the SQLite schema.
- Do not move or rewrite existing scraped images.
- Keep existing collision handling, staging, hashing, rollback, and deletion safety.
- New editor-managed images must live under `coryn_data/appearance`.
- Existing filename generation remains unchanged in this fix.

---

### Task 1: Define appearance destination behavior

**Files:**
- Create: `tests/test_managed_image_store_appearance.py`
- Modify: `toram_data/images.py`

**Interfaces:**
- `ManagedImageStore.stage(item_id: int, item_type: str, item_name: str, images: list[ImageDraft]) -> PreparedImageBatch`
- Stored `local_path` is relative to `coryn_data`.

- [ ] **Step 1: Write failing tests** for `1 Handed Sword` + `Rapier`, asserting the prepared path is `appearance/1-handed-sword/1044-rapier/...` and `materialize()` creates the file there.
- [ ] **Step 2: Run the focused test and confirm the old implementation fails** because it only accepts `(item_id, images)` and targets `database/item_images`.
- [ ] **Step 3: Implement the minimal path fix** in `ManagedImageStore`, including a reusable slug helper for directory components.
- [ ] **Step 4: Run the focused test and confirm it passes.**

### Task 2: Pass item metadata from editor saves

**Files:**
- Modify: `toram_data/editor_service.py`
- Test: `tests/test_managed_image_store_appearance.py`

**Interfaces:**
- `EditorService._save()` calls `stage(draft.id, draft.item_type, draft.name, draft.images)`.

- [ ] **Step 1: Add a regression assertion** using a recording image-store fake to require item ID/type/name to be passed during save.
- [ ] **Step 2: Confirm the assertion fails against the old call shape.**
- [ ] **Step 3: Update `EditorService._save()` to pass the draft metadata.**
- [ ] **Step 4: Run focused tests and confirm they pass.**

### Task 3: Preserve managed deletion semantics

**Files:**
- Modify: `toram_data/images.py`
- Test: `tests/test_managed_image_store_appearance.py`

**Interfaces:**
- `managed_root == coryn_data/appearance`
- `_managed_candidate()` and cleanup only treat paths under `appearance` as managed.
- `delete_item_directory()` targets the current item's appearance directory rather than `appearance/<item_id>`.

- [ ] **Step 1: Add tests** proving appearance paths are managed, old `database/item_images/...` paths are external, and item-directory cleanup uses the slugged item folder.
- [ ] **Step 2: Update deletion helpers** so save/edit/delete behavior remains safe under the new hierarchy.
- [ ] **Step 3: Run all focused image-store tests.**

### Task 4: Verify and review

**Files:**
- Review all changed files.

- [ ] **Step 1: Run the focused tests fresh.**
- [ ] **Step 2: Run the repository test suite if the environment supports the project dependencies.**
- [ ] **Step 3: Compare the feature branch against `main` and confirm only the intended files changed.**
- [ ] **Step 4: Open a PR documenting the path migration behavior and verification evidence.**
