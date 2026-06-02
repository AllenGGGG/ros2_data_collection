#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import rclpy
import cv2
import numpy as np
import pandas as pd
from concurrent.futures import ThreadPoolExecutor
from rclpy.node import Node
from rclpy.executors import MultiThreadedExecutor
from rclpy.callback_groups import MutuallyExclusiveCallbackGroup, ReentrantCallbackGroup
from rclpy.qos import QoSProfile
from sensor_msgs.msg import Image, JointState
from geometry_msgs.msg import Pose, PoseStamped
from std_msgs.msg import Int32
from cv_bridge import CvBridge
from datetime import datetime
import os
import shutil
import threading
import uuid

START_RECORDING_CODE = 13
STOP_RECORDING_CODE = 14
INFERENCE_RESUMED_CODE = 15
INFERENCE_PAUSED_CODE = 16

class MultiTopicSubscriber(Node):

    def __init__(self):
        super().__init__('multi_topic_subscriber')

        # 初始化CvBridge
        self.bridge = CvBridge()

        # 录制状态
        self.recording_active = False
        self.recording_lock = threading.Lock()
        self.base_folder = None
        self.image_folder_head = None
        self.image_folder_left = None
        self.image_folder_right = None

        # 录制数据容器初始化
        self.reset_recording_buffers()
        
        # 存储最新的末端执行器位姿
        self.latest_left_pose = None
        self.latest_right_pose = None
        self.latest_left_target = None
        self.latest_right_target = None
        self.last_ee_save_time = datetime.now()

        # 配置图像预览窗口
        self.preview_window_titles = {
            "head": "Head Camera",
            "left_wrist": "Left Wrist Camera",
            "right_wrist": "Right Wrist Camera"
        }
        self.preview_window_positions = {
            "head": (40, 40),
            "left_wrist": (560, 40),
            "right_wrist": (1080, 40)
        }
        self.preview_max_width = 480
        self.declare_parameter('enable_preview', False)
        self.enable_preview = self.get_parameter('enable_preview').value
        self.preview_windows_initialized = set()
        self.preview_thread_warning_logged = False

        # 采集最大时长（以头部相机帧数为准）
        self.max_head_frames = 10000

        # 异步写盘线程池，避免图像保存阻塞回调
        self.image_thread_pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="image-writer")

        # 回调分组与高频 QoS
        self.image_callback_group = ReentrantCallbackGroup()
        self.state_callback_group = MutuallyExclusiveCallbackGroup()
        self.controller_callback_group = MutuallyExclusiveCallbackGroup()
        self.background_callback_group = MutuallyExclusiveCallbackGroup()
        self.high_freq_qos = QoSProfile(depth=200)

        # 订阅多个话题
        self.camera_head_subscription = self.create_subscription(
            Image,
            '/camera_head/color/image_raw',
            self.camera_head_callback,
            10,
            callback_group=self.image_callback_group
        )

        self.camera_left_wrist_subscription = self.create_subscription(
            Image,
            '/camera_left_wrist/color/image_raw',
            self.camera_left_wrist_callback,
            10,
            callback_group=self.image_callback_group
        )

        self.camera_right_wrist_subscription = self.create_subscription(
            Image,
            '/camera_right_wrist/color/image_raw',
            self.camera_right_wrist_callback,
            10,
            callback_group=self.image_callback_group
        )

        self.joint_states_subscription = self.create_subscription(
            JointState,
            '/joint_states',
            self.joint_states_callback,
            self.high_freq_qos,
            callback_group=self.state_callback_group
        )

        # 订阅末端执行器的位姿话题（同一 topic 不可混用不同类型）
        self.left_current_pose_subscription = self.create_subscription(
            PoseStamped,
            '/left_current_pose',
            self.left_current_pose_callback,
            self.high_freq_qos,
            callback_group=self.state_callback_group
        )

        self.right_current_pose_subscription = self.create_subscription(
            PoseStamped,
            '/right_current_pose',
            self.right_current_pose_callback,
            self.high_freq_qos,
            callback_group=self.state_callback_group
        )

        # 订阅末端执行器目标位姿话题
        self.left_current_target_subscription = self.create_subscription(
            PoseStamped,
            '/left_current_target',
            self.left_current_target_callback,
            self.high_freq_qos,
            callback_group=self.state_callback_group
        )

        self.right_current_target_subscription = self.create_subscription(
            PoseStamped,
            '/right_current_target',
            self.right_current_target_callback,
            self.high_freq_qos,
            callback_group=self.state_callback_group
        )

        # 订阅左侧夹爪指令事件
        self.left_gripper_command_subscription = self.create_subscription(
            Int32,
            '/left_gripper_controller/target_command',
            self.left_gripper_command_callback,
            10,
            callback_group=self.state_callback_group
        )

        self.right_gripper_command_subscription = self.create_subscription(
            Int32,
            '/right_gripper_controller/target_command',
            self.right_gripper_command_callback,
            10,
            callback_group=self.state_callback_group
        )

        # 订阅控制器状态：
        # 13/15 开始录制，14/16 停止录制
        self.controller_state_subscription = self.create_subscription(
            Int32,
            '/xr/controller_state',
            self.controller_state_callback,
            10,
            callback_group=self.controller_callback_group
        )

        self.flush_timer = self.create_timer(
            0.5,
            self.flush_data_buffers,
            callback_group=self.background_callback_group
        )

    def reset_recording_buffers(self):
        # 初始化DataFrame来保存关节状态数据
        self.joint_states_data = pd.DataFrame(columns=[
            'timestamp', 'left_gripper_joint', 'left_joint1', 'left_joint2', 'left_joint3', 'left_joint4',
            'left_joint5', 'left_joint6', 'left_joint7', 'right_gripper_joint', 'right_joint1', 'right_joint2',
            'right_joint3', 'right_joint4', 'right_joint5', 'right_joint6', 'right_joint7'
        ])

        # 初始化DataFrame来分别保存左右末端执行器位姿数据
        self.left_ee_data = pd.DataFrame(columns=[
            'timestamp', 'left_pose_x', 'left_pose_y', 'left_pose_z',
            'left_orient_x', 'left_orient_y', 'left_orient_z', 'left_orient_w'
        ])
        self.right_ee_data = pd.DataFrame(columns=[
            'timestamp', 'right_pose_x', 'right_pose_y', 'right_pose_z',
            'right_orient_x', 'right_orient_y', 'right_orient_z', 'right_orient_w'
        ])
        self.left_ee_target_data = pd.DataFrame(columns=[
            'timestamp', 'left_target_x', 'left_target_y', 'left_target_z',
            'left_target_orient_x', 'left_target_orient_y', 'left_target_orient_z', 'left_target_orient_w'
        ])
        self.right_ee_target_data = pd.DataFrame(columns=[
            'timestamp', 'right_target_x', 'right_target_y', 'right_target_z',
            'right_target_orient_x', 'right_target_orient_y', 'right_target_orient_z', 'right_target_orient_w'
        ])

        # 事件型数据：左侧夹爪指令
        self.left_gripper_command_data = pd.DataFrame(columns=[
            'timestamp', 'value'
        ])
        self.right_gripper_command_data = pd.DataFrame(columns=[
            'timestamp', 'value'
        ])

        # 事件型数据：专家干预状态（True=专家干预，False=模型推理）
        self.interventions_data = pd.DataFrame(columns=[
            'timestamp', 'value'
        ])

        # 用于存储每张图像的时间戳
        self.image_timestamps = {
            "head": [],
            "left_wrist": [],
            "right_wrist": []
        }

        # 图像计数器
        self.head_image_counter = 0
        self.left_wrist_image_counter = 0
        self.right_wrist_image_counter = 0

        # 脏标记与计数
        self.joint_states_dirty = False
        self.left_ee_dirty = False
        self.right_ee_dirty = False
        self.left_ee_target_dirty = False
        self.right_ee_target_dirty = False
        self.left_gripper_command_dirty = False
        self.right_gripper_command_dirty = False
        self.joint_samples = 0
        self.left_ee_samples = 0
        self.right_ee_samples = 0
        self.left_ee_target_samples = 0
        self.right_ee_target_samples = 0
        self.left_gripper_command_samples = 0
        self.right_gripper_command_samples = 0
        self.image_samples = {"head": 0, "left_wrist": 0, "right_wrist": 0}

        # 干预状态标记与计数
        self.interventions_active = False
        self.interventions_dirty = False
        self.interventions_samples = 0

    def start_recording(self):
        with self.recording_lock:
            if self.recording_active:
                self.get_logger().info("Recording already active; ignoring start request")
                return

            # 获取当前时间戳，用于创建唯一的文件夹
            timestamp = self.get_time_string()
            self.base_folder = f"/home/zihang/ros2_ws/raw_datasets/{timestamp}"
            os.makedirs(self.base_folder, exist_ok=True)

            # 创建子文件夹来保存图像数据
            self.image_folder_head = os.path.join(self.base_folder, "camera_head")
            self.image_folder_left = os.path.join(self.base_folder, "camera_left_wrist")
            self.image_folder_right = os.path.join(self.base_folder, "camera_right_wrist")
            os.makedirs(self.image_folder_head, exist_ok=True)
            os.makedirs(self.image_folder_left, exist_ok=True)
            os.makedirs(self.image_folder_right, exist_ok=True)

            self.reset_recording_buffers()
            self.recording_active = True
            self.get_logger().info(f"Recording started: {self.base_folder}")

    def stop_recording(self):
        with self.recording_lock:
            if not self.recording_active:
                self.get_logger().info("Recording not active; ignoring stop request")
                return

            self.recording_active = False

        # 保存所有剩余数据
        self.save_image_timestamps()
        self.flush_data_buffers()

        #冗余保存一次，确保所有数据都写入磁盘
        # if self.base_folder:
        #     joint_states_path = os.path.join(self.base_folder, "joint_states.csv")
        #     left_ee_data_path = os.path.join(self.base_folder, "left_ee_data.csv")
        #     right_ee_data_path = os.path.join(self.base_folder, "right_ee_data.csv")
        #     left_gripper_command_path = os.path.join(self.base_folder, "left_gripper_command.csv")
        #     right_gripper_command_path = os.path.join(self.base_folder, "right_gripper_command.csv")
        #     interventions_path = os.path.join(self.base_folder, "interventions.csv")

        #     if not self.joint_states_data.empty:
        #         self.joint_states_data.to_csv(joint_states_path, index=False)
        #         self.get_logger().info(f"Final joint states saved to {joint_states_path}")

        #     if not self.left_ee_data.empty:
        #         self.left_ee_data.to_csv(left_ee_data_path, index=False)
        #         self.get_logger().info(f"Final left end effector data saved to {left_ee_data_path}")

        #     if not self.right_ee_data.empty:
        #         self.right_ee_data.to_csv(right_ee_data_path, index=False)
        #         self.get_logger().info(f"Final right end effector data saved to {right_ee_data_path}")

        #     if not self.left_gripper_command_data.empty:
        #         self.left_gripper_command_data.to_csv(left_gripper_command_path, index=False)
        #         self.get_logger().info(
        #             f"Final left gripper command data saved to {left_gripper_command_path}"
        #         )

        #     if not self.right_gripper_command_data.empty:
        #         self.right_gripper_command_data.to_csv(right_gripper_command_path, index=False)
        #         self.get_logger().info(
        #             f"Final right gripper command data saved to {right_gripper_command_path}"
        #         )

        #     if not self.interventions_data.empty:
        #         self.interventions_data.to_csv(interventions_path, index=False)
        #         self.get_logger().info(
        #             f"Final interventions data saved to {interventions_path}"
        #         )

        self.get_logger().info("Recording stopped")

    def abort_recording(self, reason: str):
        with self.recording_lock:
            if not self.recording_active:
                return
            self.recording_active = False

        if self.base_folder and os.path.exists(self.base_folder):
            try:
                shutil.rmtree(self.base_folder)
            except Exception as exc:
                self.get_logger().warn(f"Failed to remove aborted recording folder: {exc}")

        self.reset_recording_buffers()
        self.base_folder = None
        self.image_folder_head = None
        self.image_folder_left = None
        self.image_folder_right = None
        self.get_logger().warn(f"Recording aborted: {reason}")

    def save_image(self, msg, folder, view):
        if not self.recording_active:
            return
        try:
            # 将ROS图像消息转换为OpenCV图像
            cv_image = self.bridge.imgmsg_to_cv2(msg, desired_encoding='bgr8')
            # 获取当前时间戳
            timestamp = self.get_time_string()

            if view == "head" and getattr(self, "head_image_counter", 0) >= self.max_head_frames:
                self.get_logger().info(
                    f"Max head frames reached ({self.max_head_frames}); stopping recording"
                )
                self.stop_recording()
                return
            
            # 保存图像
            image_filename = f"{view}_image{getattr(self, f'{view}_image_counter')}.png"
            image_path = os.path.join(folder, image_filename)
            image_copy = cv_image.copy()
            self.image_thread_pool.submit(self.write_image_async, image_path, image_copy)
            
            # 记录时间戳
            self.image_timestamps[view].append({
                "image": image_filename,
                "timestamp": timestamp
            })

            # 实时显示图像，用于监控视角情况
            if self.enable_preview:
                self.show_preview(view, cv_image)

            # 更新图像计数器
            setattr(self, f"{view}_image_counter", getattr(self, f"{view}_image_counter") + 1)
            self.image_samples[view] += 1
            if self.image_samples[view] % 60 == 0:
                self.get_logger().info(f"Captured {self.image_samples[view]} {view} frames")
        except Exception as e:
            self.get_logger().error(f"Failed to save image: {e}")

    def write_image_async(self, path, image):
        tmp_path = f"{path}.{uuid.uuid4().hex}.tmp"
        try:
            success, encoded = cv2.imencode('.png', image)
            if not success:
                raise RuntimeError("cv2.imencode returned False")
            with open(tmp_path, 'wb') as tmp_file:
                tmp_file.write(encoded.tobytes())
            os.replace(tmp_path, path)
        except Exception as exc:
            self.get_logger().error(f"Async image save failed for {path}: {exc}")
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass

    def show_preview(self, view, cv_image):
        if not self.enable_preview:
            return

        if threading.current_thread() is not threading.main_thread():
            if not self.preview_thread_warning_logged:
                self.get_logger().warn("Preview disabled because OpenCV windows must run in the main thread")
                self.preview_thread_warning_logged = True
            return

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
        if not self.recording_active:
            return
        # 提取位置数据（只关心位置）
        joint_positions = msg.position
        # 获取关节数据的时间戳
        timestamp = self.get_time_string()

        # 按照给定顺序整理关节位置数据
        joint_data = [
            timestamp,
            joint_positions[0], joint_positions[1], joint_positions[2], joint_positions[3], joint_positions[4],
            joint_positions[5], joint_positions[6], joint_positions[7],
            joint_positions[8], joint_positions[9], joint_positions[10], joint_positions[11],
            joint_positions[12], joint_positions[13], joint_positions[14], joint_positions[15]
        ]

        # 将 joint_data 转换为 DataFrame
        joint_df = pd.DataFrame([joint_data], columns=self.joint_states_data.columns)

        # 使用 pd.concat() 合并数据
        if self.joint_states_data.empty:
            self.joint_states_data = joint_df
        else:
            self.joint_states_data = pd.concat([self.joint_states_data, joint_df], ignore_index=True)
        self.joint_states_dirty = True
        self.joint_samples += 1
        if self.joint_samples % 200 == 0:
            self.get_logger().info(f"Buffered {self.joint_samples} joint states")

    def save_left_ee_pose(self, msg):
        """保存左侧末端执行器位姿到CSV文件"""
        if not self.recording_active:
            return
        # 从msg中提取位置和姿态信息
        timestamp = self.get_time_string()  # 获取当前时间戳
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
            pose_msg.orientation.w
        ]
        # 将数据添加到DataFrame中
        left_ee_df = pd.DataFrame([left_pose_data], columns=self.left_ee_data.columns)
        if self.left_ee_data.empty:
            self.left_ee_data = left_ee_df
        else:
            self.left_ee_data = pd.concat([self.left_ee_data, left_ee_df], ignore_index=True)
        self.left_ee_dirty = True
        self.left_ee_samples += 1
        if self.left_ee_samples % 100 == 0:
            self.get_logger().info(f"Buffered {self.left_ee_samples} left EE poses")

    def save_right_ee_pose(self, msg):
        """保存右侧末端执行器位姿到CSV文件"""
        if not self.recording_active:
            return
        # 从msg中提取位置和姿态信息
        timestamp = self.get_time_string()  # 获取当前时间戳
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
            pose_msg.orientation.w
        ]
        # 将数据添加到DataFrame中
        right_ee_df = pd.DataFrame([right_pose_data], columns=self.right_ee_data.columns)
        if self.right_ee_data.empty:
            self.right_ee_data = right_ee_df
        else:
            self.right_ee_data = pd.concat([self.right_ee_data, right_ee_df], ignore_index=True)
        self.right_ee_dirty = True
        self.right_ee_samples += 1
        if self.right_ee_samples % 100 == 0:
            self.get_logger().info(f"Buffered {self.right_ee_samples} right EE poses")

    def save_left_ee_target(self, msg):
        """保存左侧末端执行器目标位姿到CSV文件"""
        if not self.recording_active:
            return
        timestamp = self.get_time_string()
        pose_msg = msg.pose if hasattr(msg, 'pose') else msg
        left_target_data = [
            timestamp,
            pose_msg.position.x,
            pose_msg.position.y,
            pose_msg.position.z,
            pose_msg.orientation.x,
            pose_msg.orientation.y,
            pose_msg.orientation.z,
            pose_msg.orientation.w
        ]
        left_target_df = pd.DataFrame([left_target_data], columns=self.left_ee_target_data.columns)
        if self.left_ee_target_data.empty:
            self.left_ee_target_data = left_target_df
        else:
            self.left_ee_target_data = pd.concat([self.left_ee_target_data, left_target_df], ignore_index=True)
        self.left_ee_target_dirty = True
        self.left_ee_target_samples += 1
        if self.left_ee_target_samples % 100 == 0:
            self.get_logger().info(f"Buffered {self.left_ee_target_samples} left EE targets")

    def save_right_ee_target(self, msg):
        """保存右侧末端执行器目标位姿到CSV文件"""
        if not self.recording_active:
            return
        timestamp = self.get_time_string()
        pose_msg = msg.pose if hasattr(msg, 'pose') else msg
        right_target_data = [
            timestamp,
            pose_msg.position.x,
            pose_msg.position.y,
            pose_msg.position.z,
            pose_msg.orientation.x,
            pose_msg.orientation.y,
            pose_msg.orientation.z,
            pose_msg.orientation.w
        ]
        right_target_df = pd.DataFrame([right_target_data], columns=self.right_ee_target_data.columns)
        if self.right_ee_target_data.empty:
            self.right_ee_target_data = right_target_df
        else:
            self.right_ee_target_data = pd.concat([self.right_ee_target_data, right_target_df], ignore_index=True)
        self.right_ee_target_dirty = True
        self.right_ee_target_samples += 1
        if self.right_ee_target_samples % 100 == 0:
            self.get_logger().info(f"Buffered {self.right_ee_target_samples} right EE targets")

    def save_left_gripper_command(self, msg: Int32):
        if not self.recording_active:
            return
        timestamp = self.get_time_string()
        command_data = [timestamp, msg.data]
        command_df = pd.DataFrame([command_data], columns=self.left_gripper_command_data.columns)
        if self.left_gripper_command_data.empty:
            self.left_gripper_command_data = command_df
        else:
            self.left_gripper_command_data = pd.concat(
                [self.left_gripper_command_data, command_df],
                ignore_index=True
            )
        self.left_gripper_command_dirty = True
        self.left_gripper_command_samples += 1
        self.get_logger().info(
            f"Buffered {self.left_gripper_command_samples} left gripper commands"
        )

    def save_right_gripper_command(self, msg: Int32):
        if not self.recording_active:
            return
        timestamp = self.get_time_string()
        command_data = [timestamp, msg.data]
        command_df = pd.DataFrame([command_data], columns=self.right_gripper_command_data.columns)
        if self.right_gripper_command_data.empty:
            self.right_gripper_command_data = command_df
        else:
            self.right_gripper_command_data = pd.concat(
                [self.right_gripper_command_data, command_df],
                ignore_index=True
            )
        self.right_gripper_command_dirty = True
        self.right_gripper_command_samples += 1
        self.get_logger().info(
            f"Buffered {self.right_gripper_command_samples} right gripper commands"
        )

    def save_interventions_state(self, active: bool):
        if not self.recording_active:
            return
        timestamp = self.get_time_string()
        data = [timestamp, active]
        df = pd.DataFrame([data], columns=self.interventions_data.columns)
        if self.interventions_data.empty:
            self.interventions_data = df
        else:
            self.interventions_data = pd.concat(
                [self.interventions_data, df],
                ignore_index=True
            )
        self.interventions_dirty = True
        self.interventions_samples += 1
        self.get_logger().info(
            f"Buffered {self.interventions_samples} intervention states"
        )


    def flush_data_buffers(self):
        try:
            if not self.base_folder:
                return
            if self.joint_states_dirty and not self.joint_states_data.empty:
                joint_states_path = os.path.join(self.base_folder, "joint_states.csv")
                self.joint_states_data.to_csv(joint_states_path, index=False)
                self.joint_states_dirty = False

            if self.left_ee_dirty and not self.left_ee_data.empty:
                left_ee_data_path = os.path.join(self.base_folder, "left_ee_data.csv")
                self.left_ee_data.to_csv(left_ee_data_path, index=False)
                self.left_ee_dirty = False

            if self.right_ee_dirty and not self.right_ee_data.empty:
                right_ee_data_path = os.path.join(self.base_folder, "right_ee_data.csv")
                self.right_ee_data.to_csv(right_ee_data_path, index=False)
                self.right_ee_dirty = False

            if self.left_ee_target_dirty and not self.left_ee_target_data.empty:
                left_ee_target_data_path = os.path.join(self.base_folder, "left_ee_target_data.csv")
                self.left_ee_target_data.to_csv(left_ee_target_data_path, index=False)
                self.left_ee_target_dirty = False

            if self.right_ee_target_dirty and not self.right_ee_target_data.empty:
                right_ee_target_data_path = os.path.join(self.base_folder, "right_ee_target_data.csv")
                self.right_ee_target_data.to_csv(right_ee_target_data_path, index=False)
                self.right_ee_target_dirty = False

            if self.left_gripper_command_dirty and not self.left_gripper_command_data.empty:
                left_gripper_command_path = os.path.join(
                    self.base_folder, "left_gripper_command.csv"
                )
                self.left_gripper_command_data.to_csv(left_gripper_command_path, index=False)
                self.left_gripper_command_dirty = False

            if self.right_gripper_command_dirty and not self.right_gripper_command_data.empty:
                right_gripper_command_path = os.path.join(
                    self.base_folder, "right_gripper_command.csv"
                )
                self.right_gripper_command_data.to_csv(right_gripper_command_path, index=False)
                self.right_gripper_command_dirty = False

            if self.interventions_dirty and not self.interventions_data.empty:
                interventions_path = os.path.join(self.base_folder, "interventions.csv")
                self.interventions_data.to_csv(interventions_path, index=False)
                self.interventions_dirty = False
        except Exception as exc:
            self.get_logger().error(f"Failed to flush data buffers: {exc}")


    def get_time_string(self):
        # 获取当前时间戳
        now = datetime.now()
        return now.strftime("%Y%m%d_%H%M%S_%f")

    def save_image_timestamps(self):
        """保存图像时间戳到CSV文件"""
        if not self.base_folder:
            return
        # 保存每个视角图像和其对应时间戳的 CSV 文件
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
            
        self.get_logger().info("Saved image timestamps")

    def camera_head_callback(self, msg):
        self.save_image(msg, self.image_folder_head, "head")

    def camera_left_wrist_callback(self, msg):
        self.save_image(msg, self.image_folder_left, "left_wrist")

    def camera_right_wrist_callback(self, msg):
        self.save_image(msg, self.image_folder_right, "right_wrist")

    def joint_states_callback(self, msg):
        self.save_joint_state(msg)

    def left_current_pose_callback(self, msg):
        self.latest_left_pose = msg
        if not self.recording_active:
            return
        self.get_logger().info("Received left_current_pose message")
        self.save_left_ee_pose(msg)

    def right_current_pose_callback(self, msg):
        self.latest_right_pose = msg
        if not self.recording_active:
            return
        self.get_logger().info("Received right_current_pose message")
        self.save_right_ee_pose(msg)

    def left_current_target_callback(self, msg):
        self.latest_left_target = msg
        if not self.recording_active:
            return
        self.save_left_ee_target(msg)

    def right_current_target_callback(self, msg):
        self.latest_right_target = msg
        if not self.recording_active:
            return
        self.save_right_ee_target(msg)

    def left_gripper_command_callback(self, msg: Int32):
        self.save_left_gripper_command(msg)

    def right_gripper_command_callback(self, msg: Int32):
        self.save_right_gripper_command(msg)

    def controller_state_callback(self, msg: Int32):
        if msg.data == START_RECORDING_CODE:
            self.start_recording()
        elif msg.data == STOP_RECORDING_CODE:
            self.stop_recording()
        elif msg.data == INFERENCE_RESUMED_CODE:
            if self.recording_active:
                self.interventions_active = False
                self.save_interventions_state(False)
        elif msg.data == INFERENCE_PAUSED_CODE:
            if self.recording_active:
                self.interventions_active = True
                self.save_interventions_state(True)

def main(args=None):
    rclpy.init(args=args)
    node = MultiTopicSubscriber()
    executor = MultiThreadedExecutor(num_threads=4)
    executor.add_node(node)

    try:
        node.get_logger().info(
            "Node initialized; recording start/stop is controlled only by "
            f"/xr/controller_state={START_RECORDING_CODE}/{STOP_RECORDING_CODE}; "
            f"{INFERENCE_RESUMED_CODE}/{INFERENCE_PAUSED_CODE} only annotate inference/intervention while recording."
        )
        executor.spin()
    except KeyboardInterrupt:
        node.get_logger().info("Keyboard interrupt received, shutting down...")
    finally:
        executor.shutdown()
        executor.remove_node(node)
        if node.recording_active:
            node.stop_recording()
        node.image_thread_pool.shutdown(wait=True)
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()