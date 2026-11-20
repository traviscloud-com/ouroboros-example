import uuid
from datetime import datetime, timezone

from . import __version__, config, interactions, spool
from .auth import azure_token_provider, dev_token_provider
from .sender import Sender

_STATUSES = {"success", "failure", "user_corrected"}
DEFAULT_SCOPE = "api://ouroboros-telemetry/.default"


def default_token_provider(scope: str = DEFAULT_SCOPE):
    """Dev-mode tokens when OUROBOROS_DEV_KEY_DIR is set, Entra otherwise."""
    key_dir = config.dev_key_dir()
    if key_dir is not None:
        oid, upn = config.dev_identity()
        return dev_token_provider(key_dir, oid=oid, upn=upn)
    return azure_token_provider(scope)


class TelemetryClient:
    """Fire-and-forget telemetry reporter.

    record() persists locally and returns; the network happens on a background
    thread with a 3s budget. No call on this class ever raises into the caller
    or prompts for credentials (FR-6 / SR-1).
    """

    def __init__(
        self,
        api_url: str | None = None,
        scope: str = DEFAULT_SCOPE,
        token_provider=None,
    ):
        """Everything defaults from the environment so callers (skills, the CLI)
        need no deployment details: OUROBOROS_API_URL, and for the token
        OUROBOROS_DEV_KEY_DIR (+ OUROBOROS_OID / OUROBOROS_UPN) or Entra. With no
        API URL at all the client is inert: record() keeps nothing and sends nothing."""
        self._api_url = api_url or config.api_url()
        self._sender = Sender(
            self._api_url or "", token_provider or default_token_provider(scope)
        )

    @property
    def configured(self) -> bool:
        return bool(self._api_url)

    def record(
        self,
        skill_name: str,
        skill_version: str,
        status: str,
        session_id: str | None = None,
        feedback: dict | None = None,
        *,
        model: str | None = None,
        latency_ms: int | None = None,
        error_code: str | None = None,
    ) -> None:
        try:
            if not config.telemetry_enabled() or not self.configured:
                return
            if status not in _STATUSES or not skill_name or not skill_version:
                return  # invalid input is dropped, never raised
            if not isinstance(feedback, dict) or not feedback.get("notes"):
                return
            session_id = str(uuid.UUID(str(session_id or config.session_id())))

            event: dict = {
                "event_id": str(uuid.uuid4()),
                "session_id": session_id,
                "client_ts": datetime.now(timezone.utc).isoformat(),
                "skill_name": skill_name,
                "skill_version": skill_version,
                "status": status,
                "feedback": feedback,
                "client_version": __version__,
            }
            if model is not None:
                event["model"] = model
            if latency_ms is not None:
                event["latency_ms"] = latency_ms
            if error_code is not None:
                event["error_code"] = error_code

            pair, prompt_id = interactions.take_latest_complete_with_prompt_id(session_id)
            if pair is not None:
                event["interaction"] = pair
                if prompt_id:
                    event["prompt_id"] = prompt_id

            spool.write_event(event)
            self._sender.nudge()
        except Exception:
            pass

    def flush(self) -> None:
        """Synchronously attempt one drain of both spools (outcomes and hook
        events). Still never raises; intended for tests, scripts, the `report`
        CLI and process-exit best effort."""
        try:
            if config.telemetry_enabled() and self.configured:
                self._sender.drain_once()
        except Exception:
            pass
