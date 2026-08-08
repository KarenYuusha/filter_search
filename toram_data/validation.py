from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from .aliases import is_crysta_item_type, normalize_name
from .models import ConditionDraft, ItemDraft, SourceDraft
from .repository import ItemRepository


@dataclass(frozen=True)
class ConditionDefinition:
    slug: str
    label: str
    coryn_applies_to: int


KNOWN_CONDITIONS: tuple[ConditionDefinition, ...] = (
    ConditionDefinition("shield", "Shield only", 1),
    ConditionDefinition("knuckles", "Knuckle only", 2),
    ConditionDefinition("magic_device", "Magic Device only", 4),
    ConditionDefinition("staff", "Staff only", 8),
    ConditionDefinition("bowgun", "Bowgun only", 16),
    ConditionDefinition("bow", "Bow only", 32),
    ConditionDefinition("two_handed_sword", "2-Handed Sword only", 64),
    ConditionDefinition("one_handed_sword", "1-Handed Sword only", 128),
    ConditionDefinition("armor", "Armor only", 256),
    ConditionDefinition("special", "Special Gear only", 512),
    ConditionDefinition("additional", "Additional Gear only", 1024),
    ConditionDefinition("halberd", "Halberd only", 2048),
    ConditionDefinition("katana", "Katana only", 8192),
    ConditionDefinition("heavy_armor", "Heavy Armor only", 16384),
    ConditionDefinition("light_armor", "Light Armor only", 32768),
    ConditionDefinition("dagger", "Dagger only", 65536),
    ConditionDefinition("dual_swords", "Dual Swords only", 131072),
    ConditionDefinition("arrow", "Arrow only", 262144),
    ConditionDefinition("ninjutsu_scroll", "Ninjutsu Scroll only", 524288),
)


IssueLevel = Literal["error", "warning"]


@dataclass(frozen=True)
class ValidationIssue:
    level: IssueLevel
    code: str
    message: str


@dataclass(frozen=True)
class ValidationReport:
    issues: tuple[ValidationIssue, ...]

    @property
    def errors(self) -> tuple[ValidationIssue, ...]:
        return tuple(issue for issue in self.issues if issue.level == "error")

    @property
    def warnings(self) -> tuple[ValidationIssue, ...]:
        return tuple(issue for issue in self.issues if issue.level == "warning")

    @property
    def error_codes(self) -> frozenset[str]:
        return frozenset(issue.code for issue in self.errors)

    @property
    def is_valid(self) -> bool:
        return not self.errors


def condition_from_slug(slug: str) -> ConditionDraft:
    for definition in KNOWN_CONDITIONS:
        if definition.slug == slug:
            return ConditionDraft(
                mode="known",
                slugs=(definition.slug,),
                text=definition.label,
                coryn_applies_to=definition.coryn_applies_to,
                needs_review=False,
            )
    raise KeyError(f"Unknown condition slug: {slug}")


def free_text_condition(text: str) -> ConditionDraft:
    value = text.strip()
    return ConditionDraft(mode="free_text", slugs=(), text=value or None, coryn_applies_to=None, needs_review=True)


def generate_raw_cells_json(source: SourceDraft) -> str:
    values: list[str] = []
    for value in (
        source.source_name,
        source.level,
        source.map,
        source.dye,
        source.source_url,
        source.lookup_error,
    ):
        if value is not None and str(value).strip():
            values.append(str(value))
    return json.dumps(values, ensure_ascii=False, separators=(",", ":"))


def _valid_url(value: str | None) -> bool:
    if value is None or not value.strip():
        return True
    parsed = urlparse(value.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _same_source_field(original: ItemDraft | None, index: int, field: str, value: object) -> bool:
    if original is None or index >= len(original.sources):
        return False
    return getattr(original.sources[index], field) == value


def validate_item_draft(
    draft: ItemDraft,
    repository: ItemRepository,
    *,
    original: ItemDraft | None = None,
) -> ValidationReport:
    issues: list[ValidationIssue] = []

    def error(code: str, message: str) -> None:
        issues.append(ValidationIssue("error", code, message))

    def warning(code: str, message: str) -> None:
        issues.append(ValidationIssue("warning", code, message))

    if not draft.name.strip():
        error("missing_name", "Item name is required")
    if not draft.item_type.strip():
        error("missing_item_type", "Item type is required")
    if draft.is_new and repository.item_id_exists(draft.id):
        error("duplicate_item_id", f"Item ID {draft.id} already exists")
    if original is not None and draft.id != original.id:
        error("duplicate_item_id", "Existing item IDs cannot be changed")
    if original is not None and draft.json_path != original.json_path:
        error("unusual_existing_row", "Existing JSON path is read-only")

    for label, value in (
        ("sell_price", draft.sell_price),
        ("process_amount", draft.process_amount),
    ):
        if value is not None:
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                error("invalid_number", f"{label} must be numeric")
            else:
                if not math.isfinite(numeric):
                    error("non_finite_number", f"{label} must be finite")

    for label, value, original_value in (
        ("api_url", draft.api_url, original.api_url if original else None),
        ("page_url", draft.page_url, original.page_url if original else None),
    ):
        if not _valid_url(value):
            if original is not None and value == original_value:
                warning("unusual_existing_row", f"Preserving unusual existing {label}")
            else:
                error("invalid_url", f"{label} must be an http(s) URL")

    existing_types = {normalize_name(value) for value in repository.list_item_types()}
    if draft.item_type.strip() and normalize_name(draft.item_type) not in existing_types:
        warning("new_item_type", f"New item type: {draft.item_type}")

    existing_stats = {normalize_name(value) for value in repository.list_stat_names()}
    for index, stat in enumerate(draft.stats, start=1):
        if not stat.stat_name.strip():
            error("empty_stat_name", f"Stat {index} has an empty name")
        elif normalize_name(stat.stat_name) == "upgrade for":
            error("reserved_upgrade_stat", "Use Upgrade relationship instead of a normal stat")
        elif normalize_name(stat.stat_name) not in existing_stats:
            warning("new_stat_name", f"New stat name: {stat.stat_name}")
        if stat.amount is not None:
            try:
                numeric = float(stat.amount)
            except (TypeError, ValueError):
                error("invalid_number", f"Stat {index} amount must be numeric")
            else:
                if not math.isfinite(numeric):
                    error("non_finite_number", f"Stat {index} amount must be finite")
        if len(stat.condition.slugs) > 1 and stat.condition.mode != "preserve":
            error("invalid_json", f"Stat {index} may have at most one newly authored condition")
        if stat.condition.needs_review:
            warning("condition_needs_review", f"Stat {index} condition requires review")

    for index, source in enumerate(draft.sources):
        if source.raw_cells_json:
            try:
                parsed = json.loads(source.raw_cells_json)
                valid = isinstance(parsed, list)
            except json.JSONDecodeError:
                valid = False
            if not valid:
                if _same_source_field(original, index, "raw_cells_json", source.raw_cells_json):
                    warning("unusual_existing_row", f"Preserving unusual source JSON at row {index + 1}")
                else:
                    error("invalid_json", f"Source {index + 1} raw_cells_json must be a JSON array")
        if not _valid_url(source.source_url):
            if _same_source_field(original, index, "source_url", source.source_url):
                warning("unusual_existing_row", f"Preserving unusual source URL at row {index + 1}")
            else:
                error("invalid_url", f"Source {index + 1} URL must be http(s)")

    for index, image in enumerate(draft.images):
        if image.selected_source_path is not None:
            path = Path(image.selected_source_path)
            if not path.is_file():
                error("invalid_image_source", f"Image {index + 1} source file is not readable")
        if not _valid_url(image.source_url):
            error("invalid_url", f"Image {index + 1} URL must be http(s)")

    selected_names = [
        image.selected_source_path.name.casefold()
        for image in draft.images
        if image.selected_source_path is not None
    ]
    if len(selected_names) != len(set(selected_names)):
        error("duplicate_image_destination", "Two selected images would use the same destination name")

    if draft.malformed_upgrade_rows:
        error("malformed_upgrade_relationship", "Malformed Upgrade for rows must be resolved")
    if len(draft.existing_upgrade_item_ids) > 1 and draft.previous_upgrade_item_id is None:
        error("multiple_upgrade_relationships", "Choose one predecessor or remove the relationship")
    predecessor = draft.previous_upgrade_item_id
    if predecessor is not None:
        if predecessor == draft.id:
            error("upgrade_self_reference", "An item cannot upgrade from itself")
        elif not repository.item_id_exists(predecessor):
            error("missing_upgrade_target", f"Upgrade target {predecessor} does not exist")
        else:
            current_is_crysta = is_crysta_item_type(draft.item_type)
            target = repository.load_item_draft(predecessor)
            if not current_is_crysta or not is_crysta_item_type(target.item_type):
                error("upgrade_requires_crysta", "Upgrade relationships require crysta item types")
            if repository.would_create_upgrade_cycle(draft.id, predecessor):
                error("upgrade_cycle", "This relationship would create an upgrade cycle")

    return ValidationReport(tuple(issues))
