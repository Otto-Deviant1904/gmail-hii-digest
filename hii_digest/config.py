"""Configuration loaded from environment variables (and an optional ``.env`` file)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_MODEL = "gpt-4.1-mini"
DEFAULT_BASE_URL = "https://api.openai.com/v1"


class ConfigError(ValueError):
    """Raised when a configuration value is invalid."""


@dataclass(frozen=True)
class Config:
    trigger_word: str = "hii"
    timezone: str = "Australia/Melbourne"
    poll_interval: float = 20.0
    top_n: int = 10
    trigger_lookback_hours: float = 24.0
    max_candidates: int = 150
    max_triggers: int = 25
    credentials_file: Path = Path("credentials.json")
    token_file: Path = Path("token.json")
    handled_label: str = "hii-digest/handled"
    digest_label: str = "hii-digest/sent"
    log_level: str = "INFO"
    openai_api_key: str | None = None
    openai_base_url: str = DEFAULT_BASE_URL
    openai_model: str = DEFAULT_MODEL
    llm_timeout: float = 30.0

    @property
    def tz(self) -> ZoneInfo:
        return load_zone(self.timezone)

    @property
    def llm_enabled(self) -> bool:
        return bool(self.openai_api_key)


def load_zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ConfigError(
            f"Unknown timezone {name!r}. Use an IANA name such as 'Australia/Melbourne'."
        ) from exc


def _get(env: dict[str, str], key: str, default: str) -> str:
    value = env.get(key)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def _num(env: dict[str, str], key: str, default: float, cast, minimum: float) -> float:
    raw = _get(env, key, str(default))
    try:
        value = cast(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} must be a number, got {raw!r}") from exc
    if value < minimum:
        raise ConfigError(f"{key} must be >= {minimum}, got {value}")
    return value


def load_config(env: dict[str, str] | None = None, env_file: str | os.PathLike | None = None) -> Config:
    """Build a :class:`Config`.

    If ``env`` is None the process environment is used, after loading ``env_file``
    (default ``.env`` in the current directory) with python-dotenv. Real environment
    variables win over values in the file.
    """
    if env is None:
        from dotenv import load_dotenv

        load_dotenv(dotenv_path=env_file or Path.cwd() / ".env", override=False)
        env = dict(os.environ)

    trigger = _get(env, "HII_TRIGGER_WORD", "hii").lower()
    if not trigger or any(ch.isspace() for ch in trigger):
        raise ConfigError("HII_TRIGGER_WORD must be a single word")

    cfg = Config(
        trigger_word=trigger,
        timezone=_get(env, "HII_TIMEZONE", "Australia/Melbourne"),
        poll_interval=_num(env, "HII_POLL_INTERVAL", 20.0, float, 1.0),
        top_n=int(_num(env, "HII_TOP_N", 10, int, 1)),
        trigger_lookback_hours=_num(env, "HII_TRIGGER_LOOKBACK_HOURS", 24.0, float, 0.1),
        max_candidates=int(_num(env, "HII_MAX_CANDIDATES", 150, int, 1)),
        max_triggers=int(_num(env, "HII_MAX_TRIGGERS", 25, int, 1)),
        credentials_file=Path(_get(env, "HII_CREDENTIALS_FILE", "credentials.json")),
        token_file=Path(_get(env, "HII_TOKEN_FILE", "token.json")),
        handled_label=_get(env, "HII_HANDLED_LABEL", "hii-digest/handled"),
        digest_label=_get(env, "HII_DIGEST_LABEL", "hii-digest/sent"),
        log_level=_get(env, "HII_LOG_LEVEL", "INFO").upper(),
        openai_api_key=_get(env, "OPENAI_API_KEY", "") or None,
        openai_base_url=_get(env, "OPENAI_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
        openai_model=_get(env, "OPENAI_MODEL", DEFAULT_MODEL),
        llm_timeout=_num(env, "HII_LLM_TIMEOUT", 30.0, float, 1.0),
    )
    load_zone(cfg.timezone)  # validate early
    return cfg
