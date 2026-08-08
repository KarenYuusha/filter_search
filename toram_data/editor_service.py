from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .backup import BackupManager
from .images import ManagedImageStore
from .models import ImageDraft, ItemDraft
from .repository import DeleteCounts, ItemRepository
from .validation import ValidationReport, validate_item_draft


@dataclass(frozen=True)
class PreviewSection:
    title: str
    lines: tuple[str, ...]


@dataclass(frozen=True)
class MutationPreview:
    operation: Literal["add", "edit", "delete"]
    item_id: int
    item_name: str
    sections: tuple[PreviewSection, ...]
    validation: ValidationReport

    def render_text(self) -> str:
        output = [f"{self.operation.upper()}: {self.item_name} — {self.item_id}"]
        for section in self.sections:
            output.append("")
            output.append(section.title)
            output.extend(section.lines or ("(none)",))
        if self.validation.errors:
            output.append("")
            output.append("Validation errors")
            output.extend(f"- {issue.message}" for issue in self.validation.errors)
        if self.validation.warnings:
            output.append("")
            output.append("Warnings")
            output.extend(f"- {issue.message}" for issue in self.validation.warnings)
        return "\n".join(output)


@dataclass(frozen=True)
class MutationResult:
    operation: Literal["add", "edit", "delete"]
    item_id: int
    backup_path: Path
    warnings: tuple[str, ...] = ()
    delete_counts: DeleteCounts | None = None


class ValidationFailed(RuntimeError):
    def __init__(self, report: ValidationReport) -> None:
        self.report = report
        super().__init__("Item draft validation failed")


def _format_number(value: float | None) -> str:
    if value is None:
        return "(blank)"
    return str(int(value)) if float(value).is_integer() else str(value)


def _stat_display(draft: ItemDraft) -> tuple[str, ...]:
    lines: list[str] = []
    for stat in draft.stats:
        suffix = f" [{stat.condition.text}]" if stat.condition.text else ""
        lines.append(f"{stat.stat_name}: {_format_number(stat.amount)}{suffix}")
    return tuple(lines)


def _source_display(draft: ItemDraft) -> tuple[str, ...]:
    return tuple(
        " | ".join(str(value) for value in (source.source_name, source.level, source.map) if value not in (None, ""))
        or "(empty source)"
        for source in draft.sources
    )


def _image_display(draft: ItemDraft) -> tuple[str, ...]:
    lines: list[str] = []
    for image in draft.images:
        local = str(image.selected_source_path) if image.selected_source_path else image.local_path
        fields = [value for value in (image.category, image.gender, image.variant, local, image.source_url) if value]
        lines.append(" | ".join(str(value) for value in fields) or "(empty image)")
    return tuple(lines)


def _basic_fields(draft: ItemDraft) -> tuple[tuple[str, object], ...]:
    return (
        ("ID", draft.id),
        ("Schema version", draft.schema_version),
        ("Name", draft.name),
        ("Item type", draft.item_type),
        ("Sell price", draft.sell_price),
        ("Process material", draft.process_material),
        ("Process amount", draft.process_amount),
        ("Badge", draft.badge),
        ("Note", draft.note),
        ("API URL", draft.api_url),
        ("Page URL", draft.page_url),
        ("JSON path", draft.json_path),
    )


class EditorService:
    def __init__(
        self,
        repository: ItemRepository,
        backup_manager: BackupManager | None = None,
        image_store: ManagedImageStore | None = None,
    ) -> None:
        self.repository = repository
        self.backup_manager = backup_manager or BackupManager(repository.database_path)
        self.image_store = image_store or ManagedImageStore(repository.database_path)

    def preview_add(self, draft: ItemDraft) -> MutationPreview:
        report = validate_item_draft(draft, self.repository)
        sections = (
            PreviewSection("Basic information", tuple(f"{label}: {value if value not in (None, '') else '(blank)'}" for label, value in _basic_fields(draft))),
            PreviewSection("Stats", _stat_display(draft)),
            PreviewSection("Upgrade relationship", (f"Previous item ID: {draft.previous_upgrade_item_id}",) if draft.previous_upgrade_item_id is not None else ("None",)),
            PreviewSection("Sources", _source_display(draft)),
            PreviewSection("Images to copy", _image_display(draft)),
            PreviewSection("Generated fields", (f"json_path: {draft.json_path}",)),
        )
        return MutationPreview("add", draft.id, draft.name or "(unnamed)", sections, report)

    def preview_edit(self, original: ItemDraft, draft: ItemDraft) -> MutationPreview:
        report = validate_item_draft(draft, self.repository, original=original)
        changed = []
        for (label, before), (_label, after) in zip(_basic_fields(original), _basic_fields(draft)):
            if before != after:
                changed.append(f"{label}: {before!r} -> {after!r}")
        sections = (
            PreviewSection("Changed", tuple(changed)),
            PreviewSection("Stats before", _stat_display(original)),
            PreviewSection("Stats after", _stat_display(draft)),
            PreviewSection("Sources before", _source_display(original)),
            PreviewSection("Sources after", _source_display(draft)),
            PreviewSection("Images before", _image_display(original)),
            PreviewSection("Images after", _image_display(draft)),
            PreviewSection("Upgrade relationship", (f"{original.previous_upgrade_item_id} -> {draft.previous_upgrade_item_id}",)),
        )
        return MutationPreview("edit", draft.id, draft.name or "(unnamed)", sections, report)

    def _save(self, draft: ItemDraft, *, original: ItemDraft | None) -> MutationResult:
        report = validate_item_draft(draft, self.repository, original=original)
        if not report.is_valid:
            raise ValidationFailed(report)
        batch = self.image_store.stage(draft.id, draft.images)
        try:
            backup_path = self.backup_manager.create_verified_backup()
            prepared_images = batch.materialize()
            prepared_draft = copy.deepcopy(draft)
            prepared_draft.images = prepared_images
            if original is None:
                self.repository.add_item(prepared_draft)
                operation: Literal["add", "edit"] = "add"
            else:
                self.repository.update_item(prepared_draft)
                operation = "edit"
        except Exception:
            batch.rollback_new_files()
            batch.cleanup_staging()
            raise
        else:
            batch.cleanup_staging()

        warnings: list[str] = []
        if original is not None:
            current_paths = {image.local_path for image in prepared_images if image.local_path}
            obsolete = [
                image.local_path
                for image in original.images
                if image.local_path and image.local_path not in current_paths
            ]
            failures = self.image_store.delete_managed_paths(obsolete)
            warnings.extend(f"Could not delete image file: {path}" for path in failures)
        return MutationResult(operation, draft.id, backup_path, tuple(warnings))

    def save_add(self, draft: ItemDraft) -> MutationResult:
        return self._save(draft, original=None)

    def save_edit(self, original: ItemDraft, draft: ItemDraft) -> MutationResult:
        return self._save(draft, original=original)

    def preview_delete(self, item_id: int) -> MutationPreview:
        draft = self.repository.load_item_draft(item_id)
        counts = self.repository.delete_counts(item_id)
        managed: list[str] = []
        external: list[str] = []
        for image in draft.images:
            if not image.local_path:
                continue
            candidate = self.image_store._managed_candidate(image.local_path)
            (managed if candidate is not None else external).append(image.local_path)
        lines = (
            f"Stats: {counts.stats}",
            f"Sources: {counts.sources}",
            f"Images: {counts.images}",
            f"Image errors: {counts.image_errors}",
            f"Incoming upgrade links: {counts.incoming_upgrade_rows}",
        )
        sections = (
            PreviewSection("Rows to delete", lines),
            PreviewSection("Managed image paths", tuple(managed)),
            PreviewSection("External image paths (not deleted)", tuple(external)),
        )
        return MutationPreview("delete", draft.id, draft.name, sections, ValidationReport(()))

    def delete_item(self, item_id: int) -> MutationResult:
        self.repository.load_item_draft(item_id)
        backup_path = self.backup_manager.create_verified_backup()
        counts = self.repository.delete_item(item_id)
        failures = self.image_store.delete_item_directory(item_id)
        warnings = tuple(f"Could not delete image path: {path}" for path in failures)
        return MutationResult("delete", item_id, backup_path, warnings, counts)
