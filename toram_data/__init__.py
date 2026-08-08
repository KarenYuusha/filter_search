"""Shared data-access and editing utilities for Toram item tools."""

from .models import ConditionDraft, ImageDraft, ItemDraft, ItemSnapshot, SourceDraft, StatDraft, manual_json_path
from .repository import ConfigurationError, DatabaseBusyError, DeleteCounts, ItemLookup, ItemRepository, SchemaError
from .validation import KNOWN_CONDITIONS, ValidationIssue, ValidationReport, condition_from_slug, free_text_condition, generate_raw_cells_json, validate_item_draft
from .backup import BackupError, BackupManager
from .images import ImageStoreError, ManagedImageStore, PreparedImageBatch, file_sha256
from .editor_service import EditorService, MutationPreview, MutationResult, PreviewSection, ValidationFailed
