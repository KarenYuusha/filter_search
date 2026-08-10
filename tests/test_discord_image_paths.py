from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from discord_bot import valid_local_image_paths


class DiscordImagePathTests(unittest.TestCase):
    def test_parser_appearance_path_resolves_from_coryn_data_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data_root = root / "coryn_data"
            database_path = data_root / "database" / "items.sqlite"
            database_path.parent.mkdir(parents=True)
            database_path.touch()
            image = data_root / "appearance" / "bow" / "1226-1st-anniv-bow" / "item_01.jpg"
            image.parent.mkdir(parents=True)
            image.write_bytes(b"image")

            paths = valid_local_image_paths(
                database_path,
                [{"local_path": "appearance/bow/1226-1st-anniv-bow/item_01.jpg"}],
            )

            self.assertEqual(paths, (image.resolve(),))

    def test_editor_relative_appearance_path_still_resolves_from_database_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data_root = root / "coryn_data"
            database_path = data_root / "database" / "items.sqlite"
            database_path.parent.mkdir(parents=True)
            database_path.touch()
            image = data_root / "appearance" / "armor" / "sample" / "00-photo.jpg"
            image.parent.mkdir(parents=True)
            image.write_bytes(b"image")

            paths = valid_local_image_paths(
                database_path,
                [{"local_path": "../appearance/armor/sample/00-photo.jpg"}],
            )

            self.assertEqual(paths, (image.resolve(),))


if __name__ == "__main__":
    unittest.main()
