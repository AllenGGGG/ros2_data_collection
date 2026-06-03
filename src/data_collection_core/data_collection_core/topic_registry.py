from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import yaml

from data_collection_core.constants import STATE_RECORD_TOPICS


@dataclass(frozen=True)
class TopicSpec:
    name: str
    type: str
    qos_depth: int = 10
    # Max write rate to the bag (Hz). 0 means no limit. Does not affect subscription rate.
    record_max_hz: float = 0.0


class TopicProfile:
    def __init__(self, topics: Iterable[TopicSpec]) -> None:
        self.topics: List[TopicSpec] = list(topics)

    @classmethod
    def from_yaml(cls, path: Path) -> 'TopicProfile':
        with path.expanduser().open('r', encoding='utf-8') as profile_file:
            raw_profile = yaml.safe_load(profile_file) or {}

        state_record_max_hz = float(raw_profile.get('state_record_max_hz', 0.0))
        topics = []
        for raw_topic in raw_profile.get('topics', []):
            if isinstance(raw_topic, str):
                raise ValueError(
                    'Topic profile entries must include name and type; '
                    f'got plain string {raw_topic!r}'
                )
            topic_name = raw_topic['name']
            if 'record_max_hz' in raw_topic:
                record_max_hz = float(raw_topic['record_max_hz'])
            elif topic_name in STATE_RECORD_TOPICS and state_record_max_hz > 0:
                record_max_hz = state_record_max_hz
            else:
                record_max_hz = 0.0
            topics.append(TopicSpec(
                name=topic_name,
                type=raw_topic['type'],
                qos_depth=int(raw_topic.get('qos_depth', raw_profile.get('default_qos_depth', 10))),
                record_max_hz=record_max_hz,
            ))
        return cls(topics)

    def type_map(self) -> Dict[str, str]:
        return {topic.name: topic.type for topic in self.topics}

    def spec_for(self, topic_name: str) -> Optional[TopicSpec]:
        for topic in self.topics:
            if topic.name == topic_name:
                return topic
        return None
