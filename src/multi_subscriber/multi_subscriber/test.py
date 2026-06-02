#!/usr/bin/env python3
"""Quick subscriber for Isaac Sim RGB streams and joint states.

Subscribes to:
- /head_camera/rgb
- /left_wrist_camera/rgb
- /right_wrist_camera/rgb
- /isaac/joint_states

Logs one-line shapes for each image topic and a summary of the first joint state.
"""

from __future__ import annotations

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image, JointState
from rclpy.qos import QoSProfile, QoSHistoryPolicy, QoSReliabilityPolicy

qos_profile = QoSProfile(
    history=QoSHistoryPolicy.KEEP_LAST,
    depth=10,
    reliability=QoSReliabilityPolicy.BEST_EFFORT
)

class IsaacTestSubscriber(Node):
    """Minimal subscriber to verify Isaac Sim topics."""

    def __init__(self) -> None:
        super().__init__("isaac_test_subscriber")

        # Track first log per topic to avoid spam
        self._logged_images: set[str] = set()
        self._logged_joint_state = False

        # Subscriptions (default QoS depth=10)
        self.create_subscription(Image, "/head_camera/rgb", self._on_head, qos_profile)
        self.create_subscription(Image, "/left_wrist_camera/rgb", self._on_left, qos_profile)
        self.create_subscription(Image, "/right_wrist_camera/rgb", self._on_right, qos_profile)
        self.create_subscription(JointState, "/isaac/joint_states", self._on_joint_state, qos_profile)

    def _log_image(self, topic: str, msg: Image) -> None:
        """Log image shape once per topic."""
        if topic in self._logged_images:
            return
        channels = int(msg.step // msg.width) if msg.width else 0
        self.get_logger().info(
            f"{topic} shape=({msg.height}, {msg.width}, {channels}) encoding={msg.encoding}"
        )
        self._logged_images.add(topic)

    def _on_head(self, msg: Image) -> None:
        self._log_image("/head_camera/rgb", msg)

    def _on_left(self, msg: Image) -> None:
        self.get_logger().info("Received left wrist camera image!")
        self._log_image("/left_wrist_camera/rgb", msg)

    def _on_right(self, msg: Image) -> None:
        self._log_image("/right_wrist_camera/rgb", msg)

    def _on_joint_state(self, msg: JointState) -> None:
        if self._logged_joint_state:
            return
        names_preview = ", ".join(msg.name[:6])
        self.get_logger().info(
            f"/isaac/joint_states names=[{names_preview}] total={len(msg.name)} "
            f"positions={len(msg.position)} velocities={len(msg.velocity)} efforts={len(msg.effort)}"
        )
        self._logged_joint_state = True


def main(args=None) -> None:
    rclpy.init(args=args)
    node = IsaacTestSubscriber()
    node.get_logger().info("Node initialized and subscribing...")
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        node.get_logger().info("Node spinning stopped.")
        pass
    finally:
        node.get_logger().info("Node shutting down.")
        node.destroy_node()
        rclpy.shutdown()



if __name__ == "__main__":
    main()
