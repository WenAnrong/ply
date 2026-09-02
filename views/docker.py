"""
Docker 管理模块
"""

import json
import os
import subprocess

from flask import Blueprint
from flask import flash
from flask import redirect
from flask import render_template
from flask import request
from flask import url_for
from flask_login import login_required

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


def _list_images():
    """列出本地 Docker 镜像，返回 (列表, 错误信息)。"""
    r = _sudo(
        [
            "docker",
            "images",
            "--format",
            "{{.Repository}}:{{.Tag}}\t{{.ID}}\t{{.Size}}",
        ]
    )
    if r.returncode != 0:
        return [], r.stderr.strip() or "无法读取 Docker 镜像（请确认 Docker 是否可用）"
    images = []
    for line in r.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3:
            images.append({"name": parts[0], "id": parts[1], "size": parts[2]})
    return images, None


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
    image_list, err = _list_images()
    return render_template(
        "docker.html",
        active_tab="images",
        docker_images=image_list,
        docker_images_error=err,
    )


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


@docker_bp.route("/docker/images/delete", methods=["POST"])
@login_required
def delete_docker_image():
    ref = request.form.get("ref", "").strip()

    if not ref:
        flash("未选择镜像", "error")
        return redirect(url_for("docker.images"))

    r = _sudo(["docker", "rmi", ref])
    if r.returncode != 0:
        flash("删除失败：" + (r.stderr.strip() or "镜像可能正被容器使用"), "error")
    else:
        flash(f"镜像 {ref} 已删除", "success")
    return redirect(url_for("docker.images"))
