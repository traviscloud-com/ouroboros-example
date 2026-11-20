# transcript-ingestion (delta)

## ADDED Requirements

### Requirement: Explicit opt-in transcript upload
The SDK SHALL provide `python3 -m ouroboros_client.transcript` (`upload <path>` or `upload --session <id>`, and `backfill --days N`) which uploads lines from Claude Code transcript JSONL files to `POST /v1/telemetry/transcripts`. By default it SHALL upload only metadata per line (`uuid`, `parentUuid`, `timestamp`, `type`, `model`, `usage`, tool-use ids and names, content lengths). Message content SHALL be included only with `--content`, which SHALL require a one-time acknowledgement recorded in `~/.ouroboros/consent.json` by the `consent` subcommand. The command SHALL never be invoked from a hook and SHALL do nothing when `OUROBOROS_TELEMETRY=off`.

#### Scenario: Default upload carries no text
- **WHEN** the command runs without `--content`
- **THEN** no uploaded line contains message text and each line carries usage and threading metadata

#### Scenario: Content requires consent
- **WHEN** `--content` is passed and no consent record exists
- **THEN** the command uploads nothing and points at the `consent` subcommand, which prints the disclosure and records consent only on explicit confirmation

### Requirement: Parser pinned to observed schema
The transcript parser SHALL be validated against golden fixtures that reproduce the structure observed in real transcripts (main session, subagent, tool-results) before the endpoint ships; the fixtures SHALL be synthesized, with no real lines committed. The parser SHALL skip and count lines that do not match the fixture-derived shape instead of guessing field meanings.

#### Scenario: Unknown line shape is skipped
- **WHEN** a transcript contains a line type absent from the fixtures
- **THEN** the line is not uploaded, the skip counter increments, and the rest of the file is processed

#### Scenario: Usage tokens attributed to turns
- **WHEN** an assistant line with `usage` and a `parentUuid` chain back to a user line is uploaded
- **THEN** a `transcript_lines` row exists and its tokens are joinable to the session's `turns` by `prompt_id`

### Requirement: Transcript data follows the sensitive retention clock
Uploaded transcript lines SHALL be classified `sensitive` when they carry content and `internal` otherwise, and the retention job SHALL null content columns after `RETENTION_SENSITIVE_DAYS`.

#### Scenario: Content expires
- **WHEN** a content-bearing transcript line is older than the sensitive retention window
- **THEN** its content column is null while usage metadata remains
