from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QDoubleValidator, QIntValidator
from PySide6.QtWidgets import QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QTextEdit, QVBoxLayout, QWidget

from toram_data import ItemDraft, ItemRepository
from toram_data.aliases import ITEM_TYPE_ALIASES
from .widgets.searchable_combo import SearchableComboBox


class BasicInfoTab(QWidget):
    deleteRequested = Signal()

    def __init__(self, repository: ItemRepository, parent=None) -> None:
        super().__init__(parent)
        self.repository = repository
        self.draft: ItemDraft | None = None
        self._last_valid_type = ""

        self.id_edit = QLineEdit()
        self.id_edit.setValidator(QIntValidator(1, 2_147_483_647, self))
        self.name_edit = QLineEdit()
        self.item_type_combo = SearchableComboBox()
        self.sell_price_edit = QLineEdit()
        self.process_material_edit = QLineEdit()
        self.process_amount_edit = QLineEdit()
        self.badge_edit = QLineEdit()
        self.note_edit = QTextEdit()
        self.api_url_edit = QLineEdit()
        self.page_url_edit = QLineEdit()
        self.json_path_edit = QLineEdit()
        self.json_path_edit.setReadOnly(True)
        self.type_error = QLabel()
        self.type_error.setStyleSheet("color: palette(highlight);")
        self.delete_button = QPushButton("Delete Item")
        self.delete_button.setProperty("destructive", True)

        numeric = QDoubleValidator(self)
        numeric.setNotation(QDoubleValidator.StandardNotation)
        self.sell_price_edit.setValidator(numeric)
        numeric2 = QDoubleValidator(self)
        numeric2.setNotation(QDoubleValidator.StandardNotation)
        self.process_amount_edit.setValidator(numeric2)

        form = QFormLayout()
        form.addRow("ID", self.id_edit)
        form.addRow("Name", self.name_edit)
        type_wrap = QWidget()
        type_layout = QVBoxLayout(type_wrap)
        type_layout.setContentsMargins(0, 0, 0, 0)
        type_layout.addWidget(self.item_type_combo)
        type_layout.addWidget(self.type_error)
        form.addRow("Item Type", type_wrap)
        form.addRow("Sell price", self.sell_price_edit)
        form.addRow("Process material", self.process_material_edit)
        form.addRow("Process amount", self.process_amount_edit)
        form.addRow("Badge", self.badge_edit)
        form.addRow("Note", self.note_edit)
        form.addRow("API URL", self.api_url_edit)
        form.addRow("Page URL", self.page_url_edit)
        form.addRow("JSON path", self.json_path_edit)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        bottom.addWidget(self.delete_button)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addStretch(1)
        layout.addLayout(bottom)

        self.item_type_combo.set_values(repository.list_item_types(), aliases=ITEM_TYPE_ALIASES)
        if self.item_type_combo.lineEdit() is not None:
            self.item_type_combo.lineEdit().editingFinished.connect(self._commit_item_type)
        self.id_edit.editingFinished.connect(self._commit_id)
        self.delete_button.clicked.connect(self.deleteRequested)

    @staticmethod
    def _num_text(value: float | None) -> str:
        if value is None:
            return ""
        return str(int(value)) if float(value).is_integer() else str(value)

    @staticmethod
    def _nullable_number(text: str) -> float | None:
        value = text.strip()
        return None if not value else float(value)

    def set_draft(self, draft: ItemDraft | None) -> None:
        self.draft = draft
        enabled = draft is not None
        self.setEnabled(enabled)
        if draft is None:
            for widget in (self.id_edit, self.name_edit, self.sell_price_edit, self.process_material_edit,
                           self.process_amount_edit, self.badge_edit, self.api_url_edit, self.page_url_edit,
                           self.json_path_edit):
                widget.clear()
            self.note_edit.clear()
            self.item_type_combo.setCurrentIndex(-1)
            self.delete_button.setEnabled(False)
            return
        self.id_edit.setText(str(draft.id))
        self.id_edit.setReadOnly(not draft.is_new)
        self.name_edit.setText(draft.name)
        self._last_valid_type = draft.item_type
        if draft.item_type in self.item_type_combo.values():
            self.item_type_combo.setCurrentText(draft.item_type)
        else:
            self.item_type_combo.setCurrentIndex(-1)
            if self.item_type_combo.lineEdit() is not None:
                self.item_type_combo.lineEdit().setText(draft.item_type)
        self.sell_price_edit.setText(self._num_text(draft.sell_price))
        self.process_material_edit.setText(draft.process_material or "")
        self.process_amount_edit.setText(self._num_text(draft.process_amount))
        self.badge_edit.setText(draft.badge or "")
        self.note_edit.setPlainText(draft.note or "")
        self.api_url_edit.setText(draft.api_url or "")
        self.page_url_edit.setText(draft.page_url or "")
        self.json_path_edit.setText(draft.json_path)
        self.delete_button.setEnabled(not draft.is_new)
        self.type_error.clear()

    def _commit_id(self) -> None:
        if self.draft is None or not self.draft.is_new:
            return
        text = self.id_edit.text().strip()
        if text:
            self.draft.set_new_item_id(int(text))
            self.json_path_edit.setText(self.draft.json_path)

    def _commit_item_type(self) -> None:
        if self.draft is None:
            return
        if self.item_type_combo.resolve_typed_text():
            self._last_valid_type = self.item_type_combo.currentText()
            self.draft.item_type = self._last_valid_type
            self.type_error.clear()
            return
        self.type_error.setText("Choose an existing item type")
        if self._last_valid_type in self.item_type_combo.values():
            self.item_type_combo.setCurrentText(self._last_valid_type)

    def flush_to_draft(self) -> None:
        if self.draft is None:
            return
        self._commit_id()
        self._commit_item_type()
        self.draft.name = self.name_edit.text().strip()
        if self.item_type_combo.currentText() in self.item_type_combo.values():
            self.draft.item_type = self.item_type_combo.currentText()
        self.draft.sell_price = self._nullable_number(self.sell_price_edit.text())
        self.draft.process_material = self.process_material_edit.text().strip() or None
        self.draft.process_amount = self._nullable_number(self.process_amount_edit.text())
        self.draft.badge = self.badge_edit.text().strip() or None
        self.draft.note = self.note_edit.toPlainText().strip() or None
        self.draft.api_url = self.api_url_edit.text().strip() or None
        self.draft.page_url = self.page_url_edit.text().strip() or None

    def set_validation_errors(self, issues) -> None:
        widgets = [self.id_edit, self.name_edit, self.item_type_combo, self.sell_price_edit,
                   self.process_amount_edit, self.api_url_edit, self.page_url_edit]
        for widget in widgets:
            widget.setProperty("validationError", False)
            widget.style().unpolish(widget); widget.style().polish(widget)
        for issue in issues:
            targets = []
            if issue.code == "missing_name": targets = [self.name_edit]
            elif issue.code == "missing_item_type": targets = [self.item_type_combo]
            elif issue.code == "duplicate_item_id": targets = [self.id_edit]
            elif issue.code in {"invalid_number", "non_finite_number"}: targets = [self.sell_price_edit, self.process_amount_edit]
            elif issue.code == "invalid_url": targets = [self.api_url_edit, self.page_url_edit]
            for widget in targets:
                widget.setProperty("validationError", True)
                widget.style().unpolish(widget); widget.style().polish(widget)
