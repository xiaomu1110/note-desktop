"""组装层：把存储、卡片、托盘装到一起，管好三者的生命周期。

托盘回调跑在独立线程里，绝不能直接在那边碰 Tk 对象，
所以托盘只往 queue 里塞事件，主线程用 after() 轮询取出来执行。
"""

from __future__ import annotations

import queue
import sys
import tkinter as tk

from . import win_layer
from .config import APP_TITLE, db_path
from .store import MemoStore
from .tray import TrayIcon
from .widget import MemoCard


def _hwnd_of(root: tk.Tk) -> int:
    """拿到真正的顶层窗口句柄。wm_frame() 在 Windows 上返回顶层 HWND 的十六进制串。"""
    try:
        return int(root.wm_frame(), 16)
    except (ValueError, tk.TclError):
        try:
            return int(root.winfo_id())
        except tk.TclError:
            return 0


def main(argv: list[str] | None = None, hidden: bool = False) -> int:
    win_layer.enable_dpi_awareness()

    if not win_layer.acquire_single_instance():
        # 只允许一个实例：第二个人启动时安静退出，避免两张卡片打架
        print("桌面备忘录已经在运行了。")
        return 0

    store = MemoStore(db_path())
    card = MemoCard(store)
    card.root.update_idletasks()

    hwnd = _hwnd_of(card.root)
    if hwnd:
        win_layer.mark_as_tool_window(hwnd)

    tray = TrayIcon(APP_TITLE)
    if not tray.start():
        print("托盘图标创建失败，程序继续运行（可以用 Esc 收起卡片）。", file=sys.stderr)

    autostart_var = tk.BooleanVar(master=card.root, value=win_layer.autostart_enabled())
    menu = tk.Menu(card.root, tearoff=0)

    def toggle_autostart() -> None:
        win_layer.set_autostart(bool(autostart_var.get()))

    menu.add_command(label="显示 / 隐藏卡片", command=card.toggle)
    menu.add_separator()
    menu.add_checkbutton(
        label="开机自动启动", variable=autostart_var, command=toggle_autostart
    )
    menu.add_separator()
    menu.add_command(label="退出", command=lambda: _quit())

    def _quit() -> None:
        card._save_rect()
        try:
            tray.stop()
        except Exception:
            pass
        try:
            card.root.quit()
        except tk.TclError:
            pass

    def _popup(x: int, y: int) -> None:
        try:
            menu.tk_popup(x, y)
        except tk.TclError:
            pass

    def _poll() -> None:
        try:
            while True:
                kind, x, y = tray.events.get_nowait()
                if kind == "toggle":
                    card.toggle()
                elif kind == "menu":
                    _popup(x, y)
        except queue.Empty:
            pass
        card.root.after(80, _poll)

    card.root.protocol("WM_DELETE_WINDOW", _quit)

    if hidden:
        card.hide()

    card.root.after(80, _poll)
    card.run()

    try:
        tray.stop()
    except Exception:
        pass
    store.close()
    return 0
