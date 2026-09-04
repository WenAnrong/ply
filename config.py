"""
应用配置：开发 / 正式两套
通过环境变量 FLASK_CONFIG 切换：development / production
"""

import os
import configparser
import secrets  # 生成随机密钥

# 算出当前项目所在目录的绝对路径
basedir = os.path.abspath(os.path.dirname(__file__))


def load_and_ensure_config_ini(flask_config, ini_path):
    """
    确保 config.ini 存在（首次启动自动生成随机的 SECRET_KEY），
    读取其中的键值并覆盖到 Flask 配置。

    键名约定：config.ini 内一律使用小写键（configparser 默认会把 option
    小写化后落盘，install.sh 也是按小写写）；读取时统一 key.upper() 转成
    大写后写入 Flask 配置。请在 ini 中保持小写键、代码里按大写引用，
    避免大小写混用造成误解。
    """
    os.makedirs(os.path.dirname(ini_path) or ".", exist_ok=True)

    # 首次启动：自动生成默认 config.ini（与 install.sh 一致，用小写键 secret_key）
    if not os.path.exists(ini_path):
        parser = configparser.ConfigParser()
        parser["secret"] = {"secret_key": secrets.token_hex(32)}
        with open(ini_path, "w", encoding="utf-8") as f:
            parser.write(f)

    # 读取 config.ini 并覆盖默认配置（小写键 -> 大写 Flask 配置键）
    ini = configparser.ConfigParser()
    ini.read(ini_path, encoding="utf-8")
    for section in ini.sections():
        for key, value in ini.items(section):
            flask_config[key.upper()] = value

    # 确保 Caddy 临时站点片段文件存在（与 config.ini 同目录），
    # 这样用户把 Caddyfile 的 import 指向它时不会因文件缺失而启动失败。
    _ensure_temp_snippet(flask_config)


def _ensure_temp_snippet(flask_config):
    """确保临时站点片段文件存在；缺失时写入最小头注释。"""
    snippet_path = flask_config.get("TEMP_SITE_SNIPPET_PATH")
    if not snippet_path or os.path.exists(snippet_path):
        return
    os.makedirs(os.path.dirname(snippet_path) or ".", exist_ok=True)
    with open(snippet_path, "w", encoding="utf-8") as f:
        f.write("# ply 临时站点片段（由面板自动生成，请勿手改）\n")
        f.write("# 请在你的 Caddyfile 的泛域名 site 块内 import 本文件。\n")


class BaseConfig:
    """所有环境共用的配置"""

    # 关闭数据库修改追踪
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # session cookie 安全项
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = False

    # ---- 临时站点（Caddy 泛域名反向代理）----
    # 默认 TTL（小时）
    TEMP_SITE_DEFAULT_TTL_HOURS = 24
    # 可选时长（小时），0 = 永久不过期
    TEMP_SITE_TTL_OPTIONS = [1, 4, 12, 24, 0]


class DevelopmentConfig(BaseConfig):
    """开发环境"""

    # SQLite 放在项目 tmp/ 下
    SQLALCHEMY_DATABASE_URI = "sqlite:///" + os.path.join(basedir, "tmp", "ply.db")
    # 配置文件放在项目 tmp/ 下
    CONFIG_INI_PATH = os.path.join(basedir, "tmp", "config.ini")
    # 临时站点片段与 config.ini 同目录
    TEMP_SITE_SNIPPET_PATH = os.path.join(basedir, "tmp", "ply-temp.caddy")


class ProductionConfig(BaseConfig):
    """正式环境"""

    # sqlite 放在 /var/lib/ply/ 下
    SQLALCHEMY_DATABASE_URI = "sqlite:///" + os.path.join("/var/lib/ply", "ply.db")

    # 配置文件放在 /etc/ply/ 下
    CONFIG_INI_PATH = os.path.join("/etc/ply/", "config.ini")
    # 临时站点片段与 config.ini 同目录
    TEMP_SITE_SNIPPET_PATH = os.path.join("/etc/ply/", "ply-temp.caddy")


# 通过环境变量 FLASK_CONFIG 切换配置
CONFIG_MAP = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "default": DevelopmentConfig,
}
