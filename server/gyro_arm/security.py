"""Token auth, WebSocket origin checks, and per-key rate limiting."""

import hmac
import threading
import time
from functools import wraps
from urllib.parse import urlsplit

from flask import jsonify, request

from .config import RUNTIME


def _supplied_token():
    tok = request.args.get("token") or request.headers.get("X-Auth-Token")
    if not tok:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            tok = auth[7:].strip()
    return tok or ""


def authorized():
    expected = RUNTIME["token"] or ""
    return bool(expected) and hmac.compare_digest(_supplied_token(), expected)


def require_token(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not authorized():
            return jsonify({"ok": False, "error": "unauthorized"}), 401
        return fn(*a, **kw)
    return wrapper


def require_token_page(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not authorized():
            return ("<h3>401 &mdash; auth token required</h3>"
                    "<p>Open the exact URL printed in the server banner "
                    "(it includes <code>?token=&hellip;</code>).</p>", 401)
        return fn(*a, **kw)
    return wrapper


def ws_authorized():
    """Token + browser-origin check for WebSocket upgrades. Non-browser
    clients send no Origin header and are covered by the token alone."""
    origin = request.headers.get("Origin")
    if origin and urlsplit(origin).netloc != request.host:
        return False
    return authorized()


def ws_refuse(ws):
    try:
        ws.close(reason=1008, message="unauthorized")
    except Exception:
        pass


# Simple per-key token-bucket rate limiter (in-process, LAN scale).
_rl_lock = threading.Lock()
_rl_buckets = {}


def rate_ok(key, per_sec, burst):
    now = time.monotonic()
    with _rl_lock:
        tokens, last = _rl_buckets.get(key, (burst, now))
        tokens = min(burst, tokens + (now - last) * per_sec)
        if tokens < 1.0:
            _rl_buckets[key] = (tokens, now)
            return False
        _rl_buckets[key] = (tokens - 1.0, now)
        return True
