"""CircuitPython entry point.

CircuitPython runs this file automatically after boot.py.  All real logic
lives in the showertrack package; this just starts it.
"""

from showertrack.app import main

main()
