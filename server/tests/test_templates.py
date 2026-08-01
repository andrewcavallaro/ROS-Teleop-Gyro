"""Template checks: both pages render with a token, their inline JavaScript
passes `node --check` (skipped if node is unavailable), and the controller
contains the authoritative-staleness fix."""

import re
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path

from helpers import TOKEN, Checker, server


def run(check):
    node = shutil.which("node")
    if not node:
        print("  [skip] node not installed; JS syntax checks skipped")
    with server() as (port, base):
        for name, path in (("dashboard", "/"), ("controller", "/controller")):
            html = urllib.request.urlopen(f"{base}{path}?token={TOKEN}", timeout=2).read().decode()
            check(f"{name} renders", len(html) > 1000)
            if name == "controller":
                check("controller uses authoritative staleness",
                      "controller stale" in html)
            if not node:
                continue
            scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
            check(f"{name} has inline script", len(scripts) >= 1)
            for i, src in enumerate(scripts):
                f = tempfile.NamedTemporaryFile("w", suffix=".js", delete=False)
                f.write(src)
                f.close()
                r = subprocess.run([node, "--check", f.name],
                                   capture_output=True, text=True)
                check(f"{name} script {i} JS syntax", r.returncode == 0,
                      r.stderr.strip()[:120])
                Path(f.name).unlink()


if __name__ == "__main__":
    c = Checker()
    run(c.check)
    c.finish("TEMPLATES")
