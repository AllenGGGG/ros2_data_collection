'''
python3 "/home/zihang/A_W2_ROBOT/lerobot_convert/convert_to_lerobot_dataset_pi05_gripper_command_rollout_mcap.py" \
  --input-root "/home/zihang/A_W2_ROBOT/lerobot_convert/20260602_175821_448833" \
  --output-dir "/home/zihang/A_W2_ROBOT/lerobot_convert/lerobot_mcap" \
  --action-pose-mode delta
多线程版本
一条数据 num-workers 32 -> 47"
python3 /home/f/code/lerobot_data/convert_to_lerobot_dataset_pi05_gripper_command_rollout_mcap.py \
  --input-root /home/f/code/lerobot_data/20260603_161 \
  --output-dir /home/f/code/lerobot_data/test \
  --action-pose-mode delta
'''
import argparse
import csv
import io
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd
from PIL import Image, UnidentifiedImageError
from pandas.errors import EmptyDataError

from lerobot.datasets.lerobot_dataset import LeRobotDataset

try:
    from rosbags.highlevel import AnyReader
    from rosbags.typesys import Stores, get_typestore
except ImportError:
    AnyReader = None
    Stores = None
    get_typestore = None

try:
    import rosbag2_py
    from rclpy.serialization import deserialize_message
    from rosidl_runtime_py.utilities import get_message
except ImportError:
    rosbag2_py = None
    deserialize_message = None
    get_message = None

FPS = 30

# PI0.5 (pi05) in LeRobot expects fixed-size vectors (max_state_dim/max_action_dim).
# We export padded 32D state and action to match the common `pi05-base` config.
PI05_MAX_DIM = 32

# EE pose (pos + quat) + grippers.
LEFT_POS_NAMES = ["left_pos_x", "left_pos_y", "left_pos_z"]
LEFT_QUAT_NAMES = ["left_quat_x", "left_quat_y", "left_quat_z", "left_quat_w"]
RIGHT_POS_NAMES = ["right_pos_x", "right_pos_y", "right_pos_z"]
RIGHT_QUAT_NAMES = ["right_quat_x", "right_quat_y", "right_quat_z", "right_quat_w"]
EE_BASE_STATE_NAMES = (
    LEFT_POS_NAMES
    + LEFT_QUAT_NAMES
    + ["left_gripper"]
    + RIGHT_POS_NAMES
    + RIGHT_QUAT_NAMES
    + ["right_gripper"]
)

# Match pi05 config feature keys.
CAMERA_KEY_TO_DIRNAME = {
    "base_0_rgb": "camera_head",
    "left_wrist_0_rgb": "camera_left_wrist",
    "right_wrist_0_rgb": "camera_right_wrist",
}

TEXT_INPUT = "Pick up the blue Finasteride Tablets medicine box from the delivery box and place it into slot 2 of the storage box, then return to the home position."

#Pick up the green-and-white Oral Cleansing Spray medicine box from the delivery box and place it in the top‑left slot of the storage box as seen in the main camera. Then return to the home pose.
#Pick up the blue Finasteride Tablets medicine box from the delivery box and place it into slot 2 of the storage box, then return to the home position.

NEW_FORMAT_RECORDING_DIR = "recording"
NEW_FORMAT_MCAP_NAME = "recording_0.mcap"
EXTRACTED_CACHE_DIR = "_lerobot_extracted_cache"

COMPRESSED_CAMERA_TOPIC_MAP: dict[str, tuple[str, str]] = {
    "/camera_head/color/image_raw/compressed": ("image_compressed", "camera_head"),
    "/camera_left_wrist/color/image_raw/compressed": ("image_compressed", "camera_left_wrist"),
    "/camera_right_wrist/color/image_raw/compressed": ("image_compressed", "camera_right_wrist"),
}

POSE_GRIPPER_TOPIC_MAP: dict[str, tuple[str, str]] = {
    "/left_current_pose": ("pose", "left_pose"),
    "/right_current_pose": ("pose", "right_pose"),
    "/left_gripper_controller/target_command": ("gripper_command", "left_gripper"),
    "/right_gripper_controller/target_command": ("gripper_command", "right_gripper"),
}


def _read_recording_topics(recording_dir: Path) -> set[str]:
    metadata_yaml = recording_dir / "metadata.yaml"
    if not metadata_yaml.is_file():
        return set()
    topics: set[str] = set()
    in_topics = False
    for line in metadata_yaml.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped == "topics_with_message_count:":
            in_topics = True
            continue
        if in_topics:
            if stripped.startswith("- topic_metadata:"):
                continue
            if stripped.startswith("name:"):
                topics.add(stripped.split(":", 1)[1].strip())
                continue
            if stripped and not stripped.startswith("-"):
                in_topics = False
    return topics


def _ros_ns_to_timestamp_str(ns: int) -> str:
    sec = int(ns) // 1_000_000_000
    micros = (int(ns) % 1_000_000_000) // 1_000
    dt = datetime.fromtimestamp(sec)
    return dt.strftime("%Y%m%d_%H%M%S_") + f"{micros:06d}"


def _looks_like_new_episode_dir(path: Path) -> bool:
    return (
        (path / "metadata.json").is_file()
        and (path / NEW_FORMAT_RECORDING_DIR).is_dir()
        and (path / NEW_FORMAT_RECORDING_DIR / NEW_FORMAT_MCAP_NAME).is_file()
    )


def _extract_gripper_from_joint_state(msg, *, side: str) -> float | None:
    names = [str(n).lower() for n in getattr(msg, "name", [])]
    positions = list(getattr(msg, "position", []))
    if not names or not positions or len(names) != len(positions):
        return None

    side_keywords = {
        "left": ("left", "l_", "/l", "_l"),
        "right": ("right", "r_", "/r", "_r"),
    }[side]
    grip_keywords = ("gripper", "finger", "jaw", "claw")

    side_idx = []
    for idx, name in enumerate(names):
        if any(k in name for k in side_keywords) and any(k in name for k in grip_keywords):
            side_idx.append(idx)

    if not side_idx:
        for idx, name in enumerate(names):
            if any(k in name for k in side_keywords):
                side_idx.append(idx)

    if not side_idx:
        return None

    vals = [float(positions[i]) for i in side_idx if i < len(positions)]
    if not vals:
        return None
    return float(np.mean(vals))


def _write_camera_frames_and_csv(
    camera_rows: list[tuple[int, Image.Image | bytes]],
    camera_dir: Path,
    csv_path: Path,
):
    camera_dir.mkdir(parents=True, exist_ok=True)
    camera_rows_sorted = sorted(camera_rows, key=lambda x: x[0])
    csv_rows = []
    for idx, (ts_ns, payload) in enumerate(camera_rows_sorted):
        if isinstance(payload, bytes):
            # Keep bag JPEG bytes as-is to avoid decode->PNG re-encode during cache extraction.
            img_name = f"frame_{idx:06d}.jpg"
            (camera_dir / img_name).write_bytes(payload)
        else:
            img_name = f"frame_{idx:06d}.png"
            payload.save(camera_dir / img_name)
        csv_rows.append((img_name, _ros_ns_to_timestamp_str(ts_ns)))

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["image", "timestamp"])
        writer.writerows(csv_rows)


def _write_pose_csv(pose_rows: list[tuple[int, object]], csv_path: Path, side: str):
    pose_rows_sorted = sorted(pose_rows, key=lambda x: x[0])
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "timestamp",
                f"{side}_pose_x",
                f"{side}_pose_y",
                f"{side}_pose_z",
                f"{side}_orient_x",
                f"{side}_orient_y",
                f"{side}_orient_z",
                f"{side}_orient_w",
            ]
        )
        for ts_ns, pose_msg in pose_rows_sorted:
            p = pose_msg.pose.position
            q = pose_msg.pose.orientation
            writer.writerow(
                [
                    _ros_ns_to_timestamp_str(ts_ns),
                    float(p.x),
                    float(p.y),
                    float(p.z),
                    float(q.x),
                    float(q.y),
                    float(q.z),
                    float(q.w),
                ]
            )


def _write_gripper_events_csv(event_rows: list[tuple[int, float]], csv_path: Path):
    if not event_rows:
        return
    event_rows_sorted = sorted(event_rows, key=lambda x: x[0])
    dedup_rows = [event_rows_sorted[0]]
    for ts_ns, val in event_rows_sorted[1:]:
        if abs(val - dedup_rows[-1][1]) > 1e-6:
            dedup_rows.append((ts_ns, val))

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "value"])
        for ts_ns, val in dedup_rows:
            writer.writerow([_ros_ns_to_timestamp_str(ts_ns), float(val)])


def _decode_ros_image_to_pil(msg) -> Image.Image:
    width = int(msg.width)
    height = int(msg.height)
    encoding = str(msg.encoding).lower()
    raw = bytes(msg.data)

    if width <= 0 or height <= 0:
        raise ValueError(f"Invalid image shape width={width}, height={height}")

    if encoding == "rgb8":
        arr = np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 3)
        return Image.fromarray(arr, mode="RGB")
    if encoding == "bgr8":
        arr = np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 3)[:, :, ::-1]
        return Image.fromarray(arr, mode="RGB")
    if encoding == "rgba8":
        arr = np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 4)
        return Image.fromarray(arr[:, :, :3], mode="RGB")
    if encoding == "bgra8":
        arr = np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 4)[:, :, [2, 1, 0]]
        return Image.fromarray(arr, mode="RGB")
    if encoding == "mono8":
        arr = np.frombuffer(raw, dtype=np.uint8).reshape(height, width)
        return Image.fromarray(arr, mode="L").convert("RGB")

    raise ValueError(f"Unsupported image encoding: {encoding}")


def _decode_ros_compressed_image_to_pil(msg) -> Image.Image:
    return Image.open(io.BytesIO(bytes(msg.data))).convert("RGB")


def _extract_gripper_command(msg) -> float:
    if not hasattr(msg, "data"):
        raise ValueError(f"Unsupported gripper command message without data field: {type(msg)}")
    return float(msg.data)


def _build_extracted_cache_from_episode(episode_dir: Path) -> Path:
    has_rosbags = AnyReader is not None and get_typestore is not None and Stores is not None
    has_rosbag2_py = (
        rosbag2_py is not None
        and deserialize_message is not None
        and get_message is not None
    )
    if not has_rosbags and not has_rosbag2_py:
        raise ImportError(
            "Detected MCAP input format, but neither python package 'rosbags' nor ROS2 "
            "'rosbag2_py' is available. Install rosbags with `pip install rosbags` or "
            "source your ROS2 environment before running this script."
        )

    cache_dir = episode_dir / EXTRACTED_CACHE_DIR
    if _looks_like_trajectory_dir(cache_dir):
        return cache_dir
    if cache_dir.exists():
        print(f"  ↳ Existing extracted cache is incomplete, rebuilding: {cache_dir}")
        shutil.rmtree(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    recording_dir = episode_dir / NEW_FORMAT_RECORDING_DIR
    if not recording_dir.is_dir():
        raise FileNotFoundError(f"Recording dir not found: {recording_dir}")

    # Only read sensor_msgs/CompressedImage topics; never subscribe to image_raw.
    topic_map = {**COMPRESSED_CAMERA_TOPIC_MAP, **POSE_GRIPPER_TOPIC_MAP}
    image_buffers: dict[str, list[tuple[int, Image.Image | bytes]]] = {
        "camera_head": [],
        "camera_left_wrist": [],
        "camera_right_wrist": [],
    }
    left_pose_rows: list[tuple[int, object]] = []
    right_pose_rows: list[tuple[int, object]] = []
    left_grip_rows: list[tuple[int, float]] = []
    right_grip_rows: list[tuple[int, float]] = []

    metadata_path = episode_dir / "metadata.json"
    if metadata_path.is_file():
        with metadata_path.open("r", encoding="utf-8") as f:
            _ = json.load(f)

    def handle_message(topic: str, timestamp: int, msg) -> None:
        if topic not in topic_map:
            return
        kind, key = topic_map[topic]

        if kind == "image_compressed":
            image_buffers[key].append((int(timestamp), bytes(msg.data)))
        elif key == "left_pose":
            left_pose_rows.append((int(timestamp), msg))
        elif key == "right_pose":
            right_pose_rows.append((int(timestamp), msg))
        elif key == "left_gripper":
            left_grip_rows.append((int(timestamp), _extract_gripper_command(msg)))
        elif key == "right_gripper":
            right_grip_rows.append((int(timestamp), _extract_gripper_command(msg)))

    if has_rosbags:
        typestore = get_typestore(Stores.ROS2_JAZZY)
        with AnyReader([recording_dir], default_typestore=typestore) as reader:
            target_connections = [c for c in reader.connections if c.topic in topic_map]
            if not target_connections:
                raise ValueError(f"No expected topics found in {recording_dir}")

            for connection, timestamp, rawdata in reader.messages(connections=target_connections):
                msg = reader.deserialize(rawdata, connection.msgtype)
                handle_message(connection.topic, int(timestamp), msg)
    else:
        reader = rosbag2_py.SequentialReader()
        storage_options = rosbag2_py.StorageOptions(
            uri=str(recording_dir),
            storage_id="mcap",
        )
        converter_options = rosbag2_py.ConverterOptions(
            input_serialization_format="cdr",
            output_serialization_format="cdr",
        )
        reader.open(storage_options, converter_options)
        topic_types = {
            topic_metadata.name: topic_metadata.type
            for topic_metadata in reader.get_all_topics_and_types()
        }
        target_topic_types = {
            topic: msg_type
            for topic, msg_type in topic_types.items()
            if topic in topic_map
        }
        if not target_topic_types:
            raise ValueError(f"No expected topics found in {recording_dir}")

        msg_classes = {
            topic: get_message(msg_type)
            for topic, msg_type in target_topic_types.items()
        }
        while reader.has_next():
            topic, rawdata, timestamp = reader.read_next()
            if topic not in msg_classes:
                continue
            msg = deserialize_message(rawdata, msg_classes[topic])
            handle_message(topic, int(timestamp), msg)

    if not left_pose_rows or not right_pose_rows:
        raise ValueError(
            f"Missing pose topics in {recording_dir}. "
            "Need /left_current_pose and /right_current_pose."
        )
    required_camera_topics = {
        "camera_head": "/camera_head/color/image_raw/compressed",
        "camera_left_wrist": "/camera_left_wrist/color/image_raw/compressed",
        "camera_right_wrist": "/camera_right_wrist/color/image_raw/compressed",
    }
    missing_cameras = [
        topic_name
        for camera_name, topic_name in required_camera_topics.items()
        if not image_buffers[camera_name]
    ]
    if missing_cameras:
        available_topics = _read_recording_topics(recording_dir)
        camera_image_topics = sorted(
            topic
            for topic in available_topics
            if "/camera_" in topic and "/color/image_raw" in topic
        )
        hint = ""
        if camera_image_topics and all("/compressed" not in topic for topic in camera_image_topics):
            hint = (
                " Bag only contains image_raw topics; re-record with "
                ".../image_raw/compressed or enable compressed publishing."
            )
        raise ValueError(
            f"Missing required compressed camera topics in {recording_dir}: {missing_cameras}."
            f"{hint} Found camera image topics: {camera_image_topics or 'none'}."
        )

    _write_pose_csv(left_pose_rows, cache_dir / "left_ee_data.csv", side="left")
    _write_pose_csv(right_pose_rows, cache_dir / "right_ee_data.csv", side="right")
    _write_camera_frames_and_csv(
        image_buffers["camera_head"],
        cache_dir / "camera_head",
        cache_dir / "camera_head_timestamps.csv",
    )
    _write_camera_frames_and_csv(
        image_buffers["camera_left_wrist"],
        cache_dir / "camera_left_wrist",
        cache_dir / "camera_left_wrist_timestamps.csv",
    )
    _write_camera_frames_and_csv(
        image_buffers["camera_right_wrist"],
        cache_dir / "camera_right_wrist",
        cache_dir / "camera_right_wrist_timestamps.csv",
    )
    _write_gripper_events_csv(left_grip_rows, cache_dir / "left_gripper_command.csv")
    _write_gripper_events_csv(right_grip_rows, cache_dir / "right_gripper_command.csv")

    return cache_dir


def _adapt_input_trajectory_dir(path: Path) -> Path:
    if _looks_like_new_episode_dir(path):
        print(f"  ↳ Detected MCAP episode format at {path}, extracting cache...")
        cache_dir = _build_extracted_cache_from_episode(path)
        print(f"  ↳ Extracted cache ready: {cache_dir}")
        return cache_dir
    raise ValueError(
        f"Unsupported trajectory dir format: {path}. "
        "Expected MCAP episode format: metadata.json + recording/recording_0.mcap."
    )

def _center_crop(
    img: Image.Image,
    crop_ratio_w: float,
    crop_ratio_h: float,
    *,
    offset_x: int = 0,
    offset_y: int = 0,
) -> Image.Image:
    if crop_ratio_w <= 0 or crop_ratio_w > 1:
        raise ValueError(f"crop_ratio_w must be in (0, 1], got {crop_ratio_w}")
    if crop_ratio_h <= 0 or crop_ratio_h > 1:
        raise ValueError(f"crop_ratio_h must be in (0, 1], got {crop_ratio_h}")

    width, height = img.size
    crop_w = max(1, int(round(width * crop_ratio_w)))
    crop_h = max(1, int(round(height * crop_ratio_h)))

    left = (width - crop_w) // 2 + offset_x
    top = (height - crop_h) // 2 + offset_y
    left = max(0, min(left, width - crop_w))
    top = max(0, min(top, height - crop_h))
    right = left + crop_w
    bottom = top + crop_h
    return img.crop((left, top, right, bottom))


def _pad_to_aspect(
    img: Image.Image,
    target_ratio: float,
    *,
    fill: tuple[int, int, int] = (0, 0, 0),
) -> Image.Image:
    if target_ratio <= 0:
        raise ValueError(f"target_ratio must be > 0, got {target_ratio}")

    width, height = img.size
    current_ratio = width / height

    if abs(current_ratio - target_ratio) < 1e-6:
        return img

    if current_ratio > target_ratio:
        # too wide -> increase height
        new_height = int(round(width / target_ratio))
        new_width = width
    else:
        # too tall -> increase width
        new_width = int(round(height * target_ratio))
        new_height = height

    canvas = Image.new("RGB", (new_width, new_height), fill)
    left = (new_width - width) // 2
    top = (new_height - height) // 2
    canvas.paste(img, (left, top))
    return canvas


def load_image(
    path: str,
    *,
    image_size: int | None = None,
    center_crop_ratio_w: float | None = None,
    center_crop_ratio_h: float | None = None,
    center_crop_offset_x: int = 0,
    center_crop_offset_y: int = 0,
    pad_to_aspect_ratio: float | None = None,
) -> Image.Image:
    img = Image.open(path).convert("RGB")

    # Center-crop and then resize back to keep output size unchanged.
    if (
        center_crop_ratio_w is not None
        and center_crop_ratio_h is not None
        and (center_crop_ratio_w < 1 or center_crop_ratio_h < 1)
    ):
        original_size = img.size
        img = _center_crop(
            img,
            center_crop_ratio_w,
            center_crop_ratio_h,
            offset_x=center_crop_offset_x,
            offset_y=center_crop_offset_y,
        )
        if pad_to_aspect_ratio is not None:
            img = _pad_to_aspect(img, pad_to_aspect_ratio)
        if image_size is not None and image_size > 0:
            img = img.resize((image_size, image_size), resample=Image.BILINEAR)
        else:
            img = img.resize(original_size, resample=Image.BILINEAR)
    elif pad_to_aspect_ratio is not None:
        original_size = img.size
        img = _pad_to_aspect(img, pad_to_aspect_ratio)
        if image_size is not None and image_size > 0:
            img = img.resize((image_size, image_size), resample=Image.BILINEAR)
        else:
            img = img.resize(original_size, resample=Image.BILINEAR)
    elif image_size is not None and image_size > 0:
        img = img.resize((image_size, image_size), resample=Image.BILINEAR)

    return img


def _is_skippable_image_error(exc: Exception) -> bool:
    return isinstance(exc, (UnidentifiedImageError, OSError, FileNotFoundError))


def parse_timestamp(ts_str):
    """
    Parse timestamp like '20251229_183050_488122' to seconds (float).
    """
    dt = datetime.strptime(ts_str, "%Y%m%d_%H%M%S_%f")
    return dt.timestamp()


def _normalize_quat_xyzw(quat_xyzw: np.ndarray) -> np.ndarray:
    quat_xyzw = np.asarray(quat_xyzw, dtype=np.float32)
    if quat_xyzw.shape != (4,):
        raise ValueError(f"Expected quaternion shape (4,), got {quat_xyzw.shape}")
    norm = float(np.linalg.norm(quat_xyzw))
    if norm < 1e-8:
        # Fall back to identity quaternion if invalid.
        return np.asarray([0.0, 0.0, 0.0, 1.0], dtype=np.float32)
    return quat_xyzw / norm


def _canonicalize_quat_sequence_xyzw(quat_xyzw: np.ndarray) -> np.ndarray:
    """Keep quaternion signs continuous across a trajectory.

    q and -q represent the same orientation, but discontinuous signs create
    artificial jumps in state/action vectors.
    """
    quat_xyzw = np.asarray(quat_xyzw, dtype=np.float32).copy()
    if quat_xyzw.ndim != 2 or quat_xyzw.shape[1] != 4:
        raise ValueError(f"Expected quaternion array shape (N, 4), got {quat_xyzw.shape}")
    for idx in range(1, len(quat_xyzw)):
        if float(np.dot(quat_xyzw[idx - 1], quat_xyzw[idx])) < 0.0:
            quat_xyzw[idx] *= -1.0
    return quat_xyzw


def _canonicalize_delta_quat_xyzw(quat_xyzw: np.ndarray) -> np.ndarray:
    quat_xyzw = _normalize_quat_xyzw(quat_xyzw)
    if float(quat_xyzw[3]) < 0.0:
        quat_xyzw = -quat_xyzw
    return quat_xyzw.astype(np.float32)


def _quat_conjugate_xyzw(quat_xyzw: np.ndarray) -> np.ndarray:
    q = _normalize_quat_xyzw(quat_xyzw)
    return np.asarray([-q[0], -q[1], -q[2], q[3]], dtype=np.float32)


def _quat_multiply_xyzw(q1_xyzw: np.ndarray, q2_xyzw: np.ndarray) -> np.ndarray:
    """Quaternion multiplication for xyzw convention."""
    x1, y1, z1, w1 = _normalize_quat_xyzw(q1_xyzw)
    x2, y2, z2, w2 = _normalize_quat_xyzw(q2_xyzw)

    x = w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2
    y = w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2
    z = w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2
    w = w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2

    return _normalize_quat_xyzw(np.asarray([x, y, z, w], dtype=np.float32))


def build_ee_state(frame: dict) -> np.ndarray:
    """Build EE pose state vector (pos + quat + grippers) from aligned frame dict."""
    left_pos = np.asarray(frame["left_ee"]["position"], dtype=np.float32)
    left_quat = _normalize_quat_xyzw(frame["left_ee"]["orientation_quat"])
    right_pos = np.asarray(frame["right_ee"]["position"], dtype=np.float32)
    right_quat = _normalize_quat_xyzw(frame["right_ee"]["orientation_quat"])

    left_grip = np.asarray([frame["gripper"]["left"]], dtype=np.float32)
    right_grip = np.asarray([frame["gripper"]["right"]], dtype=np.float32)

    return np.concatenate(
        [left_pos, left_quat, left_grip, right_pos, right_quat, right_grip], axis=0
    ).astype(np.float32)


def build_ee_delta_action(curr: dict, next_: dict) -> np.ndarray:
    """Build relative EE action from current to next frame.

    Position uses subtraction; orientation uses relative quaternion.
    Gripper keeps absolute command value at next frame (step-wise hold semantics).
    q_delta = q_next * conj(q_curr).
    """
    left_dpos = (
        np.asarray(next_["left_ee"]["position"], dtype=np.float32)
        - np.asarray(curr["left_ee"]["position"], dtype=np.float32)
    )
    left_q_curr = np.asarray(curr["left_ee"]["orientation_quat"], dtype=np.float32)
    left_q_next = np.asarray(next_["left_ee"]["orientation_quat"], dtype=np.float32)
    left_dquat = _canonicalize_delta_quat_xyzw(
        _quat_multiply_xyzw(left_q_next, _quat_conjugate_xyzw(left_q_curr))
    )
    left_grip = np.asarray([float(next_["gripper"]["left"])], dtype=np.float32)

    right_dpos = (
        np.asarray(next_["right_ee"]["position"], dtype=np.float32)
        - np.asarray(curr["right_ee"]["position"], dtype=np.float32)
    )
    right_q_curr = np.asarray(curr["right_ee"]["orientation_quat"], dtype=np.float32)
    right_q_next = np.asarray(next_["right_ee"]["orientation_quat"], dtype=np.float32)
    right_dquat = _canonicalize_delta_quat_xyzw(
        _quat_multiply_xyzw(right_q_next, _quat_conjugate_xyzw(right_q_curr))
    )
    right_grip = np.asarray([float(next_["gripper"]["right"])], dtype=np.float32)

    return np.concatenate(
        [left_dpos, left_dquat, left_grip, right_dpos, right_dquat, right_grip],
        axis=0,
    ).astype(np.float32)


def pad_to_dim(x: np.ndarray, dim: int) -> np.ndarray:
    x = np.asarray(x, dtype=np.float32)
    if x.ndim != 1:
        raise ValueError(f"Expected 1D vector, got shape {x.shape}")
    if x.shape[0] > dim:
        raise ValueError(f"Vector dim {x.shape[0]} exceeds target dim {dim}")
    if x.shape[0] == dim:
        return x
    out = np.zeros((dim,), dtype=np.float32)
    out[: x.shape[0]] = x
    return out


def read_ee_data_quat(csv_path: str, *, side: str):
    """Read end-effector pose (position + quaternion) from CSV.

    Expected columns (same as XVLA raw export):
      - timestamp
      - {side}_pose_{x,y,z}
      - {side}_orient_{x,y,z,w}  (quaternion)
    """
    if side not in {"left", "right"}:
        raise ValueError(f"side must be 'left' or 'right', got {side}")

    df = pd.read_csv(csv_path)
    required = [
        "timestamp",
        f"{side}_pose_x",
        f"{side}_pose_y",
        f"{side}_pose_z",
        f"{side}_orient_x",
        f"{side}_orient_y",
        f"{side}_orient_z",
        f"{side}_orient_w",
    ]
    missing = set(required) - set(df.columns)
    if missing:
        raise ValueError(f"Missing columns in {csv_path}: {missing}")

    timestamp = df["timestamp"].apply(parse_timestamp).to_numpy()
    position = df[[f"{side}_pose_x", f"{side}_pose_y", f"{side}_pose_z"]].to_numpy(
        dtype=np.float32
    )
    quat = df[
        [f"{side}_orient_x", f"{side}_orient_y", f"{side}_orient_z", f"{side}_orient_w"]
    ].to_numpy(dtype=np.float32)

    # Normalize quaternions row-wise.
    norms = np.linalg.norm(quat, axis=1, keepdims=True)
    norms = np.clip(norms, 1e-8, None)
    quat = quat / norms
    quat = _canonicalize_quat_sequence_xyzw(quat)

    return {
        "timestamp": timestamp,
        "position": position,
        "orientation_quat": quat,
    }


def read_gripper_command(csv_path: str, *, initial_value: float = 1.0) -> dict:
    """Read event-based gripper command values.

    Expected columns:
      - timestamp
      - value

    Returns dict with sorted timestamps and values.
    """
    default = {
        "timestamp": np.array([]),
        "value": np.array([], dtype=np.float32),
        "initial": float(initial_value),
    }

    if not os.path.isfile(csv_path):
        return default

    try:
        df = pd.read_csv(csv_path)
    except EmptyDataError:
        return default

    if df.empty:
        return default
    if "timestamp" not in df.columns or "value" not in df.columns:
        raise ValueError(f"Missing columns in {csv_path}. Need 'timestamp' and 'value'.")

    ts = df["timestamp"].apply(parse_timestamp).to_numpy()
    vals = df["value"].to_numpy(dtype=np.float32)

    if ts.size > 0:
        order = np.argsort(ts)
        ts = ts[order]
        vals = vals[order]

    return {"timestamp": ts, "value": vals, "initial": float(initial_value)}


def read_interventions(csv_path: str) -> dict:
    """Read event-based intervention flags.

    Expected columns:
      - timestamp
      - value

    Returns dict with sorted timestamps and boolean flags. If file is missing
    or empty, defaults to expert control for all frames (intervention=True).
    """
    empty_result = {
        "timestamp": np.array([]),
        "value": np.array([], dtype=bool),
        "default": True,
    }

    if not os.path.isfile(csv_path):
        return empty_result

    try:
        df = pd.read_csv(csv_path)
    except EmptyDataError:
        return empty_result

    if df.empty:
        return empty_result

    if "timestamp" not in df.columns or "value" not in df.columns:
        raise ValueError(f"Missing columns in {csv_path}. Need 'timestamp' and 'value'.")

    ts = df["timestamp"].apply(parse_timestamp).to_numpy()
    raw_flags = df["value"].to_numpy()

    def _to_bool(val):
        if isinstance(val, str):
            val = val.strip().lower()
            if val in {"true", "1", "t", "yes", "y"}:
                return True
            if val in {"false", "0", "f", "no", "n"}:
                return False
        return bool(val)

    vals = np.array([_to_bool(v) for v in raw_flags], dtype=bool)

    if ts.size > 0:
        order = np.argsort(ts)
        ts = ts[order]
        vals = vals[order]

    # value represents "intervention" (expert takeover) directly.
    # For pre-first-event timestamps, default to the first event value.
    default_value = bool(vals[0]) if vals.size > 0 else True
    return {"timestamp": ts, "value": vals, "default": default_value}


def sample_command_at(cmd: dict, t_ref: float) -> float:
    ts = cmd["timestamp"]
    vals = cmd["value"]
    if ts.size == 0:
        return float(cmd["initial"])

    idx = np.searchsorted(ts, t_ref, side="right") - 1
    if idx < 0:
        return float(cmd["initial"])
    return float(vals[idx])


def sample_intervention_at(interventions: dict, t_ref: float) -> bool:
    ts = interventions["timestamp"]
    vals = interventions["value"]
    if ts.size == 0:
        return bool(interventions["default"])

    idx = np.searchsorted(ts, t_ref, side="right") - 1
    if idx < 0:
        return bool(interventions["default"])
    return bool(vals[idx])


def _format_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y%m%d_%H%M%S_%f")


def log_gripper_command_usage(
    *,
    gripper_cmd: dict,
    aligned_data: list,
    label: str,
) -> None:
    cmd = gripper_cmd[label]
    ts = cmd["timestamp"]
    vals = cmd["value"]

    print(
        f"  ↳ {label} gripper cmd events: {int(ts.size)} (initial={cmd['initial']})"
    )
    if ts.size > 0:
        print(
            f"     first cmd: {_format_ts(float(ts[0]))} -> {float(vals[0])}"
        )
        print(
            f"     last  cmd: {_format_ts(float(ts[-1]))} -> {float(vals[-1])}"
        )

    if not aligned_data:
        print("     no aligned frames to sample gripper cmd")
        return

    t_start = float(aligned_data[0]["timestamp"])
    t_end = float(aligned_data[-1]["timestamp"])
    v_start = sample_command_at(cmd, t_start)
    v_end = sample_command_at(cmd, t_end)
    print(
        "     sampled at aligned frames: "
        f"{_format_ts(t_start)} -> {v_start}, {_format_ts(t_end)} -> {v_end}"
    )


def log_intervention_usage(*, interventions: dict, aligned_data: list) -> None:
    ts = interventions["timestamp"]
    vals = interventions["value"]
    default = bool(interventions["default"])

    print(
        f"  ↳ intervention events: {int(ts.size)} (default={default})"
    )
    if ts.size > 0:
        print(
            f"     first event: {_format_ts(float(ts[0]))} -> {bool(vals[0])}"
        )
        print(
            f"     last  event: {_format_ts(float(ts[-1]))} -> {bool(vals[-1])}"
        )

    if not aligned_data:
        print("     no aligned frames to sample intervention")
        return

    t_start = float(aligned_data[0]["timestamp"])
    t_end = float(aligned_data[-1]["timestamp"])
    v_start = sample_intervention_at(interventions, t_start)
    v_end = sample_intervention_at(interventions, t_end)
    print(
        "     sampled at aligned frames: "
        f"{_format_ts(t_start)} -> {v_start}, {_format_ts(t_end)} -> {v_end}"
    )

    vals = [bool(frame.get("intervention", False)) for frame in aligned_data]
    if vals:
        true_count = sum(vals)
        ratio = true_count / len(vals)
        print(
            f"     aligned frames: intervention True {true_count}/{len(vals)} ({ratio:.1%})"
        )


def read_camera_images(dir_path, camera_name="camera_head"):
    """
    Read camera image paths and timestamps.

    Args:
        dir_path (str): base directory (e.g. 20251229_183050_452747)
        camera_name (str): e.g. "camera_head"

    Returns:
        list of dicts, each with:
            - timestamp
            - image_path
    """
    camera_dir = os.path.join(dir_path, camera_name)
    timestamp_csv = os.path.join(dir_path, f"{camera_name}_timestamps.csv")

    if not os.path.isdir(camera_dir):
        raise FileNotFoundError(f"Camera dir not found: {camera_dir}")

    if not os.path.isfile(timestamp_csv):
        raise FileNotFoundError(f"Timestamp CSV not found: {timestamp_csv}")

    df = pd.read_csv(timestamp_csv)

    required_columns = ["image", "timestamp"]
    missing = set(required_columns) - set(df.columns)
    if missing:
        raise ValueError(f"Missing columns in {timestamp_csv}: {missing}")

    frames = []

    for _, row in df.iterrows():
        img_path = os.path.join(camera_dir, row["image"])

        if not os.path.isfile(img_path):
            raise FileNotFoundError(f"Image not found: {img_path}")

        frames.append({
            "timestamp": parse_timestamp(row["timestamp"]),
            "image_path": img_path,
        })

    return frames


def find_nearest_index(t_ref, timestamps, tol):
    """
    Find index of nearest timestamp to t_ref within tolerance.

    Args:
        t_ref (float)
        timestamps (np.ndarray) shape (N,)
        tol (float): tolerance in same unit as timestamps

    Returns:
        int or None
    """
    idx = np.searchsorted(timestamps, t_ref)

    candidates = []
    if idx > 0:
        candidates.append(idx - 1)
    if idx < len(timestamps):
        candidates.append(idx)

    best_idx = None
    best_dt = None

    for i in candidates:
        dt = abs(timestamps[i] - t_ref)
        if dt <= tol and (best_dt is None or dt < best_dt):
            best_dt = dt
            best_idx = i

    return best_idx


def find_nearest_frame(t_ref, frames, tol, *, timestamps: np.ndarray | None = None):
    """Find the camera frame closest to t_ref within tol.

    If `timestamps` is provided (sorted, same length as `frames`), use an
    O(log N) searchsorted lookup; otherwise fall back to an O(N) scan.
    """
    if timestamps is not None:
        idx = find_nearest_index(t_ref, timestamps, tol)
        return frames[idx] if idx is not None else None

    best = None
    best_dt = None
    for f in frames:
        dt = abs(f["timestamp"] - t_ref)
        if dt <= tol and (best_dt is None or dt < best_dt):
            best = f
            best_dt = dt
    return best


def align_by_head_camera(
    head_frames: list,
    left_ee: dict,
    right_ee: dict,
    gripper_cmd: dict,
    interventions: dict,
    cameras: dict,
    tol=0.1,
):
    """Align modalities using head camera timestamp as reference.

    head_frames: list of {"timestamp", "image_path"}
    cameras: dict mapping camera keys to lists of frames
    """
    aligned = []

    camera_timestamps = {
        name: np.asarray([f["timestamp"] for f in frames], dtype=np.float64)
        for name, frames in cameras.items()
        if name != "base_0_rgb"
    }

    for head_frame in head_frames:
        t_ref = head_frame["timestamp"]

        idx_left = find_nearest_index(t_ref, left_ee["timestamp"], tol)
        idx_right = find_nearest_index(t_ref, right_ee["timestamp"], tol)

        if idx_left is None or idx_right is None:
            continue

        camera_frames = {}
        valid = True
        # cameras dict uses CAMERA_KEY_TO_DIRNAME keys like 'base_0_rgb'
        for name, frames in cameras.items():
            # use the provided head_frame for the head camera key
            if name == "base_0_rgb":
                camera_frames[name] = head_frame
                continue

            frame = find_nearest_frame(t_ref, frames, tol, timestamps=camera_timestamps[name])
            if frame is None:
                valid = False
                break
            camera_frames[name] = frame

        if not valid:
            continue

        aligned.append(
            {
                "timestamp": t_ref,
                "left_ee": {
                    "position": left_ee["position"][idx_left],
                    "orientation_quat": left_ee["orientation_quat"][idx_left],
                },
                "right_ee": {
                    "position": right_ee["position"][idx_right],
                    "orientation_quat": right_ee["orientation_quat"][idx_right],
                },
                "gripper": {
                    "left": sample_command_at(gripper_cmd["left"], t_ref),
                    "right": sample_command_at(gripper_cmd["right"], t_ref),
                },
                "intervention": sample_intervention_at(interventions, t_ref),
                "cameras": camera_frames,
            }
        )

    return aligned


def _auto_tune_runtime(*, requested_workers: int | None, parallel_encoding: bool = True) -> dict:
    cpu_count = max(1, os.cpu_count() or 4)
    worker_budget = (
        max(1, cpu_count // 2)
        if requested_workers is None or int(requested_workers) <= 0
        else max(1, int(requested_workers))
    )

    if worker_budget <= 1:
        return {
            "cpu_count": cpu_count,
            "worker_budget": worker_budget,
            "image_writer_threads": 0,
            "image_writer_processes": 0,
            "parallel_encoding": False,
        }

    image_writer_processes = min(4, max(1, worker_budget // 8))
    image_writer_threads = max(1, worker_budget // image_writer_processes)

    return {
        "cpu_count": cpu_count,
        "worker_budget": worker_budget,
        "image_writer_threads": image_writer_threads,
        "image_writer_processes": image_writer_processes,
        "parallel_encoding": bool(parallel_encoding),
    }


def _ffmpeg_has_encoder(encoder_name: str) -> bool:
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return False
    return f" {encoder_name} " in result.stdout


def _resolve_video_codec(requested_video_codec: str) -> str:
    if requested_video_codec == "auto":
        if _ffmpeg_has_encoder("h264_nvenc"):
            return "h264_nvenc"
        return "libsvtav1"

    if requested_video_codec in {"h264_nvenc", "hevc_nvenc"} and not _ffmpeg_has_encoder(
        requested_video_codec
    ):
        raise ValueError(
            f"Requested video codec '{requested_video_codec}' is not available in system ffmpeg."
        )

    return requested_video_codec


def _dataset_vcodec_for_codec(video_codec: str) -> str:
    if video_codec == "h264_nvenc":
        return "h264"
    if video_codec == "hevc_nvenc":
        return "hevc"
    return video_codec


def _is_valid_lerobot_dataset_root(dataset_root: Path) -> bool:
    """Return True only for a dataset root that can be opened for appending."""
    required_files = [
        dataset_root / "meta" / "info.json",
        dataset_root / "meta" / "tasks.parquet",
    ]
    return all(path.is_file() for path in required_files)


def _move_incomplete_dataset_root(dataset_root: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_root = dataset_root.with_name(f"{dataset_root.name}.incomplete_{timestamp}")
    suffix = 1
    while backup_root.exists():
        backup_root = dataset_root.with_name(
            f"{dataset_root.name}.incomplete_{timestamp}_{suffix}"
        )
        suffix += 1
    shutil.move(str(dataset_root), str(backup_root))
    return backup_root


def _remove_empty_images_dir(dataset_root: Path) -> bool:
    """Remove LeRobot's temporary images directory when video encoding left it empty."""
    images_dir = Path(dataset_root) / "images"
    if not images_dir.exists():
        return False
    if any(path.is_file() for path in images_dir.rglob("*")):
        return False
    shutil.rmtree(images_dir)
    return True


def _configure_dataset_runtime(
    dataset,
    *,
    image_size: int | None,
    center_crop_ratio_w: float,
    center_crop_ratio_h: float,
    center_crop_offset_x: int,
    center_crop_offset_y: int,
    pad_to_aspect_ratio: float | None,
    parallel_encoding: bool,
    video_codec: str,
    video_crf: int,
):
    dataset._pi05_image_size = image_size
    dataset._pi05_center_crop_ratio_w = center_crop_ratio_w
    dataset._pi05_center_crop_ratio_h = center_crop_ratio_h
    dataset._pi05_center_crop_offset_x = center_crop_offset_x
    dataset._pi05_center_crop_offset_y = center_crop_offset_y
    dataset._pi05_pad_to_aspect_ratio = pad_to_aspect_ratio
    dataset._pi05_parallel_encoding = bool(parallel_encoding)
    dataset._pi05_video_codec = video_codec
    dataset._pi05_video_crf = int(video_crf)
    _sync_pi05_runtime_to_writer(dataset)


def _sync_pi05_runtime_to_writer(dataset) -> None:
    """Copy pi05 runtime attrs onto DatasetWriter (lerobot >= recent API)."""
    writer = getattr(dataset, "writer", None)
    if writer is None:
        return
    for attr in (
        "_pi05_video_codec",
        "_pi05_video_crf",
        "_pi05_image_size",
        "_pi05_center_crop_ratio_w",
        "_pi05_center_crop_ratio_h",
        "_pi05_center_crop_offset_x",
        "_pi05_center_crop_offset_y",
        "_pi05_pad_to_aspect_ratio",
        "_pi05_parallel_encoding",
    ):
        if hasattr(dataset, attr):
            setattr(writer, attr, getattr(dataset, attr))


def _patch_dataset_compat(dataset):
    writer = getattr(dataset, "writer", None)
    if writer is None:
        return

    _sync_pi05_runtime_to_writer(dataset)

    writer._encode_temporary_episode_video = _encode_temporary_episode_video_av1.__get__(
        writer, type(writer)
    )

    if not hasattr(writer, "_original_save_episode_data"):
        writer._original_save_episode_data = writer._save_episode_data

        def _save_episode_data_with_success(self, episode_buffer):
            ep_metadata = self._original_save_episode_data(episode_buffer)
            if hasattr(self, "_current_episode_success"):
                ep_metadata["success"] = bool(self._current_episode_success)
            if hasattr(self, "_current_episode_quality"):
                ep_metadata["quality"] = int(self._current_episode_quality)
            return ep_metadata

        writer._save_episode_data = _save_episode_data_with_success.__get__(
            writer, type(writer)
        )


def aligned_to_lerobot_episode(
    aligned_data,
    dataset,
    *,
    success: bool,
    quality: int,
    action_pose_mode: str = "absolute",
):
    dataset._current_episode_success = bool(success)
    dataset._current_episode_quality = int(quality)
    if getattr(dataset, "writer", None) is not None:
        dataset.writer._current_episode_success = bool(success)
        dataset.writer._current_episode_quality = int(quality)

    frames_written = 0
    frames_skipped = 0
    candidate_frames = max(0, len(aligned_data) - 1)
    image_size = getattr(dataset, "_pi05_image_size", None)
    center_crop_ratio_w = getattr(dataset, "_pi05_center_crop_ratio_w", None)
    center_crop_ratio_h = getattr(dataset, "_pi05_center_crop_ratio_h", None)
    center_crop_offset_x = getattr(dataset, "_pi05_center_crop_offset_x", 0)
    center_crop_offset_y = getattr(dataset, "_pi05_center_crop_offset_y", 0)
    pad_to_aspect_ratio = getattr(dataset, "_pi05_pad_to_aspect_ratio", None)

    for i in range(len(aligned_data) - 1):
        curr = aligned_data[i]
        next_ = aligned_data[i + 1]

        obs_state = pad_to_dim(build_ee_state(curr), PI05_MAX_DIM)
        if action_pose_mode == "delta":
            act = pad_to_dim(build_ee_delta_action(curr, next_), PI05_MAX_DIM)
        else:
            act = pad_to_dim(build_ee_state(next_), PI05_MAX_DIM)

        frame_dict = {
            "task": TEXT_INPUT,
            "observation.state": obs_state,
            "observation.intervention": np.asarray(
                [int(bool(curr.get("intervention", False)))], dtype=np.int64
            ),
            "action": act,
        }

        try:
            for cam_key in CAMERA_KEY_TO_DIRNAME.keys():
                frame_dict[f"observation.images.{cam_key}"] = load_image(
                    curr["cameras"][cam_key]["image_path"],
                    image_size=image_size,
                    center_crop_ratio_w=(center_crop_ratio_w if cam_key == "base_0_rgb" else None),
                    center_crop_ratio_h=(center_crop_ratio_h if cam_key == "base_0_rgb" else None),
                    center_crop_offset_x=(center_crop_offset_x if cam_key == "base_0_rgb" else 0),
                    center_crop_offset_y=(center_crop_offset_y if cam_key == "base_0_rgb" else 0),
                    pad_to_aspect_ratio=(pad_to_aspect_ratio if cam_key == "base_0_rgb" else None),
                )
        except Exception as exc:
            if not _is_skippable_image_error(exc):
                raise
            frames_skipped += 1
            print(
                f"  ! Skipping sample at {_format_ts(float(curr['timestamp']))} "
                f"because an image is unreadable: {exc}"
            )
            continue

        dataset.add_frame(frame_dict)
        frames_written += 1

    if frames_written == 0:
        print("  ! No valid samples left after dropping unreadable images; episode not saved.")
        return {
            "frames_written": 0,
            "frames_skipped": frames_skipped,
            "candidate_frames": candidate_frames,
        }

    dataset.save_episode(
        parallel_encoding=bool(getattr(dataset, "_pi05_parallel_encoding", True))
    )
    _remove_empty_images_dir(Path(dataset.root))
    return {
        "frames_written": frames_written,
        "frames_skipped": frames_skipped,
        "candidate_frames": candidate_frames,
    }


def filter_pauses(
    aligned_data,
    *,
    min_state_delta: float,
    min_grip_delta: float,
    min_dt: float,
    keep_after_grip_sec: float,
):
    """Remove long/near-static segments caused by operator pauses.

    Strategy: keep frames where either
      - EE state changes more than min_state_delta, or
      - gripper changes more than min_grip_delta, or
      - time gap exceeds min_dt (to avoid collapsing large time jumps).
    Always keep the first frame.
    """
    if not aligned_data:
        return aligned_data

    kept = [aligned_data[0]]
    prev = aligned_data[0]
    prev_state = build_ee_state(prev)
    grip_keep_until = float("-inf")

    for curr in aligned_data[1:]:
        curr_state = build_ee_state(curr)
        state_delta = float(np.linalg.norm(curr_state - prev_state))

        grip_delta = max(
            abs(float(curr["gripper"]["left"]) - float(prev["gripper"]["left"])),
            abs(float(curr["gripper"]["right"]) - float(prev["gripper"]["right"])),
        )

        dt = float(curr["timestamp"] - prev["timestamp"])

        if grip_delta > min_grip_delta:
            grip_keep_until = curr["timestamp"] + keep_after_grip_sec

        if (
            state_delta > min_state_delta
            or grip_delta > min_grip_delta
            or dt > min_dt
            or curr["timestamp"] <= grip_keep_until
        ):
            kept.append(curr)
            prev = curr
            prev_state = curr_state

    return kept


def infer_video_shape(
    sample_dir,
    *,
    image_size: int | None = None,
    center_crop_ratio_w: float | None = None,
    center_crop_ratio_h: float | None = None,
    center_crop_offset_x: int = 0,
    center_crop_offset_y: int = 0,
    pad_to_aspect_ratio: float | None = None,
):
    """Infer (C, H, W) from the first head camera frame inside sample_dir.

    Uses the same load_image options as episode export so metadata shape matches videos.
    """
    sample_dir = _adapt_input_trajectory_dir(Path(sample_dir))
    head_frames = read_camera_images(str(sample_dir), camera_name="camera_head")
    if not head_frames:
        raise ValueError(f"No head camera frames found in {sample_dir}")

    last_exc = None
    for frame in head_frames:
        try:
            sample_image = load_image(
                frame["image_path"],
                image_size=image_size,
                center_crop_ratio_w=center_crop_ratio_w,
                center_crop_ratio_h=center_crop_ratio_h,
                center_crop_offset_x=center_crop_offset_x,
                center_crop_offset_y=center_crop_offset_y,
                pad_to_aspect_ratio=pad_to_aspect_ratio,
            )
            width, height = sample_image.size
            channels = len(sample_image.getbands())
            return (channels, height, width)
        except Exception as exc:
            if not _is_skippable_image_error(exc):
                raise
            last_exc = exc

    raise ValueError(
        f"No readable head camera frames found in {sample_dir}. Last error: {last_exc}"
    )


def export_states_csv(aligned_data, output_path):
    raise RuntimeError(
        "export_states_csv() is now mode-dependent; use export_states_csv_with_names()."
    )


def export_states_csv_with_names(aligned_data, output_path, *, state_names: list[str]):
    rows = []
    for frame in aligned_data:
        state_vec = pad_to_dim(build_ee_state(frame), PI05_MAX_DIM)

        row = {"timestamp": frame["timestamp"]}
        if len(state_vec) != len(state_names):
            raise ValueError(
                f"State dim mismatch: vec has {len(state_vec)} dims but state_names has {len(state_names)} names"
            )
        row.update({state_names[i]: float(state_vec[i]) for i in range(len(state_names))})
        rows.append(row)

    df = pd.DataFrame(rows)
    df.to_csv(output_path, index=False)


def convert_trajectory(
    dir_path,
    dataset,
    tol=0.1,
    export_states=False,
    *,
    state_names: list[str],
    success: bool,
    quality: int,
    remove_pauses: bool = False,
    min_state_delta: float = 1e-3,
    min_grip_delta: float = 1e-3,
    min_dt: float = 0.5,
    keep_after_grip_sec: float = 1.0,
    action_pose_mode: str = "absolute",
):
    """Convert a single trajectory directory into one LeRobot episode."""
    dir_path = _adapt_input_trajectory_dir(Path(dir_path))

    start_episode_fn = getattr(dataset, "start_episode", None)
    if callable(start_episode_fn):
        start_episode_fn()

    left_ee_csv_path = dir_path / "left_ee_data.csv"
    right_ee_csv_path = dir_path / "right_ee_data.csv"
    left_gripper_cmd_csv_path = dir_path / "left_gripper_command.csv"
    right_gripper_cmd_csv_path = dir_path / "right_gripper_command.csv"
    interventions_csv_path = dir_path / "interventions.csv"

    left_ee = read_ee_data_quat(str(left_ee_csv_path), side="left")
    right_ee = read_ee_data_quat(str(right_ee_csv_path), side="right")

    # Event-based gripper command (step-wise hold). Initial values are open=1.
    gripper_cmd = {
        "left": read_gripper_command(str(left_gripper_cmd_csv_path), initial_value=1.0),
        "right": read_gripper_command(str(right_gripper_cmd_csv_path), initial_value=1.0),
    }

    interventions = read_interventions(str(interventions_csv_path))

    cameras = {}
    for cam_key, camera_dirname in CAMERA_KEY_TO_DIRNAME.items():
        cameras[cam_key] = read_camera_images(str(dir_path), camera_name=camera_dirname)

    # Use head camera ('base_0_rgb') frames as the reference for alignment
    head_frames = cameras.get("base_0_rgb", [])
    aligned_data = align_by_head_camera(
        head_frames,
        left_ee,
        right_ee,
        gripper_cmd,
        interventions,
        cameras,
        tol=tol,
    )

    if remove_pauses:
        before_count = len(aligned_data)
        aligned_data = filter_pauses(
            aligned_data,
            min_state_delta=min_state_delta,
            min_grip_delta=min_grip_delta,
            min_dt=min_dt,
            keep_after_grip_sec=keep_after_grip_sec,
        )
        removed = before_count - len(aligned_data)
        print(f"  ↳ Removed {removed} pause frames (kept {len(aligned_data)}/{before_count})")

    log_gripper_command_usage(gripper_cmd=gripper_cmd, aligned_data=aligned_data, label="left")
    log_gripper_command_usage(gripper_cmd=gripper_cmd, aligned_data=aligned_data, label="right")
    log_intervention_usage(interventions=interventions, aligned_data=aligned_data)

    if len(aligned_data) < 2:
        raise ValueError(f"Aligned data in {dir_path} is too short to form an episode")

    if export_states:
        csv_path = dir_path / "lerobot_states_debug.csv"
        export_states_csv_with_names(aligned_data, csv_path, state_names=state_names)
        print(f"  ↳ Exported states to {csv_path}")

    episode_stats = aligned_to_lerobot_episode(
        aligned_data,
        dataset,
        success=success,
        quality=quality,
        action_pose_mode=action_pose_mode,
    )
    print(
        "  ↳ Sample stats: "
        f"valid={episode_stats['frames_written']}, "
        f"dropped_bad_images={episode_stats['frames_skipped']}, "
        f"candidates={episode_stats['candidate_frames']}"
    )
    return episode_stats


def _looks_like_trajectory_dir(path: Path) -> bool:
    """Heuristic check whether path is an internal extracted trajectory cache."""
    required = [
        "left_ee_data.csv",
        "right_ee_data.csv",
        "camera_head_timestamps.csv",
        "camera_left_wrist_timestamps.csv",
        "camera_right_wrist_timestamps.csv",
        "camera_head",
        "camera_left_wrist",
        "camera_right_wrist",
    ]
    if not all((path / name).exists() for name in required):
        return False
    return all(
        any((path / camera_name).glob("frame_*.jpg"))
        or any((path / camera_name).glob("frame_*.png"))
        for camera_name in ["camera_head", "camera_left_wrist", "camera_right_wrist"]
    )


def _parse_quality_dir_name(path: Path) -> int:
    """Parse quality label from a directory name.

    Expected folder names are integer quality levels: 0, 1, 2, 3.
    """
    try:
        quality = int(path.name)
    except ValueError as exc:
        raise ValueError(
            f"Expected quality folder name to be an integer in [0, 3], got {path.name!r} at {path}"
        ) from exc

    if quality < 0 or quality > 3:
        raise ValueError(f"Expected quality folder name in [0, 3], got {quality} at {path}")

    return quality


def _quality_and_success_from_parent_or_default(p: Path) -> tuple[int, bool]:
    """Infer quality from parent folder name, or fall back to default success data."""
    try:
        quality = _parse_quality_dir_name(p.parent)
    except ValueError:
        quality = 3
    return quality, quality != 0


def collect_trajectory_dirs(input_root: Path) -> list[tuple[Path, bool, int]]:
    """Collect MCAP episode dirs with success flag and integer quality metadata.

    Rules:
      1) If input_root itself is an episode dir, infer quality from its parent folder
         name (0/1/2/3), or default to quality=3 and success=True if no such parent
         folder exists.
      2) If input_root is a task dir, every immediate child episode dir is converted.
      3) If an immediate subfolder name is 0/1/2/3, use that as the quality label.
         Success is defined as quality != 0.
         - If that folder itself looks like an episode dir, use it directly.
         - Otherwise, use its immediate child episode dirs.
      4) If no quality-labeled child folders are found, treat input_root as a task dir
         and convert its immediate episode children with quality=3, success=True.
    """
    input_root = Path(input_root).resolve()

    if _looks_like_new_episode_dir(input_root):
        quality, success = _quality_and_success_from_parent_or_default(input_root)
        return [(input_root, success, quality)]

    child_dirs = sorted([p for p in input_root.iterdir() if p.is_dir()])
    if not child_dirs:
        raise ValueError(
            f"No trajectory folders found under {input_root}: expected "
            "MCAP episode directory with metadata.json and recording/recording_0.mcap "
            "on this path or inside a subfolder."
        )

    trajectories: list[tuple[Path, bool, int]] = []

    for p in child_dirs:
        try:
            quality = _parse_quality_dir_name(p)
        except ValueError:
            continue

        success = quality != 0

        if _looks_like_new_episode_dir(p):
            trajectories.append((p, success, quality))
        else:
            sub_trajs = sorted(
                [
                    c
                    for c in p.iterdir()
                    if c.is_dir() and _looks_like_new_episode_dir(c)
                ]
            )
            trajectories.extend((c, success, quality) for c in sub_trajs)

    if trajectories:
        return trajectories

    traj_children = [p for p in child_dirs if _looks_like_new_episode_dir(p)]
    if not traj_children:
        raise ValueError(
            f"No trajectory folders found under {input_root}. "
            "Each episode directory must contain metadata.json and recording/recording_0.mcap."
        )
    labeled_trajs: list[tuple[Path, bool, int]] = []
    for p in traj_children:
        labeled_trajs.append((p, True, 3))
    return labeled_trajs


# ----- 视频编码覆盖函数 -----
def _encode_temporary_episode_video_av1(self, video_key: str, episode_index: int) -> Path:
    """Encode temporary PNG frames into MP4, optionally using NVENC."""
    import tempfile
    import shutil
    from pathlib import Path
    from lerobot.datasets.video_utils import encode_video_frames

    root = getattr(self, "root", None)
    if root is None:
        root = self._root
    fps = getattr(self, "fps", None)
    if fps is None:
        fps = self._meta.fps

    # 生成目标 MP4 路径
    mp4_path = Path(tempfile.mkdtemp(dir=root)) / f"{video_key}_{episode_index:06d}.mp4"
    img_dir = self._get_image_file_dir(episode_index, video_key)
    video_codec = getattr(self, "_pi05_video_codec", "libsvtav1")
    video_crf = int(getattr(self, "_pi05_video_crf", 23))

    if video_codec in {"h264_nvenc", "hevc_nvenc"}:
        codec_args = [
            "-c:v",
            video_codec,
            "-pix_fmt",
            "yuv420p",
            "-preset",
            "p4",
            "-cq",
            str(video_crf),
        ]
        cmd = [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-framerate",
            str(fps),
            "-i",
            str(img_dir / "frame-%06d.png"),
            *codec_args,
            str(mp4_path),
        ]
        try:
            subprocess.run(cmd, check=True)
        except FileNotFoundError as exc:
            raise RuntimeError("ffmpeg is required for GPU video encoding but was not found.") from exc
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                f"ffmpeg failed while encoding {video_key} episode {episode_index} with codec "
                f"{video_codec}."
            ) from exc
    else:
        encode_video_frames(
            img_dir,
            mp4_path,
            fps,
            vcodec=video_codec,
            crf=video_crf,
            overwrite=True,
        )

    # 删除临时图片目录（节省空间）
    if img_dir.exists():
        shutil.rmtree(img_dir)

    return mp4_path


def main():
    parser = argparse.ArgumentParser(description="Convert one or more trajectories to the LeRobot format.")
    parser.add_argument(
        "--input-root",
        default="handover_recap_rollout_round3_18trajs_dataset_absolute",
        help=(
            "Single episode directory or task directory containing many episode "
            "subdirectories. An episode has metadata.json + recording/recording_0.mcap."
        ),
    )
    parser.add_argument(
        "--output-dir",
        default="handover_recap_rollout_round3_18trajs_lerobot_dataset_absolute",
        help="Destination directory for the LeRobot dataset",
    )
    parser.add_argument(
        "--tol",
        type=float,
        default=0.1,
        help="Timestamp tolerance (seconds) for modality alignment",
    )
    parser.add_argument(
        "--export-states",
        action="store_true",
        help="Dump aligned observation states for each trajectory to <traj>/lerobot_states_debug.csv for inspection",
    )
    parser.add_argument(
        "--image-size",
        type=int,
        default=0,
        help="Resize images to square (SxS) before writing dataset videos. Set 0 to keep original resolution.",
    )
    parser.add_argument(
        "--center-crop-ratio-w",
        type=float,
        default=1.0,
        help="Center-crop ratio for head camera width (0-1]. 1.0 disables crop (default).",
    )
    parser.add_argument(
        "--center-crop-ratio-h",
        type=float,
        default=1.0,
        help="Center-crop ratio for head camera height (0-1]. 1.0 disables crop (default).",
    )
    parser.add_argument(
        "--center-crop-offset-x",
        type=int,
        default=0,
        help="Horizontal crop offset in pixels for head camera. Positive shifts crop window to the right.",
    )
    parser.add_argument(
        "--center-crop-offset-y",
        type=int,
        default=0,
        help="Vertical crop offset in pixels for head camera. Positive shifts crop window downward.",
    )
    parser.add_argument(
        "--pad-to-aspect-ratio",
        type=float,
        default=None,
        help="Pad cropped head camera image to this aspect ratio before resizing (e.g. 4/3). Omit to skip padding.",
    )
    parser.add_argument(
        "--remove-pauses",
        action="store_true",
        help="Remove near-static segments caused by operator pauses",
    )
    parser.add_argument(
        "--min-state-delta",
        type=float,
        default=5*1e-3,
        help="Minimum EE state L2 change to keep a frame when --remove-pauses is set",
    )
    parser.add_argument(
        "--min-grip-delta",
        type=float,
        default=1e-2,
        help="Minimum gripper change to keep a frame when --remove-pauses is set",
    )
    parser.add_argument(
        "--min-dt",
        type=float,
        default=0.5,
        help="Always keep frames separated by more than this time gap (seconds)",
    )
    parser.add_argument(
        "--keep-after-grip-sec",
        type=float,
        default=1.5,
        help="Keep frames for this many seconds after a gripper change when --remove-pauses is set",
    )
    parser.add_argument(
        "--action-pose-mode",
        choices=["absolute", "delta"],
        default="delta",
        help="Action pose representation: 'absolute' uses next-frame EE pose, 'delta' uses relative EE motion from current to next.",
    )
    parser.add_argument(
        "--num-workers",
        type=int,
        default=max(1, (os.cpu_count() or 4) // 2),
        help="Runtime worker budget for async image writing and episode encoding. 0 uses auto tuning.",
    )
    parser.add_argument(
        "--video-codec",
        default="auto",
        choices=["auto", "libsvtav1", "h264", "hevc", "h264_nvenc", "hevc_nvenc"],
        help="Video codec for exported dataset videos. 'auto' prefers h264_nvenc when available, else libsvtav1.",
    )
    parser.add_argument(
        "--video-crf",
        type=int,
        default=23,
        help="Quality setting for video encoding. For NVENC this is used as CQ.",
    )
    args = parser.parse_args()

    input_root = Path(args.input_root)
    if not input_root.is_dir():
        raise FileNotFoundError(f"Input root {input_root} does not exist or is not a directory")

    trajectory_entries = collect_trajectory_dirs(input_root)

    dataset_path = os.path.abspath(args.output_dir)
    dataset_root = Path(dataset_path)

    # Define EE feature names and pad to PI05_MAX_DIM.
    if len(EE_BASE_STATE_NAMES) > PI05_MAX_DIM:
        raise ValueError(
            f"EE state has {len(EE_BASE_STATE_NAMES)} dims, which exceeds PI0.5 max dim {PI05_MAX_DIM}. "
            "Reduce exported dims or increase PI05_MAX_DIM / pi05 config max_state_dim."
        )
    state_names = list(EE_BASE_STATE_NAMES) + [
        f"pad_{i}" for i in range(PI05_MAX_DIM - len(EE_BASE_STATE_NAMES))
    ]
    delta_base_action_names = [
        "left_dpos_x",
        "left_dpos_y",
        "left_dpos_z",
        "left_dquat_x",
        "left_dquat_y",
        "left_dquat_z",
        "left_dquat_w",
        "left_gripper",
        "right_dpos_x",
        "right_dpos_y",
        "right_dpos_z",
        "right_dquat_x",
        "right_dquat_y",
        "right_dquat_z",
        "right_dquat_w",
        "right_gripper",
    ]
    action_names_base = (
        delta_base_action_names
        if args.action_pose_mode == "delta"
        else list(EE_BASE_STATE_NAMES)
    )
    action_names = list(action_names_base) + [
        f"pad_{i}" for i in range(PI05_MAX_DIM - len(action_names_base))
    ]
    image_size = None if args.image_size == 0 else args.image_size
    runtime_tuning = _auto_tune_runtime(requested_workers=args.num_workers)
    image_writer_threads = runtime_tuning["image_writer_threads"]
    image_writer_processes = runtime_tuning["image_writer_processes"]
    parallel_encoding = runtime_tuning["parallel_encoding"]
    resolved_video_codec = _resolve_video_codec(args.video_codec)
    dataset_vcodec = _dataset_vcodec_for_codec(resolved_video_codec)

    if dataset_root.exists() and _is_valid_lerobot_dataset_root(dataset_root):
        print(f"Dataset already exists at {dataset_path}. Appending new episodes.")
        dataset = LeRobotDataset(
            repo_id=dataset_root.name,
            root=dataset_path,
            download_videos=False,
            vcodec=dataset_vcodec,
        )
        if image_writer_processes or image_writer_threads:
            dataset.start_image_writer(
                num_processes=image_writer_processes,
                num_threads=image_writer_threads,
            )
    else:
        if dataset_root.exists():
            backup_root = _move_incomplete_dataset_root(dataset_root)
            print(
                f"Found incomplete dataset at {dataset_path}. "
                f"Moved it to {backup_root} and creating a new dataset."
            )
        video_shape = infer_video_shape(
            trajectory_entries[0][0],
            image_size=image_size,
            center_crop_ratio_w=args.center_crop_ratio_w,
            center_crop_ratio_h=args.center_crop_ratio_h,
            center_crop_offset_x=args.center_crop_offset_x,
            center_crop_offset_y=args.center_crop_offset_y,
            pad_to_aspect_ratio=args.pad_to_aspect_ratio,
        )
        dataset = LeRobotDataset.create(
            repo_id=dataset_root.name,
            root=dataset_path,
            fps=FPS,
            features={
                "observation.state": {
                    "dtype": "float32",
                    "shape": (PI05_MAX_DIM,),
                    "names": state_names,
                },
                "action": {
                    "dtype": "float32",
                    "shape": (PI05_MAX_DIM,),
                    "names": action_names,
                },
                "observation.intervention": {
                    "dtype": "int64",
                    "shape": (1,),
                },
                "observation.images.base_0_rgb": {
                    "dtype": "video",
                    "shape": video_shape,
                    "names": ["channels", "height", "width"],
                },
                "observation.images.left_wrist_0_rgb": {
                    "dtype": "video",
                    "shape": video_shape,
                    "names": ["channels", "height", "width"],
                },
                "observation.images.right_wrist_0_rgb": {
                    "dtype": "video",
                    "shape": video_shape,
                    "names": ["channels", "height", "width"],
                },
            },
            robot_type="offline_robot",
            use_videos=True,
            image_writer_processes=image_writer_processes,
            image_writer_threads=image_writer_threads,
            vcodec=dataset_vcodec,
        )

    _configure_dataset_runtime(
        dataset,
        image_size=image_size,
        center_crop_ratio_w=args.center_crop_ratio_w,
        center_crop_ratio_h=args.center_crop_ratio_h,
        center_crop_offset_x=args.center_crop_offset_x,
        center_crop_offset_y=args.center_crop_offset_y,
        pad_to_aspect_ratio=args.pad_to_aspect_ratio,
        parallel_encoding=parallel_encoding,
        video_codec=resolved_video_codec,
        video_crf=args.video_crf,
    )
    _patch_dataset_compat(dataset)

    total_frames = 0
    total_dropped_bad_image_frames = 0
    total_trajs = len(trajectory_entries)
    start_time = time.time()
    print(f"Action pose mode: {args.action_pose_mode}")
    print(
        f"[INFO] Collected {total_trajs} episode(s) from {input_root.resolve()}."
    )
    print(
        "[INFO] Runtime tuning: "
        f"cpu={runtime_tuning['cpu_count']}, "
        f"worker_budget={runtime_tuning['worker_budget']}, "
        f"image_writer_threads={image_writer_threads}, "
        f"image_writer_processes={image_writer_processes}, "
        f"parallel_encoding={parallel_encoding}, "
        f"video_codec={resolved_video_codec}"
    )
    for idx, (traj_dir, success, quality) in enumerate(trajectory_entries, start=1):
        print(
            f"[{idx}/{total_trajs}] Converting {traj_dir.name} "
            f"(success={success}, quality={quality}) ..."
        )
        episode_stats = convert_trajectory(
            traj_dir,
            dataset,
            tol=args.tol,
            export_states=args.export_states,
            state_names=state_names,
            success=success,
            quality=quality,
            remove_pauses=args.remove_pauses,
            min_state_delta=args.min_state_delta,
            min_grip_delta=args.min_grip_delta,
            min_dt=args.min_dt,
            keep_after_grip_sec=args.keep_after_grip_sec,
            action_pose_mode=args.action_pose_mode,
        )
        frames = int(episode_stats["frames_written"])
        dropped_bad_images = int(episode_stats["frames_skipped"])
        total_frames += frames
        total_dropped_bad_image_frames += dropped_bad_images
        elapsed = time.time() - start_time
        progress = idx / total_trajs if total_trajs > 0 else 1.0
        eta_sec = (elapsed / idx) * (total_trajs - idx) if idx > 0 else 0.0
        print(
            f"  ✓ Saved {frames} frames from {traj_dir.name} | "
            f"Dropped bad-image frames: {dropped_bad_images} | "
            f"Progress: {idx}/{total_trajs} ({progress * 100:.1f}%) | "
            f"Total frames: {total_frames} | "
            f"Elapsed: {elapsed:.1f}s | ETA: {eta_sec:.1f}s"
        )

    if _remove_empty_images_dir(dataset_root):
        print(f"Removed empty images directory from {dataset_root}")

    finalize_fn = getattr(dataset, "finalize", None)
    if callable(finalize_fn):
        finalize_fn()

    print(
        f"Finished converting {len(trajectory_entries)} trajectories. "
        f"Valid frames: {total_frames}. "
        f"Dropped bad-image frames: {total_dropped_bad_image_frames}."
    )


if __name__ == "__main__":
    main()
