from __future__ import annotations

from dataclasses import replace

from hii_digest import DIGEST_HEADER
from hii_digest.digest import build_digest, collect_today, exclusion_reason, gmail_link
from hii_digest.message import parse_message
from hii_digest.summarize import OfflineSummarizer
from hii_digest.timewindow import today_window
from tests.conftest import ME, MEL, NOW, OWNER, at, incoming, trigger_msg


def _ids(digest):
    return [i.email.id for i in digest.items]


def test_exclusions(fake, client, cfg):
    fake.add(incoming("keep", subject="Real email"))
    fake.add(incoming("yesterday", when=at(23, 30, days_ago=1)))
    fake.add(incoming("spam", labels=["SPAM"]))
    fake.add(incoming("draft", labels=["DRAFT"]))
    fake.add(incoming("chat", labels=["CHAT"]))
    fake.add(trigger_msg("trig"))
    fake.add(incoming("mine", sender=ME, to="Boss <boss@acme.example>", labels=["SENT"]))
    fake.add(incoming("digest", sender=ME, headers={DIGEST_HEADER: "1"}, labels=["INBOX", "SENT"]))
    digest = build_digest(client, cfg, now=NOW)
    assert _ids(digest) == ["keep"]
    assert digest.stats.total == 1


def test_exclusion_reason_labels(cfg):
    e = parse_message(incoming("x", labels=["INBOX", "HANDLED"]))
    assert exclusion_reason(e, OWNER, cfg, digest_label_id=None, handled_label_id="HANDLED") == "trigger"
    e = parse_message(incoming("x", labels=["INBOX", "DIG"]))
    assert exclusion_reason(e, OWNER, cfg, digest_label_id="DIG", handled_label_id=None) == "digest"
    e = parse_message(incoming("x", labels=["TRASH"]))
    assert exclusion_reason(e, OWNER, cfg, digest_label_id=None, handled_label_id=None) == "spam/trash"


def test_top_n_and_note(fake, client, cfg):
    for i in range(12):
        fake.add(incoming(f"m{i:02d}", subject=f"Email {i}", when=at(8, i)))
    digest = build_digest(client, cfg, now=NOW)
    assert len(digest.items) == 10
    assert digest.notes == []
    assert [i.rank for i in digest.items] == list(range(1, 11))

    small = build_digest(client, replace(cfg, top_n=20), now=NOW)
    assert len(small.items) == 12
    assert small.notes == ["Only 12 emails qualified today (asked for 20)."]


def test_one_entry_per_thread_with_thread_signals(fake, client, cfg):
    fake.add(incoming("a1", thread_id="T", subject="Plan", when=at(9)))
    fake.add(
        incoming(
            "a2",
            thread_id="T",
            subject="Re: Plan",
            sender=ME,
            to="p@acme.example",
            labels=["SENT"],
            when=at(9, 30),
        )
    )
    fake.add(incoming("a3", thread_id="T", subject="Re: Plan", when=at(11)))
    window = today_window(MEL, NOW)
    emails = collect_today(client, cfg, window)
    assert [e.id for e in emails] == ["a3"]
    assert emails[0].thread_message_count == 3
    assert emails[0].owner_replied is True
    assert len(emails[0].extra["today_messages"]) == 2


def test_empty_day(fake, client, cfg):
    digest = build_digest(client, cfg, now=NOW, summarizer=OfflineSummarizer())
    assert digest.items == []
    assert "No emails have arrived today" in digest.overall
    assert digest.notes == ["Only 0 emails qualified today (asked for 10)."]


def test_items_have_links_and_local_times(fake, client, cfg):
    fake.add(incoming("m1", when=at(9, 5)))
    digest = build_digest(client, cfg, now=NOW)
    item = digest.items[0]
    assert item.time_label == "9:05 AM"
    assert item.link == gmail_link(item.email, OWNER)
    assert item.link.startswith("https://mail.google.com/mail/?authuser=harsh.thakur@gmail.com#all/")


def test_deleted_thread_is_skipped(fake, client, cfg, monkeypatch):
    fake.add(incoming("m1"))
    fake.add(incoming("m2", when=at(11)))
    real = client.get_thread

    def flaky(tid):
        if tid == "m1":
            raise RuntimeError("404")
        return real(tid)

    monkeypatch.setattr(client, "get_thread", flaky)
    assert _ids(build_digest(client, cfg, now=NOW)) == ["m2"]


def test_stats(fake, client, cfg):
    fake.add(incoming("a", sender="Priya <p@acme.example>", labels=["IMPORTANT", "UNREAD"]))
    fake.add(incoming("b", sender="Priya <p@acme.example>", when=at(11), labels=["CATEGORY_PROMOTIONS"]))
    fake.add(incoming("c", sender="Sam <s@acme.example>", when=at(12), labels=["UNREAD"]))
    d = build_digest(client, cfg, now=NOW)
    assert d.stats.total == 3
    assert d.stats.by_category == {"primary": 2, "promotions": 1}
    assert d.stats.top_senders[0] == ("Priya", 2)
    assert (d.stats.important, d.stats.unread) == (1, 2)
    assert "Busiest senders: Priya (2)" in d.overall
