"""Shared test plumbing: result collection, a server subprocess fixture, and
a tiny authed HTTP client."""

import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TOKEN = "testtoken"


class Checker:
    def __init__(self):
        self.failures = []

    def check(self, name, cond, detail=""):
        status = "ok " if cond else "FAIL"
        print(f"  [{status}] {name}" + (f"  ({detail})" if detail and not cond else ""))
        if not cond:
            self.failures.append(name)

    def finish(self, label="TESTS"):
        print()
        if self.failures:
            print(f"{label}: FAILED ({len(self.failures)}): " + ", ".join(self.failures))
            sys.exit(1)
        print(f"{label}: ALL PASSED")


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@contextmanager
def server(token=TOKEN):
    """Start app.py on a random loopback port; yield (port, base_url)."""
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "app.py"), "--bind", "127.0.0.1",
         "--port", str(port), "--token", token],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                if req(base, "/api/state", token=token)[0] == 200:
                    break
            except Exception:
                pass
            time.sleep(0.1)
        else:
            raise SystemExit("server did not start")
        yield port, base
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def req(base, path, data=None, token=TOKEN, raw=None):
    """Return (status, parsed-json-or-None)."""
    url = base + path + (f"?token={token}" if token else "")
    body = raw if raw is not None else (json.dumps(data).encode() if data is not None else None)
    r = urllib.request.Request(url, data=body,
                               method="POST" if body is not None else "GET",
                               headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=2) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"null")
        except ValueError:
            return e.code, None
