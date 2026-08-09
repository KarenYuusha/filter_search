from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from toram_data.editor_service import EditorService
from toram_data.images import ManagedImageStore
from toram_data.models import ImageDraft, ItemDraft
from toram_data.validation import ValidationReport


class ManagedImageStoreAppearanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.data_root = self.root / "coryn_data"
        self.database_dir = self.data_root / "database"
        self.database_dir.mkdir(parents=True)
        self.database_path = self.database_dir / "items.sqlite"
        self.database_path.touch()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_stage_uses_existing_appearance_hierarchy(self) -> None:
        source = self.root / "My Photo.JPG"
        source.write_bytes(b"image-bytes")
        store = ManagedImageStore(self.database_path)

        batch = store.stage(
            1044,
            "1 Handed Sword",
            "Rapier",
            [ImageDraft(selected_source_path=source)],
        )
        self.addCleanup(batch.cleanup_staging)

        prepared = batch.prepared_images[0]
        self.assertEqual(
            prepared.local_path,
            "../appearance/1-handed-sword/1044-rapier/00-my-photo.jpg",
        )
        self.assertIsNone(prepared.selected_source_path)

        batch.materialize()
        final = (self.database_dir / str(prepared.local_path)).resolve()
        self.assertEqual(
            final,
            (
                self.data_root
                / "appearance"
                / "1-handed-sword"
                / "1044-rapier"
                / "00-my-photo.jpg"
            ).resolve(),
        )
        self.assertTrue(final.is_file())
        self.assertEqual(final.read_bytes(), b"image-bytes")
        self.assertFalse((self.database_dir / "item_images").exists())

    def test_managed_candidate_accepts_appearance_and_legacy_editor_paths(self) -> None:
        store = ManagedImageStore(self.database_path)

        appearance = "../appearance/bow/500-test-bow/00-photo.jpg"
        legacy = "item_images/500/00-photo.jpg"

        self.assertEqual(
            store._managed_candidate(appearance),
            (self.database_dir / appearance).resolve(),
        )
        self.assertEqual(
            store._managed_candidate(legacy),
            (self.database_dir / legacy).resolve(),
        )
        self.assertIsNone(store._managed_candidate("../manual-images/photo.jpg"))

    def test_delete_item_directory_removes_slugged_and_legacy_folders(self) -> None:
        store = ManagedImageStore(self.database_path)
        old_name = self.data_root / "appearance" / "1-handed-sword" / "1044-old-name"
        moved_type = self.data_root / "appearance" / "bow" / "1044-new-name"
        other_item = self.data_root / "appearance" / "bow" / "1045-other-item"
        legacy = self.database_dir / "item_images" / "1044"
        for directory in (old_name, moved_type, other_item, legacy):
            directory.mkdir(parents=True)
            (directory / "image.jpg").write_bytes(b"x")

        failures = store.delete_item_directory(1044)

        self.assertEqual(failures, [])
        self.assertFalse(old_name.exists())
        self.assertFalse(moved_type.exists())
        self.assertFalse(legacy.exists())
        self.assertTrue(other_item.exists())


class _RecordingBatch:
    def __init__(self, images: list[ImageDraft]) -> None:
        self.images = images

    def materialize(self) -> list[ImageDraft]:
        return self.images

    def rollback_new_files(self) -> None:
        pass

    def cleanup_staging(self) -> None:
        pass


class _RecordingImageStore:
    def __init__(self) -> None:
        self.stage_args: tuple[object, ...] | None = None

    def stage(
        self,
        item_id: int,
        item_type: str,
        item_name: str,
        images: list[ImageDraft],
    ) -> _RecordingBatch:
        self.stage_args = (item_id, item_type, item_name, images)
        return _RecordingBatch(images)

    def delete_managed_paths(self, relative_paths):
        return []


class _FakeRepository:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.saved: ItemDraft | None = None

    def add_item(self, draft: ItemDraft) -> None:
        self.saved = draft

    def update_item(self, draft: ItemDraft) -> None:
        self.saved = draft


class _FakeBackupManager:
    def __init__(self, backup_path: Path) -> None:
        self.backup_path = backup_path

    def create_verified_backup(self) -> Path:
        return self.backup_path


class EditorServiceImageMetadataTests(unittest.TestCase):
    def test_save_passes_item_type_and_name_to_image_store(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            database_path = root / "coryn_data" / "database" / "items.sqlite"
            database_path.parent.mkdir(parents=True)
            database_path.touch()
            repository = _FakeRepository(database_path)
            image_store = _RecordingImageStore()
            service = EditorService(
                repository,
                backup_manager=_FakeBackupManager(root / "backup.sqlite"),
                image_store=image_store,
            )
            draft = ItemDraft(
                id=1044,
                schema_version=1,
                name="Rapier",
                item_type="1 Handed Sword",
                images=[ImageDraft()],
            )

            with patch(
                "toram_data.editor_service.validate_item_draft",
                return_value=ValidationReport(()),
            ):
                service.save_add(draft)

            self.assertIsNotNone(image_store.stage_args)
            assert image_store.stage_args is not None
            self.assertEqual(image_store.stage_args[:3], (1044, "1 Handed Sword", "Rapier"))
            self.assertIs(image_store.stage_args[3], draft.images)


if __name__ == "__main__":
    unittest.main()
