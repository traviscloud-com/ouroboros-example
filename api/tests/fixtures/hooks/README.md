Synthesized hook-event batches (design D6 projections) for `test_hook_events.py`.
No real payloads or identifiers: fixed fake UUIDs, `user@example.com`, placeholder
paths. `client_ts` values are relative to 2026-01-01T00:00:00Z and are rebased to
"now" by the tests so the 7-day freshness window never bites.
