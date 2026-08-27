from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QPoint, QSignalBlocker, Qt, Signal
from PySide6.QtGui import QDropEvent
from PySide6.QtWidgets import QAbstractItemView, QComboBox, QHBoxLayout, QInputDialog, QLineEdit, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget

from toram_data import KNOWN_CONDITIONS, ItemDraft, ItemRepository, StatDraft, condition_from_slug, free_text_condition
from toram_data.aliases import STAT_ALIASES, STAT_AMBIGUOUS_GROUPS, normalize_name, normalize_stat_text, resolve_editor_value
from .widgets.numeric_delegate import NumericDelegate
from .widgets.stat_name_delegate import StatNameDelegate


class StatTableWidget(QTableWidget):
    rowMoveRequested = Signal(int, int)

    def __init__(self, rows: int, columns: int, parent=None) -> None:
        super().__init__(rows, columns, parent)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setDragEnabled(True)
        self.viewport().setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setSupportedDragActions(Qt.MoveAction)

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


class StatsTab(QWidget):
    draftChanged = Signal()
    COL_NAME, COL_AMOUNT, COL_CONDITION, COL_REVIEW = range(4)

    def __init__(self, repository: ItemRepository, parent=None,
                 confirm_resolution: Callable | None = None,
                 request_free_text: Callable | None = None) -> None:
        super().__init__(parent)
        self.repository = repository
        self.draft: ItemDraft | None = None
        self.confirm_resolution = confirm_resolution or self._default_confirm_resolution
        self.request_free_text = request_free_text or self._default_request_free_text
        self._loading = False
        self._previous_names: dict[int, str] = {}

        self.table = StatTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Stat Name", "Amount", "Condition", "Review"])
        self.table.setItemDelegateForColumn(self.COL_AMOUNT, NumericDelegate(self.table))
        self.table.setItemDelegateForColumn(self.COL_NAME, StatNameDelegate(repository.list_stat_names(), self.table))
        self.table.horizontalHeader().setStretchLastSection(True)
        self.add_button = QPushButton("Add Stat")
        self.remove_button = QPushButton("Remove Stat")
        row = QHBoxLayout(); row.addWidget(self.add_button); row.addWidget(self.remove_button); row.addStretch(1)
        layout = QVBoxLayout(self); layout.addLayout(row); layout.addWidget(self.table)
        self.add_button.clicked.connect(self.add_stat)
        self.remove_button.clicked.connect(self.remove_selected)
        self.table.rowMoveRequested.connect(self._move_stat_row)
        self.table.itemChanged.connect(self._item_changed)

    def _default_confirm_resolution(self, typed: str, candidates: tuple[str, ...], status: str) -> str | None:
        if status == "new":
            return typed if QMessageBox.question(self, "New stat", f'Create new stat "{typed}"?') == QMessageBox.Yes else None
        if len(candidates) == 1:
            candidate = candidates[0]
            return candidate if QMessageBox.question(self, "Resolve stat", f'Use "{candidate}" for "{typed}"?') == QMessageBox.Yes else None
        choice, ok = QInputDialog.getItem(self, "Choose stat", f'What does "{typed}" mean?', list(candidates), 0, False)
        return str(choice) if ok else None

    def _default_request_free_text(self, current: str) -> str | None:
        text, ok = QInputDialog.getText(self, "Condition", "Condition text:", QLineEdit.Normal, current)
        return str(text) if ok else None

    def set_draft(self, draft: ItemDraft | None) -> None:
        self.draft = draft
        self.setEnabled(draft is not None)
        self._loading = True
        try:
            self._reload_rows()
        finally:
            self._loading = False

    def _reload_rows(self) -> None:
        self.table.setRowCount(0)
        self._previous_names.clear()
        if self.draft is None:
            return
        for stat in self.draft.stats:
            if normalize_name(stat.stat_name) == "upgrade for":
                continue
            self._append_row(stat)

    def _condition_combo(self, stat: StatDraft) -> QComboBox:
        combo = QComboBox()
        combo.addItem("(None)", ("none", None))
        for definition in KNOWN_CONDITIONS:
            combo.addItem(definition.label, ("known", definition.slug))
        combo.addItem("Other…", ("free_text", None))
        index = 0
        if stat.condition.mode == "known" and stat.condition.slugs:
            slug = stat.condition.slugs[0]
            for i in range(combo.count()):
                data = combo.itemData(i)
                if data == ("known", slug): index = i; break
        elif stat.condition.mode in {"free_text", "preserve"} and stat.condition.text:
            combo.insertItem(combo.count() - 1, stat.condition.text, ("existing_text", stat.condition.text))
            index = combo.count() - 2
        combo.setCurrentIndex(index)
        combo.currentIndexChanged.connect(lambda _i, c=combo: self._condition_changed(c))
        return combo

    def _append_row(self, stat: StatDraft) -> None:
        row = self.table.rowCount(); self.table.insertRow(row)
        name = QTableWidgetItem(stat.stat_name)
        amount = QTableWidgetItem("" if stat.amount is None else (str(int(stat.amount)) if float(stat.amount).is_integer() else str(stat.amount)))
        review = QTableWidgetItem("Needs review" if stat.condition.needs_review else "")
        review.setFlags(review.flags() & ~Qt.ItemIsEditable)
        self.table.setItem(row, self.COL_NAME, name)
        self.table.setItem(row, self.COL_AMOUNT, amount)
        self.table.setCellWidget(row, self.COL_CONDITION, self._condition_combo(stat))
        self.table.setItem(row, self.COL_REVIEW, review)
        self._previous_names[row] = stat.stat_name

    def add_stat(self) -> None:
        if self.draft is None: return
        stat = StatDraft(stat_name="")
        self.draft.stats.append(stat)
        self._loading = True
        try: self._append_row(stat)
        finally: self._loading = False
        self.table.editItem(self.table.item(self.table.rowCount() - 1, self.COL_NAME))

    def remove_selected(self) -> None:
        if self.draft is None: return
        rows = sorted({index.row() for index in self.table.selectedIndexes()}, reverse=True)
        for row in rows:
            if 0 <= row < len(self.draft.stats): self.draft.stats.pop(row)
            self.table.removeRow(row)
        self._previous_names = {r: self.table.item(r, self.COL_NAME).text() for r in range(self.table.rowCount())}

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

    def _resolve_name(self, typed: str) -> str | None:
        if normalize_name(typed) == "upgrade for":
            QMessageBox.warning(self, "Reserved stat", "Upgrade for is edited in the Upgrade tab.")
            return None
        resolution = resolve_editor_value(
            typed,
            self.repository.list_stat_names(),
            aliases=STAT_ALIASES,
            ambiguous_groups=STAT_AMBIGUOUS_GROUPS,
            normalizer=normalize_stat_text,
        )
        if resolution.status in {"exact", "alias"} and resolution.candidates:
            return resolution.candidates[0]
        if resolution.status in {"ambiguous", "fuzzy"}:
            return self.confirm_resolution(typed, resolution.candidates, resolution.status)
        if resolution.status == "new":
            return self.confirm_resolution(typed, (typed,), "new")
        return None

    def _item_changed(self, item: QTableWidgetItem) -> None:
        if self._loading or self.draft is None: return
        row = item.row()
        if row >= len(self.draft.stats): return
        stat = self.draft.stats[row]
        if item.column() == self.COL_NAME:
            typed = item.text().strip()
            resolved = self._resolve_name(typed)
            if resolved is None:
                with QSignalBlocker(self.table): item.setText(self._previous_names.get(row, stat.stat_name))
                return
            stat.stat_name = resolved
            self.draftChanged.emit()
            self._previous_names[row] = resolved
            if item.text() != resolved:
                with QSignalBlocker(self.table): item.setText(resolved)
        elif item.column() == self.COL_AMOUNT:
            text = item.text().strip()
            try:
                stat.amount = None if not text else float(text)
                self.draftChanged.emit()
            except ValueError:
                pass

    def _condition_changed(self, combo: QComboBox) -> None:
        if self._loading or self.draft is None: return
        row = self.table.indexAt(combo.pos()).row()
        if row < 0:
            for r in range(self.table.rowCount()):
                if self.table.cellWidget(r, self.COL_CONDITION) is combo: row = r; break
        if not (0 <= row < len(self.draft.stats)): return
        stat = self.draft.stats[row]
        mode, value = combo.currentData()
        if mode == "none":
            from toram_data import ConditionDraft
            stat.condition = ConditionDraft()
        elif mode == "known": stat.condition = condition_from_slug(value)
        elif mode == "existing_text": pass
        elif mode == "free_text":
            text = self.request_free_text(stat.condition.text or "")
            if text is None:
                self.set_draft(self.draft); return
            stat.condition = free_text_condition(text)
        self.table.item(row, self.COL_REVIEW).setText("Needs review" if stat.condition.needs_review else "")
        self.draftChanged.emit()

    def flush_to_draft(self) -> None:
        if self.draft is None: return
        for row, stat in enumerate(self.draft.stats[:self.table.rowCount()]):
            stat.stat_name = self.table.item(row, self.COL_NAME).text().strip()
            text = self.table.item(row, self.COL_AMOUNT).text().strip()
            try: stat.amount = None if not text else float(text)
            except ValueError: pass

    def set_validation_errors(self, issues) -> None:
        self.table.setProperty("validationError", bool(issues))
        self.table.style().unpolish(self.table); self.table.style().polish(self.table)
