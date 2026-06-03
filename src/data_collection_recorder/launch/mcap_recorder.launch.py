from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    output_dir = LaunchConfiguration('output_dir')
    profile_path = LaunchConfiguration('profile_path')
    storage_preset_profile = LaunchConfiguration('storage_preset_profile')

    return LaunchDescription([
        DeclareLaunchArgument(
            'output_dir',
            default_value='',
            description='Override output directory. Empty = use profile YAML output_dir.',
        ),
        DeclareLaunchArgument(
            'profile_path',
            default_value='',
            description='Optional recording profile YAML. Defaults to package config.',
        ),
        DeclareLaunchArgument(
            'storage_preset_profile',
            default_value='zstd_small',
            description='MCAP preset passed to native ros2 bag record.',
        ),
        Node(
            package='data_collection_recorder',
            executable='mcap_recorder',
            name='mcap_recorder',
            output='screen',
            parameters=[{
                'output_dir': output_dir,
                'profile_path': profile_path,
                'storage_preset_profile': storage_preset_profile,
            }],
        ),
    ])
