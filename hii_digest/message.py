"""Parsing of Gmail API message resources into a small, typed model."""

from __future__ import annotations

import base64
import html
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import getaddresses, parseaddr
from html.parser import HTMLParser
from typing import ClassVar

# Headers we ask Gmail for when fetching metadata only.
METADATA_HEADERS = [
    "From",
    "To",
    "Cc",
    "Subject",
    "Date",
    "Message-ID",
    "References",
    "In-Reply-To",
    "List-Unsubscribe",
    "List-Id",
    "Precedence",
    "Auto-Submitted",
    "X-Hii-Digest",
    "X-Hii-Digest-Trigger",
]


def normalize_address(addr: str) -> str:
    """Canonical form of an email address for identity comparisons.

    Lower-cases, and for Gmail addresses removes dots and ``+tags`` from the local
    part (Gmail ignores both), so ``Harsh.Thakur+x@gmail.com`` equals
    ``harshthakur@gmail.com``.
    """
    addr = (addr or "").strip().lower()
    if "@" not in addr:
        return addr
    local, _, domain = addr.rpartition("@")
    if domain in ("gmail.com", "googlemail.com"):
        local = local.split("+", 1)[0].replace(".", "")
        domain = "gmail.com"
    return f"{local}@{domain}"


@dataclass
class Email:
    """The subset of a Gmail message the app cares about."""

    id: str
    thread_id: str
    subject: str
    from_name: str
    from_addr: str
    to_addrs: list[str]
    cc_addrs: list[str]
    received: datetime  # timezone-aware (UTC) from Gmail's internalDate
    labels: set[str]
    snippet: str
    headers: dict[str, str]  # lower-cased header name -> value
    body_text: str | None = None
    # Filled in from the thread when available.
    thread_message_count: int = 1
    owner_replied: bool = False
    extra: dict = field(default_factory=dict)

    @property
    def message_id_header(self) -> str:
        return self.headers.get("message-id", "")

    def header(self, name: str) -> str:
        return self.headers.get(name.lower(), "")

    @property
    def sender_display(self) -> str:
        return self.from_name or self.from_addr or "(unknown sender)"


def _decode_b64url(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def _charset(part: dict) -> str:
    for h in part.get("headers", []) or []:
        if h.get("name", "").lower() == "content-type":
            m = re.search(r'charset="?([\w\-]+)"?', h.get("value", ""), re.I)
            if m:
                return m.group(1)
    return "utf-8"


def _decode_part(part: dict) -> str:
    data = (part.get("body") or {}).get("data")
    if not data:
        return ""
    raw = _decode_b64url(data)
    try:
        return raw.decode(_charset(part), errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


class _TextExtractor(HTMLParser):
    _SKIP: ClassVar[frozenset[str]] = frozenset({"script", "style", "head", "title"})
    _BLOCK: ClassVar[frozenset[str]] = frozenset(
        {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "table", "blockquote"}
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip_depth:
            self.parts.append(data)


def html_to_text(markup: str) -> str:
    parser = _TextExtractor()
    parser.feed(markup)
    parser.close()
    text = "".join(parser.parts)
    lines = [re.sub(r"[ \t\u00a0]+", " ", line).strip() for line in text.splitlines()]
    out: list[str] = []
    for line in lines:
        if line or (out and out[-1]):
            out.append(line)
    return "\n".join(out).strip()


def extract_body_text(payload: dict | None) -> str:
    """Return the best plain-text body of a Gmail ``payload`` (prefers text/plain)."""
    if not payload:
        return ""
    plain: list[str] = []
    htmls: list[str] = []

    def walk(part: dict) -> None:
        mime = (part.get("mimeType") or "").lower()
        filename = part.get("filename") or ""
        if part.get("parts"):
            for child in part["parts"]:
                walk(child)
            return
        if filename:  # attachment
            return
        if mime == "text/plain":
            plain.append(_decode_part(part))
        elif mime == "text/html":
            htmls.append(_decode_part(part))

    walk(payload)
    if any(p.strip() for p in plain):
        return "\n".join(plain).replace("\r\n", "\n").strip()
    if htmls:
        return html_to_text("\n".join(htmls))
    return ""


def clean_snippet(snippet: str) -> str:
    text = html.unescape(snippet or "")
    text = text.replace("\u200c", "").replace("\u034f", "")
    return re.sub(r"\s+", " ", text).strip()


def parse_message(resource: dict, include_body: bool = True) -> Email:
    """Convert a Gmail ``users.messages`` resource into an :class:`Email`."""
    payload = resource.get("payload") or {}
    headers: dict[str, str] = {}
    for h in payload.get("headers", []) or []:
        name = (h.get("name") or "").lower()
        if name and name not in headers:
            headers[name] = h.get("value") or ""

    from_name, from_addr = parseaddr(headers.get("from", ""))
    to_addrs = [a for _, a in getaddresses([headers.get("to", "")]) if a]
    cc_addrs = [a for _, a in getaddresses([headers.get("cc", "")]) if a]

    internal_ms = int(resource.get("internalDate") or 0)
    received = datetime.fromtimestamp(internal_ms / 1000, tz=timezone.utc)

    body = extract_body_text(payload) if include_body and payload.get("mimeType") else None

    return Email(
        id=resource["id"],
        thread_id=resource.get("threadId", resource["id"]),
        subject=(headers.get("subject") or "").strip(),
        from_name=from_name.strip().strip('"'),
        from_addr=from_addr.strip(),
        to_addrs=to_addrs,
        cc_addrs=cc_addrs,
        received=received,
        labels=set(resource.get("labelIds") or []),
        snippet=clean_snippet(resource.get("snippet", "")),
        headers=headers,
        body_text=body or None,
    )
