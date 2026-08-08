from __future__ import annotations

from collections.abc import Mapping

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QCompleter

from toram_data.aliases import normalize_name


class SearchableComboBox(QComboBox):
    """Editable combo that only commits one of its configured values."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.NoInsert)
        self._aliases: dict[str, str] = {}
        self._valid_values: list[str] = []
        self._install_completer()
        if self.lineEdit() is not None:
            self.lineEdit().editingFinished.connect(self.resolve_typed_text)

    def _install_completer(self) -> None:
        completer = QCompleter(self.model(), self)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        completer.setCompletionMode(QCompleter.PopupCompletion)
        self.setCompleter(completer)

    def set_values(self, values: list[str], *, aliases: Mapping[str, str] | None = None) -> None:
        current = self.currentText()
        self.blockSignals(True)
        self.clear()
        self._valid_values = list(dict.fromkeys(values))
        self.addItems(self._valid_values)
        self._aliases = {normalize_name(k): v for k, v in (aliases or {}).items()}
        if current in self._valid_values:
            self.setCurrentText(current)
        self.blockSignals(False)
        self._install_completer()

    def resolve_typed_text(self) -> bool:
        typed = self.currentText().strip()
        normalized = normalize_name(typed)
        by_normalized = {normalize_name(v): v for v in self._valid_values}
        target = by_normalized.get(normalized)
        if target is None:
            alias_target = self._aliases.get(normalized)
            if alias_target is not None:
                target = by_normalized.get(normalize_name(alias_target))
        if target is None:
            return False
        self.setCurrentText(target)
        return True

    def values(self) -> list[str]:
        return list(self._valid_values)
