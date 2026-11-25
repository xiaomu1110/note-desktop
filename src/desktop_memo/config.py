"""全局常量与路径。

数据一律放在 %APPDATA%\\desktop-memo 下，便于备份与彻底卸载。
测试时可用环境变量 DESKTOP_MEMO_HOME 覆盖。
"""

from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "desktop-memo"
APP_TITLE = "备忘录"

# 窗口默认尺寸与最小尺寸（逻辑像素，96 DPI 基准）
DEFAULT_WIDTH = 320
DEFAULT_HEIGHT = 220
MIN_WIDTH = 220
MIN_HEIGHT = 140

# 开机自启用的注册表项名
AUTOSTART_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_NAME = "DesktopMemo"

# 单实例互斥体名
MUTEX_NAME = "Local\\DesktopMemo-SingleInstance"

# 本地 API 服务：只绑回环地址，端口固定方便 MCP 配置
API_HOST = "127.0.0.1"
API_PORT = 52741
API_TOKEN_FILE = "api_token"
API_TOKEN_ENV = "DESKTOP_MEMO_TOKEN"


def _token_path() -> Path:
    return data_dir() / API_TOKEN_FILE


def get_api_token(create: bool = True) -> str:
    """读取（必要时生成）本地 API 的访问令牌。

    令牌放在数据目录里，权限交给 NTFS 默认的当前用户 ACL。
    环境变量 DESKTOP_MEMO_TOKEN 优先，便于测试和临时覆盖。
    """
    import secrets

    override = os.environ.get(API_TOKEN_ENV)
    if override:
        return override
    path = _token_path()
    try:
        if path.exists():
            token = path.read_text(encoding="utf-8").strip()
            if token:
                return token
    except OSError:
        pass
    if not create:
        return ""
    token = secrets.token_urlsafe(32)
    try:
        path.write_text(token, encoding="utf-8")
        os.chmod(path, 0o600)
    except OSError:
        pass
    return token


def data_dir() -> Path:
    """返回应用数据目录，不存在时创建。"""
    override = os.environ.get("DESKTOP_MEMO_HOME")
    if override:
        root = Path(override)
    else:
        base = os.environ.get("APPDATA")
        root = Path(base) / APP_NAME if base else Path.home() / ".desktop-memo"
    root.mkdir(parents=True, exist_ok=True)
    return root


def db_path() -> Path:
    """SQLite 数据库文件路径。"""
    return data_dir() / "memo.db"


def asset_path(name: str) -> Path:
    """静态资源路径（图标等）。

    三种运行形态都要能找到：
    1. PyInstaller 打包：资源被解到 sys._MEIPASS
    2. pip 安装：资源在包的 assets/ 里
    3. 源码运行（src layout）：同上，因为图标就放在包内
    """
    import sys

    mei = getattr(sys, "_MEIPASS", None)
    if mei:
        bundled = Path(mei) / "desktop_memo" / "assets" / name
        if bundled.exists():
            return bundled
    return Path(__file__).with_name("assets") / name
