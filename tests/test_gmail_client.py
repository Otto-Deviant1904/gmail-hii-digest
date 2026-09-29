from __future__ import annotations

import base64
from email.message import EmailMessage

from hii_digest.gmail import GmailClient
from tests.conftest import OWNER, at, incoming


def test_owner_email_is_cached(fake, client):
    assert client.owner_email() == OWNER
    assert client.owner_email() == OWNER
    assert fake.calls.count("getProfile") == 1


def test_ensure_label_creates_parent_then_child_once(fake, client):
    lid = client.ensure_label("hii-digest/handled")
    assert fake.labels[lid]["name"] == "hii-digest/handled"
    assert fake.label_id("hii-digest")
    assert client.ensure_label("hii-digest/handled") == lid
    assert GmailClient(fake).ensure_label("hii-digest/handled") == lid  # found, not re-created
    creates = [c for c in fake.calls if c.startswith("labels.create")]
    assert creates == ["labels.create:hii-digest", "labels.create:hii-digest/handled"]


def test_search_paginates_and_respects_limit(fake, client):
    for i in range(130):
        fake.add(incoming(f"m{i:03d}", when=at(8, 0)))
    assert len(client.search("", 250)) == 130
    assert len(client.search("", 120)) == 120
    assert len([c for c in fake.calls if c.startswith("messages.list")]) == 4


def test_get_message_metadata_has_no_body(fake, client):
    fake.add(incoming("m1", body="secret body"))
    assert client.get_message("m1", full=False).body_text is None
    assert client.get_message("m1", full=True).body_text == "secret body"


def test_send_encodes_raw_and_thread(fake, client):
    msg = EmailMessage()
    msg["To"] = OWNER
    msg["From"] = OWNER
    msg["Subject"] = "x"
    msg.set_content("hello")
    resp = client.send(msg, thread_id="T1")
    assert resp["threadId"] == "T1"
    assert b"hello" in fake.sent[0]["raw"]
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    assert raw


def test_send_without_thread(fake, client):
    msg = EmailMessage()
    msg["To"] = "other@example.com"
    msg.set_content("hi")
    resp = client.send(msg)
    assert resp["threadId"] == resp["id"]
    assert resp["labelIds"] == ["SENT"]


def test_find_label_never_creates(fake, client):
    assert client.find_label("hii-digest/handled") is None
    assert not [c for c in fake.calls if c.startswith("labels.create")]
    lid = fake.add_label("hii-digest/handled")
    assert client.find_label("hii-digest/handled") == lid
