"""Report one skill outcome from the command line.

    python3 -m ouroboros_client.report --skill <name> --version <ver> \
        --status success|failure|user_corrected --notes "..." \
        [--corrections "..."] [--patch "..."] [--latency-ms N] [--error-code X] \
        [--session-id UUID] [--no-flush]

Deployment and identity come from the environment (OUROBOROS_API_URL,
OUROBOROS_DEV_KEY_DIR or Entra, OUROBOROS_OID/UPN); the session id defaults to
the one Claude Code exports (CLAUDE_CODE_SESSION_ID). Like the SDK it never
fails the caller: every path exits 0, and an unreachable API leaves the event
spooled for the next report to drain. Every report also drains the hook-event
spool (`spool/hooks/`), so a session that never reports an outcome still
uploads once any skill does. --json prints what happened.
"""

import argparse
import json
import sys

from . import config, spool
from .client import TelemetryClient


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ouroboros-report", description=__doc__.split("\n\n")[0])
    parser.add_argument("--skill", required=True, help="skill name")
    parser.add_argument("--version", required=True, help="skill version")
    parser.add_argument("--status", required=True, choices=["success", "failure", "user_corrected"])
    parser.add_argument("--notes", required=True, help="one or two sentences on what happened")
    parser.add_argument("--corrections", help="the user's correction, verbatim (sensitive)")
    parser.add_argument("--patch", help="a concrete improvement to the skill's instructions")
    parser.add_argument("--latency-ms", type=int)
    parser.add_argument("--error-code")
    parser.add_argument("--model")
    parser.add_argument("--session-id", help="defaults to CLAUDE_CODE_SESSION_ID")
    parser.add_argument("--no-flush", action="store_true", help="spool only; do not send now")
    parser.add_argument("--json", action="store_true", help="print a one-line status report")
    args = parser.parse_args(argv)

    feedback = {"notes": args.notes}
    if args.corrections:
        feedback["user_corrections"] = args.corrections
    if args.patch:
        feedback["suggested_patch"] = args.patch

    result = {
        "enabled": config.telemetry_enabled(),
        "spooled_before": len(spool.list_files()),
        "hook_spooled_before": len(spool.list_hook_files()),
    }
    try:
        client = TelemetryClient()
        result["configured"] = client.configured
        client.record(
            skill_name=args.skill,
            skill_version=args.version,
            status=args.status,
            session_id=args.session_id,
            feedback=feedback,
            model=args.model,
            latency_ms=args.latency_ms,
            error_code=args.error_code,
        )
        if not args.no_flush:
            client.flush()
    except Exception as exc:  # never fail the caller
        result["error"] = type(exc).__name__
    result["spooled_after"] = len(spool.list_files())
    result["hook_spooled_after"] = len(spool.list_hook_files())
    if args.json:
        print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
