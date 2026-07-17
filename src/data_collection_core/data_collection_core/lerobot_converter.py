import os
import queue
import subprocess
import threading
from pathlib import Path
from typing import Callable, Optional

from data_collection_core.episode_metadata import update_episode_conversion
from data_collection_core.lerobot_conversion_config import LeRobotConversionConfig
from data_collection_core.metadata_store import iso_now

ConversionCallback = Callable[[Path, bool, str], None]


class LeRobotEpisodeConverter:
    """Background conversion of completed MCAP episodes into a LeRobot dataset."""

    def __init__(
        self,
        config: LeRobotConversionConfig,
        on_finished: Optional[ConversionCallback] = None,
    ) -> None:
        self.config = config
        self.on_finished = on_finished
        self._job_queue: queue.Queue[Optional[Path]] = queue.Queue()
        self._workers: list[threading.Thread] = []
        self._stop_event = threading.Event()
        self._enqueued: set[str] = set()
        self._enqueued_lock = threading.Lock()

    def start(self) -> None:
        if not self.config.enabled:
            return
        self.config.validate()
        self.config.output_path.parent.mkdir(parents=True, exist_ok=True)
        for index in range(self.config.max_parallel):
            thread = threading.Thread(
                target=self._worker_loop,
                name=f'lerobot-convert-{index}',
                daemon=True,
            )
            thread.start()
            self._workers.append(thread)

    def shutdown(self, timeout_sec: float = 300.0) -> None:
        if not self.config.enabled:
            return
        self._stop_event.set()
        for _ in self._workers:
            self._job_queue.put(None)
        for thread in self._workers:
            thread.join(timeout=timeout_sec)

    def enqueue(self, episode_dir: Path) -> bool:
        if not self.config.enabled:
            return False
        episode_dir = episode_dir.expanduser().resolve()
        episode_key = str(episode_dir)
        with self._enqueued_lock:
            if episode_key in self._enqueued:
                return False
            self._enqueued.add(episode_key)
        update_episode_conversion(
            episode_dir,
            status='pending',
            output_dir=str(self.config.output_path),
            started_at=None,
            completed_at=None,
            error=None,
        )
        self._job_queue.put(episode_dir)
        return True

    def _worker_loop(self) -> None:
        while True:
            if self._stop_event.is_set() and self._job_queue.empty():
                return
            try:
                episode_dir = self._job_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if episode_dir is None:
                self._job_queue.task_done()
                if self._stop_event.is_set():
                    return
                continue
            try:
                self._convert_episode(episode_dir)
            except Exception as exc:
                self._mark_failed(episode_dir, str(exc))
                self._notify(episode_dir, False, str(exc))
            finally:
                with self._enqueued_lock:
                    self._enqueued.discard(str(episode_dir.resolve()))
                self._job_queue.task_done()

    def _convert_episode(self, episode_dir: Path) -> None:
        update_episode_conversion(
            episode_dir,
            status='converting',
            output_dir=str(self.config.output_path),
            started_at=iso_now(),
            error=None,
        )

        command = [
            str(self.config.script_file),
            '--input-root',
            str(episode_dir),
            '--output-dir',
            str(self.config.output_path),
            *self.config.extra_args,
        ]
        env = os.environ.copy()
        env.setdefault('CONVERTER_NONINTERACTIVE', '1')
        env.setdefault('CONVERTER_PROFILE', 'low')
        if self.config.env_name:
            env['ENV_NAME'] = self.config.env_name

        log_path = episode_dir / 'lerobot_conversion.log'
        with log_path.open('w', encoding='utf-8') as log_file:
            log_file.write('$ ' + ' '.join(command) + '\n\n')
            log_file.flush()
            completed = subprocess.run(
                command,
                cwd=str(self.config.script_file.parent),
                env=env,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
            )

        if completed.returncode != 0:
            raise RuntimeError(f'converter exited with code {completed.returncode}; see {log_path}')

        update_episode_conversion(
            episode_dir,
            status='completed',
            output_dir=str(self.config.output_path),
            completed_at=iso_now(),
            log_path=str(log_path),
            error=None,
        )
        self._notify(episode_dir, True, '')

    def _mark_failed(self, episode_dir: Path, error: str) -> None:
        update_episode_conversion(
            episode_dir,
            status='failed',
            output_dir=str(self.config.output_path),
            completed_at=iso_now(),
            error=error,
        )

    def _notify(self, episode_dir: Path, success: bool, error_message: str) -> None:
        if self.on_finished is not None:
            self.on_finished(episode_dir, success, error_message)
