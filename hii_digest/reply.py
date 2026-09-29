"""Build the digest reply as a MIME message that threads correctly in Gmail."""

from __future__ import annotations

from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from hii_digest import DIGEST_HEADER, DIGEST_TRIGGER_HEADER, __version__
from hii_digest.digest import Digest
from hii_digest.message import Email
from hii_digest.render import render_html, render_text


def reply_subject(trigger: Email, digest: Digest) -> str:
    """``Re: <trigger subject>`` (keeps Gmail threading), or a descriptive subject if empty."""
    subject = trigger.subject.strip()
    if subject:
        return subject if subject.lower().startswith("re:") else f"Re: {subject}"
    return f"{digest.title} - {digest.window.label}"


def build_reply(trigger: Email, digest: Digest, owner: str) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = owner
    msg["To"] = owner
    msg["Subject"] = reply_subject(trigger, digest)
    msg["Date"] = formatdate(localtime=True)
    domain = owner.rsplit("@", 1)[-1] if "@" in owner else None
    msg["Message-ID"] = make_msgid(idstring="hii-digest", domain=domain)
    parent = trigger.message_id_header
    if parent:
        msg["In-Reply-To"] = parent
        refs = trigger.header("references").split()
        if parent not in refs:
            refs.append(parent)
        msg["References"] = " ".join(refs)
    # Markers so we never treat our own digest as a trigger, and can detect duplicates.
    msg[DIGEST_HEADER] = __version__
    msg[DIGEST_TRIGGER_HEADER] = trigger.id
    msg["Auto-Submitted"] = "auto-replied"  # RFC 3834
    msg.set_content(render_text(digest))
    msg.add_alternative(render_html(digest), subtype="html")
    return msg
