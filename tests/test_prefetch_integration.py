"""Use synthetic tracks and a local fake Icecast, without any account or VDS."""
from dataclasses import replace
import shutil
import socket
import subprocess
import threading
import time
from collections import Counter

import pytest

from roften_wave.config import load_config
from roften_wave.db import Store
from roften_wave.player import Player


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='ffmpeg missing')
def test_next_audio_prefetched_during_first_track(tmp_path):
    sine = tmp_path/'sine.mp3'
    subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-f','lavfi','-i','sine=frequency=440:duration=1.7',
                    '-ac','2','-c:a','libmp3lame','-b:a','96k','-y',str(sine)], check=True)
    listen = socket.socket()
    listen.bind(('127.0.0.1',0));listen.listen(1);listen.settimeout(8)
    done = threading.Event()
    data = {'bytes':0}
    def server():
        try:
            conn, _ = listen.accept()
            conn.settimeout(10)
            received = b''
            while b'\r\n\r\n' not in received:
                received += conn.recv(4096)
            _, _, body = received.partition(b'\r\n\r\n')
            data['bytes'] += len(body)
            conn.sendall(b'HTTP/1.0 200 OK\r\n\r\n')
            while True:
                chunk = conn.recv(65536)
                if not chunk: break
                data['bytes'] += len(chunk)
            conn.close()
        finally:
            listen.close();done.set()
    threading.Thread(target=server, daemon=True).start()
    calls = Counter()
    class Source:
        def download(self, track_id, target):
            calls[track_id] += 1
            shutil.copyfile(sine, target)
        def wave(self): return []
        def likes(self): return []
    c = replace(load_config(), data_dir=tmp_path, yandex_token='mock',dj_host='127.0.0.1',
                dj_port=listen.getsockname()[1],dj_mount='/live',dj_username='dj',dj_password='p',
                azuracast_api_url='',azuracast_api_key='',azuracast_station_id='')
    store = Store(tmp_path/'store.sqlite3')
    for num in ('1', '2'):
        store.add({'id':num,'title':num,'artist':'Synthetic','cover':'','duration':2})
    player = Player(store, Source(), c)
    player.start()
    until=time.monotonic()+9
    while time.monotonic()<until:
        snap=player.snapshot()
        if len(snap['history'])>=2 and snap['current'] is None: break
        time.sleep(0.08)
    result=player.snapshot()
    player.stop()
    assert done.wait(6)
    assert len(result['history'])>=2
    assert result['prefetch']['hits']>=1
    assert calls == Counter({'1':1,'2':1})
    assert data['bytes'] > 20000
    assert not list(tmp_path.glob('wave-*.mp3'))
    store.close()
