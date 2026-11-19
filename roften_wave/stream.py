from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from .config import Config

SAMPLE_RATE = 44100
CHANNELS = 2
BYTES_PER_SECOND = SAMPLE_RATE * CHANNELS * 2


@dataclass
class Sink:
    process: subprocess.Popen[bytes]

    def write(self, pcm: bytes) -> None:
        if not self.process.stdin or self.process.poll() is not None:
            raise RuntimeError("Icecast broadcaster stopped or disconnected")
        self.process.stdin.write(pcm)

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
    # Don't include the URL (and DJ password) in logs or API errors.
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, bufsize=0)
    except FileNotFoundError as exc:
        raise RuntimeError("ffmpeg not installed") from exc
    return Sink(proc)


def make_decoder(audio_file: Path, *, offset: float = 0.0) -> subprocess.Popen[bytes]:
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-re"]
    if offset > 0:
        cmd += ["-ss", str(offset)]
    cmd += ["-i", str(audio_file), "-vn", "-f", "s16le", "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS), "pipe:1"]
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stdin=subprocess.DEVNULL, stderr=subprocess.DEVNULL, bufsize=0)


def silence(duration_seconds: float = 0.25) -> bytes:
    return bytes(int(BYTES_PER_SECOND * duration_seconds))
