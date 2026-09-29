"""Realistic fake mailbox for ``preview --demo`` (no Gmail account needed).

All people, companies and addresses are fictional.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from hii_digest.fake_gmail import FakeGmail, make_message

DEMO_OWNER = "you@example.com"
ME = f"Alex Morgan <{DEMO_OWNER}>"
UNSUB = {"List-Unsubscribe": "<https://example.com/unsubscribe>"}


def demo_now(tz: ZoneInfo, today: datetime | None = None) -> datetime:
    """A fixed 5:45 PM "now" on today's date, so the sample always has a full day."""
    local_today = (today or datetime.now(tz)).astimezone(tz).date()
    return datetime.combine(local_today, time(17, 45), tzinfo=tz)


def build_demo_mailbox(tz: ZoneInfo, today: datetime | None = None) -> FakeGmail:
    now = demo_now(tz, today)
    day = now.date()

    def at(h: int, m: int) -> datetime:
        return datetime.combine(day, time(h, m), tzinfo=tz)

    msgs = [
        # 1. Manager, urgent, active thread you replied to
        make_message(
            "d01a",
            thread_id="t01",
            sender="Priya Sharma <priya.sharma@acme-corp.example>",
            to=ME,
            subject="Q3 roadmap review",
            body="Hi Alex, can we review the Q3 roadmap on Thursday?",
            received=at(8, 2),
            labels=["INBOX", "IMPORTANT", "CATEGORY_PERSONAL"],
        ),
        make_message(
            "d01b",
            thread_id="t01",
            sender=ME,
            to="Priya Sharma <priya.sharma@acme-corp.example>",
            subject="Re: Q3 roadmap review",
            body="Sure, Thursday works for me.",
            received=at(8, 40),
            labels=["SENT"],
        ),
        make_message(
            "d01c",
            thread_id="t01",
            sender="Priya Sharma <priya.sharma@acme-corp.example>",
            to=ME,
            subject="Re: Q3 roadmap review",
            body="Change of plan: leadership moved the review to today at 3pm. I need your slides "
            "ASAP, ideally by 1pm, with the latency numbers from last sprint. Thanks!",
            received=at(11, 18),
            labels=["INBOX", "IMPORTANT", "UNREAD", "CATEGORY_PERSONAL"],
        ),
        # 2. Interview invite (starred)
        make_message(
            "d02",
            sender="Jordan Lee <jordan.lee@northwind-talent.example>",
            to=ME,
            subject="Interview invitation: Senior Platform Engineer",
            body="Hi Alex, thanks for applying to Northwind. We'd love to invite you to a 45-minute "
            "technical interview on Thursday at 10:00 AM AEST. Please confirm by replying to this email.",
            received=at(9, 5),
            labels=["INBOX", "IMPORTANT", "STARRED", "UNREAD", "CATEGORY_PERSONAL"],
        ),
        # 3. Invoice due
        make_message(
            "d03",
            sender="CloudHost Billing <billing@cloudhost.example>",
            to=ME,
            subject="Invoice INV-20931 due tomorrow - action required",
            body="Your invoice INV-20931 for $248.00 AUD is due tomorrow, 30 September. Pay now to "
            "avoid service interruption to your production cluster.",
            received=at(7, 30),
            labels=["INBOX", "IMPORTANT", "UNREAD", "CATEGORY_UPDATES"],
        ),
        # 4. Family
        make_message(
            "d04",
            sender="Mum <margaret.morgan@example.net>",
            to=ME,
            subject="Dinner on Sunday?",
            body="Hi love, are you free for dinner on Sunday? Dad wants to try the new Thai place "
            "near the station. Let me know by Friday.",
            received=at(12, 47),
            labels=["INBOX", "UNREAD", "CATEGORY_PERSONAL"],
        ),
        # 5. Code review request (bulk notifications sender, but addressed to you)
        make_message(
            "d05",
            sender="GitHub <notifications@github.example>",
            to=ME,
            subject="[acme/api] Fix token refresh race condition (PR #482) - review requested",
            body="Sam Patel requested your review on PR #482: Fix token refresh race condition. "
            "3 files changed, 41 additions, 12 deletions.",
            received=at(10, 12),
            labels=["INBOX", "UNREAD", "CATEGORY_UPDATES"],
            headers={**UNSUB, "List-Id": "acme/api <api.acme.github.example>"},
        ),
        # 6. CC'd on an incident thread
        make_message(
            "d06",
            thread_id="t06",
            sender="Sam Patel <sam.patel@acme-corp.example>",
            to="Platform Team <platform@acme-corp.example>",
            cc=ME,
            subject="Production incident follow-up: API 502s",
            body="Root cause was an expired TLS cert on the internal load balancer. Postmortem draft "
            "is in the shared drive; please add your timeline notes before Friday's meeting.",
            received=at(14, 3),
            labels=["INBOX", "IMPORTANT", "CATEGORY_PERSONAL"],
        ),
        make_message(
            "d06b",
            thread_id="t06",
            sender="Lina Ortiz <lina.ortiz@acme-corp.example>",
            to="Platform Team <platform@acme-corp.example>",
            subject="Production incident follow-up: API 502s",
            body="Paging worked well, but alert noise was high.",
            received=at(13, 20),
            labels=["INBOX", "CATEGORY_PERSONAL"],
        ),
        # 7. Social
        make_message(
            "d07",
            sender="LinkedIn <messages-noreply@linkedin.example>",
            to=ME,
            subject="You appeared in 23 searches this week",
            body="See who's looking at your profile and grow your network.",
            received=at(6, 55),
            labels=["INBOX", "CATEGORY_SOCIAL"],
            headers=UNSUB,
        ),
        # 8. Promotion (contains the word "offer" but is still penalised)
        make_message(
            "d08",
            sender="ShopMart Deals <deals@shopmart.example>",
            to=ME,
            subject="48-hour flash sale: 40% off + a special offer inside",
            body="Don't miss out! Our biggest sale of the season ends Thursday at midnight.",
            received=at(9, 30),
            labels=["INBOX", "UNREAD", "CATEGORY_PROMOTIONS"],
            headers=UNSUB,
        ),
        # 9. Security alert (automated, but Gmail marks important)
        make_message(
            "d09",
            sender="Harbour Bank <no-reply@harbourbank.example>",
            to=ME,
            subject="Security alert: new sign-in to your account",
            body="We noticed a new sign-in from Chrome on Windows in Sydney, NSW at 4:12 PM. If this "
            "wasn't you, call us immediately on 1800 000 000.",
            received=at(16, 14),
            labels=["INBOX", "IMPORTANT", "UNREAD", "CATEGORY_UPDATES"],
        ),
        # 10. Academic collaborator
        make_message(
            "d10",
            sender="Dr Emily Chen <e.chen@southbank-uni.example>",
            to=ME,
            subject="Research collaboration - meeting next week?",
            body="Hi Alex, I enjoyed your talk on observability. Would you be open to a 30-minute "
            "meeting next week to discuss a joint paper? Tuesday or Wednesday afternoon suit me.",
            received=at(15, 2),
            labels=["INBOX", "UNREAD", "CATEGORY_PERSONAL"],
        ),
        # 11. Forum / mailing list
        make_message(
            "d11",
            sender="Chris Nguyen <chris.n@example.org>",
            to="melb-python@groups.example",
            subject="[melb-python] Meetup venue change for October",
            body="Heads up: October's meetup moves to the State Library, Conference Room 2.",
            received=at(12, 5),
            labels=["INBOX", "CATEGORY_FORUMS"],
            headers={"List-Id": "<melb-python.groups.example>", "Precedence": "list"},
        ),
        # 12. Receipt
        make_message(
            "d12",
            sender="RideNow Receipts <noreply@ridenow.example>",
            to=ME,
            subject="Your Tuesday morning trip with RideNow",
            body="Thanks for riding with Dave. Total $23.40, charged to Visa ending 4242.",
            received=at(8, 21),
            labels=["INBOX", "CATEGORY_UPDATES"],
            headers=UNSUB,
        ),
        # 13. Newsletter-style promotion
        make_message(
            "d13",
            sender="Medium Daily Digest <noreply@medium.example>",
            to=ME,
            subject="Stories for you: 10 Python tricks senior engineers use",
            body="Today's highlights, picked for you based on your reading history.",
            received=at(7, 1),
            labels=["INBOX", "CATEGORY_PROMOTIONS"],
            headers=UNSUB,
        ),
        # 14. Client contract (already read)
        make_message(
            "d14",
            sender="Olivia Brown <olivia@designstudio.example>",
            to=ME,
            subject="Contract draft for the website redesign",
            body="Hi Alex, attached is the contract draft for the website redesign. Could you look "
            "over the payment milestones and let me know if anything needs changing?",
            received=at(13, 36),
            labels=["INBOX", "CATEGORY_PERSONAL"],
        ),
        # --- Excluded from the digest (to show the rules working) ---
        # The "hii" trigger itself
        make_message(
            "trig",
            sender=ME,
            to=ME,
            subject="hii",
            body="hii",
            received=at(17, 44),
            labels=["INBOX", "SENT"],
        ),
        # Received yesterday: outside today's window
        make_message(
            "old1",
            sender="Priya Sharma <priya.sharma@acme-corp.example>",
            to=ME,
            subject="Urgent: budget sign-off",
            body="Need this by Friday.",
            received=at(9, 0) - timedelta(days=1),
            labels=["INBOX", "IMPORTANT", "CATEGORY_PERSONAL"],
        ),
    ]
    return FakeGmail(DEMO_OWNER, msgs, now=now)
