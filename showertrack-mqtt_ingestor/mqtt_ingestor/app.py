import json
import logging
import os
import time
from typing import Any

import psycopg
from paho.mqtt import client as mqtt_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

MQTT_HOST = os.environ["MQTT_HOST"]
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USERNAME = os.environ["MQTT_USERNAME"]
MQTT_PASSWORD = os.environ["MQTT_PASSWORD"]

# Use wildcard subscriptions so device_id comes from the topic.
MQTT_TOPIC_TELEMETRY = os.environ.get("MQTT_TOPIC_TELEMETRY", "shower/+/telemetry")
MQTT_TOPIC_EVENT = os.environ.get("MQTT_TOPIC_EVENT", "shower/+/event")
MQTT_TOPIC_STATUS = os.environ.get("MQTT_TOPIC_STATUS", "shower/+/status")

PGHOST = os.environ["PGHOST"]
PGPORT = os.environ.get("PGPORT", "5432")
PGDATABASE = os.environ["PGDATABASE"]
PGUSER = os.environ["PGUSER"]
PGPASSWORD = os.environ["PGPASSWORD"]


def get_connection() -> psycopg.Connection:
    return psycopg.connect(
        host=PGHOST,
        port=PGPORT,
        dbname=PGDATABASE,
        user=PGUSER,
        password=PGPASSWORD,
    )


def wait_for_db() -> None:
    while True:
        try:
            with get_connection() as conn, conn.cursor() as cur:
                cur.execute("SELECT 1;")
            logging.info("Database reachable.")
            return
        except Exception as exc:
            logging.warning("Database not ready yet: %s", exc)
            time.sleep(5)


def extract_device_id(topic: str) -> str:
    parts = topic.split("/")
    if len(parts) != 3 or parts[0] != "shower":
        raise ValueError(f"Unexpected topic format: {topic}")
    return parts[1]


def get_latest_humidity(cur: psycopg.Cursor, device_id: str) -> float | None:
    cur.execute(
        """
        SELECT humidity_rh
        FROM telemetry_readings
        WHERE device_id = %s
          AND humidity_rh IS NOT NULL
        ORDER BY received_at DESC
        LIMIT 1
        """,
        (device_id,),
    )
    row = cur.fetchone()
    return row[0] if row else None


def update_open_session_from_telemetry(
    cur: psycopg.Cursor,
    device_id: str,
    humidity_rh: float | None,
) -> None:
    if humidity_rh is None:
        return

    cur.execute(
        """
        UPDATE shower_sessions
        SET peak_humidity_rh = GREATEST(
                COALESCE(peak_humidity_rh, %s),
                %s
            ),
            end_humidity_rh = %s
        WHERE device_id = %s
          AND ended_at IS NULL
        """,
        (humidity_rh, humidity_rh, humidity_rh, device_id),
    )


def close_stale_open_session(
    cur: psycopg.Cursor,
    device_id: str,
    ended_at,
) -> None:
    cur.execute(
        """
        SELECT session_id, started_at, timer_target_s
        FROM shower_sessions
        WHERE device_id = %s
          AND ended_at IS NULL
        ORDER BY started_at DESC
        LIMIT 1
        """,
        (device_id,),
    )
    row = cur.fetchone()
    if not row:
        return

    session_id, started_at, timer_target_s = row
    duration_s = int((ended_at - started_at).total_seconds())
    timer_overrun_s = None
    if timer_target_s is not None:
        timer_overrun_s = max(duration_s - timer_target_s, 0)

    cur.execute(
        """
        UPDATE shower_sessions
        SET ended_at = %s,
            duration_s = %s,
            timer_overrun_s = %s,
            detection_method = 'stale_auto_close',
            confidence = 0.30,
            label = COALESCE(label, 'auto_closed_previous_open_session')
        WHERE session_id = %s
        """,
        (ended_at, duration_s, timer_overrun_s, session_id),
    )


def start_session(
    cur: psycopg.Cursor,
    device_id: str,
    received_at,
    payload: dict[str, Any],
) -> None:
    close_stale_open_session(cur, device_id, received_at)

    start_humidity = get_latest_humidity(cur, device_id)
    timer_target_s = payload.get("selected_duration_s")
    if timer_target_s is None:
        timer_target_s = payload.get("run_duration_s")

    cur.execute(
        """
        INSERT INTO shower_sessions (
            device_id,
            started_at,
            ended_at,
            duration_s,
            timer_target_s,
            timer_overrun_s,
            start_humidity_rh,
            peak_humidity_rh,
            end_humidity_rh,
            detection_method,
            confidence,
            label
        )
        VALUES (
            %s, %s, NULL, NULL, %s, NULL,
            %s, %s, %s,
            'timer_event', 0.95, NULL
        )
        """,
        (
            device_id,
            received_at,
            timer_target_s,
            start_humidity,
            start_humidity,
            start_humidity,
        ),
    )


def finish_session(
    cur: psycopg.Cursor,
    device_id: str,
    received_at,
    payload: dict[str, Any],
    detection_method: str,
    confidence: float,
) -> None:
    cur.execute(
        """
        SELECT session_id, started_at, timer_target_s
        FROM shower_sessions
        WHERE device_id = %s
          AND ended_at IS NULL
        ORDER BY started_at DESC
        LIMIT 1
        """,
        (device_id,),
    )
    row = cur.fetchone()
    if not row:
        return

    session_id, started_at, timer_target_s = row

    duration_s = payload.get("run_duration_s")
    if duration_s is None:
        duration_s = int((received_at - started_at).total_seconds())

    timer_overrun_s = None
    if timer_target_s is not None and duration_s is not None:
        timer_overrun_s = max(duration_s - timer_target_s, 0)

    cur.execute(
        """
        UPDATE shower_sessions
        SET ended_at = %s,
            duration_s = %s,
            timer_overrun_s = %s,
            detection_method = %s,
            confidence = %s
        WHERE session_id = %s
        """,
        (
            received_at,
            duration_s,
            timer_overrun_s,
            detection_method,
            confidence,
            session_id,
        ),
    )


def handle_telemetry(
    cur: psycopg.Cursor,
    topic: str,
    raw: str,
) -> None:
    payload = json.loads(raw)
    device_id = extract_device_id(topic)

    cur.execute(
        """
        INSERT INTO telemetry_readings (
            device_id,
            uptime_ms,
            timer_state,
            wifi_connected,
            mqtt_connected,
            temperature_c,
            humidity_rh,
            rotary_raw,
            selected_duration_s,
            run_duration_s,
            remaining_s,
            buzzer_active,
            raw_payload
        )
        VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb
        )
        RETURNING received_at
        """,
        (
            device_id,
            payload["uptime_ms"],
            payload["state"],
            payload.get("wifi_connected"),
            payload.get("mqtt_connected"),
            payload.get("temperature_c"),
            payload.get("humidity_rh"),
            payload.get("rotary_raw"),
            payload.get("selected_duration_s"),
            payload.get("run_duration_s"),
            payload.get("remaining_s"),
            payload.get("buzzer_active"),
            json.dumps(payload),
        ),
    )
    received_at = cur.fetchone()[0]

    update_open_session_from_telemetry(
        cur,
        device_id,
        payload.get("humidity_rh"),
    )

    logging.info(
        "Inserted telemetry for %s at %s",
        device_id,
        received_at,
    )


def handle_event(
    cur: psycopg.Cursor,
    topic: str,
    raw: str,
) -> None:
    payload = json.loads(raw)
    device_id = extract_device_id(topic)
    event_name = payload["event"]

    cur.execute(
        """
        INSERT INTO device_events (
            device_id,
            uptime_ms,
            event_name,
            timer_state,
            selected_duration_s,
            run_duration_s,
            raw_payload
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
        RETURNING received_at
        """,
        (
            device_id,
            payload.get("uptime_ms"),
            event_name,
            payload.get("state"),
            payload.get("selected_duration_s"),
            payload.get("run_duration_s"),
            json.dumps(payload),
        ),
    )
    received_at = cur.fetchone()[0]

    if event_name == "timer_started":
        start_session(cur, device_id, received_at, payload)

    elif event_name == "timer_finished":
        finish_session(
            cur,
            device_id,
            received_at,
            payload,
            detection_method="timer_event",
            confidence=0.95,
        )

    elif event_name == "alarm_silenced":
        # Fallback close only if a session is still open.
        finish_session(
            cur,
            device_id,
            received_at,
            payload,
            detection_method="alarm_silenced_fallback",
            confidence=0.75,
        )

    logging.info(
        "Inserted event %s for %s at %s",
        event_name,
        device_id,
        received_at,
    )


def handle_status(
    cur: psycopg.Cursor,
    topic: str,
    raw: str,
) -> None:
    device_id = extract_device_id(topic)
    status_value = raw.strip().lower()

    if status_value not in {"online", "offline"}:
        raise ValueError(f"Unexpected status payload: {raw}")

    payload = {"status": status_value}

    cur.execute(
        """
        INSERT INTO device_events (
            device_id,
            uptime_ms,
            event_name,
            timer_state,
            selected_duration_s,
            run_duration_s,
            raw_payload
        )
        VALUES (%s, NULL, %s, NULL, NULL, NULL, %s::jsonb)
        RETURNING received_at
        """,
        (
            device_id,
            status_value,
            json.dumps(payload),
        ),
    )
    received_at = cur.fetchone()[0]

    logging.info(
        "Inserted status %s for %s at %s",
        status_value,
        device_id,
        received_at,
    )


def on_connect(client, userdata, flags, reason_code, properties=None):
    logging.info("Connected to MQTT with code %s", reason_code)
    client.subscribe(MQTT_TOPIC_TELEMETRY)
    client.subscribe(MQTT_TOPIC_EVENT)
    client.subscribe(MQTT_TOPIC_STATUS)


def on_message(client, userdata, msg):
    topic = msg.topic
    raw = msg.payload.decode("utf-8", errors="replace")

    logging.info("Message on %s: %s", topic, raw)

    try:
        with get_connection() as conn, conn.cursor() as cur:
            if topic.endswith("/telemetry"):
                handle_telemetry(cur, topic, raw)
            elif topic.endswith("/event"):
                handle_event(cur, topic, raw)
            elif topic.endswith("/status"):
                handle_status(cur, topic, raw)
            else:
                logging.warning("Ignoring unexpected topic: %s", topic)

            conn.commit()

    except Exception as exc:
        logging.exception("Failed to process message from topic %s: %s", topic, exc)


def main() -> None:
    wait_for_db()

    client = mqtt_client.Client(mqtt_client.CallbackAPIVersion.VERSION2)
    client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
    client.on_connect = on_connect
    client.on_message = on_message

    while True:
        try:
            logging.info("Connecting to MQTT broker at %s:%s ...", MQTT_HOST, MQTT_PORT)
            client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
            client.loop_forever()
        except Exception as exc:
            logging.warning("MQTT connection failed: %s", exc)
            time.sleep(5)


if __name__ == "__main__":
    main()