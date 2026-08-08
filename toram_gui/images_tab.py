from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QLabel, QListView, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget

from toram_data import ImageDraft, ItemDraft
from .widgets.image_drop_area import ImageDropArea, SUPPORTED_IMAGE_SUFFIXES


class ImagesTab(QWidget):
    def __init__(self, database_path: Path, parent=None) -> None:
        super().__init__(parent)
        self.database_path = Path(database_path)
        self.draft: ItemDraft | None = None
        self.list_widget = QListWidget()
        self.list_widget.setViewMode(QListView.IconMode)
        self.list_widget.setIconSize(QSize(128, 96))
        self.list_widget.setResizeMode(QListView.Adjust)
        self.preview = QLabel("No image selected")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumHeight(220)
        self.drop_area = ImageDropArea()
        self.add_button = QPushButton("Add Images…")
        self.replace_button = QPushButton("Replace")
        self.remove_button = QPushButton("Remove")

        buttons = QHBoxLayout(); buttons.addWidget(self.add_button); buttons.addWidget(self.replace_button); buttons.addWidget(self.remove_button); buttons.addStretch(1)
        layout = QVBoxLayout(self); layout.addWidget(self.drop_area); layout.addLayout(buttons); layout.addWidget(self.list_widget, 1); layout.addWidget(self.preview)
        self.drop_area.pathsDropped.connect(self.add_paths)
        self.add_button.clicked.connect(self._choose_images)
        self.replace_button.clicked.connect(self._choose_replacement)
        self.remove_button.clicked.connect(self.remove_selected)
        self.list_widget.currentRowChanged.connect(self._show_preview)

    def set_draft(self, draft: ItemDraft | None) -> None:
        self.draft = draft
        self.setEnabled(draft is not None)
        self._refresh()

    def _image_path(self, image: ImageDraft) -> Path | None:
        if image.selected_source_path is not None:
            return Path(image.selected_source_path)
        if image.local_path:
            path = Path(image.local_path)
            if path.is_absolute(): return path
            return self.database_path.parent / path
        return None

    def _refresh(self) -> None:
        self.list_widget.clear()
        if self.draft is None:
            self.preview.setText("No image selected")
            return
        for idx, image in enumerate(self.draft.images, start=1):
            path = self._image_path(image)
            label = path.name if path is not None else f"Image {idx}"
            row = QListWidgetItem(label)
            if path is not None and path.is_file():
                pix = QPixmap(str(path))
                if not pix.isNull(): row.setIcon(QIcon(pix.scaled(128, 96, Qt.KeepAspectRatio, Qt.SmoothTransformation)))
            self.list_widget.addItem(row)
        if self.list_widget.count(): self.list_widget.setCurrentRow(0)
        else: self.preview.setText("No image selected")

    def _choose_images(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Add images", "", "Images (*.png *.jpg *.jpeg *.webp)")
        self.add_paths([Path(p) for p in paths])

    def add_paths(self, paths: list[Path]) -> None:
        if self.draft is None: return
        for path in paths:
            path = Path(path)
            if path.is_file() and path.suffix.casefold() in SUPPORTED_IMAGE_SUFFIXES:
                self.draft.images.append(ImageDraft(selected_source_path=path))
        self._refresh()

    def _choose_replacement(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Replace image", "", "Images (*.png *.jpg *.jpeg *.webp)")
        if path: self.replace_selected(Path(path))

    def replace_selected(self, path: Path) -> None:
        if self.draft is None: return
        row = self.list_widget.currentRow()
        path = Path(path)
        if not (0 <= row < len(self.draft.images)) or not path.is_file() or path.suffix.casefold() not in SUPPORTED_IMAGE_SUFFIXES:
            return
        image = self.draft.images[row]
        if image.original_local_path is None:
            image.original_local_path = image.local_path
        image.selected_source_path = path
        self._refresh(); self.list_widget.setCurrentRow(row)

    def remove_selected(self) -> None:
        if self.draft is None: return
        row = self.list_widget.currentRow()
        if 0 <= row < len(self.draft.images):
            self.draft.images.pop(row)
            self._refresh()

    def _show_preview(self, row: int) -> None:
        if self.draft is None or not (0 <= row < len(self.draft.images)):
            self.preview.setText("No image selected"); self.preview.setPixmap(QPixmap()); return
        path = self._image_path(self.draft.images[row])
        if path is None or not path.is_file():
            self.preview.setText("Image file is unavailable"); self.preview.setPixmap(QPixmap()); return
        pix = QPixmap(str(path))
        if pix.isNull():
            self.preview.setText("Could not preview image"); return
        self.preview.setText("")
        self.preview.setPixmap(pix.scaled(640, 300, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def flush_to_draft(self) -> None:
        return

    def set_validation_errors(self, issues) -> None:
        has = bool(issues)
        self.drop_area.setProperty("validationError", has)
        self.drop_area.style().unpolish(self.drop_area); self.drop_area.style().polish(self.drop_area)
