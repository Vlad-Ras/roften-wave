import sqlite3
import time
from dataclasses import replace

from roften_wave.config import load_config
from roften_wave.db import Store
from roften_wave.player import Player
from roften_wave.yandex import YandexSource


def item(number):
    return {'id': str(number), 'artist': 'Test', 'title': f'Track {number}', 'duration': 10, 'cover': ''}


def test_claimed_track_recovers_after_restart(tmp_path):
    path = tmp_path / 'wave.sqlite3'
    store = Store(path)
    first = store.add(item(11)); store.add(item(22))
    claim = store.claim()
    assert claim is not None and claim[0] == first
    assert [t['id'] for t in store.queue()] == ['22']
    store.close()
    # Startup upgrade + recover requeues unfinished items with original order.
    recovered = Store(path)
    assert [t['id'] for t in recovered.queue()] == ['11', '22']
    c = recovered.claim()
    recovered.ack(c[0])
    assert [t['id'] for t in recovered.queue()] == ['22']
    recovered.close()


def test_010_database_migration(tmp_path):
    path = tmp_path / 'old.sqlite3'
    con = sqlite3.connect(path)
    con.execute('CREATE TABLE queue(id INTEGER PRIMARY KEY AUTOINCREMENT, track TEXT NOT NULL, priority INTEGER NOT NULL DEFAULT 0, created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)')
    con.execute('INSERT INTO queue(track) VALUES(?)', ('{"id":"7","artist":"Old","title":"Old"}',))
    con.commit(); con.close()
    s = Store(path)
    assert s.claim()[1]['id'] == '7'
    s.close()


def test_skip_paused_track_releases_claim(tmp_path):
    store = Store(tmp_path / 'wave.sqlite3')
    player = Player(store, YandexSource(''), replace(load_config(), data_dir=tmp_path))
    queue_id = player.enqueue(item(1))
    claimed_id, track = store.claim()
    assert queue_id == claimed_id
    player.resume_track = {**track, '_queue_id': claimed_id, '_resume_at': 4.0}
    player.paused = True
    player.next()
    assert store.queue() == []
    assert player.resume_track is None
    player.stop(); store.close()


def test_interrupted_download_cleans_temporary_file(tmp_path):
    class SlowSource:
        def download(self, _id, path):
            time.sleep(0.4)
            path.write_bytes(b'delayed temporary content')
        def wave(self): return []
        def likes(self): return []

    config = replace(load_config(), data_dir=tmp_path, yandex_token='test', dj_host='127.0.0.1', dj_port=8005,
                     dj_mount='/live', dj_username='dj', dj_password='secret')
    store = Store(tmp_path / 'wave.sqlite3')
    player = Player(store, SlowSource(), config)
    player.enqueue(item(5))
    player.start()
    until = time.monotonic() + 2
    while player.snapshot()['status'] != 'loading' and time.monotonic() < until:
        time.sleep(0.02)
    assert player.snapshot()['status'] == 'loading'
    player.next()
    until = time.monotonic() + 3
    while list(tmp_path.glob('wave-*.mp3')) and time.monotonic() < until:
        time.sleep(0.05)
    assert list(tmp_path.glob('wave-*.mp3')) == []
    player.stop()
    assert store.queue() == []
    store.close()
