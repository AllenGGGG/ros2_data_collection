import json
import os
from pathlib import Path
from typing import Any, Dict, Optional


def read_episode_metadata(episode_dir: Path) -> Dict[str, Any]:
    metadata_path = episode_dir / 'metadata.json'
    if not metadata_path.is_file():
        return {}
    with metadata_path.open('r', encoding='utf-8') as metadata_file:
        return json.load(metadata_file)


def update_episode_upload(episode_dir: Path, **fields: Any) -> None:
    update_episode_section(episode_dir, 'upload', **fields)


def update_episode_conversion(episode_dir: Path, **fields: Any) -> None:
    update_episode_section(episode_dir, 'lerobot_conversion', **fields)


def update_episode_section(episode_dir: Path, section_name: str, **fields: Any) -> None:
    metadata_path = episode_dir / 'metadata.json'
    data = read_episode_metadata(episode_dir) if metadata_path.is_file() else {}
    section = dict(data.get(section_name) or {})
    section.update(fields)
    data[section_name] = section

    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = metadata_path.with_suffix('.json.tmp')
    with tmp_path.open('w', encoding='utf-8') as metadata_file:
        json.dump(data, metadata_file, indent=2, ensure_ascii=False)
        metadata_file.write('\n')
    os.replace(tmp_path, metadata_path)


def episode_upload_status(episode_dir: Path) -> Optional[str]:
    upload_section = read_episode_metadata(episode_dir).get('upload') or {}
    status = upload_section.get('status')
    return str(status) if status else None
