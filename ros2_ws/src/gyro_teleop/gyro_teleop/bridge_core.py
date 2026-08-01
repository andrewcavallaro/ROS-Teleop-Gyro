"""Framework-agnostic core shared by the ROS 1 and ROS 2 bridges.

Owns everything that is not ROS: parameter validation, the authenticated
WebSocket/HTTP transport with reconnection, strict validation of every server
snapshot, freshness/deadman bookkeeping, and the mapping from snapshots to
output commands. The rclpy/rospy nodes are thin adapters that turn a
``BridgeCore.compute()`` result into messages.

Deadman semantics (enforced here, identically for both ROS versions):
  * twist components are ZERO unless the whole chain is fresh;
  * ``pose`` is None unless fresh -- a stale target is never re-issued;
  * ``joy_axes`` are zeroed unless fresh (buttons = [grip, fresh]);
  * ``grip`` deliberately HOLDS its last value on signal loss (a dropped
    packet must not open the gripper) -- the only held command.

Every snapshot is validated (types, finiteness, ranges) before it can replace
the last known-good state; invalid data immediately poisons freshness.
"""

import json
import math
import ssl
import threading
import time
import urllib.parse
import urllib.request

try:
    from websocket import create_connection  # pip install websocket-client
    HAVE_WEBSOCKET = True
except ImportError:
    HAVE_WEBSOCKET = False

# One place for every parameter and its default; both adapters declare from
# this dict, so they can never drift apart.
DEFAULTS = {
    'server': '127.0.0.1:8000',   # host:port of app.py
    'token': '',                  # auth token from the app.py banner
    'tls': False,                 # true if app.py serves TLS
    'ca_cert': '',                # pin/verify the server cert (see README)
    'publish_rate_hz': 30.0,
    'stale_timeout_s': 0.5,       # bridge-side deadman
    'frame_id': 'base_link',
    'workspace_width_m': 0.40,    # left/right span of the normalized box
    'workspace_height_m': 0.40,   # up/down span
    'reach_x_m': 0.30,            # fixed forward reach of target_pose
    'z_min_m': 0.10,              # height of the workspace bottom
    'planar_twist': False,        # ground-robot mode: pitch->lin.x, yaw->ang.z
    'planar_linear_scale': 2.0,   # m/s per workspace-width/s
    'planar_angular_scale': 2.0,  # rad/s per workspace-width/s
}


def _finite(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) \
        and math.isfinite(float(v))


def validate_params(overrides):
    """Merge overrides onto DEFAULTS, coerce types, reject nonsense.
    Raises ValueError with a clear message."""
    p = dict(DEFAULTS)
    for k, v in (overrides or {}).items():
        if k in p and v is not None:
            p[k] = v

    def bad(msg):
        raise ValueError('gyro_arm bridge parameter error: ' + msg)

    out = {k: str(p[k]) for k in ('server', 'token', 'ca_cert', 'frame_id')}
    out['tls'] = bool(p['tls'])
    out['planar_twist'] = bool(p['planar_twist'])
    for k in ('publish_rate_hz', 'stale_timeout_s', 'workspace_width_m',
              'workspace_height_m', 'reach_x_m', 'z_min_m',
              'planar_linear_scale', 'planar_angular_scale'):
        try:
            out[k] = float(p[k])
        except (TypeError, ValueError):
            bad(f'{k} must be a number')
    if not (math.isfinite(out['publish_rate_hz']) and out['publish_rate_hz'] > 0):
        bad('publish_rate_hz must be > 0')
    if not (math.isfinite(out['stale_timeout_s']) and out['stale_timeout_s'] > 0):
        bad('stale_timeout_s must be > 0')
    for k in ('workspace_width_m', 'workspace_height_m'):
        if not (math.isfinite(out[k]) and out[k] > 0):
            bad('workspace dimensions must be positive finite numbers')
    for k in ('reach_x_m', 'z_min_m'):
        if not math.isfinite(out[k]):
            bad('reach_x_m / z_min_m must be finite')
    for k in ('planar_linear_scale', 'planar_angular_scale'):
        if not (math.isfinite(out[k]) and 0 < out[k] <= 100):
            bad('planar scales must be finite, > 0 and <= 100')
    return out


def validate_snapshot(raw):
    """Parse an untrusted server snapshot into a typed dict, or None."""
    if not isinstance(raw, dict):
        return None

    def num(v, lo, hi):
        if not _finite(v):
            raise ValueError
        f = float(v)
        if f < lo or f > hi:
            raise ValueError
        return f

    try:
        out = {
            'x': num(raw['x'], 0.0, 1.0),
            'y': num(raw['y'], 0.0, 1.0),
            'vx': num(raw['vx'], -10.0, 10.0),
            'vy': num(raw['vy'], -10.0, 10.0),
            'roll': num(raw['roll'], -360.0, 360.0),
            'pitch': num(raw['pitch'], -360.0, 360.0),
            'yaw': num(raw['yaw'], -360.0, 360.0),
        }
    except (KeyError, ValueError, TypeError):
        return None
    if not isinstance(raw.get('grip_closed'), bool):
        return None
    if not isinstance(raw.get('connected'), bool):
        return None
    out['grip_closed'] = raw['grip_closed']
    out['connected'] = raw['connected']
    cfg = raw.get('cfg')
    max_tilt = cfg.get('max_tilt') if isinstance(cfg, dict) else None
    out['max_tilt'] = (float(max_tilt)
                       if _finite(max_tilt) and 1.0 <= float(max_tilt) <= 180.0
                       else 40.0)
    return out


class Output:
    """One publish tick's worth of commands, in plain numbers."""

    def __init__(self):
        self.fresh = False
        self.lin_x = self.lin_y = self.lin_z = self.ang_z = 0.0
        self.pose = None          # (x, y, z) metres, or None when not fresh
        self.grip = None          # bool, or None if no snapshot ever arrived
        self.joy_axes = [0.0, 0.0, 0.0]
        self.joy_buttons = [0, 0]  # [grip, fresh]


class BridgeCore:

    def __init__(self, params, info=lambda m: None, warn=lambda m: None):
        p = validate_params(params)
        self.server = p['server']
        self.token = p['token']
        self.tls = p['tls']
        self.ca_cert = p['ca_cert']
        self.rate_hz = p['publish_rate_hz']
        self.stale_s = p['stale_timeout_s']
        self.frame_id = p['frame_id']
        self.ws_w = p['workspace_width_m']
        self.ws_h = p['workspace_height_m']
        self.reach_x = p['reach_x_m']
        self.z_min = p['z_min_m']
        self.planar = p['planar_twist']
        self.planar_lin = p['planar_linear_scale']
        self.planar_ang = p['planar_angular_scale']

        self.info, self.warn = info, warn
        self._lock = threading.Lock()
        self._snap = None          # last VALIDATED snapshot
        self._snap_time = 0.0      # monotonic time it arrived
        self._server_ok = False
        self._poisoned = False     # last received snapshot was invalid
        self._last_fresh = None
        self._last_retry_warn = 0.0
        self._last_invalid_warn = 0.0
        self._stop_check = lambda: True

        if not self.token:
            warn('no auth token set (token param); the server will refuse requests')
        if self.tls and not self.ca_cert:
            warn('tls without ca_cert: certificate verification is DISABLED. '
                 'The link is encrypted but the command source is NOT '
                 'authenticated; pass ca_cert for real hardware.')

    # -- data source (background thread) -----------------------------------
    def start(self, stop_check):
        """Start the transport thread. `stop_check()` -> True means shut down."""
        self._stop_check = stop_check
        threading.Thread(target=self._source_loop, daemon=True).start()

    def _qs(self):
        return ('?token=' + urllib.parse.quote(self.token)) if self.token else ''

    def _source_loop(self):
        if not HAVE_WEBSOCKET:
            self.warn('websocket-client not installed; falling back to HTTP '
                      'polling (pip install websocket-client for the '
                      'lower-latency stream)')
        while not self._stop_check():
            try:
                if HAVE_WEBSOCKET:
                    self._run_websocket()
                else:
                    self._run_polling()
            except Exception as exc:  # noqa: BLE001 - any transport error means reconnect
                with self._lock:
                    self._server_ok = False
                now = time.monotonic()
                if now - self._last_retry_warn > 5.0:
                    self.warn(f'Gyro Arm server unreachable ({exc}); retrying...')
                    self._last_retry_warn = now
                time.sleep(1.0)

    def _ws_sslopt(self):
        if not self.tls:
            return None
        if self.ca_cert:
            # Pin against the provided cert; hostname check is skipped because
            # LAN/hotspot IPs rarely match the certificate name -- the pinned
            # key itself authenticates the server.
            return {'cert_reqs': ssl.CERT_REQUIRED, 'ca_certs': self.ca_cert,
                    'check_hostname': False}
        return {'cert_reqs': ssl.CERT_NONE}

    def _http_ctx(self):
        if not self.tls:
            return None
        if self.ca_cert:
            ctx = ssl.create_default_context(cafile=self.ca_cert)
            ctx.check_hostname = False
            return ctx
        return ssl._create_unverified_context()

    def _run_websocket(self):
        # Do NOT flip _server_ok on handshake alone: a successful upgrade
        # doesn't yet prove the server is producing snapshots. If we flipped
        # it here, an old but still-fresh-by-time snapshot could briefly be
        # re-marked live in the window between reconnect and the first frame
        # (matters when stale_timeout_s is set larger than the retry backoff).
        # _store_raw() sets _server_ok=True only when a frame actually arrives.
        scheme = 'wss' if self.tls else 'ws'
        ws = create_connection(f'{scheme}://{self.server}/ws/state{self._qs()}',
                               timeout=3, sslopt=self._ws_sslopt())
        self.info(f'connected to {scheme}://{self.server}/ws/state')
        try:
            while not self._stop_check():
                self._store_raw(ws.recv())
        finally:
            ws.close()

    def _run_polling(self):
        # Same rule as the WebSocket path: _store_raw() owns _server_ok.
        scheme = 'https' if self.tls else 'http'
        url = f'{scheme}://{self.server}/api/state{self._qs()}'
        self.info(f'polling {scheme}://{self.server}/api/state')
        period = 1.0 / self.rate_hz
        while not self._stop_check():
            with urllib.request.urlopen(url, timeout=1,
                                        context=self._http_ctx()) as resp:
                self._store_raw(resp.read())
            time.sleep(period)

    def _store_raw(self, raw):
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            parsed = None
        snap = validate_snapshot(parsed)
        with self._lock:
            # A frame arrived: the transport itself is proven healthy now,
            # not merely on handshake. This is the ONLY place _server_ok
            # flips true, so an old snapshot can't be re-marked live during
            # the reconnect->first-frame gap.
            self._server_ok = True
            if snap is None:
                # Never let a malformed snapshot replace known-good state, and
                # immediately stop trusting the stream until a valid one arrives.
                self._poisoned = True
            else:
                self._snap = snap
                self._snap_time = time.monotonic()
                self._poisoned = False
        if snap is None:
            now = time.monotonic()
            if now - self._last_invalid_warn > 5.0:
                self.warn('invalid snapshot from server; motion zeroed')
                self._last_invalid_warn = now

    # -- output -------------------------------------------------------------
    def compute(self):
        """Produce this tick's commands. Logs fresh/paused transitions."""
        with self._lock:
            snap = self._snap
            age = time.monotonic() - self._snap_time
            server_ok = self._server_ok
            poisoned = self._poisoned

        out = Output()
        out.fresh = bool(snap) and server_ok and not poisoned \
            and age < self.stale_s and snap['connected']

        if out.fresh != self._last_fresh:
            if out.fresh:
                self.info('teleop live: phone -> server -> bridge')
            else:
                why = ('no server' if not server_ok
                       else 'invalid data' if poisoned
                       else 'phone not streaming' if snap and not snap['connected']
                       else 'stale data')
                self.warn(f'teleop paused ({why}); velocities zeroed')
            self._last_fresh = out.fresh

        if out.fresh:
            if self.planar:
                out.lin_x = snap['vy'] * self.planar_lin
                out.ang_z = -snap['vx'] * self.planar_ang
            else:
                # Server vx is +right in workspace-widths/s; REP 103 +y is LEFT.
                out.lin_y = -snap['vx'] * self.ws_w
                out.lin_z = snap['vy'] * self.ws_h
            out.pose = (self.reach_x,
                        (0.5 - snap['x']) * self.ws_w,
                        self.z_min + snap['y'] * self.ws_h)
            mt = snap['max_tilt']

            def clip(deg):
                return max(-1.0, min(1.0, deg / mt))

            out.joy_axes = [clip(snap['yaw']), clip(snap['pitch']),
                            clip(snap['roll'])]
        if snap is not None:
            out.grip = snap['grip_closed']
        out.joy_buttons = [1 if (snap and snap['grip_closed']) else 0,
                           1 if out.fresh else 0]
        return out
