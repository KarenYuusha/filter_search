from __future__ import annotations

import hashlib
import re
import shutil
import tempfile
import unicodedata
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Iterable

from .models import ImageDraft


class ImageStoreError(RuntimeError):
    pass


def slugify_path_component(value: str) -> str:
    text = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-") or "unknown"


def sanitize_filename(name: str) -> str:
    path = Path(name)
    stem = unicodedata.normalize("NFKC", path.stem).casefold()
    stem = re.sub(r"[^a-z0-9]+", "-", stem).strip("-") or "image"
    suffix = re.sub(r"[^a-z0-9.]", "", path.suffix.casefold())
    return stem + suffix


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass
class PreparedImageBatch:
    database_root: Path
    staging_root: Path
    staged_to_final: list[tuple[Path, Path, int]]
    prepared_images: list[ImageDraft]
    created_final_paths: list[Path] = field(default_factory=list)

    def materialize(self) -> list[ImageDraft]:
        try:
            for staged, final, _index in self.staged_to_final:
                final.parent.mkdir(parents=True, exist_ok=True)
                staged.replace(final)
                self.created_final_paths.append(final)
        except Exception:
            self.rollback_new_files()
            raise
        return self.prepared_images

    def rollback_new_files(self) -> None:
        for path in reversed(self.created_final_paths):
            path.unlink(missing_ok=True)
            try:
                path.parent.rmdir()
            except OSError:
                pass
        self.created_final_paths.clear()

    def cleanup_staging(self) -> None:
        shutil.rmtree(self.staging_root, ignore_errors=True)


class ManagedImageStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = Path(database_path).expanduser().resolve()
        self.database_root = self.database_path.parent
        self.data_root = (
            self.database_root.parent
            if self.database_root.name.casefold() == "database"
            else self.database_root
        )
        self.managed_root = self.data_root / "appearance"

    def stage(
        self,
        item_id: int,
        item_type: str,
        item_name: str,
        images: list[ImageDraft],
    ) -> PreparedImageBatch:
        staging_parent = self.database_root / ".item-image-staging"
        staging_parent.mkdir(parents=True, exist_ok=True)
        staging_root = Path(tempfile.mkdtemp(prefix=f"item-{item_id}-", dir=staging_parent))
        prepared = list(images)
        transfers: list[tuple[Path, Path, int]] = []
        reserved: set[Path] = set()
        item_directory = (
            self.managed_root
            / slugify_path_component(item_type)
            / f"{item_id}-{slugify_path_component(item_name)}"
        )
        try:
            for index, image in enumerate(images):
                if image.selected_source_path is None:
                    continue
                source = Path(image.selected_source_path).expanduser().resolve()
                if not source.is_file():
                    raise ImageStoreError(f"Image source is not a readable file: {source}")
                try:
                    with source.open("rb"):
                        pass
                except OSError as exc:
                    raise ImageStoreError(f"Image source is not readable: {source}") from exc

                filename = f"{index:02d}-{sanitize_filename(source.name)}"
                final = item_directory / filename
                counter = 2
                while final.exists() or final in reserved:
                    final = final.with_name(f"{final.stem}-{counter}{final.suffix}")
                    counter += 1
                reserved.add(final)
                staged = staging_root / final.name
                shutil.copy2(source, staged)
                if file_sha256(source) != file_sha256(staged):
                    raise ImageStoreError(f"Image copy verification failed: {source}")
                relative = final.relative_to(self.data_root).as_posix()
                prepared[index] = replace(image, local_path=relative, selected_source_path=None)
                transfers.append((staged, final, index))
            return PreparedImageBatch(self.data_root, staging_root, transfers, prepared)
        except Exception:
            shutil.rmtree(staging_root, ignore_errors=True)
            raise

    def _managed_candidate(self, value: str) -> Path | None:
        raw = Path(value).expanduser()
        if raw.is_absolute():
            candidate = raw.resolve()
        else:
            candidate = (self.data_root / raw).resolve()
        try:
            candidate.relative_to(self.managed_root.resolve())
        except ValueError:
            return None
        return candidate

    def delete_managed_paths(self, relative_paths: Iterable[str]) -> list[Path]:
        failures: list[Path] = []
        for value in relative_paths:
            raw = Path(value).expanduser()
            candidate = self._managed_candidate(value)
            if candidate is None:
                failures.append(raw if raw.is_absolute() else self.data_root / raw)
                continue
            try:
                if candidate.is_file() or candidate.is_symlink():
                    candidate.unlink()
                elif candidate.exists():
                    failures.append(candidate)
                    continue
                parent = candidate.parent
                while parent != self.managed_root and parent.is_relative_to(self.managed_root):
                    try:
                        parent.rmdir()
                    except OSError:
                        break
                    parent = parent.parent
            except OSError:
                failures.append(candidate)
        return failures

    def delete_item_directory(self, item_id: int) -> list[Path]:
        if not self.managed_root.exists():
            return []

        failures: list[Path] = []
        pattern = f"{item_id}-*"
        candidates = sorted(self.managed_root.glob(f"*/{pattern}"))
        for raw_item_dir in candidates:
            item_dir = raw_item_dir.resolve()
            try:
                item_dir.relative_to(self.managed_root.resolve())
            except ValueError:
                failures.append(raw_item_dir)
                continue

            if not item_dir.exists():
                continue
            for path in sorted(item_dir.rglob("*"), reverse=True):
                try:
                    if path.is_file() or path.is_symlink():
                        path.unlink()
                    elif path.is_dir():
                        path.rmdir()
                except OSError:
                    failures.append(path)
            try:
                item_dir.rmdir()
            except OSError:
                if item_dir.exists():
                    failures.append(item_dir)
                continue

            parent = raw_item_dir.parent
            if parent != self.managed_root:
                try:
                    parent.rmdir()
                except OSError:
                    pass
        return failures
