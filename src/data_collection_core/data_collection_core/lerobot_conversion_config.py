from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict

import yaml


@dataclass(frozen=True)
class LeRobotConversionConfig:
    enabled: bool = False
    script_path: str = '~/dataset_convert2lerobot/convert.sh'
    output_dir: str = ''
    env_name: str = ''
    extra_args: list[str] = field(default_factory=list)
    max_parallel: int = 1

    def validate(self) -> None:
        if not self.enabled:
            return
        if not str(self.output_dir).strip():
            raise ValueError('LeRobot conversion config missing required field: output_dir')
        if not self.script_file.is_file():
            raise FileNotFoundError(f'LeRobot converter script not found: {self.script_file}')

    @property
    def script_file(self) -> Path:
        return Path(self.script_path).expanduser().resolve()

    @property
    def output_path(self) -> Path:
        return Path(self.output_dir).expanduser().resolve()


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError('extra_args must be a list')
    return [str(item) for item in value]


def load_lerobot_conversion_config(path: Path) -> LeRobotConversionConfig:
    with path.expanduser().open('r', encoding='utf-8') as config_file:
        raw: Dict[str, Any] = yaml.safe_load(config_file) or {}
    section = raw.get('lerobot_conversion', raw)
    return LeRobotConversionConfig(
        enabled=bool(section.get('enabled', False)),
        script_path=str(
            section.get('script_path', '~/dataset_convert2lerobot/convert.sh')
        ).strip(),
        output_dir=str(section.get('output_dir', '')).strip(),
        env_name=str(section.get('env_name', '')).strip(),
        extra_args=_string_list(section.get('extra_args')),
        max_parallel=max(1, int(section.get('max_parallel', 1))),
    )
