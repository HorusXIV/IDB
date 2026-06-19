# ShowerTrack Collector (CircuitPython)

A CircuitPython port of the ShowerTrack data-collection firmware for the
**Adafruit Feather nRF52840 Express + AirLift FeatherWing** and a small
stack of Grove sensors. The device reads local sensor/timer state and
publishes it over MQTT; shower detection and session labelling are left to
downstream services, exactly as in the original C++ firmware.

This is a behaviour-preserving migration of the Arduino/C++ project: the
MQTT topics, JSON payload shapes, timer state machine, debouncing, rotary
quantisation and connection logic all match the original. Where the
interpreted runtime made a literal translation impossible, the adaptation is
documented under [Migration notes](#migration-notes).

## Hardware

- Grove rotary angle sensor on `A0` (analog input).
- Grove button on `A2` (digital input, active HIGH).
- Grove buzzer on `A4` (PWM output).
- SHT4x humidity/temperature sensor on I2C (`SCL`/`SDA`).
- 4-digit TM1637 display on `TX`/`RX`.
- AirLift FeatherWing for Wi-Fi/MQTT (`CS=D13`, `BUSY/READY=D11`,
  `RESET=D12`, `GPIO0` not connected).

## Repository layout

```text
showertrack-circuitpython/
├── boot.py                 # runs once at reset (minimal)
├── code.py                 # entry point: starts the app
├── secrets.example.py      # copy to secrets.py and fill in
├── requirements.txt        # CircuitPython library list (circup)
├── .gitignore
└── showertrack/            # application package
    ├── __init__.py
    ├── clock.py            # monotonic millisecond clock
    ├── config.py           # pins, topics, intervals, timer limits
    ├── display.py          # bit-banged TM1637 driver (port of FourDigitDisplay)
    ├── timer.py            # TimerState constants
    └── app.py              # setup()/loop() orchestrator + state machine
```

## Deployment

1. **Install CircuitPython** (9.x recommended) on the Feather nRF52840.
   Drag the `.uf2` from <https://circuitpython.org/board/feather_nrf52840_express/>
   onto the `FEATHERBOOT` drive; the board reappears as `CIRCUITPY`.

2. **Install the libraries.** With [circup](https://github.com/adafruit/circup):

   ```bash
   uv sync
   uv run circup install adafruit_esp32spi adafruit_minimqtt adafruit_sht4x \
                  adafruit_connection_manager adafruit_ticks adafruit_bus_device
   ```

   (See `requirements.txt` for the full resolved list. The TM1637 display
   needs no library — it is bit-banged with `digitalio`.)

3. **Copy the project** onto the `CIRCUITPY` drive so the layout becomes:

   ```text
   CIRCUITPY/
   ├── boot.py
   ├── code.py
   ├── secrets.py            
   ├── showertrack/          # the package
   └── lib/                  # the libraries from step 2
   ```

4. **Create `secrets.py`** from the template:

   ```bash
   cp secrets.example.py secrets.py     # then edit secrets.py
   ```

5. The board auto-runs `code.py` on reset. Open the USB serial console
   (115200 baud) to watch boot diagnostics.

If `secrets.py`, the radio libraries, or the AirLift itself are missing, the
device still boots and runs the timer/display/buzzer locally — it simply
skips Wi-Fi/MQTT.

## Usage

The rotary angle sensor selects a duration from 1 to 20 minutes in 30-second
steps. The button drives the timer:

- `idle` → start the timer.
- `running` → pause.
- `paused` → resume from the stored remaining time.
- `alarm` → silence the buzzer and return to `idle`.

When the timer hits zero the device enters `alarm`, publishes a
`timer_finished` event, and sounds the buzzer until the button is pressed.

## MQTT interface

Identical to the original firmware. Topics:

- `shower/device1/status` — `online` (retained) / `offline` (MQTT last will).
- `shower/device1/telemetry` — published every 5 s while connected.
- `shower/device1/event` — `boot`, `mqtt_reconnected`, `timer_started`,
  `timer_paused`, `timer_resumed`, `timer_finished`, `alarm_silenced`.

Telemetry payload:

```json
{
  "uptime_ms": 123456,
  "state": "running",
  "wifi_connected": true,
  "mqtt_connected": true,
  "temperature_c": 24.81,
  "humidity_rh": 71.20,
  "rotary_raw": 512,
  "selected_duration_s": 600,
  "run_duration_s": 600,
  "remaining_s": 485,
  "buzzer_active": false
}
```

`temperature_c` and `humidity_rh` are `null` when the SHT4x is unavailable or
a read fails. The device publishes no wall-clock time; the subscriber should
record its own receive timestamp.
