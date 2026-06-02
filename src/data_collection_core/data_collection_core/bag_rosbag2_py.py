import threading
from pathlib import Path
from typing import Dict, Optional

import rosbag2_py

from data_collection_core.bag_backend import BagBackend


class BagRosbag2PyBackend(BagBackend):
    """Write serialized ROS messages directly through rosbag2_py using MCAP storage."""

    def __init__(self, storage_config_path: Optional[Path] = None, storage_id: str = 'mcap') -> None:
        self.storage_config_path = storage_config_path
        self.storage_id = storage_id
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
            )
            if self.storage_config_path:
                storage_options.storage_config_uri = str(self.storage_config_path.expanduser().resolve())

            converter_options = rosbag2_py.ConverterOptions(
                input_serialization_format='cdr',
                output_serialization_format='cdr',
            )

            writer = rosbag2_py.SequentialWriter()
            writer.open(storage_options, converter_options)

            for topic_name, topic_type in topic_types.items():
                writer.create_topic(rosbag2_py.TopicMetadata(
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
