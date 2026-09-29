from __future__ import annotations

from datetime import timedelta

import pytest

from hii_digest import DIGEST_HEADER
from hii_digest.message import normalize_address, parse_message
from hii_digest.trigger import (
    find_pending_triggers,
    is_trigger,
    matches_trigger,
    strip_quoted_and_signature,
    trigger_query,
)
from tests.conftest import NOW, OWNER, at, incoming, trigger_msg

DAY = timedelta(hours=24)


@pytest.mark.parametrize(
    "text",
    [
        "hii",
        "HII",
        "Hii",
        "  hii  ",
        "\n\nhii\n\n",
        "hii\r\n",
        "\u200bhii",
        "hii\n\n-- \nHarsh Thakur\nSoftware Engineer",
        "hii\n--\nHarsh",
        "hii\n\nOn Tue, 29 Sep 2026 at 10:00, Harsh Thakur <harsh@gmail.com> wrote:\n> Your day in 10 emails",
        "hii\n\nOn Tue, 29 Sep 2026 at 10:00, Harsh Thakur <\nharsh@gmail.com> wrote:\n> quoted",
        "hii\n> some quoted line\n> another",
        "hii\n\nSent from my iPhone",
        "hii\n\n-----Original Message-----\nFrom: someone\nhello",
        "hii\n\nFrom: Harsh\nSent: Tuesday\nSubject: hi",
    ],
)
def test_matches_trigger_positive(text):
    assert matches_trigger(text, "hii")


@pytest.mark.parametrize(
    "text",
    ["", None, "hi", "hiii", "hii there", "please hii", "hii\nsecond line", "hii!", "> hii", "h ii"],
)
def test_matches_trigger_negative(text):
    assert not matches_trigger(text, "hii")


def test_custom_trigger_word():
    assert matches_trigger("  Digest ", "digest")
    assert not matches_trigger("hii", "digest")


def test_strip_keeps_typed_text_only():
    text = "hello\nworld\n\nOn Mon, 1 Jan 2026, Bob <b@x.com> wrote:\n> quoted"
    assert strip_quoted_and_signature(text) == "hello\nworld"
    assert strip_quoted_and_signature("") == ""


def _email(resource):
    return parse_message(resource)


def test_is_trigger_owner_to_owner():
    assert is_trigger(_email(trigger_msg()), OWNER, "hii", now=NOW, lookback=DAY)


def test_is_trigger_matches_subject_even_if_body_differs():
    msg = trigger_msg(subject="HII", body="anything at all")
    assert is_trigger(_email(msg), OWNER, "hii")


def test_is_trigger_uses_normalised_gmail_address():
    msg = trigger_msg(sender="Harsh <HarshThakur+demo@gmail.com>", to="harsh.thakur@googlemail.com")
    assert is_trigger(_email(msg), OWNER, "hii")
    assert normalize_address("A.B+x@GMAIL.com") == "ab@gmail.com"
    assert normalize_address("a.b@work.com") == "a.b@work.com"
    assert normalize_address("nobody") == "nobody"


@pytest.mark.parametrize(
    "resource",
    [
        incoming("x1", body="hii"),  # from someone else
        trigger_msg(to="Someone <someone@example.com>"),  # owner -> other person
        trigger_msg(body="hii", headers={DIGEST_HEADER: "1.0.0"}),  # our own digest
        trigger_msg(labels=["DRAFT"]),  # unsent draft
        trigger_msg(labels=["TRASH"]),
        trigger_msg(subject="Re: hii", body="Your day in 10 emails\n..."),  # a digest-like reply
    ],
)
def test_is_not_trigger(resource):
    assert not is_trigger(_email(resource), OWNER, "hii", now=NOW, lookback=DAY)


def test_digest_label_prevents_trigger():
    msg = trigger_msg(labels=["INBOX", "Label_9"])
    assert not is_trigger(_email(msg), OWNER, "hii", digest_label_id="Label_9")


def test_old_trigger_outside_lookback_is_ignored():
    msg = trigger_msg(when=at(12, days_ago=2))
    assert not is_trigger(_email(msg), OWNER, "hii", now=NOW, lookback=DAY)
    assert is_trigger(_email(msg), OWNER, "hii", now=NOW, lookback=timedelta(hours=72))


def test_trigger_query():
    assert trigger_query(DAY) == "from:me to:me newer_than:2d"
    assert trigger_query(timedelta(hours=1)) == "from:me to:me newer_than:2d"
    assert trigger_query(timedelta(hours=72)) == "from:me to:me newer_than:4d"


def test_find_pending_triggers_filters_and_orders(fake, client):
    fake.add(trigger_msg("t_late", when=at(16, 50)))
    fake.add(trigger_msg("t_early", when=at(9, 0)))
    fake.add(trigger_msg("t_handled", labels=["INBOX", "HANDLED"]))
    fake.add(trigger_msg("t_other_text", body="note to self: buy milk"))
    fake.add(trigger_msg("t_digest", body="hii", headers={DIGEST_HEADER: "1"}))
    fake.add(trigger_msg("t_to_friend", to="Friend <f@example.com>"))
    fake.add(trigger_msg("t_old", when=at(9, days_ago=1) - timedelta(hours=2)))
    fake.add(incoming("in1", body="hii"))
    found = find_pending_triggers(
        client, "hii", handled_label_id="HANDLED", digest_label_id=None, now=NOW, lookback=DAY
    )
    assert [e.id for e in found] == ["t_early", "t_late"]


def test_sender_display_falls_back_to_address():
    e = _email(incoming("a", sender="plain@example.com"))
    assert e.sender_display == "plain@example.com"
