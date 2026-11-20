"""Synthesize the golden transcript fixtures (task 2.6).

No line here comes from a real transcript. Shapes follow
the structure documented in ``ouroboros_client/transcript.py``; ids are fixed fakes; every text value passes through ``redact``, a
length-preserving redactor (letters -> x, digits -> 0, punctuation and
whitespace kept) so content_chars stay realistic while nothing readable is
committed. Regenerate with::

    python3 client/tests/fixtures/transcripts/synthesize.py

``test_transcript.py`` asserts the committed files equal this script's output.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT_DIR = HERE / "-home-user-project"

SESSION = "11111111-1111-4111-8111-111111111111"
AGENT = "0123456789abcdef0"
P1 = "aaaaaaaa-0000-4000-8000-000000000001"
P2 = "aaaaaaaa-0000-4000-8000-000000000002"
CWD = "/home/user/project"
VERSION = "2.1.319"


def u(n: int) -> str:
    """Fixed fake line uuid number n."""
    return f"00000000-0000-4000-8000-{n:012d}"


def su(n: int) -> str:
    """Fixed fake subagent line uuid."""
    return f"00000000-0000-4000-9000-{n:012d}"


def msg(n: int) -> str:
    return "msg_" + f"{n:024d}"


def req(n: int) -> str:
    return "req_" + f"{n:024d}"


def toolu(n: int) -> str:
    return "toolu_" + f"{n:024d}"


def redact(text: str) -> str:
    """Length-preserving: letters -> x, digits -> 0, everything else kept."""
    return re.sub(r"[0-9]", "0", re.sub(r"[A-Za-z]", "x", text))


def ts(seconds: float) -> str:
    base_ms = 1_794_136_000_000  # 2026-11-08T11:06:40Z, fake session start
    ms = base_ms + int(seconds * 1000)
    from datetime import datetime, timezone

    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{ms % 1000:03d}Z"


def envelope(uuid: str, parent: str | None, kind: str, when: float, **extra) -> dict:
    line = {
        "parentUuid": parent,
        "isSidechain": False,
        "userType": "external",
        "cwd": CWD,
        "sessionId": SESSION,
        "version": VERSION,
        "gitBranch": "main",
        "type": kind,
        "uuid": uuid,
        "timestamp": ts(when),
        "entrypoint": "cli",
    }
    line.update(extra)
    return line


def usage(inp: int, out: int, cr: int, cc: int, thinking: int | None = None) -> dict:
    d = {
        "input_tokens": inp,
        "cache_creation_input_tokens": cc,
        "cache_read_input_tokens": cr,
        "cache_creation": {"ephemeral_5m_input_tokens": cc, "ephemeral_1h_input_tokens": 0},
        "output_tokens": out,
        "service_tier": "standard",
        "inference_geo": "global",
        "iterations": [
            {"type": "message", "model": None, "input_tokens": inp, "output_tokens": out,
             "cache_read_input_tokens": cr, "cache_creation_input_tokens": cc}
        ],
        "fallback_credit": None,
    }
    if thinking is not None:
        d["output_tokens_details"] = {"thinking_tokens": thinking}
    return d


def assistant(uuid: str, parent: str, when: float, mid: int, block: dict, use: dict, *,
              stop_reason: str = "tool_use", model: str = "claude-fable-5-1", block_index: int = 0,
              **extra) -> dict:
    return envelope(
        uuid, parent, "assistant", when,
        message={
            "id": msg(mid), "type": "message", "role": "assistant", "model": model,
            "content": [block], "stop_reason": stop_reason, "stop_sequence": None,
            "usage": use, "container": None, "context_management": None,
        },
        requestId=req(mid), apiBlockIndex=block_index, effort="high", perTurnEffort="high",
        thinkingDisplay="updates", promptCacheRequested="1h", **extra,
    )


def session_lines() -> list[dict]:
    prompt1 = redact("Please add a unit test for the parser and run the suite.")
    prompt2 = redact("Great, now update the README to mention it.")
    reply1 = redact("I'll add the test next to the existing ones and run it.")
    reply2 = redact("Done: the test passes and the README now documents the parser.")
    reply3 = redact("README updated with a short section on the parser.")
    tool_out = redact("3 passed in 0.42s")
    summary = redact("The user asked for a parser test; it was added and passes.")
    handback = redact("Subagent report: fixtures generated, 2 files written.")

    lines: list[dict] = [
        # side-channel lines (never chain nodes)
        {"type": "mode", "mode": "default", "sessionId": SESSION},
        {"type": "permission-mode", "permissionMode": "default", "sessionId": SESSION},
        {"type": "session-owner", "accountUuid": "22222222-2222-4222-8222-222222222222",
         "organizationUuid": "33333333-3333-4333-8333-333333333333", "sessionId": SESSION},
        # turn 1 ------------------------------------------------------------------
        envelope(u(1), None, "user", 0.0, message={"role": "user", "content": prompt1},
                 promptId=P1, promptSource="typed", turnOrigin="human",
                 origin={"kind": "user"}, turnPosition={"promptIndex": 0, "turnIndex": 0},
                 permissionMode="default", slug="xxxxxxxxxxxxxxxxxxxxxx",
                 futureUnknownKey={"ignored": True}),
        # attachment with timestamp BEFORE its parent (observed user -> attachment inversion)
        envelope(u(2), u(1), "attachment", -0.4, isMeta=True,
                 attachment={"type": "date", "date": "0000-00-00"},
                 rendered=[{"type": "text", "content": redact("Today's date is 2026-11-08.")}],
                 renderedRole="user"),
        {"type": "api-request", "id": "api-0001", "timestamp": ts(0.5), "version": VERSION,
         "querySource": "main", "shapeHash": "0" * 16,
         "params": {"model": "claude-fable-5-1", "max_tokens": 32000},
         "keep": [], "tail": [], "cacheControl": [], "requestDigest": "0" * 64, "base": None},
        {"type": "api-request-blob", "hash": "0" * 64,
         "message": {"role": "system", "content": redact("You are a helpful assistant. Never leak this.")}},
        {"type": "api-request-shape", "shapeHash": "0" * 16,
         "shape": {"system": [{"len": 12}], "tools": [{"name": "Bash"}, {"name": "Agent"}]}},
        # one API message, three content blocks -> three assistant lines, identical usage
        assistant(u(3), u(2), 1.0, 1, {"type": "thinking", "thinking": "", "signature": "S" * 600},
                  usage(32, 1200, 180000, 1500, thinking=400), block_index=0, thinkingDurationMs=1800),
        assistant(u(4), u(3), 1.1, 1, {"type": "text", "text": reply1},
                  usage(32, 1200, 180000, 1500, thinking=400), block_index=1),
        assistant(u(5), u(4), 1.2, 1,
                  {"type": "tool_use", "id": toolu(1), "name": "Bash",
                   "input": {"command": redact("python3 -m pytest -q"), "description": redact("Run tests")},
                   "caller": {"type": "direct"}},
                  usage(32, 1200, 180000, 1500, thinking=400), block_index=2, attributionSkill="run-tests"),
        envelope(u(6), u(5), "user", 3.0,
                 message={"role": "user", "content": [
                     {"type": "tool_result", "tool_use_id": toolu(1), "content": tool_out, "is_error": False}]},
                 promptId=P1, sourceToolAssistantUUID=u(5),
                 toolUseResult={"stdout": tool_out, "stderr": "", "interrupted": False,
                                "isImage": False, "noOutputExpected": False, "returnCodeInterpretation": "success",
                                "persistedOutputPath": f"{CWD}/../.claude/projects/-home-user-project/{SESSION}/tool-results/{toolu(1)}.txt",
                                "persistedOutputSize": 2048},
                 permissionDecision={"decision": "accept", "source": "config", "reasonType": "other"}, sandboxed=False),
        # Agent launch + completion carrying the subagent reference
        assistant(u(7), u(6), 3.5, 2,
                  {"type": "tool_use", "id": toolu(2), "name": "Agent",
                   "input": {"description": redact("Make fixtures"), "prompt": redact("Generate the golden fixtures please."),
                             "subagent_type": "general-purpose"},
                   "caller": {"type": "direct"}},
                  usage(32, 300, 181000, 900, thinking=0)),
        envelope(u(8), u(7), "user", 9.0,
                 message={"role": "user", "content": [
                     {"type": "tool_result", "tool_use_id": toolu(2), "content": handback, "is_error": False}]},
                 promptId=P1, sourceToolAssistantUUID=u(7),
                 toolUseResult={"agentId": AGENT, "agentType": "general-purpose", "status": "completed",
                                "isAsync": False, "totalDurationMs": 5400, "totalTokens": 50321, "totalToolUseCount": 1,
                                "toolStats": {"Write": 1}, "toolsUsed": ["Write"], "resolvedModel": "claude-haiku-5-5",
                                "usage": usage(20, 800, 48000, 1500), "handbackReport": handback}),
        assistant(u(9), u(8), 10.0, 3, {"type": "text", "text": reply2},
                  usage(32, 90, 182000, 400, thinking=0), stop_reason="end_turn"),
        envelope(u(10), u(9), "system", 10.2, subtype="turn_duration", durationMs=10200,
                 messageCount=9, pendingBackgroundAgentCount=0, isMeta=True, level="info"),
        {"type": "last-prompt", "leafUuid": u(9), "lastPrompt": prompt1, "explicit": True, "sessionId": SESSION},
        {"type": "ai-title", "aiTitle": redact("Parser unit test"), "sessionId": SESSION},
        {"type": "file-history-snapshot", "messageId": u(1), "snapshot": {"trackedFileBackups": {}}, "isSnapshotUpdate": False},
        # manual /compact (verified shape, section 13.2): new root + summary user line
        envelope(u(11), None, "system", 20.0, subtype="compact_boundary", isMeta=True, level="info",
                 content=redact("Conversation compacted"), logicalParentUuid=u(10),
                 compactMetadata={"trigger": "manual", "preTokens": 150000, "postTokens": 40000,
                                  "cumulativeDroppedTokens": 110000, "durationMs": 9000,
                                  "preservedMessages": {"anchorUuid": u(9), "uuids": [u(9)], "allUuids": [u(9)]},
                                  "preservedSegment": {"anchorUuid": u(9), "headUuid": u(1), "tailUuid": u(9)},
                                  "preCompactDiscoveredTools": [], "preCompactArtifactReadVersions": []}),
        envelope(u(12), u(11), "user", 20.5, message={"role": "user", "content": summary},
                 isCompactSummary=True, isVisibleInTranscriptOnly=True, promptId=P2),
        # turn 2 ------------------------------------------------------------------
        envelope(u(13), u(12), "user", 30.0, message={"role": "user", "content": [{"type": "text", "text": prompt2}]},
                 promptId=P2, promptSource="typed", turnOrigin="human", origin={"kind": "user"},
                 turnPosition={"promptIndex": 1, "turnIndex": 1}, permissionMode="default"),
        # a line type the parser has never seen: must be counted and skipped
        {"type": "future-thing", "sessionId": SESSION, "payload": {"x": 1}},
        # synthetic API error line (section 11.5)
        envelope(u(14), u(13), "assistant", 31.0,
                 message={"id": "44444444-4444-4444-8444-444444444444", "type": "message", "role": "assistant",
                          "model": "<synthetic>", "content": [{"type": "text", "text": redact("API Error: 529 overloaded")}],
                          "stop_reason": "stop_sequence", "stop_sequence": "",
                          "usage": usage(0, 0, 0, 0)},
                 isApiErrorMessage=True, error=redact("529 overloaded")),
        # streaming retry: two lines of the same message, timestamp stepping BACK (clock skew)
        assistant(u(15), u(14), 33.0, 4, {"type": "text", "text": reply3},
                  usage(32, 60, 182500, 100, thinking=0), stop_reason="end_turn", block_index=0),
        assistant(u(16), u(15), 32.4, 4, {"type": "text", "text": ""},
                  usage(32, 60, 182500, 100, thinking=0), stop_reason="end_turn", block_index=1),
        envelope(u(17), u(16), "system", 33.5, subtype="stop_hook_summary", isMeta=True, level="info",
                 hookCount=1, hookInfos=[{"command": redact("python3 /home/user/.claude/hook.py"), "durationMs": 120}],
                 hookErrors=[], preventedContinuation=False, hasOutput=False),
        {"type": "cost-state", "totalCostUSD": 0.91, "totalAPIDuration": 41000, "totalDuration": 60000,
         "totalLinesAdded": 12, "totalLinesRemoved": 1, "startTime": 1794136000000, "hasUnknownModelCost": False,
         "modelUsage": {"claude-fable-5-1": {"inputTokens": 128, "outputTokens": 1650, "cacheReadInputTokens": 725500,
                                              "cacheCreationInputTokens": 2900, "costUSD": 0.9, "thinkingTokens": 400,
                                              "webSearchRequests": 0},
                        "claude-haiku-4-5-20251001": {"inputTokens": 900, "outputTokens": 40, "cacheReadInputTokens": 0,
                                                      "cacheCreationInputTokens": 0, "costUSD": 0.01, "thinkingTokens": 0,
                                                      "webSearchRequests": 0}}},
    ]
    return lines


def subagent_lines() -> list[dict]:
    prompt = redact("Generate the golden fixtures please.")
    reply = redact("Writing the two fixture files now.")
    handback = redact("Subagent report: fixtures generated, 2 files written.")
    sa_usage = usage(20, 800, 48000, 1500, thinking=120)

    def env(uuid, parent, kind, when, **extra):
        line = envelope(uuid, parent, kind, when, **extra)
        line["isSidechain"] = True
        line["agentId"] = AGENT
        return line

    return [
        env(su(1), None, "user", 4.0, message={"role": "user", "content": prompt}, promptId=P1,
            promptSource="system", turnOrigin="human"),
        env(su(2), su(1), "attachment", 4.1, isMeta=True,
            attachment={"type": "session_context", "context": {"userEmail": "user@example.com"}},
            rendered=[{"type": "text", "content": redact("Session context for user@example.com")}]),
        {"type": "api-request-blob", "hash": "0" * 64,
         "message": {"role": "user", "content": redact("Never read this blob either.")}},
        {**env(su(3), su(2), "assistant", 5.0,
               message={"id": msg(100), "type": "message", "role": "assistant", "model": "claude-haiku-5-5",
                        "content": [{"type": "text", "text": reply}], "stop_reason": "tool_use",
                        "stop_sequence": None, "usage": sa_usage},
               requestId=req(100), apiBlockIndex=0)},
        {**env(su(4), su(3), "assistant", 5.1,
               message={"id": msg(100), "type": "message", "role": "assistant", "model": "claude-haiku-5-5",
                        "content": [{"type": "tool_use", "id": toolu(100), "name": "Write",
                                     "input": {"file_path": f"{CWD}/fixture.jsonl", "content": redact("{}")},
                                     "caller": {"type": "direct"}}],
                        "stop_reason": "tool_use", "stop_sequence": None, "usage": sa_usage},
               requestId=req(100), apiBlockIndex=1)},
        env(su(5), su(4), "user", 6.0,
            message={"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": toolu(100), "content": redact("File written."), "is_error": False}]},
            promptId=P1, sourceToolAssistantUUID=su(4),
            toolUseResult={"type": "create", "filePath": f"{CWD}/fixture.jsonl", "structuredPatch": []}),
        {**env(su(6), su(5), "assistant", 7.0,
               message={"id": msg(101), "type": "message", "role": "assistant", "model": "claude-haiku-5-5",
                        "content": [{"type": "tool_use", "id": toolu(101), "name": "SubagentHandback",
                                     "input": {"message": handback}, "caller": {"type": "direct"}}],
                        "stop_reason": "tool_use", "stop_sequence": None,
                        "usage": usage(20, 60, 49000, 200, thinking=0)},
               requestId=req(101), apiBlockIndex=0)},
        # subagent files end on a user line (the handback tool result), section 6
        env(su(7), su(6), "user", 8.0,
            message={"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": toolu(101), "content": redact("Handback delivered."), "is_error": False}]},
            promptId=P1, sourceToolAssistantUUID=su(6)),
    ]


def write_jsonl(path: Path, lines: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(l, separators=(",", ":")) + "\n" for l in lines))


def render() -> dict[Path, str]:
    """Every fixture file and its content, relative to PROJECT_DIR."""
    files = {
        Path(f"{SESSION}.jsonl"): "".join(json.dumps(l, separators=(",", ":")) + "\n" for l in session_lines()),
        Path(SESSION) / "subagents" / f"agent-{AGENT}.jsonl": "".join(
            json.dumps(l, separators=(",", ":")) + "\n" for l in subagent_lines()),
        # present on disk, never opened by the parser
        Path(SESSION) / "tool-results" / f"{toolu(1)}.txt": redact("3 passed in 0.42s\n") * 4,
        Path("memory") / "notes.md": "# memory\n\n" + redact("auto-memory note, not a transcript") + "\n",
    }
    return files


def main() -> int:
    for rel, content in render().items():
        path = PROJECT_DIR / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        print(path.relative_to(HERE))
    return 0


if __name__ == "__main__":
    sys.exit(main())
