from __future__ import annotations

import copy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCompleter, QHBoxLayout, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget

from toram_data import ItemDraft, ItemRepository, SourceDraft, generate_raw_cells_json


class SourcesTab(QWidget):
    HEADERS = ["Source Name", "Level", "Map", "Dye", "Source URL", "Lookup Error"]

    def __init__(self, repository: ItemRepository, parent=None) -> None:
        super().__init__(parent)
        self.repository = repository
        self.draft: ItemDraft | None = None
        self._baselines: list[SourceDraft] = []
        self.table = QTableWidget(0, len(self.HEADERS))
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.add_button = QPushButton("Add Source")
        self.remove_button = QPushButton("Remove Source")
        buttons = QHBoxLayout(); buttons.addWidget(self.add_button); buttons.addWidget(self.remove_button); buttons.addStretch(1)
        layout = QVBoxLayout(self); layout.addLayout(buttons); layout.addWidget(self.table)
        self.add_button.clicked.connect(self.add_source)
        self.remove_button.clicked.connect(self.remove_selected)

    def _install_completer(self, row: int, col: int, values: list[str]) -> None:
        item = self.table.item(row, col)
        if item is not None:
            item.setData(Qt.UserRole + 1, values)

    def set_draft(self, draft: ItemDraft | None) -> None:
        self.draft = draft
        self.setEnabled(draft is not None)
        self.table.setRowCount(0)
        self._baselines = []
        if draft is None: return
        for source in draft.sources:
            self._append_row(source)
            self._baselines.append(copy.deepcopy(source))

    def _append_row(self, source: SourceDraft) -> None:
        row = self.table.rowCount(); self.table.insertRow(row)
        values = [source.source_name, source.level, source.map, source.dye, source.source_url, source.lookup_error]
        for col, value in enumerate(values):
            self.table.setItem(row, col, QTableWidgetItem("" if value is None else str(value)))
        self._install_completer(row, 0, self.repository.list_source_names())
        self._install_completer(row, 2, self.repository.list_source_maps())

    def add_source(self) -> None:
        if self.draft is None: return
        source = SourceDraft()
        self.draft.sources.append(source)
        self._baselines.append(copy.deepcopy(source))
        self._append_row(source)

    def remove_selected(self) -> None:
        if self.draft is None: return
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for row in rows:
            if 0 <= row < len(self.draft.sources): self.draft.sources.pop(row)
            if 0 <= row < len(self._baselines): self._baselines.pop(row)
            self.table.removeRow(row)

    @staticmethod
    def _visible_tuple(source: SourceDraft):
        return (source.source_name, source.level, source.map, source.dye, source.source_url, source.lookup_error)

    def flush_to_draft(self) -> None:
        if self.draft is None: return
        for row in range(self.table.rowCount()):
            source = self.draft.sources[row]
            source.source_name = self.table.item(row, 0).text().strip() or None
            level = self.table.item(row, 1).text().strip()
            try: source.level = None if not level else int(level)
            except ValueError: source.level = None
            source.map = self.table.item(row, 2).text().strip() or None
            source.dye = self.table.item(row, 3).text().strip() or None
            source.source_url = self.table.item(row, 4).text().strip() or None
            source.lookup_error = self.table.item(row, 5).text().strip() or None
            baseline = self._baselines[row] if row < len(self._baselines) else SourceDraft()
            if self._visible_tuple(source) != self._visible_tuple(baseline):
                source.raw_cells_json = generate_raw_cells_json(source)
            else:
                source.raw_cells_json = baseline.raw_cells_json

    def set_validation_errors(self, issues) -> None:
        self.table.setProperty("validationError", bool(issues))
        self.table.style().unpolish(self.table); self.table.style().polish(self.table)
