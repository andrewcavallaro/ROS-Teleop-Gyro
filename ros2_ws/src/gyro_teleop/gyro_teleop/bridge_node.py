#!/usr/bin/env python3
"""ROS 2 adapter for Gyroscope-Teleop.

All logic (transport, validation, deadman, mapping) lives in bridge_core.py;
this file only declares parameters and turns each ``BridgeCore.compute()``
result into ROS 2 messages:

    ~/cmd_vel          geometry_msgs/Twist         jog velocity, m/s (REP 103: +y left, +z up); zeroed when not fresh
    ~/cmd_vel_stamped  geometry_msgs/TwistStamped  same, stamped (MoveIt Servo-style consumers)
    ~/target_pose      geometry_msgs/PoseStamped   absolute target, metres; published ONLY while fresh
    ~/grip_closed      std_msgs/Bool               gripper command; HOLDS last value on loss (documented)
    ~/joy              sensor_msgs/Joy             axes [yaw,pitch,roll] +/-1, zeroed when not fresh; buttons [grip,fresh]
    ~/connected        std_msgs/Bool               end-to-end liveness (gate your controller on it)

Run inside a sourced ROS 2 environment:

    ros2 run gyro_teleop bridge --ros-args -p server:=172.20.10.4:8000 -p token:=SECRET
"""

import rclpy
from rclpy.node import Node

from geometry_msgs.msg import PoseStamped, Twist, TwistStamped
from sensor_msgs.msg import Joy
from std_msgs.msg import Bool

try:
    from gyro_teleop.bridge_core import DEFAULTS, BridgeCore
except ImportError:  # executed directly as a script, next to bridge_core.py
    from bridge_core import DEFAULTS, BridgeCore


class GyroTeleopBridge(Node):

    def __init__(self):
        super().__init__('gyro_teleop')

        for name, default in DEFAULTS.items():
            self.declare_parameter(name, default)
        params = {n: self.get_parameter(n).value for n in DEFAULTS}

        log = self.get_logger()
        self.core = BridgeCore(params,
                               info=lambda m: log.info(m),
                               warn=lambda m: log.warn(m))
        self.frame_id = self.core.frame_id

        # Depth-1 QoS: these are commands; only the latest matters, and a
        # queue must never replay old ones.
        self.pub_twist = self.create_publisher(Twist, '~/cmd_vel', 1)
        self.pub_twist_stamped = self.create_publisher(TwistStamped, '~/cmd_vel_stamped', 1)
        self.pub_pose = self.create_publisher(PoseStamped, '~/target_pose', 1)
        self.pub_grip = self.create_publisher(Bool, '~/grip_closed', 1)
        self.pub_joy = self.create_publisher(Joy, '~/joy', 1)
        self.pub_conn = self.create_publisher(Bool, '~/connected', 1)

        self._stop = False
        self.core.start(lambda: self._stop)
        self.create_timer(1.0 / self.core.rate_hz, self._publish)

    def _publish(self):
        try:
            self._publish_inner()
        except Exception as exc:  # noqa: BLE001 - keep the timer alive
            self.get_logger().error(f'publish tick failed: {exc}')

    def _publish_inner(self):
        out = self.core.compute()
        stamp = self.get_clock().now().to_msg()

        tw = Twist()
        tw.linear.x = out.lin_x
        tw.linear.y = out.lin_y
        tw.linear.z = out.lin_z
        tw.angular.z = out.ang_z
        self.pub_twist.publish(tw)

        tws = TwistStamped()
        tws.header.stamp = stamp
        tws.header.frame_id = self.frame_id
        tws.twist = tw
        self.pub_twist_stamped.publish(tws)

        if out.pose is not None:  # position commands only while fresh
            pose = PoseStamped()
            pose.header.stamp = stamp
            pose.header.frame_id = self.frame_id
            pose.pose.position.x, pose.pose.position.y, pose.pose.position.z = out.pose
            pose.pose.orientation.w = 1.0
            self.pub_pose.publish(pose)

        if out.grip is not None:  # documented HOLD of last gripper command
            self.pub_grip.publish(Bool(data=out.grip))

        joy = Joy()
        joy.header.stamp = stamp
        joy.header.frame_id = self.frame_id
        joy.axes = list(out.joy_axes)
        joy.buttons = list(out.joy_buttons)
        self.pub_joy.publish(joy)

        self.pub_conn.publish(Bool(data=out.fresh))


def main(args=None):
    rclpy.init(args=args)
    node = GyroTeleopBridge()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node._stop = True
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
