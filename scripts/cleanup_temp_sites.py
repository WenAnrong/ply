#!/usr/bin/env python3
"""临时站点后台清理脚本（由 systemd timer / cron 周期性调用）。

功能：
1. 把已过期的 active 临时站点标记为 expired。
2. 应用保留策略：保留最新 1 条过期记录，删除其余（10 删 9）。
3. 重新生成 Caddy 片段并热加载（只动面板自有片段文件，不碰用户主 Caddyfile）。
"""

import os
import sys

# 把项目根目录加入 sys.path，使 `import app` / `from views...` 可用
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# 默认生产环境；若外部设置了 FLASK_CONFIG（systemd unit 会设）则以外部为准
os.environ.setdefault("FLASK_CONFIG", "production")

from datetime import datetime, timezone  # noqa: E402

from app import app, db  # noqa: E402
from models import TemporarySite  # noqa: E402
from views.website import _apply_expired_retention, _sync_temp_sites  # noqa: E402


def main():
    with app.app_context():
        # 1. 把已过期的 active 站点标记为 expired
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        expired = TemporarySite.query.filter(
            TemporarySite.status == "active",
            TemporarySite.expires_at.isnot(None),
            TemporarySite.expires_at <= now,
        ).all()
        for s in expired:
            s.status = "expired"
        if expired:
            db.session.commit()

        # 2. 保留策略：保留最新 1 条过期记录，删除其余
        _apply_expired_retention()

        # 3. 重新生成片段并 reload
        ok, err = _sync_temp_sites()
        print(
            f"[ply-cleanup] marked_expired={len(expired)} sync_ok={ok}"
            + (f" err={err}" if err else "")
        )
        if not ok:
            sys.exit(1)


if __name__ == "__main__":
    main()
