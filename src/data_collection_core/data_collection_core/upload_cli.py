"""CLI helper to retry pending/failed episode uploads without running the recorder node."""

import argparse
import sys
from pathlib import Path

from ament_index_python.packages import get_package_share_directory

from data_collection_core.episode_uploader import EpisodeUploader
from data_collection_core.upload_config import load_upload_config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Upload completed local episodes to the remote server.')
    parser.add_argument(
        '--output-dir',
        type=Path,
        default=Path('~/ros2_ws/raw_datasets_mcap'),
        help='Local dataset root that contains episode folders.',
    )
    parser.add_argument(
        '--upload-config',
        type=Path,
        default=None,
        help='Path to upload.yaml (defaults to data_collection_recorder share config).',
    )
    args = parser.parse_args(argv)

    config_path = args.upload_config
    if config_path is None:
        share_dir = Path(get_package_share_directory('data_collection_recorder'))
        config_path = share_dir / 'config' / 'recording' / 'upload.yaml'

    config = load_upload_config(config_path)
    config.validate()
    if not config.enabled:
        print('Upload is disabled in config.', file=sys.stderr)
        return 1

    uploader = EpisodeUploader(config=config, output_dir=args.output_dir)
    uploader.start()
    pending = uploader.pending_job_count()
    print(f'Queued {pending} episode upload job(s). Waiting for workers...')
    uploader.shutdown()
    print('Done.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
