from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Sequence

from PySide6.QtWidgets import QApplication, QMessageBox

from toram_data import ItemRepository
from toram_gui.main_window import MainWindow

GUI_VERSION = "2026.08.07-pyside6-gui-v1.1-stat-resolution-fix"
DEFAULT_DATABASE = Path("coryn_data/database/items.sqlite")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Toram item database GUI editor")
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    return parser.parse_args(argv)


def build_main_window(database_path: Path) -> MainWindow:
    repository = ItemRepository(database_path)
    try:
        return MainWindow(repository)
    except Exception:
        repository.close()
        raise


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    app = QApplication.instance() or QApplication(sys.argv[:1])
    try:
        window = build_main_window(args.database)
    except Exception as exc:
        logging.exception("Could not start Toram item editor")
        QMessageBox.critical(None, "Toram Item Editor", str(exc))
        return 2
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
