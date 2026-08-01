"""
Gyro Arm -- drive a single-arm actuator (virtual or real) with an iPhone.

Mapping (relative to the calibrated neutral pose, phone held flat like a remote):
    roll   -> grip / ungrip   (twist right past +28 deg closes; left past -28 deg opens)
    pitch  -> up / down       (nose up = up)
    yaw    -> left / right    (turn toward a side = move that way)

Two ways to feed it phone data:
    1. Sensor Logger app  ->  HTTP Push to   POST http://<host>:8000/data?token=...
    2. Built-in Safari controller at         https://<host>:8000/controller?token=...
       (iOS only exposes motion data to HTTPS pages; run with --https)

Security model:
    * Every endpoint and WebSocket requires the auth token printed at startup
      (?token=..., X-Auth-Token header, or Authorization: Bearer).
    * WebSocket connections whose browser Origin is a different host are refused.
    * The server binds to 127.0.0.1 unless --bind is given explicitly
      (use `--bind 0.0.0.0` for phone/LAN/hotspot use).
    * All input is validated -- atomically for batches -- at the boundary;
      rejected data never moves the arm.
    * --https (self-signed) only ENCRYPTS the link; use --cert/--key with your
      own certificate to authenticate the server.

This file is just the CLI entry point; the implementation lives in gyro_arm/
(see gyro_arm/__init__.py for the module map; hardware goes in
gyro_arm/actuator.py).

Run:
    pip install -r requirements.txt
    python app.py --bind 0.0.0.0                     # LAN / hotspot use
    python app.py --bind 0.0.0.0 --https             # + adhoc TLS (Safari controller)
    python app.py --bind 0.0.0.0 --cert c.pem --key k.pem   # your own / pinned cert
"""

import argparse
import secrets
import threading
from urllib.parse import quote

from gyro_arm.config import RUNTIME, validate_config
from gyro_arm.control import control_loop
from gyro_arm.netinfo import candidate_ips
from gyro_arm.server import app


def parse_args():
    ap = argparse.ArgumentParser(description="Gyro Arm server")
    ap.add_argument("--bind", default="127.0.0.1",
                    help="address to listen on (default 127.0.0.1 = this machine "
                         "only; use 0.0.0.0 or a LAN IP for phone/hotspot use)")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--ip", help="force the address shown in the banner and dashboard, "
                                 "e.g. --ip 172.20.10.4 when on an iPhone hotspot")
    ap.add_argument("--token", help="auth token to require (default: generate one)")
    ap.add_argument("--https", action="store_true",
                    help="serve with a self-signed cert (encrypts only; required for "
                         "the Safari controller on iOS)")
    ap.add_argument("--cert", help="TLS certificate file (use with --key)")
    ap.add_argument("--key", help="TLS private key file (use with --cert)")
    args = ap.parse_args()
    if bool(args.cert) != bool(args.key):
        ap.error("--cert and --key must be given together")
    return args


def banner(args):
    ips = candidate_ips()
    best = ips[0]
    scheme = RUNTIME["scheme"]
    q = "?token=" + quote(RUNTIME["token"])
    print()
    print("  Gyro Arm is up.")
    print(f"    auth token   {RUNTIME['token']}   (all URLs below include it)")
    if len(ips) > 1:
        listing = ", ".join(e["ip"] + (f" ({e['label']})" if e["label"] else "")
                            for e in ips)
        print(f"    addresses on this machine: {listing}")
    if best["label"]:
        print(f"    using {best['ip']} ({best['label']})")
    print(f"    dashboard    {scheme}://{best['ip']}:{args.port}/{q}")
    print(f"    controller   {scheme}://{best['ip']}:{args.port}/controller{q}    (phone; needs --https)")
    print(f"    data sink    {scheme}://{best['ip']}:{args.port}/data{q}          (Sensor Logger HTTP Push URL)")
    if args.bind.startswith("127.") or args.bind == "localhost":
        print()
        print("  ! bound to 127.0.0.1 -- phones CANNOT connect.")
        print("    For LAN/hotspot use restart with:  python app.py --bind 0.0.0.0")
    if scheme == "https" and not args.cert:
        print()
        print("  note: --https uses a self-signed cert: it encrypts the link but does")
        print("        not authenticate this server. Use --cert/--key for that.")
    print()


def main():
    args = parse_args()
    validate_config()
    RUNTIME["port"] = args.port
    RUNTIME["bind"] = args.bind
    RUNTIME["forced_ip"] = args.ip
    RUNTIME["token"] = args.token or secrets.token_urlsafe(16)
    RUNTIME["scheme"] = "https" if (args.https or args.cert) else "http"

    threading.Thread(target=control_loop, daemon=True).start()
    banner(args)

    run_kwargs = dict(host=args.bind, port=args.port, threaded=True, debug=False)
    if args.cert:
        run_kwargs["ssl_context"] = (args.cert, args.key)
    elif args.https:
        run_kwargs["ssl_context"] = "adhoc"  # pip install cryptography
    app.run(**run_kwargs)


if __name__ == "__main__":
    main()
