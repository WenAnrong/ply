import threading
import time
import platform
import socket
import urllib.request
from datetime import datetime

from flask import Blueprint
from flask import render_template
import psutil

dashboard_bp = Blueprint("dashboard", __name__)

# ---- 公网出口 IP 缓存：避免每次刷新页面都同步请求外部服务 ----
_PUBLIC_IP_TTL = 300  # 缓存 5 分钟
_PUBLIC_IP_LOCK = threading.Lock()
_PUBLIC_IP_CACHE = {"value": None, "ts": 0.0}


def format_uptime(seconds):
    """把秒数格式化"""
    days, rem = divmod(int(seconds), 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{days}天 {hours}小时 {minutes}分钟"


def _get_public_ip():
    """获取公网出口 IP；失败返回 None。"""
    try:
        with urllib.request.urlopen("https://ifconfig.me", timeout=5) as resp:
            return resp.read().decode().strip()
    except Exception:
        return None


def _get_public_ip_cached():
    """带 TTL 缓存地获取公网出口 IP；失败返回 None。

    外部请求只在首次/过期后发生，后续页面加载直接命中缓存，
    避免每次都阻塞最多 5 秒等待 ifconfig.me。
    """
    now = time.time()
    with _PUBLIC_IP_LOCK:
        cached = _PUBLIC_IP_CACHE["value"]
        if cached is not None and now - _PUBLIC_IP_CACHE["ts"] < _PUBLIC_IP_TTL:
            return cached
    ip = _get_public_ip()
    with _PUBLIC_IP_LOCK:
        _PUBLIC_IP_CACHE["value"] = ip
        _PUBLIC_IP_CACHE["ts"] = time.time()
    return ip


def _fmt_gb(value):
    """字节数转 GB 字符串；None 显示为 —"""
    if value is None:
        return "—"
    return f"{value / (1024 ** 3):.2f} GB"


def _fmt_size(value, min_gb):
    """按阈值在 GB / MB 之间选择单位。

    min_gb: 阈值（GB）
    """
    if value is None:
        return "—"
    gb = value / (1024**3)
    # 大于等于阈值就用 GB
    if gb >= min_gb:
        return f"{gb:.2f} GB"
    return f"{value / (1024 ** 2):.1f} MB"


# 这些文件系统不应作为"真实磁盘"展示
_IGNORE_FSTYPES = {
    "squashfs",
    "iso9660",
    "tmpfs",
    "overlay",
    "proc",
    "sysfs",
    "devtmpfs",
    "cgroup",
    "cgroup2",
    "ramfs",
    "autofs",
    "devfs",
    "binfmt_misc",
    "efivarfs",
    "debugfs",
    "tracefs",
    "securityfs",
    "pstore",
    "configfs",
    "fusectl",
    "hugetlbfs",
}


def get_disk_details():
    """列出真实磁盘/分区使用情况，按挂载点排序保持稳定"""
    details = []
    for part in psutil.disk_partitions(all=False):
        if part.fstype in _IGNORE_FSTYPES:
            continue
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except OSError:
            continue
        if usage.total == 0:
            continue
        details.append(
            {
                "device": part.device,
                "mount": part.mountpoint,
                "fstype": part.fstype,
                "total": _fmt_gb(usage.total),
                "used": _fmt_gb(usage.used),
                "free": _fmt_gb(usage.free),
                "percent": usage.percent,
            }
        )
    details.sort(key=lambda d: d["mount"])
    return details


def get_cpu_detail():
    """CPU 物理/逻辑核心数、频率"""
    physical = psutil.cpu_count(logical=False)
    logical = psutil.cpu_count(logical=True)

    freq_mhz = None
    try:
        f = psutil.cpu_freq()
        if f is not None:
            freq_mhz = f.current
    except Exception:
        freq_mhz = None

    return {
        "physical": physical if physical else "—",
        "logical": logical if logical else "—",
        "freq": f"{freq_mhz / 1000:.2f} GHz" if freq_mhz else "—",
    }


def get_live_stats():
    """实时数据（供 htmx 定时轮询）"""
    memory = psutil.virtual_memory()
    cpu = psutil.cpu_percent()

    memory_detail = {
        "total": _fmt_size(memory.total, 1),
        "used": _fmt_size(memory.used, 3),
        "shared": _fmt_size(getattr(memory, "shared", None), 3),
        "cached": _fmt_size(getattr(memory, "cached", None), 3),
        "available": _fmt_size(memory.available, 3),
        "free": _fmt_size(memory.free, 3),
    }

    swap = psutil.swap_memory()

    # 列出所有真实分区；主卡片优先展示根分区 /，否则回退到使用率最高的盘
    disk_details = get_disk_details()
    disk_primary = next((d for d in disk_details if d["mount"] == "/"), None)
    if disk_primary is None:
        disk_primary = max(disk_details, key=lambda d: d["percent"], default=None)

    cpu_detail = get_cpu_detail()

    return {
        "cpu_percent": cpu,
        "memory_percent": memory.percent,
        "disk_percent": disk_primary["percent"] if disk_primary else 0,
        "cpu_use": f"{cpu}%",
        "memory_used": _fmt_size(memory.total - memory.available, 3),
        "memory_total": _fmt_size(memory.total, 1),
        "disk_used": disk_primary["used"] if disk_primary else "—",
        "disk_total": disk_primary["total"] if disk_primary else "—",
        "memory_detail": memory_detail,
        "disk_details": disk_details,
        "swap_used": _fmt_size(swap.used, 3),
        "swap_total": _fmt_size(swap.total, 1),
        "swap_percent": swap.percent,
        "cpu_detail": cpu_detail,
        "disk_mount": disk_primary["mount"] if disk_primary else "—",
    }


def get_system_info():
    """静态系统信息（随首屏渲染）"""
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

    # 注：公网出口 IP 需要访问外部服务 ifconfig.me，为避免阻塞首屏渲染，
    # 不在本函数里同步获取，改由 /public_ip 端点 + htmx load 异步填充。

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
    return render_template("partials/stats_values.html", **get_live_stats())


@dashboard_bp.route("/public_ip")
def public_ip():
    """返回公网出口 IP（纯文本），由 htmx 在首屏后异步请求填充，不阻塞页面渲染。

    受全局 before_request 保护，需登录后访问；命中缓存时几乎零延迟。
    """
    return _get_public_ip_cached() or "—"
