-- The showertrack_app role is created by 000_app_user.sh (password from the
-- environment) before this script runs, so no credential lives in SQL.

CREATE TABLE IF NOT EXISTS shower_telemetry (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL,
    device_id TEXT NOT NULL,
    temperature_c DOUBLE PRECISION,
    humidity_rh DOUBLE PRECISION,
    timer_remaining_s INTEGER,
    timer_running BOOLEAN,
    buzzing BOOLEAN,
    raw_payload JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS shower_event (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL,
    device_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    raw_payload JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS shower_status (
    device_id TEXT PRIMARY KEY,
    last_seen TIMESTAMPTZ NOT NULL,
    online BOOLEAN NOT NULL,
    raw_payload JSONB NOT NULL
);

GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO showertrack_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO showertrack_app;
