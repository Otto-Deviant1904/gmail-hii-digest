from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from hii_digest import auth, cli
from hii_digest.auth import SCOPES, AuthError, load_credentials, run_oauth_flow
from hii_digest.config import load_config
from hii_digest.fake_gmail import FakeGmail
from hii_digest.gmail import GmailClient
from tests.conftest import NOW, OWNER, incoming, trigger_msg


@pytest.fixture(autouse=True)
def isolated_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for key in (
        "OPENAI_API_KEY",
        "HII_TRIGGER_WORD",
        "HII_TIMEZONE",
        "HII_TOKEN_FILE",
        "HII_CREDENTIALS_FILE",
    ):
        monkeypatch.delenv(key, raising=False)
    return tmp_path


def test_preview_demo_writes_files(tmp_path, capsys):
    rc = cli.main(["preview", "--demo", "--out", "out/d.html", "--text-out", "out/d.txt"])
    assert rc == 0
    html = (tmp_path / "out/d.html").read_text(encoding="utf-8")
    text = (tmp_path / "out/d.txt").read_text(encoding="utf-8")
    assert "Your day in 10 emails" in html
    assert text.startswith("YOUR DAY IN 10 EMAILS")
    out = capsys.readouterr().out
    assert "Demo mode" in out and "nothing was sent" in out
    assert "Interview invitation" in out
    # the trigger and yesterday's email never appear
    assert "Urgent: budget sign-off" not in text


def test_preview_demo_default_path(tmp_path):
    assert cli.main(["preview", "--demo", "--no-llm"]) == 0
    files = list((tmp_path / "previews").glob("demo-digest-*.html"))
    assert len(files) == 1


def test_config_error_exit_code(monkeypatch, capsys):
    monkeypatch.setenv("HII_TIMEZONE", "Nowhere/Land")
    assert cli.main(["preview", "--demo"]) == 2
    assert "Configuration error" in capsys.readouterr().out


def test_bad_interval():
    assert cli.main(["run", "--interval", "0.1"]) == 2


def test_auth_without_credentials_file(capsys):
    assert cli.main(["auth"]) == 1
    assert "credentials.json not found" in capsys.readouterr().out


def test_run_without_token(capsys):
    assert cli.main(["run"]) == 1
    assert "python -m hii_digest auth" in capsys.readouterr().out


def _fake_client(monkeypatch):
    fake = FakeGmail(
        OWNER, [incoming("m1"), trigger_msg("trig1", subject="hii")], now=NOW.astimezone(timezone.utc)
    )
    monkeypatch.setattr(cli, "_gmail_client", lambda cfg: GmailClient(fake))
    return fake


def test_once_with_fake_gmail(monkeypatch, capsys):
    fake = _fake_client(monkeypatch)
    monkeypatch.setattr("hii_digest.app.now_utc", lambda: NOW.astimezone(timezone.utc))
    assert cli.main(["once"]) == 0
    assert len(fake.sent) == 1
    assert "1 digest(s) sent" in capsys.readouterr().out


def test_run_ctrl_c_exits_cleanly(monkeypatch, capsys):
    _fake_client(monkeypatch)

    def interrupted(client, cfg, only_new=False):
        assert cfg.poll_interval == 5
        assert only_new is True
        raise KeyboardInterrupt

    monkeypatch.setattr("hii_digest.app.run_loop", interrupted)
    assert cli.main(["run", "--interval", "5", "--only-new"]) == 0
    assert "Stopped. Bye!" in capsys.readouterr().out


def test_preview_live_uses_labels(monkeypatch, tmp_path):
    fake = _fake_client(monkeypatch)
    assert cli.main(["preview", "--out", "p.html"]) == 0
    assert fake.sent == []  # preview never sends
    assert not [c for c in fake.calls if c.startswith(("labels.create", "messages.modify"))]
    assert (tmp_path / "p.html").exists()


def test_refresh_error_is_reported(monkeypatch, capsys):
    from google.auth.exceptions import RefreshError

    def boom(cfg):
        raise RefreshError("invalid_grant")

    monkeypatch.setattr(cli, "_gmail_client", boom)
    assert cli.main(["once"]) == 1
    assert "Delete token.json" in capsys.readouterr().out


def test_http_error_is_reported(monkeypatch, capsys):
    class HttpError(Exception):
        pass

    def boom(cfg):
        raise HttpError("403 insufficient permissions")

    monkeypatch.setattr(cli, "_gmail_client", boom)
    assert cli.main(["once"]) == 1
    assert "Gmail API error" in capsys.readouterr().out


def test_version(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--version"])
    assert "hii-digest" in capsys.readouterr().out


# ---------------------------------------------------------------- auth ----------
def _write_token(path, scopes=SCOPES, expiry=None):
    expiry = expiry or (datetime.now(timezone.utc) + timedelta(hours=1))
    path.write_text(
        json.dumps(
            {
                "token": "access",
                "refresh_token": "refresh",
                "client_id": "cid",
                "client_secret": "secret",
                "token_uri": "https://oauth2.googleapis.com/token",
                "scopes": scopes,
                "expiry": expiry.strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        )
    )


def test_load_credentials_valid(tmp_path):
    _write_token(tmp_path / "token.json")
    creds = load_credentials(load_config(env={}))
    assert creds.valid


def test_load_credentials_scope_mismatch(tmp_path):
    _write_token(tmp_path / "token.json", scopes=["https://www.googleapis.com/auth/gmail.readonly"])
    with pytest.raises(AuthError, match="missing Gmail permissions"):
        load_credentials(load_config(env={}))


def test_load_credentials_corrupt(tmp_path):
    (tmp_path / "token.json").write_text("{}")
    with pytest.raises(AuthError, match="unreadable"):
        load_credentials(load_config(env={}))


def test_expired_token_refresh_failure(tmp_path, monkeypatch):
    from google.auth.exceptions import RefreshError
    from google.oauth2.credentials import Credentials

    _write_token(tmp_path / "token.json", expiry=datetime.now(timezone.utc) - timedelta(hours=2))

    def fail(self, request):
        raise RefreshError("invalid_grant: Token has been expired or revoked.")

    monkeypatch.setattr(Credentials, "refresh", fail)
    with pytest.raises(AuthError, match="expire after 7 days"):
        load_credentials(load_config(env={}))


def test_expired_token_refresh_success_saves(tmp_path, monkeypatch):
    from google.oauth2.credentials import Credentials

    _write_token(tmp_path / "token.json", expiry=datetime.now(timezone.utc) - timedelta(hours=2))

    def ok(self, request):
        self.token = "new-access"
        self.expiry = (datetime.now(timezone.utc) + timedelta(hours=1)).replace(tzinfo=None)

    monkeypatch.setattr(Credentials, "refresh", ok)
    creds = load_credentials(load_config(env={}))
    assert creds.token == "new-access"
    assert json.loads((tmp_path / "token.json").read_text())["token"] == "new-access"


def test_run_oauth_flow_saves_token(tmp_path, monkeypatch):
    (tmp_path / "credentials.json").write_text("{}")

    class Creds:
        def to_json(self):
            return '{"token": "t"}'

    class Flow:
        @classmethod
        def from_client_secrets_file(cls, path, scopes):
            assert scopes == SCOPES
            return cls()

        def run_local_server(self, **kwargs):
            assert kwargs["port"] == 0
            return Creds()

    import google_auth_oauthlib.flow

    monkeypatch.setattr(google_auth_oauthlib.flow, "InstalledAppFlow", Flow)
    run_oauth_flow(load_config(env={}))
    assert (tmp_path / "token.json").read_text() == '{"token": "t"}'


def test_cmd_auth_reports_account(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(auth, "run_oauth_flow", lambda cfg: object())
    monkeypatch.setattr(auth, "build_service", lambda creds: FakeGmail(OWNER))
    assert cli.main(["auth"]) == 0
    assert f"Authorised as {OWNER}" in capsys.readouterr().out
