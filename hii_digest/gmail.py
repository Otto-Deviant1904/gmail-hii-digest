"""Thin wrapper around the Gmail API service object.

Everything that talks to Gmail goes through :class:`GmailClient`, which keeps the
rest of the code easy to test with a fake service (see ``hii_digest.fake_gmail``).
"""

from __future__ import annotations

import base64
import logging
from email.message import EmailMessage

from hii_digest.message import METADATA_HEADERS, Email, parse_message

log = logging.getLogger(__name__)

# googleapiclient retries 429/5xx responses with exponential backoff this many times.
NUM_RETRIES = 3


class GmailClient:
    def __init__(self, service, user_id: str = "me") -> None:
        self._svc = service
        self._user = user_id
        self._owner: str | None = None
        self._label_cache: dict[str, str] = {}

    # -- identity ---------------------------------------------------------------
    def owner_email(self) -> str:
        """Email address of the authenticated account (auto-detected)."""
        if self._owner is None:
            profile = self._svc.users().getProfile(userId=self._user).execute(num_retries=NUM_RETRIES)
            self._owner = profile["emailAddress"]
        return self._owner

    # -- labels -----------------------------------------------------------------
    def find_label(self, name: str) -> str | None:
        """Id of the user label ``name`` or None. Never creates anything."""
        if name in self._label_cache:
            return self._label_cache[name]
        resp = self._svc.users().labels().list(userId=self._user).execute(num_retries=NUM_RETRIES)
        for lbl in resp.get("labels", []):
            if lbl["name"] == name:
                return lbl["id"]
        return None

    def ensure_label(self, name: str) -> str:
        """Return the id of the user label ``name``, creating it (and parents) if missing."""
        if name in self._label_cache:
            return self._label_cache[name]
        resp = self._svc.users().labels().list(userId=self._user).execute(num_retries=NUM_RETRIES)
        existing = {lbl["name"]: lbl["id"] for lbl in resp.get("labels", [])}
        # Create parent labels first so Gmail shows them nested (e.g. "hii-digest").
        parts = name.split("/")
        for i in range(1, len(parts) + 1):
            path = "/".join(parts[:i])
            if path not in existing:
                body = {
                    "name": path,
                    "labelListVisibility": "labelShow",
                    "messageListVisibility": "show",
                }
                created = (
                    self._svc.users()
                    .labels()
                    .create(userId=self._user, body=body)
                    .execute(num_retries=NUM_RETRIES)
                )
                existing[path] = created["id"]
                log.info("Created Gmail label %r", path)
        self._label_cache[name] = existing[name]
        return existing[name]

    def add_labels(self, message_id: str, label_ids: list[str]) -> None:
        self._svc.users().messages().modify(
            userId=self._user, id=message_id, body={"addLabelIds": label_ids}
        ).execute(num_retries=NUM_RETRIES)

    # -- reading ----------------------------------------------------------------
    def search(self, query: str, limit: int) -> list[dict]:
        """Return up to ``limit`` ``{"id", "threadId"}`` refs matching a Gmail query."""
        refs: list[dict] = []
        page_token = None
        while len(refs) < limit:
            kwargs = {"userId": self._user, "q": query, "maxResults": min(100, limit - len(refs))}
            if page_token:
                kwargs["pageToken"] = page_token
            resp = self._svc.users().messages().list(**kwargs).execute(num_retries=NUM_RETRIES)
            refs.extend(resp.get("messages", []) or [])
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return refs[:limit]

    def get_message(self, message_id: str, full: bool = True) -> Email:
        kwargs = {"userId": self._user, "id": message_id}
        if full:
            kwargs["format"] = "full"
        else:
            kwargs["format"] = "metadata"
            kwargs["metadataHeaders"] = METADATA_HEADERS
        resource = self._svc.users().messages().get(**kwargs).execute(num_retries=NUM_RETRIES)
        return parse_message(resource, include_body=full)

    def get_thread(self, thread_id: str) -> list[Email]:
        """All messages in a thread (metadata only), oldest first."""
        resource = (
            self._svc.users()
            .threads()
            .get(userId=self._user, id=thread_id, format="metadata", metadataHeaders=METADATA_HEADERS)
            .execute(num_retries=NUM_RETRIES)
        )
        return [parse_message(m, include_body=False) for m in resource.get("messages", [])]

    # -- sending ----------------------------------------------------------------
    def send(self, message: EmailMessage, thread_id: str | None = None) -> dict:
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
        body: dict = {"raw": raw}
        if thread_id:
            body["threadId"] = thread_id
        return (
            # No automatic retries: a retried send could duplicate the digest. A failed
            # send is retried safely on the next poll (see app.process_trigger).
            self._svc.users().messages().send(userId=self._user, body=body).execute(num_retries=0)
        )
