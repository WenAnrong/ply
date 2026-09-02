"""
关于模块
"""

import os
import re
import urllib.request

from flask import Blueprint
from flask import render_template

about_bp = Blueprint("about", __name__)

# 本地版本号文件（项目根目录）
_VERSION_FILE = "VERSION"
# 远端版本号地址（GitHub raw）
_REMOTE_VERSION_URL = "https://raw.githubusercontent.com/WenAnrong/ply/main/VERSION"


def _project_root():
    """返回项目根目录（views 的上一级）。"""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read_local_version():
    """读取本地 VERSION 文件，缺失时返回 0.0.0。"""
    path = os.path.join(_project_root(), _VERSION_FILE)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip() or "0.0.0"
    except OSError:
        return "0.0.0"


def fetch_latest_version():
    """从 GitHub 拉取最新版本号，失败返回 None。"""
    try:
        with urllib.request.urlopen(_REMOTE_VERSION_URL, timeout=5) as resp:
            data = resp.read().decode("utf-8").strip()
            return data or None
    except Exception:
        return None


def _split_version(s):
    """把 'v1.2.3' 或 '1.2.3' 拆成数字元组，用于比较。"""
    return tuple(int(x) for x in re.findall(r"\d+", s))


def compare_version(local, latest):
    """比较本地与最新版本。

    返回 -1 表示 local < latest（有新版本）；0 相等；1 表示 local 更新。
    """
    a = _split_version(local)
    b = _split_version(latest)
    n = max(len(a), len(b))
    a = a + (0,) * (n - len(a))
    b = b + (0,) * (n - len(b))
    if a < b:
        return -1
    if a > b:
        return 1
    return 0


@about_bp.route("/about")
def index():
    return render_template("about.html", local_version=read_local_version())


@about_bp.route("/about/version")
def version():
    """返回版本对比片段，仅供 htmx 局部加载。"""
    local = read_local_version()
    latest = fetch_latest_version()

    if latest is None:
        status = "unknown"
        message = "无法获取最新版本，请检查网络或 GitHub 连通性"
    else:
        cmp_result = compare_version(local, latest)
        if cmp_result < 0:
            status = "outdated"
            message = "发现新版本"
        elif cmp_result > 0:
            status = "ahead"
            message = "当前版本高于远端（可能是开发版）"
        else:
            status = "latest"
            message = "已是最新版本"

    return render_template(
        "partials/version.html",
        local_version=local,
        latest_version=latest,
        status=status,
        message=message,
    )
