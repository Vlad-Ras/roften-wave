from pathlib import Path
import sqlite3
import subprocess
import sys


SCRIPT = Path(__file__).resolve().parents[1] / "deploy" / "sqlite_snapshot.py"


def run_script(mode: str, src: Path, dst: Path) -> None:
    subprocess.run(
        [sys.executable, str(SCRIPT), mode, str(src), str(dst), "0", "0"],
        check=True,
        capture_output=True,
        text=True,
    )


def test_backup_includes_uncheckpointed_wal_and_restore_is_consistent(tmp_path):
    database = tmp_path / "data" / "wave.sqlite3"
    database.parent.mkdir()
    backup = tmp_path / "backups" / "wave.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE queue(id INTEGER PRIMARY KEY, track TEXT)")
        connection.execute("INSERT INTO queue(track) VALUES('expected')")
        connection.commit()
        run_script("backup", database, backup)
        assert backup.exists()
        assert backup.stat().st_size > 0
        connection.execute("INSERT INTO queue(track) VALUES('should-rollback')")
        connection.commit()

    run_script("restore", backup, database)
    # The restored database must have no WAL from the previous incarnation.
    assert not Path(str(database) + "-wal").exists()
    assert not Path(str(database) + "-shm").exists()
    with sqlite3.connect(database) as restored:
        rows = restored.execute("SELECT track FROM queue ORDER BY id").fetchall()
    assert rows == [("expected",)]


def test_invalid_source_cannot_replace_destination(tmp_path):
    source = tmp_path / "missing.sqlite3"
    target = tmp_path / "existing.sqlite3"
    target.write_bytes(b"keep")
    outcome = subprocess.run(
        [sys.executable, str(SCRIPT), "backup", str(source), str(target), "0", "0"],
        capture_output=True,
    )
    assert outcome.returncode != 0
    assert target.read_bytes() == b"keep"
