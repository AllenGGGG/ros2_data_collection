# Data collection control mechanisms analysis

*Exported on 2026/5/20 at GMT+8 13:56:39 from Cursor (3.2.21)*

---

---

**User**

我现在需要执行这样的workflow，其中VR手势与@/home/zihang/workspace/真机实操.pdf 中手势保持一致：模型在真机上刚开始推理（不要采集数据）-> 数采员使用VR手柄进行开始录制（开始采集数据，并创建文件开始记录）-> 推理过程中数采员需要对真机进行接管，于是使用VR手柄先暂停模型推理（继续采集数据）-> 数采员使用VR手柄开始人工接管（继续采集数据，并能够在数据里记录接管开始）-> 数采员使用VR手柄结束人工接管（继续采集数据，在数据里记录接管结束）-> 数采员使用VR手柄开启模型继续推理（继续采集数据）-> 一条任务完成后，数采员使用VR手柄停止数据采集（数据采集停止，并完整已经保存一条数据）。现在bug是，数采员开始采集后确实数据在被录制，但是使用暂停模型推理还是说开始接管就停止了数据录制。请理解分析我整个workflow,并对目前的所涉及的所有文件，进行排查问题，并给出要修改的确切方案，以及合理性，然后开始修改

---

**Cursor**

我先把这条 workflow 映射到实际按键与话题值，再核对 `src`、`install`、推理脚本和 VR 发布链路，确认哪些文件必须改、哪些只需要重新构建生效。

我已经把 bug 根因收敛清楚了：**协议本身在你当前源码里已经是对的，真正出问题的是 `ros2 run` 还在跑旧的安装版本**。我准备做两类修改：一类是把 `multi_subscriber` 源码中的启停/标注语义显式化，另一类是把推理脚本里误导性的 “requested data recording/flush” 日志改掉，避免后续再次混淆。

我开始改代码了：先把 `multi_subscriber` 的控制码语义写死并打印清楚，再把推理脚本里的误导日志改成“推理启停信号”，这样修完后行为和 PDF workflow 会完全一致。

代码已经改好，接下来我会重新构建相关包并用当前环境核验：`multi_subscriber` 要确保 `ros2 run` 不再走旧 install 逻辑，推理脚本则确认只发“推理启停”信号。

下一步是把修改落到你实际运行的环境里：我会重建 `multi_subscriber` 覆盖旧 install 版本，并同步更新 `isaac_tutorials` 的运行包装，随后再核验 `ros2 run` 指向的文件内容。

构建暴露了另一个关键问题：`/home/zihang/ros2_ws` 里有**两个同名包** `multi_subscriber`，所以我刚才改的那个仓库路径不一定就是实际被安装的源。现在我要把两个源目录都对齐，避免再出现“看的是一个包，跑的是另一个包”。

我已经确认：**真正对应当前 install 的源包在 `/home/zihang/ros2_ws/src/multi_subscriber*`*，而不是你当前打开的那个嵌套仓库副本。我先把这个“实际生效源包”改掉，再只针对这个 base path 重建。

我已经把实际生效包重建完成，现在只差最后一层验证：确认 `install` 里的回调已变成“13/14 录制、15/16 只标注/暂停推理”，这样你的整条 workflow 才真正修复。

## 结论

你这条 workflow 我已经按“VR 手势保持 PDF 一致”的目标梳理并修了。真正的 bug 不是 `xr_target_node`，也不是 `tianji-hil-ws`，而是**当前实际被 `ros2 run multi_subscriber multi_topic_subscriber` 执行的包版本**把控制码解释错了：

- `15` 被当成了“开始录制”
- `16` 被当成了“停止录制”

所以你一旦用 VR 做“暂停模型推理”或“恢复模型推理”，数据采集也被连带启停了，直接破坏了你想要的 RECAP workflow。

## 排查结果

VR 手势映射本身是对的，和 `真机实操.pdf` 一致：

- 左侧键 + 左前键 -> `13`：开始数采
- 右侧键 + 右前键 -> `14`：停止数采
- 左侧键 + X -> `15`：推理启动/恢复
- 右侧键 + A -> `16`：推理暂停

`xr_target_node.py` 这部分不用改，它已经正确发布 `13/14/15/16`。推理脚本也确实会在启动时发一次 `15`，这本来没问题，问题出在数采节点的“旧安装版本”把 `15/16` 误当成录制启停。

我确认了三件关键事实：

1. 你当前真正运行的是
  `/home/zihang/ros2_ws/install/multi_subscriber/lib/python3.12/site-packages/multi_subscriber/multi_subscriber.py`
2. 这个安装版本之前的逻辑是：
  - `13 或 15` -> `start_recording()`
  - `14 或 16` -> `stop_recording()`
3. 你当前打开的另一个源码副本
  `/home/zihang/ros2_ws/ros2_data_collection/src/multi_subscriber/...`  
   其实早就已经是正确逻辑，但**它不是当前 active package**。另外，`/home/zihang/ros2_ws` 里还存在一个真正参与安装构建的同名包：
   `/home/zihang/ros2_ws/src/multi_subscriber/...`

也就是说，这次问题本质上是：

- **工作区里有两个同名 `multi_subscriber` 包**
- 你看到的是一个版本
- 真正在跑的是另一个版本

## 我已经做的修改

我改了 3 处。

第一，修正了当前真正生效的源包：

`/home/zihang/ros2_ws/src/multi_subscriber/multi_subscriber/multi_subscriber.py`

现在它的逻辑已经改成：

- `13` 才开始录制
- `14` 才停止录制
- `15` 仅在录制中写入“模型恢复/专家结束接管”的标记
- `16` 仅在录制中写入“模型暂停/专家开始接管”的标记

并且加了更明确的启动日志，避免以后再把 `15/16` 误认为录制启停。

第二，我把你当前打开的那个副本也同步成了同样语义，避免以后再次“看的是 A，跑的是 B”。

第三，我把推理脚本里误导性的日志改掉了：

`/home/zihang/IsaacSim-ros_workspaces/jazzy_ws/src/isaac_tutorials/scripts/pistar06/post_train/trajs401_delta/pistar06_inference_post_train_trajs401_delta_base_async_runtime.py`

之前是：

- `requested data recording`
- `requested data flush`

现在改成了更准确的：

- `published inference-enable signal`
- `published inference-pause signal`

这样日志就不会再把“推理控制信号”误写成“数据采集控制信号”。

## 已完成的构建

我已经完成了实际生效包的重建：

- `ros2_ws` 中的 active `multi_subscriber` 已重建
- `jazzy_ws` 中的 `isaac_tutorials` 已重建

并且我已经验证过现在 install 里的 active 版本确实是：

- `13/14` 控制录制
- `15/16` 不再启停录制

## 修复后你的 workflow 会变成什么样

修复后，流程会按你要的方式工作：

1. 启动推理脚本
  不采集数据，只是推理运行；即使启动时发 `15`，也**不会开始录制**
2. 数采员按 VR：左侧键 + 左前键 (`13`)
  开始数据采集，创建文件夹并开始写盘
3. 数采员按 VR：右侧键 + A (`16`)
  暂停模型推理，**继续采集数据**，并在数据里记录“接管开始”
4. 数采员按右摇杆开始人工接管
  **继续采集数据**，真机进入人工遥操
5. 数采员按右摇杆结束人工接管
  **继续采集数据**
6. 数采员按 VR：左侧键 + X (`15`)
  恢复模型推理，**继续采集数据**，并记录“接管结束/模型恢复”
7. 一条任务结束后按 VR：右侧键 + 右前键 (`14`)
  停止数据采集并完整保存这一条数据

这就和你要的 RECAP 采集流程一致了。

## 你现在需要做的事

你现在线上的老进程不会自动热更新，所以要在对应终端里重新 source 并重启节点。

在数采终端里重新执行：

```bash
cd /home/zihang/ros2_ws
source /opt/ros/jazzy/setup.bash
source install/setup.bash
ros2 run multi_subscriber multi_topic_subscriber
```

在推理终端里重新执行：

```bash
cd /home/zihang/IsaacSim-ros_workspaces/jazzy_ws
source /opt/ros/jazzy/setup.bash
source /home/zihang/miniconda3/etc/profile.d/conda.sh
conda activate lerobot_dev_v0.5.0
source install/setup.bash
ros2 run isaac_tutorials pistar06_inference_post_train_trajs401_delta_base_async_xiaomi_rtc.py
```

## 还需要你注意的一点

`/home/zihang/ros2_ws` 里目前有两个同名 `multi_subscriber` 包：

- `/home/zihang/ros2_ws/src/multi_subscriber`
- `/home/zihang/ros2_ws/ros2_data_collection/src/multi_subscriber`

这就是这次问题反复混淆的根源之一。现在我已经把两个副本语义对齐了，但从长期维护来说，**最好最终只保留一个**，否则以后还会出现“改了一份源码，但跑的是另一份安装包”。

如果你愿意，我下一步可以继续帮你做一件很有价值的事：**直接写一个最小验证步骤**，让你按 13 -> 16 -> 右摇杆 -> 右摇杆 -> 15 -> 14 跑一遍，并检查生成目录里的 `interventions.csv` 是否按预期记录了接管开始和结束。

---

**User**

可以

---

**Cursor**

下面给你一套**最小验证流程**，专门验证你要的这条 workflow 是否已经修好，且尽量不引入额外变量。

## 验证目标

确认现在系统满足以下行为：

1. **仅启动推理脚本**时，**不会自动开始录制**
2. 按 **左侧键 + 左前键** 后，才开始创建 episode 目录并写数据
3. 按 **右侧键 + A** 暂停模型时，**录制不中断**
4. 人工接管前后，`interventions.csv` 能正确记录状态切换
5. 按 **左侧键 + X** 恢复模型时，**录制不中断**
6. 按 **右侧键 + 右前键** 后，录制正常结束并完整保存

---

## 先重启两个关键节点

先把旧进程都停掉，再按下面命令重启，不要混用旧环境。

### 1. 数采终端

```bash
cd /home/zihang/ros2_ws
source /opt/ros/jazzy/setup.bash
source install/setup.bash
ros2 run multi_subscriber multi_topic_subscriber
```

你现在应该看到类似日志，但要注意语义已经改了，不应该再看到 “13 or 15 start recording” 这种旧提示。

### 2. 推理终端

```bash
cd /home/zihang/IsaacSim-ros_workspaces/jazzy_ws
source /opt/ros/jazzy/setup.bash
source /home/zihang/miniconda3/etc/profile.d/conda.sh
conda activate lerobot_dev_v0.5.0
source install/setup.bash
ros2 run isaac_tutorials pistar06_inference_post_train_trajs401_delta_base_async_xiaomi_rtc.py
```

---

## 最小实测步骤

按这个顺序做，最容易定位问题。

### 步骤 0：清点当前最新目录

先记一下 `ros2_ws` 里当前最新的数据目录，避免后面看混。

```bash
cd /home/zihang/ros2_ws
ls -dt 20* | head
```

### 步骤 1：只启动推理，不动 VR

预期结果：

- **不应**新建新的时间戳目录
- 数采终端**不应**出现 `Recording started`

如果这一步就自动开始录制，说明还有旧进程没停干净，或者没有 source 到新的 `install/setup.bash`。

### 步骤 2：按 `左侧键 + 左前键`

也就是 PDF 里的“开始数采”。

预期结果：

- 数采终端出现 `Recording started: /home/zihang/ros2_ws/<timestamp>`
- `ros2_ws` 下新建一个新的时间戳目录
- 目录下开始出现：
  - `camera_head/`
  - `camera_left_wrist/`
  - `camera_right_wrist/`
  - 若缓冲 flush 后会有若干 csv

### 步骤 3：让模型先跑一小段

这一步只是让录制里先有“模型自主段”。

预期结果：

- 目录持续增长
- 图像数、关节数据持续写入

### 步骤 4：按 `右侧键 + A`

也就是“暂停模型推理”。

预期结果：

- **模型暂停**
- **录制不能停**
- 数采终端**不应**出现 `Recording stopped`
- 会往 `interventions.csv` 里追加一条 `True`

你可以把这一步理解为：
`16 = 开始专家接管标记`
而不是停止录制。

### 步骤 5：按右摇杆，开始人工接管

预期结果：

- 真机进入人工接管
- 录制继续
- 不会新开目录
- 不会停止录制

注意：右摇杆本身不是数采启停键，它只是让真机进入/退出人工接管控制模式。

### 步骤 6：再次按右摇杆，结束人工接管

预期结果：

- 真机退出人工接管
- 录制继续
- 此时 `interventions.csv` **不会自动结束**
- 真正表示“恢复模型”的动作还是下一步的 `左侧键 + X`

### 步骤 7：按 `左侧键 + X`

也就是“恢复模型推理”。

预期结果：

- 模型恢复
- 录制继续
- 数采终端不应出现 `Recording stopped`
- `interventions.csv` 追加一条 `False`

你可以把这一步理解为：
`15 = 专家接管结束 / 模型恢复标记`

### 步骤 8：任务完成后按 `右侧键 + 右前键`

也就是“停止数采”。

预期结果：

- 数采终端出现 `Recording stopped`
- 当前 episode 完整落盘
- 不再继续写入该目录

---

## 验证文件该怎么看

假设最新目录是：

```bash
/home/zihang/ros2_ws/20260520_xxxxxx_xxxxxx
```

重点看这个文件：

```bash
/home/zihang/ros2_ws/20260520_xxxxxx_xxxxxx/interventions.csv
```

### 正确预期

如果你做了：

1. 开始录制
2. 模型跑一段
3. `右侧键 + A` 暂停模型，准备接管
4. 人工接管一段
5. `左侧键 + X` 恢复模型
6. 最后停采

那么 `interventions.csv` 应该至少有两条关键状态：

```csv
timestamp,value
...,True
...,False
```

语义是：

- `True`：专家接管开始 / 模型暂停
- `False`：专家接管结束 / 模型恢复

顺序通常应该是：

1. 先出现 `True`
2. 后出现 `False`

如果你在开始录制后，推理恢复时又按过一次 `左侧键 + X`，也可能在更早阶段多一条 `False`，这取决于你的实际按键时序，但**关键是 `16` 不应该再导致 stop_recording**。

---

## 快速检查命令

你可以在采集结束后跑这几个命令：

### 看最新 episode

```bash
cd /home/zihang/ros2_ws
latest=$(ls -dt 20* | head -n 1)
echo "$latest"
```

### 看 interventions

```bash
cd /home/zihang/ros2_ws
latest=$(ls -dt 20* | head -n 1)
sed -n '1,20p' "$latest/interventions.csv"
```

### 看是否有图像

```bash
cd /home/zihang/ros2_ws
latest=$(ls -dt 20* | head -n 1)
ls "$latest/camera_head" | wc -l
ls "$latest/camera_left_wrist" | wc -l
ls "$latest/camera_right_wrist" | wc -l
```

### 看主要 csv 是否生成

```bash
cd /home/zihang/ros2_ws
latest=$(ls -dt 20* | head -n 1)
ls "$latest"
```

你应能看到类似：

- `joint_states.csv`
- `left_ee_data.csv`
- `right_ee_data.csv`
- `left_ee_target_data.csv`
- `right_ee_target_data.csv`
- `left_gripper_command.csv`
- `right_gripper_command.csv`
- `interventions.csv`

---

## 如果还有异常，优先看哪三种现象

### 1. 只启动推理就又开始录制

说明你还在跑旧版 `multi_subscriber`，或者旧进程没关。

### 2. 按 `右侧键 + A` 还是停止录制

说明当前运行的仍然不是新 install 版本。

### 3. 录制没停，但 `interventions.csv` 没变化

说明 `15/16` 没正确送到数采节点，或者你按键时序不是 PDF 里的组合键。

---

如果你愿意，我下一步可以继续帮你做一件更直接的事：  
你跑完一条最小流程后，把最新 episode 目录名发我，我帮你继续检查这条数据里的 `interventions.csv` 和目录结构是否完全符合预期。

---

**User**

我需要你把混淆的那个给删了，然后把这个项目文件该清的清了，不要混淆

---

**Cursor**

我先确认两个同名包各自所在位置、当前仓库边界和哪些文件是重复来源，然后只删除真正造成混淆的那一份，避免误删你要保留的项目。

我已经确认两个同名包都在同一个 git 仓库里，真正导致 `colcon` 重名冲突的是这两份并存。现在再看一下这两个目录的角色，我会保留 ROS 工作区标准的那一份，只删掉重复副本。

我准备删除的是重复副本 `ros2_data_collection/src/multi_subscriber`，保留工作区真正用于构建和运行的 `src/multi_subscriber`。先把这个重复目录里的文件清单拉出来，再一次性删掉，避免只删一半。

我现在开始清掉这个重复副本的文件。删完后，这个嵌套目录就不再是一个可构建的 ROS 包，也不会再和真正的 `src/multi_subscriber` 混淆。

重复包的关键文件已经删掉了。我再做一次快速检查，确认工作区里只剩一份 `multi_subscriber` 包，并把当前仓库状态整理给你。

我再补一层验证：现在从工作区根目录直接构建一次 `multi_subscriber`，确认已经不会再报“Duplicate package names”。

已经清掉了会造成混淆的那一份。

我保留了真正用于构建和运行的 `src/multi_subscriber`，删除了重复副本 `ros2_data_collection/src/multi_subscriber` 里的包文件。现在仓库里只剩一份 `multi_subscriber` 包，`colcon list --base-paths src` 也只识别到这一份，直接在 `/home/zihang/ros2_ws` 下执行 `colcon build --packages-select multi_subscriber` 已经通过，不会再报重名冲突。

这次实际清理掉的是重复包的核心文件，包括 `package.xml`、`setup.py`、`setup.cfg`、`multi_subscriber/multi_subscriber.py`、`multi_subscriber_sim_real.py`、测试文件和对齐脚本，所以 `ros2_data_collection/src/multi_subscriber` 已经不再是一个可构建 ROS 包。当前 git 状态里和这次清理相关的就是这些删除项，以及你保留的有效版本 `src/multi_subscriber/multi_subscriber/multi_subscriber.py` 的修改。

我没有动你的数据目录和 `20260520_101307_091813.zip`，避免误删采集产物。如果你也想把这些历史产物一起清掉，我可以再按你的范围继续清理。现在要生效的话，重新执行一次：

```bash
cd /home/zihang/ros2_ws
source /opt/ros/jazzy/setup.bash
source install/setup.bash
```

然后再启动你的数采和推理节点即可。

---

**User**

那我之前做cd ~/ros2_ws
colcon build
source install/setup.bash
ros2 run multi_subscriber multi_topic_subscriber 
是否是/home/zihang/ros2_ws/src/multi_subscriber/@src/multi_subscriber/multi_subscriber/multi_subscriber.py ？目前的项目文件是不是没有任何意义

---

**Cursor**

是的，你之前这套流程：

```bash
cd ~/ros2_ws
colcon build
source install/setup.bash
ros2 run multi_subscriber multi_topic_subscriber
```

**实际生效的源文件就是**：

`/home/zihang/ros2_ws/src/multi_subscriber/multi_subscriber/multi_subscriber.py`

不是之前那个嵌套副本。

原因很直接：

1. `colcon build` 在 `~/ros2_ws` 下默认扫描工作区里的包。
2. 现在工作区里只剩一个有效的 `multi_subscriber` 包：`/home/zihang/ros2_ws/src/multi_subscriber`
3. `source install/setup.bash` 后，`ros2 run multi_subscriber multi_topic_subscriber` 会从 `install` 里的已安装包入口启动，而这个入口就是由上面那份 `src/multi_subscriber/...` 构建出来的。

所以对你当前运行链路来说，真正有意义的是：

- `~/ros2_ws/src/multi_subscriber/...`：**有效源码**
- `~/ros2_ws/install/...`：**构建产物**
- `ros2 run ...`：**运行 install 里对应入口**

---

至于你问的“目前的项目文件是不是没有任何意义”：

如果你说的是这个目录：

`/home/zihang/ros2_ws/ros2_data_collection`

那**对当前 ROS 构建和运行来说，基本已经没有意义了**。更准确地说：

- **对 `colcon build` / `ros2 run` 来说**：现在它已经**不参与**了
- **对当前 Cursor 聊天工作区来说**：它还只是一个你打开着的目录
- **对代码真实生效来说**：以后你再改这里，不会影响 `ros2 run multi_subscriber multi_topic_subscriber`

也就是说，**它现在最大的“意义”只剩下一个历史副本/空壳工作区**，不再是运行源。

---

你以后如果要改真正生效的数采代码，应该改这里：

`/home/zihang/ros2_ws/src/multi_subscriber/multi_subscriber/multi_subscriber.py`

而不是：

`/home/zihang/ros2_ws/ros2_data_collection/src/multi_subscriber/...`

---

我建议你下一步做一件事，避免以后再次混淆：

把 Cursor 打开的项目根目录切到：

`/home/zihang/ros2_ws`

或者更直接切到：

`/home/zihang/ros2_ws/src/multi_subscriber`

这样以后你看到的、改到的、运行的就是同一份代码。

如果你愿意，我可以继续帮你把 `ros2_data_collection` 这个空壳目录也一起彻底整理掉，或者告诉你哪些还能留、哪些可以安全删除。