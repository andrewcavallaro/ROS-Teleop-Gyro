"""Pure control math. Every function is defensive against non-finite input:
NaN or infinity can never turn into motion."""

import math

from .config import CONFIG


def wrap180(deg):
    """Wrap any finite angle into [-180, 180). Non-finite input returns 0."""
    if not math.isfinite(deg):
        return 0.0
    return (deg + 180.0) % 360.0 - 180.0


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def slew(current, target, max_delta):
    """Move `current` toward `target` by at most `max_delta`."""
    if target > current + max_delta:
        return current + max_delta
    if target < current - max_delta:
        return current - max_delta
    return target


def tilt_to_speed(deg):
    """Deadzone, then a squared ramp: fine control near neutral, fast at the
    edges. Returns exactly 0.0 for any non-finite input (fail-safe)."""
    if not math.isfinite(deg):
        return 0.0
    dz, mx = CONFIG["deadzone_deg"], CONFIG["max_tilt_deg"]
    mag = abs(deg)
    if mag <= dz:
        return 0.0
    frac = clamp((mag - dz) / (mx - dz), 0.0, 1.0)
    return math.copysign(frac * frac * CONFIG["max_speed"], deg)


def finite_number(value):
    """Strictly parse a real, finite number. Rejects bools, strings, NaN, inf."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("not_a_number")
    f = float(value)
    if not math.isfinite(f):
        raise ValueError("not_finite")
    return f
