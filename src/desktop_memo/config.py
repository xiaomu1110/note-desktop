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
    """包内静态资源路径（图标等）。"""
    return Path(__file__).with_name("assets") / name
