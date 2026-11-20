Synthesized OTLP/JSON payloads shaped after the OTLP receiver spike
for `test_otel.py`: string ints (`intValue`), `boolValue` vs the strings
`"true"`/`"false"`, `asDouble` delta sums, `event.name` attribute plus
`claude_code.<event>` body, `cost_usd_micros` next to `cost_usd`, and the
`anthropic.custom_base_url` attribute that must be dropped. No real identifiers.
