"""ShowerTrack application orchestrator.

Holds all device state. Nothing here blocks for long: each ``loop()`` pass
services the rotary, button, timer, display, Wi-Fi, MQTT and telemetry in
turn.
"""

import time

import board
import analogio
import digitalio
import pwmio

from . import config
from .clock import ticks_ms
from .display import FourDigitDisplay
from .timer import TimerState

# Optional dependencies: the device must keep timing locally even when the
# radio stack or secrets are missing, so each import is guarded.
try:
    from secrets import secrets
except ImportError:
    secrets = None

try:
    import adafruit_sht4x
except ImportError:
    adafruit_sht4x = None

try:
    from adafruit_esp32spi import adafruit_esp32spi
    import adafruit_connection_manager
    import adafruit_minimqtt.adafruit_minimqtt as MQTT
    _RADIO_LIBS = True
except ImportError:
    _RADIO_LIBS = False


class ShowerTrack:
    def __init__(self):
        # --- hardware -------------------------------------------------------
        self.rotary = analogio.AnalogIn(config.ROTARY_PIN)

        self.button = digitalio.DigitalInOut(config.BUTTON_PIN)
        # Grove button modules are active HIGH and carry their own resistor
        # network, so we read a plain input with no internal pull.
        self.button.switch_to_input()

        # Active-buzzer tone via hardware PWM (see config note); silent until
        # duty is raised in _start_buzzer.
        self.buzzer = pwmio.PWMOut(
            config.BUZZER_PIN,
            frequency=config.BUZZER_FREQUENCY_HZ,
            duty_cycle=0,
            variable_frequency=False,
        )

        self.display = FourDigitDisplay(config.DISPLAY_CLK_PIN, config.DISPLAY_DIO_PIN)

        # --- timer state ----------------------------------------------------
        self.timer_state = TimerState.IDLE
        self.selected_duration_seconds = 300
        self.active_duration_seconds = 300
        self.timer_started_ms = 0
        self.timer_paused_remaining_seconds = 0

        # --- schedulers -----------------------------------------------------
        self.last_telemetry_ms = 0
        self.last_wifi_attempt_ms = 0
        self.last_mqtt_attempt_ms = 0
        self.last_display_refresh_ms = 0
        self.last_rotary_sample_ms = 0

        # --- connection bookkeeping ----------------------------------------
        self.boot_event_pending = True
        self.mqtt_has_connected_once = False
        self.last_reported_wifi_connected = None
        self.last_reported_mqtt_connected = False
        self.display_available = False
        self.display_swapped_pins = False

        # --- button debounce -----------------------------------------------
        self.button_raw_state = False
        self.button_stable_state = False
        self.button_last_change_ms = 0

        # --- rotary smoothing ----------------------------------------------
        self.rotary_smoothed = -1
        self.last_rotary_raw = 0

        # --- sensor ---------------------------------------------------------
        self.sensor = None
        self.sensor_available = False
        self.last_temperature_c = None  # None == JSON null
        self.last_humidity_rh = None

        # --- buzzer (telemetry flag) ---------------------------------------
        self.buzzer_active = False

        # --- connectivity ---------------------------------------------------
        self.esp = None
        self.mqtt = None
        self.mqtt_connected = False

    # =======================================================================
    # Setup
    # =======================================================================
    def setup(self):
        print("ShowerTrack booting")
        print("Display CLK pin=", config.DISPLAY_CLK_PIN)
        print("Display DIO pin=", config.DISPLAY_DIO_PIN)

        self._setup_display()
        self._setup_sensor()
        self._setup_radio()

        # Prime the telemetry timer so the first reading publishes as soon as
        # MQTT is available.
        self.last_telemetry_ms = ticks_ms() - config.TELEMETRY_INTERVAL_MS

    def _setup_display(self):
        self.display.configure_pins(config.DISPLAY_CLK_PIN, config.DISPLAY_DIO_PIN)
        self.display_available = self.display.begin()

        if not self.display_available:
            print("Display did not ACK on TX/RX order, trying swapped pins")
            self.display.configure_pins(config.DISPLAY_DIO_PIN, config.DISPLAY_CLK_PIN)
            self.display_available = self.display.begin()
            self.display_swapped_pins = self.display_available

        if self.display_available:
            order = "swapped RX/TX pins" if self.display_swapped_pins else "TX->CLK, RX->DIO"
            print("Display detected using", order)
            # Brief self-test, identical to the C++ boot sequence.
            self.display.show_time(0, True)
            time.sleep(0.2)
            self.display.show_time((59 * 60) + 59, True)
            time.sleep(0.3)
            self.display.show_time(self.selected_duration_seconds, True)
        else:
            print("Display did not ACK on either TX/RX pin order")

    def _setup_sensor(self):
        if adafruit_sht4x is None:
            print("adafruit_sht4x not installed, continuing without telemetry readings")
            return
        try:
            i2c = board.I2C()
            self.sensor = adafruit_sht4x.SHT4x(i2c)
            self.sensor.mode = adafruit_sht4x.Mode.NOHEAT_HIGHPRECISION
            self.sensor_available = True
            print("SHT41 detected")
        except Exception as err:  # noqa: BLE001 - any I2C failure means "absent"
            self.sensor_available = False
            print("SHT41 not detected, continuing without telemetry readings:", err)

    def _setup_radio(self):
        if not _RADIO_LIBS:
            print("Radio libraries missing, timer will continue locally")
            return
        if secrets is None:
            print("secrets.py missing, timer will continue locally")
            return

        # Bring up the AirLift ESP32 co-processor.  If it is absent the
        # firmware version read raises, and we degrade to local-only.
        try:
            spi = board.SPI()
            esp = adafruit_esp32spi.ESP_SPIcontrol(
                spi,
                digitalio.DigitalInOut(config.ESP32_CS_PIN),
                digitalio.DigitalInOut(config.ESP32_READY_PIN),
                digitalio.DigitalInOut(config.ESP32_RESET_PIN),
                config.ESP32_GPIO0_PIN,
            )
            _ = esp.firmware_version  # confirms the module is responding
            self.esp = esp
            try:
                esp.disconnect()  # clear any AP the ESP joined on its own
            except Exception:  # noqa: BLE001
                pass
            print("AirLift detected")
        except Exception as err:  # noqa: BLE001
            self.esp = None
            print("AirLift not detected, timer will continue locally:", err)
            return

        # Build the MQTT client
        try:
            pool = adafruit_connection_manager.get_radio_socketpool(self.esp)
            ssl_context = adafruit_connection_manager.get_radio_ssl_context(self.esp)
            self.mqtt = MQTT.MQTT(
                broker=secrets["mqtt_host"],
                port=secrets["mqtt_port"],
                username=secrets.get("mqtt_username"),
                password=secrets.get("mqtt_password"),
                client_id=config.MQTT_CLIENT_ID,
                socket_pool=pool,
                ssl_context=ssl_context,
                is_ssl=False,
                keep_alive=config.MQTT_KEEPALIVE_S,
            )
            # Last will: broker publishes the retained "offline" status if we
            # drop without a clean disconnect.
            self.mqtt.will_set(
                config.STATUS_TOPIC, config.OFFLINE_PAYLOAD, qos=0, retain=True
            )
        except Exception as err:  # noqa: BLE001
            print("MQTT client setup failed:", err)
            self.mqtt = None

    # =======================================================================
    # Main loop
    # =======================================================================
    def loop(self):
        self.read_rotary_selection()
        self.handle_button()
        self.update_timer_state()
        self.update_display()

        self.ensure_wifi_connected()
        self.ensure_mqtt_connected()
        self.report_connection_changes()
        self.service_mqtt()

        self.service_telemetry()

    def run(self):
        self.setup()
        while True:
            self.loop()

    # =======================================================================
    # Rotary selection
    # =======================================================================
    def _quantize_duration_from_rotary(self, raw):
        steps = (
            (config.MAX_DURATION_SECONDS - config.MIN_DURATION_SECONDS)
            // config.DURATION_STEP_SECONDS
        ) + 1
        clamped = min(max(raw, 0), config.ADC_MAX)
        # ((raw * (steps-1)) + half) // full  -> rounded step index.
        step_index = ((clamped * (steps - 1)) + (config.ADC_MAX // 2)) // config.ADC_MAX
        return config.MIN_DURATION_SECONDS + (step_index * config.DURATION_STEP_SECONDS)

    def read_rotary_selection(self):
        now = ticks_ms()
        if (now - self.last_rotary_sample_ms) < config.ROTARY_SAMPLE_INTERVAL_MS:
            return
        self.last_rotary_sample_ms = now

        # 16-bit AnalogIn -> 10-bit reading to match the original firmware.
        raw = self.rotary.value >> config.ADC_SHIFT
        self.last_rotary_raw = raw

        if self.rotary_smoothed < 0:
            self.rotary_smoothed = raw
        else:
            # Exponential moving average, weight 7/8 to the old value.
            self.rotary_smoothed = ((self.rotary_smoothed * 7) + raw) // 8

        self.selected_duration_seconds = self._quantize_duration_from_rotary(
            self.rotary_smoothed
        )

    # =======================================================================
    # Button
    # =======================================================================
    def _button_pressed_edge(self):
        raw_pressed = self.button.value  # active HIGH
        now = ticks_ms()

        if raw_pressed != self.button_raw_state:
            self.button_raw_state = raw_pressed
            self.button_last_change_ms = now

        if (now - self.button_last_change_ms) < config.BUTTON_DEBOUNCE_MS:
            return False

        if self.button_stable_state != self.button_raw_state:
            self.button_stable_state = self.button_raw_state
            if self.button_stable_state:
                return True

        return False

    def handle_button(self):
        if not self._button_pressed_edge():
            return

        if self.timer_state == TimerState.ALARM:
            self.silence_alarm()
            return

        if self.timer_state == TimerState.IDLE:
            print("Button press: starting timer")
            self.start_timer()
            return

        if self.timer_state == TimerState.RUNNING:
            print("Button press: pausing timer")
            self.pause_timer()
            return

        if self.timer_state == TimerState.PAUSED:
            print("Button press: resuming timer")
            self.resume_timer()

    # =======================================================================
    # Timer state machine
    # =======================================================================
    def remaining_seconds(self):
        if self.timer_state == TimerState.PAUSED:
            return self.timer_paused_remaining_seconds
        if self.timer_state != TimerState.RUNNING:
            return 0

        elapsed = (ticks_ms() - self.timer_started_ms) // 1000
        if elapsed >= self.active_duration_seconds:
            return 0
        return self.active_duration_seconds - elapsed

    def update_timer_state(self):
        if self.timer_state == TimerState.RUNNING and self.remaining_seconds() == 0:
            self.finish_timer()

    def start_timer(self):
        self.active_duration_seconds = self.selected_duration_seconds
        self.timer_paused_remaining_seconds = 0
        self.timer_started_ms = ticks_ms()
        self.timer_state = TimerState.RUNNING
        self._stop_buzzer()

        extra = (
            '"run_duration_s":' + str(self.active_duration_seconds)
            + ',"selected_duration_s":' + str(self.selected_duration_seconds)
        )
        self.publish_event("timer_started", extra)

    def pause_timer(self):
        self.timer_paused_remaining_seconds = self.remaining_seconds()
        if self.timer_paused_remaining_seconds == 0:
            self.finish_timer()
            return
        self.timer_state = TimerState.PAUSED

        extra = (
            '"run_duration_s":' + str(self.active_duration_seconds)
            + ',"remaining_s":' + str(self.timer_paused_remaining_seconds)
        )
        self.publish_event("timer_paused", extra)

    def resume_timer(self):
        elapsed_before_pause = (
            self.active_duration_seconds - self.timer_paused_remaining_seconds
        )
        self.timer_started_ms = ticks_ms() - (elapsed_before_pause * 1000)
        self.timer_state = TimerState.RUNNING

        extra = (
            '"run_duration_s":' + str(self.active_duration_seconds)
            + ',"remaining_s":' + str(self.timer_paused_remaining_seconds)
        )
        self.publish_event("timer_resumed", extra)
        self.timer_paused_remaining_seconds = 0

    def finish_timer(self):
        self.timer_state = TimerState.ALARM
        self._start_buzzer()
        self.publish_event(
            "timer_finished", '"run_duration_s":' + str(self.active_duration_seconds)
        )

    def silence_alarm(self):
        self._stop_buzzer()
        self.timer_state = TimerState.IDLE
        self.timer_paused_remaining_seconds = 0
        self.publish_event("alarm_silenced")

    # =======================================================================
    # Buzzer (hardware PWM tone)
    # =======================================================================
    def _start_buzzer(self):
        self.buzzer_active = True
        self.buzzer.duty_cycle = config.BUZZER_DUTY_ON

    def _stop_buzzer(self):
        self.buzzer_active = False
        self.buzzer.duty_cycle = 0

    # =======================================================================
    # Display
    # =======================================================================
    def update_display(self):
        if not self.display_available:
            return

        now = ticks_ms()
        if (now - self.last_display_refresh_ms) < config.DISPLAY_REFRESH_INTERVAL_MS:
            return
        self.last_display_refresh_ms = now

        colon_on = ((now // 1000) % 2) == 0

        if self.timer_state == TimerState.RUNNING:
            self.display.show_time(self.remaining_seconds(), colon_on)
        elif self.timer_state == TimerState.PAUSED:
            self.display.show_time(self.remaining_seconds(), False)
        elif self.timer_state == TimerState.ALARM:
            self.display.show_time(0, colon_on)
        else:
            self.display.show_time(self.selected_duration_seconds, True)

    # =======================================================================
    # Sensor
    # =======================================================================
    def read_sensor(self):
        if not self.sensor_available:
            self.last_temperature_c = None
            self.last_humidity_rh = None
            return
        try:
            temperature, humidity = self.sensor.measurements
        except Exception:  # noqa: BLE001 - a failed read publishes null
            self.last_temperature_c = None
            self.last_humidity_rh = None
            return
        self.last_temperature_c = temperature
        self.last_humidity_rh = humidity

    # =======================================================================
    # Connectivity
    # =======================================================================
    def ensure_wifi_connected(self):
        if self.esp is None:
            return
        if self.esp.is_connected:
            return

        now = ticks_ms()
        if (now - self.last_wifi_attempt_ms) < config.WIFI_RECONNECT_INTERVAL_MS:
            return
        self.last_wifi_attempt_ms = now

        print("Connecting to Wi-Fi...")
        try:
            # Non-blocking association: kick off the join and let later loop
            # passes observe is_connected, rather than blocking in connect_AP.
            self.esp.wifi_set_passphrase(
                secrets["wifi_ssid"], secrets["wifi_password"]
            )
        except Exception as err:  # noqa: BLE001
            print("Wi-Fi connect attempt failed:", err)

    def ensure_mqtt_connected(self):
        if self.mqtt is None:
            return

        if self.esp is None or not self.esp.is_connected:
            if self.mqtt_connected:
                try:
                    self.mqtt.disconnect()
                except Exception:  # noqa: BLE001
                    pass
                self.mqtt_connected = False
            return

        if self.mqtt_connected:
            return

        now = ticks_ms()
        if (now - self.last_mqtt_attempt_ms) < config.MQTT_RECONNECT_INTERVAL_MS:
            return
        self.last_mqtt_attempt_ms = now

        print("Connecting to MQTT...")
        try:
            self.mqtt.connect()
        except Exception as err:  # noqa: BLE001
            print("MQTT connect failed:", err)
            self.mqtt_connected = False
            return

        self.mqtt_connected = True

        try:
            self.mqtt.publish(
                config.STATUS_TOPIC, config.ONLINE_PAYLOAD, retain=True, qos=0
            )
        except Exception as err:  # noqa: BLE001
            print("Status publish failed:", err)

        if self.boot_event_pending:
            self.publish_event("boot")
            self.boot_event_pending = False
        elif self.mqtt_has_connected_once:
            self.publish_event("mqtt_reconnected")

        self.mqtt_has_connected_once = True

    def report_connection_changes(self):
        wifi_connected = bool(self.esp is not None and self.esp.is_connected)
        if wifi_connected != self.last_reported_wifi_connected:
            self.last_reported_wifi_connected = wifi_connected
            print("Wi-Fi status changed:", "connected" if wifi_connected else "disconnected")
            if wifi_connected:
                try:
                    print("Wi-Fi connected, IP=", self.esp.pretty_ip(self.esp.ip_address))
                except Exception:  # noqa: BLE001
                    pass

        if self.mqtt_connected != self.last_reported_mqtt_connected:
            self.last_reported_mqtt_connected = self.mqtt_connected
            print("MQTT status changed:", "connected" if self.mqtt_connected else "disconnected")

    def service_mqtt(self):
        if not self.mqtt_connected or self.mqtt is None:
            return
        try:
            self.mqtt.loop(timeout=0.05)
        except Exception as err:  # noqa: BLE001
            print("MQTT loop error:", err)
            self.mqtt_connected = False

    # =======================================================================
    # Telemetry / events
    # =======================================================================
    @staticmethod
    def _num_or_null(value, decimals=2):
        if value is None:
            return "null"
        return ("%." + str(decimals) + "f") % value

    @staticmethod
    def _bool_json(value):
        return "true" if value else "false"

    def service_telemetry(self):
        now = ticks_ms()
        if (now - self.last_telemetry_ms) < config.TELEMETRY_INTERVAL_MS:
            return
        self.last_telemetry_ms = now
        # Read the sensor every cycle even when offline
        # (publish itself no-ops if MQTT is down).
        self.read_sensor()
        self.publish_telemetry()

    def publish_telemetry(self):
        if not self.mqtt_connected or self.mqtt is None:
            return

        wifi = bool(self.esp is not None and self.esp.is_connected)
        payload = (
            '{"uptime_ms":' + str(ticks_ms())
            + ',"state":"' + self.timer_state + '"'
            + ',"wifi_connected":' + self._bool_json(wifi)
            + ',"mqtt_connected":' + self._bool_json(self.mqtt_connected)
            + ',"temperature_c":' + self._num_or_null(self.last_temperature_c, 2)
            + ',"humidity_rh":' + self._num_or_null(self.last_humidity_rh, 2)
            + ',"rotary_raw":' + str(self.last_rotary_raw)
            + ',"selected_duration_s":' + str(self.selected_duration_seconds)
            + ',"run_duration_s":' + str(self.active_duration_seconds)
            + ',"remaining_s":' + str(self.remaining_seconds())
            + ',"buzzer_active":' + self._bool_json(self.buzzer_active)
            + "}"
        )

        try:
            self.mqtt.publish(config.TELEMETRY_TOPIC, payload, retain=False, qos=0)
        except Exception as err:  # noqa: BLE001
            print("Telemetry publish failed:", err)
            self.mqtt_connected = False

    def publish_event(self, event_name, extra_fields=""):
        if not self.mqtt_connected or self.mqtt is None:
            return

        payload = (
            '{"event":"' + event_name + '"'
            + ',"uptime_ms":' + str(ticks_ms())
            + ',"state":"' + self.timer_state + '"'
        )
        if extra_fields:
            payload += "," + extra_fields
        payload += "}"

        try:
            self.mqtt.publish(config.EVENT_TOPIC, payload, retain=False, qos=0)
        except Exception as err:  # noqa: BLE001
            print("Event publish failed:", err)
            self.mqtt_connected = False


def main():
    ShowerTrack().run()
