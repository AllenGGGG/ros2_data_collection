#!/usr/bin/env python3
"""
Matrix 220 驱动：以太网收码 -> /scan/code。

录包停止后：同一条码在 TCP 流里重复出现不会再次发布，
必须先出现一次「读码变化」（如移开出现 NG，再扫同一码）才算新一次扫码。
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ros"))

from scan_terminal import announce_code_read  # noqa: E402
from tcp import (  # noqa: E402
    DEFAULT_PORT,
    TcpClientReceiver,
    TcpServerReceiver,
    is_valid_read,
    normalize_barcode,
)

try:
    import rclpy
    from rclpy.node import Node
    from rclpy.qos import QoSProfile, ReliabilityPolicy
    from std_msgs.msg import Empty, String
except ImportError as exc:
    print("需要 ROS2：source /opt/ros/jazzy/setup.bash", file=sys.stderr)
    raise SystemExit(1) from exc

TOPIC_CODE = "/scan/code"
TOPIC_RECORD_STOP = "/ros2recordstop"
TOPIC_SCAN_ARM = "/scan/arm_next"
DEFAULT_RECORD_STOP_TOPIC = "/ros2recordstop"

_EVENT_QOS = QoSProfile(depth=10, reliability=ReliabilityPolicy.RELIABLE)


class Matrix220Driver(Node):
    def __init__(self, record_stop_topic: str) -> None:
        super().__init__("matrix220_tcp_driver")
        self._last_code = ""
        self._last_line = ""
        self._hold_repeat = False
        self._held_code = ""
        self._lock = threading.Lock()
        self._pub = self.create_publisher(String, TOPIC_CODE, 10)
        self._arm_pub = self.create_publisher(Empty, TOPIC_SCAN_ARM, 10)
        self.create_subscription(Empty, record_stop_topic, self._on_record_stop, _EVENT_QOS)
        self.get_logger().info(f"真实扫码 -> {TOPIC_CODE}")

    def _on_record_stop(self, _msg: Empty) -> None:
        with self._lock:
            # 同一条码重复采集：停录后允许下一次 TCP 读码再次发布 /scan/code
            self._hold_repeat = False
            self._held_code = ""
            self._last_code = ""
        self._notify_arm_next()
        self.get_logger().info(
            "录包停止 -> 已复位读码状态，下一条有效条码（含同码）可再次发布 /scan/code"
        )

    def _notify_arm_next(self) -> None:
        self._arm_pub.publish(Empty())
        self.get_logger().info("新一次读码就绪 -> 通知 /scan/arm_next")

    def on_barcode(self, raw: str, *, source: str) -> None:
        line = normalize_barcode(raw)
        should_arm = False
        publish_code = None

        with self._lock:
            if line != self._last_line:
                if self._hold_repeat and line != self._held_code:
                    self._hold_repeat = False
                    self._held_code = ""
                    self._last_code = ""
                    should_arm = True
                self._last_line = line

            if is_valid_read(line):
                if not (self._hold_repeat and line == self._held_code):
                    if line != self._last_code:
                        self._last_code = line
                        publish_code = line

        if should_arm:
            self._notify_arm_next()
        if publish_code is None:
            return

        msg = String()
        msg.data = publish_code
        self._pub.publish(msg)
        announce_code_read(publish_code, source=source)
        self.get_logger().info(f"[{source}] published /scan/code: {publish_code!r}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Matrix 220 -> /scan/code")
    parser.add_argument(
        "--mode",
        choices=("server", "client"),
        default="client",
    )
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--host", default="192.168.10.104")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--record-stop-topic", default=DEFAULT_RECORD_STOP_TOPIC)
    args = parser.parse_args()

    rclpy.init()
    node = Matrix220Driver(record_stop_topic=args.record_stop_topic)

    def on_line(text: str, *, source: str) -> None:
        node.on_barcode(text, source=source)

    if args.mode == "server":
        receiver = TcpServerReceiver(args.bind, args.port, on_line)
        node.get_logger().info(f"TCP Server 监听 {args.bind}:{args.port}")
    else:
        receiver = TcpClientReceiver(args.host, args.port, on_line)
        node.get_logger().info(f"TCP Client -> {args.host}:{args.port}")

    receiver.start()
    try:
        while rclpy.ok():
            rclpy.spin_once(node, timeout_sec=0.1)
    except KeyboardInterrupt:
        pass
    finally:
        receiver.close()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
