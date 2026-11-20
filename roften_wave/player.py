from __future__ import annotations

import concurrent.futures
import logging
import threading
import time
from collections import deque
from typing import Any

from .config import Config
from .db import Store
from .stream import BYTES_PER_SECOND, Sink, StreamError, make_decoder, make_sink, silence
from .prefetch import OneTrackPrefetch, cleanup_job, clean_orphan_audio
from .metadata import MetadataPublisher
from .resilience import RecoveryGate, MAX_TRACK_ATTEMPTS, RETRY_DELAYS_SECONDS
from .yandex import YandexSource

log = logging.getLogger(__name__)


class Player:
    def __init__(self, store: Store, source: YandexSource, config: Config):
        self.store, self.source, self.config = store, source, config
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.interrupt = threading.Event()
        self.shutdown = threading.Event()
        self.paused = False
        self.current: dict[str, Any] | None = None
        self.resume_track: dict[str, Any] | None = None
        self.elapsed = 0.0
        self.started = 0.0
        self.error = ""
        self.status = "idle"
        self.autos: deque[dict[str, Any]] = deque()
        self.auto_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="wave-autofill")
        self.auto_pending: concurrent.futures.Future | None = None
        self.auto_generation = 0
        self.last_track_end_at: float | None = None
        self.handoff_gaps_ms: deque[int] = deque(maxlen=20)
        self.sink: Sink | None = None
        self.worker: threading.Thread | None = None
        self.staging = OneTrackPrefetch(config.data_dir, source.download)
        self.metadata = MetadataPublisher(config)
        self.metadata_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix='wave-metadata')
        self.metadata_generation = 0
        self.metadata_state = 'disabled' if not self.metadata.enabled else 'idle'
        self.metadata_error = ''
        self.gate = RecoveryGate()
        self.retry_at = 0.0
        self.retry_attempt = 0
        self.mode = store.get("mode", "manual")
        if self.mode not in {"manual", "wave", "likes"}:
            self.mode = "manual"

    def start(self) -> None:
        if self.worker is None:
            self.config.data_dir.mkdir(parents=True, exist_ok=True)
            clean_orphan_audio(self.config.data_dir)
            self.worker = threading.Thread(target=self._run, name="roften-wave-playback", daemon=True)
            self.worker.start()

    def stop(self) -> None:
        self.shutdown.set()
        self.interrupt.set()
        self.wake.set()
        if self.worker:
            self.worker.join(timeout=5)
        self._close_sink()
        self.staging.close()
        self.auto_pool.shutdown(wait=False, cancel_futures=True)
        self.metadata_pool.shutdown(wait=False, cancel_futures=True)

    def _discard_resume_track(self) -> None:
        """Skip an already paused item without leaving a hidden DB claim."""
        if self.resume_track:
            queue_id = self.resume_track.get("_queue_id")
            if queue_id is not None:
                self.store.ack(queue_id)
            self.resume_track = None
        self.retry_at = 0.0
        self.retry_attempt = 0

    def set_mode(self, mode: str) -> None:
        if mode not in {"manual", "wave", "likes"}:
            raise ValueError("mode must be manual, wave or likes")
        with self.lock:
            self.mode = mode
            self.gate.succeeded()  # An explicit operator action can probe now.
            self.retry_at = 0.0
            self.metadata_generation += 1
            self.autos.clear()
            self.auto_generation += 1
            self.auto_pending = None
            self.last_track_end_at = None
            self._discard_resume_track()
            self.paused = False
            self.store.set("mode", mode)
            self.interrupt.set()
            self.wake.set()

    def next(self) -> None:
        with self.lock:
            self.paused = False
            self.metadata_generation += 1
            self._discard_resume_track()
            self.gate.succeeded()
            self.interrupt.set()
            self.wake.set()

    def previous(self) -> bool:
        history = self.store.history()
        if not history:
            return False
        with self.lock:
            current_id = str((self.current or {}).get("id", ""))
        # If current is already in history, choose its predecessor; otherwise replay latest.
        index = 1 if current_id and str(history[0].get("id")) == current_id else 0
        if len(history) <= index:
            return False
        self.store.add(history[index], first=True)
        self.next()
        return True

    def pause(self) -> None:
        with self.lock:
            self.paused = True
            self.retry_at = 0.0
            self.last_track_end_at = None
            self.metadata_generation += 1
            self.interrupt.set()
            self.wake.set()

    def resume(self) -> None:
        with self.lock:
            self.paused = False
            self.retry_at = 0.0
            self.gate.succeeded()  # Resume explicitly probes a previously blocked source.
            self.wake.set()

    def enqueue(self, track: dict[str, Any], *, now: bool = False) -> int:
        queue_id = self.store.add(track, first=now)
        with self.lock:
            self.paused = False
            if now:
                self._discard_resume_track()
                self.gate.succeeded()
                self.interrupt.set()
            self.wake.set()
        self.queue_updated()
        return queue_id

    def queue_updated(self) -> None:
        """Called by both API and bot after a queue mutation."""
        with self.lock:
            playing = self.status == 'playing'
        if playing:
            self._stage_next()
        self.wake.set()

    def _schedule_auto_refill(self) -> None:
        """Fetch the next Wave/likes batch before the current last song ends."""
        with self.lock:
            if self.shutdown.is_set() or self.mode == "manual" or self.autos:
                return
            if self.auto_pending is not None and not self.auto_pending.done():
                return
            mode = self.mode
            generation = self.auto_generation
            try:
                task = self.auto_pool.submit(self.source.wave if mode == "wave" else self.source.likes)
            except RuntimeError:
                return
            self.auto_pending = task

        def completed(future: concurrent.futures.Future) -> None:
            try:
                batch = future.result()
            except concurrent.futures.CancelledError:
                return
            except Exception as exc:
                log.warning("Auto playlist prefetch failed: %s", type(exc).__name__)
                return
            with self.lock:
                if self.shutdown.is_set() or self.auto_generation != generation or self.mode != mode:
                    return
                self.autos.extend(batch)
            if batch:
                self._stage_next()
            self.wake.set()

        task.add_done_callback(completed)

    def _stage_next(self) -> None:
        next_item = self.store.peek()
        if next_item is None:
            with self.lock:
                if self.mode != 'manual' and self.autos:
                    next_item = self.autos[0]
            if next_item is None:
                with self.lock:
                    auto_mode = self.mode != "manual"
                if auto_mode:
                    # The future's callback will stage the next item when
                    # ready. Do not race it by clearing the staging slot here.
                    self._schedule_auto_refill()
                    return
        self.staging.stage(str(next_item['id']) if next_item and next_item.get('id') else None)

    def _submit_metadata(self, track: dict[str, Any]) -> None:
        if not self.metadata.enabled or self.shutdown.is_set():
            return
        with self.lock:
            self.metadata_state = 'pending'
            self.metadata_error = ''
            generation = self.metadata_generation

        def task() -> None:
            with self.lock:
                if generation != self.metadata_generation or self.shutdown.is_set():
                    return
            try:
                self.metadata.publish(track)
            except Exception as exc:
                log.warning('AzuraCast metadata failed: %s', type(exc).__name__)
                with self.lock:
                    if generation == self.metadata_generation:
                        self.metadata_state = 'error'
                        self.metadata_error = type(exc).__name__
            else:
                with self.lock:
                    if generation == self.metadata_generation:
                        self.metadata_state = 'synced'

        try:
            self.metadata_pool.submit(task)
        except RuntimeError:
            # A shutdown racing with an audio frame must never stop audio.
            pass

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "current": self.current,
                "elapsed": round(self.elapsed, 1),
                "paused": self.paused,
                "status": self.status,
                "mode": self.mode,
                "error": self.error,
                "queue": self.store.queue(),
                "history": self.store.history()[:10],
                "statistics": self.store.stats(),
                "handoff": {"last_ms": self.handoff_gaps_ms[-1] if self.handoff_gaps_ms else None,
                            "average_ms": round(sum(self.handoff_gaps_ms) / len(self.handoff_gaps_ms))
                            if self.handoff_gaps_ms else None,
                            "samples": len(self.handoff_gaps_ms)},
                "prefetch": self.staging.stats(),
                "metadata": {"status": self.metadata_state, "error": self.metadata_error},
                "recovery": {
                    "retry_in_seconds": round(max(0.0, self.retry_at - time.monotonic(),
                                                  self.gate.seconds_until_probe(time.monotonic())), 1),
                    "attempt": self.retry_attempt,
                    "max_attempts": MAX_TRACK_ATTEMPTS,
                    "circuit_open": self.gate.seconds_until_probe(time.monotonic()) > 0,
                    "circuit_trips": self.gate.trips,
                    "total_failures": self.gate.total_failures,
                },
                "configured": {"yandex": self.config.yandex_configured, "azuracast": self.config.radio_configured},
            }

    def _close_sink(self) -> None:
        with self.lock:
            sink, self.sink = self.sink, None
        if sink:
            sink.close()

    def _next_track(self) -> dict[str, Any] | None:
        with self.lock:
            if self.resume_track:
                track, self.resume_track = self.resume_track, None
                return track
        claimed = self.store.claim()
        if claimed:
            queue_id, manual = claimed
            return {**manual, "_queue_id": queue_id}
        if self.mode == "manual":
            return None
        if not self.autos:
            with self.lock:
                pending = self.auto_pending
            if pending is not None and not pending.done():
                try:
                    pending.result(timeout=8)
                except Exception:
                    # Fall back to normal retrieval; the bounded recovery gate
                    # still controls repeated Yandex failures.
                    pass
            with self.lock:
                ready = bool(self.autos)
            if ready:
                with self.lock:
                    return self.autos.popleft()
            try:
                batch = self.source.wave() if self.mode == "wave" else self.source.likes()
                self.autos.extend(batch)
            except Exception as exc:
                log.warning("Cannot load automatic tracks: %s", type(exc).__name__)
                with self.lock:
                    now = time.monotonic()
                    self.gate.failed(now)
                    self.retry_at = now + RETRY_DELAYS_SECONDS[0]
                    self.error = f"Auto playlist error: {type(exc).__name__}"
                    self.status = "recovering"
                return None
            if not self.autos:
                with self.lock:
                    # Empty automatic playlists are not necessarily errors.
                    self.retry_at = time.monotonic() + 30.0
        return self.autos.popleft() if self.autos else None

    def _run(self) -> None:
        while not self.shutdown.is_set():
            with self.lock:
                is_paused = self.paused
            if is_paused or not self.config.radio_configured or not self.config.yandex_configured:
                self._close_sink()  # Release Live DJ -> AzuraCast AutoDJ resumes.
                with self.lock:
                    self.status = "paused" if is_paused else "unconfigured"
                self.wake.wait(timeout=1)
                self.wake.clear()
                continue
            with self.lock:
                now = time.monotonic()
                wait_seconds = max(0.0, self.retry_at - now, self.gate.seconds_until_probe(now))
                if wait_seconds > 0:
                    self.status = "recovering" if self.error else "idle"
            if wait_seconds > 0:
                self._close_sink()  # Do not transmit silence during recovery.
                self.wake.wait(timeout=min(wait_seconds, 1.0))
                self.wake.clear()
                continue
            track = self._next_track()
            if track is None:
                self._close_sink()
                with self.lock:
                    self.last_track_end_at = None
                    self.current = None
                    self.status = "recovering" if self.error else "idle"
                self.wake.wait(timeout=2)
                self.wake.clear()
                continue
            self.interrupt.clear()
            self._play_track(track)

    def _play_track(self, track: dict[str, Any]) -> None:
        track = dict(track)  # Never mutate the stored metadata or cached automatic track.
        offset = float(track.pop("_resume_at", 0))
        retry_attempt = int(track.pop("_retry_attempt", 0))
        queue_id = track.pop("_queue_id", None)
        preserve_claim = False
        playback_failed = False
        finished_track = False
        job = None
        future: concurrent.futures.Future | None = None
        with self.lock:
            self.current = track
            self.elapsed = offset
            self.error = ""
            self.status = "loading"
            self.retry_attempt = retry_attempt + 1
            self.metadata_generation += 1
        decoder = None
        try:
            job = self.staging.acquire(str(track['id']))
            future = job.future
            tmp = job.path
            deadline = time.monotonic() + 35
            while not future.done():
                if self.shutdown.is_set() or self.interrupt.is_set():
                    future.cancel()
                    with self.lock:
                        if self.paused and not self.shutdown.is_set():
                            preserve_claim = True
                            self.resume_track = {**track, "_resume_at": offset, **({"_queue_id": queue_id} if queue_id is not None else {})}
                    return
                with self.lock:
                    sink = self.sink
                if sink and time.monotonic() < deadline:
                    sink.write(silence(0.25))
                    time.sleep(0.25)
                else:
                    if sink:
                        self._close_sink()
                    time.sleep(0.1)
                if time.monotonic() > deadline:
                    raise TimeoutError("Yandex track download exceeded 35 seconds")
            future.result()
            if self.shutdown.is_set() or self.interrupt.is_set():
                with self.lock:
                    if self.paused and not self.shutdown.is_set():
                        preserve_claim = True
                        self.resume_track = {**track, "_resume_at": offset, **({"_queue_id": queue_id} if queue_id is not None else {})}
                return
            with self.lock:
                if self.sink is None:
                    self.sink = make_sink(self.config)
                sink = self.sink
                self.status = "playing"
            decoder = make_decoder(tmp, offset=offset)
            first_frame = True
            while not self.shutdown.is_set() and not self.interrupt.is_set():
                assert decoder.stdout is not None
                pcm = decoder.stdout.read(17640)  # approx 100ms of PCM
                if not pcm:
                    break
                sink.write(pcm)
                if first_frame:
                    first_frame = False
                    with self.lock:
                        if self.last_track_end_at is not None:
                            gap_ms = round((time.monotonic() - self.last_track_end_at) * 1000)
                            if 0 <= gap_ms <= 30000:
                                self.handoff_gaps_ms.append(gap_ms)
                        self.last_track_end_at = None
                    if offset < 0.01:
                        self.store.record(track)
                    self._submit_metadata(track)
                    self._stage_next()
                with self.lock:
                    self.elapsed += len(pcm) / BYTES_PER_SECOND
            if not self.interrupt.is_set() and not self.shutdown.is_set():
                # An empty or corrupt decoder output is a failure, not a played song.
                if first_frame or decoder.wait(timeout=3) != 0:
                    raise RuntimeError("Audio decoder produced no complete track")
                finished_track = True
                with self.lock:
                    self.last_track_end_at = time.monotonic()
                    self.gate.succeeded()
                    self.retry_at = 0.0
            with self.lock:
                if self.paused and not self.shutdown.is_set():
                    preserve_claim = True
                    self.resume_track = {**track, "_resume_at": self.elapsed, **({"_queue_id": queue_id} if queue_id is not None else {})}
                self.status = "paused" if self.paused else "idle"
        except Exception as exc:
            playback_failed = True
            if isinstance(exc, StreamError):
                # StreamError only contains fixed labels, never raw FFmpeg output.
                reason = str(exc)
                log.error("Track playback error: %s", reason)
            elif isinstance(exc, (BrokenPipeError, OSError, RuntimeError, ValueError, TimeoutError)):
                reason = type(exc).__name__
                log.error("Track playback error: %s", reason)
            else:
                reason = type(exc).__name__
                log.exception("Uncaught playback error")
            with self.lock:
                self.error = f"Playback failed: {reason}"
                self.status = "error"
                if not self.shutdown.is_set() and not self.interrupt.is_set():
                    now = time.monotonic()
                    self.gate.failed(now)
                    if retry_attempt + 1 < MAX_TRACK_ATTEMPTS:
                        # Keep manual DB claim, or retain auto item, through bounded retries.
                        preserve_claim = True
                        self.retry_at = now + RETRY_DELAYS_SECONDS[retry_attempt]
                        self.resume_track = {
                            **track, "_retry_attempt": retry_attempt + 1,
                            "_resume_at": self.elapsed,
                            **({"_queue_id": queue_id} if queue_id is not None else {}),
                        }
                        self.status = "recovering"
                    elif queue_id is not None:
                        # Manual item remains available for explicit retry/removal.
                        self.paused = True
                        self.status = "error"
                    else:
                        # Exhausted automatic track is skipped; the circuit guards
                        # repeated source failures over successive auto tracks.
                        self.retry_at = now + 1.0
                        self.status = "recovering"
            self._close_sink()
        finally:
            # Count only actual decoded PCM delivered to the broadcaster.
            # This is one SQLite write per attempt, not an update per audio frame.
            delivered = max(0.0, self.elapsed - offset)
            if delivered > 0 or playback_failed or finished_track:
                try:
                    self.store.record_audio_result(
                        seconds=delivered,
                        finished=finished_track,
                        failed=playback_failed,
                        interrupted=not playback_failed and not finished_track,
                    )
                except Exception:
                    log.exception("Could not persist audio output statistics")
            if decoder:
                if decoder.poll() is None:
                    decoder.terminate()
                    try:
                        decoder.wait(timeout=2)
                    except Exception:
                        decoder.kill()
                if decoder.stdout:
                    decoder.stdout.close()
            if job:
                cleanup_job(job)
            if queue_id is not None and not self.shutdown.is_set() and not preserve_claim:
                if playback_failed:
                    # Keep failed requests visible for inspection/removal, but
                    # avoid retrying the same unplayable track forever.
                    self.store.release(queue_id)
                    with self.lock:
                        self.paused = True
                        self.status = "error"
                else:
                    self.store.ack(queue_id)
