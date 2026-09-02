"""
Docker 管理模块
"""

import json
import os
import subprocess

from flask import Blueprint
from flask import current_app
from flask import flash
from flask import redirect
from flask import render_template
from flask import request
from flask import url_for
from flask_login import login_required

from models import Setting
from models import db

docker_bp = Blueprint("docker", __name__)

# Docker 守护进程配置文件
DOCKER_DAEMON_JSON = "/etc/docker/daemon.json"


def _read_daemon_json():
    """读取 daemon.json，返回 (展示文本, 文件是否存在, 错误信息)。"""
    if not os.path.exists(DOCKER_DAEMON_JSON):
        return "{}", False, None
    try:
        with open(DOCKER_DAEMON_JSON, "r", encoding="utf-8") as f:
            raw = f.read()
        # 解析后重新格式化，方便编辑
        parsed = json.loads(raw)
        return json.dumps(parsed, ensure_ascii=False, indent=2), True, None
    except json.JSONDecodeError as e:
        # 文件存在但不是合法 JSON：仍返回原文，让用户修复
        return raw, True, f"文件不是合法 JSON：{e}"
    except Exception as e:
        return "{}", True, f"读取失败：{e}"


def _sudo(args, stdin=None):
    """以服务用户通过免密 sudo 执行系统命令，避免卡在密码输入。"""
    return subprocess.run(
        ["sudo", "-n", *args],
        input=stdin,
        capture_output=True,
        text=True,
    )


def get_docker_data_dir():
    """当前生效的 Docker 数据目录：SQLite 设置 > config 默认值。"""
    return Setting.get("docker_data_dir") or current_app.config.get(
        "DOCKER_DATA_DIR", "/var/lib/ply/docker"
    )


def ensure_docker_data_dir():
    """确保 Docker 数据目录存在，返回该路径。"""
    path = get_docker_data_dir()
    os.makedirs(path, exist_ok=True)
    return path


@docker_bp.route("/docker")
@login_required
def index():
    return render_template("docker.html", active_tab="services")


@docker_bp.route("/docker/services")
@login_required
def services():
    return render_template("docker.html", active_tab="services")


@docker_bp.route("/docker/images")
@login_required
def images():
    return render_template("docker.html", active_tab="images")


@docker_bp.route("/docker/settings")
@login_required
def settings():
    content, exists, err = _read_daemon_json()
    return render_template(
        "docker.html",
        active_tab="settings",
        docker_config=content,
        docker_config_exists=exists,
        docker_config_error=err,
        docker_config_path=DOCKER_DAEMON_JSON,
        docker_data_dir=get_docker_data_dir(),
        docker_data_dir_default=current_app.config.get(
            "DOCKER_DATA_DIR", "/var/lib/ply/docker"
        ),
    )


@docker_bp.route("/docker/settings/config", methods=["POST"])
@login_required
def save_docker_config():
    content = request.form.get("content", "")

    # 1. 校验 JSON 合法性
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as e:
        flash(f"配置不是合法的 JSON：{e}", "error")
        return redirect(url_for("docker.settings"))

    # 2. 备份原文件（文件不存在时跳过）
    _sudo(["cp", DOCKER_DAEMON_JSON, DOCKER_DAEMON_JSON + ".bak"])

    # 3. 写入新配置（通过 stdin 交给 sudo tee，以 root 写）
    payload = json.dumps(parsed, ensure_ascii=False, indent=2) + "\n"
    write = _sudo(["tee", DOCKER_DAEMON_JSON], stdin=payload)
    if write.returncode != 0:
        flash("写入失败：" + (write.stderr or "权限不足"), "error")
        return redirect(url_for("docker.settings"))

    # 4. 重启 Docker 使配置生效
    restart = _sudo(["systemctl", "restart", "docker"])
    if restart.returncode != 0:
        flash("配置已保存，但 Docker 重启失败：" + (restart.stderr or ""), "error")
    else:
        flash("Docker 配置已更新并重启", "success")

    return redirect(url_for("docker.settings"))


@docker_bp.route("/docker/settings/data-dir", methods=["POST"])
@login_required
def save_docker_data_dir():
    value = request.form.get("data_dir", "").strip()

    # 校验：不能为空、必须是绝对路径
    if not value:
        flash("目录不能为空", "error")
        return redirect(url_for("docker.settings"))
    if not os.path.isabs(value):
        flash("目录必须是绝对路径", "error")
        return redirect(url_for("docker.settings"))

    # 尝试创建目录，写不进去直接报错，避免留一个不可用路径
    try:
        os.makedirs(value, exist_ok=True)
    except Exception as e:
        flash(f"无法创建目录：{e}", "error")
        return redirect(url_for("docker.settings"))

    # upsert 到设置表
    setting = db.session.get(Setting, "docker_data_dir")
    if setting is None:
        setting = Setting(key="docker_data_dir", value=value)
        db.session.add(setting)
    else:
        setting.value = value
    db.session.commit()

    flash("Docker 数据目录已更新", "success")
    return redirect(url_for("docker.settings"))
