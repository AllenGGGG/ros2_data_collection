# ros2_data_collection

全套数采 workflow 说明见：

- `docs/project_structure_and_python_files.md` — **项目分级与各 py 文件说明**
- `docs/data_collection_code_flow_and_refactor_plan.md`
- `docs/refactor_project_structure_and_mcap_recording.md`

## 当前入口

本仓库现在保留两条录制路径，并附带上传模块：

| 模块 | 入口 | 是否 `colcon build` | 说明 |
| --- | --- | --- | --- |
| MCAP 新路径 | `data_collection_recorder/mcap_recorder` | ✅ 需要 | 控制节点启动原生 `ros2 bag record -s mcap` 全频录制 |
| 边采边传 | `upload.yaml` + 后台 `rsync` | ✅ 随 core/recorder 一起 build | 本地落盘完成后异步上传整段 episode |
| legacy 路径 | `multi_subscriber/multi_topic_subscriber` | ✅ 需要 | 旧版 PNG + CSV 落盘，作为 fallback 保留 |

## MCAP 录制路径

MCAP recorder 由 `/xr/controller_state` 控制：

| 控制码 | 行为 |
| --- | --- |
| `4`（AA + 右摇杆，进入 OCS2 遥操） | 开始录制，创建 episode 目录并启动 `ros2 bag record` |
| `13` | 丢弃刚结束的上一段，与终端输入 `d/discard` 相同；后台保存未完成时不删除 |
| `14` | 停止录制；后台落盘；并发布 `/ros2recordstop` |
| `30` | 记录 `intervention_end`，录制不中断 |
| `31` | 记录 `intervention_start`，录制不中断 |

按控制器 `13`，或在运行 `mcap_recorder` 的终端里输入 `d`/`discard` 并回车，均可丢弃**刚结束的那一段**（默认仍然是保存，只有主动操作才会删除）：

- 删除该段本地目录；若已启用自动上传，同时尝试删除远端副本（已上传/上传中都会尝试清理，失败不影响后续采集）。
- 默认要求上一段保存后的 MCAP 在 **108–145 MB** 范围内（包含边界）。若小于 108 MB 或大于 145 MB 且尚未删除，按 `4` 不会开始下一段；recorder 会同时发布 `/ros2recordstop` 和 `/fsm_command=2`，命令机器人回到 `HOLD`、退出/取消 OCS2 遥操。终端会提示先按 `13`（或输入 `d/discard`）删除该段。
- 上一段仍在后台保存时，按 `4` 也会被暂时拦截，避免文件尚未写完就误判大小；看到“保存完成”后可重试。
- 位于 108–145 MB 范围内时可直接开始下一段；一旦下一段开始，上一段就不能再撤销。上下限可通过 `minimum_episode_size_mb:=108.0` 和 `maximum_episode_size_mb:=145.0` 调整，设为 `0` 可分别关闭对应检查。
- 控制器和终端共用同一删除入口并进行并发保护；同时操作不会重复删除。
- 若该段还在后台保存中，会提示稍后再试；若没有可丢弃的段，会提示当前无操作对象。

通过 `./quick.start` 启动普通 MCAP 或 RECAP 数采时，终端会在启动前询问本次是否开启 `108–145 MB` 限制。默认选择开启；选择关闭后，本次运行不会校验上一段大小。无人值守启动可预设：

```bash
QUICK_START_EPISODE_SIZE_LIMIT=false ./quick.start 2
```

也可直接使用 ROS 参数 `episode_size_limit_enabled:=true/false`，显式传参时 `quick.start` 不再询问。

LeRobot 自动转换默认开启。程序启动时不会处理历史数据；刚结束的段先保留丢弃窗口，按 `4`（AA + 右摇杆）开始下一段后也不会立即转换，等下一段真正结束后，上一段才会加入转换队列。被 `d/discard` 删除的段不会转换。

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

## 边采边传（episode 上传）

本地 episode 落盘完成后，后台线程用 `rsync` 将**整段目录**（`metadata.json` + `recording/`）同步到远程服务器。

| 配置 | 路径 |
| --- | --- |
| 上传开关与服务器 | `src/data_collection_recorder/config/recording/upload.yaml` |
| 上传逻辑 | `src/data_collection_core/data_collection_core/episode_uploader.py` |
| 状态写入 | episode 内 `metadata.json` 的 `upload` 字段 |

- **开关**：只改 yaml 里 `upload.enabled: true/false`，无 launch 参数。
- **认证**：SSH 公钥免密（`identity_file`），勿把密码写进仓库。
- **与采集隔离**：上传失败不影响继续采集；仅终端提示 ⚠️，本地数据仍在。
- **补传**：`ros2 run data_collection_core episode_upload_pending`
- **单段测试**：`python3 scripts/test_upload_one_episode.py <episode_dir>`

## MCAP 自动转 LeRobot

转换配置：

```text
src/data_collection_recorder/config/recording/lerobot_conversion.yaml
```

默认开启。通常只需要改 `output_dir`：

```yaml
lerobot_conversion:
  enabled: true
  script_path: ~/dataset_convert2lerobot/convert.sh
  output_dir: ~/data/lerobot_data/parcel_sorting
```

recorder 会后台调用：

```bash
~/dataset_convert2lerobot/convert.sh \
  --input-root <episode_dir> \
  --output-dir <output_dir>
```

转换日志写到 episode 目录下的 `lerobot_conversion.log`，状态写入 `metadata.json` 的 `lerobot_conversion` 字段。目标目录不存在时转换器会创建；目标目录已经是兼容的 LeRobot 数据集时会续写追加 episode。

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
