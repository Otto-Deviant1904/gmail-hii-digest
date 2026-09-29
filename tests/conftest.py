from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from hii_digest.config import load_config
from hii_digest.fake_gmail import FakeGmail, make_message
from hii_digest.gmail import GmailClient

MEL = ZoneInfo("Australia/Melbourne")
OWNER = "harsh.thakur@gmail.com"
ME = f"Harsh Thakur <{OWNER}>"
# A fixed "now": Tue 29 Sep 2026, 5:00 PM Melbourne (AEST, UTC+10).
NOW = datetime(2026, 9, 29, 17, 0, tzinfo=MEL)


def at(hour: int, minute: int = 0, days_ago: int = 0) -> datetime:
    return datetime(2026, 9, 29, hour, minute, tzinfo=MEL) - timedelta(days=days_ago)


def trigger_msg(msg_id: str = "trig1", *, subject: str = "", body: str = "hii", when=None, **kw) -> dict:
    return make_message(
        msg_id,
        sender=kw.pop("sender", ME),
        to=kw.pop("to", ME),
        subject=subject,
        body=body,
        received=when or at(16, 55),
        labels=kw.pop("labels", ["INBOX", "SENT"]),
        **kw,
    )


def incoming(msg_id: str, *, subject: str = "Hello", body: str = "Some text.", when=None, **kw) -> dict:
    return make_message(
        msg_id,
        sender=kw.pop("sender", "Priya Sharma <priya@acme.example>"),
        to=kw.pop("to", ME),
        subject=subject,
        body=body,
        received=when or at(10),
        labels=kw.pop("labels", ["INBOX", "CATEGORY_PERSONAL"]),
        **kw,
    )


@pytest.fixture
def cfg():
    return load_config(env={})


@pytest.fixture
def fake():
    return FakeGmail(OWNER, now=NOW.astimezone(timezone.utc))


@pytest.fixture
def client(fake):
    return GmailClient(fake)
