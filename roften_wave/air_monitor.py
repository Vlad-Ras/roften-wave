"""Debounced DJ source alerts, independent of network and Telegram I/O.

Only alert after sustained unexpected absence of Live DJ while a radio
track is expected. Intentional pause/idle is not an outage.
"""
from __future__ import annotations


class AirAlertState:
    def __init__(self, threshold: int = 3):
        if threshold < 1:
            raise ValueError("threshold must be positive")
        self.threshold = threshold
        self.misses = 0
        self.alerted = False

    def evaluate(self, *, expected: bool, healthy: bool) -> str | None:
        """Return 'lost', 'restored', or None, without repeating messages."""
        if not expected:
            self.misses = 0
            self.alerted = False
            return None
        if healthy:
            self.misses = 0
            if self.alerted:
                self.alerted = False
                return "restored"
            return None
        self.misses += 1
        if self.misses >= self.threshold and not self.alerted:
            self.alerted = True
            return "lost"
        return None
