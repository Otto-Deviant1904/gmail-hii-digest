"""OAuth for an installed (desktop) app, with a cached ``token.json``."""

from __future__ import annotations

import contextlib
import logging
import os
from pathlib import Path

from hii_digest.config import Config

log = logging.getLogger(__name__)

# gmail.modify covers reading, labelling and sending; gmail.send is listed explicitly
# so the consent screen spells out that the app sends mail (it grants nothing extra).
SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
]


class AuthError(RuntimeError):
    """A user-actionable authentication problem."""


def _save_token(creds, token_file: Path) -> None:
    token_file.write_text(creds.to_json(), encoding="utf-8")
    with contextlib.suppress(OSError):  # best effort; limited effect on Windows
        os.chmod(token_file, 0o600)


def run_oauth_flow(cfg: Config):
    """Open the browser for Google consent and store the token. Returns credentials."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not cfg.credentials_file.exists():
        raise AuthError(
            f"{cfg.credentials_file} not found. Download your Desktop OAuth client JSON from "
            "Google Cloud Console (APIs & Services > Credentials) and save it as "
            f"'{cfg.credentials_file}' in this folder. See README 'Google Cloud setup'."
        )
    flow = InstalledAppFlow.from_client_secrets_file(str(cfg.credentials_file), SCOPES)
    creds = flow.run_local_server(
        port=0,
        open_browser=True,
        authorization_prompt_message=(
            "Opening your browser for Google sign-in. If it doesn't open, visit:\n{url}"
        ),
        success_message="hii-digest is authorised. You can close this tab and return to the terminal.",
    )
    _save_token(creds, cfg.token_file)
    log.info("Saved OAuth token to %s", cfg.token_file)
    return creds


def load_credentials(cfg: Config):
    """Load cached credentials, refreshing them if needed. Never opens a browser."""
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    if not cfg.token_file.exists():
        raise AuthError(f"No {cfg.token_file} yet. Run:  python -m hii_digest auth")
    try:
        # Load with the scopes stored in the file so we can detect an outdated token.
        creds = Credentials.from_authorized_user_file(str(cfg.token_file))
    except ValueError as exc:
        raise AuthError(
            f"{cfg.token_file} is unreadable ({exc}). Delete it and run:  python -m hii_digest auth"
        ) from exc
    granted = set(creds.scopes or [])
    if granted and not set(SCOPES) <= granted:
        raise AuthError(
            f"{cfg.token_file} is missing Gmail permissions (on the Google consent screen every "
            f"checkbox must be ticked). Delete {cfg.token_file} and run:  python -m hii_digest auth"
        )
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except RefreshError as exc:
                raise AuthError(
                    "Your Google token has expired or was revoked (tokens for apps in 'Testing' "
                    f"mode expire after 7 days). Delete {cfg.token_file} and run:  python -m hii_digest auth"
                ) from exc
            _save_token(creds, cfg.token_file)
        else:
            raise AuthError(f"{cfg.token_file} is invalid. Delete it and run:  python -m hii_digest auth")
    return creds


def build_service(creds):
    from googleapiclient.discovery import build

    return build("gmail", "v1", credentials=creds, cache_discovery=False)
