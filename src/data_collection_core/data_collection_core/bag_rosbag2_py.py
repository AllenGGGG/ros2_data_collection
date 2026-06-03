import queue
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

import rosbag2_py

from data_collection_core.bag_backend import BagBackend

JOINT_STATES_TOPIC = '/joint_states'
_STOP = object()

# Lower number = higher priority in the write queue.
_PRIORITY_JOINT = 0
_PRIORITY_DEFAULT = 1


@dataclass(frozen=True)
class _WriteItem:
    topic: str
    serialized_msg: bytes
    timestamp_ns: int


class BagRosbag2PyBackend(BagBackend):
    """Write serialized ROS messages through rosbag2_py using MCAP storage.

    A dedicated writer thread drains a priority queue so subscription callbacks
    return quickly. Joint states are prioritized over other topics when the
    queue backs up.
    """

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
        self._writer_lock = threading.Lock()
        self._write_queue: queue.PriorityQueue[
            Tuple[int, int, Optional[_WriteItem]]
        ] = queue.PriorityQueue()
        self._enqueue_seq = 0
        self._enqueue_lock = threading.Lock()
        self._accept_writes = False
        self._worker: Optional[threading.Thread] = None

    @property
    def active(self) -> bool:
        return self._writer is not None

    def start(self, recording_dir: Path, topic_types: Dict[str, str]) -> None:
        with self._writer_lock:
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
            self._accept_writes = True
            self._enqueue_seq = 0
            self._worker = threading.Thread(
                target=self._writer_loop,
                name='mcap-bag-writer',
                daemon=True,
            )
            self._worker.start()

    def write_serialized(self, topic: str, serialized_msg: bytes, timestamp_ns: int) -> None:
        if not self._accept_writes:
            return
        priority = _PRIORITY_JOINT if topic == JOINT_STATES_TOPIC else _PRIORITY_DEFAULT
        with self._enqueue_lock:
            self._enqueue_seq += 1
            seq = self._enqueue_seq
        self._write_queue.put((
            priority,
            seq,
            _WriteItem(topic=topic, serialized_msg=serialized_msg, timestamp_ns=timestamp_ns),
        ))

    def stop(self) -> None:
        self._accept_writes = False
        worker = self._worker
        if worker is not None and worker.is_alive():
            with self._enqueue_lock:
                self._enqueue_seq += 1
                stop_seq = self._enqueue_seq
            self._write_queue.put((_PRIORITY_JOINT, stop_seq, _STOP))
            worker.join(timeout=120.0)
        self._worker = None
        with self._writer_lock:
            self._writer = None

    def _writer_loop(self) -> None:
        while True:
            _priority, _seq, item = self._write_queue.get()
            try:
                if item is _STOP:
                    self._flush_pending_writes()
                    return
                self._write_item(item)
            finally:
                self._write_queue.task_done()

    def _flush_pending_writes(self) -> None:
        while True:
            try:
                _priority, _seq, pending = self._write_queue.get_nowait()
            except queue.Empty:
                return
            try:
                if pending is _STOP:
                    continue
                self._write_item(pending)
            finally:
                self._write_queue.task_done()

    def _write_item(self, item: _WriteItem) -> None:
        with self._writer_lock:
            writer = self._writer
        if writer is None:
            return
        writer.write(item.topic, item.serialized_msg, item.timestamp_ns)

    def _resolved_storage_config_path(self) -> Optional[Path]:
        if not self.storage_config_path:
            return None
        resolved = self.storage_config_path.expanduser().resolve()
        return resolved if resolved.is_file() else None
