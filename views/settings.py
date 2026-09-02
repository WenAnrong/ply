"""
设置模块
"""

from flask import Blueprint
from flask import flash
from flask import redirect
from flask import render_template
from flask import request
from flask import url_for
from flask_login import current_user
from flask_login import login_required

from models import db

setting_bp = Blueprint("setting", __name__)


@setting_bp.route("/setting")
@login_required
def index():
    return render_template("setting.html")


@setting_bp.route("/setting/password", methods=["POST"])
@login_required
def change_password():
    """更改当前登录用户的密码。"""
    current_password = request.form.get("current_password", "")
    new_password = request.form.get("new_password", "")
    confirm_password = request.form.get("confirm_password", "")

    error = None
    if not current_user.check_password(current_password):
        error = "当前密码不正确"
    elif not new_password:
        error = "新密码不能为空"
    elif len(new_password) < 6:
        error = "新密码至少 6 位"
    elif new_password != confirm_password:
        error = "两次输入的新密码不一致"
    elif new_password == current_password:
        error = "新密码不能与当前密码相同"

    if error:
        flash(error, "error")
    else:
        current_user.set_password(new_password)
        db.session.commit()
        flash("密码已更新", "success")

    return redirect(url_for("setting.index"))
