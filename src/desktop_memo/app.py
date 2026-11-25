"""组装层：把存储、卡片、托盘、本地 API 装到一起，管好它们的生命周期。

线程模型：
- Tk / SQLite 只在主线程操作（Tk 不是线程安全的）
- 托盘回调在独立线程，只往 queue 塞事件
- HTTP API 在独立线程，需要动 Tk 时把任务排进 _main_tasks，
  由主线程的 after() 轮询取出执行
"""

from __future__ import annotations

import queue
import sys
import threading
import tkinter as tk

from . import win_layer
from .api import Controller, start_server
from .config import API_PORT, APP_TITLE, db_path
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


class LiveController(Controller):
    """API 线程 -> 主线程的桥。

    跨线程的 Tk 操作一律 submit 到主线程执行，调用方阻塞等结果。
    """

    def __init__(self, store: MemoStore, card: MemoCard) -> None:
        self.store = store
        self.card = card
        self._tasks: queue.Queue = queue.Queue()
        self._lock = threading.Lock()  # 保证 SQLite 写不被并发打乱
        self._quit_cb = None
        self._autostart_get = win_layer.autostart_enabled

    # ---------------------------------------------------------------- 桥接

    def set_quit_callback(self, cb) -> None:  # noqa: ANN001
        self._quit_cb = cb

    def _run_on_main(self, fn, timeout: float = 5.0):  # noqa: ANN001
        """把一个函数丢到主线程执行并等结果。"""
        box: dict = {}
        done = threading.Event()

        def wrapper() -> None:
            try:
                box["value"] = fn()
            except Exception as exc:  # 把异常带回调用线程
                box["error"] = exc
            finally:
                done.set()

        self._tasks.put(wrapper)
        if not done.wait(timeout):
            raise RuntimeError("主线程响应超时")
        if "error" in box:
            raise box["error"]
        return box.get("value")

    def pump_tasks(self) -> None:
        """主线程定期调用，执行 API 线程排进来的任务。"""
        try:
            while True:
                task = self._tasks.get_nowait()
                task()
        except queue.Empty:
            pass

    # ---------------------------------------------------------------- 读

    def get_state(self) -> dict:
        def snapshot() -> dict:
            return {
                "running": True,
                "visible": not self.card._hiding,
                "autostart": bool(self._autostart_get()),
                "count": self.store.count(),
                "api": {"host": "127.0.0.1", "port": API_PORT},
            }

        return self._run_on_main(snapshot)

    def list_items(self) -> list[dict]:
        with self._lock:
            return [
                {
                    "id": int(r["id"]),
                    "text": r["text"],
                    "done": bool(r["done"]),
                    "pos": int(r["pos"]),
                }
                for r in self.store.list_memos()
            ]

    # ---------------------------------------------------------------- 写

    def add_item(self, text: str) -> dict:
        with self._lock:
            memo_id = self.store.add_memo(text)
            row = next(
                (r for r in self.store.list_memos() if int(r["id"]) == memo_id),
                None,
            )
            item = {
                "id": memo_id,
                "text": row["text"] if row else text,
                "done": False,
                "pos": int(row["pos"]) if row else -1,
            }

        def redraw() -> None:
            self.card.render()

        self._run_on_main(redraw)
        return item

    def update_item(self, memo_id: int, text: object, done: object) -> dict:
        with self._lock:
            if not self.store.get_text(memo_id) and not self._exists(memo_id):
                raise LookupError(f"条目 {memo_id} 不存在")
            if text is not None:
                if not isinstance(text, str) or not text.strip():
                    raise ValueError("text 不能为空")
                self.store.update_text(memo_id, text.strip())
            if done is not None:
                if not isinstance(done, bool):
                    raise ValueError("done 必须是布尔值")
                self.store.set_done(memo_id, done)
            item = {
                "id": memo_id,
                "text": self.store.get_text(memo_id),
                "done": self.store.is_done(memo_id),
            }

        self._run_on_main(self.card.render)
        return item

    def _exists(self, memo_id: int) -> bool:
        return any(int(r["id"]) == memo_id for r in self.store.list_memos())

    def delete_item(self, memo_id: int) -> dict:
        with self._lock:
            if not self._exists(memo_id):
                raise LookupError(f"条目 {memo_id} 不存在")
            self.store.delete_memo(memo_id)
            remaining = self.store.count()

        self._run_on_main(self.card.render)
        return {"deleted": memo_id, "remaining": remaining}

    def set_autostart(self, enabled: bool) -> bool:
        return win_layer.set_autostart(enabled)

    def set_visible(self, visible: bool) -> bool:
        def apply() -> bool:
            if visible:
                self.card.show()
            else:
                self.card.hide()
            return not self.card._hiding

        return self._run_on_main(apply)

    def shutdown(self) -> dict:
        def do_quit() -> None:
            if self._quit_cb is not None:
                self._quit_cb()

        # 异步排进去，先把响应发回去再退出
        threading.Timer(0.15, lambda: self._run_on_main(do_quit, timeout=2)).start()
        return {"ok": True, "message": "正在退出"}


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
            api.stop()
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
        controller.pump_tasks()
        card.root.after(80, _poll)

    controller = LiveController(store, card)
    controller.set_quit_callback(lambda: None)
    api = start_server(controller)
    if api is None:
        print(
            f"本地 API 启动失败（端口 {API_PORT} 可能被占用），"
            "界面功能不受影响。",
            file=sys.stderr,
        )
    else:
        controller.set_quit_callback(_quit)

    card.root.protocol("WM_DELETE_WINDOW", _quit)

    if hidden:
        card.hide()

    card.root.after(80, _poll)
    card.run()

    try:
        tray.stop()
    except Exception:
        pass
    try:
        if api is not None:
            api.stop()
    except Exception:
        pass
    store.close()
    return 0