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
    """
    os.makedirs(os.path.dirname(ini_path) or ".", exist_ok=True)

    # 首次启动：自动生成默认 config.ini
    if not os.path.exists(ini_path):
        parser = configparser.ConfigParser()
        parser["secret"] = {"SECRET_KEY": secrets.token_hex(32)}
        with open(ini_path, "w", encoding="utf-8") as f:
            parser.write(f)

    # 读取 config.ini 并覆盖默认配置
    ini = configparser.ConfigParser()
    ini.read(ini_path, encoding="utf-8")
    for section in ini.sections():
        for key, value in ini.items(section):
            flask_config[key.upper()] = value


class BaseConfig:
    """所有环境共用的配置"""

    # 关闭数据库修改追踪
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # session cookie 安全项
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = False

    # Docker 相关数据/启动文件默认目录（生产环境）
    DOCKER_DATA_DIR = "/var/lib/ply/docker"


class DevelopmentConfig(BaseConfig):
    """开发环境"""

    # SQLite 放在项目 tmp/ 下
    SQLALCHEMY_DATABASE_URI = "sqlite:///" + os.path.join(basedir, "tmp", "ply.db")
    # 配置文件放在项目 tmp/ 下
    CONFIG_INI_PATH = os.path.join(basedir, "tmp", "config.ini")

    # 开发环境：Docker 数据目录放到项目 tmp/ 下，避免污染系统目录
    DOCKER_DATA_DIR = os.path.join(basedir, "tmp", "docker")


class ProductionConfig(BaseConfig):
    """正式环境"""

    # sqlite 放在 /var/lib/ply/ 下
    SQLALCHEMY_DATABASE_URI = "sqlite:///" + os.path.join("/var/lib/ply", "ply.db")

    # 配置文件放在 /etc/ply/ 下
    CONFIG_INI_PATH = os.path.join("/etc/ply/", "config.ini")


# 通过环境变量 FLASK_CONFIG 切换配置
CONFIG_MAP = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "default": DevelopmentConfig,
}
