#!/usr/bin/env python3
"""Gyro Arm automated tests. Run from the project root:

    python tests/run_tests.py

Groups (each module is also runnable standalone):
    test_unit.py       math/validation, ATOMIC batch regression, rate limits,
                       monotonic timing, actuator fault/recovery
    test_http_ws.py    black-box auth/schema/deadman/WebSocket tests
    test_templates.py  page rendering + `node --check` of the inline JS
    ros_bridge/        shared bridge core + BOTH ROS adapters via mocks

Needs only the project requirements; websocket-client enables the WS tests
and node the JS checks (both skipped with a note if missing). Exits non-zero
on any failure.
"""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from helpers import ROOT, Checker  # noqa: E402
import test_http_ws  # noqa: E402
import test_templates  # noqa: E402
import test_unit  # noqa: E402

checker = Checker()

print("== unit tests (in-process) ==")
test_unit.run(checker.check)

print("== http/ws tests (subprocess server) ==")
test_http_ws.run(checker.check)

print("== template tests ==")
test_templates.run(checker.check)

print("== ros bridge tests (core + rclpy adapter) ==")
rc = subprocess.call([sys.executable,
                      str(ROOT / "tests" / "ros_bridge" / "test_ros_bridge.py")])
checker.check("ros bridge suite", rc == 0)

checker.finish("ALL TESTS")
