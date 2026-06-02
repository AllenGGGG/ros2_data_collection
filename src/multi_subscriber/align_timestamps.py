import pandas as pd
import cv2
from datetime import datetime, timedelta
import os

def align_and_generate_video_from_images(joint_states_csv, image_timestamps_csv, image_folder, video_output_path):
    """
    对齐关节状态时间戳和图像时间戳，并生成对齐后的视频。

    :param joint_states_csv: 关节状态时间戳的 CSV 文件路径
    :param image_timestamps_csv: 图像时间戳的 CSV 文件路径
    :param image_folder: 存储图像文件的文件夹路径
    :param video_output_path: 输出对齐后的视频文件路径
    """
    # 读取关节状态和图像时间戳
    joint_states = pd.read_csv(joint_states_csv)
    image_timestamps = pd.read_csv(image_timestamps_csv)

    # 转换时间戳为 datetime 格式
    joint_states['timestamp'] = pd.to_datetime(joint_states['timestamp'])
    image_timestamps['timestamp'] = pd.to_datetime(image_timestamps['timestamp'])

    # 初始化输出视频
    frame_example = cv2.imread(os.path.join(image_folder, image_timestamps.iloc[0]['image']))
    if frame_example is None:
        raise Exception("Failed to read example image for video initialization")

    frame_height, frame_width, _ = frame_example.shape
    fourcc = cv2.VideoWriter_fourcc(*'XVID')
    fps = 30  # 假设帧率为 30
    video_writer = cv2.VideoWriter(video_output_path, fourcc, fps, (frame_width, frame_height))

    # 对齐逻辑
    aligned_images = []
    last_image_path = None
    for _, joint_row in joint_states.iterrows():
        joint_time = joint_row['timestamp']
        # 找到正负 100ms 内最接近的图像时间戳
        valid_images = image_timestamps[
            (image_timestamps['timestamp'] >= joint_time - timedelta(milliseconds=100)) &
            (image_timestamps['timestamp'] <= joint_time + timedelta(milliseconds=100))
        ]
        if not valid_images.empty:
            # 找到时间差最小的图像
            closest_image = valid_images.iloc[(valid_images['timestamp'] - joint_time).abs().idxmin()]
            image_path = os.path.join(image_folder, closest_image['image'])
            last_image_path = image_path
        else:
            # 如果没有匹配的图像，复用上一帧
            image_path = last_image_path

        if image_path is not None:
            aligned_images.append(image_path)

    # 写入对齐后的视频
    for image_path in aligned_images:
        frame = cv2.imread(image_path)
        if frame is not None:
            video_writer.write(frame)

    # 释放资源
    video_writer.release()
    print(f"Aligned video saved to {video_output_path}")

if __name__ == "__main__":
    # 示例用法
    joint_states_csv = "joint_states.csv"
    image_timestamps_csv = "head_image_timestamps.csv"
    image_folder = "camera_head"  # 存储图像的文件夹
    video_output_path = "aligned_head_camera.avi"

    if not os.path.exists(joint_states_csv) or not os.path.exists(image_timestamps_csv) or not os.path.exists(image_folder):
        print("Error: Required input files or folders are missing.")
    else:
        align_and_generate_video_from_images(joint_states_csv, image_timestamps_csv, image_folder, video_output_path)