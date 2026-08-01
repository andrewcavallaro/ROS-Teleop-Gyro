"""The control loop: turns the latest angles into motion at a fixed rate.

Runs in its own thread. All timing is monotonic (immune to clock steps); the
commanded velocity is slew-limited; the deadman and hardware faults hard-stop
motion with no slew on the way down."""

import sys
import time
import traceback

from . import actuator
from .config import CONFIG
from .motion import clamp, slew, tilt_to_speed, wrap180
from .state import STATE, fresh_locked, snapshot_locked


def control_loop():
    period = 1.0 / CONFIG["loop_hz"]
    deadline = time.monotonic()
    prev = deadline
    hw_retry_at = 0.0
    while True:
        now = time.monotonic()
        dt = min(max(now - prev, 0.0), 0.1)  # measured dt, capped after stalls
        prev = now

        with STATE.lock:
            fresh = fresh_locked(now)
            faulted = STATE.hw_fault
            if fresh and not faulted:
                roll = wrap180(STATE.roll - STATE.roll0)
                pitch = wrap180(STATE.pitch - STATE.pitch0)
                yaw = wrap180(STATE.yaw - STATE.yaw0)

                # Pitch is nose-up-positive on iOS -> nose up moves the arm up.
                sp = -1.0 if CONFIG["invert_pitch"] else 1.0
                # Yaw is counter-clockwise-positive on iOS -> negate so that
                # turning the phone to the right moves the arm to the right.
                sy = 1.0 if CONFIG["invert_yaw"] else -1.0

                dv = CONFIG["max_accel"] * dt  # slew-rate limit (no velocity steps)
                STATE.vy = slew(STATE.vy, sp * tilt_to_speed(pitch), dv)
                STATE.vx = slew(STATE.vx, sy * tilt_to_speed(yaw), dv)
                STATE.y = clamp(STATE.y + STATE.vy * dt, 0.0, 1.0)
                STATE.x = clamp(STATE.x + STATE.vx * dt, 0.0, 1.0)

                # Grip latches with hysteresis: twist right to close,
                # twist left to open, return to neutral in between.
                if roll >= CONFIG["grip_close_deg"]:
                    STATE.grip_closed = True
                elif roll <= CONFIG["grip_open_deg"]:
                    STATE.grip_closed = False
            else:
                # Deadman / hardware fault: hard stop, no slew on the way down.
                STATE.vx = STATE.vy = 0.0
            snap = snapshot_locked(now)

        # Actuator output with bounded fault handling: on error, stop motion,
        # surface the fault, and retry only a safe-stop command at 1 Hz.
        if not faulted or now >= hw_retry_at:
            try:
                actuator.send_to_actuator(snap)
                if faulted:
                    with STATE.lock:
                        STATE.hw_fault, STATE.hw_error = False, None
                    print("[gyro-arm] actuator recovered; teleop re-enabled",
                          file=sys.stderr)
            except Exception as exc:
                with STATE.lock:
                    newly = not STATE.hw_fault
                    STATE.hw_fault = True
                    STATE.hw_error = f"{type(exc).__name__}: {exc}"
                    STATE.vx = STATE.vy = 0.0
                if newly:
                    print("[gyro-arm] ACTUATOR FAULT -- motion stopped; "
                          "retrying safe-stop at 1 Hz", file=sys.stderr)
                    traceback.print_exc()
                hw_retry_at = now + 1.0

        deadline += period
        remaining = deadline - time.monotonic()
        if remaining < -1.0:  # fell far behind (suspend/debugger): resync
            deadline = time.monotonic()
            remaining = 0.0
        time.sleep(max(0.0, remaining))
