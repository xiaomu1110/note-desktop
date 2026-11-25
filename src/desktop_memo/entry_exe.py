"""打包成 exe 时的入口。

和 `python -m desktop_memo` 的区别：
- 没有源码目录，所以不能靠 run.pyw 再起一个解释器
- 开机自启 / MCP 的 mem_start 都改为直接重启 exe 自己
"""

import sys


def _frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def main() -> int:
    from desktop_memo.cli import main as cli_main

    return cli_main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())