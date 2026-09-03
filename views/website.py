"""
网站管理模块
"""

import os
import secrets
import string
import subprocess
from datetime import datetime, timedelta, timezone

from flask import Blueprint
from flask import current_app
from flask import flash
from flask import redirect
from flask import render_template
from flask import request
from flask import url_for
from flask_login import login_required

from models import TemporarySite
from models import db

website_bp = Blueprint("website", __name__)

# Caddy 配置文件
CADDYFILE_PATH = "/etc/caddy/Caddyfile"


def _sudo(args, stdin=None, cwd=None):
    """以服务用户通过免密 sudo 执行系统命令，避免卡在密码输入。"""
    return subprocess.run(
        ["sudo", "-n", *args],
        input=stdin,
        capture_output=True,
        text=True,
        cwd=cwd,
    )


def _read_caddyfile():
    """读取 Caddyfile，返回 (展示文本, 文件是否存在, 错误信息)。"""
    if not os.path.exists(CADDYFILE_PATH):
        return "", False, None
    try:
        with open(CADDYFILE_PATH, "r", encoding="utf-8") as f:
            return f.read(), True, None
    except Exception as e:
        return "", True, f"读取失败：{e}"


def _strip_host(addr):
    """从 Caddy 站点地址中提取基准域名（去掉协议/通配符/端口/路径）。"""
    addr = (addr or "").strip()
    # 去掉协议前缀，如 http:// 或 https://
    if "://" in addr:
        addr = addr.split("://", 1)[1]
    # 去掉路径
    addr = addr.split("/", 1)[0]
    # 去掉端口
    addr = addr.split(":", 1)[0]
    # 去掉通配符
    if addr.startswith("*."):
        addr = addr[2:]
    return addr.strip()


def _detect_wildcard_domain():
    """从主 Caddyfile 中自动识别泛域名基准域名。

    找到包含 `import <TEMP_SITE_SNIPPET_PATH>` 的 site 块，
    取其站点地址（如 *.example.com）并提取为 example.com。
    未找到时返回空字符串。
    """
    snippet_path = current_app.config["TEMP_SITE_SNIPPET_PATH"]
    if not os.path.exists(CADDYFILE_PATH):
        return ""
    try:
        with open(CADDYFILE_PATH, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    except Exception:
        return ""

    for i, line in enumerate(lines):
        if "import" not in line or snippet_path not in line:
            continue
        # 向上查找包裹该 import 的 site 块起始行（以 { 结尾，且不在更深层块内）
        depth = 0
        for j in range(i - 1, -1, -1):
            stripped = lines[j].split("#", 1)[0].strip()
            if not stripped:
                continue
            if stripped.endswith("}"):
                depth += 1
            elif stripped.endswith("{"):
                if depth > 0:
                    depth -= 1
                else:
                    addr = _strip_host(stripped[:-1])
                    if addr:
                        return addr
    return ""


def _build_temp_snippet(items, domain):
    """根据临时站点列表与泛域名生成 Caddy 片段文本。

    每个临时站点绑定到 <code>.<domain> 子域名，并反向代理到本机端口 port。
    """
    lines = [
        "# ply 临时站点片段（由面板自动生成，请勿手改）",
        "# 请在你的 Caddyfile 的泛域名 site 块（如 *."
        + domain
        + "）内 import 本文件。",
    ]
    for it in items:
        host = "%s.%s" % (it.code, domain)
        lines.append("@%s host %s" % (it.code, host))
        lines.append("handle @%s {" % it.code)
        lines.append("    reverse_proxy 127.0.0.1:%d" % it.port)
        lines.append("}")
    return "\n".join(lines) + "\n"


def _write_temp_snippet():
    """从数据库读取活动未过期的临时站点，重写面板自有片段文件。

    只写 TEMP_SITE_SNIPPET_PATH（面板自有文件），不写用户主 Caddyfile。
    """
    path = current_app.config["TEMP_SITE_SNIPPET_PATH"]
    domain = _detect_wildcard_domain()
    if not domain:
        raise RuntimeError(
            "无法从主 Caddyfile 识别泛域名，请确认其中存在一个形如 "
            "'*.example.com { import " + path + " }' 的 site 块"
        )
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    items = (
        TemporarySite.query.filter(
            TemporarySite.status == "active",
            (TemporarySite.expires_at.is_(None)) | (TemporarySite.expires_at > now),
        )
        .order_by(TemporarySite.created_at)
        .all()
    )

    content = _build_temp_snippet(items, domain)

    lock = path + ".lock"
    tmp = path + ".tmp"
    # 单条 sudo 命令：加锁 -> 写临时文件 -> 原子 mv -> sync
    cmd = 'flock "{}" -c \'tee "{}" >/dev/null && mv "{}" "{}" && sync\''.format(
        lock, tmp, tmp, path
    )
    r = _sudo(["sh", "-c", cmd], stdin=content)
    if r.returncode != 0:
        raise RuntimeError("写入片段失败：" + (r.stderr or "权限不足"))


def _reload_caddy():
    """热加载 Caddy（需要 admin API 在线）。返回 (成功, 错误信息)。"""
    r = _sudo(["caddy", "reload", "--config", CADDYFILE_PATH])
    if r.returncode == 0:
        return True, None
    return False, r.stderr.strip() or "caddy reload 失败"


def _sync_temp_sites():
    """重新生成片段并热加载 Caddy。返回 (成功, 错误信息)。"""
    try:
        _write_temp_snippet()
    except Exception as e:
        return False, str(e)
    return _reload_caddy()


def _gen_code(length=10):
    """生成唯一随机子域名标签（小写字母 + 数字，便于作为子域名）。"""
    alphabet = string.ascii_lowercase + string.digits
    while True:
        code = "".join(secrets.choice(alphabet) for _ in range(length))
        if not TemporarySite.query.filter_by(code=code).first():
            return code


def _apply_expired_retention():
    """保留最新 1 条过期记录用于参考，删除其余（10 删 9，固定值）。"""
    expired = (
        TemporarySite.query.filter(TemporarySite.status == "expired")
        .order_by(TemporarySite.created_at.desc())
        .all()
    )
    for rec in expired[1:]:
        db.session.delete(rec)
    if expired:
        db.session.commit()


@website_bp.route("/website")
@login_required
def index():
    temp_sites = TemporarySite.query.order_by(TemporarySite.created_at.desc()).all()
    snippet_path = current_app.config["TEMP_SITE_SNIPPET_PATH"]
    temp_site_domain = _detect_wildcard_domain()
    example_domain = temp_site_domain or "你的域名"
    # HTTPS（默认，Caddy 自动申请证书）
    temp_snippet_example = (
        "*." + example_domain + " {\n"
        "    import " + snippet_path + "\n"
        '    respond "" 204\n'
        "}"
    )
    # HTTP（不想要 HTTPS：站点地址前加 http://，只监听 80 端口）
    temp_snippet_example_http = (
        "http://*." + example_domain + " {\n"
        "    import " + snippet_path + "\n"
        '    respond "" 204\n'
        "}"
    )
    return render_template(
        "website.html",
        active_tab="sites",
        temp_sites=temp_sites,
        ttl_options=current_app.config["TEMP_SITE_TTL_OPTIONS"],
        temp_snippet_path=snippet_path,
        temp_snippet_example=temp_snippet_example,
        temp_snippet_example_http=temp_snippet_example_http,
        temp_site_domain=temp_site_domain,
    )


@website_bp.route("/website/sites/create", methods=["POST"])
@login_required
def create_temp_site():
    port_raw = request.form.get("port", "").strip()
    ttl_raw = request.form.get("ttl_hours", "").strip()

    # 校验端口
    try:
        port = int(port_raw)
    except ValueError:
        flash("端口必须是数字", "error")
        return redirect(url_for("website.index"))
    if not (1 <= port <= 65535):
        flash("端口必须在 1-65535 之间", "error")
        return redirect(url_for("website.index"))

    # 校验/确定 TTL（ttl=0 表示永久）
    options = current_app.config["TEMP_SITE_TTL_OPTIONS"]
    default_ttl = current_app.config["TEMP_SITE_DEFAULT_TTL_HOURS"]
    try:
        ttl = int(ttl_raw) if ttl_raw != "" else default_ttl
    except ValueError:
        ttl = default_ttl
    if ttl not in options:
        ttl = default_ttl

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    expires_at = None if ttl == 0 else now + timedelta(hours=ttl)

    site = TemporarySite(code=_gen_code(), port=port, expires_at=expires_at)
    db.session.add(site)
    db.session.commit()

    domain = _detect_wildcard_domain()
    host = site.code + "." + domain if domain else site.code
    ok, err = _sync_temp_sites()
    if ok:
        flash(f"临时站点已创建：{host}", "success")
    else:
        flash(f"临时站点已创建，但 Caddy 更新失败：{err}", "error")

    return redirect(url_for("website.index"))


@website_bp.route("/website/sites/<code>/close", methods=["POST"])
@login_required
def close_temp_site(code):
    site = TemporarySite.query.filter_by(code=code).first()
    if site is None:
        flash("临时站点不存在", "error")
        return redirect(url_for("website.index"))

    site.status = "expired"
    db.session.commit()

    # 保留最新 1 条过期记录，删除其余
    _apply_expired_retention()
    domain = _detect_wildcard_domain()
    host = code + "." + domain if domain else code
    ok, err = _sync_temp_sites()
    if ok:
        flash(f"临时站点已关闭：{host}", "success")
    else:
        flash(f"临时站点已关闭，但 Caddy 更新失败：{err}", "error")

    return redirect(url_for("website.index"))


@website_bp.route("/website/settings")
@login_required
def settings():
    content, exists, err = _read_caddyfile()
    return render_template(
        "website.html",
        active_tab="settings",
        caddy_config=content,
        caddy_config_exists=exists,
        caddy_config_error=err,
        caddy_config_path=CADDYFILE_PATH,
    )


@website_bp.route("/website/settings/config", methods=["POST"])
@login_required
def save_caddy_config():
    content = request.form.get("content", "")

    # 备份原文件（文件不存在时跳过）
    _sudo(["cp", CADDYFILE_PATH, CADDYFILE_PATH + ".bak"])

    # 写入新配置（通过 stdin 交给 sudo tee，以 root 写）
    payload = content + "\n"
    write = _sudo(["tee", CADDYFILE_PATH], stdin=payload)
    if write.returncode != 0:
        flash("写入失败：" + (write.stderr or "权限不足"), "error")
        return redirect(url_for("website.settings"))

    # 缓冲：先把文件刷入磁盘，避免重启瞬间读到空/未写完的内容
    _sudo(["sync"])

    # 先清除可能已耗尽的启动限流，再重启 Caddy
    _sudo(["systemctl", "reset-failed", "caddy"])
    restart = _sudo(["systemctl", "restart", "caddy"])
    if restart.returncode != 0:
        flash("配置已保存，但 Caddy 重启失败：" + (restart.stderr or ""), "error")
    else:
        flash("Caddy 配置已更新并重启", "success")

    return redirect(url_for("website.settings"))
