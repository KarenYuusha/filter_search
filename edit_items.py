# /// script
# requires-python = ">=3.10"
# dependencies = ["rapidfuzz>=3.9,<4"]
# ///

from __future__ import annotations

import argparse
import copy
import json
import logging
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence

from toram_data.aliases import (
    ITEM_TYPE_ALIASES,
    STAT_ALIASES,
    normalize_name,
    normalize_stat_text,
    resolve_editor_value,
)
from toram_data.backup import BackupManager
from toram_data.editor_service import EditorService, MutationResult, ValidationFailed
from toram_data.images import ManagedImageStore
from toram_data.models import ConditionDraft, ImageDraft, ItemDraft, SourceDraft, StatDraft
from toram_data.repository import DatabaseBusyError, ItemRepository, SchemaError
from toram_data.validation import (
    KNOWN_CONDITIONS,
    condition_from_slug,
    free_text_condition,
    generate_raw_cells_json,
    validate_item_draft,
)

SCRIPT_VERSION = "2026.08.06-item-editor-v1"
LOGGER = logging.getLogger("toram_item_editor")


@dataclass
class TerminalIO:
    input_fn: Callable[[str], str] = input
    output_fn: Callable[..., None] = print

    def input(self, prompt: str = "") -> str:
        return self.input_fn(prompt)

    def print(self, *values: object, sep: str = " ", end: str = "\n") -> None:
        self.output_fn(*values, sep=sep, end=end)


def _read(io: TerminalIO, prompt: str) -> str | None:
    try:
        return io.input(prompt)
    except (EOFError, KeyboardInterrupt):
        io.print()
        return None


def confirm(io: TerminalIO, prompt: str, *, default: bool = False) -> bool:
    answer = _read(io, prompt)
    if answer is None:
        return False
    value = answer.strip().casefold()
    if not value:
        return default
    return value in {"y", "yes"}


def choose_menu(
    io: TerminalIO,
    title: str,
    options: Sequence[str],
    *,
    allow_cancel: bool = True,
) -> int | None:
    while True:
        io.print()
        io.print(title)
        for index, option in enumerate(options, start=1):
            io.print(f"{index}. {option}")
        if allow_cancel:
            io.print("0. Cancel")
        answer = _read(io, "Choose: ")
        if answer is None:
            return None
        normalized = answer.strip().casefold()
        if allow_cancel and normalized in {"0", "q", "quit", "cancel"}:
            return None
        try:
            choice = int(normalized)
        except ValueError:
            io.print("Enter a menu number.")
            continue
        if 1 <= choice <= len(options):
            return choice
        io.print("Choice is outside the available range.")


def _ambiguous_stat_groups(existing_values: Iterable[str]) -> dict[str, tuple[str, ...]]:
    values = tuple(existing_values)
    return {
        "dt": tuple(value for value in values if normalize_name(value).startswith("stronger against") or normalize_name(value).startswith("% stronger against")),
        "bar": tuple(value for value in values if "barrier" in normalize_name(value)),
        "rest": tuple(value for value in values if "resistance" in normalize_name(value)),
        "resist": tuple(value for value in values if "resistance" in normalize_name(value)),
    }


def resolve_value_prompt(
    io: TerminalIO,
    *,
    prompt: str,
    existing_values: Iterable[str],
    aliases: Mapping[str, str],
    ambiguous_groups: Mapping[str, tuple[str, ...]] | None = None,
    allow_new: bool,
    required: bool = True,
    kind: str = "value",
    initial: str | None = None,
    normalizer: Callable[[str], str] = normalize_name,
) -> str | None:
    values = tuple(existing_values)
    while True:
        suffix = f" [{initial}]" if initial not in (None, "") else ""
        answer = _read(io, f"{prompt.rstrip()}${suffix}: ".replace("$", ""))
        if answer is None:
            return None
        typed = answer.strip()
        if not typed and initial is not None:
            return initial
        result = resolve_editor_value(
            typed,
            values,
            aliases=aliases,
            ambiguous_groups=ambiguous_groups,
            normalizer=normalizer,
        )
        if result.status == "empty":
            if not required:
                return None
            io.print(f"{kind.capitalize()} is required.")
            continue
        if result.status == "exact":
            return result.candidates[0]
        if result.status == "alias":
            value = result.candidates[0]
            io.print(f"Resolved: {value}")
            if confirm(io, "Accept? [Y/n]: ", default=True):
                return value
            continue
        if result.status in {"ambiguous", "fuzzy"}:
            title = "Choose a value:" if result.status == "ambiguous" else "Did you mean:"
            options = list(result.candidates)
            create_index: int | None = None
            if allow_new:
                options.append(f'Create new {kind} "{result.typed_value}"')
                create_index = len(options)
            choice = choose_menu(io, title, options)
            if choice is None:
                return None
            if create_index is not None and choice == create_index:
                if confirm(io, f'Create new {kind} "{result.typed_value}"? [y/N]: '):
                    return result.typed_value
                continue
            return result.candidates[choice - 1]
        if result.status == "new":
            if not allow_new:
                io.print(f"Unknown {kind}: {result.typed_value}")
                continue
            if confirm(io, f'Create new {kind} "{result.typed_value}"? [y/N]: '):
                return result.typed_value


def _optional_float(io: TerminalIO, prompt: str, current: float | None = None) -> tuple[bool, float | None]:
    while True:
        suffix = f" [{current}]" if current is not None else ""
        answer = _read(io, f"{prompt}{suffix}: ")
        if answer is None:
            return False, current
        value = answer.strip()
        if not value:
            return True, current if current is not None else None
        try:
            number = float(value)
        except ValueError:
            io.print("Enter a number or leave blank.")
            continue
        if not math.isfinite(number):
            io.print("Number must be finite.")
            continue
        return True, number


def _optional_int(io: TerminalIO, prompt: str, current: int | None = None) -> tuple[bool, int | None]:
    while True:
        suffix = f" [{current}]" if current is not None else ""
        answer = _read(io, f"{prompt}{suffix}: ")
        if answer is None:
            return False, current
        value = answer.strip()
        if not value:
            return True, current if current is not None else None
        try:
            return True, int(value)
        except ValueError:
            io.print("Enter a whole number or leave blank.")


def _optional_text(io: TerminalIO, prompt: str, current: str | None = None) -> tuple[bool, str | None]:
    suffix = f" [{current}]" if current not in (None, "") else ""
    answer = _read(io, f"{prompt}{suffix}: ")
    if answer is None:
        return False, current
    value = answer.strip()
    if not value:
        return True, current if current not in (None, "") else None
    return True, value


def _multiline_text(io: TerminalIO, prompt: str, current: str | None = None) -> tuple[bool, str | None]:
    suffix = " [existing value; blank preserves]" if current not in (None, "") else ""
    first = _read(io, f"{prompt}{suffix} (enter . on its own to finish): ")
    if first is None:
        return False, current
    if not first:
        return True, current if current not in (None, "") else None
    if first == ".":
        return True, None
    lines = [first]
    while True:
        line = _read(io, "... ")
        if line is None:
            return False, current
        if line == ".":
            return True, "\n".join(lines)
        lines.append(line)


def _required_text(io: TerminalIO, prompt: str, current: str = "") -> tuple[bool, str]:
    while True:
        suffix = f" [{current}]" if current else ""
        answer = _read(io, f"{prompt}{suffix}: ")
        if answer is None:
            return False, current
        value = answer.strip() or current
        if value:
            return True, value
        io.print("This field is required.")


def edit_basic_information(draft: ItemDraft, repository: ItemRepository, io: TerminalIO) -> bool:
    if draft.is_new:
        while True:
            default_id = repository.next_item_id()
            answer = _read(io, f"Item ID [{default_id}]: ")
            if answer is None:
                return False
            value = answer.strip()
            try:
                item_id = default_id if not value else int(value)
            except ValueError:
                io.print("Item ID must be a whole number.")
                continue
            if repository.item_id_exists(item_id):
                io.print(f"Item ID {item_id} already exists.")
                continue
            draft.set_new_item_id(item_id)
            io.print(f"Generated JSON path: {draft.json_path}")
            break
    else:
        io.print(f"Item ID: {draft.id} (read-only)")
        io.print(f"JSON path: {draft.json_path} (read-only)")

    ok, name = _required_text(io, "Name", draft.name)
    if not ok:
        return False
    draft.name = name

    item_type = resolve_value_prompt(
        io,
        prompt="Item type",
        existing_values=repository.list_item_types(),
        aliases=ITEM_TYPE_ALIASES,
        allow_new=True,
        required=True,
        kind="item type",
        initial=draft.item_type or None,
    )
    if item_type is None:
        return False
    draft.item_type = item_type

    ok, draft.sell_price = _optional_float(io, "Sell price", draft.sell_price)
    if not ok:
        return False
    ok, draft.process_material = _optional_text(io, "Process material", draft.process_material)
    if not ok:
        return False
    ok, draft.process_amount = _optional_float(io, "Process amount", draft.process_amount)
    if not ok:
        return False
    ok, draft.badge = _optional_text(io, "Badge", draft.badge)
    if not ok:
        return False
    ok, draft.note = _multiline_text(io, "Note", draft.note)
    if not ok:
        return False
    ok, draft.api_url = _optional_text(io, "API URL", draft.api_url)
    if not ok:
        return False
    ok, draft.page_url = _optional_text(io, "Page URL", draft.page_url)
    if not ok:
        return False
    ok, schema_version = _optional_int(io, "Schema version", draft.schema_version)
    if not ok or schema_version is None:
        return False
    draft.schema_version = schema_version
    return True


def prompt_condition(existing: ConditionDraft | None, io: TerminalIO) -> ConditionDraft | None:
    options = ["No condition", "Known condition", "Free-text condition"]
    allow_preserve = existing is not None and (existing.mode == "preserve" or len(existing.slugs) > 1)
    if allow_preserve:
        options.append("Preserve existing condition")
    options.append("Cancel")
    choice = choose_menu(io, "Condition", options, allow_cancel=False)
    if choice is None or choice == len(options):
        return None
    if choice == 1:
        return ConditionDraft()
    if choice == 2:
        cond_choice = choose_menu(io, "Known conditions", [definition.label for definition in KNOWN_CONDITIONS])
        if cond_choice is None:
            return None
        return condition_from_slug(KNOWN_CONDITIONS[cond_choice - 1].slug)
    if choice == 3:
        text = _read(io, "Condition text: ")
        if text is None or not text.strip():
            io.print("Condition text cannot be empty.")
            return None
        return free_text_condition(text)
    if allow_preserve and choice == 4:
        return copy.deepcopy(existing)
    return None


def prompt_stat(
    draft: StatDraft | None,
    repository: ItemRepository,
    io: TerminalIO,
) -> StatDraft | None:
    existing_names = repository.list_stat_names()
    stat_name = resolve_value_prompt(
        io,
        prompt="Stat name",
        existing_values=existing_names,
        aliases=STAT_ALIASES,
        ambiguous_groups=_ambiguous_stat_groups(existing_names),
        allow_new=True,
        required=True,
        kind="stat",
        initial=draft.stat_name if draft else None,
        normalizer=normalize_stat_text,
    )
    if stat_name is None:
        return None
    if normalize_name(stat_name) == "upgrade for":
        io.print("Use Upgrade relationship for 'Upgrade for'.")
        return None
    ok, amount = _optional_float(io, "Amount", draft.amount if draft else None)
    if not ok:
        return None
    condition = prompt_condition(draft.condition if draft else None, io)
    if condition is None:
        return None
    return StatDraft(
        stat_name=stat_name,
        amount=amount,
        condition=condition,
        raw_conditions_json=(draft.raw_conditions_json if draft and condition.mode == "preserve" else None),
        original_position=draft.original_position if draft else None,
    )


def _stat_line(stat: StatDraft) -> str:
    amount = "" if stat.amount is None else (f"+{stat.amount:g}" if stat.amount >= 0 else f"{stat.amount:g}")
    condition = f" [{stat.condition.text}]" if stat.condition.text else ""
    return f"{stat.stat_name} {amount}{condition}".strip()


def edit_stats(draft: ItemDraft, repository: ItemRepository, io: TerminalIO) -> None:
    while True:
        io.print()
        io.print("Stats")
        for index, stat in enumerate(draft.stats, start=1):
            io.print(f"{index}. {_stat_line(stat)}")
        choice = choose_menu(io, "Stat actions", ["Add stat", "Edit stat", "Delete stat", "Reorder stats", "Back"], allow_cancel=False)
        if choice in (None, 5):
            return
        if choice == 1:
            stat = prompt_stat(None, repository, io)
            if stat is not None:
                draft.stats.append(stat)
        elif choice == 2:
            if not draft.stats:
                io.print("No stats to edit.")
                continue
            row = choose_menu(io, "Choose stat", [_stat_line(stat) for stat in draft.stats])
            if row is None:
                continue
            updated = prompt_stat(draft.stats[row - 1], repository, io)
            if updated is not None:
                draft.stats[row - 1] = updated
        elif choice == 3:
            if not draft.stats:
                io.print("No stats to delete.")
                continue
            row = choose_menu(io, "Choose stat", [_stat_line(stat) for stat in draft.stats])
            if row is not None and confirm(io, f'Delete "{_stat_line(draft.stats[row - 1])}"? [y/N]: '):
                del draft.stats[row - 1]
        elif choice == 4:
            if len(draft.stats) < 2:
                io.print("At least two stats are required to reorder.")
                continue
            source = choose_menu(io, "Move which stat?", [_stat_line(stat) for stat in draft.stats])
            if source is None:
                continue
            destination = choose_menu(io, "Move to which position?", [str(index) for index in range(1, len(draft.stats) + 1)])
            if destination is not None:
                value = draft.stats.pop(source - 1)
                draft.stats.insert(destination - 1, value)


def edit_upgrade_relationship(draft: ItemDraft, repository: ItemRepository, io: TerminalIO) -> None:
    if "crysta" not in normalize_name(draft.item_type).split():
        io.print("Upgrade relationships are only available for crysta items.")
        return
    if draft.existing_upgrade_item_ids:
        io.print("Existing predecessor IDs: " + ", ".join(map(str, draft.existing_upgrade_item_ids)))
    if not confirm(io, "Does this crysta upgrade from another crysta? [y/N]: "):
        draft.previous_upgrade_item_id = None
        draft.existing_upgrade_item_ids = ()
        draft.malformed_upgrade_rows = ()
        return
    query = _read(io, "Search previous crysta: ")
    if query is None or not query.strip():
        return
    matches = repository.find_items(query, crysta_only=True, limit=5)
    matches = [match for match in matches if match.id != draft.id]
    if not matches:
        io.print("No crysta matches found.")
        return
    choice = choose_menu(io, "Choose previous crysta", [f"{item.name} — {item.item_type} — ID {item.id}" for item in matches])
    if choice is None:
        return
    candidate = matches[choice - 1].id
    previous = draft.previous_upgrade_item_id
    draft.previous_upgrade_item_id = candidate
    draft.existing_upgrade_item_ids = (candidate,)
    draft.malformed_upgrade_rows = ()
    report = validate_item_draft(draft, repository)
    upgrade_errors = [issue for issue in report.errors if issue.code.startswith("upgrade_") or issue.code in {"missing_upgrade_target", "multiple_upgrade_relationships"}]
    if upgrade_errors:
        draft.previous_upgrade_item_id = previous
        draft.existing_upgrade_item_ids = (previous,) if previous is not None else ()
        for issue in upgrade_errors:
            io.print(f"Cannot use this relationship: {issue.message}")
        return
    io.print(f"Previous crysta set to {matches[choice - 1].name} (ID {candidate}).")


def prompt_source(existing: SourceDraft | None, repository: ItemRepository, io: TerminalIO) -> SourceDraft | None:
    current = existing or SourceDraft()
    ok, source_id = _optional_int(io, "Source ID", current.source_id)
    if not ok:
        return None
    ok, source_name = _optional_text(io, "Source name", current.source_name)
    if not ok:
        return None
    ok, level = _optional_int(io, "Level", current.level)
    if not ok:
        return None
    ok, map_value = _optional_text(io, "Map", current.map)
    if not ok:
        return None
    ok, dye = _optional_text(io, "Dye", current.dye)
    if not ok:
        return None
    ok, source_url = _optional_text(io, "Source URL", current.source_url)
    if not ok:
        return None
    ok, lookup_error = _optional_text(io, "Lookup error", current.lookup_error)
    if not ok:
        return None
    source = SourceDraft(
        source_id=source_id,
        source_name=source_name,
        level=level,
        map=map_value,
        dye=dye,
        source_url=source_url,
        lookup_error=lookup_error,
        original_position=current.original_position,
    )
    generated = generate_raw_cells_json(source)
    io.print(f"Generated raw_cells_json: {generated}")
    choice = choose_menu(io, "Raw cells JSON", ["Use generated JSON", "Enter advanced JSON array", "Cancel"], allow_cancel=False)
    if choice == 1:
        source.raw_cells_json = generated
        return source
    if choice == 2:
        while True:
            raw = _read(io, "JSON array: ")
            if raw is None:
                return None
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError as exc:
                io.print(f"Invalid JSON: {exc}")
                continue
            if not isinstance(parsed, list):
                io.print("JSON value must be an array.")
                continue
            source.raw_cells_json = json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
            return source
    return None


def _source_line(source: SourceDraft) -> str:
    fields = [value for value in (source.source_name, source.level, source.map, source.dye) if value not in (None, "")]
    return " | ".join(map(str, fields)) or "(empty source)"


def edit_sources(draft: ItemDraft, repository: ItemRepository, io: TerminalIO) -> None:
    while True:
        io.print()
        io.print("Sources")
        for index, source in enumerate(draft.sources, start=1):
            io.print(f"{index}. {_source_line(source)}")
        choice = choose_menu(io, "Source actions", ["Add source", "Edit source", "Delete source", "Reorder sources", "Back"], allow_cancel=False)
        if choice in (None, 5):
            return
        if choice == 1:
            source = prompt_source(None, repository, io)
            if source:
                draft.sources.append(source)
        elif choice == 2:
            if not draft.sources:
                io.print("No sources to edit.")
                continue
            row = choose_menu(io, "Choose source", [_source_line(source) for source in draft.sources])
            if row:
                updated = prompt_source(draft.sources[row - 1], repository, io)
                if updated:
                    draft.sources[row - 1] = updated
        elif choice == 3:
            if not draft.sources:
                io.print("No sources to delete.")
                continue
            row = choose_menu(io, "Choose source", [_source_line(source) for source in draft.sources])
            if row and confirm(io, f'Delete "{_source_line(draft.sources[row - 1])}"? [y/N]: '):
                del draft.sources[row - 1]
        elif choice == 4:
            if len(draft.sources) < 2:
                io.print("At least two sources are required to reorder.")
                continue
            source_row = choose_menu(io, "Move which source?", [_source_line(source) for source in draft.sources])
            destination = choose_menu(io, "Move to which position?", [str(i) for i in range(1, len(draft.sources) + 1)]) if source_row else None
            if source_row and destination:
                value = draft.sources.pop(source_row - 1)
                draft.sources.insert(destination - 1, value)


def prompt_image(existing: ImageDraft | None, io: TerminalIO) -> ImageDraft | None:
    current = existing or ImageDraft()
    if current.local_path:
        io.print(f"Existing local path: {current.local_path}")
    answer = _read(io, "Local image file (blank to preserve/none): ")
    if answer is None:
        return None
    selected = current.selected_source_path
    value = answer.strip()
    if value:
        path = Path(value).expanduser().resolve()
        if not path.is_file():
            io.print(f"Image file is not readable: {path}")
            return None
        selected = path
    ok, category = _optional_text(io, "Category", current.category)
    if not ok:
        return None
    ok, gender = _optional_text(io, "Gender", current.gender)
    if not ok:
        return None
    ok, variant = _optional_text(io, "Variant", current.variant)
    if not ok:
        return None
    ok, source_url = _optional_text(io, "Source URL", current.source_url)
    if not ok:
        return None
    return ImageDraft(
        category=category,
        gender=gender,
        variant=variant,
        local_path=current.local_path,
        source_url=source_url,
        selected_source_path=selected,
        original_local_path=current.original_local_path or current.local_path,
        original_position=current.original_position,
    )


def _image_line(image: ImageDraft) -> str:
    path = image.selected_source_path or image.local_path
    fields = [value for value in (image.category, image.gender, image.variant, path) if value]
    return " | ".join(map(str, fields)) or "(empty image)"


def edit_images(draft: ItemDraft, database_path: Path, io: TerminalIO) -> None:
    while True:
        io.print()
        io.print("Images")
        for index, image in enumerate(draft.images, start=1):
            io.print(f"{index}. {_image_line(image)}")
        if draft.image_error_rows:
            io.print("Image errors (read-only):")
            for row in draft.image_error_rows:
                io.print(f"- position {row.get('position')}: {row.get('error_json')}")
        choice = choose_menu(io, "Image actions", ["Add image", "Edit image", "Delete image", "Reorder images", "Back"], allow_cancel=False)
        if choice in (None, 5):
            return
        if choice == 1:
            image = prompt_image(None, io)
            if image:
                draft.images.append(image)
        elif choice == 2:
            if not draft.images:
                io.print("No images to edit.")
                continue
            row = choose_menu(io, "Choose image", [_image_line(image) for image in draft.images])
            if row:
                updated = prompt_image(draft.images[row - 1], io)
                if updated:
                    draft.images[row - 1] = updated
        elif choice == 3:
            if not draft.images:
                io.print("No images to delete.")
                continue
            row = choose_menu(io, "Choose image", [_image_line(image) for image in draft.images])
            if row and confirm(io, f'Delete "{_image_line(draft.images[row - 1])}"? [y/N]: '):
                del draft.images[row - 1]
        elif choice == 4:
            if len(draft.images) < 2:
                io.print("At least two images are required to reorder.")
                continue
            source_row = choose_menu(io, "Move which image?", [_image_line(image) for image in draft.images])
            destination = choose_menu(io, "Move to which position?", [str(i) for i in range(1, len(draft.images) + 1)]) if source_row else None
            if source_row and destination:
                value = draft.images.pop(source_row - 1)
                draft.images.insert(destination - 1, value)


def select_item(repository: ItemRepository, io: TerminalIO, *, crysta_only: bool = False) -> int | None:
    query = _read(io, "Item ID or name: ")
    if query is None or not query.strip():
        return None
    typed = query.strip()
    if typed.isdigit() and repository.item_id_exists(int(typed)):
        item = repository.load_item_draft(int(typed))
        if not crysta_only or "crysta" in normalize_name(item.item_type).split():
            return int(typed)
    matches = repository.find_items(typed, crysta_only=crysta_only, limit=5)
    if not matches:
        io.print("No matching items found.")
        return None
    choice = choose_menu(io, "Choose item", [f"{item.name} — {item.item_type} — ID {item.id}" for item in matches])
    return None if choice is None else matches[choice - 1].id


def _print_result(io: TerminalIO, result: MutationResult) -> None:
    io.print(f"{result.operation.capitalize()} complete for item ID {result.item_id}.")
    io.print(f"Verified backup: {result.backup_path}")
    for warning in result.warnings:
        io.print(f"Warning: {warning}")


def _confirm_preview(io: TerminalIO, preview) -> bool:
    io.print()
    io.print(preview.render_text())
    if preview.validation.errors:
        io.print("Save is disabled until validation errors are fixed.")
        return False
    if not confirm(io, "Save these changes? [y/N]: "):
        return False
    if preview.validation.warnings and not confirm(io, "Proceed despite warnings? [y/N]: "):
        return False
    return True


def _save_with_retry(io: TerminalIO, action: Callable[[], MutationResult]) -> MutationResult | None:
    while True:
        try:
            return action()
        except DatabaseBusyError:
            choice = choose_menu(io, "Database is locked by another process.", ["Retry", "Cancel"], allow_cancel=False)
            if choice != 1:
                return None


def _item_section_menu(
    draft: ItemDraft,
    repository: ItemRepository,
    service: EditorService,
    io: TerminalIO,
    *,
    original: ItemDraft | None,
) -> bool:
    while True:
        choice = choose_menu(
            io,
            f"Item {draft.id}: {draft.name or '(unnamed)'}",
            ["Basic information", "Stats", "Upgrade relationship", "Sources", "Images", "Preview", "Save", "Cancel"],
            allow_cancel=False,
        )
        if choice in (None, 8):
            return False
        if choice == 1:
            edit_basic_information(draft, repository, io)
        elif choice == 2:
            edit_stats(draft, repository, io)
        elif choice == 3:
            edit_upgrade_relationship(draft, repository, io)
        elif choice == 4:
            edit_sources(draft, repository, io)
        elif choice == 5:
            edit_images(draft, repository.database_path, io)
        elif choice == 6:
            preview = service.preview_add(draft) if original is None else service.preview_edit(original, draft)
            io.print(preview.render_text())
        elif choice == 7:
            preview = service.preview_add(draft) if original is None else service.preview_edit(original, draft)
            if not _confirm_preview(io, preview):
                continue
            result = _save_with_retry(
                io,
                (lambda: service.save_add(draft)) if original is None else (lambda: service.save_edit(original, draft)),
            )
            if result is not None:
                _print_result(io, result)
                return True


def run_add_flow(repository: ItemRepository, service: EditorService, io: TerminalIO) -> None:
    try:
        draft = ItemDraft.new(item_id=repository.next_item_id(), schema_version=repository.default_schema_version())
        _item_section_menu(draft, repository, service, io, original=None)
    except KeyboardInterrupt:
        io.print("Add canceled.")


def run_edit_flow(repository: ItemRepository, service: EditorService, io: TerminalIO) -> None:
    item_id = select_item(repository, io)
    if item_id is None:
        return
    original = repository.load_item_draft(item_id)
    draft = copy.deepcopy(original)
    _item_section_menu(draft, repository, service, io, original=original)


def run_delete_flow(repository: ItemRepository, service: EditorService, io: TerminalIO) -> None:
    item_id = select_item(repository, io)
    if item_id is None:
        return
    preview = service.preview_delete(item_id)
    io.print(preview.render_text())
    typed = _read(io, "Type the exact item name to confirm: ")
    if typed != preview.item_name:
        io.print("Name did not match. Deletion canceled.")
        return
    if not confirm(io, "Permanently delete this item? [y/N]: "):
        io.print("Deletion canceled.")
        return
    result = _save_with_retry(io, lambda: service.delete_item(item_id))
    if result is not None:
        _print_result(io, result)


def run_editor(database_path: Path, io: TerminalIO) -> int:
    try:
        repository = ItemRepository(database_path)
    except (FileNotFoundError, SchemaError) as exc:
        io.print(f"Cannot open database: {exc}")
        return 2
    try:
        service = EditorService(repository, BackupManager(repository.database_path), ManagedImageStore(repository.database_path))
        while True:
            io.print()
            io.print("Toram Item Database Editor")
            io.print(f"Version: {SCRIPT_VERSION}")
            io.print(f"Database: {repository.database_path}")
            io.print(f"Items: {repository.count_items():,}")
            choice = choose_menu(io, "Main menu", ["Add item", "Edit item", "Delete item", "Exit"], allow_cancel=False)
            if choice in (None, 4):
                return 0
            try:
                if choice == 1:
                    run_add_flow(repository, service, io)
                elif choice == 2:
                    run_edit_flow(repository, service, io)
                elif choice == 3:
                    run_delete_flow(repository, service, io)
            except (ValidationFailed, ValueError, KeyError) as exc:
                io.print(f"Operation failed: {exc}")
            except KeyboardInterrupt:
                io.print("Operation canceled.")
    finally:
        repository.close()


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Edit the Toram item database")
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("coryn_data/database/items.sqlite"),
        help="Path to items.sqlite",
    )
    parser.add_argument("--debug", action="store_true", help="Re-raise unexpected exceptions after logging")
    return parser.parse_args(argv)


def _configure_logging(database_path: Path) -> None:
    LOGGER.setLevel(logging.INFO)
    if not LOGGER.handlers:
        handler = logging.FileHandler(database_path.expanduser().resolve().parent / "item_editor.log", encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        LOGGER.addHandler(handler)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    _configure_logging(args.database)
    try:
        return run_editor(args.database, TerminalIO())
    except Exception as exc:
        LOGGER.exception("Unhandled editor error")
        print(f"Unexpected error: {exc}", file=sys.stderr)
        if args.debug:
            raise
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
