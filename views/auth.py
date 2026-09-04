"""
认证模块：注册 / 登录 / 退出
"""

import threading
import time

from flask import Blueprint
from flask import redirect
from flask import render_template
from flask import request
from flask import url_for
from flask_login import current_user
from flask_login import login_required
from flask_login import login_user
from flask_login import logout_user

from models import User
from models import db

# ---- 登录失败限速：连续 5 次失败 → 锁定 30 秒 ----
_LOGIN_LOCK = threading.Lock()
_LOGIN_ATTEMPTS = {}  # ip -> {"fail": int, "lock_until": float}
_LOGIN_MAX_FAILS = 5
_LOGIN_LOCK_SECONDS = 30


def _client_ip():
    """获取客户端 IP；若处于可信反代之后则使用 X-Forwarded-For 首地址。"""
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.remote_addr or "unknown"


auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    # 已登录用户直接回首页
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    # 如果已经存在用户则拒绝注册
    if User.query.count() > 0:
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")

        valid = True
        if not username or not password:
            valid = False
        elif len(password) < 6:
            valid = False
        elif password != confirm:
            valid = False

        # 没问题，开始注册
        if valid:
            user = User(username=username)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            # 注册成功后直接登录并回首页
            login_user(user)
            return redirect(url_for("dashboard.index"))

        return render_template("register.html")

    return render_template("register.html")


@auth_bp.route("/login", methods=["GET", "POST"])
def login():

    # 还没有任何用户，强制进入注册页完成初始化
    if User.query.count() == 0:
        return redirect(url_for("auth.register"))

    # 已登录用户直接回首页
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    error = None
    if request.method == "POST":
        ip = _client_ip()
        now = time.time()

        # 锁定检查：在锁定时间内直接拒绝，不校验密码
        with _LOGIN_LOCK:
            rec = _LOGIN_ATTEMPTS.get(ip, {"fail": 0, "lock_until": 0})
            if rec["lock_until"] > now:
                left = int(rec["lock_until"] - now) + 1
                return render_template(
                    "login.html", error=f"尝试次数过多，请 {left} 秒后再试"
                )

        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        user = User.query.filter_by(username=username).first()
        if user is None or not user.check_password(password):
            # 连续失败计数，达到阈值则锁定 30 秒
            with _LOGIN_LOCK:
                rec = _LOGIN_ATTEMPTS.get(ip, {"fail": 0, "lock_until": 0})
                rec["fail"] += 1
                if rec["fail"] >= _LOGIN_MAX_FAILS:
                    rec["fail"] = 0
                    rec["lock_until"] = now + _LOGIN_LOCK_SECONDS
                _LOGIN_ATTEMPTS[ip] = rec
            error = "用户名或密码错误"
        else:
            # 登录成功，清除该 IP 的失败记录
            with _LOGIN_LOCK:
                _LOGIN_ATTEMPTS.pop(ip, None)
            login_user(user)
            # 优先跳转到被拦截的页面（?next=），否则回首页
            next_page = request.args.get("next")
            if next_page and next_page.startswith("/"):
                return redirect(next_page)
            return redirect(url_for("dashboard.index"))

    return render_template("login.html", error=error)


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))
