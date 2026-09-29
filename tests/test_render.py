from __future__ import annotations

from hii_digest.digest import build_digest
from hii_digest.message import parse_message
from hii_digest.render import render_html, render_text
from hii_digest.reply import build_reply, reply_subject
from tests.conftest import NOW, OWNER, incoming, trigger_msg


def _digest(fake, client, cfg):
    fake.add(
        incoming(
            "m1",
            subject="Interview <script>alert(1)</script> & offer",
            sender='"O\'Brien, Pat" <pat@acme.example>',
            labels=["INBOX", "IMPORTANT", "UNREAD"],
            body="Please confirm your interview slot for Thursday.",
        )
    )
    fake.add(
        incoming(
            "m2",
            subject="Sale",
            sender="Shop <deals@shop.example>",
            labels=["CATEGORY_PROMOTIONS"],
            headers={"List-Unsubscribe": "<x>"},
        )
    )
    return build_digest(client, cfg, now=NOW)


def test_html_rendering(fake, client, cfg):
    d = _digest(fake, client, cfg)
    html = render_html(d)
    assert html.startswith("<!DOCTYPE html>")
    assert "<script>" not in html  # everything escaped
    assert "Interview &lt;script&gt;alert(1)&lt;/script&gt; &amp; offer" in html
    assert "Your day in 2 emails" in html
    assert "Tue 29 Sep 2026" in html and "Australia/Melbourne" in html
    assert "score 12.5" in html  # small-print score
    assert "Gmail important +3" in html
    assert 'href="https://mail.google.com/mail/?authuser=harsh.thakur@gmail.com#all/m1"' in html
    assert "Summary of your day" in html
    assert "O&#x27;Brien, Pat" in html
    assert "promotions" in html
    assert "<style" not in html  # inline styles only (email clients strip <style>)


def test_text_rendering(fake, client, cfg):
    d = _digest(fake, client, cfg)
    text = render_text(d)
    assert text.startswith("YOUR DAY IN 2 EMAILS\n")
    assert "1. Interview <script>alert(1)</script> & offer" in text
    assert "From: O'Brien, Pat <pat@acme.example> · 10:00 AM · primary" in text
    assert "Score 12.5: " in text
    assert "https://mail.google.com/mail/?authuser=" in text
    assert "Please confirm your interview slot for Thursday." in text
    assert text.rstrip().endswith("(sent by hii-digest)")


def test_empty_digest_rendering(fake, client, cfg):
    d = build_digest(client, cfg, now=NOW)
    assert "Nothing important has arrived since midnight." in render_html(d)
    text = render_text(d)
    assert "Nothing important has arrived since midnight." in text
    assert "Only 0 emails qualified today" in text
    assert "Your day in 0 emails" in render_html(d)


def test_singular_title(fake, client, cfg):
    fake.add(incoming("m1"))
    d = build_digest(client, cfg, now=NOW)
    assert d.title == "Your day in 1 email"
    assert "1 email since midnight" in render_text(d)


def test_reply_subjects(fake, client, cfg):
    d = _digest(fake, client, cfg)
    assert reply_subject(parse_message(trigger_msg(subject="hii")), d) == "Re: hii"
    assert reply_subject(parse_message(trigger_msg(subject="Re: hii")), d) == "Re: hii"
    assert (
        reply_subject(parse_message(trigger_msg(subject="")), d) == "Your day in 2 emails - Tue 29 Sep 2026"
    )


def test_build_reply_without_message_id(fake, client, cfg):
    d = _digest(fake, client, cfg)
    trig = parse_message(trigger_msg(subject="hii"))
    trig.headers.pop("message-id")
    msg = build_reply(trig, d, OWNER)
    assert msg["In-Reply-To"] is None and msg["References"] is None
    assert msg["Message-ID"].endswith("@gmail.com>")
