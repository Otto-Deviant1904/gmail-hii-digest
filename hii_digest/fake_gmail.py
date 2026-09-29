"""An in-memory stand-in for the Gmail API ``service`` object.

It implements just the calls hii-digest makes (same method names, arguments and
response shapes as google-api-python-client), so the real code paths can run in
tests and in ``preview --demo`` without network access or credentials.
"""

from __future__ import annotations

import base64
import copy
import itertools
import re
from datetime import datetime, timedelta, timezone
from email import message_from_bytes, policy
from email.utils import getaddresses, parseaddr

from hii_digest.message import normalize_address


class FakeHttpError(Exception):
    """Mimics googleapiclient.errors.HttpError closely enough for our purposes."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"<HttpError {status}: {message}>")
        self.status = status


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def make_message(
    msg_id: str,
    *,
    sender: str,
    to: str,
    subject: str,
    body: str,
    received: datetime,
    labels: list[str] | None = None,
    thread_id: str | None = None,
    cc: str = "",
    headers: dict[str, str] | None = None,
    html_body: str | None = None,
    snippet: str | None = None,
) -> dict:
    """Build a Gmail ``users.messages`` resource (format=full)."""
    hdrs = {
        "From": sender,
        "To": to,
        "Subject": subject,
        "Date": received.strftime("%a, %d %b %Y %H:%M:%S %z"),
        "Message-ID": f"<{msg_id}@mail.example.com>",
    }
    if cc:
        hdrs["Cc"] = cc
    hdrs.update(headers or {})
    parts = [{"mimeType": "text/plain", "headers": [], "body": {"data": _b64(body)}}]
    if html_body is not None:
        parts.append({"mimeType": "text/html", "headers": [], "body": {"data": _b64(html_body)}})
    return {
        "id": msg_id,
        "threadId": thread_id or msg_id,
        "labelIds": list(labels or ["INBOX"]),
        "snippet": snippet if snippet is not None else " ".join(body.split())[:200],
        "internalDate": str(int(received.timestamp() * 1000)),
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": [{"name": k, "value": v} for k, v in hdrs.items()],
            "parts": parts,
        },
    }


def _payload_from_mime(part) -> dict:
    headers = [{"name": k, "value": str(v)} for k, v in part.items()]
    if part.is_multipart():
        return {
            "mimeType": part.get_content_type(),
            "headers": headers,
            "parts": [_payload_from_mime(p) for p in part.iter_parts()],
        }
    data = part.get_payload(decode=True) or b""
    return {
        "mimeType": part.get_content_type(),
        "headers": headers,
        "body": {"data": base64.urlsafe_b64encode(data).decode("ascii")},
    }


class _Req:
    def __init__(self, fn):
        self._fn = fn

    def execute(self, num_retries: int = 0):
        return self._fn()


class FakeGmail:
    def __init__(self, owner: str, messages: list[dict] | None = None, now: datetime | None = None) -> None:
        self.owner = owner
        self.messages: dict[str, dict] = {}
        self.labels: dict[str, dict] = {}
        self.sent: list[dict] = []
        self.calls: list[str] = []
        self.now = now or datetime.now(timezone.utc)
        self.fail_send: Exception | None = None
        self._ids = itertools.count(1)
        for m in messages or []:
            self.add(m)

    # -- helpers for tests -------------------------------------------------------
    def add(self, resource: dict) -> None:
        self.messages[resource["id"]] = copy.deepcopy(resource)

    def label_id(self, name: str) -> str | None:
        for lid, lbl in self.labels.items():
            if lbl["name"] == name:
                return lid
        return None

    def add_label(self, name: str) -> str:
        lid = f"Label_{next(self._ids)}"
        self.labels[lid] = {"id": lid, "name": name, "type": "user"}
        return lid

    # -- service surface: ``build("gmail", "v1")`` equivalent -------------------
    def users(self):
        return _Users(self)


class _Messages:
    def __init__(self, fake: FakeGmail) -> None:
        self.f = fake

    def list(self, userId, q="", maxResults=100, pageToken=None, **_):
        self.f.calls.append(f"messages.list:{q}")

        def run():
            matches = [m for m in self.f.messages.values() if _matches(self.f, m, q)]
            matches.sort(key=lambda m: -int(m["internalDate"]))
            start = int(pageToken or 0)
            page = matches[start : start + maxResults]
            resp = {"messages": [{"id": m["id"], "threadId": m["threadId"]} for m in page]}
            if start + maxResults < len(matches):
                resp["nextPageToken"] = str(start + maxResults)
            resp["resultSizeEstimate"] = len(matches)
            return resp

        return _Req(run)

    def get(self, userId, id, format="full", metadataHeaders=None, **_):
        self.f.calls.append(f"messages.get:{id}:{format}")

        def run():
            if id not in self.f.messages:
                raise FakeHttpError(404, f"message {id} not found")
            return _shape(self.f.messages[id], format, metadataHeaders)

        return _Req(run)

    def modify(self, userId, id, body):
        self.f.calls.append(f"messages.modify:{id}")

        def run():
            m = self.f.messages[id]
            labels = [lbl for lbl in m["labelIds"] if lbl not in body.get("removeLabelIds", [])]
            for lbl in body.get("addLabelIds", []):
                if lbl not in labels:
                    labels.append(lbl)
            m["labelIds"] = labels
            return {"id": id, "threadId": m["threadId"], "labelIds": labels}

        return _Req(run)

    def send(self, userId, body):
        self.f.calls.append("messages.send")

        def run():
            if self.f.fail_send:
                raise self.f.fail_send
            raw = base64.urlsafe_b64decode(body["raw"] + "=" * (-len(body["raw"]) % 4))
            mime = message_from_bytes(raw, policy=policy.default)
            msg_id = f"sent{next(self.f._ids)}"
            self.f.now = self.f.now + timedelta(seconds=1)
            recipients = [a for _, a in getaddresses([str(mime.get("To", ""))])]
            labels = ["SENT"]
            if any(normalize_address(a) == normalize_address(self.f.owner) for a in recipients):
                labels += ["INBOX", "UNREAD"]
            resource = {
                "id": msg_id,
                "threadId": body.get("threadId") or msg_id,
                "labelIds": labels,
                "snippet": "",
                "internalDate": str(int(self.f.now.timestamp() * 1000)),
                "payload": _payload_from_mime(mime),
                "raw": raw,
            }
            self.f.messages[msg_id] = resource
            self.f.sent.append(resource)
            return {"id": msg_id, "threadId": resource["threadId"], "labelIds": labels}

        return _Req(run)


class _Threads:
    def __init__(self, fake: FakeGmail) -> None:
        self.f = fake

    def get(self, userId, id, format="full", metadataHeaders=None, **_):
        self.f.calls.append(f"threads.get:{id}")

        def run():
            msgs = [m for m in self.f.messages.values() if m["threadId"] == id]
            if not msgs:
                raise FakeHttpError(404, f"thread {id} not found")
            msgs.sort(key=lambda m: int(m["internalDate"]))
            return {"id": id, "messages": [_shape(m, format, metadataHeaders) for m in msgs]}

        return _Req(run)


class _Labels:
    def __init__(self, fake: FakeGmail) -> None:
        self.f = fake

    def list(self, userId):
        self.f.calls.append("labels.list")
        system = [{"id": x, "name": x, "type": "system"} for x in ("INBOX", "SENT", "IMPORTANT", "STARRED")]
        return _Req(lambda: {"labels": system + list(self.f.labels.values())})

    def create(self, userId, body):
        self.f.calls.append(f"labels.create:{body['name']}")

        def run():
            if self.f.label_id(body["name"]):
                raise FakeHttpError(409, "Label name exists or conflicts")
            lid = self.f.add_label(body["name"])
            return self.f.labels[lid]

        return _Req(run)


class _Users:
    def __init__(self, fake: FakeGmail) -> None:
        self.f = fake

    def getProfile(self, userId):  # noqa: N802 - Gmail API name
        self.f.calls.append("getProfile")
        return _Req(lambda: {"emailAddress": self.f.owner, "messagesTotal": len(self.f.messages)})

    def messages(self):
        return _Messages(self.f)

    def threads(self):
        return _Threads(self.f)

    def labels(self):
        return _Labels(self.f)


def _shape(resource: dict, fmt: str, metadata_headers) -> dict:
    out = copy.deepcopy(resource)
    out.pop("raw", None)
    if fmt == "metadata":
        wanted = {h.lower() for h in (metadata_headers or [])}
        payload = out.get("payload", {})
        headers = payload.get("headers", [])
        if wanted:
            headers = [h for h in headers if h["name"].lower() in wanted]
        out["payload"] = {"mimeType": payload.get("mimeType"), "headers": headers}
    elif fmt == "minimal":
        out.pop("payload", None)
    return out


def _header(resource: dict, name: str) -> str:
    for h in resource.get("payload", {}).get("headers", []):
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def _matches(fake: FakeGmail, m: dict, query: str) -> bool:
    """Evaluate the small subset of Gmail search syntax that hii-digest uses."""
    labels = set(m.get("labelIds", []))
    if labels & {"SPAM", "TRASH"}:
        return False
    me = normalize_address(fake.owner)
    from_me = normalize_address(parseaddr(_header(m, "From"))[1]) == me
    recips = [a for _, a in getaddresses([_header(m, "To"), _header(m, "Cc")])]
    to_me = any(normalize_address(a) == me for a in recips)
    received = int(m["internalDate"]) / 1000
    for token in query.split():
        neg = token.startswith("-")
        term = token[1:] if neg else token
        if term == "from:me":
            ok = from_me
        elif term == "to:me":
            ok = to_me
        elif term.startswith("after:"):
            ok = received > int(term[6:])
        elif match := re.fullmatch(r"newer_than:(\d+)d", term):
            ok = received >= (fake.now - timedelta(days=int(match.group(1)))).timestamp()
        elif term == "in:chats":
            ok = "CHAT" in labels
        else:
            continue  # unsupported terms are ignored
        if ok == neg:
            return False
    return True
