"""Strict request-body schema for /data.

Validation is ATOMIC: the entire batch is validated into a temporary list and
nothing touches the arm state until every reading has passed. A request that
mixes one valid and one invalid reading is rejected with 422 AND leaves the
state (angles, last_packet, source) completely untouched.
"""

import math

from .config import MAX_PAYLOAD_READINGS
from .motion import finite_number
from .state import ingest


def ingest_body(body):
    """Validate + ingest one request body. Returns the number of readings
    accepted. Raises ValueError with a machine-readable code on any schema
    violation -- in which case the state is guaranteed unchanged."""
    payload = body.get("payload")
    if payload is not None:
        # Sensor Logger batch: angles in RADIANS.
        if not isinstance(payload, list):
            raise ValueError("payload_not_list")
        if len(payload) > MAX_PAYLOAD_READINGS:
            raise ValueError("payload_too_large")
        accepted = []
        for reading in payload:
            if not isinstance(reading, dict):
                raise ValueError("payload_entry_not_object")
            if str(reading.get("name") or "").lower() != "orientation":
                continue
            values = reading.get("values")
            if not isinstance(values, dict):
                raise ValueError("values_not_object")
            try:
                roll = finite_number(values["roll"])
                pitch = finite_number(values["pitch"])
                yaw = finite_number(values["yaw"])
            except KeyError:
                raise ValueError("missing_angle")
            except ValueError:
                raise ValueError("angle_invalid")
            accepted.append((math.degrees(roll), math.degrees(pitch),
                             math.degrees(yaw)))
        # Commit only after the WHOLE batch validated. The state keeps just
        # the newest orientation, so the final reading is the only one that
        # matters.
        if accepted:
            ingest(*accepted[-1], "sensor-logger")
        return len(accepted)

    # Custom packet: angles in DEGREES at the top level. Atomic by
    # construction: all three validated before the single ingest().
    if all(k in body for k in ("roll", "pitch", "yaw")):
        try:
            r = finite_number(body["roll"])
            p = finite_number(body["pitch"])
            y = finite_number(body["yaw"])
        except ValueError:
            raise ValueError("angle_invalid")
        ingest(r, p, y, "custom")
        return 1
    return 0
