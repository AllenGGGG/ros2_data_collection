#!/usr/bin/env bash
# 关闭所有扫码相关 ROS 节点（含旧版 matrix220_scan_bridge）
pkill -f scan_success_publisher 2>/dev/null || true
pkill -f 'matrix220/driver' 2>/dev/null || true
pkill -f matrix220_tcp_to_ros2_bridge 2>/dev/null || true
pkill -f matrix220_scan_bridge 2>/dev/null || true
pkill -f mock_scanner_ros2 2>/dev/null || true
echo "已尝试关闭扫码节点。验证:"
source /opt/ros/jazzy/setup.bash 2>/dev/null || true
ros2 topic info /scan/success 2>/dev/null | head -3 || true
