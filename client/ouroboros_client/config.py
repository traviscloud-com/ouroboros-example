"""Configuration. Stdlib-only — the hook path imports this module."""

import os
from pathlib import Path

_ON = {"on", "1", "true", "yes"}
_OFF = {"off", "0", "false", "no"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, ""))
    except ValueError:
        return default


def _env_flag(name: str, default: bool) -> bool:
    value = os.environ.get(name, "").strip().lower()
    if value in _ON:
        return True
    if value in _OFF:
        return False
    return default


def telemetry_enabled() -> bool:
    """Master switch: OUROBOROS_TELEMETRY=off disables every capture and send."""
    return os.environ.get("OUROBOROS_TELEMETRY", "").strip().lower() != "off"


def capture_prompts() -> bool:
    """OUROBOROS_CAPTURE_PROMPTS (default on — the existing disclosed behaviour):
    prompt text, last assistant message, compaction summary, session title."""
    return _env_flag("OUROBOROS_CAPTURE_PROMPTS", True)


def capture_tool_content() -> bool:
    """OUROBOROS_CAPTURE_TOOL_CONTENT (default off): tool_input / tool_response
    and slash-command argument text in hook events."""
    return _env_flag("OUROBOROS_CAPTURE_TOOL_CONTENT", False)


def api_url() -> str | None:
    """Ingestion API base URL (OUROBOROS_API_URL); None = not configured."""
    return os.environ.get("OUROBOROS_API_URL", "").strip() or None


def dev_key_dir() -> Path | None:
    """Directory holding jwks.json + private_key.pem for dev-mode tokens
    (OUROBOROS_DEV_KEY_DIR); None = use Entra (DefaultAzureCredential)."""
    value = os.environ.get("OUROBOROS_DEV_KEY_DIR", "").strip()
    return Path(value).expanduser() if value else None


DEFAULT_OID = "00000000-0000-0000-0000-000000000001"
DEFAULT_UPN = "dev@ouroboros.local"


def dev_identity() -> tuple[str, str]:
    """(oid, upn) claims minted into dev-mode tokens: OUROBOROS_OID / OUROBOROS_UPN."""
    oid = os.environ.get("OUROBOROS_OID", "").strip() or DEFAULT_OID
    upn = os.environ.get("OUROBOROS_UPN", "").strip() or DEFAULT_UPN
    return oid, upn


def session_id() -> str | None:
    """The conversation id to correlate events on: OUROBOROS_SESSION_ID, else the
    id Claude Code exports to its tools (CLAUDE_CODE_SESSION_ID / CLAUDE_SESSION_ID)."""
    for name in ("OUROBOROS_SESSION_ID", "CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return None


def base_dir() -> Path:
    return Path(os.environ.get("OUROBOROS_HOME", "~/.ouroboros")).expanduser()


def spool_dir() -> Path:
    """Outcome events (`events`), subject to the 300 s freshness rule."""
    return base_dir() / "spool"


def hook_spool_dir() -> Path:
    """Structured hook-event records (design D6): separate directory so the
    stale-outcome logic never applies to them."""
    return base_dir() / "spool" / "hooks"


def interactions_dir() -> Path:
    return base_dir() / "interactions"


def spool_cap() -> int:
    return _env_int("OUROBOROS_SPOOL_CAP", 500)


def hook_spool_cap() -> int:
    return _env_int("OUROBOROS_HOOK_SPOOL_CAP", 5000)


def hook_nudge_enabled() -> bool:
    """Whether a hook run may spawn the out-of-process sender
    (OUROBOROS_HOOK_NUDGE, default on). Off is for debugging only."""
    return _env_flag("OUROBOROS_HOOK_NUDGE", True)


def ring_size() -> int:
    return _env_int("OUROBOROS_INTERACTION_RING", 5)


def interaction_ttl_seconds() -> int:
    return _env_int("OUROBOROS_INTERACTION_TTL", 86400)


def freshness_window_seconds() -> int:
    return _env_int("OUROBOROS_FRESHNESS_WINDOW", 300)


SEND_TIMEOUT_SECONDS = 3.0
# Upper bound on one hook-spool drain (all batches together), design D6.
HOOK_DRAIN_BUDGET_SECONDS = 3.0
HOOK_BATCH_SIZE = 200
# Minimum gap between sender processes spawned by hook runs.
HOOK_NUDGE_MIN_INTERVAL_SECONDS = 5.0
# InstructionsLoaded content hashes are computed only for files up to this size.
INSTRUCTIONS_HASH_MAX_BYTES = 4 * 1024 * 1024
