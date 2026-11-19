"""Recovery tests never access a Yandex account or a running AzuraCast station."""
from dataclasses import replace
import time

from roften_wave.config import load_config
from roften_wave.db import Store
from roften_wave.player import Player
from roften_wave.resilience import RecoveryGate


def track(number='101'):
    return {'id': number, 'title': 'Example', 'artist': 'Unit', 'duration': 5, 'cover': ''}


def player_for(tmp_path):
    class Source:
        def download(self, track_id, path):
            path.write_bytes(b'mock pcm input')
        def wave(self):
            return []
        def likes(self):
            return []

    cfg = replace(load_config(), data_dir=tmp_path, yandex_token='mock', dj_host='localhost',
                  dj_port=8005, dj_mount='/live', dj_username='mock', dj_password='mock')
    store = Store(tmp_path / 'wave.sqlite3')
    return Player(store, Source(), cfg), store


def test_circuit_opens_and_recovers_with_expanding_cooldown():
    gate = RecoveryGate(threshold=3, base_cooldown_seconds=10, max_cooldown_seconds=25)
    for now in (0, 1, 2):
        gate.failed(now)
    assert gate.seconds_until_probe(3) == 9
    assert gate.seconds_until_probe(13) == 0
    for now in (13, 14, 15):
        gate.failed(now)
    assert gate.seconds_until_probe(15) == 20
    for now in (40, 41, 42):
        gate.failed(now)
    assert gate.seconds_until_probe(42) == 25
    gate.succeeded()
    assert gate.trips == 0
    assert gate.seconds_until_probe(43) == 0
    assert gate.total_failures == 9


def test_circuit_forgets_old_failures():
    gate = RecoveryGate(threshold=2, window_seconds=10, base_cooldown_seconds=5)
    gate.failed(0)
    gate.failed(11)
    assert gate.seconds_until_probe(11) == 0
    gate.failed(12)
    assert gate.seconds_until_probe(12) == 5


def test_manual_retries_are_bounded_and_request_is_preserved(monkeypatch, tmp_path):
    player, store = player_for(tmp_path)
    def disconnected(_config):
        raise BrokenPipeError('fake DJ outage')
    monkeypatch.setattr('roften_wave.player.make_sink', disconnected)
    qid = player.enqueue(track())
    claim_id, requested = store.claim()
    assert qid == claim_id
    attempt = {**requested, '_queue_id': qid}
    for count in (1, 2):
        player._play_track(attempt)
        state = player.snapshot()
        assert state['status'] == 'recovering'
        assert state['recovery']['attempt'] == count
        assert state['recovery']['retry_in_seconds'] > 0
        assert store.queue() == []  # still claimed, not lost
        attempt = player._next_track()
        assert attempt['_queue_id'] == qid
        assert attempt['_retry_attempt'] == count
    player._play_track(attempt)
    assert player.snapshot()['status'] == 'error'
    assert player.snapshot()['paused']
    assert store.queue()[0]['queue_id'] == qid
    assert player.resume_track is None
    player.stop(); store.close()


def test_autoplay_skips_exhausted_item_without_poisoning_queue(monkeypatch, tmp_path):
    player, store = player_for(tmp_path)
    monkeypatch.setattr('roften_wave.player.make_sink', lambda _config: (_ for _ in ()).throw(OSError('outage')))
    player._play_track(track())
    attempt = player._next_track()
    assert attempt['_retry_attempt'] == 1
    player._play_track(attempt)
    attempt = player._next_track()
    assert attempt['_retry_attempt'] == 2
    player._play_track(attempt)
    assert player.resume_track is None
    assert not player.paused
    assert store.queue() == []
    assert player.snapshot()['recovery']['total_failures'] == 3
    player.stop(); store.close()


def test_skip_cancels_retry_and_acknowledges_manual_claim(monkeypatch, tmp_path):
    player, store = player_for(tmp_path)
    monkeypatch.setattr('roften_wave.player.make_sink', lambda _config: (_ for _ in ()).throw(OSError('outage')))
    qid = player.enqueue(track())
    _, item = store.claim()
    player._play_track({**item, '_queue_id': qid})
    assert player.resume_track is not None
    player.next()
    assert player.resume_track is None
    assert player.retry_at == 0
    assert store.queue() == []
    player.stop(); store.close()


def test_autoplay_playlist_error_uses_cooldown(tmp_path):
    player, store = player_for(tmp_path)
    player.mode = 'wave'
    def unavailable():
        raise ConnectionError('mock unavailable')
    player.source.wave = unavailable
    assert player._next_track() is None
    snapshot = player.snapshot()
    assert snapshot['recovery']['retry_in_seconds'] > 0
    assert snapshot['status'] == 'recovering'
    player.stop(); store.close()


def test_empty_decoder_is_not_marked_played(monkeypatch, tmp_path):
    import io
    player, store = player_for(tmp_path)

    class FakeSink:
        def write(self, _data):
            pass
        def close(self):
            pass

    class EmptyDecoder:
        stdout = io.BytesIO()
        def wait(self, timeout=None):
            return 0
        def poll(self):
            return 0

    monkeypatch.setattr('roften_wave.player.make_sink', lambda _config: FakeSink())
    monkeypatch.setattr('roften_wave.player.make_decoder', lambda _path, offset=0: EmptyDecoder())
    qid = player.enqueue(track())
    _, item = store.claim()
    player._play_track({**item, '_queue_id': qid})
    assert player.snapshot()['status'] == 'recovering'
    assert store.history() == []
    assert player.resume_track['_queue_id'] == qid
    player.stop(); store.close()


def test_broadcast_failure_resumes_from_last_pcm_frame(monkeypatch, tmp_path):
    import io
    from roften_wave.stream import BYTES_PER_SECOND
    player, store = player_for(tmp_path)
    pcm_frame = b'\x00' * (BYTES_PER_SECOND // 10)

    class FlakySink:
        def __init__(self):
            self.writes = 0
        def write(self, data):
            self.writes += 1
            if self.writes >= 2:
                raise BrokenPipeError('mock DJ disconnect')
        def close(self):
            pass

    class Decoder:
        def __init__(self):
            self.stdout = io.BytesIO(pcm_frame * 3)
        def poll(self):
            return 0
        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr('roften_wave.player.make_sink', lambda _config: FlakySink())
    monkeypatch.setattr('roften_wave.player.make_decoder', lambda _path, offset=0: Decoder())
    qid = player.enqueue(track())
    _, item = store.claim()
    player._play_track({**item, '_queue_id': qid})
    assert player.resume_track['_queue_id'] == qid
    assert abs(player.resume_track['_resume_at'] - 0.1) < 0.00001
    assert store.history()[0]['id'] == '101'
    player.stop(); store.close()


def test_explicit_next_can_probe_even_while_circuit_is_open(tmp_path):
    player, store = player_for(tmp_path)
    for now in (1, 2, 3, 4):
        player.gate.failed(now)
    # The breaker should block until its cooldown expires.
    assert player.gate.open_until > 4
    player.next()
    assert player.gate.open_until == 0
    assert player.gate.total_failures == 4  # keep diagnostic counter
    player.stop(); store.close()
