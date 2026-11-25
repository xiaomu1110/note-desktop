"""无窗口启动器：开机自启用它，双击时不会弹黑色控制台。

等价于 `python -m desktop_memo`。
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "src"))

from desktop_memo.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())