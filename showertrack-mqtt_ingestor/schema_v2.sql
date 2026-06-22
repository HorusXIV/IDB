DROP TABLE IF EXISTS shower_telemetry CASCADE;
DROP TABLE IF EXISTS shower_event CASCADE;
DROP TABLE IF EXISTS shower_status CASCADE;

DROP TABLE IF EXISTS telemetry_readings CASCADE;
DROP TABLE IF EXISTS device_events CASCADE;
DROP TABLE IF EXISTS shower_sessions CASCADE;

CREATE TABLE telemetry_readings (
	    id BIGSERIAL PRIMARY KEY,
	    device_id TEXT NOT NULL,
	    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

	    uptime_ms BIGINT NOT NULL,
	    timer_state TEXT NOT NULL,
	    wifi_connected BOOLEAN,
	    mqtt_connected BOOLEAN,

	    temperature_c DOUBLE PRECISION,
	    humidity_rh DOUBLE PRECISION,

	    rotary_raw INTEGER,
	    selected_duration_s INTEGER,
	    run_duration_s INTEGER,
	    remaining_s INTEGER,
	    buzzer_active BOOLEAN,

	    raw_payload JSONB NOT NULL
);

CREATE TABLE device_events (
	    id BIGSERIAL PRIMARY KEY,
	    device_id TEXT NOT NULL,
	    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

	    uptime_ms BIGINT,
	    event_name TEXT NOT NULL,
	    timer_state TEXT,
	    selected_duration_s INTEGER,
	    run_duration_s INTEGER,

	    raw_payload JSONB NOT NULL
);

CREATE TABLE shower_sessions (
	    session_id BIGSERIAL PRIMARY KEY,
	    device_id TEXT NOT NULL,

	    started_at TIMESTAMPTZ NOT NULL,
	    ended_at TIMESTAMPTZ,
	    duration_s INTEGER,
	    timer_target_s INTEGER,
	    timer_overrun_s INTEGER,

	    start_humidity_rh DOUBLE PRECISION,
	    peak_humidity_rh DOUBLE PRECISION,
	    end_humidity_rh DOUBLE PRECISION,

	    detection_method TEXT NOT NULL,
	    confidence DOUBLE PRECISION,
	    label TEXT
);

CREATE INDEX telemetry_readings_device_received_idx
    ON telemetry_readings (device_id, received_at DESC);

CREATE INDEX device_events_device_received_idx
    ON device_events (device_id, received_at DESC);

CREATE INDEX shower_sessions_device_started_idx
    ON shower_sessions (device_id, started_at DESC);

CREATE UNIQUE INDEX shower_sessions_one_open_per_device_idx
    ON shower_sessions (device_id)
    WHERE ended_at IS NULL;

GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO showertrack_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO showertrack_app;
