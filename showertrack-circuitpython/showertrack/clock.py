"""Monotonic millisecond clock.

``time.monotonic_ns()`` returns an arbitrary-precision integer in
nanoseconds, so there is no wrap to worry about and the same interval
comparisons work unchanged.  We deliberately avoid ``time.monotonic()``
because its float resolution degrades as uptime grows.
"""

import time


def ticks_ms():
    """Return milliseconds since power-on as an integer."""
    return time.monotonic_ns() // 1_000_000
