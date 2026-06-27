from pathlib import Path
from typing import Any

import rclpy
from ament_index_python.packages import get_package_share_directory
from rclpy.executors import MultiThreadedExecutor
from rclpy.qos import QoSProfile
from std_msgs.msg import Int32

from data_collection_core.constants import (
    DEFAULT_CONTROL_TOPIC,
    INFERENCE_PAUSED_CODE,
    INFERENCE_RESUMED_CODE,
    START_RECORDING_CODE,
    STOP_RECORDING_CODE,
)
from data_collection_recorder.recorder_node import (
    McapRecorderNode,
    message_timestamp_ns,
)

DEFAULT_INTERVENTION_TOPIC = '/intervention'


class RecapMcapRecorderNode(McapRecorderNode):
    """MCAP recorder for recap rollout collection.

    Unlike the base recorder, this node treats /xr/controller_state 30/31 as
    inference-resume/inference-pause events while an episode is recording and
    publishes a continuous /intervention stream for converter alignment.
    """

    def __init__(self) -> None:
        super().__init__()
        self.declare_parameter('intervention_topic', DEFAULT_INTERVENTION_TOPIC)
        self.declare_parameter('intervention_publish_hz', 10.0)
        self.intervention_topic = str(self.get_parameter('intervention_topic').value)
        self.intervention_publish_hz = float(self.get_parameter('intervention_publish_hz').value)
        self._intervention_value = 0
        self.intervention_pub = self.create_publisher(
            Int32,
            self.intervention_topic,
            QoSProfile(depth=10),
        )
        self._intervention_timer = self.create_timer(
            1.0 / max(self.intervention_publish_hz, 1e-6),
            self._publish_intervention_state,
        )
        self.get_logger().info(
            f'Recap recorder enabled; intervention_topic={self.intervention_topic}, '
            f'intervention_publish_hz={self.intervention_publish_hz}'
        )

    def _resolve_profile_path(self) -> Path:
        configured = str(self.get_parameter('profile_path').value)
        if configured:
            return Path(configured).expanduser().resolve()
        share_dir = Path(get_package_share_directory('data_collection_recap_recorder'))
        return share_dir / 'config' / 'recording' / 'recap_profile.yaml'

    def _handle_control_message(self, msg: Any) -> None:
        timestamp_ns = message_timestamp_ns(msg, self.get_clock().now().nanoseconds)
        code = int(getattr(msg, 'data'))

        if code == START_RECORDING_CODE:
            self._start_recording(timestamp_ns)
        elif code == STOP_RECORDING_CODE:
            self._stop_recording(timestamp_ns, source='controller_14')
        elif code == INFERENCE_PAUSED_CODE:
            if self.session.start_intervention(code=code, timestamp_ns=timestamp_ns):
                self._set_intervention_value(1)
                self.get_logger().info('Recap intervention started; recording continues')
        elif code == INFERENCE_RESUMED_CODE:
            if self.session.end_intervention(code=code, timestamp_ns=timestamp_ns):
                self._set_intervention_value(0)
                self.get_logger().info('Recap intervention ended; recording continues')

    def _publish_intervention_state(self) -> None:
        msg = Int32()
        msg.data = int(self._intervention_value)
        self.intervention_pub.publish(msg)

    def _set_intervention_value(self, value: int) -> None:
        value = int(bool(value))
        if self._intervention_value == value:
            return
        self._intervention_value = value
        self._publish_intervention_state()
        self.get_logger().info(f'Published recap intervention={value} on {self.intervention_topic}')

    def _start_recording(self, timestamp_ns: int) -> None:
        self._set_intervention_value(0)
        super()._start_recording(timestamp_ns)

    def _stop_recording(self, timestamp_ns: int, *, source: str = 'controller') -> None:
        try:
            super()._stop_recording(timestamp_ns, source=source)
        finally:
            self._set_intervention_value(0)


def main(args=None) -> None:
    rclpy.init(args=args)
    node = RecapMcapRecorderNode()
    executor = MultiThreadedExecutor(num_threads=2)
    executor.add_node(node)
    try:
        executor.spin()
    except KeyboardInterrupt:
        node.get_logger().info('Keyboard interrupt received, shutting down recap recorder...')
    finally:
        executor.shutdown()
        executor.remove_node(node)
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
