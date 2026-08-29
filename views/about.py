"""
关于模块
"""

from flask import Blueprint
from flask import render_template

about_bp = Blueprint("about", __name__)


@about_bp.route("/about")
def index():
    return render_template("about.html")
