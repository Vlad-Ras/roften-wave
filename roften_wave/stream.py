from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO
from urllib.parse import quote

from .config import Config

SAMPLE_RATE = 44100
CHANNELS = 2
BYTES_PER_SECOND = SAMPLE_RATE * CHANNELS * 2


class StreamError(RuntimeError):
    """A fixed diagnostic label, never raw FFmpeg output or credentials."""


def classify_stream_error(data: bytes) -> str:
    """Classify FFmpeg's private stderr without exposing it to application logs."""
    message = data.lower()
    if b"401" in message or b"unauthorized" in message:
        return "DJ authentication rejected (401)"
    if b"403" in message or b"forbidden" in message:
        return "DJ publishing forbidden (403)"
    if b"404" in message or b"not found" in message:
        return "DJ mount not found (404)"
    if b"connection refused" in message:
        return "DJ TCP connection refused"
    if b"timed out" in message or b"timeout" in message:
        return "DJ connection timed out"
    if b"broken pipe" in message:
        return "DJ stream connection dropped"
    return "FFmpeg/Icecast stream stopped"


@dataclass
class Sink:
    process: subprocess.Popen[bytes]
    error_log: BinaryIO | None = None

    def _error(self) -> StreamError:
        raw = b""
        if self.error_log:
            try:
                self.error_log.flush()
                self.error_log.seek(0)
                raw = self.error_log.read(16_384)
            except OSError:
                pass
        return StreamError(classify_stream_error(raw))

    def write(self, pcm: bytes) -> None:
        if not self.process.stdin or self.process.poll() is not None:
            raise self._error()
        try:
            self.process.stdin.write(pcm)
        except (BrokenPipeError, OSError) as exc:
            raise self._error() from exc

    def close(self) -> None:
        if self.process.stdin:
            try:
                self.process.stdin.close()
            except OSError:
                pass
        if self.process.poll() is None:
            try:
                self.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=2)
        if self.error_log:
            self.error_log.close()


def make_sink(config: Config) -> Sink:
    mount = "/" + config.dj_mount.lstrip("/")
    if mount == "/":
        raise ValueError("FFmpeg Icecast requires a named DJ mount; set AzuraCast Customize DJ/Streamer Mount Point to /live")
    if not config.radio_configured:
        raise RuntimeError("AzuraCast DJ connection not configured")
    if config.dj_host.startswith("-") or not (1 <= config.dj_port <= 65535):
        raise ValueError("Invalid DJ host/port")
    username = quote(config.dj_username, safe="")
    password = quote(config.dj_password, safe="")
    host = config.dj_host
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    url = f"icecast://{username}:{password}@{host}:{config.dj_port}{mount}"
    # ffmpeg handles source publishing itself; one encoder shared by every listener.
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
        "-f", "s16le", "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS), "-i", "pipe:0",
        "-codec:a", "libmp3lame", "-b:a", config.bitrate,
        "-content_type", "audio/mpeg", "-ice_name", "Roften Wave",
        "-f", "mp3", url,
    ]
    # Keep FFmpeg stderr in a private anonymous file, never stderr/syslog.
    # The source URL contains DJ credentials: expose only fixed error classes.
    error_log = tempfile.TemporaryFile(mode="w+b")
    try:
        proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
            stderr=error_log, bufsize=0,
        )
    except FileNotFoundError as exc:
        error_log.close()
        raise StreamError("FFmpeg executable not installed") from exc
    except Exception:
        error_log.close()
        raise
    return Sink(proc, error_log)


def make_decoder(audio_file: Path, *, offset: float = 0.0) -> subprocess.Popen[bytes]:
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-re"]
    if offset > 0:
        cmd += ["-ss", str(offset)]
    cmd += ["-i", str(audio_file), "-vn", "-f", "s16le", "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS), "pipe:1"]
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stdin=subprocess.DEVNULL, stderr=subprocess.DEVNULL, bufsize=0)


def silence(duration_seconds: float = 0.25) -> bytes:
    return bytes(int(BYTES_PER_SECOND * duration_seconds))
