from __future__ import annotations

import time
from dataclasses import replace

import httpx
import pytest

from roften_wave.config import load_config
from roften_wave.db import Store
from roften_wave.metadata import MetadataPublisher
from roften_wave.player import Player
from roften_wave.yandex import YandexSource


def config(**kwargs):
    return replace(load_config(), azuracast_api_url='http://127.0.0.1:10000',
                   azuracast_api_key='super-private-token', azuracast_station_id='radio', **kwargs)


def test_metadata_request_path_header_and_payload(monkeypatch):
    seen = []
    def handle(request: httpx.Request):
        seen.append(request)
        return httpx.Response(200, json={'ok': True})
    transport = httpx.MockTransport(handle)
    original = httpx.Client
    monkeypatch.setattr('roften_wave.metadata.httpx.Client', lambda **kw: original(transport=transport, **kw))
    publisher = MetadataPublisher(config())
    publisher.publish({'artist': 'My Artist', 'title': 'My Title'})
    assert len(seen) == 1
    assert str(seen[0].url) == 'http://127.0.0.1:10000/api/station/radio/nowplaying/update'
    assert seen[0].headers['authorization'] == 'Bearer super-private-token'
    assert seen[0].content == b'{"artist":"My Artist","title":"My Title"}'


def test_metadata_rejects_urls_with_credentials_and_paths():
    for url in ('https://user:pass@radio.example', 'https://radio.example/private', 'file:///etc/passwd'):
        c = replace(config(), azuracast_api_url=url)
        with pytest.raises(ValueError):
            MetadataPublisher(c).endpoint()


def test_metadata_failure_does_not_stop_player(tmp_path):
    store = Store(tmp_path/'store.sqlite3')
    p = Player(store, YandexSource(''), config(data_dir=tmp_path))
    def fail(_track):
        raise httpx.HTTPStatusError('unavailable', request=httpx.Request('POST','https://test.local'),
                                     response=httpx.Response(401, request=httpx.Request('POST','https://test.local')))
    p.metadata.publish = fail
    p._submit_metadata({'title':'Example','artist':'Example'})
    deadline = time.monotonic() + 2
    while p.snapshot()['metadata']['status'] == 'pending' and time.monotonic() < deadline:
        time.sleep(0.01)
    assert p.snapshot()['metadata'] == {'status':'error', 'error':'HTTPStatusError'}
    assert not p.shutdown.is_set()
    p.stop(); store.close()
