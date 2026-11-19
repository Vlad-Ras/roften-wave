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
