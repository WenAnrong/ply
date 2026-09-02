# ply 服务器面板

一个轻量的 Flask 服务器管理面板，提供系统资源监控、在线终端、账号与安全设置等功能。前端使用原生 `htmx` 做局部刷新，终端基于 `WebSocket + tmux` 实现持久会话。

## 功能特性

- **仪表盘**：实时展示 CPU、内存、交换分区、磁盘使用率，以及主机名、系统发行版、内核、IP、开机时长等信息（通过 `htmx` 定时轮询 `/stats` 局部更新）。
- **在线终端**：浏览器内 Web 终端，基于 `flask-sock` WebSocket + `pty` + `tmux`，断线后会话保留、重连可恢复。
- **账号管理**：注册 / 登录 / 退出；首次部署通过 `/register` 创建第一个管理员；登录后仅管理员可改密码。
- **安全设置**：CSRF 保护（Flask-WTF）、密码哈希存储、未登录全局拦截。
- **主题**：响应式侧边栏布局，支持移动端。

## 系统要求

- 已测试系统：**Debian 13**（同时支持 Ubuntu / CentOS / RockyLinux / openSUSE Leap）
- 需要 **Python ≥ 3.10**
- 需要系统包：`git`、`python3`、`python3-pip`、`tmux`（安装脚本会自动安装）

## 快速安装（生产部署）

### 一键运行（无需手动下载脚本）

在服务器上直接执行下面任一命令，会自动下载并运行安装脚本：

```bash
# 使用 curl
curl -fsSL https://raw.githubusercontent.com/WenAnrong/ply/main/install.sh | sudo bash

# 或使用 wget
wget -qO- https://raw.githubusercontent.com/WenAnrong/ply/main/install.sh | sudo bash
```

### 本地运行

如果你已经下载或克隆了仓库，也可以直接在项目根目录执行：

```bash
sudo bash install.sh
```

脚本会自动完成：

1. 识别发行版并选择包管理器（`apt` / `dnf` / `yum` / `zypper`）；
2. 安装系统依赖（`git`、`python3`、`pip`、`tmux`）；
3. 检测 `python >= 3.10`（优先 `3.13/3.12/3.11/3.10/python3`）；
4. 从仓库克隆源码到 `/opt/ply`；
5. 创建虚拟环境并安装 `requirements.txt` 依赖，以及 `gunicorn`；
6. 创建服务用户 `ply`，并准备数据目录 `/var/lib/ply` 与配置目录 `/etc/ply`；
7. 生成 systemd 服务 `ply.service`，开机自启并立即启动；
8. 为服务用户 `ply` 配置免密 sudo，Web 终端内可直接执行 `sudo` 命令。

安装完成后通过浏览器访问：`http://<服务器IP>:8000`

> 首次使用：打开 `/register` 页面创建第一个管理员账户（数据库为空时才允许注册）。

### 安装脚本参数

可通过环境变量覆盖默认值：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `PLY_REPO_URL` | `https://github.com/WenAnrong/ply` | 源码仓库地址 |
| `PLY_INSTALL_DIR` | `/opt/ply` | 安装目录 |
| `PLY_USER` | `ply` | 运行服务用户 |
| `PLY_SERVICE_NAME` | `ply` | systemd 服务名 |
| `PLY_PORT` | `8000` | 监听端口 |
| `PLY_BIND` | `0.0.0.0:<PLY_PORT>` | 完整监听地址 |
| `PLY_WORKERS` | `1` | gunicorn worker 数 |
| `PLY_THREADS` | `50` | gunicorn 线程数 |
| `PLY_PYTHON` | 自动检测 | 指定 Python 解释器 |
| `PLY_SUDO` | `1` | 为服务用户配置免密 sudo；设为 `0` 关闭 |

示例：

```bash
sudo PLY_PORT=9000 PLY_BIND=0.0.0.0:9000 bash install.sh
```

## 卸载

### 一键卸载（无需手动下载脚本）

```bash
# 使用 curl（PLY_YES=1 跳过二次确认）
curl -fsSL https://raw.githubusercontent.com/WenAnrong/ply/main/uninstall.sh | sudo PLY_YES=1 bash

# 或使用 wget
wget -qO- https://raw.githubusercontent.com/WenAnrong/ply/main/uninstall.sh | sudo PLY_YES=1 bash
```

> 通过管道执行时 `read` 无法交互，故默认跳过二次确认；本地直接运行脚本则仍会有确认提示。

### 本地运行

```bash
sudo bash uninstall.sh
```

会停止并禁用服务、删除 systemd 单元、数据目录 `/var/lib/ply`、配置目录 `/etc/ply`、安装目录 `/opt/ply` 及服务用户 `ply`。

## 生产部署说明

生产模式由 systemd 服务单元注入 `FLASK_CONFIG=production` 触发，对应 `config.py` 中的 `ProductionConfig`：

- 数据库：`/var/lib/ply/ply.db`
- 配置文件：`/etc/ply/config.ini`
- 首次启动自动生成随机 `SECRET_KEY`（由应用自身完成）

WebSocket 终端需使用支持其升级的 WSGI 服务器。本项目采用 `gunicorn` 的**线程型 worker**（`--threads`），因为每个活跃 WebSocket 会话会占用一个线程；不要使用 `gevent`/`eventlet`（会 monkey-patch 线程，导致本项目的 `threading.Thread` + 阻塞 `os.read` 行为异常）。

**终端权限**：默认服务以 `ply` 用户运行，但安装脚本会为 `ply` 配置免密 sudo，因此 Web 终端内可直接执行 `sudo` 命令（如 `sudo apt update`、`sudo systemctl restart xxx`）。如需关闭，可在安装时设 `PLY_SUDO=0`。

常用命令：

```bash
systemctl status ply       # 查看状态
systemctl restart ply      # 重启
journalctl -u ply -f       # 查看日志
```

若要对外提供 HTTPS，建议用 Nginx/Caddy 反向代理到 `0.0.0.0:8000` 上游，同时将 `SESSION_COOKIE_SECURE` 置为 `True`。

## 更新部署

代码更新并推送到仓库后，用下面任一方式更新服务器上的服务（不会清空数据库与配置）。

### 方式一：重跑安装脚本（推荐）

`install.sh` 是幂等的，重复运行会自动 `git pull` 最新代码、重装依赖并重启服务：

```bash
# 本地或已在服务器上
sudo bash install.sh

# 或远端一键
curl -fsSL https://raw.githubusercontent.com/WenAnrong/ply/main/install.sh | sudo bash
```

### 方式二：手动更新

```bash
cd /opt/ply
sudo git pull --ff-only
sudo /opt/ply/.venv/bin/pip install -r requirements.txt   # 依赖有变化时才需要
sudo systemctl restart ply
sudo journalctl -u ply -f                                 # 观察日志确认正常
```

## 开发环境

### 前置

```bash
sudo apt install python3 python3-venv tmux   # Debian/Ubuntu，其他发行版用对应包管理器
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 配置 `.flaskenv`

在项目根目录创建 `.flaskenv`：

```bash
FLASK_CONFIG=development  # 使用开发环境配置
FLASK_DEBUG=True          # 启动 debug 模式
```

### 启动

```bash
flask run
```

开发配置（`DevelopmentConfig`）会将数据库与 `config.ini` 放在项目 `tmp/` 下，例如 `tmp/ply.db`、`tmp/config.ini`。当前终端使用后会自动清理，无需额外处理。

## 配置说明

`config.py` 通过环境变量 `FLASK_CONFIG` 在 `CONFIG_MAP` 中切换环境：

| 环境 | `FLASK_CONFIG` | 数据库 | 配置文件 |
|------|----------------|--------|----------|
| 开发 | `development` | `tmp/ply.db` | `tmp/config.ini` |
| 生产 | `production` | `/var/lib/ply/ply.db` | `/etc/ply/config.ini` |

`config.ini`（INI 格式）示例：

```ini
[secret]
secret_key = <随机生成的密钥>
```

应用首次启动时若 `config.ini` 不存在会自动生成随机 `SECRET_KEY`。

## 目录结构

```
├── app.py                 # Flask 应用入口、扩展初始化、全局登录拦截
├── config.py              # 环境配置（development / production）与 config.ini 处理
├── models.py              # SQLAlchemy 数据模型（User）
├── requirements.txt       # Python 依赖
├── install.sh             # 一键安装脚本
├── uninstall.sh           # 卸载脚本
├── static/                # 静态资源（CSS / JS / 图片）
├── templates/             # Jinja2 模板（含 partials/ 局部片段）
└── views/                 # 蓝图：dashboard / terminal / auth / setting / about
```

## 常见问题

- **首次注册后无法再注册**：设计如此——仅当数据库无用户时才开放 `/register`，用于创建初始管理员。
- **终端连接失败**：请确认部署在支持 WebSocket 的服务（`gunicorn --threads`）下，且 `tmux` 已安装。
- **服务启动失败**：执行 `journalctl -u ply -e` 查看日志，常见原因为端口占用或 `/var/lib/ply`、`/etc/ply` 权限不对。
