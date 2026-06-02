import threading
from pathlib import Path
from typing import Dict, Optional

import rosbag2_py

from data_collection_core.bag_backend import BagBackend


class BagRosbag2PyBackend(BagBackend):
    """Write serialized ROS messages directly through rosbag2_py using MCAP storage."""

    def __init__(
        self,
        storage_config_path: Optional[Path] = None,
        storage_id: str = 'mcap',
        storage_preset_profile: str = 'zstd_small',
    ) -> None:
        self.storage_config_path = storage_config_path
        self.storage_id = storage_id
        self.storage_preset_profile = storage_preset_profile
        self._writer: Optional[rosbag2_py.SequentialWriter] = None
        self._lock = threading.Lock()

    @property
    def active(self) -> bool:
        return self._writer is not None

    def start(self, recording_dir: Path, topic_types: Dict[str, str]) -> None:
        with self._lock:
            if self._writer is not None:
                raise RuntimeError('Bag writer is already active')

            storage_options = rosbag2_py.StorageOptions(
                uri=str(recording_dir),
                storage_id=self.storage_id,
                storage_preset_profile=self.storage_preset_profile,
            )
            config_path = self._resolved_storage_config_path()
            if config_path is not None:
                storage_options.storage_config_uri = str(config_path)

            converter_options = rosbag2_py.ConverterOptions(
                input_serialization_format='cdr',
                output_serialization_format='cdr',
            )

            writer = rosbag2_py.SequentialWriter()
            writer.open(storage_options, converter_options)

            for topic_name, topic_type in topic_types.items():
                writer.create_topic(rosbag2_py.TopicMetadata(
                    id=0,
                    name=topic_name,
                    type=topic_type,
                    serialization_format='cdr',
                ))

            self._writer = writer

    def write_serialized(self, topic: str, serialized_msg: bytes, timestamp_ns: int) -> None:
        with self._lock:
            if self._writer is None:
                return
            self._writer.write(topic, serialized_msg, timestamp_ns)

    def stop(self) -> None:
        with self._lock:
            if self._writer is None:
                return
            # rosbag2_py closes the storage when the writer object is destroyed.
            self._writer = None

    def _resolved_storage_config_path(self) -> Optional[Path]:
        if not self.storage_config_path:
            return None
        resolved = self.storage_config_path.expanduser().resolve()
        return resolved if resolved.is_file() else None
