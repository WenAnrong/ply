#!/usr/bin/env bash
#
# ply 服务器面板 卸载脚本
#
# 用法(需要 root):
#   sudo bash uninstall.sh
#
# 可用环境变量覆盖默认值:
#   PLY_USER         服务用户(默认 ply)
#   PLY_SERVICE_NAME systemd 服务名(默认 ply)
#   PLY_INSTALL_DIR  安装目录(默认 /opt/ply)
#   PLY_DATA_DIR     数据目录(默认 /var/lib/ply)
#   PLY_CONFIG_DIR   配置目录(默认 /etc/ply)
#
set -euo pipefail

SERVICE_NAME="${PLY_SERVICE_NAME:-ply}"
SERVICE_USER="${PLY_USER:-ply}"
INSTALL_DIR="${PLY_INSTALL_DIR:-/opt/ply}"
DATA_DIR="${PLY_DATA_DIR:-/var/lib/ply}"
CONFIG_DIR="${PLY_CONFIG_DIR:-/etc/ply}"

err() { echo "[错误] $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || err "请使用 root 运行: sudo bash $0"

echo
echo "即将卸载 ply 面板，以下内容会被删除:"
echo "  服务      $SERVICE_NAME"
echo "  安装目录  $INSTALL_DIR"
echo "  数据目录  $DATA_DIR"
echo "  配置目录  $CONFIG_DIR"
if id -u "$SERVICE_USER" >/dev/null 2>&1; then
  echo "  服务用户  $SERVICE_USER (及其家目录)"
fi
if [[ "${PLY_YES:-}" != "1" ]]; then
  read -r -p "确认继续吗? 输入 yes 继续: " ans
  [[ "$ans" == "yes" ]] || { echo "已取消"; exit 0; }
fi

# 停止并禁用服务
echo "==> 停止并禁用服务"
systemctl stop "$SERVICE_NAME" 2>/dev/null || true
systemctl disable "$SERVICE_NAME" 2>/dev/null || true
rm -f "/etc/systemd/system/$SERVICE_NAME.service"
systemctl daemon-reload || true

# 删除目录
echo "==> 删除数据/配置/安装目录"
rm -rf "$DATA_DIR" "$CONFIG_DIR"
rm -rf "$INSTALL_DIR"

# 删除服务用户
if id -u "$SERVICE_USER" >/dev/null 2>&1; then
  echo "==> 删除服务用户 $SERVICE_USER"
  userdel -r "$SERVICE_USER" 2>/dev/null || true
fi

echo "docker不会被删除, 如果需要请手动删除"

echo "==> 卸载完成"
