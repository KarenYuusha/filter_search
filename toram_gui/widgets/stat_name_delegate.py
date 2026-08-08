from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCompleter, QLineEdit, QStyledItemDelegate


class StatNameDelegate(QStyledItemDelegate):
    def __init__(self, stat_names: list[str], parent=None) -> None:
        super().__init__(parent)
        self.stat_names = stat_names

    def createEditor(self, parent, option, index):
        editor = QLineEdit(parent)
        completer = QCompleter(self.stat_names, editor)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        editor.setCompleter(completer)
        return editor
