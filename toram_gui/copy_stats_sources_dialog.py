from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
)

from toram_data import ItemDraft, ItemLookup, ItemRepository
from toram_data.aliases import normalize_name


SCOPE_SAME_TYPE = "Same Item Type"
SCOPE_ALL_ITEMS = "All Items"


class CopyStatsSourcesDialog(QDialog):
    def __init__(self, repository: ItemRepository, target: ItemDraft, parent=None) -> None:
        super().__init__(parent)
        self.repository = repository
        self.target = target
        self._items = repository.list_items()
        self._visible: list[ItemLookup] = []

        self.setWindowTitle("Copy Stats + Sources")
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search by item name or ID")
        self.scope_combo = QComboBox()
        self.scope_combo.addItems([SCOPE_SAME_TYPE, SCOPE_ALL_ITEMS])
        if not target.item_type.strip():
            self.scope_combo.setCurrentText(SCOPE_ALL_ITEMS)

        self.list_widget = QListWidget()
        self.buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.copy_button = self.buttons.addButton("Copy", QDialogButtonBox.AcceptRole)
        self.copy_button.setEnabled(False)

        form = QFormLayout()
        form.addRow("Search:", self.search_edit)
        form.addRow("Scope:", self.scope_combo)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.list_widget)
        layout.addWidget(self.buttons)

        self.search_edit.textChanged.connect(self._apply_filter)
        self.scope_combo.currentTextChanged.connect(self._apply_filter)
        self.list_widget.itemSelectionChanged.connect(self._selection_changed)
        self.buttons.rejected.connect(self.reject)
        self.copy_button.clicked.connect(self.accept)
        self._apply_filter()

    def _matches_search(self, item: ItemLookup, text: str) -> bool:
        needle = normalize_name(text)
        if not needle:
            return True
        return needle in normalize_name(item.name) or str(item.id).startswith(text.strip())

    def _include_item(self, item: ItemLookup) -> bool:
        if not self.target.is_new and item.id == self.target.id:
            return False
        if self.scope_combo.currentText() == SCOPE_SAME_TYPE and self.target.item_type.strip():
            if item.item_type != self.target.item_type:
                return False
        return self._matches_search(item, self.search_edit.text())

    def _apply_filter(self, *_args) -> None:
        self._visible = [item for item in self._items if self._include_item(item)]
        self.list_widget.clear()
        for item in self._visible:
            row = QListWidgetItem(f"{item.id}  {item.name}")
            row.setData(Qt.UserRole, item.id)
            row.setToolTip(item.item_type)
            self.list_widget.addItem(row)
        self._selection_changed()

    def _selection_changed(self) -> None:
        self.copy_button.setEnabled(self.list_widget.currentItem() is not None)

    def selected_source_item_id(self) -> int | None:
        row = self.list_widget.currentItem()
        return None if row is None else int(row.data(Qt.UserRole))

    def visible_source_ids(self) -> list[int]:
        return [item.id for item in self._visible]
