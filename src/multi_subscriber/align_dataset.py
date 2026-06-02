import argparse
from pathlib import Path
from typing import Dict, Optional

import pandas as pd
from pandas import DataFrame, Series
from datetime import timedelta

TIMESTAMP_FORMAT = "%Y%m%d_%H%M%S_%f"
VIEWS = {
    "head": "camera_head",
    "left_wrist": "camera_left_wrist",
    "right_wrist": "camera_right_wrist"
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Align joint states, end-effector poses, and multi-view images by timestamp."
    )
    parser.add_argument(
        "--base-dir",
        required=True,
        type=Path,
        help="Path to the data folder generated per recording run."
    )
    parser.add_argument(
        "--tolerance-ms",
        type=float,
        default=100.0,
        help="Maximum allowed timestamp difference (in milliseconds) when aligning modalities."
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional output CSV path; defaults to <base-dir>/aligned_dataset.csv"
    )
    return parser.parse_args()


def read_timestamp_csv(path: Path) -> DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Missing required file: {path}")
    df = pd.read_csv(path)
    if "timestamp" not in df.columns:
        raise ValueError(f"File {path} lacks a 'timestamp' column")
    df["timestamp"] = pd.to_datetime(df["timestamp"], format=TIMESTAMP_FORMAT)
    return df


def find_closest_row(df: DataFrame, target_time: pd.Timestamp, tolerance: timedelta) -> Optional[Series]:
    if df.empty:
        return None
    diffs = (df["timestamp"] - target_time).abs()
    idx = diffs.idxmin()
    if pd.isna(idx):
        return None
    if diffs.loc[idx] <= tolerance:
        return df.loc[idx]
    return None


def resolve_image_path(
    df: DataFrame,
    view_dir: Path,
    target_time: pd.Timestamp,
    tolerance: timedelta,
    last_path: Optional[Path]
) -> Optional[Path]:
    candidate = find_closest_row(df, target_time, tolerance)
    if candidate is not None:
        filename = candidate.get("image")
        if isinstance(filename, str):
            path = view_dir / filename
            if path.exists():
                return path
    return last_path


def align_dataset(base_dir: Path, tolerance_ms: float, output_path: Optional[Path]) -> Path:
    tolerance = timedelta(milliseconds=tolerance_ms)

    joint_states = read_timestamp_csv(base_dir / "joint_states.csv")
    left_ee = read_timestamp_csv(base_dir / "left_ee_data.csv")
    right_ee = read_timestamp_csv(base_dir / "right_ee_data.csv")

    image_data: Dict[str, DataFrame] = {}
    for view, folder in VIEWS.items():
        csv_path = base_dir / f"{view}_image_timestamps.csv"
        image_data[view] = read_timestamp_csv(csv_path)

    image_dirs = {view: base_dir / folder for view, folder in VIEWS.items()}
    last_image_paths: Dict[str, Optional[Path]] = {view: None for view in VIEWS}

    samples = []
    skipped = 0

    for _, joint_row in joint_states.iterrows():
        sample_time = joint_row["timestamp"]

        left_pose = find_closest_row(left_ee, sample_time, tolerance)
        right_pose = find_closest_row(right_ee, sample_time, tolerance)
        if left_pose is None or right_pose is None:
            skipped += 1
            continue

        view_paths: Dict[str, Optional[Path]] = {}
        missing_view = False
        for view in VIEWS:
            view_path = resolve_image_path(
                image_data[view],
                image_dirs[view],
                sample_time,
                tolerance,
                last_image_paths[view]
            )
            if view_path is None:
                missing_view = True
                break
            last_image_paths[view] = view_path
            view_paths[view] = view_path

        if missing_view:
            skipped += 1
            continue

        sample = {
            "timestamp": sample_time.strftime(TIMESTAMP_FORMAT),
            "left_gripper_joint": joint_row.get("left_gripper_joint"),
            "right_gripper_joint": joint_row.get("right_gripper_joint"),
        }

        sample.update({
            "left_pose_x": left_pose.get("left_pose_x"),
            "left_pose_y": left_pose.get("left_pose_y"),
            "left_pose_z": left_pose.get("left_pose_z"),
            "left_orient_x": left_pose.get("left_orient_x"),
            "left_orient_y": left_pose.get("left_orient_y"),
            "left_orient_z": left_pose.get("left_orient_z"),
            "left_orient_w": left_pose.get("left_orient_w"),
            "right_pose_x": right_pose.get("right_pose_x"),
            "right_pose_y": right_pose.get("right_pose_y"),
            "right_pose_z": right_pose.get("right_pose_z"),
            "right_orient_x": right_pose.get("right_orient_x"),
            "right_orient_y": right_pose.get("right_orient_y"),
            "right_orient_z": right_pose.get("right_orient_z"),
            "right_orient_w": right_pose.get("right_orient_w"),
        })

        for view, path in view_paths.items():
            rel_path = path.relative_to(base_dir)
            sample[f"{view}_image"] = str(rel_path)

        samples.append(sample)

    if not samples:
        raise RuntimeError("No aligned samples were produced; consider increasing the tolerance window.")

    aligned_df = pd.DataFrame(samples)
    output_csv = output_path or (base_dir / "aligned_dataset.csv")
    aligned_df.to_csv(output_csv, index=False)

    print(f"Aligned {len(samples)} samples (skipped {skipped}) -> {output_csv}")
    return output_csv


def main() -> None:
    args = parse_args()
    base_dir = args.base_dir.expanduser().resolve()
    if not base_dir.exists():
        raise FileNotFoundError(f"Base directory does not exist: {base_dir}")

    output_path = args.output
    if output_path is not None:
        output_path = output_path.expanduser().resolve()

    align_dataset(base_dir, args.tolerance_ms, output_path)


if __name__ == "__main__":
    main()
