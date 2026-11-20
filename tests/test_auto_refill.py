"""The automatic source is refilled in background before it runs dry."""
from dataclasses import replace
import time

from roften_wave.config import load_config
from roften_wave.db import Store
from roften_wave.player import Player


def song(i: int) -> dict:
    return {"id": str(i), "artist": "Test", "title": f"Song {i}",
            "duration": 10, "cover": ""}


def test_auto_refill_downloads_upcoming_track_early(tmp_path):
    class Source:
        def __init__(self):
            self.calls = 0

        def wave(self):
            self.calls += 1
            return [song(101), song(102)]

        def likes(self):
            return []

        def download(self, track_id, dest):
            dest.write_bytes(b"synthetic audio" * 32)

    source = Source()
    store = Store(tmp_path / "wave.sqlite3")
    cfg = replace(load_config(), data_dir=tmp_path)
    player = Player(store, source, cfg)
    player.set_mode("wave")
    player._stage_next()
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline and not player.snapshot()["prefetch"]["ready"]:
        time.sleep(0.01)
    assert source.calls == 1
    assert [t["id"] for t in player.autos] == ["101", "102"]
    assert player.snapshot()["prefetch"]["track_id"] == "101"
    assert player.snapshot()["prefetch"]["ready"]
    player.stop()
    store.close()


def test_old_auto_fetch_is_discarded_on_mode_change(tmp_path):
    import threading
    started = threading.Event()
    release = threading.Event()
    class Source:
        def wave(self):
            started.set()
            release.wait(2)
            return [song(201)]

        def likes(self):
            return []

        def download(self, track_id, dest):
            dest.write_bytes(b"x")

    store = Store(tmp_path / "wave.sqlite3")
    player = Player(store, Source(), replace(load_config(), data_dir=tmp_path))
    player.set_mode("wave")
    player._stage_next()
    assert started.wait(2)
    player.set_mode("manual")
    release.set()
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and player.auto_pending is not None and not player.auto_pending.done():
        time.sleep(0.01)
    assert list(player.autos) == []
    player.stop()
    store.close()
