from __future__ import annotations

import logging
import threading
from email import message_from_bytes, policy

import pytest

from hii_digest import DIGEST_HEADER, DIGEST_TRIGGER_HEADER
from hii_digest.app import Labels, process_once, run_loop
from hii_digest.fake_gmail import FakeHttpError
from hii_digest.gmail import GmailClient
from hii_digest.timewindow import now_utc
from tests.conftest import NOW, OWNER, at, incoming, trigger_msg


def _mailbox(fake):
    fake.add(incoming("m1", subject="Interview on Thursday", labels=["INBOX", "IMPORTANT", "UNREAD"]))
    fake.add(
        incoming(
            "m2",
            subject="Big sale",
            sender="Shop <deals@shop.example>",
            labels=["INBOX", "CATEGORY_PROMOTIONS"],
            headers={"List-Unsubscribe": "<x>"},
        )
    )
    fake.add(trigger_msg("trig1", subject="hii", headers={"References": "<older@x>"}))


def _mime(resource):
    return message_from_bytes(resource["raw"], policy=policy.default)


def test_process_once_replies_in_thread(fake, client, cfg):
    _mailbox(fake)
    assert process_once(client, cfg, now=NOW) == 1
    assert len(fake.sent) == 1
    sent = fake.sent[0]
    assert sent["threadId"] == "trig1"
    mime = _mime(sent)
    assert mime["Subject"] == "Re: hii"
    assert mime["To"] == OWNER and mime["From"] == OWNER
    assert mime["In-Reply-To"] == "<trig1@mail.example.com>"
    assert mime["References"] == "<older@x> <trig1@mail.example.com>"
    assert mime[DIGEST_HEADER]
    assert mime[DIGEST_TRIGGER_HEADER] == "trig1"
    assert mime["Auto-Submitted"] == "auto-replied"
    assert mime.get_content_type() == "multipart/alternative"
    types = [p.get_content_type() for p in mime.iter_parts()]
    assert types == ["text/plain", "text/html"]
    html = mime.get_body(("html",)).get_content()
    assert "Interview on Thursday" in html
    # labels: trigger handled, digest tagged
    handled = fake.label_id("hii-digest/handled")
    digest = fake.label_id("hii-digest/sent")
    assert fake.label_id("hii-digest")  # parent created for tidy nesting
    assert handled in fake.messages["trig1"]["labelIds"]
    assert digest in fake.messages[sent["id"]]["labelIds"]


def test_each_trigger_answered_once_even_across_restarts(fake, cfg):
    _mailbox(fake)
    assert process_once(GmailClient(fake), cfg, now=NOW) == 1
    assert process_once(GmailClient(fake), cfg, now=NOW) == 0  # "restart": fresh client + labels
    assert process_once(GmailClient(fake), cfg, now=NOW) == 0
    assert len(fake.sent) == 1


def test_never_replies_to_its_own_digest(fake, client, cfg):
    _mailbox(fake)
    process_once(client, cfg, now=NOW)
    digest_resource = fake.sent[0]
    # Even if someone strips our label off the digest, the header still protects us.
    fake.messages[digest_resource["id"]]["labelIds"] = ["INBOX", "SENT"]
    fake.add(trigger_msg("fake_digest", body="hii", headers={DIGEST_HEADER: "1.0.0"}))
    assert process_once(client, cfg, now=NOW) == 0
    assert len(fake.sent) == 1


def test_crash_between_send_and_label_does_not_duplicate(fake, client, cfg):
    _mailbox(fake)
    process_once(client, cfg, now=NOW)
    handled = fake.label_id("hii-digest/handled")
    fake.messages["trig1"]["labelIds"].remove(handled)  # simulate the label never being applied
    assert process_once(client, cfg, now=NOW) == 0
    assert len(fake.sent) == 1
    assert handled in fake.messages["trig1"]["labelIds"]


def test_failed_send_is_retried_next_check(fake, client, cfg):
    _mailbox(fake)
    fake.fail_send = FakeHttpError(500, "backend error")
    with pytest.raises(FakeHttpError):
        process_once(client, cfg, now=NOW)
    assert fake.label_id("hii-digest/handled") not in fake.messages["trig1"]["labelIds"]
    fake.fail_send = None
    assert process_once(client, cfg, now=NOW) == 1


def test_multiple_triggers_each_get_a_reply(fake, client, cfg):
    _mailbox(fake)
    fake.add(trigger_msg("trig2", body="HII ", when=at(16, 58)))
    assert process_once(client, cfg, now=NOW) == 2
    assert {s["threadId"] for s in fake.sent} == {"trig1", "trig2"}


def test_empty_subject_trigger_gets_descriptive_subject(fake, client, cfg):
    fake.add(incoming("m1"))
    fake.add(trigger_msg("trig1", subject="", body="hii"))
    process_once(client, cfg, now=NOW)
    assert _mime(fake.sent[0])["Subject"] == "Your day in 1 email - Tue 29 Sep 2026"


def test_ignores_unrelated_preexisting_label(fake, client, cfg):
    old = fake.add_label("Mia/hii-handled")
    _mailbox(fake)
    fake.messages["trig1"]["labelIds"].append(old)
    assert process_once(client, cfg, now=NOW) == 1


def test_no_trigger_logs_and_sends_nothing(fake, client, cfg, caplog):
    fake.add(incoming("m1"))
    with caplog.at_level(logging.INFO):
        assert process_once(client, cfg, now=NOW) == 0
    assert "no new 'hii' emails" in caplog.text
    assert fake.sent == []


def test_digest_label_failure_is_not_fatal(fake, client, cfg, monkeypatch):
    _mailbox(fake)
    labels = Labels(client, cfg)
    original = client.add_labels

    def flaky(message_id, label_ids):
        if label_ids == [labels.digest]:
            raise FakeHttpError(500, "oops")
        return original(message_id, label_ids)

    monkeypatch.setattr(client, "add_labels", flaky)
    assert process_once(client, cfg, labels, now=NOW) == 1
    assert labels.handled in fake.messages["trig1"]["labelIds"]


def test_run_loop_survives_errors_and_stops(fake, client, cfg, monkeypatch, caplog):
    calls = []

    def boom(*a, **k):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("network down")
        return 0

    monkeypatch.setattr("hii_digest.app.process_once", boom)
    stop = threading.Event()
    monkeypatch.setattr(stop, "wait", lambda delay: None)
    with caplog.at_level(logging.INFO):
        run_loop(client, cfg.__class__(poll_interval=1), stop=stop, max_checks=3)
    assert len(calls) == 3
    assert "Check failed (RuntimeError: network down)" in caplog.text
    assert "Watching harsh.thakur@gmail.com" in caplog.text


def test_run_loop_exits_when_stop_set(client, cfg):
    stop = threading.Event()
    stop.set()
    run_loop(client, cfg, stop=stop)  # returns immediately


def test_run_loop_reraises_auth_errors(client, cfg, monkeypatch):
    from google.auth.exceptions import RefreshError

    def expired(*a, **k):
        raise RefreshError("invalid_grant")

    monkeypatch.setattr("hii_digest.app.process_once", expired)
    with pytest.raises(RefreshError):
        run_loop(client, cfg, stop=threading.Event(), max_checks=1)


def test_only_new_ignores_triggers_before_startup(fake, client, cfg):
    _mailbox(fake)  # trigger received at 16:55
    assert process_once(client, cfg, now=NOW, not_before=NOW) == 0
    assert process_once(client, cfg, now=NOW, not_before=at(16, 0)) == 1


def test_run_loop_only_new_passes_startup_time(client, cfg, monkeypatch):
    seen = {}

    def fake_once(client, cfg, labels, not_before=None):
        seen["not_before"] = not_before
        return 0

    monkeypatch.setattr("hii_digest.app.process_once", fake_once)
    run_loop(client, cfg, stop=threading.Event(), max_checks=1, only_new=True)
    assert seen["not_before"] is not None


def test_only_new_cutoff_is_the_startup_moment(fake, client, cfg, monkeypatch):
    """No grace period: a trigger received even 1s before launch is skipped."""
    seen = {}

    def fake_once(client, cfg, labels, not_before=None):
        seen["not_before"] = not_before
        return 0

    monkeypatch.setattr("hii_digest.app.process_once", fake_once)
    started = now_utc()
    run_loop(client, cfg, stop=threading.Event(), max_checks=1, only_new=True)
    assert started <= seen["not_before"] <= now_utc()


def test_unreadable_thread_does_not_send_a_duplicate(fake, client, cfg, monkeypatch):
    """The pre-send guard fails closed: an unreadable thread means we do not send."""
    _mailbox(fake)
    labels = Labels(client, cfg)

    def boom(thread_id):
        raise FakeHttpError(503, "backend error")

    monkeypatch.setattr(client, "get_thread", boom)
    with pytest.raises(FakeHttpError):
        process_once(client, cfg, labels, now=NOW)
    assert fake.sent == []
    # Left unhandled, so the next poll retries it.
    assert labels.handled not in fake.messages["trig1"]["labelIds"]


def test_run_loop_retries_the_trigger_after_a_read_error(fake, client, cfg, monkeypatch):
    _mailbox(fake)
    from hii_digest.app import already_answered as real_guard

    attempts = []

    def flaky(client_, trigger, digest_label_id):
        attempts.append(trigger.id)
        if len(attempts) == 1:
            raise FakeHttpError(503, "backend error")
        return real_guard(client_, trigger, digest_label_id)

    monkeypatch.setattr("hii_digest.app.already_answered", flaky)
    stop = threading.Event()
    monkeypatch.setattr(stop, "wait", lambda delay: None)
    run_loop(client, cfg.__class__(poll_interval=1), stop=stop, max_checks=2)
    assert len(attempts) == 2
    assert len(fake.sent) == 1  # answered exactly once, on the retry
