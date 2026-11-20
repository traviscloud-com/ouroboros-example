from enum import Enum
from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field, model_validator


class EventStatus(str, Enum):
    success = "success"
    failure = "failure"
    user_corrected = "user_corrected"


class Feedback(BaseModel):
    notes: str
    # SENSITIVE: verbatim user content (PR-1)
    user_corrections: str | None = None
    suggested_patch: str | None = None


class Interaction(BaseModel):
    # SENSITIVE: verbatim conversation content (PR-1); requires schema_version 1.1
    user_prompt: str | None = None
    assistant_response: str | None = None


class TelemetryEvent(BaseModel):
    event_id: UUID
    session_id: UUID
    client_ts: AwareDatetime
    skill_name: str = Field(min_length=1)
    skill_version: str = Field(min_length=1)
    status: EventStatus
    model: str | None = None
    latency_ms: int | None = Field(default=None, ge=0)
    error_code: str | None = None
    feedback: Feedback
    interaction: Interaction | None = None
    # Claude Code prompt id of the turn the skill ran in (optional join key;
    # the server resolves turn_id from it, see db.INSERT_EVENT).
    prompt_id: str | None = Field(default=None, max_length=200)


class TelemetryBatch(BaseModel):
    schema_version: Literal["1.0", "1.1"]
    events: list[TelemetryEvent] = Field(min_length=1)

    @model_validator(mode="after")
    def _interaction_requires_v11(self) -> "TelemetryBatch":
        if self.schema_version == "1.0" and any(
            e.interaction is not None for e in self.events
        ):
            raise ValueError("interaction requires schema_version 1.1")
        return self


# --------------------------------------------------------------- hook events


class HookEvent(BaseModel):
    """One structured hook-event record (design D6). ``payload`` is the client's
    whitelist projection of the hook stdin; the server stores it as-is and
    never adds content the client did not send."""

    hook_event_id: UUID
    session_id: UUID
    prompt_id: str | None = Field(default=None, max_length=200)
    agent_id: str | None = Field(default=None, max_length=200)
    hook_event_name: str = Field(min_length=1, max_length=64)
    client_ts: AwareDatetime
    cwd_hash: str | None = Field(default=None, max_length=128)
    client_version: str | None = Field(default=None, max_length=64)
    # Per-session monotonic counter from the client (spike 0.4: the host clock
    # can step backwards, so projections order by this, not client_ts).
    client_seq: int | None = Field(default=None, ge=0)
    payload: dict[str, Any] = Field(default_factory=dict)


class HookClient(BaseModel):
    name: str | None = None
    version: str | None = None


class HookEventBatch(BaseModel):
    """Batch envelope. Events are validated one by one by the endpoint so a
    single malformed record lands in ``rejected`` instead of failing the batch."""

    events: list[Any] = Field(min_length=1, max_length=200)
    client: HookClient | None = None


class HookEventRejection(BaseModel):
    hook_event_id: str | None
    reason: str


class HookEventResult(BaseModel):
    accepted: int
    duplicates: int
    rejected: list[HookEventRejection]
