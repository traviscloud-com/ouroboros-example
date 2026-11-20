"""Reporting authentication and authorization (design D12).

Identity comes from the Gateway: oauth2-proxy sets ``X-Auth-Request-Email`` on
every authenticated request and the reporting pod only ever sees the Envoy
proxy as its TCP peer. A request to ``/v1/*`` or ``/internal/*`` is therefore
authenticated iff the peer address is inside ``TRUSTED_PROXY_CIDRS`` **and**
the header is present; anything else is 401 (including direct in-cluster calls
with a forged header). The email is then resolved to a role: ``ADMIN_EMAILS``
are admins unconditionally, everyone else needs a ``principals`` row, and an
unknown email gets 403. Health probes and the SPA's static files stay public,
and so does ``POST /v1/proposals/github-webhook``, which checks its own HMAC.

``POST /internal/refresh`` may alternatively present ``INTERNAL_REFRESH_TOKEN``
(a CronJob inside the cluster is not behind the Gateway); that identity is an
admin named ``internal-token``.
"""

from __future__ import annotations

import hmac
import ipaddress
import json
import logging
from dataclasses import dataclass

from fastapi import HTTPException, Request
from sqlalchemy import text
from sqlalchemy.engine import Engine
from starlette.concurrency import run_in_threadpool

from .settings import Settings

log = logging.getLogger("ouroboros.reporting.auth")

ROLES = ("leadership", "engineer", "admin")
ROLE_RANK = {role: i for i, role in enumerate(ROLES)}
LANDING = {"leadership": "/overview", "engineer": "/engineering", "admin": "/engineering"}

EMAIL_HEADER = b"x-auth-request-email"
PROTECTED_PREFIXES = ("/v1/", "/internal/")
# /v1 paths that authenticate themselves and are reached without the Gateway
# login (they need their own HTTPRoute path + allow SecurityPolicy): the GitHub
# webhook verifies an HMAC signature with GITHUB_WEBHOOK_SECRET.
SELF_AUTHENTICATED_PATHS = frozenset({"/v1/proposals/github-webhook"})
INTERNAL_TOKEN_IDENTITY = "internal-token"


@dataclass(frozen=True)
class Principal:
    email: str
    role: str
    via: str = "gateway"  # gateway | bootstrap | token

    def at_least(self, role: str) -> bool:
        return ROLE_RANK[self.role] >= ROLE_RANK[role]

    @property
    def redacted(self) -> bool:
        """Leadership never sees sensitive text (design D12)."""
        return self.role == "leadership"


def parse_cidrs(values: list[str]) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    nets = []
    for value in values:
        nets.append(ipaddress.ip_network(value.strip(), strict=False))
    return nets


def peer_is_trusted(peer: str | None, networks) -> bool:
    if not peer or not networks:
        return False
    try:
        addr = ipaddress.ip_address(peer)
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return any(addr in net for net in networks)


# --- principals ---------------------------------------------------------------------


def bootstrap_admins(engine: Engine, emails: list[str]) -> int:
    """Upsert ADMIN_EMAILS into principals as admins (created_by = 'bootstrap')."""
    if not emails:
        return 0
    with engine.begin() as conn:
        for email in emails:
            conn.execute(
                text(
                    "INSERT INTO principals (email, role, created_by) VALUES (:email, 'admin', 'bootstrap') "
                    "ON CONFLICT (email) DO UPDATE SET role = 'admin' WHERE principals.role <> 'admin'"
                ),
                {"email": email.lower()},
            )
    return len(emails)


def lookup_role(engine: Engine, email: str) -> str | None:
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT role FROM principals WHERE email = :email"), {"email": email}
        ).scalar_one_or_none()


def list_principals(engine: Engine) -> list[dict]:
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT email, role, created_at, created_by FROM principals ORDER BY email")
        ).mappings().all()
    return [dict(r) for r in rows]


def upsert_principal(engine: Engine, *, email: str, role: str, actor: Principal) -> dict:
    email = email.strip().lower()
    with engine.begin() as conn:
        previous = conn.execute(
            text("SELECT role FROM principals WHERE email = :email"), {"email": email}
        ).scalar_one_or_none()
        row = conn.execute(
            text(
                "INSERT INTO principals (email, role, created_by) VALUES (:email, :role, :by) "
                "ON CONFLICT (email) DO UPDATE SET role = EXCLUDED.role "
                "RETURNING email, role, created_at, created_by"
            ),
            {"email": email, "role": role, "by": actor.email},
        ).mappings().one()
        write_audit(
            conn, actor, "principal.upsert", target_kind="principal", target_id=email,
            details={"role": role, "previous_role": previous},
        )
    return dict(row)


def delete_principal(engine: Engine, *, email: str, actor: Principal) -> bool:
    email = email.strip().lower()
    with engine.begin() as conn:
        deleted = conn.execute(
            text("DELETE FROM principals WHERE email = :email RETURNING role"), {"email": email}
        ).scalar_one_or_none()
        if deleted is not None:
            write_audit(
                conn, actor, "principal.delete", target_kind="principal", target_id=email,
                details={"previous_role": deleted},
            )
    return deleted is not None


def write_audit(conn, actor: Principal | None, action: str, *, target_kind: str | None = None,
                target_id: str | None = None, details: dict | None = None) -> None:
    conn.execute(
        text(
            "INSERT INTO audit_log (actor_email, actor_role, action, target_kind, target_id, details) "
            "VALUES (:email, :role, :action, :kind, :target, CAST(:details AS JSONB))"
        ),
        {
            "email": actor.email if actor else "system",
            "role": actor.role if actor else None,
            "action": action,
            "kind": target_kind,
            "target": target_id,
            "details": json.dumps(details or {}, default=str),
        },
    )


# --- middleware -----------------------------------------------------------------------


class AuthMiddleware:
    """Pure ASGI middleware: resolves ``scope["state"]["principal"]`` for protected
    paths or answers 401/403 itself."""

    def __init__(self, app, *, settings: Settings, engine_getter):
        self.app = app
        self.settings = settings
        self.networks = parse_cidrs(settings.trusted_proxy_cidrs)
        self.admin_emails = set(settings.admin_emails)
        self._engine_getter = engine_getter

    @staticmethod
    def is_protected(path: str) -> bool:
        return path.startswith(PROTECTED_PREFIXES) and path.rstrip("/") not in SELF_AUTHENTICATED_PATHS

    def client_address(self, scope) -> str | None:
        client = scope.get("client")
        peer = client[0] if client else None
        if self.settings.trust_forwarded_for:
            for name, value in scope.get("headers", []):
                if name == b"x-forwarded-for":
                    forwarded = value.decode("latin-1").split(",")[-1].strip()
                    if forwarded:
                        return forwarded
        return peer

    def _header(self, scope, name: bytes) -> str | None:
        for key, value in scope.get("headers", []):
            if key == name:
                return value.decode("latin-1").strip()
        return None

    def _token_principal(self, scope) -> Principal | None:
        token = self.settings.internal_refresh_token
        if not token or not scope["path"].startswith("/internal/"):
            return None
        presented = self._header(scope, b"x-internal-token")
        if presented is None:
            auth = self._header(scope, b"authorization") or ""
            if auth.lower().startswith("bearer "):
                presented = auth[7:].strip()
        if presented and hmac.compare_digest(presented, token):
            return Principal(email=INTERNAL_TOKEN_IDENTITY, role="admin", via="token")
        return None

    async def resolve(self, scope) -> Principal:
        if (principal := self._token_principal(scope)) is not None:
            return principal
        if not peer_is_trusted(self.client_address(scope), self.networks):
            raise HTTPException(status_code=401, detail="Untrusted peer")
        email = (self._header(scope, EMAIL_HEADER) or "").lower()
        if not email:
            raise HTTPException(status_code=401, detail="Missing X-Auth-Request-Email")
        if email in self.admin_emails:
            return Principal(email=email, role="admin", via="bootstrap")
        engine = self._engine_getter()
        role = await run_in_threadpool(lookup_role, engine, email)
        if role is None:
            raise HTTPException(status_code=403, detail="No role assigned for this account")
        return Principal(email=email, role=role)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not self.is_protected(scope["path"]):
            await self.app(scope, receive, send)
            return
        try:
            principal = await self.resolve(scope)
        except HTTPException as exc:
            await self._reject(send, exc.status_code, exc.detail)
            return
        except Exception:  # database down etc.: fail closed, let /readyz tell the story
            log.exception("principal lookup failed")
            await self._reject(send, 503, "Authorization unavailable")
            return
        scope.setdefault("state", {})["principal"] = principal
        await self.app(scope, receive, send)

    @staticmethod
    async def _reject(send, status: int, detail: str) -> None:
        body = json.dumps({"detail": detail}).encode()
        headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
        if status == 401:
            headers.append((b"www-authenticate", b"X-Auth-Request-Email"))
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})


# --- route dependencies ------------------------------------------------------------


def current_principal(request: Request) -> Principal:
    principal = getattr(request.state, "principal", None)
    if principal is None:  # route mounted outside the protected prefixes
        raise HTTPException(status_code=401, detail="Not authenticated")
    return principal


def require_role(minimum: str):
    """Dependency factory: ``Depends(require_role("engineer"))``."""
    if minimum not in ROLE_RANK:
        raise ValueError(f"unknown role {minimum!r}")

    def dependency(request: Request) -> Principal:
        principal = current_principal(request)
        if not principal.at_least(minimum):
            raise HTTPException(status_code=403, detail=f"Requires role {minimum} or higher")
        return principal

    return dependency
