"""Flask app: pages, ingest endpoints, state output, and WebSockets.

All the actual logic lives in the sibling modules; this file is just routing
plus the WebSocket abuse limits."""

import json
import time
from urllib.parse import quote

from flask import Flask, jsonify, render_template, request
from flask_sock import Sock

from .config import (HTTP_DATA_BURST, HTTP_DATA_RATE_PER_SEC, MAX_BODY_BYTES,
                     RUNTIME, WS_MAX_MSG_BYTES, WS_MAX_MSG_PER_SEC,
                     WS_MAX_STRIKES)
from .ingest import ingest_body
from .motion import finite_number
from .netinfo import candidate_ips
from .security import (rate_ok, require_token, require_token_page,
                       ws_authorized, ws_refuse)
from .state import STATE, calibrate, ingest, snapshot_locked

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_BODY_BYTES  # 413 beyond
sock = Sock(app)


# -- pages ------------------------------------------------------------------
@app.get("/")
@require_token_page
def dashboard():
    return render_template("dashboard.html", token=RUNTIME["token"])


@app.get("/controller")
@require_token_page
def controller():
    return render_template("controller.html", token=RUNTIME["token"])


# -- ingest (strict schema; 400 = bad JSON, 422 = unsupported payload) ------
@app.post("/data")
@require_token
def data():
    """Accepts Sensor Logger "HTTP Push" batches (radians) or a plain
    {"roll","pitch","yaw"} packet in degrees. Batch validation is atomic:
    a rejected request never moves the arm."""
    if not rate_ok(("data", request.remote_addr),
                   HTTP_DATA_RATE_PER_SEC, HTTP_DATA_BURST):
        return jsonify({"ok": False, "error": "rate_limited"}), 429
    body = request.get_json(force=True, silent=True)
    if body is None:
        return jsonify({"ok": False, "error": "bad_json"}), 400
    if not isinstance(body, dict):
        return jsonify({"ok": False, "error": "root_not_object"}), 422
    try:
        accepted = ingest_body(body)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 422
    if accepted == 0:
        return jsonify({"ok": False, "error": "no_orientation"}), 422
    return jsonify({"ok": True, "accepted": accepted})


@sock.route("/ws/controller")
def ws_controller(ws):
    """Built-in web controller stream: {"roll","pitch","yaw"} in degrees at
    ~30 Hz, or {"type": "calibrate"}. Requires token + same-origin; abusive
    or repeatedly-malformed clients are disconnected."""
    if not ws_authorized():
        ws_refuse(ws)
        return
    strikes = 0
    win_start, win_count = time.monotonic(), 0
    while True:
        msg = ws.receive()
        if msg is None:
            break
        now = time.monotonic()
        if now - win_start >= 1.0:
            win_start, win_count = now, 0
        win_count += 1
        if win_count > WS_MAX_MSG_PER_SEC:
            break  # abusive message rate

        bad = False
        if not isinstance(msg, str) or len(msg) > WS_MAX_MSG_BYTES:
            bad = True
        else:
            try:
                d = json.loads(msg)
                if not isinstance(d, dict):
                    raise ValueError("not_object")
                if d.get("type") == "calibrate":
                    calibrate()
                    continue
                r = finite_number(d["roll"])
                p = finite_number(d["pitch"])
                y = finite_number(d["yaw"])
                ingest(r, p, y, "web-controller")
            except (KeyError, ValueError, TypeError):
                bad = True
        if bad:
            strikes += 1
            if strikes >= WS_MAX_STRIKES:
                break


# -- state out + commands ---------------------------------------------------
@sock.route("/ws/state")
def ws_state(ws):
    """Pushes the state snapshot to the dashboard/bridges at ~30 Hz."""
    if not ws_authorized():
        ws_refuse(ws)
        return
    while True:
        now = time.monotonic()
        with STATE.lock:
            snap = snapshot_locked(now)
        ws.send(json.dumps(snap))
        time.sleep(1 / 30)


@app.get("/api/state")
@require_token
def api_state():
    now = time.monotonic()
    with STATE.lock:
        return jsonify(snapshot_locked(now))


@app.get("/api/info")
@require_token
def api_info():
    """Addresses/URLs for connecting the phone. Shown in the NO SIGNAL panel."""
    ips = candidate_ips()
    best = ips[0]
    base = f"{RUNTIME['scheme']}://{best['ip']}:{RUNTIME['port']}"
    q = "?token=" + quote(RUNTIME["token"] or "")
    return jsonify({
        "ips": ips,
        "port": RUNTIME["port"],
        "bind": RUNTIME["bind"],
        "https": RUNTIME["scheme"] == "https",
        "urls": {
            "dashboard": base + "/" + q,
            "data": base + "/data" + q,
            "controller": base + "/controller" + q,
        },
    })


@app.post("/calibrate")
@require_token
def do_calibrate():
    calibrate()
    return jsonify({"ok": True})


@app.post("/reset")
@require_token
def do_reset():
    with STATE.lock:
        STATE.x = STATE.y = 0.5
    return jsonify({"ok": True})
