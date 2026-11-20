# reporting-auth (delta)

## ADDED Requirements

### Requirement: Gateway-trusted identity only
The reporting service SHALL treat a request as authenticated only when its peer address is within `TRUSTED_PROXY_CIDRS` and it carries `X-Auth-Request-Email`; any other request under `/v1/` or `/internal/` SHALL receive 401. Health endpoints and the SPA's static files stay open; `POST /v1/proposals/github-webhook` authenticates with its own HMAC signature instead, and `POST /internal/refresh` MAY alternatively present `INTERNAL_REFRESH_TOKEN`.

#### Scenario: Direct in-cluster call rejected
- **WHEN** a pod outside the trusted CIDRs calls `/v1/reports/overview` with a forged `X-Auth-Request-Email`
- **THEN** the service returns 401

#### Scenario: Gateway-forwarded call accepted
- **WHEN** oauth2-proxy forwards a request with `X-Auth-Request-Email: admin@example.com` from the Gateway CIDR
- **THEN** the request proceeds with that identity

### Requirement: Roles from principals with admin bootstrap
The service SHALL resolve the email to a role in `principals` (`leadership` | `engineer` | `admin`); emails listed in `ADMIN_EMAILS` SHALL be admins regardless of their row and SHALL NOT be changeable through the API; unknown emails SHALL receive 403. Admins SHALL manage principals through `/v1/principals` (also served at `/v1/settings/users`) and the `/settings/users` page; every change SHALL be written to `audit_log`.

#### Scenario: Unknown but Google-authenticated user
- **WHEN** a user on the Gateway allow-list but absent from `principals` and `ADMIN_EMAILS` opens the dashboard
- **THEN** every data endpoint returns 403 and the UI shows a request-access page telling the user to ask an Ouroboros admin for a role

#### Scenario: Admin grants engineer
- **WHEN** an admin adds `dev@example.com` as `engineer`
- **THEN** that user's next request resolves to `engineer` and an audit row records the grant

### Requirement: Server-side redaction by role
Fields classified `sensitive` (prompt text, response text, corrections, notes, tool content, session titles, compaction summaries, transcript content) SHALL be replaced server-side with `{"redacted": true, "length": n}` for `leadership`; `engineer` and `admin` receive verbatim values. Redaction SHALL apply uniformly to reports, analytics examples, timelines and exports.

#### Scenario: Leadership opens a session timeline
- **WHEN** a `leadership` principal requests `/v1/analytics/sessions/{id}/timeline`
- **THEN** turn entries carry lengths and redaction markers instead of prompt and response text

#### Scenario: Engineer sees verbatim
- **WHEN** an `engineer` requests the same timeline
- **THEN** prompt and response text are present

### Requirement: Identity endpoint for the UI
The service SHALL expose `GET /v1/me` returning `{email, role, landing, via, features}` so the SPA can route by persona without inferring role client-side and can show whether the analyst and PR creation are configured.

#### Scenario: Landing by role
- **WHEN** a `leadership` principal calls `/v1/me`
- **THEN** `landing` is `/overview`; for `engineer` it is `/engineering`
