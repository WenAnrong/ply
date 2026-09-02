#!/usr/bin/env bash
#
# ply 服务器面板 一键安装脚本
# 支持发行版: Debian / Ubuntu / CentOS / RockyLinux / openSUSE Leap
#
# 用法(需要 root):
#   sudo bash install.sh
#
# 可用环境变量覆盖默认值:
#   PLY_REPO_URL     源码仓库地址
#   PLY_INSTALL_DIR  安装目录(默认 /opt/ply)
#   PLY_USER         运行服务用户(默认 ply)
#   PLY_SERVICE_NAME systemd 服务名(默认 ply)
#   PLY_PORT         监听端口(默认 8000)
#   PLY_BIND         完整监听地址(默认 0.0.0.0:<PLY_PORT>)
#   PLY_WORKERS      gunicorn worker 数(默认 1)
#   PLY_THREADS      gunicorn 线程数(默认 50)
#   PLY_PYTHON       指定 Python 解释器
#   PLY_SUDO         为服务用户配置免密 sudo(默认 1, 设 0 关闭)
#
set -euo pipefail

# ---------- 可配置参数 ----------
REPO_URL="${PLY_REPO_URL:-https://github.com/WenAnrong/ply}"
INSTALL_DIR="${PLY_INSTALL_DIR:-/opt/ply}"
SERVICE_USER="${PLY_USER:-ply}"
SERVICE_NAME="${PLY_SERVICE_NAME:-ply}"
DATA_DIR="${PLY_DATA_DIR:-/var/lib/ply}"
CONFIG_DIR="${PLY_CONFIG_DIR:-/etc/ply}"
WORKERS="${PLY_WORKERS:-1}"
THREADS="${PLY_THREADS:-50}"
if [[ -n "${PLY_BIND:-}" ]]; then
  BIND="$PLY_BIND"
else
  BIND="0.0.0.0:${PLY_PORT:-8000}"
fi

log() { echo; echo "==> $*"; }
err() { echo "[错误] $*" >&2; exit 1; }

# ---------- root 检查 ----------
[[ $EUID -eq 0 ]] || err "请使用 root 运行: sudo bash $0"

# ---------- 发行版识别 ----------
[[ -r /etc/os-release ]] || err "无法读取 /etc/os-release"
# shellcheck disable=SC1091
. /etc/os-release
PKG_MANAGER=""
case "${ID:-}" in
  debian | ubuntu) PKG_MANAGER="apt-get" ;;
  centos | rocky | rhel | almalinux | fedora)
    if command -v dnf >/dev/null 2>&1; then PKG_MANAGER="dnf"; else PKG_MANAGER="yum"; fi
    ;;
  opensuse-leap | opensuse | sles | suse) PKG_MANAGER="zypper" ;;
esac
# 依据 ID_LIKE 兜底
if [[ -z "$PKG_MANAGER" ]]; then
  case "${ID_LIKE:-}" in
    *debian*) PKG_MANAGER="apt-get" ;;
    *rhel* | *fedora*)
      if command -v dnf >/dev/null 2>&1; then PKG_MANAGER="dnf"; else PKG_MANAGER="yum"; fi
      ;;
    *suse*) PKG_MANAGER="zypper" ;;
  esac
fi
[[ -n "$PKG_MANAGER" ]] || err "不支持的发行版: ${ID:-unknown}"

log "检测到发行版: ${ID:-unknown} (${PRETTY_NAME:-?})"
log "使用包管理器: $PKG_MANAGER"

# ---------- 安装系统依赖 ----------
log "安装系统依赖 (git / python3 / pip / tmux / curl)"
case "$PKG_MANAGER" in
  apt-get)
    apt-get update -y
    DEBIAN_FRONTEND=noninteractive apt-get install -y git python3 python3-venv python3-pip tmux sudo curl
    ;;
  dnf)  dnf install -y git python3 python3-pip tmux sudo curl ;;
  yum)  yum install -y git python3 python3-pip tmux sudo curl ;;
  zypper) zypper --non-interactive install git python3 python3-pip tmux sudo curl ;;
esac

# ---------- 安装 Docker 与 Docker Compose ----------
log "检查 Docker 是否已安装"
if command -v docker >/dev/null 2>&1; then
  log "检测到 Docker 已安装，跳过安装脚本"
else
  log "安装 Docker 与 Docker Compose（官方 get.docker.com 脚本）"
  command -v curl >/dev/null 2>&1 || err "缺少 curl，无法使用 get.docker.com 脚本"
  curl -fsSL https://get.docker.com | sh
fi
# 确保 Docker 服务启动（systemd 环境）
if command -v systemctl >/dev/null 2>&1; then
  systemctl enable --now docker >/dev/null 2>&1 || true
fi
command -v docker >/dev/null 2>&1 || err "Docker 安装失败"
log "Docker 版本: $(docker --version 2>&1)"
if docker compose version >/dev/null 2>&1; then
  log "Docker Compose 版本: $(docker compose version 2>&1)"
else
  err "Docker Compose 插件未安装，请先自行安装"
fi

# ---------- 找到 Python >= 3.10 ----------
find_python() {
  if [[ -n "${PLY_PYTHON:-}" ]]; then
    command -v "$PLY_PYTHON" >/dev/null 2>&1 || err "找不到指定 Python: $PLY_PYTHON"
    "$PLY_PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
      || err "指定的 Python $PLY_PYTHON 低于 3.10"
    echo "$PLY_PYTHON"
    return
  fi
  local p
  for p in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$p" >/dev/null 2>&1; then
      if "$p" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
        echo "$p"
        return
      fi
    fi
  done
  return 1
}
PYTHON="$(find_python)" || err "未找到 Python >= 3.10。请先安装 Python 3.10+ (例如 Debian/Ubuntu: sudo apt install python3.11)"
log "使用 Python: $("$PYTHON" --version 2>&1)"

# ---------- 获取源码 ----------
log "获取源码: $REPO_URL"
if [[ -d "$INSTALL_DIR/.git" ]]; then
  git -C "$INSTALL_DIR" pull --ff-only
elif [[ -e "$INSTALL_DIR" ]]; then
  err "$INSTALL_DIR 已存在但不是 git 仓库，请先移除或设置 PLY_INSTALL_DIR"
else
  git clone --depth 1 "$REPO_URL" "$INSTALL_DIR"
fi

# ---------- 虚拟环境 & Python 依赖 ----------
log "创建虚拟环境并安装依赖"
"$PYTHON" -m venv "$INSTALL_DIR/.venv"
VENV_PY="$INSTALL_DIR/.venv/bin/python"
VENV_PIP="$INSTALL_DIR/.venv/bin/pip"
"$VENV_PY" -m ensurepip --upgrade >/dev/null 2>&1 || true
"$VENV_PIP" install --upgrade pip >/dev/null
"$VENV_PIP" install -r "$INSTALL_DIR/requirements.txt"
# WebSocket 场景建议线程型 worker(见 flask-sock 文档)，gunicorn 即可，无需 gevent/eventlet
"$VENV_PIP" install gunicorn

# ---------- 服务用户 & 数据/配置目录 ----------
log "创建服务用户 $SERVICE_USER"
if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
  useradd --system --create-home --home-dir "/home/$SERVICE_USER" --shell /bin/bash "$SERVICE_USER"
fi
mkdir -p "$DATA_DIR" "$CONFIG_DIR"
chown -R "$SERVICE_USER:$SERVICE_USER" "$DATA_DIR" "$CONFIG_DIR"
# 源码目录保持 root 所有，git(以 root 运行) 与属主一致，避免 "dubious ownership"
chown -R root:root "$INSTALL_DIR"

# ---------- 终端权限：允许服务用户免密 sudo ----------
if [[ "${PLY_SUDO:-1}" != "0" ]]; then
  log "为 $SERVICE_USER 配置免密 sudo（终端可直接执行 sudo 命令）"
  echo "$SERVICE_USER ALL=(ALL) NOPASSWD: ALL" > "/etc/sudoers.d/$SERVICE_USER"
  chmod 0440 "/etc/sudoers.d/$SERVICE_USER"
fi

# ---------- systemd 服务 ----------
log "创建 systemd 服务 $SERVICE_NAME"
cat > "/etc/systemd/system/$SERVICE_NAME.service" <<EOF
[Unit]
Description=ply server panel
After=network.target

[Service]
User=$SERVICE_USER
Group=$SERVICE_USER
WorkingDirectory=$INSTALL_DIR
Environment=FLASK_CONFIG=production
Environment=PYTHONUNBUFFERED=1
ExecStart=$INSTALL_DIR/.venv/bin/gunicorn -w $WORKERS --threads $THREADS -b $BIND app:app
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"
if systemctl is-active --quiet "$SERVICE_NAME"; then
  log "服务已启动"
else
  err "服务启动失败，请查看日志: journalctl -u $SERVICE_NAME -e"
fi

# ---------- 输出 ----------
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
PORT="${BIND##*:}"
log "安装完成"
echo "  访问地址:   http://${IP:-<服务器IP>}:$PORT"
echo "  首次使用:   打开 /register 页面创建第一个管理员账户"
echo "  服务管理:   systemctl status|restart|stop $SERVICE_NAME"
echo "  日志查看:   journalctl -u $SERVICE_NAME -f"
echo "  如需 HTTPS: 建议用 Nginx/Caddy 反代上游 $BIND"
echo
