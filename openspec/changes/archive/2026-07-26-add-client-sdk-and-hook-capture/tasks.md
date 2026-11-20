# Tasks: add-client-sdk-and-hook-capture

## 1. Package scaffolding

- [x] 1.1 Create `client/` package (`ouroboros_client/`, `tests/`, `pyproject.toml`): hard dep `httpx` only, `[azure]` extra for `azure-identity`, `[dev]` extras for pytest; console script `ouroboros-hook`
- [x] 1.2 Implement config/constants module: spool dir, interactions dir, spool cap, ring size, TTL, freshness window — all env-overridable; `OUROBOROS_TELEMETRY=off` helper

## 2. Spool and sender

- [x] 2.1 Implement spool store: atomic per-event JSON files in `~/.ouroboros/spool/`, oldest-dropped cap, stale/fresh partitioning by freshness window
- [x] 2.2 Implement background sender: daemon thread nudged by `record()`, batches spool into POSTs (≤3s timeout, any 2xx = success, delete on success; stale batch separated and dropped on 400), all failures swallowed

## 3. Client API and auth

- [x] 3.1 Implement `TelemetryClient(api_url, scope, token_provider=None)` with `record(skill_name, skill_version, status, session_id, feedback, **optional)`: local validation, event_id/client_ts generation, spool write, sender nudge, opt-out no-op, never raises
- [x] 3.2 Implement token providers: `azure_token_provider(scope)` (lazy azure-identity import, cached credentials only, None on any failure) and `dev_token_provider(key_dir, ...)` compatible with the api JWKS layout

## 4. Hook capture and attach

- [x] 4.1 Implement `ouroboros-hook` entrypoint (stdlib only): parse stdin JSON, handle UserPromptSubmit (insert prompt by prompt_id) and Stop (complete pair), ring eviction, TTL cleanup, atomic writes, always exit 0, opt-out aware
- [x] 4.2 Implement interaction cache reader + attach in `record()`: newest complete unconsumed pair for session → `interaction` + schema_version 1.1, mark consumed; document the manual hooks settings.json wiring snippet in `client/README.md`

## 5. Tests (no cluster required)

- [x] 5.1 Local capture-server tests: fire-and-forget timing (record returns while server hangs), 2xx acceptance, spool-on-failure and drain-on-recovery, crash-resend tolerance (duplicate event_id), stale/fresh partitioning
- [x] 5.2 Auth tests: provider None → silent spool, no interactive prompts, dev provider produces Bearer header
- [x] 5.3 Hook tests: pairing via prompt_id, out-of-order/missing Stop, ring eviction, TTL cleanup, malformed input exits 0, opt-out writes nothing
- [x] 5.4 Attach tests: v1.1 attach + consumed-once semantics, v1.0 fallback, opt-out end-to-end no-op

## 6. End-to-end verification

- [x] 6.1 `deploy/verify_sdk.sh`: pipe simulated UserPromptSubmit/Stop events through `ouroboros-hook`, `record()` an outcome with the dev token provider against the deployed stack, assert via the reporting API that a v1.1 row landed with the captured interaction and stamped identity
- [x] 6.2 Verify outage path live: point the client at a dead port, record, confirm spool; re-point at the cluster, record again, confirm drain and dedup
