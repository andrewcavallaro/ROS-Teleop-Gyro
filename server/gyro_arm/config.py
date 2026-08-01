"""Tuning knobs, runtime info, and hard input limits.

Everything you're likely to want to touch lives in CONFIG; it is validated at
startup by validate_config().
"""

import math

CONFIG = {
    "deadzone_deg":    8.0,   # tilt smaller than this does nothing
    "max_tilt_deg":   40.0,   # tilt at which speed saturates
    "max_speed":      0.55,   # workspace-widths per second at full tilt
    "max_accel":       3.0,   # workspace-widths/s^2 slew limit on commanded velocity
    "grip_close_deg": 28.0,   # roll right past this -> grip closes (latches)
    "grip_open_deg": -28.0,   # roll left past this  -> grip opens  (latches)
    "stale_after_s":   0.6,   # failsafe: freeze motion if the phone goes quiet
    "loop_hz":          60,   # control-loop rate
    "invert_pitch": False,    # flip if up/down feels backwards on your grip
    "invert_yaw":   False,    # flip if left/right feels backwards
}

# Filled in at startup by app.py; read by /api/info, auth, and the banner.
RUNTIME = {"port": 8000, "scheme": "http", "forced_ip": None,
           "token": None, "bind": "127.0.0.1"}

# Input-hardening limits.
MAX_PAYLOAD_READINGS = 200      # bound Sensor Logger batches per request
MAX_BODY_BYTES = 64 * 1024      # request bodies over this get 413
WS_MAX_MSG_BYTES = 2048
WS_MAX_STRIKES = 10             # malformed WS messages before disconnect
WS_MAX_MSG_PER_SEC = 100        # per-connection WS message rate
HTTP_DATA_RATE_PER_SEC = 240.0  # per-client-IP /data rate (Sensor Logger max ~100 Hz)
HTTP_DATA_BURST = 240.0


def validate_config():
    c, bad = CONFIG, []

    def fin(x):
        return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)

    if not (fin(c["deadzone_deg"]) and fin(c["max_tilt_deg"])
            and 0 <= c["deadzone_deg"] < c["max_tilt_deg"]):
        bad.append("need 0 <= deadzone_deg < max_tilt_deg")
    if not (fin(c["max_speed"]) and c["max_speed"] > 0):
        bad.append("max_speed must be a positive finite number")
    if not (fin(c["max_accel"]) and c["max_accel"] > 0):
        bad.append("max_accel must be a positive finite number")
    if not (fin(c["grip_close_deg"]) and fin(c["grip_open_deg"])
            and c["grip_open_deg"] < c["grip_close_deg"]):
        bad.append("need grip_open_deg < grip_close_deg")
    if not (fin(c["stale_after_s"]) and c["stale_after_s"] > 0):
        bad.append("stale_after_s must be a positive finite number")
    if not (fin(c["loop_hz"]) and c["loop_hz"] > 0):
        bad.append("loop_hz must be a positive finite number")
    if bad:
        raise SystemExit("CONFIG invalid: " + "; ".join(bad))
