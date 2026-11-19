#!/usr/bin/env python3
"""Consistent SQLite backup/restore for the Roften Wave Docker volume.

Runs inside a short-lived local, network-disabled Python container.
Uses SQLite's online backup API so WAL state is included.
"""
from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import sys


def snapshot(mode: str, source: Path, destination: Path, uid: int, gid: int) -> None:
    if mode not in {"backup", "restore"}:
        raise ValueError("mode must be backup or restore")
    if not source.is_file():
        raise FileNotFoundError("SQLite source file does not exist")
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_name(destination.name + ".partial")
    try:
        if tmp.exists():
            tmp.unlink()
        src_uri = "file:" + source.resolve().as_posix() + "?mode=ro"
        with sqlite3.connect(src_uri, uri=True, timeout=30) as src:
            with sqlite3.connect(tmp, timeout=30) as dst:
                src.backup(dst, pages=100, sleep=0.1)
                status = dst.execute("PRAGMA integrity_check").fetchone()[0]
                if status != "ok":
                    raise RuntimeError("SQLite integrity_check failed")
        os.chmod(tmp, 0o600)
        if os.geteuid() == 0:
            os.chown(tmp, uid, gid)
        os.replace(tmp, destination)
        if mode == "restore":
            # Called only after the application has stopped. Never replay stale WAL
            # or shared-memory files from the database that we just replaced.
            for suffix in ("-wal", "-shm"):
                stale = Path(str(destination) + suffix)
                if stale.exists():
                    stale.unlink()
    finally:
        if tmp.exists():
            tmp.unlink()


def main() -> None:
    if len(sys.argv) != 6:
        raise SystemExit("Usage: sqlite_snapshot.py backup|restore SOURCE DEST UID GID")
    mode, src, dst, uid, gid = sys.argv[1:]
    snapshot(mode, Path(src), Path(dst), int(uid), int(gid))
    print(f"SQLite {mode} verified")


if __name__ == "__main__":
    main()
