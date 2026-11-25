"""系统托盘图标。

Tk 没有原生托盘，这里直接用 ctypes 调 Shell_NotifyIcon：
在独立线程里建一个消息专用窗口，跑自己的消息循环，
收到点击就扔进 queue，主线程用 after() 轮询取走 —— 两个线程互不碰对方的对象。

对外只有三个东西：start() / stop() / events
"""

from __future__ import annotations

import ctypes
import queue
import sys
import threading
from ctypes import wintypes as w

from .config import asset_path

WM_APP = 0x8000
WM_TRAY = WM_APP + 1
WM_CLOSE = 0x0010
WM_DESTROY = 0x0002

NIF_MESSAGE = 0x01
NIF_ICON = 0x02
NIF_TIP = 0x04
NIM_ADD = 0
NIM_DELETE = 2

WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205

IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
IDI_APPLICATION = 32512
HWND_MESSAGE = -3

SM_CXSMICON = 49
SM_CYSMICON = 50


def _int_resource(value: int) -> w.LPCWSTR:
    return ctypes.cast(ctypes.c_void_p(value), w.LPCWSTR)


class NOTIFYICONDATAW(ctypes.Structure):
    """Shell_NotifyIcon 用的结构体（V3，64 位下 976 字节）。

    szTip 长度为 128 是 V1 的写法，这里按 V3 补齐，Windows 会校验 cbSize。
    """

    _fields_ = [
        ("cbSize", w.DWORD),
        ("hWnd", w.HWND),
        ("uID", w.UINT),
        ("uFlags", w.UINT),
        ("uCallbackMessage", w.UINT),
        ("hIcon", w.HICON),
        ("szTip", w.WCHAR * 128),
        ("dwState", w.DWORD),
        ("dwStateMask", w.DWORD),
        ("szInfo", w.WCHAR * 256),
        ("uVersion", w.UINT),
        ("szInfoTitle", w.WCHAR * 64),
        ("dwInfoFlags", w.DWORD),
        ("guidItem", ctypes.c_byte * 16),
        ("hBalloonIcon", w.HICON),
    ]


WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, w.HWND, w.UINT, w.WPARAM, w.LPARAM)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", w.UINT),
        ("style", w.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", w.HINSTANCE),
        ("hIcon", w.HICON),
        ("hCursor", w.HANDLE),
        ("hbrBackground", w.HBRUSH),
        ("lpszMenuName", w.LPCWSTR),
        ("lpszClassName", w.LPCWSTR),
        ("hIconSm", w.HICON),
    ]


_IS_WINDOWS = sys.platform == "win32"

if _IS_WINDOWS:
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _shell32 = ctypes.WinDLL("shell32", use_last_error=True)

    _user32.CreateWindowExW.restype = w.HWND
    _user32.CreateWindowExW.argtypes = [
        w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        w.HWND, w.HMENU, w.HINSTANCE, w.LPVOID,
    ]
    _user32.DefWindowProcW.restype = ctypes.c_ssize_t
    _user32.DefWindowProcW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
    _user32.RegisterClassExW.restype = w.ATOM
    _user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
    _user32.GetMessageW.argtypes = [ctypes.POINTER(w.MSG), w.HWND, w.UINT, w.UINT]
    _user32.DestroyWindow.argtypes = [w.HWND]
    _user32.PostMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
    _user32.GetCursorPos.argtypes = [ctypes.POINTER(w.POINT)]
    _user32.LoadImageW.restype = w.HANDLE
    _user32.LoadImageW.argtypes = [
        w.HINSTANCE, w.LPCWSTR, w.UINT, ctypes.c_int, ctypes.c_int, w.UINT
    ]
    _user32.LoadIconW.restype = w.HICON
    _user32.LoadIconW.argtypes = [w.HINSTANCE, w.LPCWSTR]
    _user32.GetSystemMetrics.restype = ctypes.c_int
    _user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    _user32.PostQuitMessage.argtypes = [ctypes.c_int]
    _shell32.Shell_NotifyIconW.restype = w.BOOL
    _shell32.Shell_NotifyIconW.argtypes = [w.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]


class TrayIcon:
    """托盘图标。events 里会出现 ("toggle"|"menu", x, y)。"""

    def __init__(self, tooltip: str = "备忘录") -> None:
        self.tooltip = tooltip[:127]
        self.events: queue.Queue = queue.Queue()
        self._hwnd = 0
        self._hicon = 0
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._wnd_proc = WNDPROC(self._on_message)  # 必须留在实例上，否则回调被回收
        self._taskbar_created = 0
        self._icon_taskbar_created = 0

    # ---------------------------------------------------------------- 外部接口

    def start(self) -> bool:
        """起线程并等图标挂上去，返回是否成功。"""
        if not _IS_WINDOWS:
            return False
        self._thread = threading.Thread(
            target=self._run, name="tray", daemon=True
        )
        self._thread.start()
        self._ready.wait(timeout=4.0)
        return self._hwnd != 0

    def stop(self) -> None:
        if self._hwnd:
            _user32.PostMessageW(w.HWND(self._hwnd), WM_CLOSE, 0, 0)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    # ---------------------------------------------------------------- 线程内部

    def _load_icon(self) -> int:
        path = asset_path("memo.ico")
        if path.exists():
            cx = _user32.GetSystemMetrics(SM_CXSMICON) or 16
            cy = _user32.GetSystemMetrics(SM_CYSMICON) or 16
            h = _user32.LoadImageW(
                None, str(path), IMAGE_ICON, cx, cy, LR_LOADFROMFILE
            )
            if not h:
                h = _user32.LoadImageW(
                    None, str(path), IMAGE_ICON, 0, 0, LR_LOADFROMFILE
                )
            if h:
                return int(h)
        return int(_user32.LoadIconW(None, _int_resource(IDI_APPLICATION)) or 0)

    def _add_icon(self) -> None:
        if not self._hwnd:
            return
        if not self._hicon:
            self._hicon = self._load_icon()
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = w.HWND(self._hwnd)
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAY
        nid.hIcon = w.HICON(self._hicon)
        nid.szTip = self.tooltip
        if not _shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)):
            self._icon_taskbar_created = 1

    def _del_icon(self) -> None:
        if not self._hwnd:
            return
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = w.HWND(self._hwnd)
        nid.uID = 1
        try:
            _shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
        except Exception:
            pass

    def _on_message(self, hwnd, msg, wparam, lparam):  # noqa: ANN001
        try:
            if self._taskbar_created and msg == self._taskbar_created:
                self._add_icon()
                return 0
            if msg == WM_TRAY:
                kind = int(lparam) & 0xFFFF
                if kind in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                    self.events.put(("toggle", 0, 0))
                elif kind == WM_RBUTTONUP:
                    pt = w.POINT()
                    _user32.GetCursorPos(ctypes.byref(pt))
                    self.events.put(("menu", int(pt.x), int(pt.y)))
                return 0
            if msg == WM_CLOSE:
                self._del_icon()
                _user32.DestroyWindow(w.HWND(hwnd))
                return 0
            if msg == WM_DESTROY:
                _user32.PostQuitMessage(0)
                return 0
        except Exception:
            pass
        return _user32.DefWindowProcW(w.HWND(hwnd), msg, wparam, lparam)

    def _run(self) -> None:
        try:
            hinst = _kernel32.GetModuleHandleW(None)
            class_name = f"DesktopMemoTray_{id(self)}"
            self._taskbar_created = _user32.RegisterWindowMessageW("TaskbarCreated")

            wc = WNDCLASSEXW()
            wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
            wc.lpfnWndProc = self._wnd_proc
            wc.hInstance = wintypes_hinstance(hinst)
            wc.lpszClassName = class_name
            if not _user32.RegisterClassExW(ctypes.byref(wc)):
                return

            hwnd = _user32.CreateWindowExW(
                0, class_name, class_name, 0,
                0, 0, 0, 0,
                w.HWND(HWND_MESSAGE), None, wintypes_hinstance(hinst), None,
            )
            if not hwnd:
                return
            self._hwnd = int(hwnd)
            self._add_icon()
            self._ready.set()

            msg = w.MSG()
            while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                _user32.TranslateMessage(ctypes.byref(msg))
                _user32.DispatchMessageW(ctypes.byref(msg))
        except Exception:
            pass
        finally:
            self._hwnd = 0
            self._ready.set()


def wintypes_hinstance(handle) -> w.HINSTANCE:  # noqa: ANN001
    """GetModuleHandleW 返回裸句柄，这里补上类型。"""
    return ctypes.cast(handle, w.HINSTANCE) if handle else w.HINSTANCE(0)
