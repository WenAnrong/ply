"""
网站管理模块
"""

from flask import Blueprint
from flask import render_template
from flask_login import login_required

website_bp = Blueprint("website", __name__)


@website_bp.route("/website")
@login_required
def index():
    return render_template("website.html")
