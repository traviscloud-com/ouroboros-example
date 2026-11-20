"""Token providers (design D5): plain callables returning a bearer token or
None. None always means: leave events spooled, stay silent."""

import json
import time
from pathlib import Path

DEV_AUDIENCE = "api://ouroboros-telemetry"
DEV_ISSUER = "https://ouroboros.local/dev-issuer"
DEV_SCOPE = "Telemetry.Write"


def azure_token_provider(scope: str):
    """DefaultAzureCredential with cached credentials only — never interactive.
    azure-identity is an optional extra; missing import just means None."""
    state: dict = {}

    def provider() -> str | None:
        try:
            from azure.identity import DefaultAzureCredential

            if "credential" not in state:
                state["credential"] = DefaultAzureCredential(
                    exclude_interactive_browser_credential=True
                )
            return state["credential"].get_token(scope).token
        except Exception:
            return None

    return provider


def mint_dev_token(
    key_dir: Path | str,
    *,
    oid: str,
    upn: str,
    scope: str = DEV_SCOPE,
    audience: str = DEV_AUDIENCE,
    issuer: str = DEV_ISSUER,
    expires_in: int = 3600,
) -> str:
    """Mint one dev-mode RS256 token against the JWKS layout written by
    api/scripts/gen_jwks.py. Raises on a missing key dir or missing PyJWT —
    callers that must stay silent wrap it (dev_token_provider), callers that
    talk to a human (setup_otel) surface the error."""
    import jwt  # PyJWT[crypto], the `dev` extra

    key_dir = Path(key_dir)
    private_key = (key_dir / "private_key.pem").read_bytes()
    kid = json.loads((key_dir / "jwks.json").read_text())["keys"][0]["kid"]
    now = int(time.time())
    claims = {
        "iss": issuer,
        "aud": audience,
        "iat": now,
        "exp": now + int(expires_in),
        "oid": oid,
        "upn": upn,
        "scp": scope,
    }
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": kid})


def dev_token_provider(
    key_dir: Path | str,
    *,
    oid: str = "00000000-0000-0000-0000-000000000001",
    upn: str = "dev@ouroboros.local",
    scope: str = DEV_SCOPE,
    audience: str = DEV_AUDIENCE,
    issuer: str = DEV_ISSUER,
    expires_in: int = 3600,
):
    """Mints tokens against the same JWKS layout as api/scripts/gen_jwks.py.
    Requires PyJWT[crypto] (dev extra); returns None if unavailable."""

    def provider() -> str | None:
        try:
            return mint_dev_token(
                key_dir,
                oid=oid,
                upn=upn,
                scope=scope,
                audience=audience,
                issuer=issuer,
                expires_in=expires_in,
            )
        except Exception:
            return None

    return provider
