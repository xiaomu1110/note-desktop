"""表现层 + 交互层：一张无边框圆角卡片。

实现要点：
- overrideredirect(True) 去掉系统边框与标题栏
- wm_attributes("-transparentcolor") 把卡片外部的洋红色像素变透明，
  于是圆角是真的圆角（Windows 上 Tk 唯一不靠图片做圆角的办法）
- 按住卡片任意位置拖动，位移超过 4px 才算拖动，否则算点击，
  这样一个手势同时满足了「拖动窗口」和「点文字改内容」
"""

from __future__ import annotations

import math
import tkinter as tk
from tkinter import font as tkfont

from .config import DEFAULT_HEIGHT, DEFAULT_WIDTH, MIN_HEIGHT, MIN_WIDTH
from .store import MemoStore

BG = "#FFFFFF"
INK = "#2B3648"
MUTED = "#8A96A8"
FAINT = "#C3CBD8"
LINE = "#EEF1F6"
ACCENT = "#F0A63C"
BORDER = "#E4E9F1"
MAGIC = "#FF00FE"  # 透明色键，卡片以外全是这个颜色

DRAG_THRESHOLD = 4


def _round_points(x0: float, y0: float, x1: float, y1: float, r: float, steps: int = 12):
    """生成圆角矩形轮廓点（逐个圆弧采样，比 smooth 多边形更准）。"""
    pts: list[float] = []
    corners = (
        (x1 - r, y0 + r, -90),
        (x1 - r, y1 - r, 0),
        (x0 + r, y1 - r, 90),
        (x0 + r, y0 + r, 180),
    )
    for cx, cy, a0 in corners:
        for i in range(steps + 1):
            ang = math.radians(a0 + 90.0 * i / steps)
            pts.extend((cx + r * math.cos(ang), cy + r * math.sin(ang)))
    return pts


class MemoCard:
    """桌面备忘录卡片。"""

    def __init__(self, store: MemoStore, on_quit=None) -> None:
        self.store = store
        self.on_quit = on_quit
        self._rows: dict[int, tuple[tk.Frame, tk.Canvas, tk.Label]] = {}
        self._editor: tuple[int, tk.Entry, tk.Label] | None = None
        self._press: tuple[int, int, int, int] | None = None
        self._moved = False
        self._resizing = False
        self._hiding = False
        self._menu: tk.Menu | None = None

        self.root = tk.Tk()
        self.root.withdraw()
        self.root.overrideredirect(True)
        self.root.configure(bg=MAGIC)
        try:
            self.root.wm_attributes("-transparentcolor", MAGIC)
        except tk.TclError:
            pass

        # 先拿到真实顶层窗口，才能问 Windows 真实 DPI
        self.root.update_idletasks()
        self.s = self._detect_scale()

        fams = set(tkfont.families(self.root))
        self.family = next(
            (f for f in ("Microsoft YaHei UI", "Microsoft YaHei", "微软雅黑", "Segoe UI")
             if f in fams),
            "TkDefaultFont",
        )
        self.f_item = tkfont.Font(family=self.family, size=10)
        self.f_done = tkfont.Font(family=self.family, size=10, overstrike=1)
        self.f_title = tkfont.Font(family=self.family, size=9)
        self.f_foot = tkfont.Font(family=self.family, size=8)
        self.f_plus = tkfont.Font(family=self.family, size=12)

        self._init_geometry()
        self._build()
        self._rebuild()
        self._bind_events()
        self.root.deiconify()
        self.root.update_idletasks()
        # 内容布局完成后强制回到我们定的尺寸（无边框窗口很容被内容撑开）
        self._apply_geometry()
        self._after_show()

    # ---------------------------------------------------------------- 几何

    def _detect_scale(self) -> float:
        """屏幕 DPI 相对 96 的倍数。

        winfo_fpixels('1i') 在部分 Windows 缩放设置下会撒谎（返回 96），
        所以以 GetDpiForWindow 为准，取不到再退回 Tk 的算法。
        """
        try:
            import ctypes

            hwnd = int(self.root.wm_frame(), 16)
            dpi = ctypes.windll.user32.GetDpiForWindow(ctypes.c_void_p(hwnd))
            if dpi:
                return max(1.0, dpi / 96.0)
        except Exception:
            pass
        try:
            return max(1.0, self.root.winfo_fpixels("1i") / 96.0)
        except Exception:
            return 1.0

    def _init_geometry(self) -> None:
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        rect = self.store.get_window_rect()
        if rect:
            x, y, w, h = rect
        else:
            w = int(DEFAULT_WIDTH * self.s)
            h = int(DEFAULT_HEIGHT * self.s)
            x = int(sw * 0.72)
            y = int(sh * 0.18)
        w = max(int(MIN_WIDTH * self.s), min(w, sw))
        h = max(int(MIN_HEIGHT * self.s), min(h, sh))
        # 防止上次在副屏、这次副屏已经拔掉，卡片跑到屏幕外面
        x = max(-w + 80, min(x, sw - 80))
        y = max(0, min(y, sh - 60))
        self._x, self._y, self._w, self._h = int(x), int(y), int(w), int(h)
        self._apply_geometry()

    def _apply_geometry(self) -> None:
        self.root.geometry(f"{self._w}x{self._h}+{self._x}+{self._y}")

    def _pad(self) -> int:
        return max(8, int(11 * self.s))

    # ---------------------------------------------------------------- 界面

    def _build(self) -> None:
        self.canvas = tk.Canvas(
            self.root, bg=MAGIC, highlightthickness=0, bd=0,
            width=self._w, height=self._h,
        )
        self.canvas.pack(fill="both", expand=True)

        self.body = tk.Frame(self.canvas, bg=BG, bd=0, highlightthickness=0)
        self._body_id = self.canvas.create_window(
            self._pad(), self._pad(), anchor="nw", window=self.body
        )
        self.body.grid_columnconfigure(0, weight=1)
        self.body.grid_rowconfigure(1, weight=1)

        # ---- 顶部
        head = tk.Frame(self.body, bg=BG)
        head.grid(row=0, column=0, sticky="ew", padx=3, pady=(0, 2))

        dot = tk.Canvas(head, width=7, height=7, bg=BG, highlightthickness=0, bd=0)
        dot.create_oval(1, 1, 6, 6, fill=ACCENT, outline=ACCENT)
        dot.pack(side="left", pady=(1, 0))

        tk.Label(
            head, text="备忘录", bg=BG, fg=MUTED, font=self.f_title, anchor="w"
        ).pack(side="left", padx=(5, 0))

        self.btn_plus = tk.Label(
            head, text="＋", bg=BG, fg=FAINT, font=self.f_plus, cursor="hand2"
        )
        self.btn_plus.pack(side="right")
        self.btn_plus.bind("<Button-1>", lambda e: self._add_item())
        self.btn_plus.bind("<Enter>", lambda e: self.btn_plus.configure(fg=ACCENT))
        self.btn_plus.bind("<Leave>", lambda e: self.btn_plus.configure(fg=FAINT))

        # ---- 中部列表
        self.list_canvas = tk.Canvas(
            self.body, bg=BG, highlightthickness=0, bd=0
        )
        self.list_canvas.grid(row=1, column=0, sticky="nsew", padx=3)
        self.rows = tk.Frame(self.list_canvas, bg=BG)
        self._rows_id = self.list_canvas.create_window(
            (0, 0), window=self.rows, anchor="nw"
        )

        # ---- 底部
        foot = tk.Frame(self.body, bg=BG)
        foot.grid(row=2, column=0, sticky="ew", padx=3, pady=(2, 0))
        self.lbl_status = tk.Label(
            foot, text="已自动保存", bg=BG, fg=FAINT, font=self.f_foot, anchor="w"
        )
        self.lbl_status.pack(side="left")

        self.grip = tk.Canvas(
            foot, width=11, height=11, bg=BG, highlightthickness=0, bd=0,
            cursor="bottom_right_corner",
        )
        self.grip.pack(side="right")
        for i in range(3):
            o = 1 + i * 4
            self.grip.create_line(o, 10, 10, o, fill=FAINT, width=1)

    # ---------------------------------------------------------------- 绘制

    def _draw_bg(self) -> None:
        w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
        if w <= 1 or h <= 1:
            return
        r = max(10, int(15 * self.s))
        self.canvas.delete("bg")
        self.canvas.create_polygon(
            _round_points(0.5, 0.5, w - 0.5, h - 0.5, r),
            fill=BG, outline=BORDER, width=1, tags="bg",
        )
        self.canvas.tag_lower("bg")

    # ---------------------------------------------------------------- 列表渲染

    def _rebuild(self) -> None:
        if self._editor:
            self._editor = None
        for child in self.rows.winfo_children():
            child.destroy()
        self._rows.clear()

        memos = self.store.list_memos()
        for row_data in memos:
            self._make_row(int(row_data["id"]), row_data["text"], bool(row_data["done"]))

        self._update_wrap()
        self.lbl_status.configure(
            text=f"已自动保存 · {self.store.count()} 条" if self.store.count() else "点 ＋ 添加"
        )
        self.list_canvas.yview_moveto(0.0)

    def _make_row(self, memo_id: int, text: str, done: bool) -> None:
        row = tk.Frame(self.rows, bg=BG)
        row.pack(fill="x", pady=(0, 5))

        cs = max(12, int(13 * self.s))
        cb = tk.Canvas(row, width=cs, height=cs, bg=BG, highlightthickness=0, bd=0,
                       cursor="hand2")
        cb.pack(side="left", anchor="n", pady=(2, 0))
        self._draw_check(cb, cs, done)

        lbl = tk.Label(
            row, text=text or "（空）", bg=BG,
            fg=FAINT if done else INK,
            font=self.f_done if done else self.f_item,
            anchor="w", justify="left", cursor="xterm",
        )
        lbl.pack(side="left", fill="x", expand=True, padx=(7, 0))

        cb.bind("<Button-1>", lambda e, i=memo_id: self._toggle(i))
        lbl.bind("<ButtonRelease-1>", lambda e, i=memo_id: self._on_row_click(i))
        for widget in (row, cb, lbl):
            widget.bind("<Button-3>", lambda e, i=memo_id: self._row_menu(e, i))

        self._rows[memo_id] = (row, cb, lbl)

    def _draw_check(self, cb: tk.Canvas, cs: int, done: bool) -> None:
        cb.delete("all")
        m = 1
        if done:
            cb.create_oval(m, m, cs - m, cs - m, fill=ACCENT, outline=ACCENT)
            cb.create_line(
                cs * 0.28, cs * 0.52, cs * 0.44, cs * 0.68, cs * 0.72, cs * 0.34,
                fill="#FFFFFF", width=max(1, int(1.6 * self.s)),
                capstyle="round", joinstyle="round",
            )
        else:
            cb.create_oval(m, m, cs - m, cs - m, fill=BG, outline=FAINT, width=1)

    def _update_wrap(self) -> None:
        try:
            inner = self.list_canvas.winfo_width()
        except tk.TclError:
            return
        if inner <= 1:
            inner = self._w - 2 * self._pad()
        wrap = max(int(80 * self.s), inner - int(13 * self.s) - int(14 * self.s))
        for _row, _cb, lbl in self._rows.values():
            lbl.configure(wraplength=wrap)

    # ---------------------------------------------------------------- 条目操作

    def _add_item(self) -> None:
        memo_id = self.store.add_memo("")
        self._rebuild()
        self.root.update_idletasks()
        self.list_canvas.yview_moveto(1.0)
        self._begin_edit(memo_id)

    def _toggle(self, memo_id: int) -> None:
        self.store.set_done(memo_id, not self.store.is_done(memo_id))
        self._rebuild()
        self.lbl_status.configure(text="已自动保存")

    def _on_row_click(self, memo_id: int) -> None:
        if self._moved:  # 这一下是拖动，不是点击
            return
        self._begin_edit(memo_id)

    def _row_menu(self, event: tk.Event, memo_id: int) -> None:
        if self._menu is not None:
            try:
                self._menu.destroy()
            except tk.TclError:
                pass
        self._menu = tk.Menu(self.root, tearoff=0)
        self._menu.add_command(label="编辑", command=lambda: self._begin_edit(memo_id))
        self._menu.add_command(label="删除", command=lambda: self._delete_item(memo_id))
        try:
            self._menu.tk_popup(event.x_root, event.y_root)
        finally:
            pass

    def _delete_item(self, memo_id: int) -> None:
        self.store.delete_memo(memo_id)
        self._rebuild()
        self.lbl_status.configure(text="已自动保存")

    # ---------------------------------------------------------------- 行内编辑

    def _begin_edit(self, memo_id: int) -> None:
        if self._editor:
            self._finish_edit()
        item = self._rows.get(memo_id)
        if not item:
            return
        _row, _cb, lbl = item
        text = self.store.get_text(memo_id)
        lbl.pack_forget()

        entry = tk.Entry(
            self.rows, bd=0, relief="flat", bg="#F7F9FC", fg=INK,
            insertbackground=INK, font=self.f_item,
            highlightthickness=1, highlightbackground="#DCE3EE",
            highlightcolor=ACCENT,
        )
        entry.pack(side="left", fill="x", expand=True, padx=(7, 0))
        entry.insert(0, text)
        entry.select_range(0, "end")
        entry.icursor("end")
        self._editor = (memo_id, entry, lbl)

        entry.bind("<Return>", lambda e: self._finish_edit())
        entry.bind("<Escape>", lambda e: self._finish_edit(commit=False))
        entry.bind("<FocusOut>", lambda e: self._finish_edit())
        self.root.after(10, self._focus_entry)

    def _focus_entry(self) -> None:
        if not self._editor:
            return
        _mid, entry, _lbl = self._editor
        try:
            entry.focus_force()
        except tk.TclError:
            pass

    def _finish_edit(self, commit: bool = True) -> None:
        if not self._editor:
            return
        memo_id, entry, _lbl = self._editor
        self._editor = None
        try:
            text = entry.get().strip()
        except tk.TclError:
            text = ""
        try:
            entry.destroy()
        except tk.TclError:
            pass

        if commit:
            if text:
                self.store.update_text(memo_id, text)
            else:
                self.store.delete_memo(memo_id)
            self.lbl_status.configure(text="已自动保存")
        self._rebuild()

    # ---------------------------------------------------------------- 拖动 / 缩放

    def _bind_events(self) -> None:
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.list_canvas.bind("<Configure>", self._on_list_configure)
        self.rows.bind("<Configure>", self._on_rows_configure)

        self.root.bind("<ButtonPress-1>", self._on_press, add="+")
        self.root.bind("<B1-Motion>", self._on_motion, add="+")
        self.root.bind("<ButtonRelease-1>", self._on_release, add="+")
        self.root.bind("<MouseWheel>", self._on_wheel, add="+")
        self.root.bind("<Unmap>", self._on_unmap, add="+")
        self.root.bind("<Escape>", lambda e: self._abandon(), add="+")

        self.grip.bind("<ButtonPress-1>", self._grip_press)
        self.grip.bind("<B1-Motion>", self._grip_motion)
        self.grip.bind("<ButtonRelease-1>", self._grip_release)

    def _on_canvas_configure(self, event: tk.Event) -> None:
        pad = self._pad()
        self.canvas.coords(self._body_id, pad, pad)
        self.canvas.itemconfigure(
            self._body_id,
            width=max(1, event.width - 2 * pad),
            height=max(1, event.height - 2 * pad),
        )
        self._draw_bg()

    def _on_list_configure(self, event: tk.Event) -> None:
        self.list_canvas.itemconfigure(self._rows_id, width=event.width)
        self._update_wrap()

    def _on_rows_configure(self, _event: tk.Event) -> None:
        self.list_canvas.configure(scrollregion=self.list_canvas.bbox("all"))

    def _on_wheel(self, event: tk.Event) -> None:
        try:
            self.list_canvas.yview_scroll(int(-event.delta / 120), "units")
        except tk.TclError:
            pass

    def _on_press(self, event: tk.Event) -> None:
        if self._resizing:
            return
        self._press = (event.x_root, event.y_root, self._x, self._y)
        self._moved = False

    def _on_motion(self, event: tk.Event) -> None:
        if self._press is None or self._resizing:
            return
        px, py, wx, wy = self._press
        dx, dy = event.x_root - px, event.y_root - py
        if not self._moved and abs(dx) < DRAG_THRESHOLD and abs(dy) < DRAG_THRESHOLD:
            return
        self._moved = True
        self._move_to(wx + dx, wy + dy)

    def _on_release(self, _event: tk.Event) -> None:
        if self._moved:
            self._save_rect()
        self._press = None
        # 子控件先收到 release，这里稍后再复位，避免误判成点击
        self._schedule(120, self._clear_moved)

    def _schedule(self, ms: int, fn) -> None:  # noqa: ANN001
        """窗口销毁后 after 回调会抛 TclError，统一在这里吞掉。"""
        try:
            self.root.after(ms, fn)
        except (tk.TclError, RuntimeError):
            pass

    def _clear_moved(self) -> None:
        self._moved = False

    def _move_to(self, x: int, y: int) -> None:
        self._x, self._y = int(x), int(y)
        self.root.geometry(f"+{self._x}+{self._y}")

    def _grip_press(self, event: tk.Event):
        self._resizing = True
        self._rsz = (event.x_root, event.y_root, self._w, self._h)
        return "break"

    def _grip_motion(self, event: tk.Event):
        if not self._resizing:
            return "break"
        ox, oy, ow, oh = self._rsz
        w = max(int(MIN_WIDTH * self.s), int(ow + event.x_root - ox))
        h = max(int(MIN_HEIGHT * self.s), int(oh + event.y_root - oy))
        self._w, self._h = w, h
        self.root.geometry(f"{w}x{h}+{self._x}+{self._y}")
        return "break"

    def _grip_release(self, _event: tk.Event):
        self._resizing = False
        self._save_rect()
        return "break"

    def _save_rect(self) -> None:
        self.store.save_window_rect(self._x, self._y, self._w, self._h)

    # ---------------------------------------------------------------- 显示 / 隐藏

    def _on_unmap(self, _event: tk.Event) -> None:
        """Win+D / 最小化之后把卡片捞回来：toolwindow 没有任务栏按钮，丢了就找不回。"""
        if self._hiding:
            return
        self._schedule(80, self._restore)

    def _restore(self) -> None:
        if self._hiding:
            return
        try:
            if self.root.state() == "iconic":
                self.root.deiconify()
        except tk.TclError:
            pass

    def _after_show(self) -> None:
        self._draw_bg()
        self._update_wrap()

    def hide(self) -> None:
        self._save_rect()
        self._hiding = True
        self.root.withdraw()

    def show(self) -> None:
        self._hiding = False
        self.root.deiconify()
        self.root.geometry(f"{self._w}x{self._h}+{self._x}+{self._y}")

    def toggle(self) -> None:
        if self.root.state() == "normal" and not self._hiding:
            self.hide()
        else:
            self.show()

    def _abandon(self) -> None:
        if self._editor:
            self._finish_edit(commit=False)

    def run(self) -> None:
        self.root.mainloop()

    def destroy(self) -> None:
        try:
            self.root.destroy()
        except tk.TclError:
            pass
