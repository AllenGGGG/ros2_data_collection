#!/usr/bin/env python3
"""
模拟扫码器：30Hz 连续发布 /scan/success。

- 按键前：每帧全 0
- 按空格/s 后：每帧全 1（可用 r 复位为全 0，便于录下一条）
"""

from __future__ import annotations

import argparse
import sys
import threading

try:
    import rclpy
    from rclpy.node import Node
    from std_msgs.msg import Empty, Float32MultiArray
except ImportError as exc:
    print(
        "需要 ROS2 环境：先 source /opt/ros/jazzy/setup.bash 再运行本脚本。",
        file=sys.stderr,
    )
    raise SystemExit(1) from exc

from keyboard_single_key import SingleKeyKeyboard

TOPIC = "/scan/success"
DEFAULT_RECORD_STOP_TOPIC = "/ros2recordstop"
DEFAULT_FPS = 30.0
DEFAULT_DIM = 1


class MockScannerNode(Node):
    def __init__(self, *, fps: float, dim: int, record_stop_topic: str) -> None:
        super().__init__("mock_scanner")
        if dim < 1:
            raise ValueError("--dim 至少为 1")
        self._dim = dim
        self._scanned = False
        self._lock = threading.Lock()
        self._pub = self.create_publisher(Float32MultiArray, TOPIC, 10)
        period = 1.0 / fps
        self._timer = self.create_timer(period, self._on_tick)
        self._zeros = [0.0] * dim
        self._ones = [1.0] * dim
        self.create_subscription(Empty, record_stop_topic, self._on_record_stop, 10)
        self.get_logger().info(
            f"{TOPIC} @ {fps:.1f} Hz, dim={dim}; "
            f"{record_stop_topic} -> 0"
        )

    def _on_record_stop(self, _msg: Empty) -> None:
        self.reset(reason="ros2recordstop")

    def mark_scanned(self) -> None:
        with self._lock:
            if self._scanned:
                self.get_logger().info("已是全 1 状态")
                return
            self._scanned = True
        self.get_logger().info("扫成功 -> 之后持续输出全 1")

    def reset(self, *, reason: str = "manual") -> None:
        with self._lock:
            self._scanned = False
        self.get_logger().info(f"已复位 -> 之后持续输出全 0 ({reason})")

    def _on_tick(self) -> None:
        with self._lock:
            scanned = self._scanned
        msg = Float32MultiArray()
        msg.data = self._ones if scanned else self._zeros
        self._pub.publish(msg)


def _print_help(dim: int, fps: float) -> None:
    print(
        f"\n=== 模拟扫码器 ({fps:.0f} Hz, dim={dim}) ===\n"
        "  未按键: 每帧发布全 0\n"
        "  [空格]/[s]: 切换为每帧全 1\n"
        "  [r]: 手动复位为全 0\n"
        "  录包停止 (/ros2recordstop) 也会复位为全 0\n"
        "  [q]: 退出\n"
    )


def _keyboard_loop(node: MockScannerNode, stop: threading.Event, fps: float) -> None:
    _print_help(node._dim, fps)
    try:
        with SingleKeyKeyboard() as kb:
            while not stop.is_set() and rclpy.ok():
                key = kb.read_key(timeout_sec=0.05)
                if key is None:
                    continue
                k = key.lower()
                if k == "q":
                    stop.set()
                    break
                if k in (" ", "s"):
                    node.mark_scanned()
                elif k == "r":
                    node.reset()
                else:
                    print(f"  未识别 {key!r}，用 空格/s / r / q")
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        print("可改用: python3 mock_scanner_ros2.py --mode line", file=sys.stderr)
        stop.set()


def _line_keyboard_loop(node: MockScannerNode, stop: threading.Event, fps: float) -> None:
    _print_help(node._dim, fps)
    while not stop.is_set() and rclpy.ok():
        try:
            line = input("回车=全1, r=复位, q=退出> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            stop.set()
            break
        if line in ("q", "quit"):
            stop.set()
            break
        if line == "r":
            node.reset()
        else:
            node.mark_scanned()


def main() -> None:
    parser = argparse.ArgumentParser(description="30Hz 扫码信号：按键前全0，按键后全1")
    parser.add_argument("--fps", type=float, default=DEFAULT_FPS)
    parser.add_argument(
        "--dim",
        type=int,
        default=DEFAULT_DIM,
        help="向量长度，每维同为 0 或 1（可与训练 observation 维度对齐）",
    )
    parser.add_argument(
        "--mode",
        choices=("keyboard", "line"),
        default="keyboard",
    )
    parser.add_argument(
        "--start-scanned",
        action="store_true",
        help="启动即全 1（调试用）",
    )
    parser.add_argument(
        "--record-stop-topic",
        default=DEFAULT_RECORD_STOP_TOPIC,
    )
    args = parser.parse_args()

    rclpy.init()
    node = MockScannerNode(
        fps=args.fps, dim=args.dim, record_stop_topic=args.record_stop_topic
    )
    if args.start_scanned:
        node.mark_scanned()

    stop = threading.Event()
    try:
        if args.mode == "line":
            t = threading.Thread(
                target=_line_keyboard_loop, args=(node, stop, args.fps), daemon=True
            )
        else:
            t = threading.Thread(
                target=_keyboard_loop, args=(node, stop, args.fps), daemon=True
            )
        t.start()
        while rclpy.ok() and not stop.is_set():
            rclpy.spin_once(node, timeout_sec=0.1)
        stop.set()
        t.join(timeout=1.0)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
