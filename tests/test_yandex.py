from types import SimpleNamespace
import pytest
from roften_wave.yandex import as_track, YandexSource


def test_track_normalization():
    t=SimpleNamespace(id=123,title='<script>bad</script>',artists=[SimpleNamespace(name='Artist')],duration_ms=183000,cover_uri='avatars.yandex.net/test/%%')
    info=as_track(t)
    assert info['duration']==183
    assert info['id']=='123'
    assert info['cover']=='https://avatars.yandex.net/test/200x200'
    assert '<script>' in info['title']  # UI must render this using textContent, not HTML


def test_reject_invalid_ids():
    s=YandexSource('dummy')
    with pytest.raises(ValueError):s.get_track('../../../etc/passwd')
    with pytest.raises(ValueError):s.get_track('abc')
