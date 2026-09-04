"""
设置模块
"""

from datetime import datetime, timezone

from flask import Blueprint
from flask import flash
from flask import redirect
from flask import render_template
from flask import request
from flask import url_for
from flask_login import current_user
from flask_login import login_required

from models import LoginLog
from models import db

setting_bp = Blueprint("setting", __name__)

# 登录日志结果的中文说明
_LOGIN_REASON_LABELS = {
    "success": "登录成功",
    "bad_credentials": "密码错误 / 用户不存在",
    "locked": "触发限速锁定",
}


def _utc_to_local_str(utc_naive):
    """把库里存的 naive UTC 转成本地时间字符串；为空返回 —。"""
    if utc_naive is None:
        return "—"
    return (
        utc_naive.replace(tzinfo=timezone.utc)
        .astimezone()
        .strftime("%Y-%m-%d %H:%M:%S")
    )


def _fmt_login_logs(query):
    """把 LoginLog 查询结果转成模板易用的字典列表。"""
    rows = []
    for log in query:
        rows.append(
            {
                "time": _utc_to_local_str(log.created_at),
                "username": (log.username or "") or "—",
                "ip": (log.ip or "") or "—",
                "success": log.success,
                "reason": _LOGIN_REASON_LABELS.get(log.reason, log.reason or "—"),
                "ua": (log.user_agent or "") or "—",
            }
        )
    return rows


@setting_bp.route("/setting")
@login_required
def index():
    return render_template("setting.html")


@setting_bp.route("/setting/loginlog")
@login_required
def loginlog():
    """登录日志：最近 800 条，可按“只看失败”过滤。"""
    only_fail = request.args.get("only") == "1"
    query = LoginLog.query.order_by(LoginLog.created_at.desc())
    if only_fail:
        query = query.filter(LoginLog.success.is_(False))
    rows = _fmt_login_logs(query.limit(800).all())
    return render_template("loginlog.html", logs=rows, only_fail=only_fail)


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
