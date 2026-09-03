"""
网站管理模块
"""

import os
import subprocess

from flask import Blueprint
from flask import flash
from flask import redirect
from flask import render_template
from flask import request
from flask import url_for
from flask_login import login_required

website_bp = Blueprint("website", __name__)

# Caddy 配置文件
CADDYFILE_PATH = "/etc/caddy/Caddyfile"


def _sudo(args, stdin=None, cwd=None):
    """以服务用户通过免密 sudo 执行系统命令，避免卡在密码输入。"""
    return subprocess.run(
        ["sudo", "-n", *args],
        input=stdin,
        capture_output=True,
        text=True,
        cwd=cwd,
    )


def _read_caddyfile():
    """读取 Caddyfile，返回 (展示文本, 文件是否存在, 错误信息)。"""
    if not os.path.exists(CADDYFILE_PATH):
        return "", False, None
    try:
        with open(CADDYFILE_PATH, "r", encoding="utf-8") as f:
            return f.read(), True, None
    except Exception as e:
        return "", True, f"读取失败：{e}"


@website_bp.route("/website")
@login_required
def index():
    return render_template("website.html", active_tab="sites")


@website_bp.route("/website/settings")
@login_required
def settings():
    content, exists, err = _read_caddyfile()
    return render_template(
        "website.html",
        active_tab="settings",
        caddy_config=content,
        caddy_config_exists=exists,
        caddy_config_error=err,
        caddy_config_path=CADDYFILE_PATH,
    )


@website_bp.route("/website/settings/config", methods=["POST"])
@login_required
def save_caddy_config():
    content = request.form.get("content", "")

    # 备份原文件（文件不存在时跳过）
    _sudo(["cp", CADDYFILE_PATH, CADDYFILE_PATH + ".bak"])

    # 写入新配置（通过 stdin 交给 sudo tee，以 root 写）
    payload = content + "\n"
    write = _sudo(["tee", CADDYFILE_PATH], stdin=payload)
    if write.returncode != 0:
        flash("写入失败：" + (write.stderr or "权限不足"), "error")
        return redirect(url_for("website.settings"))

    # 缓冲：先把文件刷入磁盘，避免重启瞬间读到空/未写完的内容
    _sudo(["sync"])

    # 先清除可能已耗尽的启动限流，再重启 Caddy
    _sudo(["systemctl", "reset-failed", "caddy"])
    restart = _sudo(["systemctl", "restart", "caddy"])
    if restart.returncode != 0:
        flash("配置已保存，但 Caddy 重启失败：" + (restart.stderr or ""), "error")
    else:
        flash("Caddy 配置已更新并重启", "success")

    return redirect(url_for("website.settings"))
