from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


class BackupError(RuntimeError):
    pass


class BackupManager:
    def __init__(
        self,
        database_path: Path,
        backup_directory: Path | None = None,
        keep: int = 10,
    ) -> None:
        if keep < 1:
            raise ValueError("Backup retention must be at least 1")
        self.database_path = Path(database_path).expanduser().resolve()
        self.backup_directory = (
            Path(backup_directory).expanduser().resolve()
            if backup_directory is not None
            else self.database_path.parent / "backups"
        )
        self.keep = keep

    def create_verified_backup(self, *, now: datetime | None = None) -> Path:
        timestamp = now or datetime.now(timezone.utc)
        stem = timestamp.strftime("items-%Y%m%d-%H%M%S%f")
        final_path = self.backup_directory / f"{stem}.sqlite"
        partial_path = self.backup_directory / f"{stem}.partial"
        self.backup_directory.mkdir(parents=True, exist_ok=True)
        try:
            self._copy_database(partial_path)
            with closing(sqlite3.connect(partial_path)) as verification:
                result = verification.execute("PRAGMA integrity_check").fetchone()[0]
            if result != "ok":
                raise BackupError(f"Backup integrity check failed: {result}")
            partial_path.replace(final_path)
            self._prune_verified_backups()
            return final_path
        except Exception as exc:
            partial_path.unlink(missing_ok=True)
            if isinstance(exc, BackupError):
                raise
            raise BackupError(f"Could not create database backup: {exc}") from exc

    def _copy_database(self, destination_path: Path) -> None:
        with closing(sqlite3.connect(self.database_path)) as source:
            with closing(sqlite3.connect(destination_path)) as destination:
                source.backup(destination)

    def _prune_verified_backups(self) -> None:
        candidates = sorted(self.backup_directory.glob("items-*.sqlite"), key=lambda path: path.name, reverse=True)
        verified: list[Path] = []
        for path in candidates:
            try:
                with closing(sqlite3.connect(path)) as connection:
                    result = connection.execute("PRAGMA integrity_check").fetchone()[0]
            except sqlite3.Error:
                continue
            if result == "ok":
                verified.append(path)
        for path in verified[self.keep:]:
            path.unlink(missing_ok=True)
