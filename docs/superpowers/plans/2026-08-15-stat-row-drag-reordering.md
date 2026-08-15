# Stat Row Drag Reordering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users drag any stat row in the Stats editor to rearrange stat order, keep the full `StatDraft` attached to its condition/amount data, and persist the new order through the existing Save path.

**Architecture:** Add a focused `StatTableWidget` subclass in `toram_gui/stats_tab.py` that owns only drag/drop index calculation and emits a logical whole-row move signal. `StatsTab` remains the owner of application state: it moves the corresponding `StatDraft` inside `draft.stats`, rebuilds the table so embedded condition combo boxes stay aligned, restores row selection, and emits `draftChanged` once. The repository already persists list order by enumerating `draft.stats`, so no schema or repository production change is required.

**Tech Stack:** Python 3.12+, PySide6 6.11+, `unittest`, SQLite-backed `ItemRepository`.

## Global Constraints

- Clicking any cell selects its full row.
- A selected row can be dragged vertically within the Stats table.
- The drop target uses the final destination index after the source row has been removed.
- The moved row remains selected after a successful move.
- Reordering updates `ItemDraft.stats` immediately and emits `draftChanged` exactly once.
- Reordering does not save automatically; persistence happens through the existing Save flow.
- The Condition column's `QComboBox` cell widgets must remain attached to the same `StatDraft` after reordering.
- `Upgrade for` remains excluded from the Stats table and is unaffected.
- No database schema or migration changes.

---

### Task 1: Add failing tests for logical stat reordering

**Files:**
- Create: `tests/test_stats_tab_reordering.py`
- Read/Use: `toram_gui/stats_tab.py`
- Read/Use: `toram_data/models.py`

**Interfaces:**
- Consumes: `StatsTab.set_draft(draft: ItemDraft | None) -> None`, `StatsTab.draftChanged`, `ItemDraft.stats: list[StatDraft]`.
- Produces: tests that define the required `StatsTab._move_stat_row(source: int, destination: int) -> None` behavior.

- [ ] **Step 1: Write the failing GUI-state tests**

Create `tests/test_stats_tab_reordering.py` with an offscreen Qt application and a lightweight repository stub that only implements `list_stat_names()`:

```python
from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from toram_data import ConditionDraft, ItemDraft, StatDraft
from toram_gui.stats_tab import StatsTab


class _RepositoryStub:
    def list_stat_names(self) -> list[str]:
        return ["ATK", "Critical Rate", "MaxHP", "Physical Pierce"]


class StatsTabReorderingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.tab = StatsTab(_RepositoryStub())
        self.draft = ItemDraft.new(item_id=999_001, schema_version=1)
        self.draft.stats = [
            StatDraft("ATK", 10.0, ConditionDraft(mode="known", slugs=("main_weapon",))),
            StatDraft("Critical Rate", 20.0),
            StatDraft("MaxHP", 30.0, ConditionDraft(mode="free_text", text="custom", needs_review=True)),
            StatDraft("Physical Pierce", 40.0),
        ]
        self.tab.set_draft(self.draft)

    def tearDown(self) -> None:
        self.tab.deleteLater()
        self.app.processEvents()

    def names(self) -> list[str]:
        return [stat.stat_name for stat in self.draft.stats]

    def test_move_first_stat_to_last(self) -> None:
        self.tab._move_stat_row(0, 3)
        self.assertEqual(
            self.names(),
            ["Critical Rate", "MaxHP", "Physical Pierce", "ATK"],
        )
        self.assertEqual(self.tab.table.currentRow(), 3)

    def test_move_last_stat_to_first(self) -> None:
        self.tab._move_stat_row(3, 0)
        self.assertEqual(
            self.names(),
            ["Physical Pierce", "ATK", "Critical Rate", "MaxHP"],
        )
        self.assertEqual(self.tab.table.currentRow(), 0)

    def test_move_middle_stat_both_directions(self) -> None:
        self.tab._move_stat_row(2, 1)
        self.assertEqual(
            self.names(),
            ["ATK", "MaxHP", "Critical Rate", "Physical Pierce"],
        )
        self.tab._move_stat_row(1, 2)
        self.assertEqual(
            self.names(),
            ["ATK", "Critical Rate", "MaxHP", "Physical Pierce"],
        )

    def test_no_op_does_not_emit_change(self) -> None:
        emissions = 0

        def changed() -> None:
            nonlocal emissions
            emissions += 1

        self.tab.draftChanged.connect(changed)
        self.tab._move_stat_row(1, 1)
        self.assertEqual(self.names(), ["ATK", "Critical Rate", "MaxHP", "Physical Pierce"])
        self.assertEqual(emissions, 0)

    def test_move_preserves_stat_object_data_and_emits_once(self) -> None:
        moved = self.draft.stats[2]
        emissions = 0

        def changed() -> None:
            nonlocal emissions
            emissions += 1

        self.tab.draftChanged.connect(changed)
        self.tab._move_stat_row(2, 0)

        self.assertIs(self.draft.stats[0], moved)
        self.assertEqual(self.draft.stats[0].amount, 30.0)
        self.assertEqual(self.draft.stats[0].condition.text, "custom")
        self.assertTrue(self.draft.stats[0].condition.needs_review)
        self.assertEqual(self.tab.table.item(0, self.tab.COL_REVIEW).text(), "Needs review")
        self.assertEqual(emissions, 1)

    def test_invalid_move_is_ignored(self) -> None:
        before = list(self.draft.stats)
        self.tab._move_stat_row(-1, 0)
        self.tab._move_stat_row(0, 99)
        self.assertEqual(self.draft.stats, before)
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
QT_QPA_PLATFORM=offscreen uv run python -m unittest tests.test_stats_tab_reordering -v
```

Expected: FAIL/ERROR because `StatsTab` has no `_move_stat_row` method yet.

- [ ] **Step 3: Commit only the failing tests**

```bash
git add tests/test_stats_tab_reordering.py
git commit -m "test: define stat row reorder behavior"
```

---

### Task 2: Implement drag-aware whole-row movement

**Files:**
- Modify: `toram_gui/stats_tab.py`
- Test: `tests/test_stats_tab_reordering.py`

**Interfaces:**
- Consumes: `StatsTab._move_stat_row(source: int, destination: int) -> None` contract from Task 1.
- Produces: `StatTableWidget.rowMoveRequested = Signal(int, int)` where destination is the final post-removal list index; `StatsTab._move_stat_row(source: int, destination: int) -> None`.

- [ ] **Step 1: Add the drag-aware table class**

Update imports to include `QAbstractItemView`, `QDropEvent`, and `QPoint`, then define a focused table subclass before `StatsTab`:

```python
from PySide6.QtCore import QPoint, QSignalBlocker, Qt, Signal
from PySide6.QtGui import QDropEvent
from PySide6.QtWidgets import QAbstractItemView, ...


class StatTableWidget(QTableWidget):
    rowMoveRequested = Signal(int, int)

    def __init__(self, rows: int, columns: int, parent=None) -> None:
        super().__init__(rows, columns, parent)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setDefaultDropAction(Qt.MoveAction)

    def _destination_for_drop(self, position: QPoint, source: int) -> int:
        row_count = self.rowCount()
        if row_count <= 1:
            return source

        index = self.indexAt(position)
        if not index.isValid():
            insertion = row_count
        else:
            target_row = index.row()
            rect = self.visualRect(index)
            insertion = target_row + (1 if position.y() >= rect.center().y() else 0)

        if insertion > source:
            insertion -= 1
        return max(0, min(insertion, row_count - 1))

    def dropEvent(self, event: QDropEvent) -> None:
        source = self.currentRow()
        if not (0 <= source < self.rowCount()):
            event.ignore()
            return

        destination = self._destination_for_drop(event.position().toPoint(), source)
        if destination == source:
            event.ignore()
            return

        self.rowMoveRequested.emit(source, destination)
        event.acceptProposedAction()
```

Do not call `super().dropEvent(event)`: Qt must not directly move individual `QTableWidgetItem`s or the embedded condition combo boxes.

- [ ] **Step 2: Wire `StatsTab` to the logical move signal**

Replace:

```python
self.table = QTableWidget(0, 4)
```

with:

```python
self.table = StatTableWidget(0, 4)
```

Connect the signal during initialization:

```python
self.table.rowMoveRequested.connect(self._move_stat_row)
```

- [ ] **Step 3: Add one table rebuild helper**

Refactor the existing row-building portion of `set_draft()` into a private helper so reorder uses exactly the same rendering path:

```python
def _reload_rows(self) -> None:
    self.table.setRowCount(0)
    self._previous_names.clear()
    if self.draft is None:
        return
    for stat in self.draft.stats:
        if normalize_name(stat.stat_name) == "upgrade for":
            continue
        self._append_row(stat)
```

Then make `set_draft()` call `_reload_rows()` while `_loading` is `True`.

- [ ] **Step 4: Implement the minimal state reorder handler**

Add:

```python
def _move_stat_row(self, source: int, destination: int) -> None:
    if self.draft is None:
        return
    if not (0 <= source < len(self.draft.stats)):
        return
    if not (0 <= destination < len(self.draft.stats)):
        return
    if source == destination:
        return

    stat = self.draft.stats.pop(source)
    self.draft.stats.insert(destination, stat)

    self._loading = True
    try:
        self._reload_rows()
        self.table.selectRow(destination)
        self.table.setCurrentCell(destination, self.COL_NAME)
    finally:
        self._loading = False

    self.draftChanged.emit()
```

The handler moves the existing `StatDraft` object rather than copying its values, so amount, condition metadata, `raw_conditions_json`, and `original_position` remain attached to the same stat.

- [ ] **Step 5: Run the focused tests and verify GREEN**

Run:

```bash
QT_QPA_PLATFORM=offscreen uv run python -m unittest tests.test_stats_tab_reordering -v
```

Expected: all tests PASS.

- [ ] **Step 6: Run existing editor-related tests/full suite**

Run:

```bash
QT_QPA_PLATFORM=offscreen uv run python -m unittest discover -s tests -p "test_*.py" -v
```

Expected: PASS. If unrelated environment-heavy tests cannot run, record the exact failure and still run every GUI/editor-focused test available.

- [ ] **Step 7: Commit implementation**

```bash
git add toram_gui/stats_tab.py tests/test_stats_tab_reordering.py
git commit -m "feat: drag to reorder item stats"
```

---

### Task 3: Verify drop-index normalization and persistence

**Files:**
- Modify: `tests/test_stats_tab_reordering.py`
- Read/Use: `toram_gui/stats_tab.py`
- Read/Use: `toram_data/repository.py`

**Interfaces:**
- Consumes: `StatTableWidget._destination_for_drop(position: QPoint, source: int) -> int`; `ItemRepository.update_item(draft: ItemDraft) -> None`; `ItemRepository.load_item_draft(item_id: int) -> ItemDraft`.
- Produces: regression coverage proving downward/upward drop normalization and database persistence of reordered list order.

- [ ] **Step 1: Add focused destination-index tests**

Extend `tests/test_stats_tab_reordering.py` with tests that resize/show the table, process events, then use row visual rectangles to validate insertion semantics:

```python
def test_drop_destination_normalizes_downward_move(self) -> None:
    self.tab.table.resize(700, 400)
    self.tab.table.show()
    self.app.processEvents()

    last_rect = self.tab.table.visualRect(self.tab.table.model().index(3, 0))
    point_below_last_center = last_rect.center()
    point_below_last_center.setY(last_rect.bottom())

    self.assertEqual(
        self.tab.table._destination_for_drop(point_below_last_center, source=0),
        3,
    )


def test_drop_destination_keeps_upward_index(self) -> None:
    self.tab.table.resize(700, 400)
    self.tab.table.show()
    self.app.processEvents()

    first_rect = self.tab.table.visualRect(self.tab.table.model().index(0, 0))
    point_above_first_center = first_rect.center()
    point_above_first_center.setY(first_rect.top())

    self.assertEqual(
        self.tab.table._destination_for_drop(point_above_first_center, source=3),
        0,
    )
```

- [ ] **Step 2: Run the two destination tests**

Run:

```bash
QT_QPA_PLATFORM=offscreen uv run python -m unittest \
  tests.test_stats_tab_reordering.StatsTabReorderingTests.test_drop_destination_normalizes_downward_move \
  tests.test_stats_tab_reordering.StatsTabReorderingTests.test_drop_destination_keeps_upward_index -v
```

Expected: PASS. If either fails, fix only `_destination_for_drop` until both insertion directions match the post-removal destination contract.

- [ ] **Step 3: Add a repository persistence regression**

Add a second `unittest.TestCase` in the same file using a temporary copy of `coryn_data/database/items.sqlite` so the real database is never modified:

```python
import shutil
import tempfile
from pathlib import Path

from toram_data import ItemRepository


class StatOrderPersistenceTests(unittest.TestCase):
    def test_update_and_reload_preserves_reordered_stats(self) -> None:
        source_db = Path("coryn_data/database/items.sqlite")
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "items.sqlite"
            shutil.copy2(source_db, database)
            repository = ItemRepository(database)
            try:
                item = next(
                    candidate
                    for candidate in repository.list_items()
                    if len(repository.load_item_draft(candidate.id).stats) >= 2
                )
                draft = repository.load_item_draft(item.id)
                original_names = [stat.stat_name for stat in draft.stats]
                draft.stats = draft.stats[1:] + draft.stats[:1]

                repository.update_item(draft)
                reloaded = repository.load_item_draft(item.id)

                self.assertEqual(
                    [stat.stat_name for stat in reloaded.stats],
                    original_names[1:] + original_names[:1],
                )
            finally:
                repository.close()
```

This test relies only on existing repository behavior and confirms the GUI needs no new persistence mechanism.

- [ ] **Step 4: Run all reordering tests**

Run:

```bash
QT_QPA_PLATFORM=offscreen uv run python -m unittest tests.test_stats_tab_reordering -v
```

Expected: PASS.

- [ ] **Step 5: Run static and regression verification**

Run:

```bash
uv run python -m py_compile toram_gui/stats_tab.py tests/test_stats_tab_reordering.py
git diff --check
QT_QPA_PLATFORM=offscreen uv run python -m unittest discover -s tests -p "test_*.py" -v
```

Expected: compile PASS, `git diff --check` produces no output, and the full suite PASS (or any environment-specific unrelated failure is documented exactly).

- [ ] **Step 6: Commit final regression coverage**

```bash
git add tests/test_stats_tab_reordering.py
git commit -m "test: cover stat reorder persistence"
```

---

## Final Verification Checklist

- [ ] Drag can start from any cell because the table selects whole rows and enables internal dragging.
- [ ] Dragging first → last and last → first results in the expected `draft.stats` order.
- [ ] Downward drop index is normalized after source removal.
- [ ] Name, amount, condition, review state, raw condition data, and object identity stay together.
- [ ] The moved row remains selected.
- [ ] No-op/invalid moves do not mark the draft changed.
- [ ] Successful moves emit `draftChanged` exactly once.
- [ ] Save/reload persists the new order with existing repository position enumeration.
- [ ] `Upgrade for`, add/remove/edit/validation/discard behavior remain unchanged.
- [ ] No schema or migration changes are present.
