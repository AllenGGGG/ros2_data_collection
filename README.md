# ros2_data_collection

全套数采 workflow 说明见：

- `docs/project_structure_and_python_files.md` — **项目分级与各 py 文件说明**
- `docs/data_collection_code_flow_and_refactor_plan.md`
- `docs/refactor_project_structure_and_mcap_recording.md`

## 当前入口

本仓库现在保留两条录制路径：

| 路径 | 入口 | 说明 |
| --- | --- | --- |
| MCAP 新路径 | `data_collection_recorder/mcap_recorder` | 控制节点启动原生 `ros2 bag record -s mcap` 全频录制 |
| legacy 路径 | `multi_subscriber/multi_topic_subscriber` | 旧版 PNG + CSV 落盘，作为 fallback 保留 |

## MCAP 录制路径

MCAP recorder 由 `/xr/controller_state` 控制：

| 控制码 | 行为 |
| --- | --- |
| `13` | 开始录制，创建 episode 目录并启动 `ros2 bag record` |
| `14` | 停止录制，向 `ros2 bag record` 发送 `SIGINT` 并等待写完 metadata |
| `15` | 记录 `intervention_end`，录制不中断 |
| `16` | 记录 `intervention_start`，录制不中断 |

默认录制话题见：

```text
src/data_collection_recorder/config/recording/default_profile.yaml
```

默认输出目录：

```text
~/ros2_ws/raw_datasets_mcap/<episode_id>/
├── metadata.json
└── recording/
```

输出目录默认来自 profile YAML 顶层字段：

```yaml
output_dir: ~/ros2_ws/raw_datasets_mcap
```

## 运行 MCAP recorder

先确认安装了 MCAP storage 插件：

```bash
ros2 pkg list | grep rosbag2_storage_mcap
```

构建并启动：

```bash
cd ~/workspace/ros2_data_collection
colcon build
source install/setup.bash
ros2 launch data_collection_recorder mcap_recorder.launch.py
```

自定义输出目录：

```bash
ros2 launch data_collection_recorder mcap_recorder.launch.py \
  output_dir:=/home/zihang/ros2_ws/raw_datasets_mcap
```

路径优先级为：`launch output_dir` > `profile YAML output_dir` > 内置默认值。

等价的原生命令形式为：

```bash
ros2 bag record \
  -s mcap \
  --storage-preset-profile zstd_small \
  /camera_head/color/image_raw/compressed \
  /camera_left_wrist/color/image_raw/compressed \
  /camera_right_wrist/color/image_raw/compressed \
  /joint_states \
  /left_current_pose \
  /right_current_pose \
  /left_current_target \
  /right_current_target \
  /left_gripper_controller/target_command \
  /right_gripper_controller/target_command \
  /xr/controller_state \
  -o ~/ros2_bags/recording_$(date +%Y%m%d_%H%M%S)
```

采完后检查：

```bash
ros2 bag info /home/zihang/ros2_ws/raw_datasets_mcap/<episode_id>/recording
```

### 录制说明（重要）

- 当前默认 profile 使用三路 `sensor_msgs/msg/CompressedImage`，不再由 Python 回调转写每条消息。
- `record_max_hz` / `state_record_max_hz` 不再生效；原生 `ros2 bag record` 会按 topic 实际发布频率全频录制。
- 要明显减小体积，优先录相机的 `/compressed` 话题，并保持 MCAP preset 为 `zstd_small`：

```bash
ros2 launch data_collection_recorder mcap_recorder.launch.py \
  profile_path:=$(ros2 pkg prefix data_collection_recorder)/share/data_collection_recorder/config/recording/default_profile.yaml \
  storage_preset_profile:=zstd_small
```

- Launch 里 **`storage_preset_profile:=none` 会几乎不压缩**，勿用。

## legacy 录制路径

旧版入口仍然保留：

```bash
ros2 run multi_subscriber multi_topic_subscriber
```

它会把三路相机写成 PNG，并把关节、EE、夹爪、intervention 写成 CSV。后续 MCAP 路径验证稳定后，可以逐步下线 legacy 路径。

Yerba 相机对比实验可切换为订阅 `CompressedImage`：

```bash
ros2 run multi_subscriber multi_topic_subscriber --ros-args -p use_compressed_images:=true
```

启用后会改为订阅：

- `/camera_head/color/image_raw/compressed`
- `/camera_left_wrist/color/image_raw/compressed`
- `/camera_right_wrist/color/image_raw/compressed`

## `multi_subscriber_sim_real.py`

`multi_subscriber_sim_real.py` 是旧的 sim-real 同采脚本，目前未与 MCAP recorder 对齐。后续建议把 sim 相机 topic 合并进 recording profile，而不是继续维护一套独立落盘逻辑。
