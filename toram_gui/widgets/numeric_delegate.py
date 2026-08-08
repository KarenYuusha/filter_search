from __future__ import annotations

from PySide6.QtGui import QDoubleValidator
from PySide6.QtWidgets import QLineEdit, QStyledItemDelegate


class NumericDelegate(QStyledItemDelegate):
    """Table editor accepting blank or finite decimal input."""

    def createEditor(self, parent, option, index):
        editor = QLineEdit(parent)
        validator = QDoubleValidator(editor)
        validator.setNotation(QDoubleValidator.StandardNotation)
        editor.setValidator(validator)
        return editor
