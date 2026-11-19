from dataclasses import replace
import socket
import threading

from roften_wave.config import load_config
from roften_wave.diagnostics import check_local, check_yandex


def test_diagnostics_do_not_broadcast(tmp_path):
    server = socket.socket()
    server.bind(('127.0.0.1', 0))
    server.listen(1)
    received = []

    def check_connection():
        server.settimeout(4)
        try:
            connection, _ = server.accept()
            connection.settimeout(0.3)
            try:
                received.append(connection.recv(16))
            except socket.timeout:
                received.append(b'')
            connection.close()
        finally:
            server.close()

    thread = threading.Thread(target=check_connection, daemon=True)
    thread.start()
    cfg = replace(load_config(), data_dir=tmp_path, dj_host='127.0.0.1', dj_port=server.getsockname()[1],
                  dj_mount='/live', dj_username='dj', dj_password='secret', yandex_token='fake')
    checks = check_local(cfg)
    thread.join(timeout=5)
    assert checks['dj_port']['ok'] is True
    assert checks['storage']['ok'] is True
    assert received == [b'']  # no DJ source request, just a TCP handshake


def test_failed_yandex_check_hides_token():
    secret='SECRET_OAUTH_TOKEN'
    class BadSource:
        def check_connection(self):
            raise RuntimeError(secret)
    cfg=replace(load_config(),yandex_token=secret)
    result=check_yandex(BadSource(),cfg)
    assert result['ok'] is False
    assert secret not in str(result)
