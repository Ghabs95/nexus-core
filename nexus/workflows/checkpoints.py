"""Checkpoint stores for provider-neutral Nexus workflows."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, MutableMapping
from pathlib import Path
from typing import Any


class JsonCheckpointStore(MutableMapping[str, Any]):
    """Small JSON-backed checkpoint mapping.

    The store is intentionally simple and deterministic: every write flushes the
    full JSON document. It is suitable for workflow outputs that are JSON-safe
    primitives, lists, and dictionaries. Non-JSON-serializable outputs raise at
    assignment time rather than being silently corrupted.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._data: dict[str, Any] = {}
        self._load()

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __setitem__(self, key: str, value: Any) -> None:
        json.dumps(value)
        self._data[key] = value
        self.flush()

    def __delitem__(self, key: str) -> None:
        del self._data[key]
        self.flush()

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def flush(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, indent=2, sort_keys=True))

    def _load(self) -> None:
        if not self.path.exists():
            self._data = {}
            return
        raw = self.path.read_text().strip()
        self._data = json.loads(raw) if raw else {}


class SQLiteCheckpointStore(MutableMapping[str, Any]):
    """SQLite-backed checkpoint mapping for durable workflow resumes.

    Values are stored as JSON. This keeps the store provider-neutral and easy to
    inspect while avoiding the whole-file rewrite behavior of JsonCheckpointStore.
    """

    def __init__(self, path: str | Path, *, table: str = "workflow_checkpoints") -> None:
        self.path = Path(path)
        self.table = table
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.execute(
            f"CREATE TABLE IF NOT EXISTS {self.table} "
            "(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        self._conn.commit()

    def __getitem__(self, key: str) -> Any:
        row = self._conn.execute(
            f"SELECT value FROM {self.table} WHERE key = ?",
            (key,),
        ).fetchone()
        if row is None:
            raise KeyError(key)
        return json.loads(row[0])

    def __setitem__(self, key: str, value: Any) -> None:
        encoded = json.dumps(value)
        self._conn.execute(
            f"INSERT OR REPLACE INTO {self.table} (key, value) VALUES (?, ?)",
            (key, encoded),
        )
        self._conn.commit()

    def __delitem__(self, key: str) -> None:
        cur = self._conn.execute(f"DELETE FROM {self.table} WHERE key = ?", (key,))
        self._conn.commit()
        if cur.rowcount == 0:
            raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        rows = self._conn.execute(f"SELECT key FROM {self.table} ORDER BY key")
        return (str(row[0]) for row in rows.fetchall())

    def __len__(self) -> int:
        row = self._conn.execute(f"SELECT COUNT(*) FROM {self.table}").fetchone()
        return int(row[0])

    def close(self) -> None:
        self._conn.close()
