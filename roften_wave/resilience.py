"""Pure retry/circuit-breaker policy; no network I/O or secrets.

A circuit trips after several failures within a rolling window. This keeps a
broken external source from hammering AzuraCast/Yandex every second. The circuit
closes automatically after a cooldown, and the next attempt is a probe.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

MAX_TRACK_ATTEMPTS = 3  # initial playback + two retries
RETRY_DELAYS_SECONDS = (1.0, 2.0)


@dataclass
class RecoveryGate:
    threshold: int = 4
    window_seconds: float = 90.0
    base_cooldown_seconds: float = 30.0
    max_cooldown_seconds: float = 120.0
    failures: deque[float] = field(default_factory=deque)
    open_until: float = 0.0
    trips: int = 0
    total_failures: int = 0

    def failed(self, now: float) -> None:
        self.total_failures += 1
        while self.failures and self.failures[0] <= now - self.window_seconds:
            self.failures.popleft()
        self.failures.append(now)
        if len(self.failures) >= self.threshold:
            cooldown = min(self.base_cooldown_seconds * 2 ** min(self.trips, 8), self.max_cooldown_seconds)
            self.trips += 1
            self.open_until = max(self.open_until, now + cooldown)
            self.failures.clear()

    def seconds_until_probe(self, now: float) -> float:
        return max(0.0, self.open_until - now)

    def succeeded(self) -> None:
        self.failures.clear()
        self.open_until = 0.0
        self.trips = 0
