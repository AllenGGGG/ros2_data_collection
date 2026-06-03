import signal
import subprocess
from pathlib import Path
from typing import Dict, Iterable, Optional

from data_collection_core.bag_backend import BagBackend


class BagRos2CliBackend(BagBackend):
    """Record bags by running the native `ros2 bag record` command."""

    def __init__(
        self,
        topic_names: Iterable[str],
        storage_id: str = 'mcap',
        storage_preset_profile: str = 'zstd_small',
        stop_timeout_sec: float = 30.0,
    ) -> None:
        self.topic_names = list(topic_names)
        self.storage_id = storage_id
        self.storage_preset_profile = storage_preset_profile
        self.stop_timeout_sec = stop_timeout_sec
        self._process: Optional[subprocess.Popen] = None

    @property
    def active(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(self, recording_dir: Path, topic_types: Dict[str, str]) -> None:
        del topic_types
        if self.active:
            raise RuntimeError('ros2 bag record is already active')
        if not self.topic_names:
            raise RuntimeError('No topics configured for ros2 bag record')

        recording_dir.parent.mkdir(parents=True, exist_ok=True)
        command = self._record_command(recording_dir)
        self._process = subprocess.Popen(command)

    def write_serialized(self, topic: str, serialized_msg: bytes, timestamp_ns: int) -> None:
        del topic, serialized_msg, timestamp_ns

    def stop(self) -> None:
        process = self._process
        if process is None:
            return

        try:
            if process.poll() is None:
                process.send_signal(signal.SIGINT)
                try:
                    process.wait(timeout=self.stop_timeout_sec)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    try:
                        process.wait(timeout=5.0)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5.0)
        finally:
            self._process = None

    def _record_command(self, recording_dir: Path) -> list[str]:
        command = [
            'ros2',
            'bag',
            'record',
            '-s',
            self.storage_id,
        ]
        if self.storage_preset_profile and self.storage_preset_profile != 'none':
            command.extend(['--storage-preset-profile', self.storage_preset_profile])
        command.extend(self.topic_names)
        command.extend(['-o', str(recording_dir)])
        return command
