#!/usr/bin/env bash
# Install a persistent NetworkManager profile for a Matrix 220 scanner.
#
# Defaults:
#   scanner: 192.168.10.104
#   local:   192.168.10.10/32
#
# Override examples:
#   MATRIX220_IFACE=enx207bd51a38c5 sudo bash install_matrix220_network.sh
#   MATRIX220_HOST=192.168.20.104 MATRIX220_LOCAL_CIDR=192.168.20.10/32 sudo -E bash install_matrix220_network.sh
set -euo pipefail

CONN_NAME="${MATRIX220_CONN_NAME:-matrix220-scanner}"
SCANNER_HOST="${MATRIX220_HOST:-192.168.10.104}"
LOCAL_CIDR="${MATRIX220_LOCAL_CIDR:-192.168.10.10/32}"
IFACE="${MATRIX220_IFACE:-}"
DRY_RUN="${MATRIX220_DRY_RUN:-0}"

die() {
  echo "错误: $*" >&2
  exit 1
}

run() {
  if [[ "${DRY_RUN}" == "1" ]]; then
    printf '[dry-run]'
    printf ' %q' "$@"
    printf '\n'
    return 0
  fi
  "$@"
}

has_global_ipv4() {
  ip -o -4 addr show dev "$1" scope global 2>/dev/null | grep -q .
}

ethernet_devices() {
  nmcli -t -f DEVICE,TYPE device status |
    awk -F: '$2 == "ethernet" && $1 != "" { print $1 }'
}

select_iface() {
  local candidates=()
  local dev

  while IFS= read -r dev; do
    [[ -n "${dev}" ]] || continue
    if [[ "${dev}" == enx* ]] && ! has_global_ipv4 "${dev}"; then
      candidates+=("${dev}")
    fi
  done < <(ethernet_devices)

  if [[ "${#candidates[@]}" -eq 1 ]]; then
    echo "${candidates[0]}"
    return 0
  fi

  candidates=()
  while IFS= read -r dev; do
    [[ -n "${dev}" ]] || continue
    if ! has_global_ipv4 "${dev}"; then
      candidates+=("${dev}")
    fi
  done < <(ethernet_devices)

  if [[ "${#candidates[@]}" -eq 1 ]]; then
    echo "${candidates[0]}"
    return 0
  fi

  echo "无法自动确定扫码器网卡。" >&2
  echo "当前以太网设备:" >&2
  nmcli -f DEVICE,TYPE,STATE device status | sed 's/^/  /' >&2
  echo >&2
  echo "请指定网卡后重试，例如:" >&2
  echo "  MATRIX220_IFACE=enx207bd51a38c5 sudo -E bash $0" >&2
  return 1
}

if [[ "${EUID}" -ne 0 && "${DRY_RUN}" != "1" ]]; then
  die "请用 sudo 运行: sudo -E bash $0"
fi

command -v nmcli >/dev/null 2>&1 || die "找不到 nmcli，请确认系统使用 NetworkManager"
command -v ip >/dev/null 2>&1 || die "找不到 ip 命令"

if [[ -z "${IFACE}" ]]; then
  IFACE="$(select_iface)"
fi

ip link show "${IFACE}" >/dev/null 2>&1 || die "网卡不存在: ${IFACE}"

echo "Matrix 220 网络配置:"
echo "  connection : ${CONN_NAME}"
echo "  interface  : ${IFACE}"
echo "  local IP   : ${LOCAL_CIDR}"
echo "  scanner IP : ${SCANNER_HOST}"
echo

if nmcli -t -f NAME connection show | grep -Fxq "${CONN_NAME}"; then
  run nmcli connection modify "${CONN_NAME}" \
    connection.interface-name "${IFACE}" \
    connection.autoconnect yes \
    connection.autoconnect-priority 100 \
    ipv4.method manual \
    ipv4.addresses "${LOCAL_CIDR}" \
    ipv4.routes "${SCANNER_HOST}/32 0.0.0.0" \
    ipv4.never-default yes \
    ipv4.route-metric 10 \
    ipv6.method disabled
else
  run nmcli connection add type ethernet \
    con-name "${CONN_NAME}" \
    ifname "${IFACE}" \
    connection.autoconnect yes \
    connection.autoconnect-priority 100 \
    ipv4.method manual \
    ipv4.addresses "${LOCAL_CIDR}" \
    ipv4.routes "${SCANNER_HOST}/32 0.0.0.0" \
    ipv4.never-default yes \
    ipv4.route-metric 10 \
    ipv6.method disabled
fi

run nmcli connection up "${CONN_NAME}" ifname "${IFACE}"

echo
echo "已安装持久化配置。以后这台主机插上该 hub/扫码器后会自动使用 ${CONN_NAME}。"

if [[ "${DRY_RUN}" == "1" ]]; then
  exit 0
fi

echo
echo "当前状态:"
nmcli -f GENERAL.DEVICE,GENERAL.STATE,IP4.ADDRESS,IP4.ROUTE device show "${IFACE}"

echo
echo "测试扫码器连通性:"
if ping -I "${IFACE}" -c 2 -W 2 "${SCANNER_HOST}"; then
  echo "扫码器网络正常。"
else
  echo "未 ping 通 ${SCANNER_HOST}。请检查扫码器电源、网线、hub、扫码器 IP，或用 MATRIX220_HOST 指定实际 IP。" >&2
  exit 2
fi
