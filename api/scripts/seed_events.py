"""Seed the ingestion API with a realistic mix of v1.0 and v1.1 telemetry.

Distribution (known totals, so report math can be asserted):
  SQL_Generator  12 events: 9 success / 2 user_corrected / 1 failure — mostly happy
  Doc_Writer      6 events: 5 success / 1 user_corrected            — content
  K8s_Helper      6 events: 1 success / 2 user_corrected / 3 failure — frustrated

Usage: python scripts/dev_token.py must work (dev JWKS present); then
  python scripts/seed_events.py --key-dir ../.dev-keys [--base-url http://127.0.0.1:80] [--host ouroboros.local]
"""

import argparse
import random
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx

from dev_token import mint

USERS = [
    ("11111111-aaaa-4aaa-8aaa-111111111111", "alice@ouroboros.local"),
    ("22222222-bbbb-4bbb-8bbb-222222222222", "bob@ouroboros.local"),
]

HAPPY_PROMPTS = [
    "generate a monthly revenue query, this tool is great",
    "please write the join for orders and customers — thanks, works well",
    "nice, exactly what I needed, one more query please",
]
NEUTRAL_PROMPTS = [
    "write a query for weekly active users",
    "draft the deployment doc section",
]
FRUSTRATED_PROMPTS = [
    "this is wrong again, the manifest keeps failing to apply",
    "no — still broken, why does it keep using the wrong namespace",
    "terrible, third try and the probe config is still invalid",
]
CORRECTIONS = [
    "actually use a LEFT JOIN so unmatched rows are kept",
    "the namespace should be ouroboros, not default",
    "wrong table — use events, not event_log",
]

# (skill, status, mood) tuples defining the seeded story
PLAN = (
    [("SQL_Generator", "success", "happy")] * 9
    + [("SQL_Generator", "user_corrected", "neutral")] * 2
    + [("SQL_Generator", "failure", "neutral")]
    + [("Doc_Writer", "success", "neutral")] * 5
    + [("Doc_Writer", "user_corrected", "neutral")]
    + [("K8s_Helper", "success", "neutral")]
    + [("K8s_Helper", "user_corrected", "frustrated")] * 2
    + [("K8s_Helper", "failure", "frustrated")] * 3
)


def build_event(rng, skill, status, mood, session_id):
    feedback = {"notes": {"happy": "worked on the first try", "neutral": "completed",
                          "frustrated": "user gave up after retries"}[mood]}
    interaction = None
    if mood == "happy":
        interaction = {"user_prompt": rng.choice(HAPPY_PROMPTS),
                       "assistant_response": "Here is the query you asked for."}
    elif mood == "frustrated":
        interaction = {"user_prompt": rng.choice(FRUSTRATED_PROMPTS),
                       "assistant_response": "Apologies — here is a corrected manifest."}
    elif rng.random() < 0.5:
        interaction = {"user_prompt": rng.choice(NEUTRAL_PROMPTS)}
    if status == "user_corrected":
        feedback["user_corrections"] = rng.choice(CORRECTIONS)
    event = {
        "event_id": str(uuid.uuid4()),
        "session_id": session_id,
        "client_ts": datetime.now(timezone.utc).isoformat(),
        "skill_name": skill,
        "skill_version": "1.0.0",
        "status": status,
        "latency_ms": rng.randint(200, 4000),
        "feedback": feedback,
    }
    if status == "failure":
        event["error_code"] = "E_SKILL_FAILED"
    if interaction:
        event["interaction"] = interaction
    return event


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key-dir", type=Path, default=Path(".dev-keys"))
    parser.add_argument("--base-url", default="http://127.0.0.1:80")
    parser.add_argument("--host", default="ouroboros.local")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    rng = random.Random(args.seed)
    sessions = {
        (skill, user[1]): str(uuid.uuid4())
        for skill in {p[0] for p in PLAN}
        for user in USERS
    }

    sent = 0
    with httpx.Client(base_url=args.base_url, headers={"Host": args.host}, timeout=10) as client:
        for skill, status, mood in PLAN:
            oid, upn = rng.choice(USERS)
            event = build_event(rng, skill, status, mood, sessions[(skill, upn)])
            body = {
                "schema_version": "1.1" if "interaction" in event else "1.0",
                "events": [event],
            }
            token = mint(args.key_dir, oid=oid, upn=upn)
            for attempt in range(4):
                try:
                    resp = client.post(
                        "/v1/telemetry/events",
                        json=body,
                        headers={"Authorization": f"Bearer {token}"},
                    )
                    resp.raise_for_status()
                    break
                except (httpx.TimeoutException, httpx.TransportError):
                    # Ingress endpoints can lag briefly after a rollout
                    if attempt == 3:
                        raise
            sent += 1
    print(f"Seeded {sent} events across {len({p[0] for p in PLAN})} skills, "
          f"{len(sessions)} sessions, {len(USERS)} users")


if __name__ == "__main__":
    main()
