-- Reporting authorization (design D12): role per Gateway-authenticated email.
-- ADMIN_EMAILS (env) bootstraps admins without a row; everyone else needs one.

CREATE TABLE IF NOT EXISTS principals (
    email      TEXT        PRIMARY KEY CHECK (email = lower(email)),
    role       TEXT        NOT NULL CHECK (role IN ('leadership', 'engineer', 'admin')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by TEXT        NOT NULL
);
