# skill-template

## Purpose

The feedback-protocol SKILL.md shipped in the plugin: what a telemetry-reporting skill must contain — disclosure before instructions, the full reporting protocol, and its role as the copyable authoring template for instrumenting other skills.

## Requirements

### Requirement: Feedback-protocol skill ships in the plugin
The plugin SHALL include a `telemetry-reporting` skill (SKILL.md) that instructs Claude how to report skill outcomes through `ouroboros-client`: running the `python3 -m ouroboros_client.report` command (configured from environment variables), choosing `success` / `failure` / `user_corrected` (user_corrected whenever the user had to fix or redirect the result), populating the notes and, verbatim, the user's corrections, sourcing the stable `session_id`, and recording latency. The skill file SHALL be directly copyable as the authoring template for other skills.

#### Scenario: Skill teaches the full protocol
- **WHEN** the skill file is read
- **THEN** it covers the report command and its environment configuration, all three statuses with selection guidance, feedback field population, session_id sourcing, and the fire-and-forget guarantee (reporting never blocks or errors the user's task)

### Requirement: Template leads with disclosure and opt-out
The skill SHALL begin with a disclosure section stating what is collected per data class (outcomes and feedback, turn text, session and tool metadata, tool content, instruction files, subagents and compaction, model switches, optional token and cost telemetry), each class's default and toggle, where it goes, which classes are sensitive, and the `OUROBOROS_TELEMETRY=off` opt-out — telemetry is disclosed, never covert.

#### Scenario: Disclosure precedes protocol
- **WHEN** the skill file is read top to bottom
- **THEN** disclosure and opt-out appear before any reporting instructions
