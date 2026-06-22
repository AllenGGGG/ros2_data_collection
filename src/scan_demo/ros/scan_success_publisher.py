#!/usr/bin/env python3
"""
30Hz 发布 /scan/success（与相机帧率对齐）。

状态机（固定 30Hz，扫码/录停只是变换信号）：
  0 --[/scan/code 有效条码]--> 1
  1 --[/ros2recordstop]--> 0（保持 0，直到扫码器读到「新一次」条码）
"""

from __future__ import annotations

import argparse
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "matrix220"))
from scan_terminal import (  # noqa: E402
    announce_arm_next,
    announce_record_stop,
    announce_scan_ignored,
    announce_scan_success,
)
from tcp import is_valid_read  # noqa: E402

try:
    import rclpy
    from rclpy.node import Node
    from rclpy.qos import QoSProfile, ReliabilityPolicy
    from std_msgs.msg import Empty, Float32MultiArray, String
except ImportError as exc:
    print("需要 ROS2：source /opt/ros/jazzy/setup.bash", file=sys.stderr)
    raise SystemExit(1) from exc

TOPIC_SUCCESS = "/scan/success"
DEFAULT_SCAN_TOPIC = "/scan/code"
DEFAULT_RECORD_STOP_TOPIC = "/ros2recordstop"
DEFAULT_SCAN_ARM_TOPIC = "/scan/arm_next"
DEFAULT_FPS = 30.0

_EVENT_QOS = QoSProfile(depth=10, reliability=ReliabilityPolicy.RELIABLE)


class ScanSuccessPublisher(Node):
    def __init__(
        self,
        *,
        fps: float,
        dim: int,
        scan_topic: str,
        record_stop_topic: str,
    ) -> None:
        super().__init__("scan_success_publisher")
        if dim < 1:
            raise ValueError("--dim 至少为 1")
        self._dim = dim
        self._scanned = False
        self._armed = True
        self._record_stop_topic = record_stop_topic
        self._lock = threading.Lock()
        self._pub = self.create_publisher(Float32MultiArray, TOPIC_SUCCESS, 10)
        self._zeros = [0.0] * dim
        self._ones = [1.0] * dim
        self._timer = self.create_timer(1.0 / fps, self._on_tick)
        self.create_subscription(String, scan_topic, self._on_scan, _EVENT_QOS)
        self.create_subscription(
            Empty, record_stop_topic, self._on_record_stop, _EVENT_QOS
        )
        self.create_subscription(
            Empty, DEFAULT_SCAN_ARM_TOPIC, self._on_arm_next, _EVENT_QOS
        )
        self.create_timer(2.0, self._check_duplicate_publishers)
        self.get_logger().info(
            f"{TOPIC_SUCCESS} @ {fps:.1f} Hz; "
            f"{scan_topic}->1; {record_stop_topic}->0"
        )

    def _check_duplicate_publishers(self) -> None:
        pubs = self.get_publishers_info_by_topic(TOPIC_SUCCESS)
        if len(pubs) <= 1:
            return
        names = ", ".join(sorted({f"{p.node_name}" for p in pubs}))
        self.get_logger().error(
            f"检测到 {len(pubs)} 个 /scan/success 发布者 ({names})，"
            f"echo 会在 0/1 间跳动！请运行: bash stop_all.sh"
        )

    def _on_scan(self, msg: String) -> None:
        code = (msg.data or "").strip()
        if not is_valid_read(code):
            return
        with self._lock:
            if not self._armed:
                announce_scan_ignored(code, reason="未解锁(请先停录后再扫，或等待录包结束)")
                return
            if self._scanned:
                announce_scan_ignored(code, reason="本段已扫成功( success 仍为 1 )")
                return
            self._scanned = True
            self._armed = False
        seq = announce_scan_success(code)
        self.get_logger().info(f"scan success #{seq:03d}: {code!r} -> {TOPIC_SUCCESS}=1")

    def _on_record_stop(self, _msg: Empty) -> None:
        with self._lock:
            self._scanned = False
            self._armed = True
        announce_record_stop()
        self.get_logger().info(
            f"{self._record_stop_topic} -> {TOPIC_SUCCESS} 置 0，已允许下一次扫码（含同条码）"
        )

    def _on_arm_next(self, _msg: Empty) -> None:
        with self._lock:
            self._armed = True
        announce_arm_next()
        self.get_logger().info("已允许下一次扫码置 1")

    def _on_tick(self) -> None:
        with self._lock:
            scanned = self._scanned
        msg = Float32MultiArray()
        msg.data = self._ones if scanned else self._zeros
        self._pub.publish(msg)


def main() -> None:
    parser = argparse.ArgumentParser(description="30Hz /scan/success，扫码置1，录包停止置0")
    parser.add_argument("--fps", type=float, default=DEFAULT_FPS)
    parser.add_argument("--dim", type=int, default=1)
    parser.add_argument("--scan-topic", default=DEFAULT_SCAN_TOPIC)
    parser.add_argument("--record-stop-topic", default=DEFAULT_RECORD_STOP_TOPIC)
    args = parser.parse_args()

    rclpy.init()
    node = ScanSuccessPublisher(
        fps=args.fps,
        dim=args.dim,
        scan_topic=args.scan_topic,
        record_stop_topic=args.record_stop_topic,
    )
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
