from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument(
            'server', default_value='127.0.0.1:8000',
            description='host:port of the Gyro Arm Flask server '
                        '(e.g. 172.20.10.4:8000 on an iPhone hotspot)'),
        DeclareLaunchArgument(
            'token', default_value='',
            description='auth token printed by the app.py banner'),
        DeclareLaunchArgument(
            'frame_id', default_value='base_link',
            description='frame for target_pose / cmd_vel_stamped headers'),
        Node(
            package='gyro_teleop',
            executable='bridge',
            name='gyro_teleop',
            output='screen',
            parameters=[{
                'server': LaunchConfiguration('server'),
                'token': LaunchConfiguration('token'),
                'frame_id': LaunchConfiguration('frame_id'),
                # Set the rest with -p on the command line or a params YAML:
                # tls, ca_cert, publish_rate_hz, stale_timeout_s,
                # workspace_width_m, workspace_height_m, reach_x_m, z_min_m,
                # planar_twist, planar_linear_scale, planar_angular_scale.
            }],
        ),
    ])
