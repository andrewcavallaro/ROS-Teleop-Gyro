"""Address discovery -- works on a normal router *and* on phone hotspots.

An iPhone Personal Hotspot always puts the phone at 172.20.10.1 and hands
clients 172.20.10.2-14; Android commonly uses 192.168.43.x. Probing those
gateways only consults the routing table (connect() on a UDP socket sends no
packets), so it finds the right interface even when the hotspot has no
internet, or when the machine is also wired into another network."""

import socket

from .config import RUNTIME

PROBE_TARGETS = (
    ("172.20.10.1", "iPhone hotspot"),
    ("192.168.43.1", "Android hotspot"),
    ("8.8.8.8", None),  # whatever routes to the internet
)


def probe_ip(target):
    """Return this machine's IP on the interface that routes to `target`."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((target, 80))
        ip = s.getsockname()[0]
        return None if ip.startswith("127.") else ip
    except OSError:
        return None
    finally:
        s.close()


def candidate_ips():
    """[{'ip': '172.20.10.4', 'label': 'iPhone hotspot'}, ...], best first."""
    if RUNTIME["forced_ip"]:
        return [{"ip": RUNTIME["forced_ip"], "label": "set via --ip"}]
    bind = RUNTIME["bind"]
    if bind and bind not in ("0.0.0.0", ""):
        loop = bind.startswith("127.") or bind == "localhost"
        return [{"ip": bind, "label": "loopback only" if loop else "set via --bind"}]
    out = []
    for target, label in PROBE_TARGETS:
        ip = probe_ip(target)
        if not ip or any(e["ip"] == ip for e in out):
            continue
        # Only keep the hotspot label if we're genuinely on that subnet.
        on_subnet = label is not None and ip.rsplit(".", 1)[0] == target.rsplit(".", 1)[0]
        out.append({"ip": ip, "label": label if on_subnet else None})
    out.sort(key=lambda e: e["label"] is None)  # hotspot addresses first
    return out or [{"ip": "127.0.0.1", "label": None}]
