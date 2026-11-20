"""Server-side redaction by role (design D12).

Every ``sensitive``-class text value is replaced by ``{"redacted": true,
"length": n}`` for ``leadership``; ``engineer`` and ``admin`` see verbatim
text. Redaction is key-based and recursive so one layer covers the legacy
``/v1/reports/*`` responses, the analytics routes, timelines and exports:

* top-level / nested columns: ``prompt_text``, ``response_text`` (turns),
  ``summary_text`` (compactions), ``input_content``, ``output_content``
  (tool_calls), ``title_redacted`` / ``session_title`` (sessions),
  ``content`` (transcript_lines), ``message`` (turn_errors);
* ``events.feedback`` free text: ``notes``, ``user_corrections``,
  ``suggested_patch``; ``events.interaction``: ``user_prompt``,
  ``assistant_response``;
* hook payload text keys (``prompt``, ``last_assistant_message``,
  ``response``, ``compact_summary``, ``custom_instructions``, ``tool_input``,
  ``tool_response``) in case a raw payload is ever surfaced.

Non-string values under those keys (null, numbers, objects) are returned as
``null`` with ``length`` 0 when redacting, so no sensitive structure leaks.
``RedactingRoute`` applies this to every JSON response on a router based on the
principal the auth middleware attached to the request.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

SENSITIVE_KEYS: frozenset[str] = frozenset(
    {
        "prompt_text", "response_text", "summary_text", "input_content", "output_content",
        "title_redacted", "session_title", "content", "message",
        "notes", "user_corrections", "suggested_patch", "user_prompt", "assistant_response",
        "prompt", "last_assistant_message", "response", "compact_summary", "custom_instructions",
        "tool_input", "tool_response",
    }
)

# Column lists the export datasets use to know what to drop / mark.
SENSITIVE_COLUMNS: dict[str, tuple[str, ...]] = {
    "sessions": ("title_redacted",),
    "turns": ("prompt_text", "response_text"),
    "tool_calls": ("input_content", "output_content"),
    "compactions": ("summary_text",),
    "transcript_lines": ("content",),
    "turn_errors": ("message",),
    "events": ("feedback", "interaction"),  # JSONB containers of sensitive keys
    "usage_facts": (),
    "artifact_versions": (),  # internal class, never redacted
}


def marker(value: Any) -> dict:
    if isinstance(value, str):
        return {"redacted": True, "length": len(value)}
    if isinstance(value, (bytes, bytearray)):
        return {"redacted": True, "length": len(value)}
    if isinstance(value, (dict, list)):
        return {"redacted": True, "length": len(json.dumps(value, default=str))}
    return {"redacted": True, "length": 0}


def redact(obj: Any, *, keys: frozenset[str] = SENSITIVE_KEYS) -> Any:
    """Return a redacted deep copy of ``obj``; non-container leaves are untouched."""
    if isinstance(obj, dict):
        out = {}
        for key, value in obj.items():
            if key in keys:
                out[key] = None if value is None else marker(value)
            else:
                out[key] = redact(value, keys=keys)
        return out
    if isinstance(obj, (list, tuple)):
        return [redact(v, keys=keys) for v in obj]
    return obj


def apply_for(principal, obj: Any) -> Any:
    """Redact unless the principal may see verbatim text."""
    if principal is not None and not principal.redacted:
        return obj
    return redact(obj)


class RedactingRoute(APIRoute):
    """APIRoute that rewrites JSON responses through :func:`redact` for leadership.

    Streaming responses (exports) redact per row themselves; anything that is
    not a ``JSONResponse`` passes through untouched.
    """

    def get_route_handler(self) -> Callable:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            response = await original(request)
            principal = getattr(request.state, "principal", None)
            if principal is None or not principal.redacted:
                return response
            # FastAPI may hand back a plain Response carrying JSON; go by media type.
            content_type = response.headers.get("content-type", "")
            body = getattr(response, "body", None)
            if not content_type.startswith("application/json") or not body:
                return response
            payload = json.loads(bytes(body))
            redacted = JSONResponse(
                content=redact(payload),
                status_code=response.status_code,
                headers={k: v for k, v in response.headers.items() if k.lower() != "content-length"},
                background=response.background,
            )
            return redacted

        return handler
