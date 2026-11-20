"""Mint a dev-mode JWT signed by the key produced by gen_jwks.py.

    python3 scripts/dev_token.py [--key-dir .dev-keys] [--ttl 90d|12h|30m|3600]

--ttl takes a duration with an optional d/h/m/s suffix (seconds when bare) and
is the long-lived form used by `python3 -m ouroboros_client.setup_otel`;
--expires-in (seconds) remains as the older spelling.
"""

import argparse
import json
import time
from pathlib import Path

import jwt

DEFAULT_AUDIENCE = "api://ouroboros-telemetry"
DEFAULT_ISSUER = "https://ouroboros.local/dev-issuer"
DEFAULT_SCOPE = "Telemetry.Write"

_TTL_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_ttl(value: str) -> int:
    """'90d' -> 7776000, '12h' -> 43200, '3600' -> 3600."""
    text = value.strip().lower()
    if not text:
        raise argparse.ArgumentTypeError("empty ttl")
    unit = 1
    if text[-1] in _TTL_UNITS:
        unit = _TTL_UNITS[text[-1]]
        text = text[:-1]
    try:
        seconds = int(text) * unit
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid ttl {value!r}; use e.g. 90d, 12h, 3600") from exc
    if seconds <= 0:
        raise argparse.ArgumentTypeError("ttl must be positive")
    return seconds


def mint(
    key_dir: Path,
    oid: str = "00000000-0000-0000-0000-000000000001",
    upn: str = "dev@ouroboros.local",
    scope: str | None = DEFAULT_SCOPE,
    audience: str = DEFAULT_AUDIENCE,
    issuer: str = DEFAULT_ISSUER,
    expires_in: int = 3600,
    drop_claims: set[str] | None = None,
) -> str:
    private_key = (key_dir / "private_key.pem").read_bytes()
    jwks = json.loads((key_dir / "jwks.json").read_text())
    kid = jwks["keys"][0]["kid"]

    now = int(time.time())
    claims = {
        "iss": issuer,
        "aud": audience,
        "iat": now,
        "exp": now + expires_in,
        "oid": oid,
        "upn": upn,
    }
    if scope is not None:
        claims["scp"] = scope
    for name in drop_claims or ():
        claims.pop(name, None)
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": kid})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key-dir", type=Path, default=Path(".dev-keys"))
    parser.add_argument("--oid", default="00000000-0000-0000-0000-000000000001")
    parser.add_argument("--upn", default="dev@ouroboros.local")
    parser.add_argument("--scope", default=DEFAULT_SCOPE)
    parser.add_argument("--audience", default=DEFAULT_AUDIENCE)
    parser.add_argument("--issuer", default=DEFAULT_ISSUER)
    parser.add_argument("--expires-in", type=int, default=None, help="lifetime in seconds (default 3600)")
    parser.add_argument("--ttl", type=parse_ttl, default=None,
                        help="lifetime as a duration: 90d, 12h, 30m or bare seconds; overrides --expires-in")
    args = parser.parse_args()
    expires_in = args.ttl if args.ttl is not None else (args.expires_in if args.expires_in is not None else 3600)
    print(
        mint(
            key_dir=args.key_dir,
            oid=args.oid,
            upn=args.upn,
            scope=args.scope,
            audience=args.audience,
            issuer=args.issuer,
            expires_in=expires_in,
        )
    )


if __name__ == "__main__":
    main()
