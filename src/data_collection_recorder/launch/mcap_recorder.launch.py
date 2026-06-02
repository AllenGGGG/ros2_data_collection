from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    output_dir = LaunchConfiguration('output_dir')
    profile_path = LaunchConfiguration('profile_path')compressed
    storage_config_path = LaunchConfiguration('storage_config_path')
    storage_preset_profile = LaunchConfiguration('storage_preset_profile')

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
            description='Optional MCAP writer YAML. Empty = use package mcap_storage.yaml if present.',
        ),
        DeclareLaunchArgument(
            'storage_preset_profile',
            default_value='zstd_small',
            description='MCAP preset: zstd_small (smaller, slower) recommended for raw images.',
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
                'storage_preset_profile': storage_preset_profile,
            }],
        ),
    ])
