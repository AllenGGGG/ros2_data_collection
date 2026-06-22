from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, ExecuteProcess
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.substitutions import FindPackageShare
from launch_ros.actions import Node


def generate_launch_description():
    output_dir = LaunchConfiguration('output_dir')
    profile_path = LaunchConfiguration('profile_path')
    storage_preset_profile = LaunchConfiguration('storage_preset_profile')
    intervention_topic = LaunchConfiguration('intervention_topic')
    intervention_publish_hz = LaunchConfiguration('intervention_publish_hz')

    run_inference = LaunchConfiguration('run_inference')
    python_executable = LaunchConfiguration('python_executable')
    inference_script = LaunchConfiguration('inference_script')
    inference_params_file = LaunchConfiguration('inference_params_file')

    default_inference_script = (
        '/home/zihang/workspace/chekp/IsaacSim-ros_workspaces/jazzy_ws/src/isaac_tutorials/scripts/'
        'pistar06/post_train/trajs683_delta/'
        'pistar06_inference_post_train_trajs683_delta_speedup_2x_chunksize_35_async_scan_runtime.py'
    )
    default_inference_params_file = PathJoinSubstitution([
        FindPackageShare('data_collection_recap_recorder'),
        'config',
        'inference',
        'no_subtask_inference.yaml',
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
            'run_inference',
            default_value='true',
            description='Start the no-subtask inference runtime together with recap recording.',
        ),
        DeclareLaunchArgument(
            'python_executable',
            default_value='python3',
            description='Python executable used to run the inference script.',
        ),
        DeclareLaunchArgument(
            'inference_script',
            default_value=default_inference_script,
            description='Path to the no-subtask inference runtime script.',
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
            }],
        ),
        ExecuteProcess(
            condition=IfCondition(run_inference),
            output='screen',
            cmd=[
                python_executable,
                inference_script,
                '--ros-args',
                '--params-file', inference_params_file,
            ],
        ),
    ])
