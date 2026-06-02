#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import rclpy
import cv2
import numpy as np
import pandas as pd
from rclpy.node import Node
from sensor_msgs.msg import Image, JointState
from geometry_msgs.msg import Pose, PoseStamped
from cv_bridge import CvBridge
from datetime import datetime
import os


class MultiTopicSubscriberSimReal(Node):
    """Subscribe to real and Isaac sim feeds and log images and robot states."""

    def __init__(self):
        super().__init__('multi_topic_subscriber_sim_real')

        self.bridge = CvBridge()
        self.timestamp = self.get_time_string()

        self.base_folder = f"/home/zihang/ros2_ws/{self.timestamp}"
        os.makedirs(self.base_folder, exist_ok=True)

        # Real camera folders
        self.image_folder_head = os.path.join(self.base_folder, "camera_head")
        self.image_folder_left = os.path.join(self.base_folder, "camera_left_wrist")
        self.image_folder_right = os.path.join(self.base_folder, "camera_right_wrist")
        # Isaac camera folders
        self.image_folder_isaac_head = os.path.join(self.base_folder, "isaac_head_camera")
        self.image_folder_isaac_left = os.path.join(self.base_folder, "isaac_left_wrist_camera")
        self.image_folder_isaac_right = os.path.join(self.base_folder, "isaac_right_wrist_camera")
        for folder in [
            self.image_folder_head,
            self.image_folder_left,
            self.image_folder_right,
            self.image_folder_isaac_head,
            self.image_folder_isaac_left,
            self.image_folder_isaac_right,
        ]:
            os.makedirs(folder, exist_ok=True)

        self.joint_states_data = pd.DataFrame(columns=[
            'timestamp', 'left_gripper_joint', 'left_joint1', 'left_joint2', 'left_joint3', 'left_joint4',
            'left_joint5', 'left_joint6', 'left_joint7', 'right_gripper_joint', 'right_joint1', 'right_joint2',
            'right_joint3', 'right_joint4', 'right_joint5', 'right_joint6', 'right_joint7'
        ])

        self.left_ee_data = pd.DataFrame(columns=[
            'timestamp', 'left_pose_x', 'left_pose_y', 'left_pose_z',
            'left_orient_x', 'left_orient_y', 'left_orient_z', 'left_orient_w'
        ])
        self.right_ee_data = pd.DataFrame(columns=[
            'timestamp', 'right_pose_x', 'right_pose_y', 'right_pose_z',
            'right_orient_x', 'right_orient_y', 'right_orient_z', 'right_orient_w'
        ])

        self.image_timestamps = {
            "head": [],
            "left_wrist": [],
            "right_wrist": [],
            "isaac_head": [],
            "isaac_left_wrist": [],
            "isaac_right_wrist": [],
        }

        self.head_image_counter = 0
        self.left_wrist_image_counter = 0
        self.right_wrist_image_counter = 0
        self.isaac_head_image_counter = 0
        self.isaac_left_wrist_image_counter = 0
        self.isaac_right_wrist_image_counter = 0

        self.latest_left_pose = None
        self.latest_right_pose = None
        self.last_ee_save_time = datetime.now()

        self.preview_window_titles = {
            "head": "Head Camera",
            "left_wrist": "Left Wrist Camera",
            "right_wrist": "Right Wrist Camera",
            "isaac_head": "Isaac Head Camera",
            "isaac_left_wrist": "Isaac Left Wrist Camera",
            "isaac_right_wrist": "Isaac Right Wrist Camera",
        }
        self.preview_window_positions = {
            "head": (40, 40),
            "left_wrist": (560, 40),
            "right_wrist": (1080, 40),
            "isaac_head": (40, 520),
            "isaac_left_wrist": (560, 520),
            "isaac_right_wrist": (1080, 520),
        }
        self.preview_max_width = 480
        self.enable_preview = True
        self.preview_windows_initialized = set()

        # Real camera subscriptions
        self.camera_head_subscription = self.create_subscription(
            Image,
            '/camera_head/color/image_raw',
            self.camera_head_callback,
            10,
        )
        self.camera_left_wrist_subscription = self.create_subscription(
            Image,
            '/camera_left_wrist/color/image_raw',
            self.camera_left_wrist_callback,
            10,
        )
        self.camera_right_wrist_subscription = self.create_subscription(
            Image,
            '/camera_right_wrist/color/image_raw',
            self.camera_right_wrist_callback,
            10,
        )

        # Isaac camera subscriptions
        self.isaac_head_subscription = self.create_subscription(
            Image,
            '/head_camera/rgb',
            self.isaac_head_camera_callback,
            10,
        )
        self.isaac_left_subscription = self.create_subscription(
            Image,
            '/left_wrist_camera/rgb',
            self.isaac_left_camera_callback,
            10,
        )
        self.isaac_right_subscription = self.create_subscription(
            Image,
            '/right_wrist_camera/rgb',
            self.isaac_right_camera_callback,
            10,
        )

        self.joint_states_subscription = self.create_subscription(
            JointState,
            '/joint_states',
            self.joint_states_callback,
            10,
        )

        # 同一 topic 不可混用不同消息类型，保留 PoseStamped 订阅
        self.left_current_pose_subscription = self.create_subscription(
            PoseStamped,
            '/left_current_pose',
            self.left_current_pose_callback,
            10,
        )

        self.right_current_pose_subscription = self.create_subscription(
            PoseStamped,
            '/right_current_pose',
            self.right_current_pose_callback,
            10,
        )

    def save_image(self, msg, folder, view):
        try:
            cv_image = self.bridge.imgmsg_to_cv2(msg, desired_encoding='bgr8')
            timestamp = self.get_time_string()

            counter_attr = f"{view}_image_counter"
            image_filename = f"{view}_image{getattr(self, counter_attr)}.png"
            image_path = os.path.join(folder, image_filename)
            cv2.imwrite(image_path, cv_image)

            self.image_timestamps[view].append({
                "image": image_filename,
                "timestamp": timestamp,
            })

            if self.enable_preview:
                self.show_preview(view, cv_image)

            setattr(self, counter_attr, getattr(self, counter_attr) + 1)
            self.get_logger().info(f"Saved {image_filename} at {folder}")
        except Exception as exc:
            self.get_logger().error(f"Failed to save image for {view}: {exc}")

    def show_preview(self, view, cv_image):
        try:
            window_title = self.preview_window_titles.get(view, view)
            preview_image = cv_image
            if cv_image.shape[1] > self.preview_max_width:
                scale = self.preview_max_width / cv_image.shape[1]
                new_size = (int(cv_image.shape[1] * scale), int(cv_image.shape[0] * scale))
                preview_image = cv2.resize(cv_image, new_size)

            if window_title not in self.preview_windows_initialized:
                cv2.namedWindow(window_title, cv2.WINDOW_AUTOSIZE)
                pos = self.preview_window_positions.get(view)
                if pos:
                    cv2.moveWindow(window_title, pos[0], pos[1])
                self.preview_windows_initialized.add(window_title)

            cv2.imshow(window_title, preview_image)
            pos = self.preview_window_positions.get(view)
            if pos:
                cv2.moveWindow(window_title, pos[0], pos[1])
            cv2.waitKey(1)
        except Exception as exc:
            self.enable_preview = False
            self.get_logger().warn(f"Disabled previews due to error: {exc}")

    def save_joint_state(self, msg):
        joint_positions = msg.position
        timestamp = self.get_time_string()

        joint_data = [
            timestamp,
            joint_positions[0], joint_positions[1], joint_positions[2], joint_positions[3], joint_positions[4],
            joint_positions[5], joint_positions[6], joint_positions[7],
            joint_positions[8], joint_positions[9], joint_positions[10], joint_positions[11],
            joint_positions[12], joint_positions[13], joint_positions[14], joint_positions[15],
        ]

        joint_df = pd.DataFrame([joint_data], columns=self.joint_states_data.columns)
        self.joint_states_data = pd.concat([self.joint_states_data, joint_df], ignore_index=True)

        joint_states_path = os.path.join(self.base_folder, "joint_states.csv")
        self.joint_states_data.to_csv(joint_states_path, index=False)
        self.get_logger().info(f"Saved joint states at timestamp {timestamp}")

    def save_left_ee_pose(self, msg):
        timestamp = self.get_time_string()
        pose_msg = msg.pose if hasattr(msg, 'pose') else msg
        self.get_logger().info(
            f"Saving left EE pose: pos=({pose_msg.position.x:.4f}, {pose_msg.position.y:.4f}, {pose_msg.position.z:.4f}), "
            f"orient=({pose_msg.orientation.x:.4f}, {pose_msg.orientation.y:.4f}, {pose_msg.orientation.z:.4f}, {pose_msg.orientation.w:.4f})"
        )
        left_pose_data = [
            timestamp,
            pose_msg.position.x,
            pose_msg.position.y,
            pose_msg.position.z,
            pose_msg.orientation.x,
            pose_msg.orientation.y,
            pose_msg.orientation.z,
            pose_msg.orientation.w,
        ]
        left_ee_df = pd.DataFrame([left_pose_data], columns=self.left_ee_data.columns)
        self.left_ee_data = pd.concat([self.left_ee_data, left_ee_df], ignore_index=True)

        left_ee_data_path = os.path.join(self.base_folder, "left_ee_data.csv")
        self.left_ee_data.to_csv(left_ee_data_path, index=False)
        self.get_logger().info(f"Saved left end effector pose at timestamp {timestamp}")

    def save_right_ee_pose(self, msg):
        timestamp = self.get_time_string()
        pose_msg = msg.pose if hasattr(msg, 'pose') else msg
        self.get_logger().info(
            f"Saving right EE pose: pos=({pose_msg.position.x:.4f}, {pose_msg.position.y:.4f}, {pose_msg.position.z:.4f}), "
            f"orient=({pose_msg.orientation.x:.4f}, {pose_msg.orientation.y:.4f}, {pose_msg.orientation.z:.4f}, {pose_msg.orientation.w:.4f})"
        )
        right_pose_data = [
            timestamp,
            pose_msg.position.x,
            pose_msg.position.y,
            pose_msg.position.z,
            pose_msg.orientation.x,
            pose_msg.orientation.y,
            pose_msg.orientation.z,
            pose_msg.orientation.w,
        ]
        right_ee_df = pd.DataFrame([right_pose_data], columns=self.right_ee_data.columns)
        self.right_ee_data = pd.concat([self.right_ee_data, right_ee_df], ignore_index=True)

        right_ee_data_path = os.path.join(self.base_folder, "right_ee_data.csv")
        self.right_ee_data.to_csv(right_ee_data_path, index=False)
        self.get_logger().info(f"Saved right end effector pose at timestamp {timestamp}")

    def get_time_string(self):
        now = datetime.now()
        return now.strftime("%Y%m%d_%H%M%S_%f")

    def save_image_timestamps(self):
        if self.image_timestamps["head"]:
            head_timestamps_df = pd.DataFrame(self.image_timestamps["head"])
            head_timestamps_path = os.path.join(self.base_folder, "camera_head_timestamps.csv")
            head_timestamps_df.to_csv(head_timestamps_path, index=False)

        if self.image_timestamps["left_wrist"]:
            left_wrist_timestamps_df = pd.DataFrame(self.image_timestamps["left_wrist"])
            left_wrist_timestamps_path = os.path.join(self.base_folder, "camera_left_wrist_timestamps.csv")
            left_wrist_timestamps_df.to_csv(left_wrist_timestamps_path, index=False)

        if self.image_timestamps["right_wrist"]:
            right_wrist_timestamps_df = pd.DataFrame(self.image_timestamps["right_wrist"])
            right_wrist_timestamps_path = os.path.join(self.base_folder, "camera_right_wrist_timestamps.csv")
            right_wrist_timestamps_df.to_csv(right_wrist_timestamps_path, index=False)

        if self.image_timestamps["isaac_head"]:
            isaac_head_df = pd.DataFrame(self.image_timestamps["isaac_head"])
            isaac_head_path = os.path.join(self.base_folder, "isaac_head_camera_timestamps.csv")
            isaac_head_df.to_csv(isaac_head_path, index=False)

        if self.image_timestamps["isaac_left_wrist"]:
            isaac_left_df = pd.DataFrame(self.image_timestamps["isaac_left_wrist"])
            isaac_left_path = os.path.join(self.base_folder, "isaac_left_wrist_camera_timestamps.csv")
            isaac_left_df.to_csv(isaac_left_path, index=False)

        if self.image_timestamps["isaac_right_wrist"]:
            isaac_right_df = pd.DataFrame(self.image_timestamps["isaac_right_wrist"])
            isaac_right_path = os.path.join(self.base_folder, "isaac_right_wrist_camera_timestamps.csv")
            isaac_right_df.to_csv(isaac_right_path, index=False)

        self.get_logger().info("Saved image timestamps")

    def camera_head_callback(self, msg):
        self.save_image(msg, self.image_folder_head, "head")

    def camera_left_wrist_callback(self, msg):
        self.save_image(msg, self.image_folder_left, "left_wrist")

    def camera_right_wrist_callback(self, msg):
        self.save_image(msg, self.image_folder_right, "right_wrist")

    def isaac_head_camera_callback(self, msg):
        self.save_image(msg, self.image_folder_isaac_head, "isaac_head")

    def isaac_left_camera_callback(self, msg):
        self.save_image(msg, self.image_folder_isaac_left, "isaac_left_wrist")

    def isaac_right_camera_callback(self, msg):
        self.save_image(msg, self.image_folder_isaac_right, "isaac_right_wrist")

    def joint_states_callback(self, msg):
        self.save_joint_state(msg)

    def left_current_pose_callback(self, msg):
        self.latest_left_pose = msg
        self.get_logger().info("Received left_current_pose message")
        self.save_left_ee_pose(msg)

    def right_current_pose_callback(self, msg):
        self.latest_right_pose = msg
        self.get_logger().info("Received right_current_pose message")
        self.save_right_ee_pose(msg)


def main(args=None):
    rclpy.init(args=args)
    node = MultiTopicSubscriberSimReal()

    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        node.get_logger().info("Keyboard interrupt received, shutting down...")
    finally:
        node.save_image_timestamps()

        joint_states_path = os.path.join(node.base_folder, "joint_states.csv")
        left_ee_data_path = os.path.join(node.base_folder, "left_ee_data.csv")
        right_ee_data_path = os.path.join(node.base_folder, "right_ee_data.csv")

        if not node.joint_states_data.empty:
            node.joint_states_data.to_csv(joint_states_path, index=False)
            node.get_logger().info(f"Final joint states saved to {joint_states_path}")

        if not node.left_ee_data.empty:
            node.left_ee_data.to_csv(left_ee_data_path, index=False)
            node.get_logger().info(f"Final left end effector data saved to {left_ee_data_path}")

        if not node.right_ee_data.empty:
            node.right_ee_data.to_csv(right_ee_data_path, index=False)
            node.get_logger().info(f"Final right end effector data saved to {right_ee_data_path}")

        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
