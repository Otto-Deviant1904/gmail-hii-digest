"""Transparent importance scoring for today's emails.

Every email starts at 0 and gains or loses points for each signal below. The
total decides the ranking, and every contribution is recorded as a human-readable
:class:`Reason` so the digest can explain *why* an email made the list.

=========================  ======  ==================================================
Signal                     Points  Notes
=========================  ======  ==================================================
Starred                    +4.0    You starred it (Gmail ``STARRED``)
Gmail "important"          +3.0    Gmail's own importance marker (``IMPORTANT``)
Unread                     +1.0    Still needs your attention (``UNREAD``)
Primary tab                +2.0    ``CATEGORY_PERSONAL`` or no category label
Updates tab                -1.0    Receipts, notifications (mild penalty)
Forums tab                 -2.0    Mailing lists, groups
Social tab                 -3.0    Social network notifications
Promotions tab             -4.0    Marketing
Real person                +2.0    Not a bulk/no-reply sender
Bulk / no-reply sender     -3.0    ``List-Unsubscribe``/``List-Id``/``Precedence: bulk``
                                   headers or a no-reply style address
Sent directly to you       +1.5    Your address is in ``To``
CC'd                       +0.5    Your address is only in ``Cc``
Not addressed to you       -1.0    Neither To nor Cc (BCC, mailing list)
Active thread              +0.5    per extra message in the thread, max +2.0
You replied in thread      +2.0    You have already engaged with this conversation
Urgency keyword            +1.5    each distinct keyword in subject/preview, max +3.0
=========================  ======  ==================================================

Excluded entirely: your own sent mail, trigger ("hii") emails, digests sent by
this app, drafts, chats, spam and trash. Ties are broken by most recent first.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from hii_digest.message import Email, normalize_address


@dataclass(frozen=True)
class Weights:
    starred: float = 4.0
    important: float = 3.0
    unread: float = 1.0
    primary: float = 2.0
    updates: float = -1.0
    forums: float = -2.0
    social: float = -3.0
    promotions: float = -4.0
    human_sender: float = 2.0
    bulk_sender: float = -3.0
    direct_to: float = 1.5
    cc: float = 0.5
    not_addressed: float = -1.0
    thread_per_message: float = 0.5
    thread_max: float = 2.0
    owner_replied: float = 2.0
    urgency_per_keyword: float = 1.5
    urgency_max: float = 3.0


DEFAULT_WEIGHTS = Weights()

URGENCY_KEYWORDS: tuple[str, ...] = (
    "urgent",
    "asap",
    "deadline",
    "action required",
    "action needed",
    "time sensitive",
    "time-sensitive",
    "overdue",
    "due today",
    "due tomorrow",
    "invoice",
    "payment due",
    "interview",
    "offer",
    "contract",
    "meeting",
    "reschedule",
    "security alert",
    "final notice",
)
_URGENCY_RE = [(kw, re.compile(r"\b" + re.escape(kw) + r"\b", re.I)) for kw in URGENCY_KEYWORDS]

_NOREPLY_RE = re.compile(
    r"(no[-_.]?reply|do[-_.]?not[-_.]?reply|mailer-daemon|notifications?|newsletters?|bounces?|automated|alerts?)",
    re.I,
)

CATEGORY_LABELS = {
    "CATEGORY_PERSONAL": "primary",
    "CATEGORY_UPDATES": "updates",
    "CATEGORY_FORUMS": "forums",
    "CATEGORY_SOCIAL": "social",
    "CATEGORY_PROMOTIONS": "promotions",
}


@dataclass(frozen=True)
class Reason:
    label: str
    points: float

    def __str__(self) -> str:
        return f"{self.label} {self.points:+g}"


@dataclass
class ScoredEmail:
    email: Email
    score: float
    reasons: list[Reason] = field(default_factory=list)

    @property
    def category(self) -> str:
        return category_of(self.email)


def category_of(email: Email) -> str:
    for label, name in CATEGORY_LABELS.items():
        if label in email.labels and name != "primary":
            return name
    return "primary"


def is_bulk_sender(email: Email) -> bool:
    if email.header("list-unsubscribe") or email.header("list-id"):
        return True
    if email.header("precedence").strip().lower() in {"bulk", "list", "junk"}:
        return True
    auto = email.header("auto-submitted").strip().lower()
    if auto and auto != "no":
        return True
    local = email.from_addr.split("@", 1)[0]
    return bool(_NOREPLY_RE.search(local))


def urgency_hits(email: Email) -> list[str]:
    haystack = f"{email.subject}\n{email.snippet}"
    return [kw for kw, rx in _URGENCY_RE if rx.search(haystack)]


def score_email(email: Email, owner: str, weights: Weights = DEFAULT_WEIGHTS) -> ScoredEmail:
    reasons: list[Reason] = []

    def add(label: str, points: float) -> None:
        if points:
            reasons.append(Reason(label, points))

    labels = email.labels
    if "STARRED" in labels:
        add("starred", weights.starred)
    if "IMPORTANT" in labels:
        add("Gmail important", weights.important)
    if "UNREAD" in labels:
        add("unread", weights.unread)

    category = category_of(email)
    add(f"{category} tab", getattr(weights, category))

    if is_bulk_sender(email):
        add("bulk/no-reply sender", weights.bulk_sender)
    else:
        add("real person", weights.human_sender)

    me = normalize_address(owner)
    if any(normalize_address(a) == me for a in email.to_addrs):
        add("sent to you", weights.direct_to)
    elif any(normalize_address(a) == me for a in email.cc_addrs):
        add("cc'd", weights.cc)
    else:
        add("not addressed to you", weights.not_addressed)

    extra = max(0, email.thread_message_count - 1)
    if extra:
        add(
            f"thread of {email.thread_message_count}",
            min(extra * weights.thread_per_message, weights.thread_max),
        )
    if email.owner_replied:
        add("you replied", weights.owner_replied)

    hits = urgency_hits(email)
    if hits:
        add(
            "keywords: " + ", ".join(hits[:3]),
            min(len(hits) * weights.urgency_per_keyword, weights.urgency_max),
        )

    total = round(sum(r.points for r in reasons), 2)
    return ScoredEmail(email=email, score=total, reasons=reasons)


def rank(emails: Iterable[Email], owner: str, weights: Weights = DEFAULT_WEIGHTS) -> list[ScoredEmail]:
    """Score and sort (highest score first, then most recent first)."""
    scored = [score_email(e, owner, weights) for e in emails]
    scored.sort(key=lambda s: (-s.score, -s.email.received.timestamp()))
    return scored
