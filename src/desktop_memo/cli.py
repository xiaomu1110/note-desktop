"""命令行入口。

用法：
    desktop-memo              启动（开机自启项调用的就是这个）
    desktop-memo --hidden     启动但不显示卡片，只用托盘
    desktop-memo --version
"""

from __future__ import annotations

import argparse

from . import __version__

PROG = "note-desktop"


def build_parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器。"""
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="常驻桌面、可拖动的极简便签（零第三方依赖）",
    )
    parser.add_argument(
        "-v", "--version", action="version", version=f"{PROG} {__version__}"
    )
    parser.add_argument(
        "--hidden",
        action="store_true",
        help="启动后不显示卡片（只留托盘图标），单击托盘即可唤出",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """命令行主函数。"""
    args = build_parser().parse_args(argv)
    from .app import main as run_app

    return run_app(argv, hidden=args.hidden)


if __name__ == "__main__":
    raise SystemExit(main())