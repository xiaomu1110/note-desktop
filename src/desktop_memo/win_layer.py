"""Win32 桌面层绑定（ctypes）。

只做四件事：
1. 让窗口不进任务栏 / Alt+Tab（WS_EX_TOOLWINDOW）
2. 进程 DPI 感知，保证高分屏文字不糊
3. 开机自启（读写 HKCU 的 Run 项）
4. 单实例（命名互斥体）

任何一步失败都静默降级，不能因为拿不到窗口句柄就起不来。
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

from .config import AUTOSTART_KEY, AUTOSTART_NAME, MUTEX_NAME

_IS_WINDOWS = sys.platform == "win32"

if _IS_WINDOWS:
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
else:  # pragma: no cover - 仅 Windows 有效
    _user32 = None
    _kernel32 = None

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
WS_EX_NOACTIVATE = 0x08000000

HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010

ERROR_ALREADY_EXISTS = 183


def enable_dpi_awareness() -> None:
    """每个显示器独立感知 DPI，避免 125%/150% 缩放下发虚。"""
    if not _IS_WINDOWS:
        return
    try:
        # PROCESS_PER_MONITOR_DPI_AWARE = 2
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            _user32.SetProcessDPIAware()
        except Exception:
            pass


def _set_ex_style(hwnd: int, add: int = 0, remove: int = 0) -> None:
    if not _IS_WINDOWS or not hwnd:
        return
    try:
        getter = _user32.GetWindowLongPtrW
        setter = _user32.SetWindowLongPtrW
    except AttributeError:  # pragma: no cover - 32 位
        getter = _user32.GetWindowLongW
        setter = _user32.SetWindowLongW
    try:
        style = getter(wintypes.HWND(hwnd), GWL_EXSTYLE)
        style = (style | add) & ~remove
        setter(wintypes.HWND(hwnd), GWL_EXSTYLE, style)
    except Exception:
        pass


def mark_as_tool_window(hwnd: int) -> None:
    """加 WS_EX_TOOLWINDOW、去 WS_EX_APPWINDOW：任务栏与 Alt+Tab 里都不出现。"""
    _set_ex_style(hwnd, add=WS_EX_TOOLWINDOW, remove=WS_EX_APPWINDOW)


def set_topmost(hwnd: int, on: bool) -> None:
    if not _IS_WINDOWS or not hwnd:
        return
    try:
        _user32.SetWindowPos(
            wintypes.HWND(hwnd),
            wintypes.HWND(HWND_TOPMOST if on else HWND_NOTOPMOST),
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
        )
    except Exception:
        pass


def restore_window(hwnd: int) -> None:
    """从最小化状态恢复（Win+D 之后要把卡片捞回来）。"""
    if not _IS_WINDOWS or not hwnd:
        return
    try:
        _user32.ShowWindow(wintypes.HWND(hwnd), 9)  # SW_RESTORE
    except Exception:
        pass


# ---------------------------------------------------------------- 云母


class _WINCOMPATTRDATA(ctypes.Structure):
    _fields_ = [
        ("Attribute", ctypes.c_int),
        ("Data", ctypes.c_void_p),
        ("SizeOfData", ctypes.c_size_t),
    ]


_DWMWA_NCRENDERING_POLICY = 2
_DWMWA_WINDOW_CORNER_PREFERENCE = 33
_DWMWCP_ROUNDSMALL = 3
_DWMWCP_DONOTROUND = 1
_DWMNCRP_ENABLED = 2


def _dwm_set(hwnd: int, attr: int, value: int) -> bool:
    """DwmSetWindowAttribute。系统不支持这个属性时返回非 0，静默忽略。"""
    if not _IS_WINDOWS:
        return False
    try:
        dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
        val = ctypes.c_int(value)
        hr = dwmapi.DwmSetWindowAttribute(
            wintypes.HWND(hwnd), attr, ctypes.byref(val), ctypes.sizeof(val)
        )
        return hr == 0
    except Exception:
        return False



def enable_mica(hwnd: int, tint: int = 0xC8F8F6F4) -> bool:
    """给窗口加 DWM 圆角与阴影，视觉上接近原生玻璃卡片。

    **不再铺 SYSTEMBACKDROP、也不调 DwmExtendFrameIntoClientArea。**

    实测结论（见 widget.py 顶部注释）：
    想让 DWM 的云母/亚克力透出来，客户区必须交给 DWM 绘制，
    同时 Tk 底板要用 -transparentcolor 抠成透明。而透明色键会**强制关闭
    文字的抗锯齿**（中间灰度占比从 55.2% 掉到 0.9%），字形严重像素化。
    二者在 Windows + Tk 上不可兼得，这里选择保住文字清晰度。

    现在只保留不影响内容绘制的部分：圆角、边框色、阴影。
    """
    if not _IS_WINDOWS or not hwnd:
        return False
    try:
        # 无边框窗口默认没有阴影，显式打开
        _dwm_set(hwnd, _DWMWA_NCRENDERING_POLICY, _DWMNCRP_ENABLED)
        # 圆角交给 DWM，与自绘的小圆角对齐
        ok = _dwm_set(hwnd, _DWMWA_WINDOW_CORNER_PREFERENCE, _DWMWCP_ROUNDSMALL)
        return bool(ok)
    except Exception:
        return False


def disable_mica(hwnd: int) -> None:
    """恢复系统默认的窗口外观。"""
    if not _IS_WINDOWS or not hwnd:
        return
    _dwm_set(hwnd, _DWMWA_WINDOW_CORNER_PREFERENCE, _DWMWCP_DONOTROUND)


# ---------------------------------------------------------------- 单实例

_mutex_handle = None


def acquire_single_instance() -> bool:
    """拿到互斥体返回 True；已经有一个实例在跑则返回 False。"""
    global _mutex_handle
    if not _IS_WINDOWS:
        return True
    try:
        handle = _kernel32.CreateMutexW(None, False, MUTEX_NAME)
        if not handle:
            return True
        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            return False
        _mutex_handle = handle
        return True
    except Exception:
        return True


# ---------------------------------------------------------------- 开机自启


def is_frozen() -> bool:
    """是否运行在 PyInstaller 打出的 exe 里。"""
    return bool(getattr(sys, "frozen", False))


def _run_command() -> str:
    """要写进注册表的启动命令。

    打包后：直接就是 exe 自己的路径。
    源码运行：用 pythonw 跑 run.pyw，不弹黑框。
    """
    from pathlib import Path

    if is_frozen():
        return f'"{Path(sys.executable).resolve()}"'

    root = Path(__file__).resolve().parent.parent.parent
    launcher = root / "run.pyw"
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    if not pythonw.exists():
        pythonw = exe
    if launcher.exists():
        return f'"{pythonw}" "{launcher}"'
    return f'"{pythonw}" -m desktop_memo'


def autostart_enabled() -> bool:
    if not _IS_WINDOWS:
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY) as key:
            winreg.QueryValueEx(key, AUTOSTART_NAME)
            return True
    except OSError:
        return False


def set_autostart(enabled: bool) -> bool:
    """写入/删除 Run 项，返回操作后是否处于自启状态。"""
    if not _IS_WINDOWS:
        return False
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, AUTOSTART_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            if enabled:
                winreg.SetValueEx(key, AUTOSTART_NAME, 0, winreg.REG_SZ, _run_command())
            else:
                try:
                    winreg.DeleteValue(key, AUTOSTART_NAME)
                except FileNotFoundError:
                    pass
    except OSError:
        pass
    return autostart_enabled()
