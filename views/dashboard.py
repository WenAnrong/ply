from flask import Blueprint
from flask import render_template
import psutil

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/")
def index():
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    return render_template(
        "dashboard.html",
        cpu=f"{psutil.cpu_percent()}%",
        memory=f"{memory.used / (1024 ** 3):.1f} GB",
        disk=f"{disk.percent:.1f}%",
        uptime=psutil.boot_time(),
    )
