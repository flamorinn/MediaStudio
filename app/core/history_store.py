"""SQLite tabanlı işlem geçmişi (%APPDATA%\\MediaStudio\\history.db)."""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.models.history import HistoryEntry, OperationKind, OperationStatus
from app.utils.paths import user_data_dir

_SCHEMA = """
CREATE TABLE IF NOT EXISTS history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT    NOT NULL,
    kind        TEXT    NOT NULL,
    title       TEXT    NOT NULL,
    status      TEXT    NOT NULL,
    source      TEXT    NOT NULL DEFAULT '',
    output_path TEXT    NOT NULL DEFAULT '',
    size_bytes  INTEGER NOT NULL DEFAULT 0,
    details     TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_history_created ON history(created_at DESC);
"""
_COLUMNS = "id, created_at, kind, title, status, source, output_path, size_bytes, details"
_DOWNLOAD_KINDS = tuple(k.value for k in OperationKind if k.is_download)


@dataclass(frozen=True)
class HistoryStats:
    total: int = 0
    completed: int = 0
    failed: int = 0
    downloaded_files: int = 0
    downloaded_bytes: int = 0
    last_7_days: int = 0


def _row_to_entry(row: tuple) -> HistoryEntry:
    id_, created, kind, title, status, source, output, size, details = row
    return HistoryEntry(id_, created, OperationKind(kind), title, OperationStatus(status),
                        source, output, size, details)


class HistoryStore:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or (user_data_dir() / "history.db")
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def add(self, *, kind: OperationKind, title: str, status: OperationStatus, source: str = "",
            output_path: str = "", size_bytes: int = 0, details: str = "",
            created_at: datetime | None = None) -> HistoryEntry:
        kind = OperationKind(kind)
        status = OperationStatus(status)
        created = (created_at or datetime.now()).isoformat(timespec="seconds")
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO history (created_at, kind, title, status, source, output_path, size_bytes, details) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (created, kind.value, title, status.value, source, output_path, int(size_bytes), details),
            )
            self._conn.commit()
            return HistoryEntry(cur.lastrowid, created, kind, title, status, source, output_path,
                                int(size_bytes), details)

    def list(self, limit: int = 1000, kind: OperationKind | None = None, search: str = "") -> list[HistoryEntry]:
        sql = f"SELECT {_COLUMNS} FROM history WHERE 1=1"
        params: list = []
        if kind is not None:
            sql += " AND kind = ?"
            params.append(OperationKind(kind).value)
        if search.strip():
            sql += " AND (title LIKE ? OR source LIKE ? OR output_path LIKE ?)"
            like = f"%{search.strip()}%"
            params += [like, like, like]
        sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [_row_to_entry(r) for r in rows]

    def stats(self, now: datetime | None = None) -> HistoryStats:
        now = now or datetime.now()
        week_ago = datetime.fromtimestamp(now.timestamp() - 7 * 86400).isoformat(timespec="seconds")
        marks = ",".join("?" * len(_DOWNLOAD_KINDS))
        with self._lock:
            row = self._conn.execute(
                f"""SELECT COUNT(*),
                       SUM(status = 'success'),
                       SUM(status = 'failed'),
                       SUM(status = 'success' AND kind IN ({marks})),
                       SUM(CASE WHEN status = 'success' AND kind IN ({marks}) THEN size_bytes ELSE 0 END),
                       SUM(created_at >= ?)
                    FROM history""",
                (*_DOWNLOAD_KINDS, *_DOWNLOAD_KINDS, week_ago),
            ).fetchone()
        return HistoryStats(*(int(v or 0) for v in row))

    def count(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM history").fetchone()[0]

    def delete(self, entry_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM history WHERE id = ?", (entry_id,))
            self._conn.commit()
            return cur.rowcount > 0

    def clear(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM history")
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()
