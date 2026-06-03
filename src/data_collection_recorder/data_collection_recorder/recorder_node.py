import sys
import threading
from pathlib import Path
from typing import Any, Optional

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

DEFAULT_OUTPUT_DIR = '~/ros2_ws/raw_datasets_mcap'

# 采集员终端样式（仅 print，不重复刷 ROS 日志）
_R = '\033[0m'
_GREEN = '\033[1;92m'
_CYAN = '\033[1;96m'
_YELLOW = '\033[1;93m'
_MAGENTA = '\033[1;95m'
_RED = '\033[1;91m'
_BLUE = '\033[1;94m'
_DIM = '\033[2m'
_BG_GREEN = '\033[42;30m'
_BG_YELLOW = '\033[43;30m'
_BG_CYAN = '\033[46;30m'
_BG_RED = '\033[41;97m'


def message_timestamp_ns(msg: Any, fallback_ns: int) -> int:
    header = getattr(msg, 'header', None)
    stamp = getattr(header, 'stamp', None)
    if stamp is None:
        return fallback_ns
    return int(stamp.sec) * 1_000_000_000 + int(stamp.nanosec)


def _colorize(text: str, *codes: str) -> str:
    if not sys.stdout.isatty():
        return text
    return ''.join(codes) + text + _R


def _collector_line(text: str = '') -> None:
    print(text, flush=True)


def _collector_card(
    headline: str,
    lines: list[str],
    *,
    headline_bg: str = _BG_CYAN,
    body_color: str = _CYAN,
) -> None:
    _collector_line()
    _collector_line(_colorize(f'  {headline}  ', headline_bg))
    for line in lines:
        _collector_line(_colorize(f'  {line}', body_color))
    _collector_line(_colorize('  ' + '─' * 56, _DIM))
    _collector_line()


class McapRecorderNode(Node):
    """Record configured topics to MCAP, controlled by /xr/controller_state."""

    def __init__(self) -> None:
        super().__init__('mcap_recorder')

        self.declare_parameter('output_dir', '')
        self.declare_parameter('profile_path', '')
        self.declare_parameter('control_topic', DEFAULT_CONTROL_TOPIC)
        self.declare_parameter('storage_id', 'mcap')
        self.declare_parameter('storage_preset_profile', 'zstd_small')

        self.control_topic = str(self.get_parameter('control_topic').value)
        self.profile_path = self._resolve_profile_path()

        self.profile = TopicProfile.from_yaml(self.profile_path)
        self.output_dir = self._resolve_output_dir()
        self.session = EpisodeSession(self.output_dir)
        self._storage_id = str(self.get_parameter('storage_id').value)
        self._storage_preset_profile = str(self.get_parameter('storage_preset_profile').value)
        self._topic_names = [topic.name for topic in self.profile.topics]

        self._active_backend: Optional[BagRos2CliBackend] = None
        self._save_threads: list[threading.Thread] = []
        self._save_threads_lock = threading.Lock()

        self.control_callback_group = MutuallyExclusiveCallbackGroup()
        self._create_control_subscription()

        self.get_logger().debug(
            f'mcap_recorder ready output_dir={self.output_dir} profile={self.profile_path}'
        )
        _collector_card(
            '📡  数采程序已就绪',
            [
                f'📂 保存目录：{self.output_dir.expanduser()}',
                '🎮 开始采集 → 控制器 13',
                '🛑 结束采集 → 控制器 14',
            ],
            headline_bg=_BG_CYAN,
            body_color=_BLUE,
        )

    def _resolve_profile_path(self) -> Path:
        configured = str(self.get_parameter('profile_path').value)
        if configured:
            return Path(configured).expanduser().resolve()
        share_dir = Path(get_package_share_directory('data_collection_recorder'))
        return share_dir / 'config' / 'recording' / 'default_profile.yaml'

    def _resolve_output_dir(self) -> Path:
        configured = str(self.get_parameter('output_dir').value).strip()
        if configured:
            return Path(configured)
        if self.profile.output_dir:
            return Path(self.profile.output_dir)
        return Path(DEFAULT_OUTPUT_DIR)

    def _new_backend(self) -> BagRos2CliBackend:
        return BagRos2CliBackend(
            topic_names=self._topic_names,
            storage_id=self._storage_id,
            storage_preset_profile=self._storage_preset_profile,
        )

    def _create_control_subscription(self) -> None:
        self.create_subscription(
            Int32,
            self.control_topic,
            self._handle_control_message,
            QoSProfile(depth=10),
            callback_group=self.control_callback_group,
        )

    def _handle_control_message(self, msg: Any) -> None:
        timestamp_ns = message_timestamp_ns(msg, self.get_clock().now().nanoseconds)
        code = int(getattr(msg, 'data'))

        if code == START_RECORDING_CODE:
            self._start_recording(timestamp_ns)
        elif code == STOP_RECORDING_CODE:
            self._stop_recording(timestamp_ns)
        elif code == INFERENCE_PAUSED_CODE:
            self.session.start_intervention(code=code, timestamp_ns=timestamp_ns)
        elif code == INFERENCE_RESUMED_CODE:
            self.session.end_intervention(code=code, timestamp_ns=timestamp_ns)

    def _pending_save_count(self) -> int:
        with self._save_threads_lock:
            self._save_threads = [thread for thread in self._save_threads if thread.is_alive()]
            return len(self._save_threads)

    def _start_recording(self, timestamp_ns: int) -> None:
        if self.session.is_recording:
            self._collector_warn('⚠️  已在采集中，无需重复按开始')
            return

        pending = self._pending_save_count()
        try:
            episode = self.session.start(timestamp_ns=timestamp_ns)
            backend = self._new_backend()
            backend.start(
                recording_dir=episode.recording_dir,
                topic_types=self.profile.type_map(),
            )
            self._active_backend = backend
            self._show_recording_started(episode, pending)
            if self._storage_preset_profile == 'none':
                self.get_logger().warn('storage_preset_profile=none, bags may be very large')
        except Exception as exc:
            self.session.mark_error(str(exc))
            if self.session.is_recording:
                self.session.stop(timestamp_ns=timestamp_ns, status='error')
            self._active_backend = None
            self.get_logger().error(f'Failed to start MCAP recording: {exc}')
            self._collector_warn(f'❌  开始采集失败：{exc}')

    def _stop_recording(self, timestamp_ns: int) -> None:
        if not self.session.is_recording:
            self._collector_warn('⚠️  当前没有在采集，无需按结束')
            return

        backend = self._active_backend
        self._active_backend = None
        episode: Optional[EpisodeInfo] = None

        try:
            episode = self.session.stop(timestamp_ns=timestamp_ns)
            if episode is None:
                return

            record_process = backend.detach_process() if backend is not None else None
            self._show_stop_handoff(episode)

            if record_process is None:
                self._show_background_save_complete(episode, success=True)
                return

            thread = threading.Thread(
                target=self._finalize_episode_save,
                args=(episode, record_process, backend.stop_timeout_sec, 'completed'),
                name=f'mcap-save-{episode.episode_id}',
                daemon=True,
            )
            with self._save_threads_lock:
                self._save_threads.append(thread)
            thread.start()
        except Exception as exc:
            self.session.mark_error(str(exc))
            if self.session.is_recording:
                self.session.stop(timestamp_ns=timestamp_ns, status='error')
            if backend is not None:
                process = backend.detach_process()
                if process is not None and episode is not None:
                    thread = threading.Thread(
                        target=self._finalize_episode_save,
                        args=(episode, process, backend.stop_timeout_sec, 'error'),
                        name=f'mcap-save-{episode.episode_id}',
                        daemon=True,
                    )
                    with self._save_threads_lock:
                        self._save_threads.append(thread)
                    thread.start()
            self.get_logger().error(f'Failed to stop MCAP recording: {exc}')
            self._collector_warn(f'❌  结束采集异常：{exc}')

    def _finalize_episode_save(
        self,
        episode: EpisodeInfo,
        process: Any,
        stop_timeout_sec: float,
        status: str,
    ) -> None:
        success = True
        error_message = ''
        try:
            BagRos2CliBackend.finalize_record_process(process, stop_timeout_sec)
        except Exception as exc:
            success = False
            error_message = str(exc)
            self.get_logger().error(f'Background save failed for {episode.episode_id}: {exc}')
        self._show_background_save_complete(
            episode,
            success=success and status == 'completed',
            error_message=error_message,
        )

    def _show_recording_started(self, episode: EpisodeInfo, pending_background_saves: int) -> None:
        episode_dir = episode.episode_dir.resolve()
        rows = [
            '🎬  正在录制，请操作机器人完成本段任务',
            f'📁  {episode_dir}',
            '🛑  结束本段 → 按 14',
        ]
        if pending_background_saves > 0:
            rows.append(f'💾  另有 {pending_background_saves} 段在后台保存（不影响本轮）')
        _collector_card(
            '✅  开始采集',
            rows,
            headline_bg=_BG_GREEN,
            body_color=_GREEN,
        )

    def _show_stop_handoff(self, episode: EpisodeInfo) -> None:
        episode_dir = episode.episode_dir.resolve()
        _collector_card(
            '🛑  本段已结束',
            [
                f'🆔  {episode.episode_id}',
                f'📁  {episode_dir}',
                '💾  上一段正在后台保存…',
                '👉  现在可以直接按 13 开始下一段',
                '⚠️  不要关闭此终端窗口',
            ],
            headline_bg=_BG_YELLOW,
            body_color=_YELLOW,
        )

    def _show_background_save_complete(
        self,
        episode: EpisodeInfo,
        *,
        success: bool,
        error_message: str = '',
    ) -> None:
        episode_dir = episode.episode_dir.resolve()
        recording_dir = episode.recording_dir.resolve()
        mcap_files = sorted(recording_dir.glob('*.mcap')) if recording_dir.is_dir() else []
        if mcap_files:
            size_mb = sum(path.stat().st_size for path in mcap_files) / 1e6
            file_hint = f'📦  {mcap_files[0].name}  约 {size_mb:.1f} MB'
        else:
            file_hint = '⚠️  未找到 MCAP 文件，请联系工程师'

        if success:
            _collector_card(
                f'✅  保存完成  ·  {episode.episode_id}',
                [
                    '📂  数据已写好，本段可归档',
                    f'📁  {episode_dir}',
                    file_hint,
                ],
                headline_bg=_BG_GREEN,
                body_color=_GREEN,
            )
        else:
            _collector_card(
                f'❌  保存失败  ·  {episode.episode_id}',
                [
                    f'📁  {episode_dir}',
                    f'💥  {error_message or "未知错误"}',
                    '📞  请暂停采集并联系工程师',
                ],
                headline_bg=_BG_RED,
                body_color=_RED,
            )

    def _collector_warn(self, message: str) -> None:
        _collector_line(_colorize(f'  {message}', _BG_YELLOW, _YELLOW))

    def _wait_for_background_saves(self, timeout_sec: float = 120.0) -> None:
        with self._save_threads_lock:
            threads = list(self._save_threads)
        for thread in threads:
            thread.join(timeout=timeout_sec)

    def destroy_node(self) -> bool:
        if self.session.is_recording:
            now_ns = self.get_clock().now().nanoseconds
            self._stop_recording(now_ns)
        pending = self._pending_save_count()
        if pending > 0:
            _collector_line(
                _colorize(f'  ⏳  程序退出中，等待 {pending} 段后台保存…', _MAGENTA)
            )
            self._wait_for_background_saves()
        return super().destroy_node()


def main(args=None) -> None:
    rclpy.init(args=args)
    node = McapRecorderNode()
    executor = MultiThreadedExecutor(num_threads=8)
    executor.add_node(node)

    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        executor.shutdown()
        executor.remove_node(node)
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
