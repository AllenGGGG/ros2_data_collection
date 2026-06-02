wo # 数采重构方案：项目分级 + rosbag2/MCAP 落盘

本文档回答两个问题：

1. 如何把当前「单文件 + CSV/PNG 落盘」重构成**清晰分层的项目结构**。
2. 如何改为 **ros2bag / MCAP** 压缩落盘，并保留现有 VR workflow（13/14/15/16）。

---

## 1. 为什么要换落盘方式

当前 `multi_subscriber.py` 的问题：

| 现状 | 问题 |
| --- | --- |
| 每帧 PNG + 多份 CSV | 磁盘占用大、IO 重、episode 目录碎 |
| `datetime.now()` 打时间戳 | 与传感器时间不对齐，后处理要对齐脚本 |
| callback 里攒 DataFrame + 0.5s flush | 逻辑重、易丢尾数据、难测 |
| 图像异步写盘与 CSV 分离 | 停录时图/表可能不一致 |

**rosbag2 + MCAP** 的收益：

- 按 ROS 消息原样录制，时间戳用 `header.stamp`（消息自带）。
- MCAP 支持 **zstd / lz4** 压缩，体积通常明显小于裸 PNG。
- 一条 episode = 一个 `.mcap`（或一个 bag 目录），便于搬运和版本管理。
- 可用 `ros2 bag play` 回放，与生态（Foxglove、离线导出）一致。
- 后处理改为「读 bag → 导出训练格式」，而不是维护采集节点里的写盘逻辑。

说明：**MCAP 不是和 rosbag2 二选一**。在 ROS 2 里通常是：

```text
rosbag2（录制 API / CLI）
    └── storage plugin: mcap（推荐）或 sqlite3
```

因此推荐表述为：**用 rosbag2 录制，存储格式选 MCAP**。

---

## 2. 推荐技术选型

### 2.1 存储格式：MCAP

Jazzy / Humble 等发行版可通过插件使用 MCAP：

```bash
sudo apt install ros-${ROS_DISTRO}-rosbag2-storage-mcap
```

录制时指定：

```bash
ros2 bag record -s mcap --storage-config-file ... topics...
```

或在代码里通过 `rosbag2_py` 的 storage 选项指定 `mcap`。

### 2.2 压缩

MCAP 常见配置（在 storage config YAML 里）：

```yaml
compression: "zstd"      # 或 lz4；lz4 更快，zstd 更小
compression_level: "default"  # 或 1–19，视 CPU 而定
```

三路 `sensor_msgs/Image` 仍是带宽大户，建议同时考虑：

- 若相机已发布 **`image_transport/compressed`**，优先录压缩话题（体积更小）。
- 否则录 `image_raw` + MCAP 压缩，仍比「每帧 PNG 文件」省管理成本。

### 2.3 录制实现方式（三选一）

| 方案 | 做法 | 优点 | 缺点 |
| --- | --- | --- | --- |
| **A. CLI 子进程** | 13 启 `ros2 bag record`，14 发 SIGINT | 实现快、压缩/插件由官方维护 | 进程管理、注解要写 sidecar |
| **B. rosbag2_py** | 节点内 `SequentialWriter` 开/关 session | 与 VR 状态机同进程，易写 metadata | 依赖 API 熟悉度，要处理 C++ 类型 |
| **C. 独立 recorder + 编排节点** | recorder 只录 bag；编排节点只管 13/14/15/16 | 职责最清晰 | 多节点/launch 稍复杂 |

**推荐：阶段 1 用 A 验证 workflow；阶段 2 迁到 B 或 C 作为长期结构。**

对你当前需求（VR 启停 + intervention 标记），**B 或 C** 更合适，因为要在 15/16 时写 episode 元数据，而不只是录话题。

---

## 3. 目标项目分级（推荐目录）

把仓库从「一个 `multi_subscriber` 包打天下」拆成 **接口 → 核心库 → 节点 → 启动 → 工具** 五层。

```text
ros2_data_collection/
├── README.md
├── docs/
│   ├── data_collection_code_flow_and_refactor_plan.md   # 现状说明（已有）
│   └── refactor_project_structure_and_mcap_recording.md # 本文档
│
├── config/                          # L0 配置（无代码依赖）
│   ├── recording/
│   │   ├── default_profile.yaml     # 要录的话题列表、QoS、压缩
│   │   └── mcap_storage.yaml        # MCAP zstd 等
│   └── workspace_paths.yaml.example # output_dir 示例，不提交机器路径
│
├── launch/                          # L4 启动编排
│   └── data_collection.launch.py
│
├── src/
│   ├── data_collection_msgs/        # L0 接口（可选但建议有）
│   │   ├── msg/
│   │   │   └── EpisodeMetadata.msg  # 可选：episode 起止、任务 id
│   │   └── srv/
│   │       └── StartEpisode.srv     # 可选：服务化启停
│   │
│   ├── data_collection_core/        # L1 领域核心（纯 Python，尽量不依赖 Node）
│   │   └── data_collection_core/
│   │       ├── __init__.py
│   │       ├── constants.py         # 13/14/15/16
│   │       ├── session.py           # EpisodeSession 状态机
│   │       ├── topic_registry.py    # 从 yaml 加载要录的 topic
│   │       ├── bag_backend.py       # 抽象：start/stop/write_metadata
│   │       ├── bag_rosbag2_py.py    # rosbag2_py 实现
│   │       └── bag_subprocess.py    # ros2 bag record 实现（过渡期）
│   │
│   ├── data_collection_recorder/    # L2 ROS 节点（薄）
│   │   └── data_collection_recorder/
│   │       ├── recorder_node.py     # 订阅 /xr/controller_state，驱动 session
│   │       └── annotation.py        # 15/16 → metadata 事件
│   │
│   └── data_collection_bringup/     # L3 元包：聚合依赖 + 安装 launch/config
│       ├── package.xml
│       ├── CMakeLists.txt 或 setup.py
│       └── ...
│
└── tools/                           # L5 离线工具（不进 ros2 run 主路径）
    ├── export_episode_to_lerobot.py # bag → 训练集
    ├── validate_episode.py          # 检查 mcap 完整性、时长、话题
    └── README.md
```

### 3.1 各层职责

| 层级 | 包/目录 | 职责 | 不应包含 |
| --- | --- | --- | --- |
| **L0** | `config/`, `data_collection_msgs/` | 契约：录哪些 topic、压缩参数、自定义消息 | 写盘、订阅实现 |
| **L1** | `data_collection_core` | Episode 状态机、bag 后端抽象、路径规则 | `rclpy.Node`、具体 topic 回调 |
| **L2** | `data_collection_recorder` | 订阅 VR 控制、调用 core 启停录、打 annotation | PNG/CSV、pandas 大缓冲 |
| **L3** | `data_collection_bringup` | launch、参数文件安装到 share | 业务逻辑 |
| **L4** | `launch/` | 一键起 recorder + 可选 relay | — |
| **L5** | `tools/` | 导出、校验、对齐 | 在线采集 |

### 3.2 与现状的映射

| 现有文件 | 重构后 |
| --- | --- |
| `multi_subscriber.py` | 删除「按帧写 PNG/CSV」；逻辑拆到 `core/session` + `recorder_node` |
| `multi_subscriber_sim_real.py` | 合并为 `config/recording/sim_real_profile.yaml` 多几路 image topic |
| `align_dataset.py` | 迁到 `tools/`，输入改为 **读 MCAP** 再对齐导出 |
| `align_timestamps.py` | 废弃或并入 export 工具 |

---

## 4. Episode 在磁盘上长什么样（MCAP）

### 4.1 目录约定

建议一次 episode 一个目录，便于附带元数据：

```text
<output_dir>/
└── 20260601_143022_123456/          # episode_id = 时间戳或 UUID
    ├── metadata.json                # 人可读：任务、操作员、事件时间线
    ├── events.jsonl                 # 15/16 等事件（可选，与 metadata 二选一）
    └── recording/                   # rosbag2 输出
        └── episode_0.mcap           # 或 ros2 bag 默认命名
```

`metadata.json` 示例：

```json
{
  "episode_id": "20260601_143022_123456",
  "schema_version": 1,
  "started_at": "2026-06-01T14:30:22.123456",
  "stopped_at": "2026-06-01T14:35:10.000000",
  "storage": { "format": "mcap", "compression": "zstd" },
  "events": [
    { "t": "2026-06-01T14:31:00.000000", "type": "intervention_start", "source": "controller_state", "code": 16 },
    { "t": "2026-06-01T14:32:30.000000", "type": "intervention_end", "source": "controller_state", "code": 15 }
  ]
}
```

### 4.2 必须录进 bag 的话题（与现网一致）

从当前 `multi_subscriber.py` 迁移的 **默认 profile**：

```yaml
# config/recording/default_profile.yaml
topics:
  - /camera_head/color/image_raw
  - /camera_left_wrist/color/image_raw
  - /camera_right_wrist/color/image_raw
  - /joint_states
  - /left_current_pose
  - /right_current_pose
  - /left_current_target
  - /right_current_target
  - /left_gripper_controller/target_command
  - /right_gripper_controller/target_command
  - /xr/controller_state    # 重要：回放时可从 bag 还原 13–16 时间线
```

**intervention 的两种做法（推荐并存）：**

1. **bag 里已有** `/xr/controller_state` → 离线解析 15/16 时间。
2. **sidecar** `metadata.json` / `events.jsonl` → 训练管线读起来简单，不依赖回放。

---

## 5. VR Workflow 在新架构下的行为

状态机放在 `data_collection_core/session.py`，与现逻辑对齐：

```text
IDLE
  │ 13
  ▼
RECORDING ─────────────────────────────────────┐
  │ 16 (仅写 annotation + metadata 事件)        │
  │     bag 录制不中断                          │
  ▼                                            │
RECORDING_INTERVENTION                          │
  │ 15 (写 intervention_end)                    │
  ▼                                            │
RECORDING ─────────────────────────────────────┘
  │ 14
  ▼
IDLE（关闭 bag writer，写 metadata stopped_at）
```

**关键约束（与现 bug 修复一致）：**

- 只有 **13** 打开 bag writer。
- 只有 **14** 关闭 bag writer。
- **15/16** 只更新 `metadata` / `events`，**不** start/stop bag。

推理节点仍订阅 15/16 控制模型；数采节点只负责录包 + 记事件。

---

## 6. 实现路径（分阶段，降低风险）

### 阶段 0：准备（1–2 天）

- [ ] 确认环境：`ros-$ROS_DISTRO-rosbag2-storage-mcap` 已安装。
- [ ] 用 CLI 手工录一条，验证体积和回放：

```bash
ros2 bag record -s mcap -o /tmp/test_episode \
  /camera_head/color/image_raw \
  /joint_states \
  /xr/controller_state
```

- [ ] 确认三路相机在真机上的实际带宽，决定要不要改录 compressed topic。

### 阶段 1：骨架 + CLI 后端（约 1 周）

- [ ] 建新包 `data_collection_core`、`data_collection_recorder`。
- [ ] `config/recording/default_profile.yaml` + `mcap_storage.yaml`。
- [ ] `recorder_node`：只处理 13/14 → `BagSubprocessBackend` 启停 `ros2 bag record`。
- [ ] 15/16 → 写 `metadata.json` 事件。
- [ ] **不删** 旧 `multi_subscriber.py`，launch 里用参数选 `legacy` / `mcap`。

### 阶段 2：rosbag2_py 后端（约 1–2 周）

- [ ] 实现 `BagRosbag2PyBackend`：同进程开/关 `SequentialWriter`，避免子进程 SIGINT 竞态。
- [ ] 统一 episode 目录布局。
- [ ] 增加 `tools/validate_episode.py`。

### 阶段 3：离线导出（按训练需求）

- [ ] `export_episode_to_lerobot.py`：MCAP → 你需要的训练格式（图像解码、joint 对齐）。
- [ ] 废弃 `align_dataset.py` 直接读 CSV 的路径，改为读 bag。

### 阶段 4：收尾

- [ ] 默认 launch 切到 MCAP recorder。
- [ ] 删除 PNG/CSV 写盘代码路径。
- [ ] 更新 README、真机实操文档中的验证步骤（`ros2 bag info` / Foxglove）。

---

## 7. 核心代码示意（rosbag2_py 后端）

以下为**设计示意**，不是现成可编译代码；实现时需按你本机 `rosbag2_py` API 微调。

```python
# data_collection_core/bag_rosbag2_py.py（示意）

from pathlib import Path
import rosbag2_py
from rclpy.serialization import serialize_message


class BagRosbag2PyBackend:
    def __init__(self, episode_dir: Path, storage_config_path: Path, topic_types: dict):
        self.episode_dir = episode_dir
        self.uri = str(episode_dir / "recording")
        self.topic_types = topic_types  # topic -> type str
        self.writer = None

    def start(self):
        storage_options = rosbag2_py.StorageOptions(
            uri=self.uri,
            storage_id="mcap",
            storage_config_uri=str(storage_config_path),
        )
        converter_options = rosbag2_py.ConverterOptions("", "")
        self.writer = rosbag2_py.SequentialWriter()
        self.writer.open(storage_options, converter_options)
        for topic, type_str in self.topic_types.items():
            self.writer.create_topic(
                rosbag2_py.TopicMetadata(name=topic, type=type_str, serialization_format="cdr")
            )

    def write(self, topic: str, msg, timestamp_ns: int):
        self.writer.write(topic, serialize_message(msg), timestamp_ns)

    def stop(self):
        if self.writer:
            del self.writer  # 触发落盘关闭
            self.writer = None
```

**Recorder 节点**不再订阅图像去 `cv2.imwrite`，而是：

- **方案 B1**：recorder 订阅所有 topic，在 callback 里 `writer.write()`（节点会较重，但控制力强）。
- **方案 B2（更 ROS 原生）**：recorder 只 orchestrate；用 **component / 多进程** 让 rosbag2 官方 record 路径录——本质接近方案 A。

对 **高频 Image + JointState**，若 B1 CPU 吃紧，优先 **A 或官方 record 子进程**，编排节点只发启停。

---

## 8. `recorder_node` 最小职责（L2）

```python
# 伪代码
class RecorderNode(Node):
    def __init__(self):
        self.session = EpisodeSession(output_dir=self.get_parameter("output_dir").value)
        self.backend = create_backend_from_param("backend")  # subprocess | rosbag2_py
        self.create_subscription(Int32, "/xr/controller_state", self.on_control, 10)

    def on_control(self, msg):
        code = msg.data
        if code == 13:
            self.session.start()
            self.backend.start(self.session.episode_dir, profile=load_profile())
        elif code == 14:
            self.backend.stop()
            self.session.stop()
        elif code == 15 and self.session.is_recording:
            self.session.annotate("intervention_end", code=15)
        elif code == 16 and self.session.is_recording:
            self.session.annotate("intervention_start", code=16)
```

---

## 9. 依赖变更（package.xml）

`data_collection_recorder` 需要增加：

```xml
<exec_depend>rosbag2_py</exec_depend>
<exec_depend>rosbag2_storage_mcap</exec_depend>
<exec_depend>rosbag2_transport</exec_depend>
<exec_depend>std_msgs</exec_depend>
<exec_depend>sensor_msgs</exec_depend>
<exec_depend>geometry_msgs</exec_depend>
```

`data_collection_core` 若要保持「无 ROS 依赖」便于单测，则 **不要** 依赖 `rclpy`；`rosbag2_py` 仅放在 recorder 或单独 `bag_backends_ros` 包。

---

## 10. 什么建议改、什么可以不改

### 建议改

| 项 | 原因 |
| --- | --- |
| PNG + CSV 落盘 | 你要的核心目标 |
| 单文件 800 行 Node | 拆 session / backend / annotation |
| 硬编码 `/home/zihang/ros2_ws/raw_datasets` | 改为参数 + `metadata.json` |
| `align_dataset.py` 读 CSV | 改为读 MCAP |
| README 启动命令 | 与新包名一致 |

### 可以暂缓

| 项 | 原因 |
| --- | --- |
| 13/14/15/16 语义 | 已正确，迁到新 session 即可 |
| 真机 topic 名称 | 先原样录入 bag |
| 推理节点发 15/16 | 仍由推理侧处理，数采只录 |
| `data_collection_msgs` | 第一版用 JSON sidecar 即可，消息包可后加 |

### 不建议做

- 自研 MCAP 写库（直接用 rosbag2 + mcap plugin）。
- 第一版同时上 C++ recorder + Python 编排（除非性能不够）。
- 重构时改掉 episode 里图像命名规则（新格式本来就没有 `head_image0.png`）。

---

## 11. 决策摘要

| 问题 | 建议 |
| --- | --- |
| ros2bag 还是 MCAP？ | **rosbag2 录制 + MCAP 存储**，不是二选一 |
| 第一版怎么录？ | 先 **CLI `ros2 bag record -s mcap`** 验证，再 **rosbag2_py** |
| 项目怎么分级？ | **msgs/config → core → recorder → bringup → tools** |
| intervention 怎么留？ | **bag 录 `/xr/controller_state` + sidecar `metadata.json`** |
| 旧代码怎么办？ | 保留一个 launch 参数 `recording_backend:=legacy\|mcap`，验证后删 legacy |

---

## 12. 建议你本地立刻做的两条验证命令

```bash
# 1. 是否已有 mcap 插件
ros2 pkg list | grep rosbag2_storage_mcap

# 2. 录 30 秒看体积与信息
ros2 bag record -s mcap -o /tmp/episode_test \
  /camera_head/color/image_raw /joint_states /xr/controller_state
# Ctrl+C 后
ros2 bag info /tmp/episode_test
```

把 `ros2 bag info` 的输出（话题列表、时长、大小）记下来，阶段 1 的 `default_profile.yaml` 和压缩档位就可以按真机数据定标。

---

## 13. 下一步（若你确认方向）

可按你优先级选一条让我直接开工改代码：

1. **只搭骨架**：新包目录 + 空 `recorder_node` + yaml + launch，13/14 用子进程录 MCAP。
2. **直接 rosbag2_py**：跳过子进程，一次到位（开发量更大）。
3. **先写 `export_episode` 工具**：证明 MCAP 能导出成你现在训练用的格式，再删 CSV 路径。

你回复倾向 **1 / 2 / 3** 或组合即可。
