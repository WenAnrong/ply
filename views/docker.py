"""
Docker 管理模块
"""

from flask import Blueprint
from flask import render_template
from flask_login import login_required

docker_bp = Blueprint("docker", __name__)


@docker_bp.route("/docker")
@login_required
def index():
    return render_template("docker.html")
