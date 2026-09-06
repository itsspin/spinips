"""Bounded, log-driven leveling evidence with optional XP checkpoints."""
from collections import deque
from datetime import datetime
import math


class ProgressionTracker:
    def __init__(self):
        self.started_at = None
        self.gained = 0.0
        self.measured_events = 0
        self.events = 0
        self.aa = 0
        self.levels = 0
        self.level = 0
        self.percent = None
        self.checkpoint_at = None
        self.checkpoint_percent = None
        self.checkpoint_level = 0
        self.checkpoint_rate = None
        self.checkpoint_rate_at = None
        self.history = deque(maxlen=60)
        self.rate_started_at = None
        self.rate_gained = 0.0
        self.rate_events = 0
        self.rate_measured = 0

    def reset_rate(self, when):
        self.rate_started_at = when
        self.rate_gained = 0.0
        self.rate_events = self.rate_measured = 0

    def observe(self, when, kind, fields):
        if self.started_at is None:
            self.started_at = when
        if self.rate_started_at is None:
            self.rate_started_at = when
        if kind == "xp":
            self.events += 1
            self.rate_events += 1
            try:
                amount = float(fields.get("pct", "nan"))
            except (TypeError, ValueError):
                amount = math.nan
            if math.isfinite(amount) and 0 <= amount <= 100:
                self.measured_events += 1
                self.gained += amount
                self.rate_measured += 1
                self.rate_gained += amount
                if self.percent is not None:
                    self.percent = min(100, self.percent + amount)
                self.history.append((when, amount))
            else:
                # A gain without a quantity makes the exact bar position unknown.
                self.percent = None
        elif kind == "level":
            self.level = int(fields["level"])
            self.levels += 1
            self.percent = 0.0
            # Percent-of-level is not comparable across level requirements.
            self.checkpoint_at = None
            self.checkpoint_rate = None
            self.reset_rate(when)
        elif kind == "aa":
            self.aa += 1
        elif kind == "death_you":
            self.percent = None
            self.checkpoint_at = None
            self.checkpoint_rate = None
            self.reset_rate(when)

    def checkpoint(self, when: datetime, level: int, percent: float):
        if (isinstance(level, bool) or not isinstance(level, int) or
                not 1 <= level <= 125 or isinstance(percent, bool) or
                not isinstance(percent, (float, int)) or
                not math.isfinite(percent) or not 0 <= percent < 100):
            raise ValueError("Enter a level from 1 to 125 and XP from 0 to below 100%")
        rate = None
        if self.checkpoint_at and level == self.checkpoint_level:
            elapsed = (when - self.checkpoint_at).total_seconds()
            delta = percent - self.checkpoint_percent
            if elapsed >= 60 and delta >= 0:
                rate = delta * 3600 / elapsed
        self.checkpoint_rate = rate
        self.checkpoint_rate_at = when
        self.checkpoint_at = when
        self.checkpoint_percent = float(percent)
        self.checkpoint_level = level
        if self.level and self.level != level:
            self.reset_rate(when)
        self.level = level
        self.percent = float(percent)

    def snapshot(self, now):
        seconds = max(0, (now - self.started_at).total_seconds()) if self.started_at else 0
        # Need one minute before extrapolating a session into an hourly rate.
        rate_seconds = max(0, (now - self.rate_started_at).total_seconds()) if self.rate_started_at else 0
        rate = self.rate_gained * 3600 / rate_seconds if rate_seconds >= 60 and self.rate_measured else None
        source = "log" if rate is not None else "waiting"
        if self.rate_events > self.rate_measured:
            rate = None
            source = "waiting"
        if rate is None and self.checkpoint_rate is not None:
            age = (now - self.checkpoint_rate_at).total_seconds()
            if 0 <= age <= 1800:
                rate, source = self.checkpoint_rate, "checkpoints"
        remaining = (100 - self.percent) / rate * 3600 if self.percent is not None and rate and rate > 0 else None
        return {
            "sessionSeconds": seconds, "xpEvents": self.events,
            "measuredEvents": self.measured_events, "gainedPercent": self.gained,
            "percentPerHour": rate, "rateSource": source,
            "currentPercent": self.percent, "level": self.level,
            "secondsToLevel": remaining, "levelsGained": self.levels,
            "aaEarned": self.aa,
            "aaPerHour": self.aa * 3600 / seconds if seconds >= 60 else None,
            "history": [{"at": stamp.isoformat(), "percent": amount} for stamp, amount in self.history],
        }
