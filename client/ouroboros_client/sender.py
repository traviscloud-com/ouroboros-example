"""Background sender: drains both spools, swallows every failure (FR-6).

- outcome events  -> POST /v1/telemetry/events       (fresh/stale partitioned)
- hook events     -> POST /v1/telemetry/hook-events  (batches of <=200, one
                     drain bounded to HOOK_DRAIN_BUDGET_SECONDS, design D6)

`python3 -m ouroboros_client.sender` is the out-of-process nudge target the
hook spawns: it takes a lock so concurrent hook runs produce one drain, uses
the same env-driven configuration as the SDK, and never prints or raises.
"""

import json
import sys
import threading
import time
from datetime import datetime, timezone

import httpx

from . import __version__, config, spool

_LOG_MAX_BYTES = 1024 * 1024


def _log(message: str) -> None:
    """Append one line to ~/.ouroboros/sender.log (locally, never uploaded).
    Best effort; stops appending once the file grows past 1 MiB."""
    try:
        path = config.base_dir() / "sender.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > _LOG_MAX_BYTES:
            return
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with path.open("a") as fh:
            fh.write(f"{stamp} {message}\n")
    except Exception:
        pass


class Sender:
    def __init__(self, api_url: str, token_provider, timeout: float = config.SEND_TIMEOUT_SECONDS):
        self._api_url = api_url.rstrip("/")
        self._token_provider = token_provider
        self._timeout = timeout
        self._wake = threading.Event()
        self._drain_lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def nudge(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        self._wake.set()

    def _run(self) -> None:
        while self._wake.wait():
            self._wake.clear()
            self.drain_once()

    def drain_once(self) -> None:
        """Synchronous drain of both spools; also the public flush path.
        Never raises."""
        with self._drain_lock:
            if not spool.list_files() and not spool.list_hook_files():
                return  # nothing to do: never touch the credential
            token = self._token()
            if not token:
                return  # silent: events stay spooled (spec: no credential -> no-op)
            try:
                self._drain_outcomes(token)
            except Exception:
                pass
            try:
                self._drain_hooks(token)
            except Exception:
                pass

    def _token(self) -> str | None:
        try:
            return self._token_provider()
        except Exception:
            return None

    # --- outcome events ------------------------------------------------------

    def _drain_outcomes(self, token: str) -> None:
        files = spool.list_files()
        if not files:
            return
        fresh, stale = spool.partition_fresh_stale(files)
        # (files, drop_on_400): stale events are expected to be rejected for
        # freshness — drop them so they cannot accumulate forever.
        for batch_files, drop_on_400 in ((fresh, False), (stale, True)):
            if not batch_files:
                continue
            events = spool.load(batch_files)
            if not events:
                continue
            version = "1.1" if any("interaction" in e for e in events) else "1.0"
            try:
                resp = httpx.post(
                    f"{self._api_url}/v1/telemetry/events",
                    json={"schema_version": version, "events": events},
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=self._timeout,
                )
            except Exception:
                continue  # unreachable/timeout: keep spooled
            if 200 <= resp.status_code < 300:
                spool.remove(batch_files)
            elif resp.status_code == 400 and drop_on_400:
                spool.remove(batch_files)
            # anything else: keep spooled for a later retry

    # --- hook events ---------------------------------------------------------

    def _drain_hooks(self, token: str, budget: float = config.HOOK_DRAIN_BUDGET_SECONDS) -> None:
        """Upload hook-event records oldest-first in batches of <=200 until the
        spool is empty, a retryable failure happens, or the time budget is
        spent. No stale-drop: the server accepts client_ts up to 7 days old."""
        files = spool.list_hook_files()
        if not files:
            return
        deadline = time.monotonic() + budget
        size = config.HOOK_BATCH_SIZE
        for start in range(0, len(files), size):
            remaining = deadline - time.monotonic()
            if remaining <= 0.05:
                return
            batch_files = files[start : start + size]
            events = spool.load(batch_files)
            if not events:
                continue
            body = {
                "events": events,
                "client": {"name": "ouroboros-plugin", "version": __version__},
            }
            try:
                resp = httpx.post(
                    f"{self._api_url}/v1/telemetry/hook-events",
                    json=body,
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=min(self._timeout, remaining),
                )
            except Exception:
                return  # unreachable/timeout: keep spooled, retry later
            status = resp.status_code
            if 200 <= status < 300:
                spool.remove(batch_files)
            elif status in (400, 422):
                # Shape problem: this batch will never be accepted. Log the
                # server's reason locally and drop it so the spool keeps moving.
                _log(f"hook-events batch of {len(events)} rejected {status}: {resp.text[:500]}")
                spool.remove(batch_files)
            else:
                # 401/403 (token problem) and 5xx: keep spooled, stop this drain
                return


def _acquire_lock():
    """Single sender process at a time (POSIX flock; a no-op elsewhere)."""
    try:
        import fcntl
    except ImportError:
        return object()
    path = config.hook_spool_dir() / ".sender.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = path.open("w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    return fh


def main(argv: list[str] | None = None) -> int:
    """Out-of-process drain: `python3 -m ouroboros_client.sender`."""
    try:
        if not config.telemetry_enabled():
            return 0
        api_url = config.api_url()
        if not api_url:
            return 0
        lock = _acquire_lock()
        if lock is None:
            return 0  # another sender is already draining
        from .client import default_token_provider

        Sender(api_url, default_token_provider()).drain_once()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
