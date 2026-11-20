"""Read-only broadcast-status checks do not use credentials or affect the DJ connection."""
from dataclasses import replace

import httpx
import pytest

from roften_wave.config import load_config
from roften_wave.station_status import fetch_air_status, format_air_status


def configured(**kwargs):
    return replace(load_config(), azuracast_api_url="https://radio.test",
                   azuracast_station_id="radio", **kwargs)


def test_air_status_parses_real_live_dj_and_listeners(monkeypatch):
    captured = []
    def handle(request: httpx.Request):
        captured.append(request)
        return httpx.Response(200, json={
            "is_online": True,
            "live": {"is_live": True, "streamer_name": "Roften"},
            "now_playing": {"song": {"text": "Artist — Title"}},
            "listeners": {"current": 17, "unique": 12},
        })
    transport = httpx.MockTransport(handle)
    original = httpx.Client
    monkeypatch.setattr("roften_wave.station_status.httpx.Client",
                        lambda **kwargs: original(transport=transport, **kwargs))
    status = fetch_air_status(configured())
    assert status == {"online": True, "live": True, "dj": "Roften",
                      "song": "Artist — Title", "listeners": 17, "unique": 12}
    assert str(captured[0].url) == "https://radio.test/api/nowplaying/radio"
    assert "authorization" not in captured[0].headers
    assert "Roften" in format_air_status(status)
    assert "17" in format_air_status(status)


def test_air_status_autodj_fallback_and_invalid_counts(monkeypatch):
    def handle(request: httpx.Request):
        return httpx.Response(200, json={
            "is_online": True,
            "live": {"is_live": False},
            "now_playing": {"song": {"artist": "Band", "title": "Song"}},
            "listeners": {"current": -4, "unique": "not a number"},
        })
    transport = httpx.MockTransport(handle)
    original = httpx.Client
    monkeypatch.setattr("roften_wave.station_status.httpx.Client",
                        lambda **kwargs: original(transport=transport, **kwargs))
    status = fetch_air_status(configured())
    assert status["song"] == "Band — Song"
    assert status["listeners"] == status["unique"] == 0
    assert "AutoDJ" in format_air_status(status)


@pytest.mark.parametrize("bad_url,bad_id", [
    ("https://user:secret@radio.test", "radio"),
    ("https://radio.test/private", "radio"),
    ("file:///tmp", "radio"),
    ("https://radio.test", "radio/../../oops"),
    ("", "radio"),
])
def test_air_status_rejects_unsafe_api_configurations(bad_url, bad_id):
    cfg = replace(load_config(), azuracast_api_url=bad_url, azuracast_station_id=bad_id)
    with pytest.raises(ValueError):
        fetch_air_status(cfg)


def test_air_status_rejects_malformed_payload(monkeypatch):
    def handle(request: httpx.Request):
        return httpx.Response(200, json={"is_online": True, "live": ["bad"]})
    transport = httpx.MockTransport(handle)
    original = httpx.Client
    monkeypatch.setattr("roften_wave.station_status.httpx.Client",
                        lambda **kwargs: original(transport=transport, **kwargs))
    with pytest.raises(ValueError):
        fetch_air_status(configured())


def test_air_status_excludes_credentials_even_when_api_fails(monkeypatch):
    token = "PRIVATE_DJ_SECRET"
    def handle(request: httpx.Request):
        assert "authorization" not in request.headers
        return httpx.Response(503, text=token)
    transport = httpx.MockTransport(handle)
    original = httpx.Client
    monkeypatch.setattr("roften_wave.station_status.httpx.Client",
                        lambda **kwargs: original(transport=transport, **kwargs))
    with pytest.raises(httpx.HTTPStatusError):
        fetch_air_status(replace(configured(), azuracast_api_key=token, dj_password=token))
