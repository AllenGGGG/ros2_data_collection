# 扫码 ROS 节点

## 状态机（/scan/success 固定 30Hz）

```
  0 ──[/scan/code]──► 1 ──[/ros2recordstop]──► 0 ──[/scan/code 同码可再扫]──► 1
```

停录后会自动解锁；**同一条码**可连续多段采集，无需移开扫码器。

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

## 新主机 / USB hub 网络配置

扫码器默认地址是 `192.168.10.104:51236`。新主机第一次使用时，先插好
hub/扫码器，然后安装一次持久化 NetworkManager 配置：

```bash
cd ~/ros2_data_collection_YWL/src/scan_demo
sudo bash netplan/install_matrix220_network.sh
```

脚本会优先自动选择 hub 上的 USB 以太网卡（常见名称如 `enx...`），给本机配置
`192.168.10.10/32`，并添加到扫码器 `192.168.10.104/32` 的专用路由。这样即使
WiFi 也在 `192.168.10.x` 网段，访问扫码器也会走 hub 上的有线网卡。

如果自动选择失败，先查看网卡名：

```bash
nmcli device status
```

然后手动指定，例如：

```bash
MATRIX220_IFACE=enx207bd51a38c5 sudo -E bash netplan/install_matrix220_network.sh
```

如果扫码器 IP 不是默认值：

```bash
MATRIX220_HOST=192.168.10.104 MATRIX220_IFACE=enx207bd51a38c5 sudo -E bash netplan/install_matrix220_network.sh
MATRIX220_HOST=192.168.10.104 bash start_matrix220_ros2.sh
```

旧入口 `netplan/install_eno1.sh` 仍可用，但现在会转到通用安装脚本，不再固定使用
`eno1`。

## 目录

```
scan_demo/
├── start_matrix220_ros2.sh
├── stop_all.sh
├── matrix220/   tcp.py, driver.py
├── ros/         scan_success_publisher.py, mock_scanner_ros2.py
└── netplan/
```

## 终端提示

扫码成功时终端会打印**绿色横幅**（带 **第 001、002… 次** 序号、时间、条码），每次与上次区分；录包停止为黄色一行提示。

## 若 echo 在 0/1 间跳

```bash
ros2 topic info /scan/success -v   # 应只有 1 个 publisher
bash stop_all.sh && bash start_matrix220_ros2.sh
```

旧节点 `matrix220_scan_bridge` 必须关掉。

## 若启动后运控报 Zenoh 时间戳错误

如果运控日志里出现类似：

```text
zenoh ... incoming timestamp ... exceeding delta 500ms is rejected
```

这通常不是扫码节点给机械臂发了控制命令，而是 ROS/Zenoh 参与通信的主机时间相差超过约
500ms。扫码节点加入 ROS 网络后会开始发布 `/scan/code`、`/scan/success`，如果任一主机时钟
漂移，Zenoh 可能拒收数据；随后运控侧可能因为通信/写周期异常触发 emergency brake。

在所有 ROS 主机上检查：

```bash
date -Ins
timedatectl status | sed -n '/System clock synchronized/p;/NTP service/p'
```

处理建议：

```bash
sudo timedatectl set-ntp true
sudo systemctl restart systemd-timesyncd 2>/dev/null || sudo systemctl restart chrony
```

等各机器 UTC 时间差小于 100ms 后，重新启动运控，再启动 `./quick.start 3`。

## 无扫码器

```bash
python3 ros/mock_scanner_ros2.py
```
