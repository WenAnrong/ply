#!/usr/bin/env python3
"""登录日志月度清理脚本（由 systemd timer 每月 1 号调用）。

删除超过 _RETENTION_DAYS 天的登录日志，避免 login_log 表无限增长。
只删旧数据、保留最近记录，便于事后排查爆破。
"""

import os
import sys

# 把项目根目录加入 sys.path，使 `import app` / `from models...` 可用
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# 默认生产环境；若外部设置了 FLASK_CONFIG（systemd unit 会设）则以外部为准
os.environ.setdefault("FLASK_CONFIG", "production")

from datetime import datetime, timedelta, timezone  # noqa: E402

from app import app, db  # noqa: E402
from models import LoginLog  # noqa: E402

# 保留最近 30 天的登录日志，更早的删除（每月 1 号由 timer 触发）
_RETENTION_DAYS = 30


def main():
    with app.app_context():
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        cutoff = now - timedelta(days=_RETENTION_DAYS)
        old = LoginLog.query.filter(LoginLog.created_at < cutoff)
        count = old.count()
        if count:
            old.delete(synchronize_session=False)
            db.session.commit()
        print(f"[ply-logclean] deleted={count} cutoff={cutoff:%Y-%m-%d}")


if __name__ == "__main__":
    main()
