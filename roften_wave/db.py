from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any


class Store:
    """Small transactional SQLite queue; one app process, multiple worker threads."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.execute("PRAGMA busy_timeout=5000")
            self.connection.executescript("""
                CREATE TABLE IF NOT EXISTS queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    track TEXT NOT NULL,
                    priority INTEGER NOT NULL DEFAULT 0,
                    created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    track TEXT NOT NULL,
                    created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)
            columns = {row["name"] for row in self.connection.execute("PRAGMA table_info(queue)")}
            if "inflight" not in columns:
                self.connection.execute("ALTER TABLE queue ADD COLUMN inflight INTEGER NOT NULL DEFAULT 0")
            # Recover a track that was playing when the process stopped.
            self.connection.execute("UPDATE queue SET inflight=0 WHERE inflight<>0")

    def close(self) -> None:
        with self.lock:
            self.connection.close()

    def add(self, track: dict[str, Any], *, first: bool = False) -> int:
        with self.lock:
            row = self.connection.execute("SELECT MIN(priority) AS p FROM queue WHERE inflight=0").fetchone()
            priority = min(0, (row["p"] or 0) - 1) if first else 0
            cur = self.connection.execute("INSERT INTO queue(track,priority) VALUES(?,?)", (json.dumps(track, ensure_ascii=False), priority))
            return int(cur.lastrowid)

    def add_many(self, tracks: list[dict[str, Any]], *, limit: int = 300) -> int:
        if len(tracks) > limit:
            tracks = tracks[:limit]
        with self.lock:
            self.connection.executemany("INSERT INTO queue(track) VALUES(?)", [(json.dumps(t, ensure_ascii=False),) for t in tracks])
        return len(tracks)

    def queue(self) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.connection.execute("SELECT id,track FROM queue WHERE inflight=0 ORDER BY priority,id").fetchall()
        return [{"queue_id": r["id"], **json.loads(r["track"])} for r in rows]

    def peek(self) -> dict[str, Any] | None:
        """Read the next queued track without claiming or modifying it."""
        with self.lock:
            row = self.connection.execute("SELECT track FROM queue WHERE inflight=0 ORDER BY priority,id LIMIT 1").fetchone()
        return json.loads(row["track"]) if row else None

    def claim(self) -> tuple[int, dict[str, Any]] | None:
        """Atomically claim a queue item while retaining it across crashes."""
        with self.lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                row = self.connection.execute("SELECT id,track FROM queue WHERE inflight=0 ORDER BY priority,id LIMIT 1").fetchone()
                if row is not None:
                    self.connection.execute("UPDATE queue SET inflight=1 WHERE id=?", (row["id"],))
                self.connection.execute("COMMIT")
            except Exception:
                self.connection.execute("ROLLBACK")
                raise
        return (int(row["id"]), json.loads(row["track"])) if row else None

    def ack(self, queue_id: int) -> None:
        """Delete a track after successful playback or an explicit skip."""
        with self.lock:
            self.connection.execute("DELETE FROM queue WHERE id=? AND inflight=1", (queue_id,))

    def release(self, queue_id: int) -> None:
        """Make an unfinished track available again, e.g. after an error."""
        with self.lock:
            self.connection.execute("UPDATE queue SET inflight=0 WHERE id=? AND inflight=1", (queue_id,))

    def pop(self) -> dict[str, Any] | None:
        """Legacy one-shot removal, not used by the playback worker."""
        with self.lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                row = self.connection.execute("SELECT id,track FROM queue WHERE inflight=0 ORDER BY priority,id LIMIT 1").fetchone()
                if row is None:
                    self.connection.execute("COMMIT")
                    return None
                self.connection.execute("DELETE FROM queue WHERE id=?", (row["id"],))
                self.connection.execute("COMMIT")
            except Exception:
                self.connection.execute("ROLLBACK")
                raise
        return json.loads(row["track"])

    def remove(self, queue_id: int) -> bool:
        with self.lock:
            cur = self.connection.execute("DELETE FROM queue WHERE id=? AND inflight=0", (queue_id,))
            return bool(cur.rowcount)

    def reorder(self, ordered_ids: list[int]) -> None:
        with self.lock:
            original = [r["queue_id"] for r in self.queue()]
            if len(ordered_ids) != len(original) or len(set(ordered_ids)) != len(ordered_ids) or set(ordered_ids) != set(original):
                raise ValueError("Queue order must contain each queued ID exactly once")
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                for n, item_id in enumerate(ordered_ids):
                    self.connection.execute("UPDATE queue SET priority=? WHERE id=?", (n + 1, item_id))
                self.connection.execute("COMMIT")
            except Exception:
                self.connection.execute("ROLLBACK")
                raise

    def get(self, key: str, default: str = "") -> str:
        with self.lock:
            row = self.connection.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row is not None else default

    def set(self, key: str, value: str) -> None:
        with self.lock:
            self.connection.execute("INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    def record(self, track: dict[str, Any]) -> None:
        with self.lock:
            self.connection.execute("INSERT INTO history(track) VALUES(?)", (json.dumps(track, ensure_ascii=False),))
            self.connection.execute("DELETE FROM history WHERE id NOT IN (SELECT id FROM history ORDER BY id DESC LIMIT 50)")

    def history(self) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.connection.execute("SELECT track FROM history ORDER BY id DESC LIMIT 50").fetchall()
        return [json.loads(r["track"]) for r in rows]
