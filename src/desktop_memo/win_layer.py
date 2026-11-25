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


class _MARGINS(ctypes.Structure):
    _fields_ = [
        ("cxLeftWidth", ctypes.c_int),
        ("cxRightWidth", ctypes.c_int),
        ("cyTopHeight", ctypes.c_int),
        ("cyBottomHeight", ctypes.c_int),
    ]


_DWMWA_USE_IMMERSIVE_DARK_MODE = 20
_DWMWA_WINDOW_CORNER_PREFERENCE = 33
_DWMWA_SYSTEMBACKDROP_TYPE = 38
_DWMWA_BORDER_COLOR = 34
_DWMSBT_TRANSIENTWINDOW = 3   # 亚克力
_DWMWCP_ROUNDSMALL = 3
_DWMWCP_DONOTROUND = 1


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
    """给窗口铺上云母玻璃背景。

    踩过的坑（实测结论，别再换回去）：
    - SetWindowCompositionAttribute（老 P/Invoke 亚克力）会整块盖住 Tk 的绘制，
      文字全看不见，所以**不能用**。
    - DwmSetWindowAttribute(SYSTEMBACKDROP_TYPE) 与 Tk 共存良好，但必须同时
      DwmExtendFrameIntoClientArea(-1,-1,-1,-1)，把整个客户区交给 DWM 绘制。
    - 客户区交给 DWM 后，Tk 的底板色要设成透明色键，否则会盖住云母。

    返回是否成功。失败时调用方应保留不透明底板。
    """
    if not _IS_WINDOWS or not hwnd:
        return False
    try:
        dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
        # 1) 整个客户区交给 DWM
        margins = _MARGINS(-1, -1, -1, -1)
        hr = dwmapi.DwmExtendFrameIntoClientArea(
            wintypes.HWND(hwnd), ctypes.byref(margins)
        )
        if hr != 0:
            return False
        # 2) 背景材质：3 = Acrylic（即便系统不支持 Mica 也有玻璃质感）
        ok = _dwm_set(hwnd, _DWMWA_SYSTEMBACKDROP_TYPE, _DWMSBT_TRANSIENTWINDOW)
        if not ok:
            ok = _dwm_set(hwnd, _DWMWA_SYSTEMBACKDROP_TYPE, _DWMSBT_MAINWINDOW)
        # 3) 圆角与边框交给 DWM，边缘更干净
        _dwm_set(hwnd, _DWMWA_WINDOW_CORNER_PREFERENCE, _DWMWCP_ROUNDSMALL)
        _dwm_set(hwnd, _DWMWA_BORDER_COLOR, 0x00000000)
        _dwm_set(hwnd, _DWMWA_USE_IMMERSIVE_DARK_MODE, 0)
        return bool(ok)
    except Exception:
        return False


def disable_mica(hwnd: int) -> None:
    """撤掉背景材质，恢复普通不透明窗口。"""
    if not _IS_WINDOWS or not hwnd:
        return
    for attr in (_DWMWA_SYSTEMBACKDROP_TYPE,):
        _dwm_set(hwnd, attr, 0)
    try:
        dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
        margins = _MARGINS(0, 0, 0, 0)
        dwmapi.DwmExtendFrameIntoClientArea(
            wintypes.HWND(hwnd), ctypes.byref(margins)
        )
    except Exception:
        pass


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


def _run_command() -> str:
    """要写进注册表的启动命令：用 pythonw 跑 run.pyw，不弹黑框。"""
    from pathlib import Path

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
