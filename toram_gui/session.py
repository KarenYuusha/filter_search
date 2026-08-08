from __future__ import annotations

import copy

from toram_data import EditorService, ItemDraft, ItemRepository


class ItemEditorSession:
    """Own one editable draft while delegating persistence to EditorService."""

    def __init__(self, repository: ItemRepository, service: EditorService) -> None:
        self.repository = repository
        self.service = service
        self.original: ItemDraft | None = None
        self.draft: ItemDraft | None = None

    @property
    def is_dirty(self) -> bool:
        if self.draft is None:
            return False
        if self.original is None:
            return True
        return self.draft != self.original

    def load_item(self, item_id: int) -> ItemDraft:
        loaded = self.repository.load_item_draft(item_id)
        self.original = copy.deepcopy(loaded)
        self.draft = copy.deepcopy(loaded)
        return self.draft

    def new_item(self) -> ItemDraft:
        draft = ItemDraft.new(
            item_id=self.repository.next_item_id(),
            schema_version=self.repository.default_schema_version(),
        )
        self.original = None
        self.draft = draft
        return draft

    def replace_stats_and_sources_from(self, source_item_id: int) -> ItemDraft:
        if self.draft is None:
            raise RuntimeError("No active draft")
        if not self.draft.is_new and self.draft.id == source_item_id:
            raise ValueError("Cannot copy Stats + Sources from the active item itself")

        source = self.repository.load_item_draft(source_item_id)
        copied_stats = copy.deepcopy(source.stats)
        copied_sources = copy.deepcopy(source.sources)

        self.draft.stats = copied_stats
        self.draft.sources = copied_sources
        return self.draft

    def validate(self):
        if self.draft is None:
            raise RuntimeError("No active draft")
        if self.original is None:
            return self.service.preview_add(self.draft).validation
        return self.service.preview_edit(self.original, self.draft).validation

    def save(self):
        if self.draft is None:
            raise RuntimeError("No active draft")
        if self.original is None:
            result = self.service.save_add(self.draft)
        else:
            result = self.service.save_edit(self.original, self.draft)
        self.load_item(result.item_id)
        return result

    def discard(self) -> ItemDraft | None:
        if self.draft is None:
            return None
        if self.original is None:
            self.original = None
            self.draft = None
            return None
        return self.load_item(self.original.id)

    def delete_current(self):
        if self.draft is None or self.draft.is_new:
            raise RuntimeError("Only persisted items can be deleted")
        result = self.service.delete_item(self.draft.id)
        self.original = None
        self.draft = None
        return result
