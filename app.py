"""
主要控制模块
"""

from datetime import datetime
from flask import Flask
from flask import render_template

from views.about import about_bp
from views.dashboard import dashboard_bp
from views.terminal import terminal_bp
from views.terminal import terminal_sock

app = Flask(__name__)


# 全局模板变量
@app.context_processor
def inject_globals():
    return {"now_year": datetime.now().year}


app.register_blueprint(dashboard_bp)
app.register_blueprint(about_bp)
app.register_blueprint(terminal_bp)
# 把 terminal_sock 这个 WebSocket 对象接入 Flask 应用，这样它才能在 /terminal/ws 上提供 WebSocket 服务
if terminal_sock is not None:
    terminal_sock.init_app(app)


@app.errorhandler(404)
def not_found(error):
    return render_template("404.html"), 404


if __name__ == "__main__":
    # 多线程：WebSocket 终端连接会占用独立工作线程，避免阻塞其他请求
    app.run(threaded=True)
