"""Black-box HTTP/WebSocket tests against a real server subprocess: auth,
strict schema codes, the atomic-batch regression at the HTTP level, motion,
grip latching, the deadman, and WebSocket origin/abuse handling."""

import json
import time

from helpers import TOKEN, Checker, req, server


def run(check):
    with server() as (port, base):
        check("no token -> 401", req(base, "/api/state", token=None)[0] == 401)
        check("wrong token -> 401", req(base, "/api/state", token="nope")[0] == 401)
        check("page without token -> 401", req(base, "/", token=None)[0] == 401)
        check("data without token -> 401",
              req(base, "/data", data={"roll": 0, "pitch": 0, "yaw": 0}, token=None)[0] == 401)

        st, body = req(base, "/data", raw=b"this is not json")
        check("bad json -> 400", st == 400 and body["error"] == "bad_json")
        st, body = req(base, "/data", raw=b"[1,2,3]")
        check("list root -> 422", st == 422 and body["error"] == "root_not_object")
        st, body = req(base, "/data", data={"payload": "oops"})
        check("payload string -> 422", st == 422 and body["error"] == "payload_not_list")
        st, body = req(base, "/data", data={"payload": None})
        check("payload null -> 422", st == 422 and body["error"] == "no_orientation")
        st, body = req(base, "/data", data={"roll": "NaN", "pitch": 0, "yaw": 0})
        check("string angle -> 422", st == 422 and body["error"] == "angle_invalid")
        st, body = req(base, "/data", raw=b'{"roll": NaN, "pitch": 0, "yaw": 0}')
        check("raw NaN -> 422", st == 422)
        st, body = req(base, "/data", raw=b'{"roll": Infinity, "pitch": 0, "yaw": 0}')
        check("raw Infinity -> 422", st == 422)

        # REGRESSION (atomic batches, HTTP level): valid + invalid in one
        # request -> 422 AND the arm state is completely untouched.
        st, body = req(base, "/data", data={"payload": [
            {"name": "orientation", "values": {"roll": 0.1, "pitch": 0.7, "yaw": 0.1}},
            {"name": "orientation", "values": {"roll": "bad", "pitch": 0, "yaw": 0}},
        ]})
        check("mixed batch -> 422", st == 422 and body["error"] == "angle_invalid")
        st, snap = req(base, "/api/state")
        check("ATOMIC: mixed batch moved nothing",
              snap["connected"] is False and snap["pitch"] == 0.0
              and snap["source"] == "none" and snap["age_ms"] is None)

        st, snap = req(base, "/api/state")
        check("rejected junk moved nothing", snap["vx"] == 0.0 and snap["vy"] == 0.0
              and snap["connected"] is False)
        st, _ = req(base, "/data", raw=b"x" * (80 * 1024))
        check("oversize body -> 413", st == 413)

        st, body = req(base, "/data", data={"roll": 0, "pitch": 30, "yaw": 0})
        check("valid custom -> 200", st == 200 and body == {"ok": True, "accepted": 1})
        st, body = req(base, "/data", data={"payload": [{"name": "orientation",
                                                         "values": {"roll": 0.61, "pitch": 0.52, "yaw": 0.0}}]})
        check("sensor batch -> 200", st == 200 and body["accepted"] == 1)

        # Motion + grip + deadman through the real loop.
        end = time.time() + 0.5
        while time.time() < end:
            req(base, "/data", data={"roll": 35, "pitch": 30, "yaw": 0})
            time.sleep(0.04)
        st, snap = req(base, "/api/state")
        check("streaming: connected + moving", snap["connected"] and snap["vy"] > 0.1)
        check("grip latched closed", snap["grip_closed"] is True)
        time.sleep(0.9)
        st, snap = req(base, "/api/state")
        check("deadman over http", snap["vx"] == 0.0 and snap["vy"] == 0.0
              and snap["connected"] is False)
        check("grip holds through deadman", snap["grip_closed"] is True)

        # WebSocket security behaviour.
        try:
            from websocket import create_connection
        except ImportError:
            print("  [skip] websocket-client not installed; WS tests skipped")
            return

        def ws_denied(url, **kw):
            """websocket-client returns '' when the server closes on us."""
            try:
                ws = create_connection(url, timeout=2, **kw)
                ws.settimeout(2)
                msg = ws.recv()
                ws.close()
                return not msg
            except Exception:
                return True

        check("ws without token refused", ws_denied(f"ws://127.0.0.1:{port}/ws/state"))
        check("ws hostile origin refused",
              ws_denied(f"ws://127.0.0.1:{port}/ws/state?token={TOKEN}",
                        origin="https://evil.example"))
        ws = create_connection(f"ws://127.0.0.1:{port}/ws/state?token={TOKEN}", timeout=2)
        first = json.loads(ws.recv())
        check("ws with token streams state", "x" in first and "hw_fault" in first)
        ws.close()

        ws = create_connection(f"ws://127.0.0.1:{port}/ws/controller?token={TOKEN}", timeout=2)
        for _ in range(12):
            ws.send("definitely not json")
        ws.settimeout(3)
        try:
            dropped = not ws.recv()  # '' = server closed the connection
        except Exception:
            dropped = True
        check("controller drops after malformed flood", dropped)

        ws = create_connection(f"ws://127.0.0.1:{port}/ws/controller?token={TOKEN}", timeout=2)
        for _ in range(10):
            ws.send(json.dumps({"roll": 0, "pitch": 25, "yaw": 0}))
            time.sleep(0.04)
        st, snap = req(base, "/api/state")
        check("controller ws drives state", snap["connected"] and snap["source"] == "web-controller")
        ws.close()


if __name__ == "__main__":
    c = Checker()
    run(c.check)
    c.finish("HTTP/WS")
