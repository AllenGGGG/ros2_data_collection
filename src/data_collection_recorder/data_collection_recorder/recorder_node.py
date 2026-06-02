import importlib
from pathlib import Path
from typing import Any, Optional, Type

import rclpy
from ament_index_python.packages import get_package_share_directory
from rclpy.callback_groups import MutuallyExclusiveCallbackGroup, ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.qos import QoSProfile
from rclpy.serialization import serialize_message
from std_msgs.msg import Int32

from data_collection_core.bag_rosbag2_py import BagRosbag2PyBackend
from data_collection_core.constants import (
    DEFAULT_CONTROL_TOPIC,
    INFERENCE_PAUSED_CODE,
    INFERENCE_RESUMED_CODE,
    START_RECORDING_CODE,
    STOP_RECORDING_CODE,
)
from data_collection_core.session import EpisodeSession
from data_collection_core.topic_registry import TopicProfile, TopicSpec


def import_message_class(type_name: str) -> Type[Any]:
    """Import a ROS message class from a string like sensor_msgs/msg/Image."""
    parts = type_name.split('/')
    if len(parts) != 3 or parts[1] != 'msg':
        raise ValueError(f'Unsupported message type string: {type_name}')
    package_name, _, class_name = parts
    module = importlib.import_module(f'{package_name}.msg')
    return getattr(module, class_name)


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
        self.declare_parameter('storage_config_path', '')
        self.declare_parameter('control_topic', DEFAULT_CONTROL_TOPIC)
        self.declare_parameter('storage_id', 'mcap')

        self.output_dir = Path(self.get_parameter('output_dir').value)
        self.control_topic = str(self.get_parameter('control_topic').value)
        self.profile_path = self._resolve_profile_path()
        self.storage_config_path = self._resolve_storage_config_path()

        self.profile = TopicProfile.from_yaml(self.profile_path)
        self.session = EpisodeSession(self.output_dir)
        self.backend = BagRosbag2PyBackend(
            storage_config_path=self.storage_config_path,
            storage_id=str(self.get_parameter('storage_id').value),
        )

        self.data_callback_group = ReentrantCallbackGroup()
        self.control_callback_group = MutuallyExclusiveCallbackGroup()
        self.subscriptions = []
        self._create_topic_subscriptions()

        self.get_logger().info(
            f'MCAP recorder ready; output_dir={self.output_dir.expanduser()}, '
            f'profile={self.profile_path}, storage_config={self.storage_config_path}'
        )

    def _resolve_profile_path(self) -> Path:
        configured = str(self.get_parameter('profile_path').value)
        if configured:
            return Path(configured).expanduser().resolve()
        share_dir = Path(get_package_share_directory('data_collection_recorder'))
        return share_dir / 'config' / 'recording' / 'default_profile.yaml'

    def _resolve_storage_config_path(self) -> Optional[Path]:
        configured = str(self.get_parameter('storage_config_path').value)
        if configured:
            return Path(configured).expanduser().resolve()
        share_dir = Path(get_package_share_directory('data_collection_recorder'))
        default_path = share_dir / 'config' / 'recording' / 'mcap_storage.yaml'
        return default_path if default_path.exists() else None

    def _create_topic_subscriptions(self) -> None:
        for topic in self.profile.topics:
            msg_type = import_message_class(topic.type)
            callback_group = (
                self.control_callback_group
                if topic.name == self.control_topic
                else self.data_callback_group
            )
            subscription = self.create_subscription(
                msg_type,
                topic.name,
                self._make_callback(topic),
                QoSProfile(depth=topic.qos_depth),
                callback_group=callback_group,
            )
            self.subscriptions.append(subscription)
            self.get_logger().info(f'Subscribed to {topic.name} ({topic.type})')

    def _make_callback(self, topic: TopicSpec):
        def callback(msg: Any) -> None:
            now_ns = self.get_clock().now().nanoseconds
            stamp_ns = message_timestamp_ns(msg, now_ns)

            if topic.name == self.control_topic:
                self._handle_control_message(msg, stamp_ns)
                return

            if not self.session.is_recording:
                return
            self._write_message(topic.name, msg, stamp_ns)

        return callback

    def _handle_control_message(self, msg: Any, timestamp_ns: int) -> None:
        code = int(getattr(msg, 'data'))

        if code == START_RECORDING_CODE:
            self._start_recording(timestamp_ns)
            self._write_message(self.control_topic, msg, timestamp_ns)
        elif code == STOP_RECORDING_CODE:
            self._write_message(self.control_topic, msg, timestamp_ns)
            self._stop_recording(timestamp_ns)
        elif code == INFERENCE_PAUSED_CODE:
            if self.session.start_intervention(code=code, timestamp_ns=timestamp_ns):
                self.get_logger().info('Intervention started; recording continues')
            self._write_message(self.control_topic, msg, timestamp_ns)
        elif code == INFERENCE_RESUMED_CODE:
            if self.session.end_intervention(code=code, timestamp_ns=timestamp_ns):
                self.get_logger().info('Intervention ended; recording continues')
            self._write_message(self.control_topic, msg, timestamp_ns)
        else:
            self._write_message(self.control_topic, msg, timestamp_ns)

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
            self.get_logger().info(f'MCAP recording started: {episode.episode_dir}')
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
                self.get_logger().info(f'MCAP recording stopped: {episode.episode_dir}')
        except Exception as exc:
            self.session.mark_error(str(exc))
            self.backend.stop()
            self.session.stop(timestamp_ns=timestamp_ns, status='error')
            self.get_logger().error(f'Failed to stop MCAP recording cleanly: {exc}')

    def _write_message(self, topic_name: str, msg: Any, timestamp_ns: int) -> None:
        if not self.backend.active:
            return
        try:
            self.backend.write_serialized(topic_name, serialize_message(msg), timestamp_ns)
        except Exception as exc:
            self.get_logger().error(f'Failed to write message for {topic_name}: {exc}')

    def destroy_node(self) -> bool:
        if self.session.is_recording:
            self.get_logger().warn('Node is shutting down while recording; stopping current episode')
            now_ns = self.get_clock().now().nanoseconds
            self._stop_recording(now_ns)
        return super().destroy_node()


def main(args=None) -> None:
    rclpy.init(args=args)
    node = McapRecorderNode()
    executor = MultiThreadedExecutor(num_threads=4)
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
