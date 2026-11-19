"""One-item predictive audio staging with bounded disk use and safe cancellation.

The caller owns the returned job and removes its file after playback. A staging
job never deletes/claims a queue record; it only downloads temporary audio.
"""
from __future__ import annotations

import concurrent.futures
import logging
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)


@dataclass
class DownloadJob:
    track_id: str
    path: Path
    future: concurrent.futures.Future[None]


def cleanup_job(job: DownloadJob) -> None:
    """Cancel pending work and unlink *after* an in-flight writer finishes."""
    if job.future.done():
        job.path.unlink(missing_ok=True)
    else:
        job.future.cancel()
        job.future.add_done_callback(lambda _f: job.path.unlink(missing_ok=True))


class OneTrackPrefetch:
    def __init__(self, data_dir: Path, download: Callable[[str, Path], None]):
        self.data_dir = data_dir
        self.download = download
        self.lock = threading.RLock()
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix='wave-download')
        self.next_job: DownloadJob | None = None
        self.closed = False
        self.hits = 0
        self.misses = 0

    def _make_job(self, track_id: str) -> DownloadJob:
        with tempfile.NamedTemporaryFile(prefix='wave-', suffix='.mp3', dir=self.data_dir, delete=False) as fd:
            path = Path(fd.name)
        try:
            future = self.pool.submit(self.download, track_id, path)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return DownloadJob(track_id, path, future)

    def stage(self, track_id: str | None) -> None:
        """Only the latest predicted track is staged; stale jobs are cleaned."""
        with self.lock:
            if self.closed:
                return
            if self.next_job and track_id == self.next_job.track_id:
                return
            old, self.next_job = self.next_job, None
            if old:
                cleanup_job(old)
            if track_id:
                self.next_job = self._make_job(track_id)

    def acquire(self, track_id: str) -> DownloadJob:
        with self.lock:
            if self.closed:
                raise RuntimeError('Audio staging is closed')
            if self.next_job and self.next_job.track_id == track_id:
                job, self.next_job = self.next_job, None
                self.hits += 1
                return job
            stale, self.next_job = self.next_job, None
            if stale:
                cleanup_job(stale)
            self.misses += 1
            return self._make_job(track_id)

    def stats(self) -> dict[str, object]:
        with self.lock:
            job = self.next_job
            ready = False
            if job and job.future.done() and not job.future.cancelled():
                ready = job.future.exception() is None and job.path.exists() and job.path.stat().st_size > 0
            return {'track_id': job.track_id if job else None, 'ready': ready,
                    'hits': self.hits, 'misses': self.misses}

    def close(self) -> None:
        with self.lock:
            self.closed = True
            old, self.next_job = self.next_job, None
            if old:
                cleanup_job(old)
        # Already running SDK downloads cannot be force-cancelled. The callback
        # still deletes their files once they finish.
        self.pool.shutdown(wait=False, cancel_futures=True)


def clean_orphan_audio(directory: Path, *, max_age_seconds: int = 1800) -> int:
    """Delete stale files from previous crashes; never follow symlinks."""
    now = time.time()
    removed = 0
    for path in directory.glob('wave-*.mp3'):
        try:
            if not path.is_symlink() and path.is_file() and now - path.stat().st_mtime > max_age_seconds:
                path.unlink()
                removed += 1
        except OSError as exc:
            log.warning('Cannot clean old staged audio (%s)', type(exc).__name__)
    return removed
