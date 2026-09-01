"""
数据模型
"""

from datetime import datetime, timezone

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash

# 全局 SQLAlchemy 实例，供 app 与视图使用
db = SQLAlchemy()


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
