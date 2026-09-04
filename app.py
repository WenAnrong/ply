"""
主要控制模块
"""

import logging
import os
from datetime import datetime

from flask import Flask
from flask import redirect
from flask import render_template
from flask import request
from flask import url_for
from flask_login import LoginManager
from flask_login import current_user
from flask_wtf import CSRFProtect

from config import CONFIG_MAP
from config import load_and_ensure_config_ini
from models import User
from models import db
from views.about import about_bp
from views.auth import auth_bp
from views.dashboard import dashboard_bp
from views.docker import docker_bp
from views.terminal import terminal_bp
from views.terminal import terminal_sock
from views.settings import setting_bp
from views.website import ensure_temp_snippet
from views.website import website_bp

# 运行环境标识（development / production，默认 development）
_ENV = os.environ.get("FLASK_CONFIG", "development")

# 用标准 logging 记录运行信息：开发环境 INFO 以上、生产 INFO 以上，
# 输出带时间/级别，gunicorn worker 的日志经 stderr 进入 journald 便于分级查看。
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)

app = Flask(__name__)

# 根据环境变量 FLASK_CONFIG 加载配置（默认开发环境）
app.config.from_object(CONFIG_MAP[_ENV])
# 确保 app.logger 的 INFO 级别日志能输出（默认可能被 root 的 WARNING 吞掉）
app.logger.setLevel(logging.INFO)
app.logger.info("当前配置: %s", _ENV)

# SQLite 要求父目录已存在，这里自动创建
_db_path = app.config["SQLALCHEMY_DATABASE_URI"].replace("sqlite:///", "")
_db_dir = os.path.dirname(_db_path)
if _db_dir:
    os.makedirs(_db_dir, exist_ok=True)

# 读取 config.ini 并覆盖默认配置
load_and_ensure_config_ini(app.config, app.config["CONFIG_INI_PATH"])

# 初始化扩展
db.init_app(app)
csrf = CSRFProtect(app)

login_manager = LoginManager(app)
login_manager.login_view = "auth.login"
# 禁用登录重定向时的自动 flash 提示
login_manager.login_message = None


@login_manager.user_loader
def load_user(user_id):
    """一次请求开始时，flask-login 从 session 里取出 user_id，调用这个 load_user 函数，把 id（字符串）转成 int，去数据库查这个 User。"""

    return db.session.get(User, int(user_id))


# 未登录时拦截所有页面，仅放行登录/注册/静态资源
AUTH_PUBLIC_PATHS = {"/login", "/register"}


@app.before_request
def require_login():
    """每个请求进来后、真正执行路由函数之前都会先跑这个函数"""

    path = request.path
    # 静态资源的获取无需登录
    if path.startswith("/static"):
        return
    # 放行登录/注册页面
    if path in AUTH_PUBLIC_PATHS:
        return
    # 未登录用户访问其他页面，重定向到登录页，并在 next 参数里带上原始请求路径
    if not current_user.is_authenticated:
        return redirect(url_for("auth.login", next=path))


# 全局模板变量
@app.context_processor
def inject_globals():
    return {"now_year": datetime.now().year}


# 全局安全响应头：防 MIME 嗅探 / 点击劫持 / 跨站 Referer 泄露
@app.after_request
def security_headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "same-origin"
    return resp


app.register_blueprint(dashboard_bp)
app.register_blueprint(about_bp)
app.register_blueprint(auth_bp)
app.register_blueprint(terminal_bp)
app.register_blueprint(setting_bp)
app.register_blueprint(docker_bp)
app.register_blueprint(website_bp)
# 把 terminal_sock 这个 WebSocket 对象接入 Flask 应用，这样它才能在 /terminal/ws 上提供 WebSocket 服务
if terminal_sock is not None:
    terminal_sock.init_app(app)

# 首次运行建表（表已存在时不会重复建）
with app.app_context():
    db.create_all()
    # 确保面板自有的 Caddy 临时站点片段文件存在（与 config.ini 一样在应用层处理）
    ensure_temp_snippet()


@app.errorhandler(404)
def not_found(error):
    return render_template("404.html"), 404


if __name__ == "__main__":
    app.run()
