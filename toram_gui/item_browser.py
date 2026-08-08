from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget

from toram_data import ItemRepository
from toram_data.aliases import ITEM_TYPE_ALIASES, is_crysta_item_type, normalize_name
from toram_data.repository import ItemLookup
from .widgets.searchable_combo import SearchableComboBox


class ItemBrowserWidget(QWidget):
    itemActivated = Signal(int)
    newItemRequested = Signal()
    filterWouldHideSelection = Signal(str, str)

    def __init__(self, repository: ItemRepository, parent=None) -> None:
        super().__init__(parent)
        self.repository = repository
        self._items: list[ItemLookup] = []
        self._visible: list[ItemLookup] = []
        self._selected_id: int | None = None
        self._pending_filter: tuple[str, str] | None = None
        self._applied_filter: tuple[str, str] = ("", "All Types")
        self._ignore_filter_signals = False

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search name or ID…")
        self.type_combo = SearchableComboBox()
        self.new_button = QPushButton("+ New Item")
        self.list_widget = QListWidget()

        type_row = QHBoxLayout()
        type_row.addWidget(QLabel("Type"))
        type_row.addWidget(self.type_combo, 1)
        layout = QVBoxLayout(self)
        layout.addWidget(self.search_edit)
        layout.addLayout(type_row)
        layout.addWidget(self.new_button)
        layout.addWidget(self.list_widget, 1)

        self.search_edit.textChanged.connect(self._filter_changed)
        self.type_combo.currentTextChanged.connect(self._filter_changed)
        if self.type_combo.lineEdit() is not None:
            self.type_combo.lineEdit().editingFinished.connect(self._resolve_type)
        self.new_button.clicked.connect(self.newItemRequested)
        self.list_widget.itemSelectionChanged.connect(self._selection_changed)
        self.reload_items()

    def _resolve_type(self) -> None:
        if self.type_combo.resolve_typed_text():
            self._filter_changed()

    @staticmethod
    def _matches_search(item: ItemLookup, text: str) -> bool:
        needle = normalize_name(text)
        if not needle:
            return True
        return needle in normalize_name(item.name) or str(item.id).startswith(text.strip())

    @staticmethod
    def _matches_type(item: ItemLookup, value: str) -> bool:
        if not value or value == "All Types":
            return True
        if value == "All Crysta":
            return is_crysta_item_type(item.item_type)
        return item.item_type == value

    def reload_items(self, select_id: int | None = None) -> None:
        self._items = self.repository.list_items()
        previous = self.type_combo.currentText() or "All Types"
        values = ["All Types", "All Crysta", *self.repository.list_item_types()]
        aliases = dict(ITEM_TYPE_ALIASES)
        aliases.update({"xtal": "All Crysta", "crysta": "All Crysta"})
        self.type_combo.set_values(values, aliases=aliases)
        self.type_combo.setCurrentText(previous if previous in values else "All Types")
        self._selected_id = select_id
        self._apply_filter()
        if select_id is not None:
            self.set_selected_item(select_id)

    def _filter_changed(self, *_args) -> None:
        if self._ignore_filter_signals:
            return
        search = self.search_edit.text()
        type_value = self.type_combo.currentText() or "All Types"
        visible_ids = {
            item.id for item in self._items
            if self._matches_search(item, search) and self._matches_type(item, type_value)
        }
        if self._selected_id is not None and self._selected_id not in visible_ids:
            self._pending_filter = (search, type_value)
            self.filterWouldHideSelection.emit(search, type_value)
            return
        self._apply_filter()

    def apply_pending_filter(self) -> None:
        self._pending_filter = None
        self._apply_filter()

    def restore_filter(self, search_text: str, type_value: str) -> None:
        self._ignore_filter_signals = True
        try:
            self.search_edit.setText(search_text)
            if type_value in self.type_combo.values():
                self.type_combo.setCurrentText(type_value)
        finally:
            self._ignore_filter_signals = False
        self._pending_filter = None
        self._apply_filter()

    def _apply_filter(self) -> None:
        search = self.search_edit.text()
        type_value = self.type_combo.currentText() or "All Types"
        self._applied_filter = (search, type_value)
        self._visible = [
            item for item in self._items
            if self._matches_search(item, search) and self._matches_type(item, type_value)
        ]
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        for item in self._visible:
            row = QListWidgetItem(f"{item.id}  {item.name}")
            row.setData(Qt.UserRole, item.id)
            row.setToolTip(item.item_type)
            self.list_widget.addItem(row)
            if item.id == self._selected_id:
                row.setSelected(True)
                self.list_widget.setCurrentItem(row)
        self.list_widget.blockSignals(False)

    def _selection_changed(self) -> None:
        row = self.list_widget.currentItem()
        if row is None:
            return
        item_id = int(row.data(Qt.UserRole))
        if item_id != self._selected_id:
            self.itemActivated.emit(item_id)

    def set_selected_item(self, item_id: int | None) -> None:
        self._selected_id = item_id
        self.list_widget.blockSignals(True)
        self.list_widget.clearSelection()
        if item_id is not None:
            for index in range(self.list_widget.count()):
                row = self.list_widget.item(index)
                if int(row.data(Qt.UserRole)) == item_id:
                    row.setSelected(True)
                    self.list_widget.setCurrentItem(row)
                    self.list_widget.scrollToItem(row)
                    break
        self.list_widget.blockSignals(False)

    def applied_filter(self) -> tuple[str, str]:
        return self._applied_filter

    def visible_items(self) -> list[ItemLookup]:
        return list(self._visible)

    def visible_item_names(self) -> list[str]:
        return [item.name for item in self._visible]
