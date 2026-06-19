"""boot.py runs once at power-on / hard reset, before code.py.

ShowerTrack does not need to remount the filesystem or reconfigure USB, so
this file is intentionally minimal.  It is the right place to add, for
example::

    import storage
    storage.remount("/", readonly=False)

if you later want the device itself to write logs to the CIRCUITPY drive.
Note that remounting read/write from here makes the drive read-only to the
host computer, so leave it commented out during development.
"""

print("ShowerTrack boot.py: no special configuration required")
