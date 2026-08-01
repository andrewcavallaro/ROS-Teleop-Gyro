"""Shared teleop state, ingest, and snapshotting. All timing uses
time.monotonic(), so wall-clock steps (NTP, VM corrections) can't fake
freshness."""

import threading
import time

from .config import CONFIG
from .motion import wrap180


class State:
    def __init__(self):
        self.lock = threading.Lock()
        # Actuator pose in a normalized workspace: (0,0) bottom-left .. (1,1) top-right
        self.x = 0.5
        self.y = 0.5
        self.grip_closed = False
        # Newest phone angles, degrees (validated finite before they get here)
        self.roll = 0.0
        self.pitch = 0.0
        self.yaw = 0.0
        # Neutral reference captured by calibrate()
        self.roll0 = 0.0
        self.pitch0 = 0.0
        self.yaw0 = 0.0
        self.last_packet = None    # time.monotonic() of last accepted packet
        self.source = "none"
        # Derived velocities (for the dashboard / actuator)
        self.vx = 0.0
        self.vy = 0.0
        # Hardware output health (see control.py)
        self.hw_fault = False
        self.hw_error = None


STATE = State()


def ingest(roll_deg, pitch_deg, yaw_deg, source):
    """Store the newest phone orientation (degrees). Defense in depth: raises
    on non-finite values even if a caller forgot to validate."""
    import math
    for v in (roll_deg, pitch_deg, yaw_deg):
        if not isinstance(v, float) or not math.isfinite(v):
            raise ValueError("angle_not_finite")
    with STATE.lock:
        STATE.roll, STATE.pitch, STATE.yaw = roll_deg, pitch_deg, yaw_deg
        STATE.last_packet = time.monotonic()
        STATE.source = source


def calibrate():
    with STATE.lock:
        STATE.roll0, STATE.pitch0, STATE.yaw0 = STATE.roll, STATE.pitch, STATE.yaw


def fresh_locked(now):
    """Caller holds STATE.lock."""
    return (STATE.last_packet is not None
            and (now - STATE.last_packet) < CONFIG["stale_after_s"])


def snapshot_locked(now):
    """Build a JSON-friendly state snapshot. Caller holds STATE.lock and
    passes its own monotonic `now` so freshness matches its own decisions."""
    return {
        "x": round(STATE.x, 4),
        "y": round(STATE.y, 4),
        "grip_closed": STATE.grip_closed,
        "vx": round(STATE.vx, 3),
        "vy": round(STATE.vy, 3),
        "roll": round(wrap180(STATE.roll - STATE.roll0), 1),
        "pitch": round(wrap180(STATE.pitch - STATE.pitch0), 1),
        "yaw": round(wrap180(STATE.yaw - STATE.yaw0), 1),
        "connected": fresh_locked(now),
        "age_ms": None if STATE.last_packet is None
                  else int((now - STATE.last_packet) * 1000),
        "source": STATE.source,
        "hw_fault": STATE.hw_fault,
        "hw_error": STATE.hw_error,
        "cfg": {
            "deadzone": CONFIG["deadzone_deg"],
            "max_tilt": CONFIG["max_tilt_deg"],
            "grip_close": CONFIG["grip_close_deg"],
            "grip_open": CONFIG["grip_open_deg"],
        },
    }
