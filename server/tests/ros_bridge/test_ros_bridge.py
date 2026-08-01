#!/usr/bin/env python3
"""ROS bridge tests. Two layers:

  1. bridge_core alone (no mocks at all): parameter + snapshot validation,
     compute() mapping, deadman gating, poisoning.
  2. The ROS 2 adapter (mocked rclpy) over a LIVE server stream.

Started by tests/run_tests.py; can also run standalone."""

import json
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE / "mocks"))
# Bridge package now lives beside server/ in the sibling colcon
# workspace; add its module dir so we can import the real core.
sys.path.insert(0, str(ROOT.parent / "ros2_ws" / "src" / "gyro_teleop"))
sys.path.insert(0, str(ROOT / "tests"))

from helpers import TOKEN, Checker, req, server  # noqa: E402
import rclpy.node as rclpy_mock  # noqa: E402  (the mock)
from gyro_teleop import bridge_core  # noqa: E402  (the real thing)

c = Checker()
check = c.check


def feed(base, roll, pitch, yaw, seconds):
    end = time.time() + seconds
    while time.time() < end:
        req(base, "/data", data={"roll": roll, "pitch": pitch, "yaw": yaw})
        time.sleep(0.05)


def last(pubs, topic):
    return pubs[topic].msgs[-1]


GOOD = {"x": 0.5, "y": 0.5, "vx": 0.0, "vy": 0.26, "roll": 1.0, "pitch": 2.0,
        "yaw": 3.0, "grip_closed": True, "connected": True, "cfg": {"max_tilt": 40}}


# ---------------------------------------------------------------------------
print("-- bridge_core: parameter + snapshot validation --")
for bad_params in ({"publish_rate_hz": -5}, {"stale_timeout_s": 0},
                   {"workspace_width_m": -1}, {"planar_linear_scale": 1e9},
                   {"reach_x_m": float("nan")}):
    try:
        bridge_core.validate_params(bad_params)
        raised = False
    except ValueError:
        raised = True
    check(f"params reject {bad_params}", raised)
check("params coerce + default fine",
      bridge_core.validate_params({"server": "10.0.0.2:9000"})["publish_rate_hz"] == 30.0)

v = bridge_core.validate_snapshot
check("snapshot: accepts good", v(GOOD) is not None)
check("snapshot: rejects x out of range", v({**GOOD, "x": 5}) is None)
check("snapshot: rejects NaN velocity", v({**GOOD, "vx": float("nan")}) is None)
check("snapshot: rejects string field", v({**GOOD, "vy": "0.1"}) is None)
check("snapshot: rejects bool-as-number", v({**GOOD, "x": True}) is None)
check("snapshot: rejects non-bool grip", v({**GOOD, "grip_closed": 1}) is None)
check("snapshot: rejects non-bool connected", v({**GOOD, "connected": "true"}) is None)
check("snapshot: rejects list root", v([1, 2]) is None)

print("-- bridge_core: compute() deadman + mapping (no transport) --")
core = bridge_core.BridgeCore({"token": "t"})
out = core.compute()
check("core idle: not fresh, zero twist, no pose",
      not out.fresh and out.lin_y == 0.0 and out.pose is None and out.grip is None)
core._snap = v(GOOD)
core._snap_time = time.monotonic()
core._server_ok = True
out = core.compute()
check("core fresh: REP103 up velocity", abs(out.lin_z - 0.26 * 0.4) < 1e-9)
check("core fresh: pose present", out.pose is not None and abs(out.pose[2] - 0.30) < 1e-9)
check("core fresh: grip + joy", out.grip is True and abs(out.joy_axes[2] - 1.0 / 40) < 1e-6
      and out.joy_buttons == [1, 1])
core._store_raw('"garbage snapshot"')   # invalid data arrives
out = core.compute()
check("core poisoned: immediately unfresh + zeroed",
      not out.fresh and out.lin_z == 0.0 and out.pose is None
      and out.joy_axes == [0.0, 0.0, 0.0])
check("core poisoned: grip still held", out.grip is True)
core._store_raw('{}')  # still invalid -> stays poisoned
check("core: still poisoned on second junk", not core.compute().fresh)

# REGRESSION: reconnect must not briefly revive the previous snapshot.
# Seed a recent nonzero snapshot with a long stale window; simulate the WS
# dropping, then a fresh handshake with no first frame yet. compute() must
# stay unfresh until _store_raw runs. Also verify a valid frame flips it live.
rc = bridge_core.BridgeCore({"token": "t", "stale_timeout_s": 5.0})
rc._snap = v(GOOD)
rc._snap_time = time.monotonic()   # snapshot is "recent" by wall time
rc._server_ok = True
check("reconnect: baseline is fresh", rc.compute().fresh)
rc._server_ok = False              # transport dropped
check("reconnect: unfresh while server_ok False",
      not rc.compute().fresh and rc.compute().pose is None)
# Simulate handshake completing but no frame yet -- with the old code, _run_websocket
# would have set _server_ok True here and compute() would falsely report fresh.
# The fix moved that flip into _store_raw, so nothing changes until a frame arrives:
check("reconnect: handshake alone does NOT revive freshness",
      rc._server_ok is False and not rc.compute().fresh)
rc._store_raw(json.dumps(GOOD).encode())  # first real frame after reconnect
check("reconnect: valid frame restores freshness",
      rc._server_ok is True and rc.compute().fresh)

print("-- planar mapping (core) --")
pcore = bridge_core.BridgeCore({"token": "t", "planar_twist": True,
                                "planar_linear_scale": 2.0,
                                "planar_angular_scale": 1.0})
pcore._snap = v({**GOOD, "vx": 0.26, "vy": 0.26})
pcore._snap_time = time.monotonic()
pcore._server_ok = True
out = pcore.compute()
check("planar: forward from pitch", abs(out.lin_x - 0.52) < 1e-9)
check("planar: right turn negative ang.z", abs(out.ang_z + 0.26) < 1e-9)
check("planar: scales independent", abs(out.ang_z / out.lin_x) - 0.5 < 1e-9)

# ---------------------------------------------------------------------------
with server() as (port, base):
    print("-- ROS 2 adapter (mocked rclpy) over live stream --")
    rclpy_mock.PARAM_OVERRIDES.clear()
    rclpy_mock.PARAM_OVERRIDES.update({"server": f"127.0.0.1:{port}", "token": TOKEN})
    from gyro_teleop import bridge_node  # noqa: E402
    node = bridge_node.GyroTeleopBridge()
    tick = node.timers[0]
    time.sleep(0.8)

    tick()
    tw = last(node.pubs, "~/cmd_vel")
    check("r2 idle: zero twist", tw.linear.y == 0.0 and tw.linear.z == 0.0)
    check("r2 idle: connected False", last(node.pubs, "~/connected").data is False)
    check("r2 idle: NO pose published", len(node.pubs["~/target_pose"].msgs) == 0)
    check("r2 idle: joy axes zeroed", last(node.pubs, "~/joy").axes == [0.0, 0.0, 0.0])

    t = threading.Thread(target=feed, args=(base, 35.0, 30.0, 0.0, 1.0)); t.start()
    time.sleep(0.5); tick()
    tw = last(node.pubs, "~/cmd_vel")
    check("r2 live: upward velocity", 0.05 < tw.linear.z < 0.2, f"z={tw.linear.z}")
    check("r2 live: grip closed", last(node.pubs, "~/grip_closed").data is True)
    check("r2 live: pose published", len(node.pubs["~/target_pose"].msgs) > 0)
    joy = last(node.pubs, "~/joy")
    check("r2 live: joy normalized", abs(joy.axes[2] - 35.0 / 40.0) < 0.05
          and joy.buttons == [1, 1])
    stamped = last(node.pubs, "~/cmd_vel_stamped")
    check("r2 live: stamped mirrors twist", stamped.twist.linear.z == tw.linear.z
          and stamped.header.frame_id == "base_link")
    t.join()

    pose_count = len(node.pubs["~/target_pose"].msgs)
    time.sleep(1.0)
    tick(); tick()
    tw = last(node.pubs, "~/cmd_vel")
    check("r2 stale: twist zeroed", tw.linear.y == 0.0 and tw.linear.z == 0.0)
    check("r2 stale: pose pipeline STOPPED",
          len(node.pubs["~/target_pose"].msgs) == pose_count)
    check("r2 stale: joy axes zeroed", last(node.pubs, "~/joy").axes == [0.0, 0.0, 0.0])
    check("r2 stale: grip held", last(node.pubs, "~/grip_closed").data is True)
    node._stop = True

c.finish("ROS BRIDGE")
