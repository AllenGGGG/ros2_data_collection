#!/usr/bin/env bash
set -eo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
export ROS_LOG_DIR="${ROS_LOG_DIR:-${ROOT}/.ros_log}"
mkdir -p "${ROS_LOG_DIR}"
source /opt/ros/jazzy/setup.bash

HOST="${MATRIX220_HOST:-192.168.10.104}"
PORT="${MATRIX220_PORT:-51236}"

python3 "${ROOT}/matrix220/driver.py" --mode client --host "${HOST}" --port "${PORT}" &
DRIVER_PID=$!
python3 "${ROOT}/ros/scan_success_publisher.py" "$@" &
SUCCESS_PID=$!

cleanup() {
  kill "${DRIVER_PID}" "${SUCCESS_PID}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "已启动:"
echo "  matrix220/driver.py            -> /scan/code"
echo "  ros/scan_success_publisher.py  -> /scan/success @ 30Hz"
echo "状态: 0 --[扫码]--> 1 --[/ros2recordstop]--> 0"
wait "${SUCCESS_PID}"
