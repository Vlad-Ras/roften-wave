from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from roften_wave.prefetch import OneTrackPrefetch, clean_orphan_audio, cleanup_job


def wait_until(predicate, timeout=3):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if predicate():
            return
        time.sleep(0.015)
    raise AssertionError('timed out')


def test_one_next_track_is_downloaded_once_and_reused(tmp_path):
    calls = []

    def download(track_id, path):
        calls.append(track_id)
        path.write_bytes(track_id.encode())

    staging = OneTrackPrefetch(tmp_path, download)
    staging.stage('123')
    wait_until(lambda: staging.stats()['ready'])
    staging.stage('123')  # Duplicate peek must not fetch again.
    job = staging.acquire('123')
    assert job.path.read_bytes() == b'123'
    assert staging.stats()['hits'] == 1
    assert calls == ['123']
    cleanup_job(job)
    assert not job.path.exists()
    staging.close()


def test_cancel_inflight_staging_cleans_late_download(tmp_path):
    started = threading.Event()
    finish = threading.Event()

    def download(track_id, path):
        if track_id == '1':
            started.set()
            assert finish.wait(3)
        path.write_bytes(track_id.encode())

    staging = OneTrackPrefetch(tmp_path, download)
    staging.stage('1')
    assert started.wait(2)
    old = staging.next_job
    staging.stage('2')
    wait_until(lambda: staging.stats()['ready'])
    assert staging.stats()['track_id'] == '2'
    finish.set()
    wait_until(lambda: not old.path.exists())
    assert len(list(tmp_path.glob('wave-*.mp3'))) == 1
    staging.close()
    wait_until(lambda: not list(tmp_path.glob('wave-*.mp3')))


def test_stats_on_cancelled_job_do_not_raise(tmp_path):
    block = threading.Event()
    busy = threading.Event()
    def download(_id, path):
        busy.set()
        block.wait(2)
        path.write_bytes(b'done')
    staging = OneTrackPrefetch(tmp_path, download)
    staging.stage('1')
    assert busy.wait(2)
    staging.stage('2')
    staging.close()
    block.set()
    wait_until(lambda: not list(tmp_path.glob('wave-*.mp3')))
    assert staging.stats()['ready'] is False


def test_cleans_old_files_without_deleting_recent_or_unrelated(tmp_path):
    old = tmp_path / 'wave-old.mp3'
    recent = tmp_path / 'wave-recent.mp3'
    unrelated = tmp_path / 'user-upload.mp3'
    for path in (old, recent, unrelated):
        path.write_bytes(b'file')
    os.utime(old, (time.time()-4000, time.time()-4000))
    assert clean_orphan_audio(tmp_path, max_age_seconds=1800) == 1
    assert not old.exists()
    assert recent.exists() and unrelated.exists()
