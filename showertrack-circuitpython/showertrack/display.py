"""Bit-banged driver for the 4 digit TM1637 display.

The TM1637 speaks a two-wire protocol that *looks* like I2C -- it 
has start/stop conditions anda per-byte ACK -- but it is timed loosely 
enough to drive from Python by flipping two ``digitalio`` lines.

"Releasing" a line means letting the external pull-up float it HIGH (we
model that with ``INPUT`` + ``Pull.UP``); "driving low" means switching the
pin to ``OUTPUT`` and writing 0.  
"""

import time

import digitalio

# Standard 7-segment bit patterns for digits 0-9 (segment A is bit 0).
_DIGIT_TO_SEGMENT = (
    0x3F, 0x06, 0x5B, 0x4F, 0x66,
    0x6D, 0x7D, 0x07, 0x7F, 0x6F,
)


_BIT_DELAY_S = 5e-6


class FourDigitDisplay:
    def __init__(self, clk_pin, dio_pin):
        self._clk_pin = clk_pin
        self._dio_pin = dio_pin
        self._clk = None
        self._dio = None
        self._brightness = 7

    # -- pin management ------------------------------------------------------
    def configure_pins(self, clk_pin, dio_pin):
        """Re-assign the CLK/DIO pins (used for swapped-order retry).

        Any existing line objects are released first so the pins are free to
        be claimed in the new order on the next ``begin()``.
        """
        self._release_objects()
        self._clk_pin = clk_pin
        self._dio_pin = dio_pin

    def _release_objects(self):
        if self._clk is not None:
            self._clk.deinit()
            self._clk = None
        if self._dio is not None:
            self._dio.deinit()
            self._dio = None

    def _create_lines(self):
        self._release_objects()
        self._clk = digitalio.DigitalInOut(self._clk_pin)
        self._dio = digitalio.DigitalInOut(self._dio_pin)

    # -- lifecycle -----------------------------------------------------------
    def begin(self):
        """Initialise the display and probe for an ACK.

        Returns True if the panel acknowledged
        """
        self._create_lines()
        self._release_line(self._clk)
        self._release_line(self._dio)
        self.set_brightness(7)
        self.clear()
        return self._probe()

    def set_brightness(self, brightness):
        self._brightness = 7 if brightness > 7 else brightness
        self._write_command(0x88 | self._brightness)

    def clear(self):
        self._set_segments([0, 0, 0, 0])

    def show_time(self, total_seconds, show_colon):
        """Render MM:SS with a leading-zero blank and optional colon."""
        minutes = total_seconds // 60
        seconds = total_seconds % 60

        segments = [
            self._encode_digit((minutes // 10) % 10),
            self._encode_digit(minutes % 10),
            self._encode_digit((seconds // 10) % 10),
            self._encode_digit(seconds % 10),
        ]

        if minutes < 10:
            segments[0] = 0x00

        if show_colon:
            segments[1] |= 0x80

        self._set_segments(segments)

    # -- low-level protocol --------------------------------------------------
    @staticmethod
    def _encode_digit(digit):
        if 0 <= digit < 10:
            return _DIGIT_TO_SEGMENT[digit]
        return 0x00

    @staticmethod
    def _bit_delay():
        time.sleep(_BIT_DELAY_S)

    @staticmethod
    def _drive_low(line):
        line.switch_to_output(value=False)

    @staticmethod
    def _release_line(line):
        line.switch_to_input(pull=digitalio.Pull.UP)

    def _start_condition(self):
        self._release_line(self._dio)
        self._release_line(self._clk)
        self._bit_delay()
        self._drive_low(self._dio)
        self._bit_delay()
        self._drive_low(self._clk)

    def _stop_condition(self):
        self._drive_low(self._clk)
        self._bit_delay()
        self._drive_low(self._dio)
        self._bit_delay()
        self._release_line(self._clk)
        self._bit_delay()
        self._release_line(self._dio)
        self._bit_delay()

    def _write_byte(self, value):
        # Clock out 8 bits, LSB first.
        for _ in range(8):
            self._drive_low(self._clk)
            if value & 0x01:
                self._release_line(self._dio)
            else:
                self._drive_low(self._dio)
            self._bit_delay()
            self._release_line(self._clk)
            self._bit_delay()
            value >>= 1

        # Read the ACK bit: device pulls DIO low if it accepted the byte.
        self._drive_low(self._clk)
        self._release_line(self._dio)
        self._bit_delay()
        self._release_line(self._clk)
        self._bit_delay()
        ack = self._dio.value is False
        self._drive_low(self._clk)
        self._drive_low(self._dio)
        return ack

    def _write_command(self, command):
        self._start_condition()
        ack = self._write_byte(command)
        self._stop_condition()
        return ack

    def _set_segments(self, segments):
        # 0x40 = data command, auto-increment address.
        ack_ok = self._write_command(0x40)
        self._start_condition()
        # 0xC0 = set starting address to 0.
        ack_ok = self._write_byte(0xC0) and ack_ok
        for value in segments:
            ack_ok = self._write_byte(value) and ack_ok
        self._stop_condition()
        # 0x88 | brightness = display ON at the requested brightness.
        ack_ok = self._write_command(0x88 | self._brightness) and ack_ok
        return ack_ok

    def _probe(self):
        return self._set_segments([0, 0, 0, 0])
