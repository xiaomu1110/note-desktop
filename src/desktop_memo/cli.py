"""命令行入口。

用法：
    desktop-memo --help
"""

import argparse


def build_parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器。"""
    parser = argparse.ArgumentParser(
        prog="desktop-memo",
        description="桌面备忘录 —— 一个轻量的本地桌面备忘录工具",
    )
    parser.add_argument("-v", "--version", action="version", version="desktop-memo 0.1.0")
    return parser


def main(argv: list[str] | None = None) -> int:
    """命令行主函数。"""
    args = build_parser().parse_args(argv)
    print(f"桌面备忘录已启动，参数：{args}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
