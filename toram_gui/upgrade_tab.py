from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from toram_data import ItemDraft, ItemRepository
from toram_data.aliases import is_crysta_item_type


class UpgradeTab(QWidget):
    def __init__(self, repository: ItemRepository, parent=None) -> None:
        super().__init__(parent)
        self.repository = repository
        self.draft: ItemDraft | None = None
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search crysta…")
        self.results = QListWidget()
        self.change_button = QPushButton("Change Previous")
        self.clear_button = QPushButton("Clear Relationship")
        self.refresh_button = QPushButton("View Full Chain")
        self.current_label = QLabel("Previous: None")
        self.validation_label = QLabel()
        self.chain = QTreeWidget(); self.chain.setHeaderLabel("Upgrade chain")
        buttons = QHBoxLayout(); buttons.addWidget(self.change_button); buttons.addWidget(self.clear_button); buttons.addWidget(self.refresh_button); buttons.addStretch(1)
        layout = QVBoxLayout(self); layout.addWidget(self.current_label); layout.addWidget(self.search_edit); layout.addWidget(self.results); layout.addLayout(buttons); layout.addWidget(self.validation_label); layout.addWidget(self.chain, 1)
        self.search_edit.textChanged.connect(self.refresh_search)
        self.change_button.clicked.connect(self._change_selected)
        self.clear_button.clicked.connect(self.clear_relationship)
        self.refresh_button.clicked.connect(self.refresh_chain)
        self.results.itemDoubleClicked.connect(lambda _item: self._change_selected())

    def set_draft(self, draft: ItemDraft | None) -> None:
        self.draft = draft
        self.setEnabled(draft is not None)
        self.search_edit.clear(); self.results.clear(); self.validation_label.clear()
        self._update_current_label(); self.refresh_chain()
        if draft is not None: self.refresh_search("")

    def refresh_search(self, text: str) -> None:
        self.results.clear()
        if self.draft is None: return
        query = text.strip()
        matches = self.repository.find_items(query, crysta_only=True, limit=50) if query else [i for i in self.repository.list_items() if is_crysta_item_type(i.item_type)][:50]
        for item in matches:
            if item.id == self.draft.id: continue
            row = QListWidgetItem(f"{item.name} — {item.item_type} — ID {item.id}")
            row.setData(Qt.UserRole, item.id)
            self.results.addItem(row)

    def _change_selected(self) -> None:
        row = self.results.currentItem()
        if row is not None: self.set_previous_item(int(row.data(Qt.UserRole)))

    def set_previous_item(self, item_id: int) -> bool:
        if self.draft is None: return False
        if item_id == self.draft.id:
            QMessageBox.warning(self, "Invalid upgrade", "An item cannot upgrade from itself.")
            return False
        if self.repository.would_create_upgrade_cycle(self.draft.id, item_id):
            QMessageBox.warning(self, "Invalid upgrade", "This relationship would create an upgrade cycle.")
            return False
        self.draft.previous_upgrade_item_id = item_id
        self._update_current_label(); self.refresh_chain()
        return True

    def clear_relationship(self) -> None:
        if self.draft is None: return
        self.draft.previous_upgrade_item_id = None
        self._update_current_label(); self.refresh_chain()

    def _item_name(self, item_id: int) -> str:
        try: return self.repository.load_item_draft(item_id).name
        except Exception: return f"ID {item_id}"

    def _update_current_label(self) -> None:
        if self.draft is None or self.draft.previous_upgrade_item_id is None:
            self.current_label.setText("Previous: None")
        else:
            pid = self.draft.previous_upgrade_item_id
            self.current_label.setText(f"Previous: {self._item_name(pid)} — ID {pid}")

    def _predecessor(self, item_id: int) -> int | None:
        if self.draft is not None and item_id == self.draft.id:
            return self.draft.previous_upgrade_item_id
        try: return self.repository.load_item_draft(item_id).previous_upgrade_item_id
        except Exception: return None

    def _children_for(self, item_id: int) -> list[int]:
        ids = set(self.repository.incoming_upgrade_item_ids(item_id))
        if self.draft is not None:
            # Remove the persisted edge(s) owned by the active draft, then add
            # its unsaved predecessor override so the chain is draft-aware.
            if item_id in self.draft.existing_upgrade_item_ids:
                ids.discard(self.draft.id)
            if self.draft.previous_upgrade_item_id == item_id:
                ids.add(self.draft.id)
        return sorted(ids)

    def refresh_chain(self) -> None:
        self.chain.clear()
        if self.draft is None: return
        root = self.draft.id
        visited: set[int] = set()
        current = root
        while current not in visited:
            visited.add(current)
            previous = self._predecessor(current)
            if previous is None: break
            root = previous; current = previous
        def add(parent, item_id: int, seen: set[int]) -> None:
            node = QTreeWidgetItem([f"{self._item_name(item_id)} — ID {item_id}"])
            if parent is None: self.chain.addTopLevelItem(node)
            else: parent.addChild(node)
            if item_id in seen: return
            next_seen = set(seen); next_seen.add(item_id)
            for child in self._children_for(item_id): add(node, child, next_seen)
        add(None, root, set())
        self.chain.expandAll()

    def flush_to_draft(self) -> None:
        return

    def set_validation_errors(self, issues) -> None:
        messages = [i.message for i in issues]
        self.validation_label.setText("\n".join(messages))
        self.search_edit.setProperty("validationError", bool(messages))
        self.search_edit.style().unpolish(self.search_edit); self.search_edit.style().polish(self.search_edit)
