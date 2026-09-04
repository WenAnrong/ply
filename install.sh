#!/usr/bin/env bash
#
# ply 服务器面板 安装脚本（简化版）
# 只检测必需依赖并组装运行环境，不自动安装系统依赖或 Docker。
# 具体的系统依赖 / Docker 安装方式请参考 README.md。
#
# 用法(需要 root):
#   sudo bash install.sh
#
# 可用环境变量覆盖默认值:
#   PLY_REPO_URL     源码仓库地址
#   PLY_INSTALL_DIR  安装目录(默认 /opt/ply)
#   PLY_USER         运行服务用户(默认 ply)
#   PLY_SERVICE_NAME systemd 服务名(默认 ply)
#   PLY_PORT         监听端口(需在 12000-25000 之间；未配置时优先从 config.ini 读取，没有再随机生成)
#   PLY_PORT_MIN     端口范围下限(默认 12000)
#   PLY_PORT_MAX     端口范围上限(默认 25000)
#   PLY_BIND         完整监听地址(默认 0.0.0.0:<PORT>)
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
# 端口范围（12000-25000）
PORT_MIN="${PLY_PORT_MIN:-12000}"
PORT_MAX="${PLY_PORT_MAX:-25000}"
# 面板配置文件（存放端口等；生产环境为 /etc/ply/config.ini）
CONFIG_FILE="$CONFIG_DIR/config.ini"

log() { echo; echo "==> $*"; }
err() { echo "[错误] $*" >&2; exit 1; }

# ---------- root 检查 ----------
[[ $EUID -eq 0 ]] || err "请使用 root 运行: sudo bash $0"

# ---------- 必需依赖预检（缺失则无法继续，安装方式见 README.md） ----------
log "检查必需依赖"
MISSING=()
command -v git       >/dev/null 2>&1 || MISSING+=("git")
command -v tmux      >/dev/null 2>&1 || MISSING+=("tmux")
command -v sudo      >/dev/null 2>&1 || MISSING+=("sudo")
command -v systemctl >/dev/null 2>&1 || MISSING+=("systemctl")
if [[ ${#MISSING[@]} -gt 0 ]]; then
  err "缺少必需软件: ${MISSING[*]}，请先安装后再运行（安装方式见 README.md）"
fi
log "依赖检测通过 (git / tmux / sudo / systemctl)"

# ---------- Docker 检测（仅提示，不自动安装） ----------
log "检查 Docker（仅检测，不自动安装）"
if command -v docker >/dev/null 2>&1; then
  log "Docker 已安装: $(docker --version 2>&1)"
  if docker compose version >/dev/null 2>&1; then
    log "Docker Compose v2 插件: $(docker compose version 2>&1 | head -n1)"
  elif command -v docker-compose >/dev/null 2>&1; then
    log "[警告] 仅检测到 Docker Compose v1 命令(docker-compose)，但面板使用 docker compose 插件。"
    log "       请安装 Docker Compose v2 插件（见 README.md），否则 Compose 项目管理不可用。"
  else
    log "[警告] 未检测到 Docker Compose 插件，面板的 Compose 项目管理功能不可用（见 README.md）。"
  fi
else
  log "[警告] 未检测到 Docker，面板可继续安装，但 Docker 管理页面不可用（见 README.md）。"
fi

# ---------- Caddy 检测（仅提示，不自动安装） ----------
log "检查 Caddy（仅检测，不自动安装）"
if command -v caddy >/dev/null 2>&1; then
  log "Caddy 已安装: $(caddy version 2>&1 | head -n1)"
  log "启用并启动 Caddy 服务"
  sudo systemctl enable --now caddy
else
  log "[警告] 未检测到 Caddy，面板可继续安装，但网站管理功能不可用（见 README.md）。"
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
PYTHON="$(find_python)" || err "未找到 Python >= 3.10。请先安装 Python 3.10+（安装方式见 README.md）"
log "使用 Python: $("$PYTHON" --version 2>&1)"

# ---------- 端口解析 & 写入配置文件（范围 12000-25000） ----------
log "解析端口（范围 ${PORT_MIN}-${PORT_MAX}）"
# 端口优先级：PLY_BIND > PLY_PORT > config.ini 已有值 > 随机生成
SRC_PORT=""
if [[ -n "${PLY_BIND:-}" ]]; then
  SRC_PORT="${PLY_BIND##*:}"
elif [[ -n "${PLY_PORT:-}" ]]; then
  SRC_PORT="$PLY_PORT"
fi

resolve_port() {
  "$PYTHON" - "$CONFIG_FILE" "$SRC_PORT" "$PORT_MIN" "$PORT_MAX" <<'PYEOF'
import configparser, os, random, secrets, sys
path, explicit, pmin, pmax = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
parser = configparser.ConfigParser()
exists = os.path.exists(path)
if exists:
    try:
        parser.read(path, encoding="utf-8")
    except Exception:
        parser = configparser.ConfigParser()
        exists = False

def _p(v):
    try:
        v = int(v)
    except (TypeError, ValueError):
        return None
    return v if pmin <= v <= pmax else None

# 保证 [secret] 段存在（与应用 config.py 首次生成行为一致）
if not parser.has_section("secret"):
    parser.add_section("secret")
    parser.set("secret", "secret_key", secrets.token_hex(32))

port = None
need_write = False
if explicit:
    port = _p(explicit)
    if port is None:
        sys.stderr.write("端口超出范围: %s\n" % explicit)
        sys.exit(1)
    need_write = True
elif parser.has_section("server") and parser.has_option("server", "port"):
    port = _p(parser.get("server", "port"))
    if port is None:
        port = random.randint(pmin, pmax)
        need_write = True
else:
    port = random.randint(pmin, pmax)
    need_write = True

# 首次创建 config.ini 时，需要把 [secret] 一并写回
if not exists:
    need_write = True

# 只在需要时写回（“没有再写”），避免每次安装都覆盖已有端口
if need_write:
    if not parser.has_section("server"):
        parser.add_section("server")
    parser.set("server", "port", str(port))
    with open(path, "w", encoding="utf-8") as f:
        parser.write(f)

print(port)
PYEOF
}

PORT="$(resolve_port)" || err "端口无效或超出范围 ${PORT_MIN}-${PORT_MAX}"
BIND="${PLY_BIND:-0.0.0.0:$PORT}"
log "使用端口: $PORT（配置文件: $CONFIG_FILE）"

# 校验虚拟环境模块可用（某些发行版需额外安装 python3-venv）
if ! "$PYTHON" -c 'import venv, ensurepip' >/dev/null 2>&1; then
  err "$PYTHON 缺少 venv/ensurepip，无法创建虚拟环境。请安装 python3-venv（见 README.md）"
fi

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

# ---------- 终端历史：为服务用户启用 tmux 鼠标滚动 ----------
log "配置 $SERVICE_USER 的 tmux 鼠标滚动与历史"
USER_HOME="$(getent passwd "$SERVICE_USER" 2>/dev/null | cut -d: -f6 || true)"
USER_HOME="${USER_HOME:-/home/$SERVICE_USER}"
cat > "$USER_HOME/.tmux.conf" <<'EOF'
set -g mouse on
set -g history-limit 5000
EOF
chown "$SERVICE_USER:$SERVICE_USER" "$USER_HOME/.tmux.conf"

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

# ---------- 临时站点清理 timer（仅当安装了 Caddy） ----------
if command -v caddy >/dev/null 2>&1; then
  log "创建临时站点清理 systemd timer"
  cat > "/etc/systemd/system/${SERVICE_NAME}-temp-cleanup.service" <<EOF
[Unit]
Description=ply temp sites cleanup
After=network.target

[Service]
Type=oneshot
User=$SERVICE_USER
Group=$SERVICE_USER
WorkingDirectory=$INSTALL_DIR
Environment=FLASK_CONFIG=production
Environment=PYTHONUNBUFFERED=1
ExecStart=$INSTALL_DIR/.venv/bin/python scripts/cleanup_temp_sites.py
EOF
  cat > "/etc/systemd/system/${SERVICE_NAME}-temp-cleanup.timer" <<EOF
[Unit]
Description=Run ply temp sites cleanup every 5 minutes

[Timer]
OnBootSec=5min
OnUnitActiveSec=5min
Unit=${SERVICE_NAME}-temp-cleanup.service

[Install]
WantedBy=timers.target
EOF
  systemctl daemon-reload
  systemctl enable --now "${SERVICE_NAME}-temp-cleanup.timer"
fi

# ---------- 输出 ----------
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
PUBLIC_IP="$(curl -s --max-time 5 ifconfig.me 2>/dev/null || true)"
PORT="${BIND##*:}"
log "安装完成"
echo "  访问地址:   http://${IP:-<服务器IP>}:$PORT"
if [[ -n "$PUBLIC_IP" ]]; then
  echo "  公网访问:   http://$PUBLIC_IP:$PORT"
fi
echo "  首次使用:   打开 /register 页面创建第一个管理员账户"
echo "  服务管理:   systemctl status|restart|stop $SERVICE_NAME"
echo "  日志查看:   journalctl -u $SERVICE_NAME -f"
echo "  如需 HTTPS: 建议用 Caddy 反代上游 $BIND"
echo
