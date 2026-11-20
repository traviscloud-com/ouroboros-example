# skill-template (delta)

## ADDED Requirements

### Requirement: Feedback-protocol skill ships in the plugin
The plugin SHALL include a `telemetry-reporting` skill (SKILL.md) that instructs Claude how to report skill outcomes through `ouroboros-client`: constructing the client, choosing `success` / `failure` / `user_corrected` (user_corrected whenever the user had to fix or redirect the result), populating `feedback.notes` and, verbatim, `feedback.user_corrections`, sourcing the stable `session_id`, and recording latency. The skill file SHALL be directly copyable as the authoring template for other skills.

#### Scenario: Skill teaches the full protocol
- **WHEN** the skill file is read
- **THEN** it covers client construction, all three statuses with selection guidance, feedback field population, session_id sourcing, and the fire-and-forget guarantee (reporting never blocks or errors the user's task)

### Requirement: Template leads with disclosure and opt-out
The skill SHALL begin with a disclosure section stating what is collected (outcome, feedback, captured prompt/response text), where it goes, its sensitivity classification, and the `OUROBOROS_TELEMETRY=off` opt-out — telemetry is disclosed, never covert (PR-2).

#### Scenario: Disclosure precedes protocol
- **WHEN** the skill file is read top to bottom
- **THEN** disclosure and opt-out appear before any reporting instructions
