from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

import yaml


@dataclass(frozen=True)
class UploadConfig:
    enabled: bool = False
    host: str = ''
    port: int = 22
    user: str = ''
    remote_base_dir: str = ''
    identity_file: str = ''
    retry_count: int = 3
    max_parallel: int = 1
    scan_on_startup: bool = True

    def validate(self) -> None:
        if not self.enabled:
            return
        missing = [
            name for name, value in (
                ('host', self.host),
                ('user', self.user),
                ('remote_base_dir', self.remote_base_dir),
                ('identity_file', self.identity_file),
            )
            if not str(value).strip()
        ]
        if missing:
            raise ValueError(f'Upload config missing required fields: {", ".join(missing)}')

    @property
    def identity_path(self) -> Path:
        return Path(self.identity_file).expanduser().resolve()

    @property
    def remote_target_prefix(self) -> str:
        base = self.remote_base_dir.rstrip('/')
        return f'{self.user}@{self.host}:{base}'


def load_upload_config(path: Path) -> UploadConfig:
    with path.expanduser().open('r', encoding='utf-8') as config_file:
        raw: Dict[str, Any] = yaml.safe_load(config_file) or {}
    section = raw.get('upload', raw)
    return UploadConfig(
        enabled=bool(section.get('enabled', False)),
        host=str(section.get('host', '')).strip(),
        port=int(section.get('port', 22)),
        user=str(section.get('user', '')).strip(),
        remote_base_dir=str(section.get('remote_base_dir', '')).strip(),
        identity_file=str(section.get('identity_file', '')).strip(),
        retry_count=int(section.get('retry_count', 3)),
        max_parallel=max(1, int(section.get('max_parallel', 1))),
        scan_on_startup=bool(section.get('scan_on_startup', True)),
    )
