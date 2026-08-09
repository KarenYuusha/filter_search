# GUI Appearance Image Path Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make images selected in `edit_items_gui.py` save into the same `coryn_data/appearance/<item-type>/<item-id>-<item-name>/` hierarchy used by the existing appearance dataset.

**Architecture:** Keep image placement owned by `ManagedImageStore`. Derive `coryn_data` from the SQLite path (`coryn_data/database/items.sqlite`), slug item type/name with the existing filename-safe normalization style, and have `EditorService` pass item metadata to the store. Preserve the GUI's established database-relative `local_path` contract, so a physical file under `coryn_data/appearance/...` is stored as `../appearance/...` and continues to resolve through `ImagesTab`.

**Tech Stack:** Python 3.10+, pathlib, unittest, existing `toram_data` models/services.

## Global Constraints

- Do not change the SQLite schema.
- Do not move or rewrite existing scraped images.
- Keep existing collision handling, staging, hashing, rollback, and deletion safety.
- New editor-managed images must physically live under `coryn_data/appearance`.
- Keep `item_images.local_path` relative to the database directory so existing GUI preview behavior remains compatible.
- Existing filename generation remains unchanged in this fix.
- Legacy GUI-managed `coryn_data/database/item_images/...` files remain eligible for safe cleanup.

---

### Task 1: Define appearance destination behavior

**Files:**
- Create: `tests/test_managed_image_store_appearance.py`
- Modify: `toram_data/images.py`

**Interfaces:**
- `ManagedImageStore.stage(item_id: int, item_type: str, item_name: str, images: list[ImageDraft]) -> PreparedImageBatch`
- Stored `local_path` for the default database layout is `../appearance/<type>/<id>-<name>/<filename>`.

- [x] **Step 1: Write failing tests** for `1 Handed Sword` + `Rapier`, asserting the prepared DB path is `../appearance/1-handed-sword/1044-rapier/...` and `materialize()` creates the physical file under `coryn_data/appearance`.
- [x] **Step 2: Run the focused reproduction and confirm the old implementation fails** because it only accepts `(item_id, images)` and targets `database/item_images`.
- [x] **Step 3: Implement the minimal path fix** in `ManagedImageStore`, including a reusable slug helper for directory components.
- [x] **Step 4: Run the focused path/materialization tests and confirm they pass.**

### Task 2: Pass item metadata from editor saves

**Files:**
- Modify: `toram_data/editor_service.py`
- Test: `tests/test_managed_image_store_appearance.py`

**Interfaces:**
- `EditorService._save()` calls `stage(draft.id, draft.item_type, draft.name, draft.images)`.

- [x] **Step 1: Add a regression assertion** using a recording image-store fake to require item ID/type/name to be passed during save.
- [x] **Step 2: Confirm the assertion fails against the old call shape.**
- [x] **Step 3: Update `EditorService._save()` to pass the draft metadata.**
- [x] **Step 4: Run the focused metadata-handoff test and confirm it passes.**

### Task 3: Preserve managed deletion semantics

**Files:**
- Modify: `toram_data/images.py`
- Test: `tests/test_managed_image_store_appearance.py`

**Interfaces:**
- New `managed_root == coryn_data/appearance`.
- Legacy managed root remains `coryn_data/database/item_images` for cleanup only.
- `_managed_candidate()` accepts safe paths under either root and rejects unrelated external paths.
- `delete_item_directory(item_id)` removes every `appearance/*/<item_id>-*` directory plus the legacy `item_images/<item_id>` directory.

- [x] **Step 1: Add tests** proving new appearance paths and legacy editor paths are managed while unrelated paths are external.
- [x] **Step 2: Update deletion helpers** so save/edit/delete behavior remains safe under both layouts and renamed/type-moved item folders are discoverable by item ID.
- [x] **Step 3: Run focused cleanup tests.**

### Task 4: Verify and review

**Files:**
- Review all changed files.

- [ ] **Step 1: Run the focused tests fresh.**
- [ ] **Step 2: Run the repository test suite if the environment supports the project dependencies.**
- [ ] **Step 3: Compare the feature branch against `main` and confirm only the intended files changed.**
- [ ] **Step 4: Open a PR documenting the path behavior and verification evidence.**
