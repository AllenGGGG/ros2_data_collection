from dataclasses import dataclass
from pathlib import Path
from queue import Queue
import threading
from typing import Dict, Optional, Union

from data_collection_core.bag_backend import BagBackend

import rosbag2_py


@dataclass(frozen=True)
class _QueuedWrite:
    topic: str
    serialized_msg: bytes
    timestamp_ns: int


_STOP = object()


class BagRosbag2PyBackend(BagBackend):
    """Write serialized ROS messages directly through rosbag2_py using MCAP storage."""

    def __init__(
        self,
        storage_config_path: Optional[Path] = None,
        storage_id: str = 'mcap',
        storage_preset_profile: str = 'zstd_small',
        max_queue_size: int = 0,
    ) -> None:
        self.storage_config_path = storage_config_path
        self.storage_id = storage_id
        self.storage_preset_profile = storage_preset_profile
        self.max_queue_size = max_queue_size
        self._writer: Optional[rosbag2_py.SequentialWriter] = None
        self._write_queue: Optional[Queue[Union[_QueuedWrite, object]]] = None
        self._worker: Optional[threading.Thread] = None
        self._accepting_writes = False
        self._worker_error: Optional[BaseException] = None
        self._lock = threading.Lock()

    @property
    def active(self) -> bool:
        with self._lock:
            return self._writer is not None and self._accepting_writes

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
            self._write_queue = Queue(maxsize=max(0, self.max_queue_size))
            self._worker_error = None
            self._accepting_writes = True
            self._worker = threading.Thread(
                target=self._write_worker,
                name='rosbag2-mcap-writer',
                daemon=False,
            )
            self._worker.start()

    def write_serialized(self, topic: str, serialized_msg: bytes, timestamp_ns: int) -> None:
        with self._lock:
            if self._worker_error is not None:
                raise RuntimeError('Bag writer worker failed') from self._worker_error
            if self._writer is None or self._write_queue is None or not self._accepting_writes:
                return
            write_queue = self._write_queue
        write_queue.put(_QueuedWrite(topic, serialized_msg, timestamp_ns))

    def stop(self) -> None:
        with self._lock:
            if self._writer is None or self._write_queue is None:
                return
            self._accepting_writes = False
            write_queue = self._write_queue
            worker = self._worker

        write_queue.put(_STOP)
        if worker is not None:
            worker.join()

        with self._lock:
            worker_error = self._worker_error
            self._writer = None
            self._write_queue = None
            self._worker = None
            self._worker_error = None

        if worker_error is not None:
            raise RuntimeError('Bag writer worker failed') from worker_error

    def _write_worker(self) -> None:
        while True:
            assert self._write_queue is not None
            item = self._write_queue.get()
            try:
                if item is _STOP:
                    return
                assert isinstance(item, _QueuedWrite)
                assert self._writer is not None
                self._writer.write(item.topic, item.serialized_msg, item.timestamp_ns)
            except BaseException as exc:
                with self._lock:
                    self._worker_error = exc
                return
            finally:
                self._write_queue.task_done()

    def _resolved_storage_config_path(self) -> Optional[Path]:
        if not self.storage_config_path:
            return None
        resolved = self.storage_config_path.expanduser().resolve()
        return resolved if resolved.is_file() else None
