import dataclasses
import shutil
import socket
import threading
import time

import pytest

from roften_wave.config import load_config
from roften_wave.stream import make_sink, silence


def test_ffmpeg_rejects_empty_mount():
    cfg = dataclasses.replace(load_config(), dj_host='127.0.0.1', dj_port=8005,
                              dj_username='dj', dj_password='password', dj_mount='/')
    with pytest.raises(ValueError, match='named DJ mount'):
        make_sink(cfg)


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='ffmpeg not installed')
def test_ffmpeg_publishes_mp3_to_fake_icecast():
    server=socket.socket()
    server.bind(('127.0.0.1',0));server.listen(1)
    server.settimeout(10)
    port=server.getsockname()[1]
    output={}
    def receive():
        try:
            c,_=server.accept();c.settimeout(10)
            data=b''
            while b'\r\n\r\n' not in data:
                data+=c.recv(8192)
            header,_,payload=data.partition(b'\r\n\r\n')
            output['request']=header.decode('latin1').split('\r\n',1)[0]
            c.sendall(b'HTTP/1.0 200 OK\r\n\r\n')
            size=len(payload)
            while True:
                b=c.recv(65536)
                if not b:break
                size+=len(b)
            output['audio_bytes']=size
            c.close()
        finally:server.close()
    reader=threading.Thread(target=receive,daemon=True);reader.start()
    cfg=dataclasses.replace(load_config(),dj_host='127.0.0.1',dj_port=port,dj_username='dj',dj_password='abc',dj_mount='/live')
    sink=make_sink(cfg)
    try:
        for _ in range(16):
            sink.write(silence(0.25))
            time.sleep(0.25)
    finally:sink.close()
    reader.join(timeout=10)
    assert output['request'].startswith('PUT /live HTTP/')
    assert output['audio_bytes'] > 10000
