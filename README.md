# Gyroscope-Teleop

Steer a ROS 2 robot by tilting an iPhone. The phone streams its fused
orientation (roll / pitch / yaw) to a small Flask server; a ROS 2 bridge
turns that stream into standard command topics — `joy`, `cmd_vel`,
`target_pose`, `grip` — with a deadman that zeroes motion the instant the
phone drops off the network.

Built and verified on a **Jetson Orin Nano Super (JetPack 7.2, Ubuntu
24.04, ROS 2 Jazzy, Python 3.12)**, over an iPhone personal hotspot.

```
iPhone  ──orientation POSTs──►  Flask server  ──WebSocket──►  ROS 2 bridge  ──►  /gyro_teleop/*
 (Sensor Logger, 100 ms batch)   validate, 60 Hz              validate,           joy · cmd_vel
                                 control loop                 freshness/deadman   target_pose · grip
```

The design goal is **safety made visible**: every command topic is gated
on end-to-end freshness, a malformed packet can never move the robot, and
a dropped phone freezes motion rather than coasting on the last command.
The [snake example](https://github.com/andrewcavallaro/gyroscope-teleop-examples) is a
5-minute proof you can watch those gates fire in real time.

---

## What's in here

```
server/                 Flask teleop server (no ROS; runs anywhere)
  app.py                CLI entry point (bind, port, token, TLS)
  gyro_arm/             one concern per module — ingest, motion, state,
                        control loop, security, server routes
  tests/                unit / HTTP+WS / template / ROS-bridge suites
ros2_ws/src/gyro_teleop/
  gyro_teleop/
    bridge_core.py      framework-agnostic core: transport, snapshot
                        validation, deadman, snapshot→command mapping
    bridge_node.py      thin rclpy adapter (declares params, publishes)
  launch/bridge.launch.py
```

The ROS-independent `bridge_core.py` owns all the logic; the node is a
thin adapter. That split is what lets the same core be unit-tested with
no ROS installed and keeps the safety behavior identical everywhere.

---

## Topic contract

The bridge runs under the node name `gyro_teleop`, so its topics are:

| Topic | Type | Notes |
|---|---|---|
| `/gyro_teleop/joy` | `sensor_msgs/Joy` | `axes = [yaw, pitch, roll]` in ±1; `buttons = [grip_closed, fresh]`. Axes zeroed when not fresh. |
| `/gyro_teleop/cmd_vel` | `geometry_msgs/Twist` | jog velocity, m/s (REP-103: +y left, +z up). Zeroed when not fresh. |
| `/gyro_teleop/cmd_vel_stamped` | `geometry_msgs/TwistStamped` | same, stamped (MoveIt Servo-style consumers). |
| `/gyro_teleop/target_pose` | `geometry_msgs/PoseStamped` | absolute target, metres. Published **only** while fresh. |
| `/gyro_teleop/grip_closed` | `std_msgs/Bool` | gripper command. **Holds** its last value on signal loss (a dropped packet must not open a gripper). |
| `/gyro_teleop/connected` | `std_msgs/Bool` | end-to-end liveness. Gate your controller on this. |

This is the interface examples and downstream controllers build against.

---

## Install (Jetson / any ROS 2 Jazzy machine)

**Server deps** (pure Python; `flask-sock` serves the WebSocket,
`websocket-client` is what the bridge and the WebSocket tests consume it
with, `cryptography` is only needed for the optional `--https` Safari
controller):

```bash
cd server
pip3 install -r requirements.txt          # add --break-system-packages on 24.04
```

**ROS 2 bridge** — `ros2_ws/` is already a colcon workspace, so build it
in place:

```bash
cd ros2_ws
rosdep install --from-paths src --ignore-src -y   # optional; fetches deps
colcon build --packages-select gyro_teleop
source install/setup.bash
```

Or copy `ros2_ws/src/gyro_teleop` into a workspace you already have and
build there. `rclpy`, `geometry_msgs`, `sensor_msgs`, `std_msgs` come from
your ROS 2 install.

Source the base ROS environment **before** the workspace overlay in every
new shell — the overlay does not provide `ros2` itself:

```bash
source /opt/ros/jazzy/setup.bash
source install/setup.bash
```

---

## Run

Assumes an iPhone personal hotspot with the Jetson at `172.20.10.6`.

**1 — server** (bind to the hotspot so the phone can reach it):

```bash
cd server
python3 app.py --bind 0.0.0.0 --ip 172.20.10.6
```

The banner prints an auth token and ready-made URLs (`...?token=...`).
Copy the token.

**2 — phone.** Install **Sensor Logger** (free, iOS). Three settings
matter:

| Setting | Value | Why |
|---|---|---|
| Sensor list | **Orientation** enabled | The server wants fused roll/pitch/yaw. Gyroscope alone is angular velocity and will be rejected. |
| HTTP Push → Push URL | the `/data?token=...` URL from the banner, verbatim | The token rides in the query string; the path is `/data`. |
| HTTP Push → **Batch Period** | **100 ms** | The default `1s` exceeds the bridge's 0.6 s stale timeout, so the deadman fires between every batch and motion stutters to a halt. |

Leave **Auth Header** as `None` — the token is already in the URL.

Then press **Record**. HTTP Push only transmits while a recording is
active; the "Test Push" button alone is not enough.

Verify:

```bash
curl -s "http://172.20.10.6:8000/api/state?token=<TOKEN>" | python3 -m json.tool
# connected: true, source: "sensor-logger", roll/pitch/yaw change as you tilt
```

**3 — calibrate.** Hold the phone in a comfortable neutral pose and:

```bash
curl -s -X POST "http://172.20.10.6:8000/calibrate?token=<TOKEN>"
```

This captures the current orientation as zero. Skip it and the axes tend
to sit pinned at ±1 (everything reads as "tilted hard"), which looks like
a broken sensor but isn't. Note the route is `/calibrate`, **not**
`/api/calibrate`.

**4 — bridge:**

```bash
ros2 launch gyro_teleop bridge.launch.py server:=127.0.0.1:8000 token:=<TOKEN>
```

**5 — watch it work:**

```bash
ros2 topic list | grep gyro_teleop
ros2 topic echo /gyro_teleop/joy      # axes move as you tilt the phone
```

`buttons[1]` is the freshness flag — it reads `1` while the phone is
live. Point any controller at the topics above, or run the
[snake example](https://github.com/andrewcavallaro/gyroscope-teleop-examples) to see
the whole pipeline — including the deadman — in one terminal.

---

## Troubleshooting

Read the HTTP status in the server's own log — it localises the fault
precisely.

| Symptom | Meaning | Fix |
|---|---|---|
| `POST /data … 422` | Reached the server and authenticated, but no usable orientation in the batch. | Enable **Orientation** in Sensor Logger's sensor list and press **Record**. |
| `POST /data … 401` | Token missing or wrong. | Copy the whole `data sink` URL from the banner; the token regenerates on every server start. |
| `GET /data … 405` | Something sent a GET to a POST-only endpoint (e.g. opening the URL in Safari). | Harmless — it confirms the phone can reach the server. |
| `… 413` | Batch too large. | Disable sensors you don't need; keep **Send Images** off. |
| `connected: false`, `source: "none"` | No packet has ever arrived. | Recording not started, wrong IP, or devices on different networks. |
| `connected` flickers true/false | Batch period exceeds the stale timeout. | Set **Batch Period** to 100 ms. |
| Axes pinned at ±1 | Neutral pose never captured. | `POST /calibrate` while holding the phone neutral. |
| `ros2: command not found` | Base ROS environment not sourced. | `source /opt/ros/jazzy/setup.bash` before the workspace overlay. |
| Package builds but `ros2 pkg executables` is empty | Console script landed in `bin/` instead of `lib/<pkg>/`. | The package needs a `setup.cfg` with `script_dir`/`install_scripts` pointing at `$base/lib/<pkg>`. |

---

## Safety model (the interesting part)

- **Freshness is end-to-end.** `bridge_core` marks a tick fresh only when
  a valid snapshot arrived within `stale_timeout_s` **and** the server
  reports the phone connected **and** the last packet parsed clean. Miss
  any one and velocities are zeroed.
- **Invalid input poisons the stream.** A malformed snapshot never
  replaces known-good state and immediately drops freshness until a valid
  one arrives — a garbage packet can't move the robot.
- **The gripper holds, everything else zeroes.** On signal loss, twist
  and pose go to zero/None, but `grip_closed` deliberately holds its last
  value — dropping a packet must not fling a gripper open.
- **Auth on every request.** The server generates a token at startup and
  requires it on every endpoint and WebSocket; cross-origin WS
  connections are refused; it binds to localhost unless you pass
  `--bind`.

---

## Notes & limits

- Verified on JetPack 7.2 / Jazzy / Python 3.12 over an iPhone hotspot.
  The ROS-free core is covered by the bundled tests; CI additionally
  `colcon build`s the package.
- `--https` uses a self-signed cert (encrypts only). For authenticated
  hardware use, supply your own `--cert`/`--key` and pin it with
  `ca_cert`.
- iOS exposes motion sensors to web pages only over HTTPS — the built-in
  Safari controller needs `--https`; Sensor Logger over plain HTTP does
  not.
