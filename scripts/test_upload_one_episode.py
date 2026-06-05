#!/usr/bin/env python3
"""Upload a single episode directory using upload.yaml (for manual testing)."""

import argparse
import sys
from pathlib import Path

from ament_index_python.packages import get_package_share_directory

from data_collection_core.episode_metadata import read_episode_metadata
from data_collection_core.episode_uploader import EpisodeUploader
from data_collection_core.upload_config import load_upload_config


def main() -> int:
    parser = argparse.ArgumentParser(description='Test upload for one episode folder.')
    parser.add_argument(
        'episode_dir',
        type=Path,
        help='Path to episode directory (contains metadata.json and recording/).',
    )
    args = parser.parse_args()

    episode_dir = args.episode_dir.expanduser().resolve()
    if not episode_dir.is_dir():
        print(f'Not a directory: {episode_dir}', file=sys.stderr)
        return 1
    if not (episode_dir / 'metadata.json').is_file():
        print(f'metadata.json missing in {episode_dir}', file=sys.stderr)
        return 1

    share_dir = Path(get_package_share_directory('data_collection_recorder'))
    config = load_upload_config(share_dir / 'config' / 'recording' / 'upload.yaml')
    if not config.enabled:
        print('upload.enabled is false in upload.yaml', file=sys.stderr)
        return 1
    config.validate()

    meta = read_episode_metadata(episode_dir)
    print(f'Episode ID : {meta.get("episode_id", episode_dir.name)}')
    print(f'Local path : {episode_dir}')
    print(f'Remote     : {config.user}@{config.host}:{config.remote_base_dir}/{episode_dir.name}/')
    print('Starting upload (rsync)...')

    def on_finished(path: Path, success: bool, error: str) -> None:
        if success:
            print(f'\n✅ Upload OK: {path.name}')
        else:
            print(f'\n❌ Upload failed: {error}', file=sys.stderr)

    uploader = EpisodeUploader(
        config=config,
        output_dir=episode_dir.parent,
        on_finished=on_finished,
    )
    uploader.start()
    if not uploader.enqueue(episode_dir):
        print('Episode already queued or enqueue skipped.', file=sys.stderr)
    uploader.shutdown(timeout_sec=600.0)

    upload_meta = read_episode_metadata(episode_dir).get('upload') or {}
    print(f"Final upload.status: {upload_meta.get('status')}")
    if upload_meta.get('error'):
        print(f"Error: {upload_meta.get('error')}")
    return 0 if upload_meta.get('status') == 'completed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
