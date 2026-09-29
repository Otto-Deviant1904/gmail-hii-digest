"""Orchestration: find triggers, answer each exactly once, and the polling loop."""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta

from hii_digest import DIGEST_TRIGGER_HEADER
from hii_digest.config import Config
from hii_digest.digest import Digest, build_digest
from hii_digest.message import Email
from hii_digest.reply import build_reply
from hii_digest.timewindow import now_utc
from hii_digest.trigger import find_pending_triggers, is_digest_message

log = logging.getLogger(__name__)


class Labels:
    """Ids of the Gmail labels the app manages (created on first use)."""

    def __init__(self, client, cfg: Config) -> None:
        self.handled = client.ensure_label(cfg.handled_label)
        self.digest = client.ensure_label(cfg.digest_label)

    @staticmethod
    def lookup(client, cfg: Config) -> tuple[str | None, str | None]:
        """(handled, digest) label ids if they exist, without creating them (for preview)."""
        return client.find_label(cfg.handled_label), client.find_label(cfg.digest_label)


def already_answered(client, trigger: Email, digest_label_id: str) -> bool:
    """True if the trigger's thread already holds a digest answering this trigger.

    Guards against duplicates if we crashed after sending but before labelling.
    """
    try:
        thread = client.get_thread(trigger.thread_id)
    except Exception as exc:
        log.debug("Could not inspect thread %s: %s", trigger.thread_id, exc)
        return False
    return any(
        is_digest_message(m, digest_label_id) and m.header(DIGEST_TRIGGER_HEADER) == trigger.id
        for m in thread
    )


def process_trigger(client, cfg: Config, labels: Labels, trigger: Email, digest: Digest) -> bool:
    """Reply to one trigger. Returns True if a digest was sent."""
    if already_answered(client, trigger, labels.digest):
        log.info("Trigger %s was already answered; marking handled", trigger.id)
        client.add_labels(trigger.id, [labels.handled])
        return False
    owner = client.owner_email()
    sent = client.send(build_reply(trigger, digest, owner), thread_id=trigger.thread_id)
    # Label our digest so it is never mistaken for a trigger, then mark the trigger handled.
    try:
        client.add_labels(sent["id"], [labels.digest])
    except Exception as exc:  # the X-Hii-Digest header still protects us
        log.warning("Could not label digest %s: %s", sent.get("id"), exc)
    client.add_labels(trigger.id, [labels.handled])
    return True


def process_once(
    client,
    cfg: Config,
    labels: Labels | None = None,
    *,
    now: datetime | None = None,
    summarizer=None,
    not_before: datetime | None = None,
) -> int:
    """One check: answer every pending trigger. Returns the number of digests sent.

    ``not_before`` (used by ``run --only-new``) ignores triggers received earlier.
    """
    labels = labels or Labels(client, cfg)
    now = now or now_utc()
    triggers = find_pending_triggers(
        client,
        cfg.trigger_word,
        handled_label_id=labels.handled,
        digest_label_id=labels.digest,
        now=now,
        lookback=timedelta(hours=cfg.trigger_lookback_hours),
    )
    if not_before is not None:
        skipped = [t for t in triggers if t.received < not_before]
        if skipped:
            log.debug("Ignoring %d trigger(s) received before startup (--only-new)", len(skipped))
        triggers = [t for t in triggers if t.received >= not_before]
    if not triggers:
        log.info("Checked inbox: no new '%s' emails", cfg.trigger_word)
        return 0

    log.info("Found %d new '%s' email(s) - building digest", len(triggers), cfg.trigger_word)
    digest = build_digest(
        client,
        cfg,
        now=now,
        summarizer=summarizer,
        digest_label_id=labels.digest,
        handled_label_id=labels.handled,
    )
    sent = 0
    for trigger in triggers:
        if process_trigger(client, cfg, labels, trigger, digest):
            sent += 1
            log.info(
                "Sent digest (%d emails, %s summaries) in reply to %s received %s",
                len(digest.items),
                digest.summary_source,
                trigger.id,
                trigger.received.astimezone(cfg.tz).strftime("%H:%M:%S"),
            )
    return sent


def run_loop(
    client,
    cfg: Config,
    stop: threading.Event | None = None,
    max_checks: int | None = None,
    only_new: bool = False,
) -> None:
    """Poll forever (until ``stop`` is set / Ctrl+C). Transient errors are logged and retried.

    With ``only_new`` only triggers received after startup are answered.
    """
    stop = stop or threading.Event()
    labels = Labels(client, cfg)
    not_before = now_utc() - timedelta(seconds=5) if only_new else None
    log.info(
        "Watching %s for '%s' every %gs (timezone %s). Press Ctrl+C to stop.",
        client.owner_email(),
        cfg.trigger_word,
        cfg.poll_interval,
        cfg.timezone,
    )
    checks = 0
    failures = 0
    while not stop.is_set():
        try:
            process_once(client, cfg, labels, not_before=not_before)
            failures = 0
        except Exception as exc:
            if _is_auth_error(exc):
                raise
            failures += 1
            log.error("Check failed (%s: %s); will retry", type(exc).__name__, exc)
        checks += 1
        if max_checks is not None and checks >= max_checks:
            break
        # Back off gently after repeated failures (max 5 minutes).
        delay = min(cfg.poll_interval * (2 ** min(failures, 4)) if failures else cfg.poll_interval, 300)
        stop.wait(delay)


def _is_auth_error(exc: Exception) -> bool:
    try:
        from google.auth.exceptions import RefreshError
    except ImportError:  # pragma: no cover
        return False
    return isinstance(exc, RefreshError)
