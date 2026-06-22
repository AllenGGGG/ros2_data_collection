#!/usr/bin/env python3
"""
LeRobot dataset visualizer with Rerun.

This tool loads a local LeRobot dataset (recorded via demo_grasp_record, demo_record, etc.),
prints metadata, and streams a selected episode to the Rerun viewer. Observation/action
scalars are logged per-dimension, and camera frames are displayed as RGB images.

Subtask datasets (pistar06_subtask_vp): ``--info-only`` prints segmentation tables;
Rerun shows ``metadata/subtask_index``, segment boundaries, subtask text, and task prompts.

Usage:
    python examples/visualize_dataset_v2.py /path/to/dataset --episode-index 0 --batch-size 8
    python examples/Dataset/visualize_dataset_v2.py dataset/all_dataset_grasp100 --episode-index 0 --batch-size 8
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd
import rerun as rr
import torch
import torch.utils.data
import tqdm

REPO_ROOT = Path(__file__).resolve().parents[1]


from lerobot.datasets.lerobot_dataset import LeRobotDataset


class EpisodeSampler(torch.utils.data.Sampler[int]):
    """Sampler that iterates over a single episode's frame indices."""

    def __init__(self, dataset: LeRobotDataset, episode_index: int):
        from_idx = dataset.meta.episodes["dataset_from_index"][episode_index]
        to_idx = dataset.meta.episodes["dataset_to_index"][episode_index]
        self.frame_ids = range(from_idx, to_idx)

    def __iter__(self):
        return iter(self.frame_ids)

    def __len__(self) -> int:
        return len(self.frame_ids)


def to_hwc_uint8(image_tensor: torch.Tensor | np.ndarray) -> np.ndarray:
    """Convert CHW float tensor or uint8 array to HWC uint8 numpy."""
    if isinstance(image_tensor, torch.Tensor):
        if image_tensor.dtype == torch.float32:
            img = (image_tensor.clamp(0.0, 1.0) * 255).to(torch.uint8)
        else:
            img = image_tensor.to(torch.uint8)
        if img.ndim == 4:
            img = img.squeeze(0)
        img = img.permute(1, 2, 0).cpu().numpy()
    else:
        img = image_tensor
        if img.dtype != np.uint8:
            img = np.clip(img, 0.0, 1.0)
            img = (img * 255).astype(np.uint8)
        if img.ndim == 3 and img.shape[0] in (1, 3):
            img = np.moveaxis(img, 0, -1)
    return img


def load_subtask_catalog(meta_dir: Path) -> pd.DataFrame | None:
    path = meta_dir / "subtasks.parquet"
    if not path.is_file():
        return None
    return pd.read_parquet(path)


def load_subtask_segmentation(meta_dir: Path) -> dict[str, Any] | None:
    path = meta_dir / "lerobot_subtask_segmentation.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def subtask_index_to_text(catalog: pd.DataFrame | None) -> dict[int, str]:
    if catalog is None:
        return {}
    out: dict[int, str] = {}
    for text, row in catalog.iterrows():
        out[int(row["subtask_index"])] = str(text)
    return out


def episode_segments(
    segmentation: dict[str, Any] | None, episode_index: int
) -> list[dict[str, Any]]:
    if segmentation is None:
        return []
    for ep in segmentation.get("episodes", []):
        if int(ep.get("episode_index", -1)) == episode_index:
            return list(ep.get("segments", []))
    return []


@dataclass
class SubtaskVizContext:
    index_to_text: dict[int, str] = field(default_factory=dict)
    segments: list[dict[str, Any]] = field(default_factory=list)


def build_subtask_viz_context(dataset_root: Path, episode_index: int) -> SubtaskVizContext:
    meta_dir = dataset_root / "meta"
    return SubtaskVizContext(
        index_to_text=subtask_index_to_text(load_subtask_catalog(meta_dir)),
        segments=episode_segments(load_subtask_segmentation(meta_dir), episode_index),
    )


def format_segmentation_markdown(
    episode_index: int,
    segments: list[dict[str, Any]],
    index_to_text: dict[int, str],
) -> str:
    lines = [
        f"# Episode {episode_index} subtask segmentation",
        "",
        "| sub | frames | time (s) | ok | intv | description |",
        "|-----|--------|----------|----|------|-------------|",
    ]
    for seg in segments:
        st = int(seg["subtask_index"])
        fs, fe = int(seg["frame_start"]), int(seg["frame_end"])
        t0, t1 = float(seg["subtask_start_ts"]), float(seg["subtask_end_ts"])
        ok = "Y" if seg.get("subtask_success") else "N"
        intv = "Y" if seg.get("intervention") else "N"
        desc = index_to_text.get(st, f"subtask {st}")
        lines.append(f"| {st} | [{fs}, {fe}] | [{t0:.2f}, {t1:.2f}] | {ok} | {intv} | {desc} |")
    return "\n".join(lines)


def _log_text(path: str, text: str, level: Any | None = None) -> None:
    if not hasattr(rr, "TextLog"):
        return
    kwargs: dict[str, Any] = {}
    if level is not None:
        kwargs["level"] = level
    rr.log(path, rr.TextLog(text, **kwargs))


def log_subtask_reference_to_rerun(ctx: SubtaskVizContext, episode_index: int) -> None:
    """Log static segmentation table and per-segment boundary markers."""
    if not ctx.segments and not ctx.index_to_text:
        return

    _set_timeline_seconds(0.0)

    if ctx.segments:
        md = format_segmentation_markdown(episode_index, ctx.segments, ctx.index_to_text)
        if hasattr(rr, "TextDocument"):
            media = getattr(rr.MediaType, "MARKDOWN", "text/markdown")
            rr.log("metadata/segmentation_table", rr.TextDocument(md, media_type=media))

    info_level = getattr(rr, "TextLogLevel", None)
    info_level = getattr(info_level, "INFO", None) if info_level is not None else None

    for seg in ctx.segments:
        t0 = float(seg["subtask_start_ts"])
        st = int(seg["subtask_index"])
        label = ctx.index_to_text.get(st, f"subtask {st}")
        fs = int(seg["frame_start"])
        _set_timeline_seconds(t0)
        _log_scalar("metadata/segment_boundary", 1.0)
        _log_text(
            "metadata/segment_markers",
            f"▶ [{st}] {label}  (frame {fs}, t={t0:.2f}s)",
            level=info_level,
        )


def print_subtask_metadata(dataset_root: Path, episode_index: int | None = None) -> None:
    meta_dir = dataset_root / "meta"
    catalog = load_subtask_catalog(meta_dir)
    segmentation = load_subtask_segmentation(meta_dir)

    if catalog is None and segmentation is None:
        print("\nSubtask metadata: (none — no subtasks.parquet / lerobot_subtask_segmentation.json)")
        return

    print("\n" + "=" * 70)
    print("Subtask metadata")
    print("=" * 70)

    if catalog is not None:
        print("\nSubtask catalog (meta/subtasks.parquet):")
        for text, row in catalog.iterrows():
            idx = int(row["subtask_index"])
            print(f"  [{idx}] {text}")

    if segmentation is None:
        return

    print(f"\nSegmentation (meta/lerobot_subtask_segmentation.json, schema v{segmentation.get('schema_version', '?')}):")
    print(f"  num_subtasks: {segmentation.get('num_subtasks', '?')}")
    if segmentation.get("notes"):
        print(f"  notes: {segmentation['notes']}")

    episodes = segmentation.get("episodes", [])
    if episode_index is not None:
        episodes = [ep for ep in episodes if int(ep.get("episode_index", -1)) == episode_index]
        if not episodes:
            print(f"\n  (no segments for episode_index={episode_index})")
            return

    for ep in episodes:
        ep_idx = int(ep["episode_index"])
        print(f"\n  Episode {ep_idx} ({ep.get('num_frames', '?')} frames, source={ep.get('source', '?')}):")
        print(
            f"    {'sub':>3}  {'frames':>17}  {'time (s)':>21}  "
            f"{'ok':>4}  {'intv':>4}  {'src':>6}"
        )
        for seg in ep.get("segments", []):
            st = int(seg["subtask_index"])
            fs, fe = int(seg["frame_start"]), int(seg["frame_end"])
            t0, t1 = float(seg["subtask_start_ts"]), float(seg["subtask_end_ts"])
            ok = "Y" if seg.get("subtask_success") else "N"
            intv = "Y" if seg.get("intervention") else "N"
            src = str(seg.get("source", ""))[:6]
            print(f"    {st:3d}  [{fs:5d}, {fe:5d}]  [{t0:6.2f}, {t1:6.2f}]  {ok:>4}  {intv:>4}  {src:>6}")

    labels_path = meta_dir / "lerobot_subtask_labels.parquet"
    if labels_path.is_file():
        print(f"\n  Per-frame labels: {labels_path.name} ({labels_path.stat().st_size // 1024} KiB)")

    print("=" * 70)


def print_dataset_info(dataset: LeRobotDataset, episode_index: int | None = None) -> None:
    print("=" * 70)
    print("Dataset summary")
    print("=" * 70)
    print(f"Repo ID        : {dataset.repo_id}")
    print(f"Local root     : {dataset.root}")
    print(f"Robot type     : {dataset.meta.robot_type}")
    print(f"FPS            : {dataset.meta.fps}")
    print(f"Episodes       : {dataset.meta.total_episodes}")
    print(f"Frames         : {dataset.meta.total_frames}")
    if dataset.meta.total_episodes:
        avg = dataset.meta.total_frames / dataset.meta.total_episodes
        print(f"Avg frames/ep  : {avg:.1f}")
    print("\nTasks:")
    for idx, task in enumerate(dataset.meta.tasks):
        print(f"  {idx}: {task}")
    print("\nFeatures:")
    for key, info in dataset.features.items():
        print(f"  {key}: {info}")
    print("\nCamera keys:")
    for cam in dataset.meta.camera_keys:
        print(f"  {cam}")
    print("=" * 70)
    print_subtask_metadata(Path(dataset.root), episode_index=episode_index)


def _log_scalar(path: str, value: float) -> None:
    """Log a single scalar; supports rerun >= 0.23 (Scalars) and older (Scalar)."""
    if hasattr(rr, "Scalars"):
        rr.log(path, rr.Scalars(value))
    else:
        rr.log(path, rr.Scalar(value))


def _set_timeline_seconds(timestamp: float) -> None:
    if hasattr(rr, "set_time"):
        rr.set_time("timeline", timestamp=timestamp)
    else:
        rr.set_time_seconds("timeline", timestamp)


def log_scalar_vector(prefix: str, values: torch.Tensor | np.ndarray, names: list[str] | None):
    arr = values.cpu().numpy() if isinstance(values, torch.Tensor) else values
    if arr.ndim == 0:
        _log_scalar(prefix, float(arr))
        return
    if names is None:
        names = [f"{prefix}_{i}" for i in range(len(arr))]
    for idx, name in enumerate(names):
        _log_scalar(f"{prefix}/{name}", float(arr[idx]))


def _tensor_scalar(val: torch.Tensor | np.ndarray | float | int) -> float:
    if isinstance(val, torch.Tensor):
        return float(val.item() if val.numel() == 1 else val.reshape(-1)[0].item())
    if isinstance(val, np.ndarray):
        return float(val.reshape(-1)[0])
    return float(val)


def visualize_episode(
    dataset: LeRobotDataset,
    episode_index: int,
    batch_size: int,
    tolerance_s: float,
    log_images: bool,
    subtask_ctx: SubtaskVizContext | None = None,
) -> None:
    dataset.tolerance_s = tolerance_s
    subtask_ctx = subtask_ctx or SubtaskVizContext()
    info_level = getattr(getattr(rr, "TextLogLevel", None), "INFO", None)
    prev_subtask_index: int | None = None

    sampler = EpisodeSampler(dataset, episode_index)
    dataloader = torch.utils.data.DataLoader(
        dataset,
        batch_size=batch_size,
        sampler=sampler,
        shuffle=False,
        num_workers=0,
    )

    obs_feature = dataset.features.get("observation.state", {})
    obs_names = obs_feature.get("names")
    act_feature = dataset.features.get("action", {})
    act_names = act_feature.get("names")

    for batch in tqdm.tqdm(dataloader, desc="Processing frames"):
        batch_size_actual = batch["index"].shape[0]
        for i in range(batch_size_actual):
            timestamp = float(batch["timestamp"][i].item())
            _set_timeline_seconds(timestamp)

            if "observation.state" in batch:
                log_scalar_vector(
                    "observation/state",
                    batch["observation.state"][i],
                    obs_names,
                )

            if "action" in batch:
                log_scalar_vector(
                    "action",
                    batch["action"][i],
                    act_names,
                )

            for meta_key, rr_path in (
                ("subtask_index", "metadata/subtask_index"),
                ("subtask_success", "metadata/subtask_success"),
                ("observation.intervention", "metadata/intervention"),
                ("task_index", "metadata/task_index"),
            ):
                if meta_key in batch:
                    _log_scalar(rr_path, _tensor_scalar(batch[meta_key][i]))

            if "subtask_index" in batch:
                st_idx = int(_tensor_scalar(batch["subtask_index"][i]))
                if st_idx != prev_subtask_index:
                    prev_subtask_index = st_idx
                    label = subtask_ctx.index_to_text.get(st_idx, f"subtask {st_idx}")
                    if "subtask" in batch:
                        subtask_str = batch["subtask"][i]
                        if not isinstance(subtask_str, str):
                            subtask_str = str(subtask_str)
                        label = subtask_str
                    _log_text("metadata/subtask_current", f"[{st_idx}] {label}", level=info_level)
                    if "task" in batch:
                        task_str = batch["task"][i]
                        if not isinstance(task_str, str):
                            task_str = str(task_str)
                        _log_text("metadata/task_prompt", task_str, level=info_level)

            if log_images:
                for cam_key in dataset.meta.camera_keys:
                    if cam_key in batch:
                        image = batch[cam_key][i]
                        img = to_hwc_uint8(image)
                        rr.log(f"camera/{cam_key}", rr.Image(img))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Visualize LeRobot dataset with Rerun.")
    parser.add_argument(
        "dataset_path",
        nargs="?",
        default=None,
        help="Path to the dataset directory (default: latest under ./dataset)",
    )
    parser.add_argument("--episode-index", type=int, default=0, help="Episode to visualize")
    parser.add_argument(
        "--batch-size",
        type=int,
        default=4,
        help="Batch size for DataLoader",
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=0.04,
        help="Tolerance (seconds) for video timestamp alignment",
    )
    parser.add_argument(
        "--no-video",
        action="store_true",
        help="Skip logging camera images",
    )
    parser.add_argument(
        "--info-only",
        action="store_true",
        help="Only print dataset information",
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="Run rerun viewer in server mode (no auto-spawn window)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.dataset_path is None:
        dataset_root = REPO_ROOT / "dataset"
        candidates = sorted(dataset_root.glob("grasp_dataset_*"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not candidates:
            raise FileNotFoundError(f"No datasets found under {dataset_root}")
        dataset_path = candidates[0]
        print(f"[info] No dataset path provided, using latest: {dataset_path}")
    else:
        dataset_path = Path(args.dataset_path).expanduser().resolve()
    if not dataset_path.exists():
        raise FileNotFoundError(f"Dataset path does not exist: {dataset_path}")

    dataset = LeRobotDataset(repo_id=dataset_path.name, root=str(dataset_path))

    if not (0 <= args.episode_index < dataset.meta.total_episodes):
        raise ValueError(
            f"Episode index {args.episode_index} out of range "
            f"(max {dataset.meta.total_episodes - 1})"
        )

    print_dataset_info(dataset, episode_index=args.episode_index)

    if args.info_only:
        return

    subtask_ctx = build_subtask_viz_context(dataset_path, args.episode_index)

    viewer_name = f"{dataset.repo_id}/episode_{args.episode_index}"
    rr.init(viewer_name, spawn=not args.serve)
    if args.serve:
        rr.serve()

    log_subtask_reference_to_rerun(subtask_ctx, args.episode_index)

    visualize_episode(
        dataset=dataset,
        episode_index=args.episode_index,
        batch_size=args.batch_size,
        tolerance_s=args.tolerance,
        log_images=not args.no_video,
        subtask_ctx=subtask_ctx,
    )
    print("✅ Visualization finished.")


if __name__ == "__main__":
    main()
