"""
数据模型
"""

from datetime import datetime, timezone

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import event
from sqlalchemy.engine import Engine
from werkzeug.security import check_password_hash, generate_password_hash

# 全局 SQLAlchemy 实例，供 app 与视图使用
db = SQLAlchemy()


@event.listens_for(Engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    """SQLite 并发写优化：WAL + busy_timeout。

    面板用 SQLite，且 gunicorn 多 worker 与临时站点清理 timer 是多个进程，
    同时写库可能报 "database is locked"。开启 WAL（多读一写）并设置 busy_timeout
    （写锁等待 5s），能显著降低写冲突。
    """
    import sqlite3

    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()


class User(UserMixin, db.Model):
    """面板用户

    继承 UserMixin 后，Flask-Login 需要的
    is_authenticated / is_active / is_anonymous / get_id 都自动提供。
    """

    __tablename__ = "user"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
    )

    def set_password(self, password):
        """把明文密码哈希后保存，库里只存哈希"""
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        """校验输入的明文密码是否匹配"""
        return check_password_hash(self.password_hash, password)

    def __repr__(self):
        return f"<User {self.username}>"


class TemporarySite(db.Model):
    """临时站点

    每个临时站点对应 Caddy 里的一个路径段 /<code>，
    反向代理到本机端口 port；expires_at 为空表示永久不过期。
    """

    __tablename__ = "temp_site"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(16), unique=True, nullable=False, index=True)
    port = db.Column(db.Integer, nullable=False)
    created_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
    )
    # 到期时间（UTC），None 表示永久；创建时 now + ttl_hours 写入
    expires_at = db.Column(db.DateTime, nullable=True)
    # 最近访问时间，用于“滑动过期”顺延（可选）
    last_accessed = db.Column(db.DateTime, nullable=True)
    # 状态：active / expired
    status = db.Column(db.String(16), nullable=False, default="active")

    def __repr__(self):
        return f"<TemporarySite {self.code}:{self.port}>"
