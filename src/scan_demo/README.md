# 扫码 ROS 节点

## 状态机（/scan/success 固定 30Hz）

```
  0 ──[/scan/code + 已 arm]──► 1 ──[/ros2recordstop]──► 0
  ▲                           │
  │                           │ (同条码在视野内不会重复触发)
  └──[/scan/arm_next]─────────┘
       ↑ 移开/NG 后再扫
```

| Topic | 作用 |
|-------|------|
| `/scan/code` | 有效条码事件 |
| `/ros2recordstop` | 录包结束 → success 置 0 |
| `/scan/arm_next` | 读码流变化后允许下一次置 1（内部自动发） |
| `/scan/success` | 训练用 0/1 @ 30Hz |

## 启动 / 停止

```bash
bash stop_all.sh          # 先关旧节点（避免 0/1 跳动）
bash start_matrix220_ros2.sh
```

## 目录

```
scan_demo/
├── start_matrix220_ros2.sh
├── stop_all.sh
├── matrix220/   tcp.py, driver.py
├── ros/         scan_success_publisher.py, mock_scanner_ros2.py
└── netplan/
```

## 若 echo 在 0/1 间跳

```bash
ros2 topic info /scan/success -v   # 应只有 1 个 publisher
bash stop_all.sh && bash start_matrix220_ros2.sh
```

旧节点 `matrix220_scan_bridge` 必须关掉。

## 无扫码器

```bash
python3 ros/mock_scanner_ros2.py
```
