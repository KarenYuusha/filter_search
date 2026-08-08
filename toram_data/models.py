from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


@dataclass
class ConditionDraft:
    mode: Literal["none", "known", "free_text", "preserve"] = "none"
    slugs: tuple[str, ...] = ()
    text: str | None = None
    coryn_applies_to: int | None = None
    needs_review: bool = False


@dataclass
class StatDraft:
    stat_name: str
    amount: float | None = None
    condition: ConditionDraft = field(default_factory=ConditionDraft)
    raw_conditions_json: str | None = None
    original_position: int | None = None


@dataclass
class SourceDraft:
    source_id: int | None = None
    source_name: str | None = None
    level: int | None = None
    map: str | None = None
    dye: str | None = None
    source_url: str | None = None
    lookup_error: str | None = None
    raw_cells_json: str | None = None
    original_position: int | None = None


@dataclass
class ImageDraft:
    category: str | None = None
    gender: str | None = None
    variant: str | None = None
    local_path: str | None = None
    source_url: str | None = None
    selected_source_path: Path | None = None
    original_local_path: str | None = None
    original_position: int | None = None


@dataclass(frozen=True)
class PositionedStat:
    position: int
    value: StatDraft


@dataclass(frozen=True)
class PositionedSource:
    position: int
    value: SourceDraft


@dataclass(frozen=True)
class PositionedImage:
    position: int
    value: ImageDraft


def manual_json_path(item_id: int) -> str:
    return f"manual://items/{item_id}"


@dataclass
class ItemDraft:
    id: int
    schema_version: int
    name: str = ""
    item_type: str = ""
    sell_price: float | None = None
    process_material: str | None = None
    process_amount: float | None = None
    badge: str | None = None
    note: str | None = None
    api_url: str | None = None
    page_url: str | None = None
    json_path: str = ""
    stats: list[StatDraft] = field(default_factory=list)
    sources: list[SourceDraft] = field(default_factory=list)
    images: list[ImageDraft] = field(default_factory=list)
    previous_upgrade_item_id: int | None = None
    image_error_rows: tuple[dict[str, object], ...] = ()
    existing_upgrade_item_ids: tuple[int, ...] = ()
    malformed_upgrade_rows: tuple[dict[str, object], ...] = ()
    is_new: bool = True

    @classmethod
    def new(cls, *, item_id: int, schema_version: int) -> "ItemDraft":
        return cls(id=item_id, schema_version=schema_version, json_path=manual_json_path(item_id), is_new=True)

    def set_new_item_id(self, item_id: int) -> None:
        if not self.is_new:
            raise ValueError("Existing item IDs cannot be changed")
        self.id = item_id
        self.json_path = manual_json_path(item_id)

    def positioned_stats(self) -> list[PositionedStat]:
        return [PositionedStat(index, value) for index, value in enumerate(self.stats)]

    def positioned_sources(self) -> list[PositionedSource]:
        return [PositionedSource(index, value) for index, value in enumerate(self.sources)]

    def positioned_images(self) -> list[PositionedImage]:
        return [PositionedImage(index, value) for index, value in enumerate(self.images)]


@dataclass(frozen=True)
class ItemSnapshot:
    draft: ItemDraft


def normalize_positions(draft: ItemDraft) -> ItemDraft:
    """Positions are list-derived; return the same mutable draft for fluent use."""
    return draft
