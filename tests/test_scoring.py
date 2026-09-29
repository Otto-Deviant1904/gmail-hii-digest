from __future__ import annotations

from hii_digest.message import parse_message
from hii_digest.scoring import (
    DEFAULT_WEIGHTS,
    Weights,
    category_of,
    is_bulk_sender,
    rank,
    score_email,
    urgency_hits,
)
from tests.conftest import OWNER, at, incoming


def E(msg_id="x", **kw):  # noqa: N802 - tiny factory
    return parse_message(incoming(msg_id, **kw))


def points(scored, prefix):
    return sum(r.points for r in scored.reasons if r.label.startswith(prefix))


def test_label_signals():
    s = score_email(E(labels=["STARRED", "IMPORTANT", "UNREAD", "CATEGORY_PERSONAL"]), OWNER)
    assert points(s, "starred") == DEFAULT_WEIGHTS.starred
    assert points(s, "Gmail important") == DEFAULT_WEIGHTS.important
    assert points(s, "unread") == DEFAULT_WEIGHTS.unread
    assert points(s, "primary tab") == DEFAULT_WEIGHTS.primary
    assert s.score == round(sum(r.points for r in s.reasons), 2)


def test_categories():
    assert category_of(E(labels=["INBOX"])) == "primary"
    assert category_of(E(labels=["CATEGORY_PERSONAL"])) == "primary"
    for label, name in [
        ("CATEGORY_PROMOTIONS", "promotions"),
        ("CATEGORY_SOCIAL", "social"),
        ("CATEGORY_FORUMS", "forums"),
        ("CATEGORY_UPDATES", "updates"),
    ]:
        email = E(labels=[label])
        assert category_of(email) == name
        assert points(score_email(email, OWNER), f"{name} tab") == getattr(DEFAULT_WEIGHTS, name)
    # promotions penalised hardest, updates only mildly
    w = DEFAULT_WEIGHTS
    assert w.promotions < w.social < w.forums < w.updates < 0 < w.primary


def test_bulk_sender_detection():
    assert is_bulk_sender(E(headers={"List-Unsubscribe": "<mailto:u@x>"}))
    assert is_bulk_sender(E(headers={"List-Id": "<list.example>"}))
    assert is_bulk_sender(E(headers={"Precedence": "bulk"}))
    assert is_bulk_sender(E(headers={"Auto-Submitted": "auto-generated"}))
    assert not is_bulk_sender(E(headers={"Auto-Submitted": "no"}))
    for addr in [
        "noreply@x.com",
        "no-reply@x.com",
        "do-not-reply@x.com",
        "notifications@github.com",
        "newsletter@x.com",
        "alerts@bank.com",
        "mailer-daemon@x.com",
    ]:
        assert is_bulk_sender(E(sender=f"Bot <{addr}>")), addr
    assert not is_bulk_sender(E(sender="Priya <priya@acme.example>"))
    s = score_email(E(sender="noreply@x.com"), OWNER)
    assert points(s, "bulk/no-reply") == DEFAULT_WEIGHTS.bulk_sender
    assert points(score_email(E(), OWNER), "real person") == DEFAULT_WEIGHTS.human_sender


def test_addressing():
    direct = score_email(E(to="Harsh <harshthakur@gmail.com>"), OWNER)
    cc = score_email(E(to="team@acme.example", cc=OWNER), OWNER)
    neither = score_email(E(to="list@acme.example"), OWNER)
    assert points(direct, "sent to you") == DEFAULT_WEIGHTS.direct_to
    assert points(cc, "cc'd") == DEFAULT_WEIGHTS.cc
    assert points(neither, "not addressed") == DEFAULT_WEIGHTS.not_addressed
    assert direct.score > cc.score > neither.score


def test_thread_activity_and_reply():
    e = E()
    e.thread_message_count = 10
    e.owner_replied = True
    s = score_email(e, OWNER)
    assert points(s, "thread of 10") == DEFAULT_WEIGHTS.thread_max  # capped
    assert points(s, "you replied") == DEFAULT_WEIGHTS.owner_replied
    e2 = E()
    e2.thread_message_count = 2
    assert points(score_email(e2, OWNER), "thread of 2") == DEFAULT_WEIGHTS.thread_per_message


def test_urgency_keywords_and_cap():
    assert urgency_hits(E(subject="URGENT: invoice overdue")) == ["urgent", "overdue", "invoice"]
    assert urgency_hits(E(subject="Lunch?", body="Offer letter attached")) == ["offer"]
    assert urgency_hits(E(subject="Offering", body="nothing")) == []  # whole words only
    s = score_email(E(subject="Urgent ASAP deadline: action required"), OWNER)
    assert points(s, "keywords") == DEFAULT_WEIGHTS.urgency_max


def test_ranking_order():
    manager = E("manager", subject="Need slides ASAP", labels=["IMPORTANT", "UNREAD", "CATEGORY_PERSONAL"])
    friend = E("friend", subject="Dinner?", labels=["UNREAD"])
    receipt = E("receipt", sender="noreply@rides.example", labels=["CATEGORY_UPDATES"])
    promo = E(
        "promo",
        subject="Special offer!",
        sender="Shop <deals@shop.example>",
        labels=["CATEGORY_PROMOTIONS"],
        headers={"List-Unsubscribe": "<x>"},
    )
    social = E("social", sender="Social <messages-noreply@social.example>", labels=["CATEGORY_SOCIAL"])
    starred = E("starred", labels=["STARRED", "IMPORTANT"])
    order = [s.email.id for s in rank([promo, receipt, friend, social, manager, starred], OWNER)]
    assert order == ["starred", "manager", "friend", "receipt", "promo", "social"]


def test_ties_broken_by_recency():
    older = E("older", when=at(8))
    newer = E("newer", when=at(15))
    assert [s.email.id for s in rank([older, newer], OWNER)] == ["newer", "older"]


def test_custom_weights():
    promo = E("p", labels=["CATEGORY_PROMOTIONS"])
    assert score_email(promo, OWNER, Weights(promotions=10)).score > score_email(promo, OWNER).score


def test_reason_str():
    s = score_email(E(labels=["STARRED"]), OWNER)
    assert "starred +4" in [str(r) for r in s.reasons]
