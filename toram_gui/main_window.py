from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QInputDialog, QLabel, QMainWindow, QMessageBox, QPushButton,
    QSplitter, QTabWidget, QVBoxLayout, QWidget,
)

from toram_data import EditorService, ItemDraft, ItemRepository, ValidationReport
from .basic_tab import BasicInfoTab
from .copy_stats_sources_dialog import CopyStatsSourcesDialog
from .images_tab import ImagesTab
from .item_browser import ItemBrowserWidget
from .session import ItemEditorSession
from .sources_tab import SourcesTab
from .stats_tab import StatsTab
from .upgrade_tab import UpgradeTab


class MainWindow(QMainWindow):
    """Master-detail item editor; all persistence is delegated to EditorService."""

    def __init__(
        self,
        repository: ItemRepository,
        parent=None,
        *,
        prompt_unsaved: Callable[[], str] | None = None,
        confirm_delete: Callable[[str, str], str | None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.repository = repository
        self.service = EditorService(repository)
        self.session = ItemEditorSession(repository, self.service)
        self._prompt_unsaved_callback = prompt_unsaved
        self._confirm_delete_callback = confirm_delete
        self._closing_repository = False

        self.setWindowTitle("Toram Item Database Editor")
        self.resize(1280, 820)
        self.setStyleSheet(
            '*[validationError="true"] { border: 1px solid palette(highlight); }'
        )

        self.browser = ItemBrowserWidget(repository)
        self.browser.setMinimumWidth(300)

        self.header_label = QLabel("No item selected")
        self.tabs = QTabWidget()
        self.basic_tab = BasicInfoTab(repository)
        self.stats_tab = StatsTab(repository)
        self.upgrade_tab = UpgradeTab(repository)
        self.sources_tab = SourcesTab(repository)
        self.images_tab = ImagesTab(repository.database_path)
        self._editor_tabs = [self.basic_tab, self.stats_tab, self.upgrade_tab, self.sources_tab, self.images_tab]
        for label, tab in zip(("Basic Info", "Stats", "Upgrade", "Sources", "Images"), self._editor_tabs):
            self.tabs.addTab(tab, label)

        self.copy_stats_sources_button = QPushButton("Copy Stats + Sources...")
        self.discard_button = QPushButton("Discard Changes")
        self.save_button = QPushButton("Save")
        actions = QHBoxLayout(); actions.addWidget(self.copy_stats_sources_button); actions.addStretch(1); actions.addWidget(self.discard_button); actions.addWidget(self.save_button)
        detail = QWidget(); detail_layout = QVBoxLayout(detail); detail_layout.addWidget(self.header_label); detail_layout.addWidget(self.tabs, 1); detail_layout.addLayout(actions)
        splitter = QSplitter(Qt.Horizontal); splitter.addWidget(self.browser); splitter.addWidget(detail); splitter.setStretchFactor(1, 1)
        container = QWidget(); root = QVBoxLayout(container); root.setContentsMargins(0, 0, 0, 0); root.addWidget(splitter)
        self.setCentralWidget(container)

        self.browser.itemActivated.connect(self._select_item)
        self.browser.newItemRequested.connect(self._new_item)
        self.browser.filterWouldHideSelection.connect(self._filter_would_hide)
        self.copy_stats_sources_button.clicked.connect(self._copy_stats_and_sources)
        self.save_button.clicked.connect(self._save_current)
        self.discard_button.clicked.connect(self._discard_current)
        self.basic_tab.deleteRequested.connect(self._delete_current)
        self._bind_draft(None)

    def _bind_draft(self, draft: ItemDraft | None) -> None:
        for tab in self._editor_tabs:
            tab.set_draft(draft)
        enabled = draft is not None
        self.tabs.setEnabled(enabled)
        self.copy_stats_sources_button.setEnabled(enabled)
        self.save_button.setEnabled(enabled)
        self.discard_button.setEnabled(enabled)
        self.header_label.setText("No item selected" if draft is None else f"Item: {draft.name or '(new item)'} — ID {draft.id}")
        self.browser.set_selected_item(None if draft is None or draft.is_new else draft.id)

    def _flush_tabs(self) -> None:
        for tab in self._editor_tabs:
            tab.flush_to_draft()
        if self.session.draft is not None:
            self.header_label.setText(f"Item: {self.session.draft.name or '(new item)'} — ID {self.session.draft.id}")

    def _confirm_copy_stats_sources(self, source: ItemDraft) -> bool:
        target = self.session.draft
        if target is None:
            return False
        message = (
            f'Copy Stats + Sources from "{source.name}"?\n\n'
            f"This will replace the current draft's:\n"
            f"• {len(target.stats)} stats\n"
            f"• {len(target.sources)} sources\n\n"
            f"with:\n"
            f"• {len(source.stats)} stats\n"
            f"• {len(source.sources)} sources\n\n"
            "All other item data will remain unchanged."
        )
        box = QMessageBox(self)
        box.setWindowTitle("Replace Stats + Sources")
        box.setText(message)
        replace = box.addButton("Replace", QMessageBox.AcceptRole)
        box.addButton("Cancel", QMessageBox.RejectRole)
        box.exec()
        return box.clickedButton() is replace

    def _copy_stats_and_sources(self) -> None:
        if self.session.draft is None:
            return

        self._flush_tabs()
        source_item_id = self._choose_copy_source()
        if source_item_id is None:
            return

        try:
            source = self.repository.load_item_draft(source_item_id)
        except Exception as exc:
            QMessageBox.critical(self, "Copy failed", str(exc))
            return

        if not self._confirm_copy_stats_sources(source):
            return

        try:
            draft = self.session.replace_stats_and_sources_from(source_item_id)
        except Exception as exc:
            QMessageBox.critical(self, "Copy failed", str(exc))
            return

        self.stats_tab.set_draft(draft)
        self.sources_tab.set_draft(draft)
        self.statusBar().showMessage(
            f"Copied {len(draft.stats)} stats and {len(draft.sources)} sources from {source.name}",
            8000,
        )

    def _choose_copy_source(self) -> int | None:
        draft = self.session.draft
        if draft is None:
            return None
        dialog = CopyStatsSourcesDialog(self.repository, draft, self)
        if dialog.exec() != QDialog.Accepted:
            return None
        return dialog.selected_source_item_id()

    def _prompt_unsaved(self) -> str:
        if self._prompt_unsaved_callback is not None:
            return self._prompt_unsaved_callback()
        box = QMessageBox(self)
        box.setWindowTitle("Unsaved changes")
        box.setText("You have unsaved changes.")
        save = box.addButton("Save", QMessageBox.AcceptRole)
        discard = box.addButton("Discard", QMessageBox.DestructiveRole)
        cancel = box.addButton("Cancel", QMessageBox.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is save: return "save"
        if clicked is discard: return "discard"
        return "cancel"

    def _guard_unsaved(self) -> bool:
        self._flush_tabs()
        if not self.session.is_dirty:
            return True
        decision = self._prompt_unsaved()
        if decision == "save":
            return self._save_current()
        if decision == "discard":
            self.session.discard()
            return True
        return False

    def _select_item(self, item_id: int) -> None:
        previous_id = self.session.draft.id if self.session.draft is not None and not self.session.draft.is_new else None
        if self.session.draft is not None and self.session.draft.id == item_id and not self.session.draft.is_new:
            return
        if not self._guard_unsaved():
            self.browser.set_selected_item(previous_id)
            return
        draft = self.session.load_item(item_id)
        self._bind_draft(draft)

    def _new_item(self) -> None:
        if not self._guard_unsaved():
            return
        self._bind_draft(self.session.new_item())

    def _filter_would_hide(self, _search: str, _type_value: str) -> None:
        previous = self.browser.applied_filter()
        if self._guard_unsaved():
            self.browser.apply_pending_filter()
            if self.session.draft is not None and not self.session.draft.is_new:
                visible_ids = {item.id for item in self.browser.visible_items()}
                if self.session.draft.id not in visible_ids:
                    self.session.original = None
                    self.session.draft = None
                    self._bind_draft(None)
        else:
            self.browser.restore_filter(*previous)

    def _route_validation(self, report: ValidationReport) -> None:
        basic, stats, upgrade, sources, images = [], [], [], [], []
        upgrade_codes = {"malformed_upgrade_relationship", "multiple_upgrade_relationships", "upgrade_self_reference", "missing_upgrade_target", "upgrade_requires_crysta", "upgrade_cycle"}
        for issue in report.issues:
            msg = issue.message.casefold()
            if issue.code in upgrade_codes:
                upgrade.append(issue)
            elif msg.startswith("stat ") or issue.code in {"empty_stat_name", "reserved_upgrade_stat", "condition_needs_review"}:
                stats.append(issue)
            elif msg.startswith("source "):
                sources.append(issue)
            elif msg.startswith("image ") or issue.code in {"invalid_image_source", "duplicate_image_destination"}:
                images.append(issue)
            else:
                basic.append(issue)
        self.basic_tab.set_validation_errors(basic)
        self.stats_tab.set_validation_errors(stats)
        self.upgrade_tab.set_validation_errors(upgrade)
        self.sources_tab.set_validation_errors(sources)
        self.images_tab.set_validation_errors(images)

    def _show_validation(self, report: ValidationReport) -> None:
        self._route_validation(report)
        if report.errors:
            QMessageBox.warning(self, "Cannot save", "\n".join(f"• {issue.message}" for issue in report.errors))
        elif report.warnings:
            self.statusBar().showMessage("Warnings: " + "; ".join(issue.message for issue in report.warnings), 12000)

    def _save_current(self) -> bool:
        if self.session.draft is None:
            return False
        try:
            self._flush_tabs()
            report = self.session.validate()
            self._show_validation(report)
            if not report.is_valid:
                return False
            result = self.session.save()
        except Exception as exc:
            QMessageBox.critical(self, "Save failed", str(exc))
            return False
        self.browser.reload_items(select_id=result.item_id)
        self._bind_draft(self.session.draft)
        if result.warnings:
            QMessageBox.warning(self, "Saved with warnings", "\n".join(result.warnings))
        else:
            self.statusBar().showMessage("Saved", 5000)
        return True

    def _discard_current(self) -> None:
        draft = self.session.discard()
        self._bind_draft(draft)
        if draft is not None:
            self.browser.set_selected_item(draft.id)

    def _typed_delete_confirmation(self, item_name: str, preview_text: str) -> str | None:
        if self._confirm_delete_callback is not None:
            return self._confirm_delete_callback(item_name, preview_text)
        text, ok = QInputDialog.getText(
            self,
            "Delete item permanently",
            f"{preview_text}\n\nType the exact item name to delete it permanently:\n{item_name}",
        )
        return str(text) if ok else None

    def _delete_current(self) -> None:
        draft = self.session.draft
        if draft is None or draft.is_new:
            return
        preview = self.service.preview_delete(draft.id)
        typed = self._typed_delete_confirmation(draft.name, preview.render_text())
        if typed != draft.name:
            if typed is not None:
                QMessageBox.information(self, "Delete cancelled", "The typed name did not match exactly.")
            return
        try:
            result = self.session.delete_current()
        except Exception as exc:
            QMessageBox.critical(self, "Delete failed", str(exc))
            return
        self.browser.reload_items()
        self._bind_draft(None)
        if result.warnings:
            QMessageBox.warning(self, "Deleted with warnings", "\n".join(result.warnings))
        else:
            self.statusBar().showMessage("Item deleted", 5000)

    def closeEvent(self, event) -> None:
        if self._closing_repository:
            event.accept(); return
        if not self._guard_unsaved():
            event.ignore(); return
        self._closing_repository = True
        self.repository.close()
        event.accept()
