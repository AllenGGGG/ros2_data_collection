import os
from pathlib import Path

import yaml
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, ExecuteProcess, OpaqueFunction
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.substitutions import FindPackageShare
from launch_ros.actions import Node


def _launch_value(context, name):
    return LaunchConfiguration(name).perform(context).strip()


def _load_inference_launch_config(config_file):
    path = Path(config_file).expanduser()
    if not path.is_file():
        raise RuntimeError(f'inference_launch_config_file does not exist: {path}')
    with path.open('r', encoding='utf-8') as stream:
        data = yaml.safe_load(stream) or {}
    if not isinstance(data, dict):
        raise RuntimeError(f'{path}: launch config must be a mapping')
    return {
        key: str(value).strip()
        for key, value in data.items()
        if value is not None and str(value).strip()
    }


def _make_inference_process(context):
    run_inference = IfCondition(LaunchConfiguration('run_inference')).evaluate(context)
    if not run_inference:
        return []

    inference_params_file = _launch_value(context, 'inference_params_file')
    inference_launch_config_file = _launch_value(context, 'inference_launch_config_file')
    launch_config = _load_inference_launch_config(inference_launch_config_file)
    python_executable = (
        _launch_value(context, 'python_executable')
        or launch_config.get('python_executable')
        or 'python3'
    )
    inference_script = (
        _launch_value(context, 'inference_script')
        or launch_config.get('inference_script')
    )
    if not inference_script:
        raise RuntimeError(
            'inference_script is empty. Set inference_script in '
            f'{inference_launch_config_file}, or pass inference_script:=...'
        )
    if not Path(inference_script).expanduser().is_file():
        raise RuntimeError(f'inference_script does not exist: {inference_script}')
    python_path = launch_config.get('python_path', '')
    additional_env = {}
    if python_path:
        python_path = str(Path(python_path).expanduser())
        if not Path(python_path).is_dir():
            raise RuntimeError(f'python_path does not exist: {python_path}')
        existing_python_path = os.environ.get('PYTHONPATH', '')
        additional_env['PYTHONPATH'] = (
            python_path
            if not existing_python_path
            else python_path + os.pathsep + existing_python_path
        )

    return [
        ExecuteProcess(
            output='screen',
            additional_env=additional_env,
            cmd=[
                python_executable,
                inference_script,
                '--ros-args',
                '--params-file', inference_params_file,
            ],
        )
    ]


def generate_launch_description():
    output_dir = LaunchConfiguration('output_dir')
    profile_path = LaunchConfiguration('profile_path')
    storage_preset_profile = LaunchConfiguration('storage_preset_profile')
    intervention_topic = LaunchConfiguration('intervention_topic')
    intervention_publish_hz = LaunchConfiguration('intervention_publish_hz')
    minimum_episode_size_mb = LaunchConfiguration('minimum_episode_size_mb')
    maximum_episode_size_mb = LaunchConfiguration('maximum_episode_size_mb')

    inference_params_file = LaunchConfiguration('inference_params_file')

    default_inference_params_file = PathJoinSubstitution([
        FindPackageShare('data_collection_recap_recorder'),
        'config',
        'inference',
        'no_subtask_inference.yaml',
    ])
    default_inference_launch_config_file = PathJoinSubstitution([
        FindPackageShare('data_collection_recap_recorder'),
        'config',
        'inference',
        'no_subtask_launch.yaml',
    ])

    return LaunchDescription([
        DeclareLaunchArgument(
            'output_dir',
            default_value='',
            description='Override recap output directory. Empty = use recap profile YAML output_dir.',
        ),
        DeclareLaunchArgument(
            'profile_path',
            default_value='',
            description='Optional recap recording profile YAML. Defaults to data_collection_recap_recorder profile.',
        ),
        DeclareLaunchArgument(
            'storage_preset_profile',
            default_value='zstd_small',
            description='MCAP preset passed to native ros2 bag record.',
        ),
        DeclareLaunchArgument(
            'intervention_topic',
            default_value='/intervention',
            description='Recap intervention topic recorded into MCAP.',
        ),
        DeclareLaunchArgument(
            'intervention_publish_hz',
            default_value='10.0',
            description='Publish frequency for the /intervention state.',
        ),
        DeclareLaunchArgument(
            'minimum_episode_size_mb',
            default_value='100.0',
            description='Minimum previous MCAP size required before starting the next episode.',
        ),
        DeclareLaunchArgument(
            'maximum_episode_size_mb',
            default_value='145.0',
            description='Maximum previous MCAP size allowed before starting the next episode.',
        ),
        DeclareLaunchArgument(
            'run_inference',
            default_value='true',
            description='Start the no-subtask inference runtime together with recap recording.',
        ),
        DeclareLaunchArgument(
            'python_executable',
            default_value='',
            description='Override Python executable. Empty = use inference launch config YAML.',
        ),
        DeclareLaunchArgument(
            'inference_script',
            default_value='',
            description='Override inference runtime script. Empty = use inference launch config YAML.',
        ),
        DeclareLaunchArgument(
            'inference_launch_config_file',
            default_value=default_inference_launch_config_file,
            description='YAML file containing python_executable and inference_script.',
        ),
        DeclareLaunchArgument(
            'inference_params_file',
            default_value=default_inference_params_file,
            description='YAML params file for the no-subtask inference runtime.',
        ),
        Node(
            package='data_collection_recap_recorder',
            executable='recap_mcap_recorder',
            name='recap_mcap_recorder',
            output='screen',
            parameters=[{
                'output_dir': output_dir,
                'profile_path': profile_path,
                'storage_preset_profile': storage_preset_profile,
                'intervention_topic': intervention_topic,
                'intervention_publish_hz': intervention_publish_hz,
                'minimum_episode_size_mb': minimum_episode_size_mb,
                'maximum_episode_size_mb': maximum_episode_size_mb,
            }],
        ),
        OpaqueFunction(function=_make_inference_process),
    ])
