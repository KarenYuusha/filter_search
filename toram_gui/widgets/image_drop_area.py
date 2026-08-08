from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QLabel

SUPPORTED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


class ImageDropArea(QLabel):
    pathsDropped = Signal(list)

    def __init__(self, parent=None) -> None:
        super().__init__("Drop images here")
        self.setAcceptDrops(True)
        self.setMinimumHeight(72)
        self.setStyleSheet("QLabel { border: 1px dashed palette(mid); padding: 12px; }")

    @staticmethod
    def _acceptable_paths(mime_data) -> list[Path]:
        result: list[Path] = []
        if not mime_data.hasUrls():
            return result
        for url in mime_data.urls():
            if not url.isLocalFile():
                continue
            path = Path(url.toLocalFile())
            if path.is_file() and path.suffix.casefold() in SUPPORTED_IMAGE_SUFFIXES:
                result.append(path)
        return result

    def dragEnterEvent(self, event) -> None:
        if self._acceptable_paths(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:
        paths = self._acceptable_paths(event.mimeData())
        if paths:
            self.pathsDropped.emit(paths)
            event.acceptProposedAction()
        else:
            event.ignore()
