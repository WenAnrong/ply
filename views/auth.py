"""
认证模块：注册 / 登录 / 退出
"""

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
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        user = User.query.filter_by(username=username).first()
        if user is None or not user.check_password(password):
            error = "用户名或密码错误"
        else:
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
