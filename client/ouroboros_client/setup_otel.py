"""Enable Claude Code's built-in OpenTelemetry export to Ouroboros.

    python3 -m ouroboros_client.setup_otel [--dry-run | --print] [--settings PATH]
        [--ttl-days 90] [--token TOKEN] [--remove]

Writes the OTel env block into the user-scope `~/.claude/settings.json`
(project/local settings are ignored by Claude Code for OTel) under `env`,
idempotently: the file is read, the keys merged, everything else preserved,
and a backup taken before the first change. The bearer token is a long-lived
dev token (default 90 days, scope Telemetry.Write) minted from
OUROBOROS_DEV_KEY_DIR, or `--token` for a token obtained elsewhere.

Privacy posture (design D5/D7): metrics and lifecycle events only. Prompt,
response, tool-content and raw-API-body logging stay OFF; OTEL_LOG_TOOL_DETAILS
is ON because it carries skill/plugin/hook *names*, not content. Prompt text
reaches Ouroboros only through the hook pipeline where the
OUROBOROS_CAPTURE_* toggles apply. `OUROBOROS_TELEMETRY=off` makes this
command do nothing.

Stdlib-only except PyJWT (the `dev` extra) when minting a token.
"""

import argparse
import base64
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import __version__, auth, config

DEFAULT_SETTINGS = Path("~/.claude/settings.json")
DEFAULT_TTL_DAYS = 90
RESOURCE_VERSION_KEY = "ouroboros.client.version"

# Keys this command owns. --remove deletes exactly these (and our resource
# attribute) and nothing else.
OTEL_KEYS = (
    "CLAUDE_CODE_ENABLE_TELEMETRY",
    "OTEL_METRICS_EXPORTER",
    "OTEL_LOGS_EXPORTER",
    "OTEL_EXPORTER_OTLP_PROTOCOL",
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "OTEL_EXPORTER_OTLP_HEADERS",
    "OTEL_LOG_TOOL_DETAILS",
    "OTEL_LOG_USER_PROMPTS",
    "OTEL_LOG_ASSISTANT_RESPONSES",
    "OTEL_LOG_TOOL_CONTENT",
    "OTEL_LOG_RAW_API_BODIES",
)
PRIVACY_FLAGS = {
    "OTEL_LOG_TOOL_DETAILS": "1",  # skill / plugin / hook names, not content
    "OTEL_LOG_USER_PROMPTS": "0",
    "OTEL_LOG_ASSISTANT_RESPONSES": "0",
    "OTEL_LOG_TOOL_CONTENT": "0",
    "OTEL_LOG_RAW_API_BODIES": "0",
}


class SetupError(Exception):
    pass


def build_env(api_url: str, token: str) -> dict[str, str]:
    """The env block for one API base URL and bearer token. Claude Code appends
    /v1/metrics and /v1/logs to the endpoint itself (spike 0.2)."""
    base = api_url.rstrip("/")
    env = {
        "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
        "OTEL_METRICS_EXPORTER": "otlp",
        "OTEL_LOGS_EXPORTER": "otlp",
        "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
        "OTEL_EXPORTER_OTLP_ENDPOINT": f"{base}/v1/otel",
        "OTEL_EXPORTER_OTLP_HEADERS": f"Authorization=Bearer {token}",
    }
    env.update(PRIVACY_FLAGS)
    return env


def _parse_resource_attributes(value: str | None) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for part in (value or "").split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            if k.strip():
                attrs[k.strip()] = v.strip()
    return attrs


def merge_resource_attributes(existing: str | None, version: str = __version__) -> str:
    """Add ouroboros.client.version to OTEL_RESOURCE_ATTRIBUTES, keeping any
    attributes the user already set (e.g. department=...). The OTel resource
    then carries the plugin version next to Claude Code's own service.version
    (design D16)."""
    attrs = _parse_resource_attributes(existing)
    attrs[RESOURCE_VERSION_KEY] = version
    return ",".join(f"{k}={v}" for k, v in attrs.items())


def strip_resource_attributes(existing: str | None) -> str | None:
    attrs = _parse_resource_attributes(existing)
    attrs.pop(RESOURCE_VERSION_KEY, None)
    return ",".join(f"{k}={v}" for k, v in attrs.items()) or None


def read_settings(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise SetupError(f"{path} is not valid JSON ({exc}); not touching it") from exc
    if not isinstance(data, dict):
        raise SetupError(f"{path} does not hold a JSON object; not touching it")
    return data


def apply_env(settings: dict, env: dict[str, str]) -> dict:
    """Return a new settings dict with `env` merged under the top-level `env`
    key; everything else is preserved as-is."""
    current = settings.get("env")
    if current is None:
        current = {}
    if not isinstance(current, dict):
        raise SetupError('settings "env" is not an object; not touching it')
    merged = dict(current)
    merged.update(env)
    merged["OTEL_RESOURCE_ATTRIBUTES"] = merge_resource_attributes(
        current.get("OTEL_RESOURCE_ATTRIBUTES")
    )
    out = dict(settings)
    out["env"] = merged
    return out


def remove_env(settings: dict) -> dict:
    current = settings.get("env")
    if not isinstance(current, dict):
        return dict(settings)
    merged = {k: v for k, v in current.items() if k not in OTEL_KEYS}
    stripped = strip_resource_attributes(merged.get("OTEL_RESOURCE_ATTRIBUTES"))
    if stripped is None:
        merged.pop("OTEL_RESOURCE_ATTRIBUTES", None)
    else:
        merged["OTEL_RESOURCE_ATTRIBUTES"] = stripped
    out = dict(settings)
    if merged:
        out["env"] = merged
    else:
        out.pop("env", None)
    return out


def write_settings(path: Path, settings: dict) -> Path | None:
    """Back up the existing file (once per change) and replace it atomically.
    Returns the backup path, or None when there was nothing to back up."""
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if path.exists():
        backup = path.with_name(f"{path.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
        backup.write_bytes(path.read_bytes())
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(settings, indent=2) + "\n")
    os.replace(tmp, path)
    return backup


def token_expiry(token: str) -> datetime | None:
    """`exp` of a JWT, decoded without verification (stdlib only)."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
        return datetime.fromtimestamp(int(claims["exp"]), tz=timezone.utc)
    except Exception:
        return None


def mint_token(ttl_days: int) -> str:
    key_dir = config.dev_key_dir()
    if key_dir is None:
        raise SetupError(
            "no token source: set OUROBOROS_DEV_KEY_DIR (dev-mode keys from "
            "api/scripts/gen_jwks.py) or pass --token"
        )
    oid, upn = config.dev_identity()
    try:
        return auth.mint_dev_token(
            key_dir, oid=oid, upn=upn, expires_in=int(timedelta(days=ttl_days).total_seconds())
        )
    except ImportError as exc:
        raise SetupError('PyJWT[crypto] is required to mint a dev token: pip install "ouroboros-client[dev]"') from exc
    except OSError as exc:
        raise SetupError(f"cannot read dev keys in {key_dir}: {exc}") from exc


def redact(env: dict[str, str]) -> dict[str, str]:
    shown = dict(env)
    if "OTEL_EXPORTER_OTLP_HEADERS" in shown:
        shown["OTEL_EXPORTER_OTLP_HEADERS"] = "Authorization=Bearer <redacted>"
    return shown


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m ouroboros_client.setup_otel",
        description=__doc__.split("\n\n")[0],
    )
    parser.add_argument("--settings", type=Path, default=DEFAULT_SETTINGS,
                        help="settings file to edit (default ~/.claude/settings.json)")
    parser.add_argument("--api-url", default=None,
                        help="Ouroboros API base URL (default OUROBOROS_API_URL)")
    parser.add_argument("--ttl-days", type=int, default=DEFAULT_TTL_DAYS,
                        help="dev token lifetime in days (default 90)")
    parser.add_argument("--token", default=None,
                        help="use this bearer token instead of minting a dev token")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true",
                      help="show what would change; write nothing")
    mode.add_argument("--print", dest="print_only", action="store_true",
                      help="print the env block as JSON (with the token) and write nothing")
    mode.add_argument("--remove", action="store_true",
                      help="remove the OTel keys this command writes (undo)")
    args = parser.parse_args(argv)
    out = sys.stdout

    if not config.telemetry_enabled():
        print("OUROBOROS_TELEMETRY=off: telemetry is disabled, nothing to do.", file=out)
        return 0

    settings_path = args.settings.expanduser()
    try:
        settings = read_settings(settings_path)

        if args.remove:
            new_settings = remove_env(settings)
            if new_settings == settings:
                print(f"No OTel keys in {settings_path}; nothing to remove.", file=out)
                return 0
            if args.dry_run:
                print(f"Would remove {', '.join(OTEL_KEYS)} from {settings_path}", file=out)
                return 0
            backup = write_settings(settings_path, new_settings)
            print(f"Removed OTel export from {settings_path}" + (f" (backup: {backup})" if backup else ""), file=out)
            print("Restart Claude Code for the change to take effect.", file=out)
            return 0

        api_url = (args.api_url or config.api_url() or "").strip()
        if not api_url:
            raise SetupError("no API URL: set OUROBOROS_API_URL or pass --api-url")
        token = args.token or mint_token(args.ttl_days)
        env = build_env(api_url, token)

        if args.print_only:
            block = dict(env)
            current_env = settings.get("env") if isinstance(settings.get("env"), dict) else {}
            block["OTEL_RESOURCE_ATTRIBUTES"] = merge_resource_attributes(
                current_env.get("OTEL_RESOURCE_ATTRIBUTES")
            )
            print(json.dumps({"env": block}, indent=2), file=out)
            return 0

        new_settings = apply_env(settings, env)
        expiry = token_expiry(token)
        expiry_text = expiry.strftime("%Y-%m-%d %H:%M UTC") if expiry else "unknown"

        if new_settings == settings:
            print(f"Already configured: {settings_path} has this OTel env block.", file=out)
        elif args.dry_run:
            print(f"Would write to {settings_path} under \"env\":", file=out)
        else:
            backup = write_settings(settings_path, new_settings)
            print(f"Wrote OTel export settings to {settings_path}" + (f" (backup: {backup})" if backup else ""), file=out)

        for key, value in redact(new_settings["env"]).items():
            if key in OTEL_KEYS or key == "OTEL_RESOURCE_ATTRIBUTES":
                print(f"  {key}={value}", file=out)
        print(f"Bearer token expires: {expiry_text}", file=out)
        print("Privacy toggles: OTEL_LOG_TOOL_DETAILS=1 (skill/plugin/hook names); "
              "OTEL_LOG_USER_PROMPTS=0, OTEL_LOG_ASSISTANT_RESPONSES=0, "
              "OTEL_LOG_TOOL_CONTENT=0, OTEL_LOG_RAW_API_BODIES=0 (no content).", file=out)
        print("Undo: python3 -m ouroboros_client.setup_otel --remove, or set OUROBOROS_TELEMETRY=off.", file=out)
        if not args.dry_run and new_settings != settings:
            print("Restart Claude Code for the change to take effect.", file=out)
        return 0
    except SetupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
