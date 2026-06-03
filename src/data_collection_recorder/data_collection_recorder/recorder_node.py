from pathlib import Path
from typing import Any

import rclpy
from ament_index_python.packages import get_package_share_directory
from rclpy.callback_groups import MutuallyExclusiveCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.qos import QoSProfile
from std_msgs.msg import Int32

from data_collection_core.bag_ros2_cli import BagRos2CliBackend
from data_collection_core.constants import (
    DEFAULT_CONTROL_TOPIC,
    INFERENCE_PAUSED_CODE,
    INFERENCE_RESUMED_CODE,
    START_RECORDING_CODE,
    STOP_RECORDING_CODE,
)
from data_collection_core.session import EpisodeInfo, EpisodeSession
from data_collection_core.topic_registry import TopicProfile


def message_timestamp_ns(msg: Any, fallback_ns: int) -> int:
    header = getattr(msg, 'header', None)
    stamp = getattr(header, 'stamp', None)
    if stamp is None:
        return fallback_ns
    return int(stamp.sec) * 1_000_000_000 + int(stamp.nanosec)


class McapRecorderNode(Node):
    """Record configured topics to MCAP, controlled by /xr/controller_state."""

    def __init__(self) -> None:
        super().__init__('mcap_recorder')

        self.declare_parameter('output_dir', '~/ros2_ws/raw_datasets_mcap')
        self.declare_parameter('profile_path', '')
        self.declare_parameter('control_topic', DEFAULT_CONTROL_TOPIC)
        self.declare_parameter('storage_id', 'mcap')
        self.declare_parameter('storage_preset_profile', 'zstd_small')

        self.output_dir = Path(self.get_parameter('output_dir').value)
        self.control_topic = str(self.get_parameter('control_topic').value)
        self.profile_path = self._resolve_profile_path()

        self.profile = TopicProfile.from_yaml(self.profile_path)
        self.session = EpisodeSession(self.output_dir)
        self.backend = BagRos2CliBackend(
            topic_names=[topic.name for topic in self.profile.topics],
            storage_id=str(self.get_parameter('storage_id').value),
            storage_preset_profile=str(self.get_parameter('storage_preset_profile').value),
        )

        self.control_callback_group = MutuallyExclusiveCallbackGroup()
        self._create_control_subscription()

        self.get_logger().info(
            f'Native ros2 bag recorder ready; output_dir={self.output_dir.expanduser()}, '
            f'profile={self.profile_path}, '
            f'storage_preset_profile={self.get_parameter("storage_preset_profile").value}'
        )

    def _resolve_profile_path(self) -> Path:
        configured = str(self.get_parameter('profile_path').value)
        if configured:
            return Path(configured).expanduser().resolve()
        share_dir = Path(get_package_share_directory('data_collection_recorder'))
        return share_dir / 'config' / 'recording' / 'default_profile.yaml'

    def _create_control_subscription(self) -> None:
        self.create_subscription(
            Int32,
            self.control_topic,
            self._handle_control_message,
            QoSProfile(depth=10),
            callback_group=self.control_callback_group,
        )
        self.get_logger().info(f'Subscribed to control topic {self.control_topic} (std_msgs/msg/Int32)')

    def _handle_control_message(self, msg: Any) -> None:
        timestamp_ns = message_timestamp_ns(msg, self.get_clock().now().nanoseconds)
        code = int(getattr(msg, 'data'))

        if code == START_RECORDING_CODE:
            self._start_recording(timestamp_ns)
        elif code == STOP_RECORDING_CODE:
            self._stop_recording(timestamp_ns)
        elif code == INFERENCE_PAUSED_CODE:
            if self.session.start_intervention(code=code, timestamp_ns=timestamp_ns):
                self.get_logger().info('Intervention started; recording continues')
        elif code == INFERENCE_RESUMED_CODE:
            if self.session.end_intervention(code=code, timestamp_ns=timestamp_ns):
                self.get_logger().info('Intervention ended; recording continues')

    def _start_recording(self, timestamp_ns: int) -> None:
        if self.session.is_recording:
            self.get_logger().info('Recording already active; ignoring start request')
            return

        try:
            episode = self.session.start(timestamp_ns=timestamp_ns)
            self.backend.start(
                recording_dir=episode.recording_dir,
                topic_types=self.profile.type_map(),
            )
            preset = str(self.get_parameter('storage_preset_profile').value)
            self._log_recording_started(episode, storage_preset_profile=preset)
            if preset == 'none':
                self.get_logger().warn(
                    'Recording with no MCAP compression preset; expect very large bags for raw Image topics'
                )
        except Exception as exc:
            self.session.mark_error(str(exc))
            if self.session.is_recording:
                self.session.stop(timestamp_ns=timestamp_ns, status='error')
            self.get_logger().error(f'Failed to start MCAP recording: {exc}')

    def _stop_recording(self, timestamp_ns: int) -> None:
        if not self.session.is_recording:
            self.get_logger().info('Recording not active; ignoring stop request')
            return

        try:
            self.backend.stop()
            episode = self.session.stop(timestamp_ns=timestamp_ns)
            if episode:
                self._log_recording_stopped(episode, status='completed')
        except Exception as exc:
            self.session.mark_error(str(exc))
            self.backend.stop()
            episode = self.session.stop(timestamp_ns=timestamp_ns, status='error')
            if episode:
                self._log_recording_stopped(episode, status='error')
            self.get_logger().error(f'Failed to stop MCAP recording cleanly: {exc}')

    def _log_recording_started(self, episode: EpisodeInfo, storage_preset_profile: str) -> None:
        episode_dir = episode.episode_dir.resolve()
        recording_dir = episode.recording_dir.resolve()
        metadata_path = episode_dir / 'metadata.json'
        banner = '=' * 72
        lines = [
            banner,
            '>>> 开始采集 (ros2 bag record / MCAP) <<<',
            f'Episode ID : {episode.episode_id}',
            f'数据根目录 : {episode_dir}',
            f'MCAP 落盘  : {recording_dir}',
            f'元数据文件 : {metadata_path}',
            f'压缩预设   : {storage_preset_profile}',
            banner,
        ]
        for line in lines:
            self.get_logger().info(line)
            print(line, flush=True)

    def _log_recording_stopped(self, episode: EpisodeInfo, status: str) -> None:
        episode_dir = episode.episode_dir.resolve()
        recording_dir = episode.recording_dir.resolve()
        metadata_path = episode_dir / 'metadata.json'
        mcap_files = sorted(recording_dir.glob('*.mcap')) if recording_dir.is_dir() else []
        mcap_summary = (
            ', '.join(f'{path.name} ({path.stat().st_size / 1e6:.1f} MB)' for path in mcap_files)
            if mcap_files
            else '(暂无 .mcap 文件，请检查是否正常停录)'
        )
        banner = '=' * 72
        lines = [
            banner,
            '>>> 结束采集 (ros2 bag record / MCAP) <<<',
            f'状态       : {status}',
            f'Episode ID : {episode.episode_id}',
            f'数据根目录 : {episode_dir}',
            f'MCAP 落盘  : {recording_dir}',
            f'元数据文件 : {metadata_path}',
            f'MCAP 文件  : {mcap_summary}',
            banner,
        ]
        for line in lines:
            self.get_logger().info(line)
            print(line, flush=True)

    def destroy_node(self) -> bool:
        if self.session.is_recording:
            self.get_logger().warn('Node is shutting down while recording; stopping current episode')
            now_ns = self.get_clock().now().nanoseconds
            self._stop_recording(now_ns)
        return super().destroy_node()


def main(args=None) -> None:
    rclpy.init(args=args)
    node = McapRecorderNode()
    executor = MultiThreadedExecutor(num_threads=8)
    executor.add_node(node)

    try:
        executor.spin()
    except KeyboardInterrupt:
        node.get_logger().info('Keyboard interrupt received, shutting down...')
    finally:
        executor.shutdown()
        executor.remove_node(node)
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
