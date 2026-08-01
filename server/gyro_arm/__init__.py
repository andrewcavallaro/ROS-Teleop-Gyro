"""Gyro Arm server package.

Run the server via ``python app.py`` at the project root. Module map:

    config.py    tuning knobs (CONFIG), runtime info, hard input limits
    motion.py    pure control math, defensive against non-finite input
    state.py     shared teleop state, ingest, snapshots (monotonic timing)
    security.py  token auth, WebSocket origin checks, rate limiting
    ingest.py    strict request-body schema; batch validation is ATOMIC
    actuator.py  <-- edit this file to wire up real hardware
    control.py   the 60 Hz control loop (slew limiting, deadman, HW faults)
    netinfo.py   LAN/hotspot address discovery
    server.py    Flask app: pages, endpoints, WebSockets
"""

__version__ = "0.3.0"
