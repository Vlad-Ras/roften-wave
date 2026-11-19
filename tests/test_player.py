from dataclasses import replace
from roften_wave.config import load_config
from roften_wave.db import Store
from roften_wave.yandex import YandexSource
from roften_wave.player import Player


def test_controls_without_worker(tmp_path):
    s=Store(tmp_path/'a.sqlite3')
    player=Player(s,YandexSource(''),replace(load_config(),data_dir=tmp_path))
    t={'id':'123','title':'T','artist':'A','cover':'','duration':180}
    qid=player.enqueue(t)
    assert player.snapshot()['queue'][0]['queue_id']==qid
    player.pause();assert player.snapshot()['paused']
    player.resume();assert not player.snapshot()['paused']
    player.set_mode('likes')
    assert s.get('mode')=='likes'
    player.next()
    assert player.interrupt.is_set()
    player.stop()
    s.close()
