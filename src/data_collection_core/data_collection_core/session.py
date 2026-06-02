from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional

from data_collection_core.metadata_store import MetadataStore


class RecordingState(str, Enum):
    IDLE = 'idle'
    RECORDING = 'recording'
    INTERVENTION = 'intervention'


@dataclass(frozen=True)
class EpisodeInfo:
    episode_id: str
    episode_dir: Path
    recording_dir: Path


class EpisodeSession:
    """Owns the recording state machine and sidecar metadata."""

    def __init__(self, output_dir: Path, metadata_store: Optional[MetadataStore] = None) -> None:
        self.output_dir = output_dir.expanduser().resolve()
        self.metadata_store = metadata_store or MetadataStore()
        self.state = RecordingState.IDLE
        self.current_episode: Optional[EpisodeInfo] = None

    @property
    def is_recording(self) -> bool:
        return self.state in (RecordingState.RECORDING, RecordingState.INTERVENTION)

    def start(self, timestamp_ns: Optional[int] = None) -> EpisodeInfo:
        if self.is_recording:
            raise RuntimeError('Cannot start a new episode while recording is active')

        episode_id = self._make_episode_id()
        episode_dir = self.output_dir / episode_id
        recording_dir = episode_dir / 'recording'
        recording_dir.mkdir(parents=True, exist_ok=False)

        self.current_episode = EpisodeInfo(
            episode_id=episode_id,
            episode_dir=episode_dir,
            recording_dir=recording_dir,
        )
        self.state = RecordingState.RECORDING
        self.metadata_store.start_episode(
            episode_dir=episode_dir,
            episode_id=episode_id,
            storage_format='mcap',
            compression='zstd',
            timestamp_ns=timestamp_ns,
        )
        return self.current_episode

    def start_intervention(self, code: int, timestamp_ns: Optional[int] = None) -> bool:
        if not self.is_recording:
            return False
        if self.state == RecordingState.INTERVENTION:
            return False
        self.state = RecordingState.INTERVENTION
        self.metadata_store.append_event(
            event_type='intervention_start',
            code=code,
            source='controller_state',
            timestamp_ns=timestamp_ns,
        )
        return True

    def end_intervention(self, code: int, timestamp_ns: Optional[int] = None) -> bool:
        if not self.is_recording:
            return False
        if self.state == RecordingState.RECORDING:
            return False
        self.state = RecordingState.RECORDING
        self.metadata_store.append_event(
            event_type='intervention_end',
            code=code,
            source='controller_state',
            timestamp_ns=timestamp_ns,
        )
        return True

    def stop(self, timestamp_ns: Optional[int] = None, status: str = 'completed') -> Optional[EpisodeInfo]:
        if not self.is_recording:
            return None
        episode = self.current_episode
        self.metadata_store.stop_episode(timestamp_ns=timestamp_ns, status=status)
        self.current_episode = None
        self.state = RecordingState.IDLE
        return episode

    def mark_error(self, error: str) -> None:
        self.metadata_store.mark_error(error)

    @staticmethod
    def _make_episode_id() -> str:
        return datetime.now().strftime('%Y%m%d_%H%M%S_%f')
