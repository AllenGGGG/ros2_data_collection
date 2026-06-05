#!/usr/bin/env bash
# 安装 eno1 静态 IP，供 Matrix 220（192.168.10.104）使用
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NETPLAN_SRC="${SCRIPT_DIR}/02-eno1-matrix220.yaml"
NETPLAN_DST="/etc/netplan/02-eno1-matrix220.yaml"

if [[ "${EUID}" -ne 0 ]]; then
  echo "请用 sudo 运行: sudo bash ${SCRIPT_DIR}/install_eno1.sh"
  exit 1
fi

cp "${NETPLAN_SRC}" "${NETPLAN_DST}"
chmod 600 "${NETPLAN_DST}"
# netplan 要求配置文件仅 root 可读
if [[ -f /etc/netplan/01-network-manager-all.yaml ]]; then
  chmod 600 /etc/netplan/01-network-manager-all.yaml
fi

# 避免与「有线连接 1」(DHCP) 抢 eno1
if nmcli -t -f NAME connection show | grep -qx '有线连接 1'; then
  nmcli connection modify '有线连接 1' connection.autoconnect no
  echo "已关闭「有线连接 1」自动连接，避免与 eno1 静态 IP 冲突"
fi

netplan generate
netplan apply || true

# NetworkManager 系统上 netplan 可能提示 systemd-networkd 未运行，可忽略
nmcli connection up eno1-matrix220 ifname eno1 2>/dev/null || true

echo
echo "完成。当前 eno1 状态:"
nmcli -f GENERAL.STATE,IP4.ADDRESS,IP4.GATEWAY device show eno1
echo
echo "测试:"
ping -I eno1 -c 2 -W 2 192.168.10.104
