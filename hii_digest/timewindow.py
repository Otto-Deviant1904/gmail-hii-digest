"""The "today" window: from local midnight (in the configured timezone) until now."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Window:
    start: datetime  # aware, local midnight
    end: datetime  # aware, "now"
    tz: ZoneInfo

    def contains(self, moment: datetime) -> bool:
        return self.start <= moment <= self.end

    @property
    def start_epoch(self) -> int:
        return int(self.start.timestamp())

    @property
    def label(self) -> str:
        """Human date such as ``Tue 29 Sep 2026``."""
        d = self.start
        return f"{d:%a} {d.day} {d:%b %Y}"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def today_window(tz: ZoneInfo, now: datetime | None = None) -> Window:
    """Window from local midnight of ``now``'s local date until ``now``.

    ``now`` must be timezone-aware (defaults to the current time). Midnight is
    computed with zoneinfo so DST transitions are handled correctly (a day can be
    23 or 25 hours long).
    """
    now = now or now_utc()
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    local_now = now.astimezone(tz)
    start = datetime.combine(local_now.date(), time(0, 0), tzinfo=tz)
    return Window(start=start, end=local_now, tz=tz)


def format_local_time(moment: datetime, tz: ZoneInfo) -> str:
    """``9:05 AM`` style local time (portable: no ``%-I``, which fails on Windows)."""
    local = moment.astimezone(tz)
    hour = local.hour % 12 or 12
    return f"{hour}:{local.minute:02d} {'AM' if local.hour < 12 else 'PM'}"
