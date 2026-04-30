-- ══════════════════════════════════════════════════════════════════════════════
-- Loan Wizard – PostgreSQL Schema
-- Append-only audit tables for RBI V-CIP compliance
-- ══════════════════════════════════════════════════════════════════════════════

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ── Sessions ──────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sessions (
    call_id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    session_token   UUID NOT NULL UNIQUE,
    room_id         TEXT NOT NULL,                  -- VideoSDK room ID
    customer_phone  TEXT,
    campaign_id     TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at        TIMESTAMPTZ,
    final_stage     TEXT,
    recording_url   TEXT,                           -- VideoSDK recording URL
    s3_key          TEXT                            -- After S3 archive
);

-- ── Audit log (append-only, no UPDATE/DELETE) ─────────────────────────────────
CREATE TABLE IF NOT EXISTS audit_log (
    id              BIGSERIAL PRIMARY KEY,
    call_id         UUID NOT NULL REFERENCES sessions(call_id),
    event_type      TEXT NOT NULL,
    stage           TEXT,
    agent           TEXT,
    payload         JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Prevent updates/deletes on audit log (RBI WORM requirement)
CREATE RULE no_update_audit AS ON UPDATE TO audit_log DO INSTEAD NOTHING;
CREATE RULE no_delete_audit AS ON DELETE TO audit_log DO INSTEAD NOTHING;

-- ── Conversation log ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS conversation_log (
    id              BIGSERIAL PRIMARY KEY,
    call_id         UUID NOT NULL REFERENCES sessions(call_id),
    stage           TEXT NOT NULL,
    utterance       TEXT NOT NULL,
    stt_transcript  TEXT NOT NULL,
    stt_confidence  FLOAT NOT NULL,
    agent           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Offers ────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS offers (
    id                  BIGSERIAL PRIMARY KEY,
    call_id             UUID NOT NULL REFERENCES sessions(call_id),
    eligible_amount     NUMERIC(12, 2),
    interest_rate       FLOAT,
    selected_tenure     INT,
    emi                 NUMERIC(10, 2),
    acceptance_status   TEXT,
    accepted_at         TIMESTAMPTZ,
    upi_ref             TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Indexes ───────────────────────────────────────────────────────────────────
CREATE INDEX idx_sessions_token    ON sessions(session_token);
CREATE INDEX idx_audit_call        ON audit_log(call_id);
CREATE INDEX idx_conversation_call ON conversation_log(call_id);
CREATE INDEX idx_offers_call       ON offers(call_id);