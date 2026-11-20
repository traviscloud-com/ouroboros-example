"""Bearer JWT validation.

401 = the token cannot be verified (missing, malformed, bad signature, expired).
403 = the token verified but carries the wrong claims (audience, issuer, scope,
missing identity claims).

The validation pipeline (signature -> aud/iss/exp -> scope -> oid/upn) is shared
by design between modes; only the key source differs. AUTH_MODE=entra fails
closed until the Entra metadata key source is implemented (phase 2).
"""

import json
from dataclasses import dataclass
from pathlib import Path

import jwt
from fastapi import HTTPException, Request

from .settings import AuthMode, Settings

REQUIRED_SCOPE = "Telemetry.Write"


@dataclass(frozen=True)
class AuthContext:
    oid: str
    upn: str


class JwtValidator:
    def __init__(self, keys_by_kid: dict[str, jwt.PyJWK], audience: str, issuer: str):
        self._keys = keys_by_kid
        self._audience = audience
        self._issuer = issuer

    def validate(self, token: str) -> AuthContext:
        try:
            kid = jwt.get_unverified_header(token).get("kid")
        except jwt.PyJWTError:
            raise HTTPException(status_code=401, detail="Malformed token")

        key = self._keys.get(kid)
        if key is None:
            raise HTTPException(status_code=401, detail="Unknown signing key")

        try:
            claims = jwt.decode(
                token,
                key=key.key,
                algorithms=["RS256"],
                audience=self._audience,
                issuer=self._issuer,
                options={"require": ["exp", "aud", "iss"]},
            )
        except (jwt.InvalidAudienceError, jwt.InvalidIssuerError):
            raise HTTPException(status_code=403, detail="Invalid audience or issuer")
        except jwt.MissingRequiredClaimError:
            raise HTTPException(status_code=403, detail="Missing required claim")
        except jwt.PyJWTError:
            # Bad signature, expired, or otherwise unverifiable
            raise HTTPException(status_code=401, detail="Invalid token")

        scopes = str(claims.get("scp", "")).split()
        if REQUIRED_SCOPE not in scopes:
            raise HTTPException(status_code=403, detail=f"Scope {REQUIRED_SCOPE} required")

        oid, upn = claims.get("oid"), claims.get("upn")
        if not oid or not upn:
            raise HTTPException(status_code=403, detail="Token lacks oid/upn identity claims")

        return AuthContext(oid=oid, upn=upn)


class EntraNotImplementedValidator:
    """Fail closed: never falls back to dev keys (spec: entra mode reserved)."""

    def validate(self, token: str) -> AuthContext:
        raise HTTPException(
            status_code=501, detail="AUTH_MODE=entra is not implemented in this phase"
        )


def build_validator(settings: Settings):
    if settings.auth_mode is AuthMode.entra:
        return EntraNotImplementedValidator()
    if not settings.jwks_path:
        raise RuntimeError("AUTH_MODE=dev requires JWKS_PATH")
    jwks = json.loads(Path(settings.jwks_path).read_text())
    keys = {k["kid"]: jwt.PyJWK(k) for k in jwks["keys"]}
    return JwtValidator(keys, audience=settings.jwt_audience, issuer=settings.jwt_issuer)


def require_auth(request: Request) -> AuthContext:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Bearer token required")
    return request.app.state.auth_validator.validate(token.strip())
