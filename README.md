# ply 服务器面板

一个轻量的 Flask 服务器管理面板，提供系统资源监控、在线终端、Docker 管理、账号与安全设置等功能。

## 功能特性

- **仪表盘**：实时展示 CPU、内存、交换分区、磁盘使用率，以及主机名、系统发行版、内核、IP、开机时长等信息。
- **在线终端**：浏览器内 Web 终端，基于 `flask-sock` WebSocket + `pty` + `tmux`，断线后会话保留、重连可恢复。
- **Docker 管理**：三个子页——服务、镜像、设置。
- **网站管理**：编辑 Caddy 配置 + 临时站点（把本机端口通过 Caddy 暴露成临时访问地址，用后即关）。
- **设置**：系统配置、用户管理等。

## Docker 管理

Docker 模块遵循「**只读展示 + 快捷开关**」的轻量路线，创建、配置与拉取等操作交给面板内置终端。


## 网站管理（临时站点）

用于快速把一个**本机端口**通过 Caddy 暴露成一个临时访问地址，适合「临时用用、用完就关」的场景。

### 工作方式

- 面板维护自己的一份片段文件（默认 `/etc/caddy/ply-temp.caddy`），里面是**泛域名反向代理**规则：每个临时站点对应一个子域名 `<code>.<域名>`。
- **面板不会改动你的主 Caddyfile**。你只需在 Caddyfile 里为泛域名（`*.<域名>`）创建 site 块并 `import` 该片段即可。
- 泛域名基准域名**无需配置**，面板会自动从主 Caddyfile 中识别：找到包含 `import /etc/caddy/ply-temp.caddy` 的 site 块，取其地址（如 `*.example.com`）提取为 `example.com`。
- 临时站点有有效期（TTL，默认 24 小时），到期后自动下线并从片段移除；也可在面板手动「关闭」。**下线后该子域名即不可访问**。
- 过期记录保留最新 1 条用于追溯，其余删除。

### 一次性配置（在你的 Caddyfile 中）

为泛域名创建 site 块，并在其中 `import` 面板的片段，同时让未匹配的子域名返回空响应：

**HTTPS（默认，Caddy 自动申请证书）：**

```caddy
*.example.com {
    # 你自己的长期服务配置（若有）

    import /etc/caddy/ply-temp.caddy
    respond "" 204
}
```

**HTTP（不想要 HTTPS）：** 在站点地址前加 `http://`，Caddy 只监听 80 端口、不申请证书。

```caddy
http://*.example.com {
    # 你自己的长期服务配置（若有）

    import /etc/caddy/ply-temp.caddy
    respond "" 204
}
```

- `import /etc/caddy/ply-temp.caddy`：引入面板生成的临时站点路由（每个站点一个 `@<code> host`，转发到对应端口）。
- `respond "" 204`：让未匹配的子域名返回空响应。

使用前请确保：
- DNS 已添加 `*.example.com` 的泛域名解析并指向本机；
- 若用 HTTPS，Caddy 需能为通配符或子域名签发证书（通配符证书需要 DNS-01 挑战）；HTTP 无需证书。

改完后执行 `caddy reload`（或 `systemctl restart caddy`）。

### 如何使用

1. 进入面板「网站 → 临时站点」。
2. 填写本机端口并选择有效期，点击「创建」。
3. 面板生成一个随机子域名 `<code>.<域名>`，并写入片段文件。
4. 通过 `https://<code>.<域名>` 访问即可。
5. 用完点「关闭」，或等 TTL 到期自动下线。

### 后台清理

生产安装时，若检测到 Caddy，`install.sh` 会创建一个 **systemd timer**（默认每 5 分钟）运行 `scripts/cleanup_temp_sites.py`，自动把过期站点下线并更新片段与 Caddy。

> 注意：`caddy reload` 需要 Caddy 的 admin API（默认 localhost:2019）在线；若你关闭了 admin API，`reload` 会失败，可改用 `systemctl restart caddy`（会有短暂停机）。


## 系统要求

- 已测试系统：**Debian 13**（支持带有 `Python ≥ 3.10` 和 `systemd` 的发行版）
- 需要 **Python ≥ 3.10**，且包含 `venv` / `ensurepip`（部分发行版需额外安装 `python3-venv`）
- 需要系统包：`git`、`tmux`、`sudo`、以及 systemd 的 `systemctl`（**这些需你手动安装**，安装脚本只检测、不自动安装）
- **Docker + Docker Compose v2 插件为可选**，用于面板的 Docker 管理功能；未安装时面板仍可运行，只是 Docker 页面不可用（**Docker 同样需你手动安装**）
- **Caddy 为可选**，用于面板的网站管理功能；未安装时面板仍可运行，只是网站管理页面不可用（**Caddy 同样需你手动安装**）

## 快速安装（生产部署）

### 1. 安装依赖（手动）

安装脚本**不会**自动安装系统依赖与 Docker，请先按你的发行版安装：

```bash
# Debian / Ubuntu
sudo apt update && sudo apt install -y git python3 python3-venv tmux sudo

# CentOS / Rocky / AlmaLinux
sudo dnf install -y git python3 python3-pip tmux sudo

# openSUSE Leap
sudo zypper --non-interactive install git python3 python3-pip tmux sudo
```

> 需要 **Python ≥ 3.10** 且含 `venv`/`ensurepip`；Debian 12/13、Ubuntu 22.04+ 默认即满足。版本过低请先升级 Python。

**docker 与 docker-compose 插件可以用官方脚本一键安装：**

```bash
# Docker 官方安装脚本（仅安装 Docker 与 Compose 插件）
curl -fsSL https://get.docker.com | sudo bash
```

**caddy 安装：**

按照官方文档进行安装：https://caddyserver.com.cn/docs/install#debian-ubuntu-raspbian


### 2. 一键运行（无需手动下载脚本）

在服务器上直接执行下面任一命令，会自动下载并运行安装脚本：

```bash
# 使用 curl
curl -fsSL https://raw.githubusercontent.com/WenAnrong/ply/main/install.sh | sudo bash

# 或使用 wget
wget -qO- https://raw.githubusercontent.com/WenAnrong/ply/main/install.sh | sudo bash
```

默认配置安装完成后通过浏览器访问：`http://<服务器IP>:8000`

### 安装脚本参数（高级配置）

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

如果你克隆了源码可以用这个命令：

```bash
sudo bash uninstall.sh
```

会停止并禁用服务、删除 systemd 单元、数据目录 `/var/lib/ply`、配置目录 `/etc/ply`、安装目录 `/opt/ply` 及服务用户 `ply`。

## 生产部署说明

生产模式由 systemd 服务单元注入 `FLASK_CONFIG=production` 触发，对应 `config.py` 中的 `ProductionConfig`：

- 数据库：`/var/lib/ply/ply.db`
- 配置文件：`/etc/ply/config.ini`
- 首次启动自动生成随机 `SECRET_KEY`（由应用自身完成）

**终端权限**：默认服务以 `ply` 用户运行，但安装脚本会为 `ply` 配置免密 sudo，因此 Web 终端内可直接执行 `sudo` 命令（如 `sudo apt update`、`sudo systemctl restart xxx`）。如需关闭，可在安装时设 `PLY_SUDO=0`。

常用命令：

```bash
systemctl status ply       # 查看状态
systemctl restart ply      # 重启
journalctl -u ply -f       # 查看日志
```

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

## 常见问题

- **首次注册后无法再注册**：设计如此——仅当数据库无用户时才开放 `/register`，用于创建初始管理员。
