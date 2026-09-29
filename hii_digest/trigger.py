"""Detecting "hii" trigger emails (and making sure we never answer our own digests)."""

from __future__ import annotations

import logging
import math
import re
from datetime import datetime, timedelta

from hii_digest import DIGEST_HEADER
from hii_digest.message import Email, normalize_address

log = logging.getLogger(__name__)

# "On Tue, 29 Sep 2026 at 12:01, Harsh <x@y.com> wrote:" (Gmail may wrap it over 2-3 lines)
_ON_WROTE = re.compile(r"^[ \t]*On\b[^\n]{0,200}(?:\n[^\n]{0,200}){0,2}?wrote:[ \t]*$", re.M | re.I)
# Outlook / generic forwarded-or-quoted headers
_ORIGINAL = re.compile(
    r"^[ \t]*(?:-{2,}\s*Original Message\s*-{2,}|-{2,}\s*Forwarded message\s*-{2,}|_{10,})[ \t]*$",
    re.M | re.I,
)
_FROM_SENT = re.compile(r"^[ \t]*From:[^\n]*\n[ \t]*(?:Sent|Date):", re.M | re.I)
# Mobile-client footers that are not part of what the user typed.
_MOBILE_FOOTER = re.compile(
    r"^\s*(?:sent from my \w[\w ]*|get outlook for \w+|sent from (?:mail|yahoo mail|gmail) for \w+)\s*$",
    re.I,
)
_INVISIBLE = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff"), None)


def strip_quoted_and_signature(text: str) -> str:
    """Return only what the sender typed: drop quoted replies, signatures and footers."""
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n").translate(_INVISIBLE)
    cut = len(text)
    for pattern in (_ON_WROTE, _ORIGINAL, _FROM_SENT):
        m = pattern.search(text)
        if m:
            cut = min(cut, m.start())
    text = text[:cut]

    kept: list[str] = []
    for line in text.split("\n"):
        if line.rstrip() in ("--", "-- "):  # RFC 3676 signature delimiter
            break
        if line.lstrip().startswith(">"):
            continue
        if _MOBILE_FOOTER.match(line):
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def _normalise_words(text: str) -> str:
    return " ".join(text.split()).lower()


def matches_trigger(text: str | None, word: str) -> bool:
    """True if ``text`` (ignoring case, whitespace, quotes, signatures) is exactly ``word``."""
    if not text:
        return False
    return _normalise_words(strip_quoted_and_signature(text)) == word.strip().lower()


def is_digest_message(email: Email, digest_label_id: str | None = None) -> bool:
    """True for emails sent by this app (custom header, or our digest label)."""
    if email.header(DIGEST_HEADER):
        return True
    return bool(digest_label_id and digest_label_id in email.labels)


def is_from_owner(email: Email, owner: str) -> bool:
    return normalize_address(email.from_addr) == normalize_address(owner)


def is_to_owner(email: Email, owner: str) -> bool:
    me = normalize_address(owner)
    return any(normalize_address(a) == me for a in email.to_addrs)


def is_trigger(
    email: Email,
    owner: str,
    word: str,
    *,
    digest_label_id: str | None = None,
    now: datetime | None = None,
    lookback: timedelta | None = None,
) -> bool:
    """Is ``email`` a trigger: owner -> owner, recent, and subject or body is exactly ``word``."""
    if email.labels & {"DRAFT", "SPAM", "TRASH"}:
        return False
    if is_digest_message(email, digest_label_id):
        return False
    if not (is_from_owner(email, owner) and is_to_owner(email, owner)):
        return False
    if now is not None and lookback is not None and email.received < now - lookback:
        return False
    if _normalise_words(email.subject) == word.lower():
        return True
    body = email.body_text if email.body_text is not None else email.snippet
    return matches_trigger(body, word)


def trigger_query(lookback: timedelta) -> str:
    """Gmail search used to find candidate triggers (re-checked precisely in code)."""
    days = max(1, math.ceil(lookback.total_seconds() / 86400) + 1)
    return f"from:me to:me newer_than:{days}d"


def find_pending_triggers(
    client,
    word: str,
    *,
    handled_label_id: str,
    digest_label_id: str | None,
    now: datetime,
    lookback: timedelta,
    limit: int = 25,
) -> list[Email]:
    """Unhandled trigger emails, oldest first."""
    owner = client.owner_email()
    pending: list[Email] = []
    for ref in client.search(trigger_query(lookback), limit):
        meta = client.get_message(ref["id"], full=False)
        if handled_label_id in meta.labels or is_digest_message(meta, digest_label_id):
            continue
        if not (is_from_owner(meta, owner) and is_to_owner(meta, owner)):
            continue
        if meta.received < now - lookback:
            continue
        full = client.get_message(ref["id"], full=True)
        if is_trigger(full, owner, word, digest_label_id=digest_label_id, now=now, lookback=lookback):
            pending.append(full)
        else:
            log.debug("Ignoring self-email %s (subject=%r): not exactly %r", full.id, full.subject, word)
    pending.sort(key=lambda e: e.received)
    return pending
