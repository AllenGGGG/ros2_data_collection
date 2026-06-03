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

DEFAULT_OUTPUT_DIR = '~/ros2_ws/raw_datasets_mcap'

_TERMINAL_WIDTH = 72
_ANSI_RESET = '\033[0m'
_ANSI_BOLD_GREEN = '\033[1;92m'
_ANSI_BOLD_CYAN = '\033[1;96m'
_ANSI_BOLD_YELLOW = '\033[1;93m'
_ANSI_BOLD_RED = '\033[1;91m'
_ANSI_BOLD_WHITE = '\033[1;97m'


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
        self.backend = BagRos2CliBackend(
            topic_names=[topic.name for topic in self.profile.topics],
            storage_id=str(self.get_parameter('storage_id').value),
            storage_preset_profile=str(self.get_parameter('storage_preset_profile').value),
        )

        self.control_callback_group = MutuallyExclusiveCallbackGroup()
        self._saving_in_progress = False
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

    def _resolve_output_dir(self) -> Path:
        configured = str(self.get_parameter('output_dir').value).strip()
        if configured:
            return Path(configured)
        if self.profile.output_dir:
            return Path(self.profile.output_dir)
        return Path(DEFAULT_OUTPUT_DIR)

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
        if self._saving_in_progress:
            self._emit_terminal_notice(
                '上一段数据仍在落盘保存中，请等待「保存完成」提示后再开始下一轮采集。',
                level='warn',
            )
            return
        if self.session.is_recording:
            self._emit_terminal_notice('当前已在采集中，忽略重复的开始指令。', level='warn')
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
            self._emit_terminal_notice('当前未在采集，忽略结束指令。', level='warn')
            return

        self._saving_in_progress = True
        try:
            self._log_saving_in_progress()
            self.backend.stop()
            episode = self.session.stop(timestamp_ns=timestamp_ns)
            if episode:
                self._log_saving_complete()
                self._log_recording_stopped(episode, status='completed')
        except Exception as exc:
            self.session.mark_error(str(exc))
            try:
                self.backend.stop()
            except Exception:
                pass
            episode = self.session.stop(timestamp_ns=timestamp_ns, status='error')
            if episode:
                self._log_saving_complete(success=False)
                self._log_recording_stopped(episode, status='error')
            self.get_logger().error(f'Failed to stop MCAP recording cleanly: {exc}')
        finally:
            self._saving_in_progress = False

    def _log_recording_started(self, episode: EpisodeInfo, storage_preset_profile: str) -> None:
        episode_dir = episode.episode_dir.resolve()
        recording_dir = episode.recording_dir.resolve()
        metadata_path = episode_dir / 'metadata.json'
        lines = [
            '',
            *self._boxed_banner('★  开 始 采 集  ★', fill_char='='),
            '  采集员：本轮采集已开始，数据正在写入。',
            f'  Episode ID : {episode.episode_id}',
            f'  数据根目录   : {episode_dir}',
            f'  MCAP 落盘    : {recording_dir}',
            f'  元数据文件   : {metadata_path}',
            f'  压缩预设     : {storage_preset_profile}',
            '  结束本轮请点击「结束采集」(控制器 14)。',
            '=' * _TERMINAL_WIDTH,
            '',
        ]
        self._print_terminal_block(lines, color=_ANSI_BOLD_GREEN)

    def _log_saving_in_progress(self) -> None:
        lines = [
            '',
            *self._boxed_banner('⏳  正 在 保 存  请 稍 候  ⏳', fill_char='*'),
            '  采集员注意：',
            '  · 已收到「结束采集」，正在将 MCAP 落盘并关闭录制进程',
            '  · 请勿关闭终端，勿再次点击结束采集',
            '  · 请勿开始下一轮采集，直到出现「保存完成」提示',
            '  · 大文件可能需要数十秒，请耐心等待',
            '*' * _TERMINAL_WIDTH,
            '',
        ]
        self._print_terminal_block(lines, color=_ANSI_BOLD_YELLOW)

    def _log_saving_complete(self, success: bool = True) -> None:
        if success:
            title = '✓  保 存 完 成  可 开 始 下 一 轮  ✓'
            hints = [
                '  采集员：上一段数据已落盘完成。',
                '  · 现在可以开始下一轮采集（控制器 13）',
                '  · 请确认上方路径中已有 MCAP 文件',
            ]
            color = _ANSI_BOLD_GREEN
        else:
            title = '✗  保 存 异 常  请 检 查 日 志  ✗'
            hints = [
                '  采集员：停录过程出现异常，请查看日志后再决定是否重采。',
                '  · 勿在未确认前开始下一轮采集',
            ]
            color = _ANSI_BOLD_RED
        lines = [
            '',
            *self._boxed_banner(title, fill_char='='),
            *hints,
            '=' * _TERMINAL_WIDTH,
            '',
        ]
        self._print_terminal_block(lines, color=color)

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
        lines = [
            '',
            *self._boxed_banner('■  本 轮 采 集 已 结 束  ■', fill_char='-'),
            f'  状态         : {status}',
            f'  Episode ID   : {episode.episode_id}',
            f'  数据根目录     : {episode_dir}',
            f'  MCAP 落盘      : {recording_dir}',
            f'  元数据文件     : {metadata_path}',
            f'  MCAP 文件      : {mcap_summary}',
            '-' * _TERMINAL_WIDTH,
            '',
        ]
        self._print_terminal_block(lines, color=_ANSI_BOLD_CYAN)

    def _emit_terminal_notice(self, message: str, level: str = 'info') -> None:
        prefix = {'info': '[提示]', 'warn': '[注意]'}.get(level, '[提示]')
        color = _ANSI_BOLD_YELLOW if level == 'warn' else _ANSI_BOLD_WHITE
        line = f'{prefix} {message}'
        self.get_logger().warn(message) if level == 'warn' else self.get_logger().info(message)
        print(f'{color}{line}{_ANSI_RESET}', flush=True)

    def _print_terminal_block(self, lines: list[str], color: str = '') -> None:
        for line in lines:
            if line.strip():
                self.get_logger().info(line)
            if color and line.strip():
                print(f'{color}{line}{_ANSI_RESET}', flush=True)
            else:
                print(line, flush=True)

    @staticmethod
    def _boxed_banner(title: str, fill_char: str = '#') -> list[str]:
        width = _TERMINAL_WIDTH
        inner = width - 4
        border = fill_char * width
        return [
            border,
            f'{fill_char} {title.center(inner)} {fill_char}',
            border,
        ]

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
