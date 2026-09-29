from __future__ import annotations

import base64

import pytest

from hii_digest.config import ConfigError, load_config
from hii_digest.message import extract_body_text, html_to_text, parse_message


def b64(s: str, enc="utf-8") -> str:
    return base64.urlsafe_b64encode(s.encode(enc)).decode().rstrip("=")


def test_prefers_plain_text_and_skips_attachments():
    payload = {
        "mimeType": "multipart/mixed",
        "parts": [
            {
                "mimeType": "multipart/alternative",
                "parts": [
                    {"mimeType": "text/plain", "body": {"data": b64("plain body")}},
                    {"mimeType": "text/html", "body": {"data": b64("<p>html body</p>")}},
                ],
            },
            {"mimeType": "text/plain", "filename": "notes.txt", "body": {"attachmentId": "a1"}},
        ],
    }
    assert extract_body_text(payload) == "plain body"


def test_html_only_body_and_charset():
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [
            {
                "mimeType": "text/html",
                "headers": [{"name": "Content-Type", "value": 'text/html; charset="iso-8859-1"'}],
                "body": {
                    "data": b64(
                        "<html><head><style>p{}</style></head><body><p>Caf\u00e9 menu</p>"
                        "<div>Line&nbsp;two</div><script>x()</script></body></html>",
                        "latin-1",
                    )
                },
            },
        ],
    }
    assert extract_body_text(payload) == "Café menu\n\nLine two"


def test_unknown_charset_and_empty():
    payload = {
        "mimeType": "text/plain",
        "headers": [{"name": "Content-Type", "value": "text/plain; charset=bogus"}],
        "body": {"data": b64("hello")},
    }
    assert extract_body_text(payload) == "hello"
    assert extract_body_text(None) == ""
    assert extract_body_text({"mimeType": "text/plain", "body": {}}) == ""


def test_html_to_text_collapses_blank_lines():
    assert html_to_text("<p>a</p><p></p><p></p><p>b</p>") == "a\n\nb"


def test_parse_message_fields():
    resource = {
        "id": "m1",
        "threadId": "t1",
        "labelIds": ["INBOX", "UNREAD"],
        "snippet": "Hi &amp; welcome\u200c",
        "internalDate": "1790000000000",
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": '"Priya Sharma" <priya@acme.example>'},
                {"name": "To", "value": "a@x.com, B <b@x.com>"},
                {"name": "Cc", "value": "c@x.com"},
                {"name": "Subject", "value": "  Hello  "},
            ],
            "body": {"data": b64("Body")},
        },
    }
    e = parse_message(resource)
    assert (e.id, e.thread_id, e.subject) == ("m1", "t1", "Hello")
    assert (e.from_name, e.from_addr) == ("Priya Sharma", "priya@acme.example")
    assert e.to_addrs == ["a@x.com", "b@x.com"] and e.cc_addrs == ["c@x.com"]
    assert e.snippet == "Hi & welcome"
    assert e.body_text == "Body"
    assert e.received.year == 2026
    assert parse_message(resource, include_body=False).body_text is None


def test_config_defaults():
    cfg = load_config(env={})
    assert cfg.trigger_word == "hii"
    assert cfg.timezone == "Australia/Melbourne" and cfg.tz.key == "Australia/Melbourne"
    assert cfg.poll_interval == 20 and cfg.top_n == 10
    assert cfg.handled_label == "hii-digest/handled"
    assert not cfg.llm_enabled
    assert cfg.openai_model == "gpt-4.1-mini"


def test_config_overrides():
    cfg = load_config(
        env={
            "HII_TRIGGER_WORD": " Digest ",
            "HII_TIMEZONE": "Asia/Kolkata",
            "HII_POLL_INTERVAL": "5",
            "HII_TOP_N": "5",
            "OPENAI_API_KEY": "sk-x",
            "OPENAI_BASE_URL": "http://localhost:1234/v1/",
            "HII_LOG_LEVEL": "debug",
        }
    )
    assert cfg.trigger_word == "digest"
    assert cfg.tz.key == "Asia/Kolkata"
    assert (cfg.poll_interval, cfg.top_n) == (5.0, 5)
    assert cfg.llm_enabled and cfg.openai_base_url == "http://localhost:1234/v1"
    assert cfg.log_level == "DEBUG"


@pytest.mark.parametrize(
    "env",
    [
        {"HII_TIMEZONE": "Mars/Olympus"},
        {"HII_POLL_INTERVAL": "abc"},
        {"HII_POLL_INTERVAL": "0"},
        {"HII_TOP_N": "0"},
        {"HII_TRIGGER_WORD": "two words"},
    ],
)
def test_config_errors(env):
    with pytest.raises(ConfigError):
        load_config(env=env)


def test_config_reads_dotenv_file(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("HII_TRIGGER_WORD=yo\nHII_TOP_N=3\n")
    monkeypatch.delenv("HII_TRIGGER_WORD", raising=False)
    monkeypatch.setenv("HII_TOP_N", "7")  # real env wins over the file
    cfg = load_config(env_file=env_file)
    assert cfg.trigger_word == "yo"
    assert cfg.top_n == 7
    monkeypatch.delenv("HII_TRIGGER_WORD", raising=False)
