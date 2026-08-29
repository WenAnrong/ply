import time
import platform
import socket
from datetime import datetime

from flask import Blueprint
from flask import render_template
import psutil

dashboard_bp = Blueprint("dashboard", __name__)


def format_uptime(seconds):
    """把秒数格式化"""
    days, rem = divmod(int(seconds), 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{days}天 {hours}小时 {minutes}分钟"


def get_live_stats():
    """实时数据（供 htmx 定时轮询）"""
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    return {
        "cpu": f"{psutil.cpu_percent()}%",
        "memory": f"{memory.used / (1024 ** 3):.1f} GB",
        "disk": f"{disk.percent:.1f}%",
    }


def get_system_info():
    """静态系统信息（很少变化，随首屏渲染）"""
    boot_time = psutil.boot_time()
    boot_dt = datetime.fromtimestamp(boot_time)

    # 主机地址：取默认路由出口 IP；失败时退回主机名解析
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        host_ip = s.getsockname()[0]
        s.close()
    except OSError:
        host_ip = socket.gethostbyname(socket.gethostname())

    # 发行版本：读取 /etc/os-release 的 PRETTY_NAME
    try:
        os_release = platform.freedesktop_os_release().get("PRETTY_NAME", "未知")
    except Exception:
        os_release = platform.platform()

    return {
        "hostname": socket.gethostname(),
        "os_release": os_release,
        "kernel": platform.release(),
        "machine": platform.machine(),
        "host_ip": host_ip,
        "boot_time": boot_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "uptime": format_uptime(time.time() - boot_time),
    }


@dashboard_bp.route("/")
def index():
    return render_template("dashboard.html", **get_live_stats(), **get_system_info())


@dashboard_bp.route("/stats")
def stats():
    """只返回实时数据片段，供 htmx 定时轮询"""
    return render_template("partials/stats.html", **get_live_stats())
