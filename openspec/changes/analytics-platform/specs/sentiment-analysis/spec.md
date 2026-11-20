# sentiment-analysis (delta)

## MODIFIED Requirements

### Requirement: Session and skill rollups
The system SHALL aggregate event sentiment into session-level, skill-level and daily rollups (mean blended score, count of scored events, text-score coverage, counts of `user_corrected` and `failure` events). The daily per-skill rollup SHALL be persisted in `analytics.sentiment_daily` by the scheduler, and `/v1/analytics/sentiment` SHALL read from that table with `from`/`to`/`compare_to` on event time instead of scoring per request. The legacy `/v1/reports/sentiment` SHALL keep computing its per-skill and per-session rollups on request, filtered on event time, behind a short in-process cache.

#### Scenario: Session rollup reflects its events
- **WHEN** a session contains events with blended scores 0.5, -0.5, and -0.6 including one failure and one correction
- **THEN** the session rollup reports a mean near -0.2 with failure count 1 and correction count 1

#### Scenario: Skill rollup aggregates across sessions
- **WHEN** a skill has events across multiple sessions and users
- **THEN** the skill rollup reports the mean blended score and correction/failure counts across all of them

#### Scenario: Daily rollup served from the persisted table
- **WHEN** `/v1/analytics/sentiment?from&to` is requested
- **THEN** the response is produced from `sentiment_daily` without scoring text at request time

## ADDED Requirements

### Requirement: Correction theme clustering
The service SHALL cluster negative-sentiment user corrections per week into themes using keyword/bigram frequency, store them with counts and example event ids, and SHALL keep any LLM-based scoring behind `THEMES_LLM` (default off).

#### Scenario: Weekly themes
- **WHEN** a week has 30 corrections of which 12 mention "wrong file path"
- **THEN** a theme containing that bigram appears with count 12 and up to 5 example event ids
