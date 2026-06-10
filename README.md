# ros2_data_collection

全套数采 workflow 说明见：

- `docs/project_structure_and_python_files.md` — **项目分级与各 py 文件说明**
- `docs/data_collection_code_flow_and_refactor_plan.md`
- `docs/refactor_project_structure_and_mcap_recording.md`

## 当前入口

本仓库现在保留两条录制路径，并附带扫码与上传模块：

| 模块 | 入口 | 是否 `colcon build` | 说明 |
| --- | --- | --- | --- |
| MCAP 新路径 | `data_collection_recorder/mcap_recorder` | ✅ 需要 | 控制节点启动原生 `ros2 bag record -s mcap` 全频录制 |
| 真实扫码 | `src/scan_demo/start_matrix220_ros2.sh` | ❌ 不需要 | Matrix220 读码 + `/scan/success`；**与采集分开启动** |
| 边采边传 | `upload.yaml` + 后台 `rsync` | ✅ 随 core/recorder 一起 build | 本地落盘完成后异步上传整段 episode |
| legacy 路径 | `multi_subscriber/multi_topic_subscriber` | ✅ 需要 | 旧版 PNG + CSV 落盘，作为 fallback 保留 |

## MCAP 录制路径

MCAP recorder 由 `/xr/controller_state` 控制：

| 控制码 | 行为 |
| --- | --- |
| `13` | 开始录制，创建 episode 目录并启动 `ros2 bag record` |
| `14` | 停止录制；后台落盘；可立即开始下一段；并发布 `/ros2recordstop` |
| `15` | 记录 `intervention_end`，录制不中断 |
| `16` | 记录 `intervention_start`，录制不中断 |

此外，采集中若 `/scan/success` 从 **1 变为 0**（30Hz 信号，见下文扫码模块），也会触发与 `14` 相同的停录流程。

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
  /scan/success \
  /scan/code \
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

## 扫码模块 `scan_demo`（真实 Matrix220）

`src/scan_demo/` **不是 ROS2 包**（无 `package.xml`），不参与 `colcon build`，需用脚本**单独启动**。

### 与采集的关系

扫码与 `mcap_recorder` 通过话题协作，**没有写进同一个 launch**，采集员需开两个终端（或两个进程）：

```text
scan_demo                          mcap_recorder
  matrix220/driver.py  -> /scan/code        (录进 bag)
  scan_success_publisher -> /scan/success   (订阅：采集中 1->0 则停录)
  订阅 /ros2recordstop  <- 停录时由 recorder 发布
```

`/scan/success` 状态机（固定 30Hz）：

```text
0 --[有效扫码 /scan/code]--> 1 --[停录 /ros2recordstop]--> 0
```

- 只开 `mcap_recorder`、不开扫码：仍可用 **13/14** 采集，`/scan/success` 可能一直为 0。
- 要用「扫码停录」：必须先起扫码，再起采集。

### 启动顺序（推荐）

**终端 1 — 扫码**

```bash
cd ~/code/ros2_data_collection-ZJC/src/scan_demo
bash stop_all.sh                    # 清旧节点，避免 /scan/success 双发布
bash start_matrix220_ros2.sh        # 默认连 Matrix220 192.168.10.104:51236
```

无真实扫码器时用模拟器：

```bash
cd ~/code/ros2_data_collection-ZJC/src/scan_demo
source /opt/ros/jazzy/setup.bash
python3 ros/mock_scanner_ros2.py    # 空格/s=置1，r=置0
```

**终端 2 — 采集**

```bash
cd ~/code/ros2_data_collection-ZJC
source /opt/ros/jazzy/setup.bash
source install/setup.bash
ros2 launch data_collection_recorder mcap_recorder.launch.py
```

### 采集员典型流程

1. 两个终端分别启动扫码 + `mcap_recorder`。
2. 控制器 **13** 开始本段采集。
3. 扫有效条码 → `/scan/success` 变为 1。
4. 结束本段：**14**，或让 `/scan/success` 回到 0（真实场景由 `/ros2recordstop` 复位；mock 可按 `r`）。
5. 终端提示「本段已结束」后可**立刻按 13** 开下一段；落盘与上传在后台进行。

更多细节见 `src/scan_demo/README.md`。

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
