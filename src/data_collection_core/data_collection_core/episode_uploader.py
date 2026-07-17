import queue
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from data_collection_core.episode_metadata import (
    episode_upload_status,
    read_episode_metadata,
    update_episode_upload,
)
from data_collection_core.metadata_store import iso_now
from data_collection_core.upload_config import UploadConfig

UploadCallback = Callable[[Path, bool, str], None]


class EpisodeUploader:
    """Background rsync of completed episode directories to a remote SSH host."""

    def __init__(
        self,
        config: UploadConfig,
        output_dir: Path,
        on_finished: Optional[UploadCallback] = None,
    ) -> None:
        self.config = config
        self.output_dir = output_dir.expanduser().resolve()
        self.on_finished = on_finished
        self._job_queue: queue.Queue[Optional[Path]] = queue.Queue()
        self._workers: list[threading.Thread] = []
        self._stop_event = threading.Event()
        self._enqueued: set[str] = set()
        self._discarded: set[str] = set()
        self._enqueued_lock = threading.Lock()

    def start(self) -> None:
        if not self.config.enabled:
            return
        self.config.validate()
        if not self.config.identity_path.is_file():
            raise FileNotFoundError(
                f'SSH identity file not found: {self.config.identity_path}'
            )
        worker_count = self.config.max_parallel
        for index in range(worker_count):
            thread = threading.Thread(
                target=self._worker_loop,
                name=f'episode-upload-{index}',
                daemon=True,
            )
            thread.start()
            self._workers.append(thread)
        if self.config.check_pending_on_startup:
            self._check_pending_uploads()

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
        episode_dir = episode_dir.resolve()
        episode_key = str(episode_dir)
        with self._enqueued_lock:
            if episode_key in self._enqueued:
                return False
            self._enqueued.add(episode_key)
        update_episode_upload(
            episode_dir,
            status='pending',
            remote_path=None,
            started_at=None,
            completed_at=None,
            error=None,
        )
        self._job_queue.put(episode_dir)
        return True

    def pending_job_count(self) -> int:
        return self._job_queue.qsize()

    def discard(self, episode_dir: Path) -> None:
        """Skip a queued/in-flight upload and best-effort remove the remote copy.

        Runs synchronously on the caller's thread (the recorder's stdin listener),
        so no new worker or queue is introduced for this rare, user-triggered action.
        """
        if not self.config.enabled:
            return
        episode_dir = episode_dir.resolve()
        with self._enqueued_lock:
            self._discarded.add(str(episode_dir))
        remote_dir = f'{self.config.remote_target_prefix}/{episode_dir.name}'
        self._remove_remote_dir(remote_dir)

    def _check_pending_uploads(self) -> None:
        if not self.output_dir.is_dir():
            return
        for episode_dir in sorted(self.output_dir.iterdir()):
            if not episode_dir.is_dir():
                continue
            metadata_path = episode_dir / 'metadata.json'
            if not metadata_path.is_file():
                continue
            metadata = read_episode_metadata(episode_dir)
            if metadata.get('status') != 'completed':
                continue
            upload_status = episode_upload_status(episode_dir)
            if upload_status in (None, 'pending', 'failed', 'uploading'):
                self.enqueue(episode_dir)

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
                episode_key = str(episode_dir.resolve())
                with self._enqueued_lock:
                    was_discarded = episode_key in self._discarded
                if not was_discarded:
                    self._upload_episode(episode_dir)
            except Exception as exc:
                self._mark_failed(episode_dir, str(exc))
                self._notify(episode_dir, False, str(exc))
            finally:
                with self._enqueued_lock:
                    self._enqueued.discard(str(episode_dir.resolve()))
                self._job_queue.task_done()

    def _upload_episode(self, episode_dir: Path) -> None:
        episode_id = episode_dir.name
        remote_dir = f'{self.config.remote_target_prefix}/{episode_id}/'
        update_episode_upload(
            episode_dir,
            status='uploading',
            remote_path=remote_dir,
            started_at=iso_now(),
            error=None,
        )

        last_error = ''
        for attempt in range(1, self.config.retry_count + 1):
            try:
                self._run_rsync(episode_dir, remote_dir)
                update_episode_upload(
                    episode_dir,
                    status='completed',
                    remote_path=remote_dir,
                    completed_at=iso_now(),
                    retries=attempt - 1,
                    error=None,
                )
                self._notify(episode_dir, True, '')
                return
            except Exception as exc:
                last_error = str(exc)
                update_episode_upload(
                    episode_dir,
                    status='uploading',
                    retries=attempt,
                    error=last_error,
                )
                if attempt < self.config.retry_count:
                    time.sleep(min(2 ** attempt, 30))

        self._mark_failed(episode_dir, last_error or 'upload failed')
        self._notify(episode_dir, False, last_error)

    def _ensure_remote_dir(self, remote_dir: str) -> None:
        # remote_dir is like user@host:/path/episode_id/
        if ':/' not in remote_dir:
            raise ValueError(f'Invalid remote rsync target: {remote_dir}')
        _, remote_path = remote_dir.split(':', 1)
        remote_path = remote_path.rstrip('/')
        command = [
            'ssh',
            '-p',
            str(self.config.port),
            '-i',
            str(self.config.identity_path),
            '-o',
            'BatchMode=yes',
            '-o',
            'StrictHostKeyChecking=accept-new',
            f'{self.config.user}@{self.config.host}',
            f'mkdir -p {remote_path}',
        ]
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or '').strip()
            raise RuntimeError(detail or 'failed to create remote directory')

    def _remove_remote_dir(self, remote_dir: str) -> None:
        # remote_dir is like user@host:/path/episode_id (no trailing slash)
        if ':/' not in remote_dir:
            return
        _, remote_path = remote_dir.split(':', 1)
        remote_path = remote_path.rstrip('/')
        if not remote_path:
            return
        command = [
            'ssh',
            '-p',
            str(self.config.port),
            '-i',
            str(self.config.identity_path),
            '-o',
            'BatchMode=yes',
            '-o',
            'StrictHostKeyChecking=accept-new',
            f'{self.config.user}@{self.config.host}',
            f'rm -rf {remote_path}',
        ]
        subprocess.run(command, capture_output=True, text=True, check=False)

    def _run_rsync(self, episode_dir: Path, remote_dir: str) -> None:
        if not episode_dir.is_dir():
            raise FileNotFoundError(f'Episode directory not found: {episode_dir}')

        ssh_command = (
            f'ssh -p {self.config.port} '
            f'-i {self.config.identity_path} '
            '-o BatchMode=yes '
            '-o StrictHostKeyChecking=accept-new'
        )
        source = f'{episode_dir}/'
        self._ensure_remote_dir(remote_dir)
        command = [
            'rsync',
            '-az',
            '--partial',
            '-e',
            ssh_command,
            source,
            remote_dir,
        ]
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            stderr = (completed.stderr or '').strip()
            stdout = (completed.stdout or '').strip()
            detail = stderr or stdout or f'rsync exit code {completed.returncode}'
            raise RuntimeError(detail)

    def _mark_failed(self, episode_dir: Path, error: str) -> None:
        update_episode_upload(
            episode_dir,
            status='failed',
            completed_at=iso_now(),
            error=error,
        )

    def _notify(self, episode_dir: Path, success: bool, error_message: str) -> None:
        if self.on_finished is None:
            return
        try:
            self.on_finished(episode_dir, success, error_message)
        except Exception:
            pass
