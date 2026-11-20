# sentiment-analysis

## Purpose

How user sentiment is derived from telemetry: deterministic offline scoring of user-authored text, blending with behavioral signals (corrections, failures), and aggregation into session- and skill-level rollups.

## Requirements

### Requirement: User-text sentiment scoring
The system SHALL compute a text sentiment score in [-1, 1] per event from user-authored content only — `interaction.user_prompt`, `feedback.user_corrections`, and `feedback.notes`. `interaction.assistant_response` SHALL never contribute to the score. Scoring SHALL be deterministic and fully offline (no network calls). Events with no user-authored text SHALL be marked as having no text score rather than defaulting to neutral.

#### Scenario: Negative user text scores negative
- **WHEN** an event's user_prompt or user_corrections contains clearly frustrated language (e.g. "this is wrong again, it keeps failing")
- **THEN** the event's text score is negative

#### Scenario: Assistant text never scored
- **WHEN** an event has an enthusiastic `assistant_response` but no user-authored text
- **THEN** the event has no text score

#### Scenario: Deterministic scoring
- **WHEN** the same event content is scored twice
- **THEN** both runs produce the identical score

### Requirement: Behavioral signal blending
The system SHALL blend the text score with behavioral signals into an event sentiment score bounded [-1, 1]: `failure` and `user_corrected` statuses contribute negative signal and `success` mildly positive signal, with weights configurable via environment. For events with user text, blended = 0.6 × text + 0.4 × behavioral (default weights); for events without user text the behavioral signal stands alone.

#### Scenario: Correction drags sentiment down
- **WHEN** two events have identical positive user text but one has status `user_corrected` and the other `success`
- **THEN** the user_corrected event's blended score is lower

#### Scenario: Blended score stays bounded
- **WHEN** an event combines maximally negative text and a failure status
- **THEN** the blended score is >= -1

### Requirement: Session and skill rollups
The system SHALL aggregate event sentiment into session-level and skill-level rollups: mean blended score, count of scored events, text-score coverage (share of events with user text), and counts of `user_corrected` and `failure` events.

#### Scenario: Session rollup reflects its events
- **WHEN** a session contains events with blended scores 0.5, -0.5, and -0.6 including one failure and one correction
- **THEN** the session rollup reports a mean near -0.2 with failure count 1 and correction count 1

#### Scenario: Skill rollup aggregates across sessions
- **WHEN** a skill has events across multiple sessions and users
- **THEN** the skill rollup reports the mean blended score and correction/failure counts across all of them
