from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from hii_digest.digest import build_digest
from hii_digest.timewindow import format_local_time, today_window
from tests.conftest import MEL, incoming

UTC = timezone.utc


def test_window_starts_at_local_midnight():
    now = datetime(2026, 9, 29, 17, 0, tzinfo=MEL)
    w = today_window(MEL, now)
    assert w.start == datetime(2026, 9, 29, 0, 0, tzinfo=MEL)
    assert w.start.astimezone(UTC) == datetime(2026, 9, 28, 14, 0, tzinfo=UTC)  # AEST = UTC+10
    assert w.label == "Tue 29 Sep 2026"
    assert w.start_epoch == int(w.start.timestamp())


def test_utc_now_just_after_local_midnight():
    # 14:30 UTC on the 28th is already 00:30 on the 29th in Melbourne.
    w = today_window(MEL, datetime(2026, 9, 28, 14, 30, tzinfo=UTC))
    assert w.start.date().isoformat() == "2026-09-29"
    assert w.end.tzinfo == MEL


def test_utc_now_just_before_local_midnight():
    w = today_window(MEL, datetime(2026, 9, 28, 13, 59, tzinfo=UTC))
    assert w.start.date().isoformat() == "2026-09-28"


def test_dst_start_day_is_23_hours():
    # Melbourne DST starts Sun 4 Oct 2026 at 2am (UTC+10 -> UTC+11).
    w = today_window(MEL, datetime(2026, 10, 4, 23, 0, tzinfo=MEL))
    assert w.start.astimezone(UTC) == datetime(2026, 10, 3, 14, 0, tzinfo=UTC)
    nxt = today_window(MEL, datetime(2026, 10, 5, 9, 0, tzinfo=MEL))
    assert nxt.start.astimezone(UTC) == datetime(2026, 10, 4, 13, 0, tzinfo=UTC)
    # same-tz subtraction in Python is wall-clock; compare real elapsed time in UTC
    assert (nxt.start.astimezone(UTC) - w.start.astimezone(UTC)).total_seconds() == 23 * 3600


def test_other_timezone():
    now = datetime(2026, 9, 29, 3, 0, tzinfo=UTC)
    assert today_window(ZoneInfo("UTC"), now).start == datetime(2026, 9, 29, tzinfo=UTC)
    ny = today_window(ZoneInfo("America/New_York"), now)
    assert ny.start.date().isoformat() == "2026-09-28"


def test_contains_boundaries():
    w = today_window(MEL, datetime(2026, 9, 29, 17, 0, tzinfo=MEL))
    assert w.contains(w.start)
    assert w.contains(w.end)
    assert not w.contains(datetime(2026, 9, 28, 23, 59, 59, tzinfo=MEL))


def test_naive_now_rejected():
    with pytest.raises(ValueError):
        today_window(MEL, datetime(2026, 9, 29, 12, 0))


def test_default_now_is_current_time():
    w = today_window(MEL)
    assert w.start <= w.end


@pytest.mark.parametrize(
    "h,m,expected",
    [(0, 5, "12:05 AM"), (9, 0, "9:00 AM"), (12, 0, "12:00 PM"), (13, 7, "1:07 PM"), (23, 59, "11:59 PM")],
)
def test_format_local_time(h, m, expected):
    assert format_local_time(datetime(2026, 9, 29, h, m, tzinfo=MEL), MEL) == expected


def test_format_local_time_converts_zone():
    assert format_local_time(datetime(2026, 9, 28, 23, 30, tzinfo=UTC), MEL) == "9:30 AM"


def test_digest_uses_midnight_boundary(fake, client, cfg):
    fake.add(incoming("before", when=datetime(2026, 9, 28, 23, 59, tzinfo=MEL)))
    fake.add(incoming("after", when=datetime(2026, 9, 29, 0, 1, tzinfo=MEL)))
    digest = build_digest(client, cfg, now=datetime(2026, 9, 29, 0, 30, tzinfo=MEL))
    assert [i.email.id for i in digest.items] == ["after"]
