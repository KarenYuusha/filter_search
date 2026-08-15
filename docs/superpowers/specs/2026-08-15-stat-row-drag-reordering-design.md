# Stat Row Drag Reordering Design

## Goal

Allow users to rearrange stat order in the Stats editor by dragging any selected stat row up or down. The reordered sequence must be reflected immediately in the in-memory `ItemDraft.stats` list and persist through the existing Save flow without changing the database schema.

## Current Context

`edit_items_gui.py` only starts the PySide6 editor. The Stats UI is implemented in `toram_gui/stats_tab.py` using a `QTableWidget` with four columns: stat name, amount, condition, and review status. The table displays `draft.stats` in list order.

The repository writes stat positions by enumerating `draft.stats` when saving. Therefore, list order is already the canonical persisted order; no new position field or schema migration is required.

## User Interaction

- Clicking any cell selects its full row.
- A selected row can be dragged vertically within the Stats table.
- Qt's standard drop indicator should show the intended insertion position.
- Dropping a row above or below another row moves the complete stat entry, including its name, amount, condition, and review state.
- The moved row remains selected after the operation.
- Reordering marks the draft as changed through the existing `draftChanged` signal.
- Reordering does not save automatically; persistence still occurs only when the user clicks Save.

## Implementation Approach

Use a small `QTableWidget` subclass dedicated to whole-row drag-and-drop behavior. The subclass should translate a completed internal drag into a logical row move request rather than relying on Qt to move individual table items and embedded widgets itself.

This is preferred over generic `InternalMove` because the Condition column contains `QComboBox` cell widgets. Rebuilding the table from the reordered draft avoids mismatches between table items and embedded widgets.

### Drag-aware table responsibilities

The custom table should:

1. Enable internal dragging and row selection.
2. Track the source row at drag start.
3. Resolve the target insertion row at drop time.
4. Normalize the target when dragging downward.
5. Emit a row-move signal/callback containing the source row and the **final destination index in the list after the source row has been removed**. For example, moving row `0` to the end of a four-row list emits destination index `3`.
6. Avoid directly mutating `ItemDraft` or owning application state.

### `StatsTab` responsibilities

`StatsTab` should handle the row-move request by:

1. Ignoring invalid or no-op moves.
2. Moving the matching `StatDraft` inside `draft.stats` using `stat = draft.stats.pop(source)` followed by `draft.stats.insert(destination, stat)`, where `destination` already uses the post-removal index semantics defined above.
3. Rebuilding the table from the updated draft while `_loading` is active so existing item/condition change handlers do not fire during reconstruction.
4. Restoring selection to the moved row at `destination`.
5. Refreshing `_previous_names` through the normal row-building path.
6. Emitting `draftChanged` exactly once for the completed reorder.

## Data Flow

1. User drags a stat row.
2. The table calculates `(source_row, destination_row)` using post-removal destination semantics.
3. `StatsTab` reorders `draft.stats`.
4. `StatsTab` redraws table rows from the reordered list.
5. `draftChanged` marks the editor session dirty.
6. On Save, the existing repository code enumerates `draft.stats` and writes sequential `position` values.
7. Reloading the item returns stats in the saved order because item stats are loaded with `ORDER BY position, id`.

## Edge Cases

- Dragging a row onto its current location is a no-op and should not emit `draftChanged`.
- Dragging the first row to the end and the last row to the beginning must work.
- A table with zero or one row should remain usable and should not produce invalid moves.
- Reordering must preserve each `StatDraft` object and all attached condition metadata; only list position changes.
- The reserved `Upgrade for` relationship remains unaffected because it is not displayed in the Stats table and is saved separately after normal stats.
- Existing add, remove, edit, condition-resolution, validation, discard, and Save behavior should remain unchanged.

## Testing

Add focused GUI/unit tests around the row-reorder handler and, where practical, the drag-aware table's index normalization.

Required cases:

- move first stat to last;
- move last stat to first;
- move a middle stat upward and downward;
- no-op move leaves order unchanged and does not emit a change signal;
- amount and condition data stay attached to the same stat after a reorder;
- a reorder marks the draft/session dirty through `draftChanged`;
- save/reload preserves the reordered sequence through the existing repository position logic.

## Files Expected to Change

- `toram_gui/stats_tab.py` — add whole-row drag behavior and draft synchronization.
- One or more existing/new GUI-focused test files under `tests/` — verify reordering and persistence behavior.

No database schema or migration files should change.
