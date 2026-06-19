"""Static configuration for the ShowerTrack collector.

Pin assignments, MQTT topics, timing intervals and timer limits.
"""

import board

# --- Pins -------------------------------------------------------------------
# Grove rotary angle sensor (analog), button (digital, active HIGH) and
# buzzer (PWM) on the A0/A2/A4 signal lines
ROTARY_PIN = board.A0
BUTTON_PIN = board.A2
BUZZER_PIN = board.A4

# 4 digit TM1637 display is wired to the UART pins (TX/RX).  The driver
# auto-detects which order CLK/DIO are in
DISPLAY_CLK_PIN = board.TX
DISPLAY_DIO_PIN = board.RX

# AirLift FeatherWing (ESP32 over SPI).  These mirror SPIWIFI_SS (13),
# SPIWIFI_ACK (11), ESP32_RESETN (12) and ESP32_GPIO0 (-1 / not connected).
ESP32_CS_PIN = board.D13
ESP32_READY_PIN = board.D11
ESP32_RESET_PIN = board.D12
ESP32_GPIO0_PIN = None

# --- MQTT -------------------------------------------------------------------
STATUS_TOPIC = "shower/device1/status"
TELEMETRY_TOPIC = "shower/device1/telemetry"
EVENT_TOPIC = "shower/device1/event"
MQTT_CLIENT_ID = "showertrack-device1"
ONLINE_PAYLOAD = "online"
OFFLINE_PAYLOAD = "offline"
MQTT_KEEPALIVE_S = 30

# --- Timing (milliseconds) --------------------------------------------------
TELEMETRY_INTERVAL_MS = 5000
WIFI_RECONNECT_INTERVAL_MS = 10000
MQTT_RECONNECT_INTERVAL_MS = 5000
DISPLAY_REFRESH_INTERVAL_MS = 200
BUTTON_DEBOUNCE_MS = 35
ROTARY_SAMPLE_INTERVAL_MS = 100

BUZZER_FREQUENCY_HZ = 2000
BUZZER_DUTY_ON = 0x8000  # 50% of 0xFFFF

# --- Timer limits -----------------------------------------------------------
MIN_DURATION_SECONDS = 60
MAX_DURATION_SECONDS = 1200
DURATION_STEP_SECONDS = 30

# ADC scaling: CircuitPython's analogio.AnalogIn is 16-bit (0..65535)
# regardless of hardware resolution.  The original used a 10-bit reading
# (analogReadResolution(10) -> 0..1023).  Shifting right by 6 reproduces the
# 10-bit value that the quantizer and the telemetry "rotary_raw" field
# expect, keeping the selection maths bit-for-bit identical.
ADC_SHIFT = 6
ADC_MAX = 1023
