"""In-process unit tests: math/validation helpers, the ATOMIC batch-ingest
regression, rate limiting, monotonic timing, and actuator fault handling
against the real control loop."""

import threading
import time

from helpers import Checker


def run(check):
    from gyro_arm import actuator, control, ingest as ingest_mod, motion, state
    from gyro_arm.config import validate_config

    validate_config()  # must not raise with shipped defaults
    check("tilt_to_speed(NaN) is 0", motion.tilt_to_speed(float("nan")) == 0.0)
    check("tilt_to_speed(inf) is 0", motion.tilt_to_speed(float("inf")) == 0.0)
    check("tilt_to_speed(30) ~ 0.26", abs(motion.tilt_to_speed(30.0) - 0.2599) < 0.01)
    check("wrap180(NaN) is 0", motion.wrap180(float("nan")) == 0.0)
    check("slew limits step", motion.slew(0.0, 1.0, 0.1) == 0.1)

    for bad in (True, "1.5", None, float("nan"), float("inf")):
        try:
            motion.finite_number(bad)
            ok = False
        except ValueError:
            ok = True
        check(f"finite_number rejects {bad!r}", ok)
    check("finite_number accepts 3", motion.finite_number(3) == 3.0)

    def body_err(body):
        try:
            ingest_mod.ingest_body(body)
            return None
        except ValueError as e:
            return str(e)

    check("payload not list -> code", body_err({"payload": "oops"}) == "payload_not_list")
    check("payload too large -> code", body_err({"payload": [{}] * 500}) == "payload_too_large")
    check("entry not object -> code", body_err({"payload": [1]}) == "payload_entry_not_object")
    check("values not object -> code",
          body_err({"payload": [{"name": "orientation", "values": 3}]}) == "values_not_object")
    check("missing angle -> code",
          body_err({"payload": [{"name": "orientation", "values": {"roll": 0}}]}) == "missing_angle")
    check("string angle -> code", body_err({"roll": "x", "pitch": 0, "yaw": 0}) == "angle_invalid")

    # REGRESSION (atomic batches): one valid reading followed by one invalid
    # reading must return an error AND leave pitch/last_packet/source untouched.
    st = state.STATE
    before = (st.pitch, st.last_packet, st.source)
    err = body_err({"payload": [
        {"name": "orientation", "values": {"roll": 0.1, "pitch": 0.7, "yaw": 0.1}},
        {"name": "orientation", "values": {"roll": "bad", "pitch": 0, "yaw": 0}},
    ]})
    check("mixed batch rejected", err == "angle_invalid")
    check("ATOMIC: rejected batch changed nothing",
          (st.pitch, st.last_packet, st.source) == before,
          f"before={before} after={(st.pitch, st.last_packet, st.source)}")

    check("valid custom accepted", ingest_mod.ingest_body({"roll": 1, "pitch": 2, "yaw": 3}) == 1)
    check("valid sensor batch accepted",
          ingest_mod.ingest_body({"payload": [{"name": "orientation",
                                               "values": {"roll": 0.1, "pitch": 0.2, "yaw": 0.3}}]}) == 1)

    # Monotonic pin: last_packet must be monotonic-clock based, not wall clock.
    state.ingest(0.0, 0.0, 0.0, "test")
    check("last_packet uses time.monotonic",
          abs(st.last_packet - time.monotonic()) < 0.5
          and abs(st.last_packet - time.time()) > 1000)

    from gyro_arm.security import rate_ok
    ok3 = all(rate_ok("k", 1.0, 3.0) for _ in range(3))
    check("rate bucket allows burst", ok3)
    check("rate bucket then blocks", not rate_ok("k", 1.0, 3.0))

    # Actuator-fault handling: a raising actuator must stop motion, flag the
    # fault, keep the loop alive, and recover once the actuator works again.
    print("-- actuator fault/recovery (runs the real control loop) --")
    feed = {"on": True}

    def feeder():
        while feed["on"]:
            state.ingest(0.0, 30.0, 0.0, "test")
            time.sleep(0.02)

    def boom(_snap):
        raise IOError("simulated serial disconnect")

    actuator.send_to_actuator = boom
    threading.Thread(target=feeder, daemon=True).start()
    threading.Thread(target=control.control_loop, daemon=True).start()
    time.sleep(0.6)
    with st.lock:
        snap = state.snapshot_locked(time.monotonic())
    check("fault flagged", snap["hw_fault"] is True and "simulated" in (snap["hw_error"] or ""))
    check("fault zeroes motion", snap["vx"] == 0.0 and snap["vy"] == 0.0)

    actuator.send_to_actuator = lambda s: None  # "reconnect" the hardware
    time.sleep(1.6)  # safe-stop retry runs at 1 Hz
    with st.lock:
        snap = state.snapshot_locked(time.monotonic())
    check("fault clears on recovery", snap["hw_fault"] is False)
    check("motion resumes after recovery", snap["vy"] > 0.05, f"vy={snap['vy']}")

    feed["on"] = False
    time.sleep(0.9)  # server deadman is 0.6 s
    with st.lock:
        snap = state.snapshot_locked(time.monotonic())
    check("deadman zeroes after silence", snap["vx"] == 0.0 and snap["vy"] == 0.0
          and snap["connected"] is False)


if __name__ == "__main__":
    c = Checker()
    run(c.check)
    c.finish("UNIT")
