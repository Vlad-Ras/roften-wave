from pathlib import Path
import pytest

from roften_wave.db import Store


def track(n):
    return {"id":str(n),"title":f"Song {n}","artist":"Artist", "duration":123, "cover":""}


def test_queue_priority_and_persistence(tmp_path):
    path = tmp_path / "wave.sqlite"
    s = Store(path)
    a = s.add(track(1)); b = s.add(track(2)); c = s.add(track(3), first=True)
    assert [t["id"] for t in s.queue()] == ["3","1","2"]
    s.reorder([b,c,a])
    assert [t["id"] for t in s.queue()] == ["2","3","1"]
    with pytest.raises(ValueError):
        s.reorder([b,b,a])
    assert s.pop()["id"] == "2"
    assert s.remove(c)
    assert not s.remove(c)
    s.record(track(1));s.record(track(2))
    assert s.history()[0]["id"]=="2"
    s.set("mode","wave")
    s.close()
    s2=Store(path)
    assert s2.get("mode")=="wave"
    assert [t["id"] for t in s2.queue()]==["1"]
    s2.close()


def test_empty(tmp_path):
    s=Store(tmp_path/"a.sqlite3")
    assert s.pop() is None
    assert s.queue()==[]
    assert s.get("missing","fallback")=="fallback"
    s.close()


def test_shuffle_dedupe_and_inflight_protection(tmp_path):
    s = Store(tmp_path / "queue.sqlite3")
    s.add(track(1)); s.add(track(2)); s.add(track(1)); s.add(track(3))
    claim = s.claim()
    assert claim[1]["id"] == "1"
    assert s.dedupe() == 0  # the queued "1" isn't a duplicate of claimed item
    s.add(track(2))
    assert s.dedupe() == 1
    ids = {item["queue_id"] for item in s.queue()}
    assert s.shuffle() == 3
    assert {item["queue_id"] for item in s.queue()} == ids
    assert s.remove(claim[0]) is False
    s.release(claim[0])
    assert s.dedupe() == 1
    assert len(s.queue()) == 3
    s.close()


def test_station_stats_persist_and_aggregate(tmp_path):
    path = tmp_path / "statistics.sqlite3"
    s = Store(path)
    assert s.stats()["finished_tracks"] == 0
    s.record_audio_result(seconds=90.25, finished=True)
    s.record_audio_result(seconds=10, failed=True)
    s.record_audio_result(seconds=5, interrupted=True)
    result = s.stats()
    assert result["audio_seconds"] == 105.2
    assert result["finished_tracks"] == 1
    assert result["failed_tracks"] == 1
    assert result["interruptions"] == 1
    assert len(result["days"]) == 1
    s.close()
    reopened = Store(path)
    assert reopened.stats()["finished_tracks"] == 1
    reopened.close()
