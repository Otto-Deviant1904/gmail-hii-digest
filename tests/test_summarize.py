from __future__ import annotations

import json
from dataclasses import replace

import pytest

from hii_digest.config import load_config
from hii_digest.digest import DayStats, build_digest
from hii_digest.message import parse_message
from hii_digest.scoring import score_email
from hii_digest.summarize import (
    LLMSummarizer,
    OfflineSummarizer,
    first_sentences,
    make_summarizer,
    offline_overall,
)
from hii_digest.timewindow import today_window
from tests.conftest import MEL, NOW, OWNER, incoming

LLM_CFG = load_config(
    env={
        "OPENAI_API_KEY": "sk-test",
        "OPENAI_MODEL": "test-model",
        "OPENAI_BASE_URL": "https://llm.example/v1/",
    }
)
WINDOW = today_window(MEL, NOW)
STATS = DayStats(total=2, by_category={"primary": 2}, top_senders=[("Priya", 2)], important=1, unread=1)


def _top(n=2):
    return [
        score_email(parse_message(incoming(f"m{i}", subject=f"S{i}", body=f"Body {i}.")), OWNER)
        for i in range(n)
    ]


class FakeResponse:
    def __init__(self, status=200, payload=None, text=None):
        self.status_code = status
        self._payload = payload
        self._text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def chat(content: str) -> FakeResponse:
    return FakeResponse(payload={"choices": [{"message": {"content": content}}]})


class FakeSession:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def post(self, url, headers=None, json=None, timeout=None):
        self.requests.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


GOOD = json.dumps(
    {
        "overall": "Busy day. Reply to Priya first.",
        "items": [
            {"id": "m0", "summary": "Priya needs slides by 1pm."},
            {"id": "m1", "summary": "Second   email\nsummary."},
        ],
    }
)


def test_first_sentences():
    assert first_sentences("") == ""
    assert (
        first_sentences("Short one. Second sentence here! Third?", min_chars=20)
        == "Short one. Second sentence here!"
    )
    assert "http" not in first_sentences(
        "See https://x.example/abc for details about the thing we discussed."
    )
    long = "word " * 100
    out = first_sentences(long)
    assert len(out) <= 220 and out.endswith("…")


def test_offline_summarizer():
    s = OfflineSummarizer().summarize(_top(), STATS, WINDOW)
    assert s.source == "offline"
    assert s.lines == {"m0": "Body 0.", "m1": "Body 1."}
    assert "You've received 2 emails today (2 primary)" in s.overall
    assert "1 marked important by Gmail and 1 still unread" in s.overall
    assert 'Top priority: "S0"' in s.overall


def test_offline_no_preview_text():
    e = parse_message(incoming("x", body=""))
    e.snippet = ""
    assert OfflineSummarizer().line_for(score_email(e, OWNER)) == "(no preview text)"


def test_offline_overall_empty_day():
    empty = DayStats(total=0, by_category={}, top_senders=[], important=0, unread=0)
    assert "No emails" in offline_overall(empty, [])


def test_llm_success_uses_model_output():
    session = FakeSession(chat(GOOD))
    s = LLMSummarizer(LLM_CFG, session=session).summarize(_top(), STATS, WINDOW)
    assert s.source == "llm"
    assert s.overall == "Busy day. Reply to Priya first."
    assert s.lines == {"m0": "Priya needs slides by 1pm.", "m1": "Second email summary."}
    req = session.requests[0]
    assert req["url"] == "https://llm.example/v1/chat/completions"
    assert req["headers"]["Authorization"] == "Bearer sk-test"
    assert req["json"]["model"] == "test-model"
    assert req["json"]["response_format"] == {"type": "json_object"}
    assert req["timeout"] == LLM_CFG.llm_timeout
    user_prompt = req["json"]["messages"][1]["content"]
    assert '"id": "m0"' in user_prompt and "Body 0." in user_prompt


def test_llm_code_fenced_json_and_partial_items():
    content = (
        "```json\n"
        + json.dumps(
            {
                "overall": "",
                "items": [{"id": "m1", "summary": "Only one."}, {"id": "zzz", "summary": "unknown"}],
            }
        )
        + "\n```"
    )
    s = LLMSummarizer(LLM_CFG, session=FakeSession(chat(content))).summarize(_top(), STATS, WINDOW)
    assert s.source == "llm"
    assert s.lines == {"m0": "Body 0.", "m1": "Only one."}  # m0 falls back to offline text
    assert s.overall.startswith("You've received")  # empty overall -> offline overall


@pytest.mark.parametrize("status", [400, 404, 422, 501])
def test_llm_retries_without_json_mode_on_400(status):
    """Servers signal an unsupported response_format with more than just 400."""
    session = FakeSession(FakeResponse(status=status), chat(GOOD))
    s = LLMSummarizer(LLM_CFG, session=session).summarize(_top(), STATS, WINDOW)
    assert s.source == "llm"
    assert "response_format" not in session.requests[1]["json"]


@pytest.mark.parametrize(
    "response",
    [
        ConnectionError("network down"),
        TimeoutError("slow"),
        FakeResponse(status=401),
        FakeResponse(status=500),
        FakeResponse(payload={"unexpected": True}),
        FakeResponse(payload=None),
        chat("Sorry, I can't help with that."),
        chat("{not json}"),
        chat("[1, 2]"),
        chat(json.dumps({"overall": "", "items": []})),
    ],
)
def test_llm_errors_fall_back_to_offline(response, caplog):
    s = LLMSummarizer(LLM_CFG, session=FakeSession(response)).summarize(_top(), STATS, WINDOW)
    assert s.source == "offline"
    assert s.lines == {"m0": "Body 0.", "m1": "Body 1."}


def test_llm_not_called_for_empty_digest():
    session = FakeSession()
    s = LLMSummarizer(LLM_CFG, session=session).summarize([], STATS, WINDOW)
    assert s.source == "offline" and session.requests == []


def test_make_summarizer_selects_by_key(cfg):
    assert isinstance(make_summarizer(cfg), OfflineSummarizer)
    assert isinstance(make_summarizer(LLM_CFG), LLMSummarizer)


def test_build_digest_with_llm_fetches_full_bodies(fake, client, cfg):
    fake.add(incoming("m0", subject="S0", body="The full body text of the email."))
    session = FakeSession(
        chat(json.dumps({"overall": "LLM overall", "items": [{"id": "m0", "summary": "LLM line"}]}))
    )
    llm_cfg = replace(cfg, openai_api_key="sk-test")
    digest = build_digest(client, llm_cfg, now=NOW, summarizer=LLMSummarizer(llm_cfg, session=session))
    assert digest.summary_source == "llm"
    assert digest.items[0].summary == "LLM line"
    assert "The full body text" in session.requests[0]["json"]["messages"][1]["content"]
    assert "messages.get:m0:full" in fake.calls


def test_build_digest_llm_body_fetch_failure_is_tolerated(fake, client, cfg, monkeypatch):
    fake.add(incoming("m0"))
    monkeypatch.setattr(client, "get_message", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    llm_cfg = replace(cfg, openai_api_key="sk-test")
    session = FakeSession(ConnectionError("down"))
    digest = build_digest(client, llm_cfg, now=NOW, summarizer=LLMSummarizer(llm_cfg, session=session))
    assert digest.summary_source == "offline"
    assert len(digest.items) == 1
