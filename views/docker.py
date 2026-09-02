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
    return render_template("docker.html", active_tab="settings")
