# ros2_data_collection

全套数采 workflow 说明见：

- `docs/project_structure_and_python_files.md` — **项目分级与各 py 文件说明**
- `docs/data_collection_code_flow_and_refactor_plan.md`
- `docs/refactor_project_structure_and_mcap_recording.md`

## 当前入口

本仓库现在保留两条录制路径：

| 路径 | 入口 | 说明 |
| --- | --- | --- |
| MCAP 新路径 | `data_collection_recorder/mcap_recorder` | 推荐方向：`rosbag2_py` 直接写 MCAP |
| legacy 路径 | `multi_subscriber/multi_topic_subscriber` | 旧版 PNG + CSV 落盘，作为 fallback 保留 |

## MCAP 录制路径

MCAP recorder 由 `/xr/controller_state` 控制：

| 控制码 | 行为 |
| --- | --- |
| `13` | 开始录制，创建 episode 目录并打开 MCAP writer |
| `14` | 停止录制，关闭 MCAP writer |
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

采完后检查：

```bash
ros2 bag info /home/zihang/ros2_ws/raw_datasets_mcap/<episode_id>/recording
```

## legacy 录制路径

旧版入口仍然保留：

```bash
ros2 run multi_subscriber multi_topic_subscriber
```

它会把三路相机写成 PNG，并把关节、EE、夹爪、intervention 写成 CSV。后续 MCAP 路径验证稳定后，可以逐步下线 legacy 路径。

## `multi_subscriber_sim_real.py`

`multi_subscriber_sim_real.py` 是旧的 sim-real 同采脚本，目前未与 MCAP recorder 对齐。后续建议把 sim 相机 topic 合并进 recording profile，而不是继续维护一套独立落盘逻辑。
