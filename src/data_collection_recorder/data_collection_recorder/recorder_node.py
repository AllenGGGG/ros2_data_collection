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
    STATE_RECORD_TOPICS,
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
        self.declare_parameter('storage_preset_profile', 'zstd_small')

        self.output_dir = Path(self.get_parameter('output_dir').value)
        self.control_topic = str(self.get_parameter('control_topic').value)
        self.profile_path = self._resolve_profile_path()
        self.storage_config_path = self._resolve_storage_config_path()

        self.profile = TopicProfile.from_yaml(self.profile_path)
        self.session = EpisodeSession(self.output_dir)
        self.backend = BagRosbag2PyBackend(
            storage_config_path=self.storage_config_path,
            storage_id=str(self.get_parameter('storage_id').value),
            storage_preset_profile=str(self.get_parameter('storage_preset_profile').value),
        )

        self.state_callback_group = ReentrantCallbackGroup()
        self.data_callback_group = ReentrantCallbackGroup()
        self.control_callback_group = MutuallyExclusiveCallbackGroup()
        self._topic_subscriptions = []
        self._last_record_ns: dict[str, int] = {}
        self._last_control_code_written: Optional[int] = None
        self._logged_image_payload = False
        self._create_topic_subscriptions()

        self.get_logger().info(
            f'MCAP recorder ready; output_dir={self.output_dir.expanduser()}, '
            f'profile={self.profile_path}, storage_config={self.storage_config_path}, '
            f'storage_preset_profile={self.get_parameter("storage_preset_profile").value}'
        )

    def _resolve_profile_path(self) -> Path:
        configured = str(self.get_parameter('profile_path').value)
        if configured:
            return Path(configured).expanduser().resolve()
        share_dir = Path(get_package_share_directory('data_collection_recorder'))
        return share_dir / 'config' / 'recording' / 'default_profile.yaml'

    def _resolve_storage_config_path(self) -> Optional[Path]:
        configured = str(self.get_parameter('storage_config_path').value).strip()
        if configured:
            candidate = Path(configured).expanduser().resolve()
            if candidate.is_file():
                return candidate
            self.get_logger().warn(
                f'storage_config_path not found ({candidate}); falling back to package default'
            )
        share_dir = Path(get_package_share_directory('data_collection_recorder'))
        default_path = share_dir / 'config' / 'recording' / 'mcap_storage.yaml'
        return default_path if default_path.is_file() else None

    def _create_topic_subscriptions(self) -> None:
        for topic in self.profile.topics:
            msg_type = import_message_class(topic.type)
            if topic.name == self.control_topic:
                callback_group = self.control_callback_group
            elif topic.name in STATE_RECORD_TOPICS:
                callback_group = self.state_callback_group
            else:
                callback_group = self.data_callback_group
            subscription = self.create_subscription(
                msg_type,
                topic.name,
                self._make_callback(topic),
                QoSProfile(depth=topic.qos_depth),
                callback_group=callback_group,
            )
            self._topic_subscriptions.append(subscription)
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
            if not self._should_record_topic(topic, stamp_ns):
                return
            self._write_message(topic.name, msg, stamp_ns)

        return callback

    def _should_record_topic(self, topic: TopicSpec, timestamp_ns: int) -> bool:
        if topic.record_max_hz <= 0:
            return True
        min_interval_ns = int(1_000_000_000 / topic.record_max_hz)
        last_ns = self._last_record_ns.get(topic.name)
        if last_ns is not None and (timestamp_ns - last_ns) < min_interval_ns:
            return False
        self._last_record_ns[topic.name] = timestamp_ns
        return True

    def _handle_control_message(self, msg: Any, timestamp_ns: int) -> None:
        code = int(getattr(msg, 'data'))

        if code == START_RECORDING_CODE:
            self._start_recording(timestamp_ns)
            self._write_control_to_bag(msg, timestamp_ns, code)
        elif code == STOP_RECORDING_CODE:
            self._write_control_to_bag(msg, timestamp_ns, code)
            self._stop_recording(timestamp_ns)
        elif code == INFERENCE_PAUSED_CODE:
            if self.session.start_intervention(code=code, timestamp_ns=timestamp_ns):
                self.get_logger().info('Intervention started; recording continues')
            self._write_control_to_bag(msg, timestamp_ns, code)
        elif code == INFERENCE_RESUMED_CODE:
            if self.session.end_intervention(code=code, timestamp_ns=timestamp_ns):
                self.get_logger().info('Intervention ended; recording continues')
            self._write_control_to_bag(msg, timestamp_ns, code)
        else:
            self._write_control_to_bag(msg, timestamp_ns, code)

    def _write_control_to_bag(self, msg: Any, timestamp_ns: int, code: int) -> None:
        if not self.session.is_recording:
            return
        if self._last_control_code_written == code:
            return
        self._last_control_code_written = code
        self._write_message(self.control_topic, msg, timestamp_ns)

    def _start_recording(self, timestamp_ns: int) -> None:
        if self.session.is_recording:
            self.get_logger().info('Recording already active; ignoring start request')
            return

        try:
            self._last_record_ns.clear()
            self._last_control_code_written = None
            self._logged_image_payload = False
            episode = self.session.start(timestamp_ns=timestamp_ns)
            self.backend.start(
                recording_dir=episode.recording_dir,
                topic_types=self.profile.type_map(),
            )
            preset = str(self.get_parameter('storage_preset_profile').value)
            self.get_logger().info(
                f'MCAP recording started: {episode.episode_dir} '
                f'(storage_preset_profile={preset}, storage_config={self.storage_config_path})'
            )
            if preset == 'none' and self.storage_config_path is None:
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
            payload = serialize_message(msg)
            if not self._logged_image_payload and (
                topic_name.endswith('/image_raw') or topic_name.endswith('/compressed')
            ):
                self._logged_image_payload = True
                self.get_logger().info(
                    f'First image payload on {topic_name}: {len(payload) / 1e6:.2f} MB per frame. '
                    'Raw Image + MCAP zstd is much larger than legacy PNG; use '
                    'profile_path:=.../default_profile.yaml if JPEG topics exist.'
                )
            self.backend.write_serialized(topic_name, payload, timestamp_ns)
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
