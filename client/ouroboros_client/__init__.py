"""ouroboros-client: fire-and-forget skill telemetry.

Lazy exports keep the hook path stdlib-only — importing this package must not
pull httpx unless TelemetryClient is actually used.
"""

# Single source of truth for the SDK/plugin version. Stamped as `client_version`
# into every hook event and outcome event and exposed to the OTel resource by
# setup_otel (design D16). Must match client/pyproject.toml.
__version__ = "0.2.0"

__all__ = ["TelemetryClient", "__version__", "azure_token_provider", "dev_token_provider"]


def __getattr__(name: str):
    if name == "TelemetryClient":
        from .client import TelemetryClient

        return TelemetryClient
    if name in ("azure_token_provider", "dev_token_provider"):
        from . import auth

        return getattr(auth, name)
    raise AttributeError(name)
