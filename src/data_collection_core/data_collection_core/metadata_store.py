import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def timestamp_ns_to_iso(timestamp_ns: Optional[int]) -> str:
    if timestamp_ns is None:
        return iso_now()
    return datetime.fromtimestamp(timestamp_ns / 1_000_000_000, tz=timezone.utc).isoformat()


class MetadataStore:
    """Persist human-readable episode metadata beside the MCAP recording."""

    def __init__(self) -> None:
        self.path: Optional[Path] = None
        self.data: Dict[str, Any] = {}

    def start_episode(
        self,
        episode_dir: Path,
        episode_id: str,
        storage_format: str,
        compression: str,
        timestamp_ns: Optional[int] = None,
    ) -> None:
        self.path = episode_dir / 'metadata.json'
        self.data = {
            'episode_id': episode_id,
            'schema_version': 1,
            'started_at': timestamp_ns_to_iso(timestamp_ns),
            'stopped_at': None,
            'status': 'recording',
            'storage': {
                'format': storage_format,
                'compression': compression,
            },
            'events': [],
        }
        self._write()

    def append_event(
        self,
        event_type: str,
        code: int,
        source: str,
        timestamp_ns: Optional[int] = None,
    ) -> None:
        self.data.setdefault('events', []).append({
            't': timestamp_ns_to_iso(timestamp_ns),
            'type': event_type,
            'source': source,
            'code': code,
        })
        self._write()

    def stop_episode(self, timestamp_ns: Optional[int] = None, status: str = 'completed') -> None:
        self.data['stopped_at'] = timestamp_ns_to_iso(timestamp_ns)
        self.data['status'] = status
        self._write()

    def mark_error(self, error: str) -> None:
        self.data['status'] = 'error'
        self.data['error'] = error
        self._write()

    def _write(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix('.json.tmp')
        with tmp_path.open('w', encoding='utf-8') as metadata_file:
            json.dump(self.data, metadata_file, indent=2, ensure_ascii=False)
            metadata_file.write('\n')
        os.replace(tmp_path, self.path)
