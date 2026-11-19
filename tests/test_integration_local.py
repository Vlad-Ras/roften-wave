"""No external service required: fake Yandex file and fake Icecast endpoint."""
from dataclasses import replace
from pathlib import Path
import shutil
import socket
import subprocess
import threading
import time

import pytest

from roften_wave.config import load_config
from roften_wave.db import Store
from roften_wave.player import Player


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='ffmpeg not installed')
def test_player_yandex_to_icecast(tmp_path):
    file = tmp_path/'source.mp3'
    subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-f','lavfi','-i','sine=frequency=800:duration=2',
                    '-ac','2','-c:a','libmp3lame','-b:a','128k','-y',str(file)],check=True)
    listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(1);listener.settimeout(9)
    count={'bytes':0}
    def handle():
        try:
            conn,_=listener.accept();conn.settimeout(9)
            header=b''
            while b'\r\n\r\n' not in header:
                header+=conn.recv(4096)
            head,_,payload=header.partition(b'\r\n\r\n')
            assert b'PUT /live' in head
            count['bytes']+=len(payload)
            conn.sendall(b'HTTP/1.0 200 OK\r\n\r\n')
            while True:
                data=conn.recv(65536)
                if not data:break
                count['bytes']+=len(data)
            conn.close()
        finally:listener.close()
    thread=threading.Thread(target=handle,daemon=True);thread.start()

    class DummySource:
        def download(self, track_id, path):
            shutil.copyfile(file,path)
        def wave(self):return []
        def likes(self):return []

    cfg=replace(load_config(),data_dir=tmp_path,yandex_token='mock',dj_host='127.0.0.1',dj_port=listener.getsockname()[1],
                dj_username='dj',dj_password='p',dj_mount='/live')
    store=Store(tmp_path/'wave.sqlite3')
    player=Player(store,DummySource(),cfg)
    player.enqueue({'id':'17','artist':'Artist','title':'Test','duration':2,'cover':''})
    player.start()
    deadline=time.monotonic()+12
    while time.monotonic() < deadline:
        snapshot=player.snapshot()
        if snapshot['status']=='idle' and snapshot['history']:
            break
        time.sleep(0.15)
    player.stop()
    thread.join(timeout=8)
    assert count['bytes']>12000
    assert store.history()[0]['title']=='Test'
    store.close()
