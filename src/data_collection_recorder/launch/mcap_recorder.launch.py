from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    output_dir = LaunchConfiguration('output_dir')
    profile_path = LaunchConfiguration('profile_path')
    storage_config_path = LaunchConfiguration('storage_config_path')

    return LaunchDescription([
        DeclareLaunchArgument(
            'output_dir',
            default_value='~/ros2_ws/raw_datasets_mcap',
            description='Directory where episode folders will be created.',
        ),
        DeclareLaunchArgument(
            'profile_path',
            default_value='',
            description='Optional recording profile YAML. Defaults to package config.',
        ),
        DeclareLaunchArgument(
            'storage_config_path',
            default_value='',
            description='Optional rosbag2 MCAP storage config YAML. Defaults to package config.',
        ),
        Node(
            package='data_collection_recorder',
            executable='mcap_recorder',
            name='mcap_recorder',
            output='screen',
            parameters=[{
                'output_dir': output_dir,
                'profile_path': profile_path,
                'storage_config_path': storage_config_path,
            }],
        ),
    ])
