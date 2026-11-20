# event-storage (delta)

## ADDED Requirements

### Requirement: Interaction storage
The system SHALL persist the optional `interaction` object as a nullable JSONB column on the events table, added by an idempotent migration. Events without `interaction` store NULL. Interaction content is sensitive verbatim conversation text and SHALL be subject to the same classification and retention posture as `feedback.user_corrections`.

#### Scenario: Interaction blob round-trips
- **WHEN** a v1.1 event with `user_prompt` and `assistant_response` is persisted
- **THEN** querying the row returns the complete interaction object as submitted

#### Scenario: Absent interaction stored as NULL
- **WHEN** a v1.0 event (or a v1.1 event without `interaction`) is persisted
- **THEN** the row's interaction column is NULL

#### Scenario: Column migration is idempotent
- **WHEN** the schema script runs against a database created before the interaction column existed, then runs again
- **THEN** both runs succeed and existing rows are preserved with NULL interaction
