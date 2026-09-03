"""
Docker 管理模块
"""

import json
import os
import re
import subprocess
import time

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


def _sudo(args, stdin=None, cwd=None):
    """以服务用户通过免密 sudo 执行系统命令，避免卡在密码输入。"""
    return subprocess.run(
        ["sudo", "-n", *args],
        input=stdin,
        capture_output=True,
        text=True,
        cwd=cwd,
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


def _list_containers():
    """列出容器，区分 compose 项目与普通容器。

    返回 (compose_projects, normal_containers, 错误信息)。
    每个容器含 id/name/image/status/ports；compose 容器额外带 project/dir/config_files。
    """
    r = _sudo(
        [
            "docker",
            "ps",
            "-a",
            "--format",
            "{{.ID}}\t{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}",
        ]
    )
    if r.returncode != 0:
        return (
            [],
            [],
            r.stderr.strip() or "无法读取 Docker 容器（请确认 Docker 是否可用）",
        )

    rows = []
    for line in r.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) >= 5:
            rows.append(
                {
                    "id": parts[0],
                    "name": parts[1],
                    "image": parts[2],
                    "status": parts[3],
                    "ports": parts[4],
                }
            )

    if not rows:
        return [], [], None

    # 读取每个容器的 compose 标签（以容器名匹配，避免长短 ID 不一致）
    labels = {}
    ids = [row["id"] for row in rows]
    inspect = _sudo(
        [
            "docker",
            "inspect",
            *ids,
            "--format",
            '{{.Name}}\t{{index .Config.Labels "com.docker.compose.project"}}\t{{index .Config.Labels "com.docker.compose.project.working_dir"}}\t{{index .Config.Labels "com.docker.compose.project.config_files"}}',
        ]
    )
    if inspect.returncode == 0:
        for line in inspect.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) >= 4:
                labels[parts[0].lstrip("/")] = {
                    "project": parts[1],
                    "dir": parts[2],
                    "config_files": parts[3],
                }

    projects = {}
    normal = []
    for row in rows:
        meta = labels.get(row["name"])
        if meta and meta["project"]:
            proj = meta["project"]
            projects.setdefault(
                proj,
                {
                    "name": proj,
                    "dir": meta["dir"],
                    "config_files": meta["config_files"],
                    "containers": [],
                },
            )
            projects[proj]["containers"].append(row)
        else:
            normal.append(row)

    for p in projects.values():
        p["running"] = sum(1 for c in p["containers"] if c["status"].startswith("Up"))
        p["total"] = len(p["containers"])
    return list(projects.values()), normal, None


def _compose_args(project, action):
    """构造针对某个 compose 项目的 docker compose 命令参数。

    返回 (args, cwd)。优先用 config_files（可多个，逗号分隔）；缺失时退回使用 working_dir。
    """
    args = ["docker", "compose"]
    raw = (project.get("config_files") or "").strip()
    files = [f.strip() for f in re.split(r"[,]+", raw) if f.strip()]
    cwd = None
    if files:
        for f in files:
            args += ["-f", f]
    elif project.get("dir"):
        cwd = project["dir"]
    args.append(action)
    if action == "up":
        args.append("-d")
    return args, cwd


def _find_compose_project(name):
    """按项目名查找 compose 项目，找不到返回 None。"""
    projects, _, _ = _list_containers()
    for p in projects:
        if p["name"] == name:
            return p
    return None


@docker_bp.route("/docker")
@login_required
def index():
    return render_template("docker.html", active_tab="services")


@docker_bp.route("/docker/services")
@login_required
def services():
    projects, normal, err = _list_containers()
    return render_template(
        "docker.html",
        active_tab="services",
        compose_projects=projects,
        normal_containers=normal,
        docker_services_error=err,
    )


@docker_bp.route("/docker/services/start", methods=["POST"])
@login_required
def container_start():
    ref = request.form.get("ref", "").strip()
    if not ref:
        flash("未选择容器", "error")
        return redirect(url_for("docker.services"))
    r = _sudo(["docker", "start", ref])
    if r.returncode != 0:
        flash("启动失败：" + (r.stderr.strip() or "未知错误"), "error")
    else:
        flash(f"容器 {ref} 已启动", "success")
    return redirect(url_for("docker.services"))


@docker_bp.route("/docker/services/stop", methods=["POST"])
@login_required
def container_stop():
    ref = request.form.get("ref", "").strip()
    if not ref:
        flash("未选择容器", "error")
        return redirect(url_for("docker.services"))
    r = _sudo(["docker", "stop", ref])
    if r.returncode != 0:
        flash("停止失败：" + (r.stderr.strip() or "未知错误"), "error")
    else:
        flash(f"容器 {ref} 已停止", "success")
    return redirect(url_for("docker.services"))


@docker_bp.route("/docker/services/compose/up", methods=["POST"])
@login_required
def compose_up():
    name = request.form.get("project", "").strip()
    project = _find_compose_project(name)
    if not project:
        flash("未找到项目", "error")
        return redirect(url_for("docker.services"))
    args, cwd = _compose_args(project, "up")
    r = _sudo(args, cwd=cwd)
    if r.returncode != 0:
        flash("启动失败：" + (r.stderr.strip() or "未知错误"), "error")
    else:
        flash(f"项目 {name} 已启动", "success")
    return redirect(url_for("docker.services"))


@docker_bp.route("/docker/services/compose/down", methods=["POST"])
@login_required
def compose_down():
    name = request.form.get("project", "").strip()
    project = _find_compose_project(name)
    if not project:
        flash("未找到项目", "error")
        return redirect(url_for("docker.services"))
    args, cwd = _compose_args(project, "down")
    r = _sudo(args, cwd=cwd)
    if r.returncode != 0:
        flash("停止失败：" + (r.stderr.strip() or "未知错误"), "error")
    else:
        flash(f"项目 {name} 已停止", "success")
    return redirect(url_for("docker.services"))


@docker_bp.route("/docker/services/compose/stop", methods=["POST"])
@login_required
def compose_stop():
    name = request.form.get("project", "").strip()
    project = _find_compose_project(name)
    if not project:
        flash("未找到项目", "error")
        return redirect(url_for("docker.services"))
    args, cwd = _compose_args(project, "stop")
    r = _sudo(args, cwd=cwd)
    if r.returncode != 0:
        flash("停止失败：" + (r.stderr.strip() or "未知错误"), "error")
    else:
        flash(f"项目 {name} 已停止", "success")
    return redirect(url_for("docker.services"))


@docker_bp.route("/docker/services/delete", methods=["POST"])
@login_required
def container_delete():
    ref = request.form.get("ref", "").strip()
    if not ref:
        flash("未选择容器", "error")
        return redirect(url_for("docker.services"))
    r = _sudo(["docker", "rm", ref])
    if r.returncode != 0:
        flash("删除失败：" + (r.stderr.strip() or "容器可能正在运行"), "error")
    else:
        flash(f"容器 {ref} 已删除", "success")
    return redirect(url_for("docker.services"))


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

    # 备份原文件（文件不存在时跳过）
    _sudo(["cp", DOCKER_DAEMON_JSON, DOCKER_DAEMON_JSON + ".bak"])

    # 写入新配置（通过 stdin 交给 sudo tee，以 root 写）
    payload = content + "\n"
    write = _sudo(["tee", DOCKER_DAEMON_JSON], stdin=payload)
    if write.returncode != 0:
        flash("写入失败：" + (write.stderr or "权限不足"), "error")
        return redirect(url_for("docker.settings"))

    # 缓冲/确认：先把文件刷入磁盘、稍等片刻，再读回校验，
    # 避免 dockerd 在重启瞬间读到空/被截断的 daemon.json 而启动失败。
    _sudo(["sync"])
    time.sleep(0.5)
    try:
        with open(DOCKER_DAEMON_JSON, "r", encoding="utf-8") as f:
            written = f.read()
        if not written.strip():
            raise ValueError("写入内容为空")
        json.loads(written)
    except Exception as e:
        flash("写入内容校验失败，为避免 Docker 无法启动，未重启：" + str(e), "error")
        return redirect(url_for("docker.settings"))

    # 先清除可能已耗尽的启动限流，再重启，确保能真正启动
    _sudo(["systemctl", "reset-failed", "docker"])
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
