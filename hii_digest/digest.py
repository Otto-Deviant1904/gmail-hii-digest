"""Build today's digest: collect -> exclude -> score -> summarise."""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

from hii_digest.config import Config
from hii_digest.message import Email
from hii_digest.scoring import ScoredEmail, category_of, rank
from hii_digest.timewindow import Window, format_local_time, today_window
from hii_digest.trigger import is_digest_message, is_from_owner, is_trigger

log = logging.getLogger(__name__)


@dataclass
class DayStats:
    total: int
    by_category: dict[str, int]
    top_senders: list[tuple[str, int]]
    important: int
    unread: int


@dataclass
class DigestItem:
    rank: int
    scored: ScoredEmail
    summary: str
    link: str
    time_label: str

    @property
    def email(self) -> Email:
        return self.scored.email


@dataclass
class Digest:
    owner: str
    window: Window
    items: list[DigestItem]
    stats: DayStats
    overall: str
    top_n: int
    summary_source: str = "offline"  # "llm" or "offline"
    trigger_word: str = "hii"
    generated_at: datetime | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def title(self) -> str:
        return f"Your day in {len(self.items)} email{'s' if len(self.items) != 1 else ''}"


def gmail_link(email: Email, owner: str) -> str:
    """Deep link that opens the conversation in Gmail for the right account."""
    return f"https://mail.google.com/mail/?authuser={owner}#all/{email.thread_id}"


def exclusion_reason(
    email: Email,
    owner: str,
    cfg: Config,
    *,
    digest_label_id: str | None,
    handled_label_id: str | None,
) -> str | None:
    """Why ``email`` must not appear in the digest (None if it may)."""
    labels = email.labels
    if labels & {"SPAM", "TRASH"}:
        return "spam/trash"
    if "DRAFT" in labels:
        return "draft"
    if "CHAT" in labels:
        return "chat"
    if is_digest_message(email, digest_label_id):
        return "digest"
    if handled_label_id and handled_label_id in labels:
        return "trigger"
    if is_trigger(email, owner, cfg.trigger_word, digest_label_id=digest_label_id):
        return "trigger"
    if "SENT" in labels or is_from_owner(email, owner):
        return "sent by you"
    return None


def collect_today(
    client,
    cfg: Config,
    window: Window,
    *,
    digest_label_id: str | None = None,
    handled_label_id: str | None = None,
) -> list[Email]:
    """Emails received today, one per conversation (the latest incoming message).

    Thread-level signals (message count, whether the owner replied) are attached
    to each returned email.
    """
    owner = client.owner_email()
    query = f"after:{window.start_epoch} -from:me -in:chats"
    refs = client.search(query, cfg.max_candidates)
    thread_ids: list[str] = []
    for ref in refs:
        tid = ref.get("threadId") or ref["id"]
        if tid not in thread_ids:
            thread_ids.append(tid)

    picked: list[Email] = []
    for tid in thread_ids:
        try:
            thread = client.get_thread(tid)
        except Exception as exc:  # a thread deleted mid-run shouldn't break the digest
            log.warning("Skipping thread %s: %s", tid, exc)
            continue
        eligible = [
            m
            for m in thread
            if window.contains(m.received)
            and exclusion_reason(
                m, owner, cfg, digest_label_id=digest_label_id, handled_label_id=handled_label_id
            )
            is None
        ]
        if not eligible:
            continue
        latest = max(eligible, key=lambda m: m.received)
        latest.extra["today_messages"] = eligible
        latest.thread_message_count = len([m for m in thread if not is_digest_message(m, digest_label_id)])
        latest.owner_replied = any(
            is_from_owner(m, owner)
            and not is_digest_message(m, digest_label_id)
            and not is_trigger(m, owner, cfg.trigger_word, digest_label_id=digest_label_id)
            for m in thread
        )
        picked.append(latest)
    return picked


def compute_stats(emails: list[Email]) -> DayStats:
    """Day-level numbers, counted over every incoming message today (not just threads)."""
    messages = [m for e in emails for m in e.extra.get("today_messages", [e])]
    categories = Counter(category_of(m) for m in messages)
    senders = Counter(m.sender_display for m in messages)
    return DayStats(
        total=len(messages),
        by_category=dict(categories.most_common()),
        top_senders=senders.most_common(3),
        important=sum(1 for m in messages if "IMPORTANT" in m.labels),
        unread=sum(1 for m in messages if "UNREAD" in m.labels),
    )


def build_digest(
    client,
    cfg: Config,
    *,
    now: datetime | None = None,
    summarizer=None,
    digest_label_id: str | None = None,
    handled_label_id: str | None = None,
) -> Digest:
    from hii_digest.summarize import make_summarizer

    window = today_window(cfg.tz, now)
    owner = client.owner_email()
    emails = collect_today(
        client, cfg, window, digest_label_id=digest_label_id, handled_label_id=handled_label_id
    )
    scored = rank(emails, owner)
    top = scored[: cfg.top_n]

    if cfg.llm_enabled:  # the LLM gets the real body; offline mode only needs the snippet
        for s in top:
            try:
                s.email.body_text = client.get_message(s.email.id, full=True).body_text
            except Exception as exc:
                log.warning("Could not fetch body of %s: %s", s.email.id, exc)

    stats = compute_stats(emails)
    summarizer = summarizer or make_summarizer(cfg)
    summaries = summarizer.summarize(top, stats, window)

    items = [
        DigestItem(
            rank=i,
            scored=s,
            summary=summaries.lines.get(s.email.id, ""),
            link=gmail_link(s.email, owner),
            time_label=format_local_time(s.email.received, window.tz),
        )
        for i, s in enumerate(top, start=1)
    ]
    notes: list[str] = []
    if len(items) < cfg.top_n:
        notes.append(
            f"Only {len(items)} email{'s' if len(items) != 1 else ''} qualified today "
            f"(asked for {cfg.top_n})."
        )
    log.info(
        "Built digest: %d emails today in %d conversations, top %d chosen, summaries=%s",
        stats.total,
        len(emails),
        len(items),
        summaries.source,
    )
    return Digest(
        owner=owner,
        window=window,
        items=items,
        stats=stats,
        overall=summaries.overall,
        top_n=cfg.top_n,
        summary_source=summaries.source,
        trigger_word=cfg.trigger_word,
        generated_at=window.end,
        notes=notes,
    )
