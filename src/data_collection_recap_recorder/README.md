# data_collection_recap_recorder

no-subtask **recap 推理 rollout** 专用数采包：与 `data_collection_recorder`（普通 MCAP 数采）独立，负责录制推理侧 MCAP，并与 no-subtask 推理脚本联动。

## 和普通数采的区别

| 项目 | `data_collection_recorder` | `data_collection_recap_recorder`（本包） |
| --- | --- | --- |
| 用途 | 专家遥操 / 通用 MCAP 数采 | recap 推理 rollout + 人工接管 |
| `/intervention` | 不录制 | 录制并持续发布 |
| `/scan/success` | 不录制 | 录制（写入 `state[16]` 对齐） |
| 推理联动 | 无 | launch 可一并启动推理脚本 |
| 13/14 | 开/停录制 | 开/停录制 |
| 15/16 | 不处理 | 录制中切换 `/intervention`，不中断 MCAP |

普通数采请继续用：

```bash
ros2 launch data_collection_recorder mcap_recorder.launch.py
```

## 系统分工

```text
VR 手柄
  └─ xr_target_node  ──发布──► /xr/controller_state (13/14/15/16)
                                    │
            ┌───────────────────────┼───────────────────────┐
            ▼                       ▼                       ▼
   recap_mcap_recorder      no-subtask 推理脚本        arms_target_manager
   (13/14 开停录)           (15/16 暂停/恢复推理)       (VR 遥操 pose/夹爪)
   (15/16 写 intervention)
```

控制码约定（与 `data_collection_core/constants.py` 一致）：

| VR 操作 | code | recap 数采 | 推理脚本 |
| --- | --- | --- | --- |
| 左侧键 + 左前键 | `13` | 开始录制 episode | 无影响 |
| 右侧键 + 右前键 | `14` | 停止录制并落盘 | 无影响 |
| 左侧键 + X | `15` | 继续录，`/intervention=0` | 恢复推理，开始发动作/夹爪 |
| 右侧键 + A | `16` | 继续录，`/intervention=1` | 暂停推理，停止发动作/夹爪 |
| 右摇杆（接管） | — | 继续录 | 人工遥操（由 `arms_target_manager` 控制） |

要点：

- **开始数采（13）不会自动开始推理**。推理进程在 launch 时加载模型一次，但默认 `start_inference_enabled: false`，需 VR 发 `15` 才开始发动作。
- **暂停推理（16）不会卸载模型**，只是推理侧 `_inference_enabled=false`，清空 action buffer，不再发布 target/夹爪。
- **推理时 VR 是否发夹爪** 由机器人侧 `arms_target_manager` 仲裁；本包只保证推理侧在暂停时不发。

## 目录结构

```text
data_collection_recap_recorder/
├── config/
│   ├── recording/recap_profile.yaml    # MCAP 话题与默认输出目录
│   └── inference/no_subtask_inference.yaml  # 模型路径与推理默认参数
├── launch/
│   └── recap_inference_collection.launch.py   # 一体化启动（推荐）
├── data_collection_recap_recorder/
│   └── recap_recorder_node.py          # recap 专用 recorder 节点
└── README.md
```

## 配置

### 1. 模型路径 — `config/inference/no_subtask_inference.yaml`

改这里的 `model_id` 即可切换权重，无需改 launch：

```yaml
pistar06_inference_node:
  ros__parameters:
    model_id: /path/to/pretrained_model
    device: cuda
    action_mode: delta
    rtc_mode: xiaomi_overlap
    scan_success_topic: /scan/success
    start_inference_enabled: false   # launch 后先暂停，等 VR 发 15
    intervention_value: 1
```

### 2. 数采输出与话题 — `config/recording/recap_profile.yaml`

```yaml
output_dir: ~/ros2_ws/recap_raw_datasets_mcap
```

录制的关键 topic 包括：三路 compressed 相机、joint/pose、gripper command、`/scan/success`、`/xr/controller_state`、`/intervention`。

输出 episode 结构：

```text
<output_dir>/<episode_id>/
├── metadata.json
└── recording/
    ├── metadata.yaml
    └── recording_0.mcap
```

## 构建

```bash
cd /home/zihang/workspace/chekp/ros2_data_collection_YWL
colcon build --packages-select data_collection_core data_collection_recorder data_collection_recap_recorder
source install/setup.bash
```

确认 MCAP 插件已安装：

```bash
ros2 pkg list | grep rosbag2_storage_mcap
```

## 启动（推荐：一体化）

一条命令同时启动 **recap 数采 + no-subtask 推理**（模型只加载一次，初始为暂停状态）：

```bash
cd /home/zihang/workspace/chekp/ros2_data_collection_YWL
source /opt/ros/jazzy/setup.bash
source install/setup.bash

# 若推理依赖 lerobot，请换成对应 conda 环境的 python
ros2 launch data_collection_recap_recorder recap_inference_collection.launch.py \
  output_dir:=/home/zihang/workspace/chekp/test_recap \
  python_executable:=/home/zihang/miniconda3/envs/lerobot_dev/bin/python3
```

默认会读取安装后的：

- 数采 profile：`share/data_collection_recap_recorder/config/recording/recap_profile.yaml`
- 推理参数：`share/data_collection_recap_recorder/config/inference/no_subtask_inference.yaml`

### 常用 launch 参数

```bash
# 只改输出目录
ros2 launch data_collection_recap_recorder recap_inference_collection.launch.py \
  output_dir:=/data/recap_rollout_$(date +%Y%m%d)

# 换一份推理参数文件（例如不同 checkpoint）
ros2 launch data_collection_recap_recorder recap_inference_collection.launch.py \
  inference_params_file:=/path/to/my_inference.yaml

# 换推理脚本（例如 xiaomi_rtc wrapper）
ros2 launch data_collection_recap_recorder recap_inference_collection.launch.py \
  inference_script:=/home/zihang/workspace/chekp/IsaacSim-ros_workspaces/jazzy_ws/src/isaac_tutorials/scripts/pistar06/post_train/trajs683_delta/pistar06_inference_post_train_trajs683_delta_speedup_2x_chunksize_35_async_scan_xiaomi_rtc.py

# 只启动数采，不启动推理（推理已在别的终端跑着）
ros2 launch data_collection_recap_recorder recap_inference_collection.launch.py \
  run_inference:=false
```

查看全部参数：

```bash
ros2 launch data_collection_recap_recorder recap_inference_collection.launch.py --show-args
```

## 启动（分终端）

若希望推理和数采分开排错，可拆成两个终端。

**终端 A — recap 数采**

```bash
cd /home/zihang/workspace/chekp/ros2_data_collection_YWL
source /opt/ros/jazzy/setup.bash
source install/setup.bash

ros2 run data_collection_recap_recorder recap_mcap_recorder \
  --ros-args -p output_dir:=/home/zihang/workspace/chekp/test_recap
```

**终端 B — no-subtask 推理**

```bash
cd /home/zihang/workspace/chekp/IsaacSim-ros_workspaces/jazzy_ws
source /opt/ros/jazzy/setup.bash
source /home/zihang/miniconda3/etc/profile.d/conda.sh
conda activate lerobot_dev

python3 jazzy_ws/src/isaac_tutorials/scripts/pistar06/post_train/trajs683_delta/pistar06_inference_post_train_trajs683_delta_speedup_2x_chunksize_35_async_scan_xiaomi_rtc.py \
  --ros-args \
  --params-file /home/zihang/workspace/chekp/ros2_data_collection_YWL/install/data_collection_recap_recorder/share/data_collection_recap_recorder/config/inference/no_subtask_inference.yaml
```

## 推荐现场 workflow

1. 启动 `recap_inference_collection.launch.py`（或分终端启动数采 + 推理）。
2. 等待推理模型加载完成（终端出现 worker ready / paused mode 日志）。
3. VR：**左侧键 + 左前键** → 开始本条 episode 数采。
4. VR：**左侧键 + X** → 开始模型推理（推理发动作，VR 遥操应让出控制权）。
5. 需要接管：VR **右侧键 + A** 暂停推理 → **右摇杆** 进入人工遥操。
6. 结束接管：**右摇杆** 退出遥操 → VR **左侧键 + X** 恢复推理。
7. 任务结束：VR **右侧键 + 右前键** → 停止数采，等待终端「保存完成」提示。

## 转 LeRobot 数据集

MCAP episode 用 `dataset_convert2lerobot/no_subtask_rollout_converter` 转换：

```bash
cd /home/zihang/workspace/chekp/dataset_convert2lerobot
python -m no_subtask_rollout_converter \
  --input-root /home/zihang/workspace/chekp/test_recap \
  --output-dir /path/to/lerobot_out \
  --episode-success auto \
  --action-pose-mode delta
```

`observation.intervention` 会与 bag 里 `/intervention` 对齐；`scan_success` 在 `observation.state[16]`，不作为独立 top-level feature。

## 排错

**夹爪不受控地开合**

- 常见原因：推理与 `arms_target_manager` / `rviz` 同时发布 `/left_gripper_controller/target_command`。
- 检查：`ros2 topic info -v /left_gripper_controller/target_command`
- 推理暂停（16）后推理侧应停止发布；若仍抖动，需在机器人遥操侧根据 `15/16` 做控制权切换。

**按 15/16 后录制被意外停止**

- 确认没有同时跑旧的 `multi_subscriber` 或错误版本的 recorder（曾把 15/16 当成 14/13）。
- recap 路径应只跑本包的 `recap_mcap_recorder` 或 `recap_inference_collection.launch.py`。

**推理一启动就抢夹爪**

- 确认 `no_subtask_inference.yaml` 里 `start_inference_enabled: false`。
- 不要单独启动旧推理脚本且默认 `_inference_enabled=true`。

**修改配置后不生效**

- 改的是 `src/...` 下 yaml 后需要重新 `colcon build` 并 `source install/setup.bash`，或 launch 时显式传 `inference_params_file` / `profile_path` 指向源码路径。

## 相关文档

- 普通 MCAP 数采：`../data_collection_recorder/` 与仓库根目录 `README.md`
- VR workflow 与历史 bug 说明：`../docs/fix_bug_data_collection_control_mechanis.md`
- no-subtask 转换器：`../../../dataset_convert2lerobot/no_subtask_rollout_converter/README.md`
cd /home/zihang/workspace/chekp/ros2_data_collection_YWL
source /opt/ros/jazzy/setup.bash
source install/setup.bash

ros2 launch data_collection_recap_recorder recap_inference_collection.launch.py \
  output_dir:=/home/zihang/workspace/chekp/test_recap \
  python_executable:=/home/zihang/miniconda3/envs/lerobot_dev/bin/python3
