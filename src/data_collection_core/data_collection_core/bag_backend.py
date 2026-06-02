from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict


class BagBackend(ABC):
    @abstractmethod
    def start(self, recording_dir: Path, topic_types: Dict[str, str]) -> None:
        raise NotImplementedError

    @abstractmethod
    def write_serialized(self, topic: str, serialized_msg: bytes, timestamp_ns: int) -> None:
        raise NotImplementedError

    @abstractmethod
    def stop(self) -> None:
        raise NotImplementedError

    @property
    @abstractmethod
    def active(self) -> bool:
        raise NotImplementedError
