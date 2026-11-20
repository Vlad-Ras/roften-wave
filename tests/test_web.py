from dataclasses import replace
from fastapi.testclient import TestClient
import pytest

from roften_wave import web
from roften_wave.config import Config
from roften_wave.db import Store
from roften_wave.player import Player
from roften_wave.yandex import YandexSource


@pytest.fixture()
def client(monkeypatch,tmp_path):
    cfg=replace(web.config,
        admin_username="testadmin",admin_password="correct-horse-battery-staple",
        session_secret="very-strong-key-with-at-least-32-characters",secure_cookies=False,
        data_dir=tmp_path, yandex_token="dummy", dj_host="", dj_port=0, dj_username="", dj_password="", auto_start=False)
    store=Store(tmp_path / "test.sqlite3")
    source=YandexSource("dummy")
    player=Player(store,source,cfg)
    monkeypatch.setattr(web,"config",cfg)
    monkeypatch.setattr(web,"store",store)
    monkeypatch.setattr(web,"source",source)
    monkeypatch.setattr(web,"player",player)
    with TestClient(web.app) as c:
        yield c,store,source,player


def login(client):
    c,*_=client
    r=c.post('/api/login',json={"username":"testadmin","password":"correct-horse-battery-staple"})
    assert r.status_code==200,r.text
    return {"X-CSRF-Token":r.json()["csrf"]}


def test_login_csrf_queue_and_settings(client,monkeypatch):
    c,store,source,player=client
    assert c.get('/api/state').status_code==401
    assert c.post('/api/login',json={"username":"testadmin","password":"wrong"}).status_code==401
    headers=login(client)
    assert c.get('/api/me').status_code==200
    assert c.post('/api/next').status_code==403
    assert c.post('/api/mode',json={"mode":"wave"},headers=headers).status_code==200
    assert store.get('mode')=='wave'
    assert c.post('/api/mode',json={"mode":"invalid"},headers=headers).status_code==422
    monkeypatch.setattr(source,'get_track',lambda id: {"id":id,"title":"Example","artist":"Artist","duration":42,"cover":""})
    r=c.post('/api/queue',json={"track_id":"12345","now":False},headers=headers)
    assert r.status_code==200,r.text
    qid=r.json()['queue_id']
    assert len(c.get('/api/state').json()['queue'])==1
    assert c.delete(f'/api/queue/{qid}',headers=headers).status_code==200
    assert c.get('/api/state').json()['queue']==[]
    assert c.put('/api/settings',json={"app_url":"javascript:alert(1)","radio_url":"https://example.com"},headers=headers).status_code==422
    r=c.put('/api/settings',json={"app_url":"https://wave.example.org/","radio_url":"https://radio.example.org/listen"},headers=headers)
    assert r.status_code==200
    assert c.get('/api/settings').json()['app_url']=='https://wave.example.org'
    assert c.post('/api/logout',headers=headers).status_code==200
    assert c.get('/api/state').status_code==401


def test_index_and_health_public(client):
    c,*_=client
    assert c.get('/health').json()=={"ok":True}
    assert 'Roften Wave' in c.get('/').text
    assert c.get('/app.css').status_code==200
    assert c.get('/app.js').status_code==200


def test_diagnostics_requires_auth_and_redacts_secrets(client, monkeypatch):
    c, store, source, player = client
    assert c.get('/api/diagnostics').status_code == 401
    login(client)
    monkeypatch.setattr(source, 'check_connection', lambda: None)
    result = c.get('/api/diagnostics')
    assert result.status_code == 200
    checks = result.json()['checks']
    assert checks['storage']['ok'] is True
    assert checks['yandex_auth']['ok'] is True
    assert 'dummy' not in result.text


def test_queue_shuffle_dedupe_and_statistics_permissions(client, monkeypatch):
    c, store, source, player = client
    assert c.post("/api/queue/shuffle").status_code == 401
    assert c.post("/api/queue/dedupe").status_code == 401
    assert c.get("/api/statistics").status_code == 401
    headers = login(client)
    assert c.post("/api/queue/shuffle").status_code == 403
    for track_id in (1, 2, 1, 3):
        store.add({"id": str(track_id), "title": "Song", "artist": "Test"})
    r = c.post("/api/queue/dedupe", headers=headers)
    assert r.status_code == 200
    assert r.json()["removed"] == 1
    r = c.post("/api/queue/shuffle", headers=headers)
    assert r.status_code == 200
    assert r.json()["shuffled"] == 3
    store.record_audio_result(seconds=61, finished=True)
    assert c.get("/api/statistics").json()["audio_seconds"] == 61
    assert c.get("/api/state").json()["statistics"]["finished_tracks"] == 1
