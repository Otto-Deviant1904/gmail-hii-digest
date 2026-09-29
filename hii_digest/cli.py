"""Command-line interface: ``python -m hii_digest {auth,run,once,preview}``."""

from __future__ import annotations

import argparse
import contextlib
import logging
import sys
from pathlib import Path

from hii_digest import __version__
from hii_digest.config import Config, ConfigError, load_config

log = logging.getLogger("hii_digest")


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s  %(levelname)-7s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
        force=True,
    )
    # Keep third-party noise out of the demo log.
    for noisy in ("googleapiclient", "google_auth_oauthlib", "urllib3", "google.auth"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="hii-digest",
        description="Email yourself 'hii' and get a reply with today's 10 most important emails.",
    )
    p.add_argument("--version", action="version", version=f"hii-digest {__version__}")
    p.add_argument("--env-file", help="path to a .env file (default: ./.env)")
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = p.add_subparsers(dest="command", required=True, metavar="COMMAND")

    sub.add_parser("auth", help="sign in with Google once and cache token.json")

    run = sub.add_parser("run", help="watch Gmail and reply to 'hii' emails (Ctrl+C to stop)")
    run.add_argument("--interval", type=float, help="seconds between checks (default 20)")
    run.add_argument(
        "--only-new",
        action="store_true",
        help="ignore 'hii' emails that arrived before this run started",
    )

    sub.add_parser("once", help="check once, reply to any pending 'hii', then exit")

    prev = sub.add_parser("preview", help="build today's digest without sending anything")
    prev.add_argument("--demo", action="store_true", help="use built-in fake emails (no Gmail needed)")
    prev.add_argument("--out", type=Path, help="HTML output path (default previews/digest-*.html)")
    prev.add_argument("--text-out", type=Path, help="also write the plain-text version here")
    prev.add_argument("--no-llm", action="store_true", help="skip the LLM even if OPENAI_API_KEY is set")
    return p


def _gmail_client(cfg: Config):
    from hii_digest.auth import build_service, load_credentials
    from hii_digest.gmail import GmailClient

    return GmailClient(build_service(load_credentials(cfg)))


def cmd_auth(cfg: Config) -> int:
    from hii_digest.auth import build_service, run_oauth_flow
    from hii_digest.gmail import GmailClient

    creds = run_oauth_flow(cfg)
    owner = GmailClient(build_service(creds)).owner_email()
    log.info("Authorised as %s. Next: python -m hii_digest run", owner)
    return 0


def cmd_run(cfg: Config, only_new: bool = False) -> int:
    from hii_digest.app import run_loop

    client = _gmail_client(cfg)
    with contextlib.suppress(KeyboardInterrupt):  # Ctrl+C = clean exit
        run_loop(client, cfg, only_new=only_new)
    log.info("Stopped. Bye!")
    return 0


def cmd_once(cfg: Config) -> int:
    from hii_digest.app import process_once

    sent = process_once(_gmail_client(cfg), cfg)
    log.info("Done: %d digest(s) sent", sent)
    return 0


def cmd_preview(cfg: Config, args) -> int:
    from dataclasses import replace
    from datetime import datetime

    from hii_digest.digest import build_digest
    from hii_digest.render import render_html, render_text

    if args.no_llm:
        cfg = replace(cfg, openai_api_key=None)

    handled_id = digest_id = None
    if args.demo:
        from hii_digest.demo_data import build_demo_mailbox, demo_now
        from hii_digest.gmail import GmailClient

        fake = build_demo_mailbox(cfg.tz)
        client = GmailClient(fake)
        now = demo_now(cfg.tz)
        log.info(
            "Demo mode: using %d fake emails (nothing is read from or sent to Gmail)", len(fake.messages)
        )
    else:
        from hii_digest.app import Labels

        client = _gmail_client(cfg)
        now = None
        handled_id, digest_id = Labels.lookup(client, cfg)  # read-only: preview changes nothing

    digest = build_digest(client, cfg, now=now, digest_label_id=digest_id, handled_label_id=handled_id)
    html, text = render_html(digest), render_text(digest)

    stamp = datetime.now(cfg.tz).strftime("%Y%m%d-%H%M%S")
    out = args.out or Path("previews") / f"{'demo-' if args.demo else ''}digest-{stamp}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    if args.text_out:
        args.text_out.parent.mkdir(parents=True, exist_ok=True)
        args.text_out.write_text(text, encoding="utf-8")

    print()
    print(text)
    log.info("Wrote HTML preview to %s (nothing was sent)", out.resolve())
    if args.text_out:
        log.info("Wrote text preview to %s", args.text_out.resolve())
    return 0


def _safe_console() -> None:
    """Never crash on emoji/odd characters on legacy Windows consoles or redirected output."""
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, ValueError):
            stream.reconfigure(errors="replace")


def main(argv: list[str] | None = None) -> int:
    _safe_console()
    args = build_parser().parse_args(argv)
    try:
        cfg = load_config(env_file=args.env_file)
    except ConfigError as exc:
        setup_logging("INFO")
        log.error("Configuration error: %s", exc)
        return 2
    if args.command == "run" and args.interval:
        from dataclasses import replace

        if args.interval < 1:
            print("--interval must be at least 1 second", file=sys.stderr)
            return 2
        cfg = replace(cfg, poll_interval=args.interval)
    setup_logging("DEBUG" if args.verbose else cfg.log_level)

    from hii_digest.auth import AuthError

    try:
        if args.command == "auth":
            return cmd_auth(cfg)
        if args.command == "run":
            return cmd_run(cfg, only_new=args.only_new)
        if args.command == "once":
            return cmd_once(cfg)
        return cmd_preview(cfg, args)
    except AuthError as exc:
        log.error("%s", exc)
        return 1
    except KeyboardInterrupt:
        log.info("Stopped. Bye!")
        return 130
    except Exception as exc:
        if type(exc).__name__ == "RefreshError":
            log.error(
                "Google rejected the saved token (%s). Delete %s and run:  python -m hii_digest auth",
                exc,
                cfg.token_file,
            )
            return 1
        if type(exc).__name__ == "HttpError":
            log.error("Gmail API error: %s", exc)
            return 1
        raise


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
