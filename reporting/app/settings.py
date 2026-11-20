from functools import lru_cache
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode


def _csv(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return [str(v).strip() for v in value if str(v).strip()]


class Settings(BaseSettings):
    """Read-side configuration, environment variables only."""

    database_url: str

    # Sentiment blend (see design D3): blended = text_weight * text + behavior_weight * behavioral
    sentiment_text_weight: float = 0.6
    sentiment_behavior_weight: float = 0.4
    sentiment_weight_failure: float = -1.0
    sentiment_weight_user_corrected: float = -0.6
    sentiment_weight_success: float = 0.3
    sentiment_cache_ttl_seconds: int = 60

    max_page_size: int = 200
    static_dir: str | None = None

    # --- Reporting auth (design D12) -------------------------------------------------
    # Requests are authenticated only when the TCP peer is inside one of these CIDRs
    # (the Envoy Gateway / oauth2-proxy pods) AND carry X-Auth-Request-Email. Empty
    # means every /v1 and /internal route answers 401.
    # NoDecode: the env value is a comma list, not JSON (pydantic-settings would
    # otherwise try json.loads on list-typed fields before the validator runs).
    trusted_proxy_cidrs: Annotated[list[str], NoDecode] = []
    # Bootstrap admins: always admin, upserted into `principals` at startup.
    admin_emails: Annotated[list[str], NoDecode] = []
    # Off by default: the socket peer is the proxy pod. When on, the right-most
    # X-Forwarded-For entry (added by the trusted peer) is used as the client address.
    trust_forwarded_for: bool = False
    # Optional shared token letting an in-cluster CronJob call POST /internal/refresh
    # (Authorization: Bearer <token> or X-Internal-Token) without the Gateway identity.
    internal_refresh_token: str | None = None

    # --- Scheduler (design D13) and retention (design D11) -------------------------
    aggregate_refresh_seconds: int = 300  # 0 disables the ticker
    retention_sensitive_days: int = 30
    retention_internal_days: int = 365
    retention_interval_seconds: int = 86400  # "nightly"
    # Days of sentiment / theme history recomputed on every tick (a full recompute
    # happens when the tables are empty).
    sentiment_recompute_days: int = 35
    themes_llm: bool = False  # LLM theme scoring, reserved (off; not implemented)

    # --- Export (design D12) -----------------------------------------------------
    export_max_rows: int = 1_000_000
    export_batch_rows: int = 1000

    # --- Improvement loop (design D14) ------------------------------------------------
    # Analyst: disabled unless ANTHROPIC_API_KEY is set (a Kubernetes secret).
    anthropic_api_key: str | None = None
    analyst_model: str = "claude-sonnet-5-5"
    # "" = manual only (POST /v1/proposals/run); otherwise an interval such as
    # "daily", "weekly", "12h", "7d" or a number of seconds.
    analyst_schedule: str = ""
    analyst_max_artifacts: int = 5
    analyst_sees_sensitive: bool = False  # ANALYST_SEES_SENSITIVE=on sends verbatim corrections
    analyst_window_days: int = 30
    analyst_max_examples: int = 10
    analyst_max_tokens: int = 16000
    analyst_effort: str = "high"
    # Server-side refusal fallback (`fallbacks: "default"`) on models that support it.
    analyst_fallbacks: bool = True

    # Outcome measurement: equal before/after windows around the merge.
    outcome_window_days: int = 14
    outcome_min_sample: int = 20
    outcome_relative_threshold: float = 0.10  # counts, tokens, cost
    outcome_rate_points: float = 0.02  # correction / failure rate, absolute

    # GitHub: a GitHub App (preferred) or a token; neither = PR creation disabled.
    github_app_id: str | None = None
    github_app_private_key: str | None = None
    github_app_installation_id: str | None = None
    github_token: str | None = None
    github_webhook_secret: str | None = None
    github_api_url: str = "https://api.github.com"
    github_base_branch: str | None = None  # default: the repository's default branch
    github_poll_seconds: int = 900  # 0 disables the merge poller
    # Absolute dashboard URL for links in PR bodies (e.g. https://ouroboros.example.com).
    public_base_url: str | None = None

    @field_validator("trusted_proxy_cidrs", "admin_emails", mode="before")
    @classmethod
    def _split_csv(cls, value):
        return _csv(value)

    @field_validator("admin_emails", mode="after")
    @classmethod
    def _lower_emails(cls, value: list[str]) -> list[str]:
        return [v.lower() for v in value]

    @field_validator(
        "internal_refresh_token", "anthropic_api_key", "github_app_id", "github_app_private_key",
        "github_app_installation_id", "github_token", "github_webhook_secret", "github_base_branch",
        "public_base_url", mode="before",
    )
    @classmethod
    def _blank_token_is_none(cls, value):
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("analyst_schedule", mode="after")
    @classmethod
    def _valid_schedule(cls, value: str) -> str:
        parse_interval(value)
        return value

    @field_validator("analyst_effort", mode="after")
    @classmethod
    def _valid_effort(cls, value: str) -> str:
        if value not in ("low", "medium", "high", "xhigh", "max"):
            raise ValueError("ANALYST_EFFORT must be low, medium, high, xhigh or max")
        return value

    @property
    def analyst_interval_seconds(self) -> int | None:
        return parse_interval(self.analyst_schedule)


@lru_cache
def get_settings() -> Settings:
    return Settings()


def parse_interval(value: str | None) -> int | None:
    """``ANALYST_SCHEDULE`` -> seconds; ``None`` for "" / "off" (manual only)."""
    if value is None:
        return None
    value = value.strip().lower()
    if value in ("", "off", "manual", "none", "0"):
        return None
    named = {"hourly": 3600, "daily": 86400, "weekly": 7 * 86400}
    if value in named:
        return named[value]
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 7 * 86400}
    if value[-1] in units and value[:-1].isdigit():
        seconds = int(value[:-1]) * units[value[-1]]
    elif value.isdigit():
        seconds = int(value)
    else:
        raise ValueError(f"unrecognized schedule {value!r}; use daily, weekly, 12h, 7d or seconds")
    return seconds if seconds > 0 else None
