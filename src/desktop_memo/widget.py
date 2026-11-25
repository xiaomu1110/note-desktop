"""表现层 + 交互层：一张无边框圆角卡片（纯 Canvas 自绘）。

为什么要"纯 Canvas"：
Windows 的亚克力/云母合成层只对窗口本身的绘制生效，
Tk 的 Frame / Label 是独立的子窗口（HWND），会被合成层挡在后面看不见。
所以整个卡片只用**一个** Canvas 画所有东西，文字、圆圈、图标全是 canvas item。
这样云母才能透出来，内容也始终可见。

交互：
- 按住任意位置拖动，位移超过阈值才算拖动，否则算点击
- 单击文字 → 在该位置弹出内嵌输入框；回车保存，Esc 取消，点别处也保存
- 单击圆圈 → 切换完成状态
- 右上角 ＋ → 新增并编辑；右键条目 → 菜单
"""

from __future__ import annotations

import math
import os
import tkinter as tk
from tkinter import font as tkfont

from .config import DEFAULT_HEIGHT, DEFAULT_WIDTH, MIN_HEIGHT, MIN_WIDTH
from .store import MemoStore
from .win_layer import enable_mica, mark_as_tool_window

# ---------------------------------------------------------------- 配色
GLASS = "#F4F7FB"          # 云母不可用时的底板色，也是子控件底色
INK = "#2B3648"            # 正文
MUTED = "#5F6B7F"          # 标题
FAINT = "#A3ADBE"          # 次要信息、占位
ACCENT = "#EE9B2E"         # 主色：圆点、已完成的勾
BORDER = "#D9E1EC"         # 卡片描边
DONE_TEXT = "#9AA4B5"      # 已完成条目的文字

CARD_RADIUS = 7            # 圆角半径（96 DPI 基准）：从 15 调小到 7
# 云母模式下由 DWM 负责裁圆角（DWMWCP_ROUNDSMALL ≈ 4px），
# 自绘描边要跟着收，否则会出现两条弧线
MICA_RADIUS = 4
DRAG_THRESHOLD = 4
ROW_GAP = 6                # 条目间距
ROW_H = 22                 # 条目基准高度

# 云母玻璃色调，0xAABBGGRR：AA 是不透明度，后面是 BGR
MICA_TINT = 0xC8F8F6F4
MICA_ENABLED = os.environ.get("DESKTOP_MEMO_NO_MICA") != "1"

# 透明色键：云母生效时，卡片底板用这个颜色，Windows 会把它抠成透明，
# 从而让 DWM 绘制的云母玻璃露出来。必须选一个正文里绝不会出现的深色。
# 实测对比（彩边像素数，越小越干净）：
#   #010101 → 138（最佳）   #00FF00 → 142   #FF00FF → 319   #0000FF → 453
# 所以用近黑灰，而不是习惯上的洋红。
MAGIC = "#010101"

FONT_CANDIDATES = ("Microsoft YaHei UI", "微软雅黑", "Microsoft YaHei")


def _round_points(x0: float, y0: float, x1: float, y1: float, r: float, steps: int = 10):
    """圆角矩形轮廓：四个角各采样一段圆弧。"""
    pts: list[float] = []
    corners = (
        (x1 - r, y0 + r, -90.0),
        (x1 - r, y1 - r, 0.0),
        (x0 + r, y1 - r, 90.0),
        (x0 + r, y0 + r, 180.0),
    )
    for cx, cy, a0 in corners:
        for i in range(steps + 1):
            ang = math.radians(a0 + 90.0 * i / steps)
            pts.extend((cx + r * math.cos(ang), cy + r * math.sin(ang)))
    return pts


class MemoCard:
    """桌面备忘录卡片。"""

    # 布局常量（逻辑像素，实际乘以缩放比）
    PAD = 11
    HEAD_H = 22
    FOOT_H = 16

    def __init__(self, store: MemoStore) -> None:
        self.store = store

        # ---- 行模型：每个条目在 canvas 上占据的矩形与文本
        self._rows: list[dict] = []
        self._editor: dict | None = None
        self._menu: tk.Menu | None = None

        # ---- 拖动 / 缩放状态
        self._press: tuple[int, int, int, int] | None = None
        self._moved = False
        self._resizing = False
        self._hiding = False
        self._scroll = 0.0
        self.mica = False          # 必须在首次 render 之前就存在
        self._hwnd = 0

        self.root = tk.Tk()
        self.root.withdraw()
        self.root.overrideredirect(True)
        self.root.configure(bg=GLASS)
        self.root.update_idletasks()

        self.s = self._detect_scale()

        fams = set(tkfont.families(self.root))
        self.family = next((f for f in FONT_CANDIDATES if f in fams), "TkDefaultFont")
        self.f_item = tkfont.Font(family=self.family, size=10)
        self.f_done = tkfont.Font(family=self.family, size=10, overstrike=1)
        self.f_head = tkfont.Font(family=self.family, size=9)
        self.f_foot = tkfont.Font(family=self.family, size=8)
        self.f_plus = tkfont.Font(family=self.family, size=12)
        self.f_entry = tkfont.Font(family=self.family, size=10)

        # ---- 单一 Canvas，所有绘制都发生在它上面
        self.canvas = tk.Canvas(
            self.root, bg=GLASS, highlightthickness=0, bd=0,
            width=320, height=220,
        )
        self.canvas.pack(fill="both", expand=True)

        self._init_geometry()
        self._bind_events()
        self.root.deiconify()
        self.root.update_idletasks()
        self._apply_geometry()
        self._apply_mica()
        # 尺寸定下来之后再画，否则 winfo_width() 还是 1
        self.canvas.update_idletasks()
        self.render()

    # ---------------------------------------------------------------- DPI / 几何

    def _detect_scale(self) -> float:
        """以 GetDpiForWindow 为准，取不到再退回 Tk 的算法。"""
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
        x = max(-w + 80, min(x, sw - 80))
        y = max(0, min(y, sh - 60))
        self._x, self._y, self._w, self._h = int(x), int(y), int(w), int(h)

    def _apply_geometry(self) -> None:
        self.root.geometry(f"{self._w}x{self._h}+{self._x}+{self._y}")

    def _apply_mica(self) -> None:
        """挂上云母。失败就保持纯色底板，功能完全一致。"""
        if not MICA_ENABLED:
            return
        try:
            hwnd = int(self.root.wm_frame(), 16)
        except (ValueError, tk.TclError):
            hwnd = 0
        self._hwnd = hwnd
        if not hwnd:
            return
        try:
            self.mica = enable_mica(hwnd, tint=MICA_TINT)
        except Exception:
            self.mica = False

        if self.mica:
            # 底板改成透明色键，DWM 画的云母才能露出来
            self.root.configure(bg=MAGIC)
            self.canvas.configure(bg=MAGIC)
            try:
                self.root.wm_attributes("-transparentcolor", MAGIC)
            except tk.TclError:
                self.mica = False

        if self.mica:
            # DwmExtendFrameIntoClientArea 会重置扩展样式，必须把
            # WS_EX_TOOLWINDOW 补回去，否则卡片会重新出现在任务栏和 Alt+Tab
            mark_as_tool_window(hwnd)

        if not self.mica:
            # 云母不可用：退回不透明底板，保证文字可读
            self.root.configure(bg=GLASS)
            self.canvas.configure(bg=GLASS)

    def _pad(self) -> int:
        return max(8, int(self.PAD * self.s))

    def _row_h(self) -> int:
        return int(ROW_H * self.s)

    # ---------------------------------------------------------------- 绘制

    def render(self) -> None:
        """整张卡片重绘。数据有任何变化都只需要调这一个函数。"""
        c = self.canvas
        c.delete("all")
        W = c.winfo_width() or self._w
        H = c.winfo_height() or self._h
        if W <= 1 or H <= 1:
            return

        self._draw_backdrop(W, H)
        self._layout_rows(W, H)
        self._draw_header(W)
        self._draw_rows(W, H)
        self._draw_footer(W, H)

    def _draw_backdrop(self, W: int, H: int) -> None:
        base = MICA_RADIUS if self.mica else CARD_RADIUS
        r = max(3, int(base * self.s))
        pts = _round_points(0.5, 0.5, W - 0.5, H - 0.5, r)
        if self.mica:
            # 云母：不填色（填了就挡住合成层），只留一圈很淡的边
            self.canvas.create_polygon(pts, fill="", outline=BORDER, width=1, tags="bg")
        else:
            self.canvas.create_polygon(pts, fill=GLASS, outline=BORDER, width=1, tags="bg")

    def _layout_rows(self, W: int, H: int) -> None:
        """按文本宽度把每个条目排成若干行，记录各自的矩形，供点击命中使用。"""
        pad = self._pad()
        x0 = pad + int(3 * self.s)
        text_x = x0 + int(20 * self.s)
        wrap = max(60, W - text_x - pad - int(4 * self.s))

        y = pad + int(self.HEAD_H * self.s) + int(4 * self.s)
        self._rows = []

        for data in self.store.list_memos():
            mid = int(data["id"])
            text = data["text"] or "（空）"
            fnt = self.f_done if data["done"] else self.f_item
            # 粗算换行：逐字累加宽度
            lines: list[str] = []
            cur = ""
            for ch in text:
                if fnt.measure(cur + ch) <= wrap:
                    cur += ch
                else:
                    if cur:
                        lines.append(cur)
                    cur = ch
            if cur or not lines:
                lines.append(cur)

            lh = int(fnt.metrics("linespace"))
            row_h = max(self._row_h(), lh * len(lines) + int(2 * self.s))

            self._rows.append({
                "id": mid,
                "text": text,
                "done": bool(data["done"]),
                "lines": lines,
                "font": fnt,
                "x0": x0,
                "text_x": text_x,
                "y": y,
                "h": row_h,
                "lh": lh,
                "wrap": wrap,
                "box": int(13 * self.s),
            })
            y += row_h + int(ROW_GAP * self.s)

        self._content_bottom = y
        self._view_top = pad + int(self.HEAD_H * self.s) + int(4 * self.s)
        self._view_bottom = H - pad - int(self.FOOT_H * self.s) - int(4 * self.s)

    def _draw_header(self, W: int) -> None:
        pad = self._pad()
        cy = pad + int(5 * self.s)
        d = int(7 * self.s)
        self.canvas.create_oval(
            pad + 3, cy, pad + 3 + d, cy + d, fill=ACCENT, outline=ACCENT, tags="head"
        )
        self.canvas.create_text(
            pad + 3 + d + int(6 * self.s), cy + d / 2,
            text="备忘录", fill=MUTED, font=self.f_head, anchor="w", tags="head",
        )
        # ＋ 按钮，记录命中矩形
        px = W - pad - int(10 * self.s)
        self._plus_box = (px - int(12 * self.s), cy - int(4 * self.s),
                          px + int(12 * self.s), cy + int(4 * self.s) + int(10 * self.s))
        self.canvas.create_text(
            px, cy + d / 2, text="＋", fill=FAINT, font=self.f_plus, anchor="e",
            tags=("head", "plus"),
        )

    def _draw_rows(self, W: int, H: int) -> None:
        top, bottom = self._view_top, self._view_bottom
        cs = int(self.s)
        for row in self._rows:
            y = row["y"]
            # 简单裁剪：完全在可视区外的条目不画
            if y + row["h"] < top - 4 or y > bottom + 4:
                continue

            # 勾选框
            box = row["box"]
            bx, by = row["x0"], y + int(3 * self.s)
            if row["done"]:
                self.canvas.create_oval(
                    bx, by, bx + box, by + box, fill=ACCENT, outline=ACCENT, tags="row"
                )
                self.canvas.create_line(
                    bx + box * 0.28, by + box * 0.52,
                    bx + box * 0.44, by + box * 0.68,
                    bx + box * 0.72, by + box * 0.34,
                    fill="#FFFFFF", width=max(1, int(1.6 * cs)),
                    capstyle="round", joinstyle="round", tags="row",
                )
            else:
                self.canvas.create_oval(
                    bx, by, bx + box, by + box, fill="", outline=FAINT, width=1, tags="row"
                )

            # 文本（多行）
            ty = y + int(2 * self.s)
            for line in row["lines"]:
                self.canvas.create_text(
                    row["text_x"], ty, text=line, anchor="nw",
                    fill=DONE_TEXT if row["done"] else INK, font=row["font"], tags="row",
                )
                ty += row["lh"]

            # 已完成条目加一条删除线
            if row["done"] and row["lines"]:
                w = row["font"].measure(row["lines"][0])
                my = y + int(2 * self.s) + row["lh"] // 2 + 1
                self.canvas.create_line(
                    row["text_x"], my, row["text_x"] + w, my,
                    fill=DONE_TEXT, width=1, tags="row",
                )

    def _draw_footer(self, W: int, H: int) -> None:
        pad = self._pad()
        fy = H - pad - int(self.FOOT_H * self.s)
        n = self.store.count()
        status = f"已自动保存 · {n} 条" if n else "点 ＋ 添加"
        self.canvas.create_text(
            pad + 3, fy, text=status, fill=FAINT, font=self.f_foot,
            anchor="nw", tags="foot",
        )
        # 右下角缩放手柄
        gx = W - pad - 1
        gy = H - pad - 1
        for i in range(3):
            o = int(3 * self.s) + i * int(4 * self.s)
            self.canvas.create_line(
                gx - o, gy - 1, gx - 1, gy - o, fill=FAINT, width=1, tags="foot"
            )
        self._grip_box = (gx - int(14 * self.s), gy - int(14 * self.s), gx + 2, gy + 2)

    # ---------------------------------------------------------------- 命中测试

    def _hit_row(self, x: int, y: int) -> dict | None:
        if y < self._view_top or y > self._view_bottom:
            return None
        for row in self._rows:
            if row["y"] <= y <= row["y"] + row["h"]:
                return row
        return None

    def _hit_in(self, box, x: int, y: int) -> bool:  # noqa: ANN001
        if not box:
            return False
        b0, t0, b1, t1 = box
        return b0 <= x <= b1 and t0 <= y <= t1

    # ---------------------------------------------------------------- 事件

    def _bind_events(self) -> None:
        self.canvas.bind("<Configure>", self._on_configure)
        self.root.bind("<ButtonPress-1>", self._on_press, add="+")
        self.root.bind("<B1-Motion>", self._on_motion, add="+")
        self.root.bind("<ButtonRelease-1>", self._on_release, add="+")
        self.root.bind("<Button-3>", self._on_right_click, add="+")
        self.root.bind("<MouseWheel>", self._on_wheel, add="+")
        self.root.bind("<Escape>", lambda e: self._finish_edit(commit=False), add="+")

    def _on_configure(self, _event: tk.Event) -> None:
        self.render()

    def _on_press(self, event: tk.Event) -> None:
        if self._resizing:
            return
        self._press = (event.x_root, event.y_root, self._x, self._y)
        self._press_local = (event.x, event.y)
        self._moved = False
        self._on_grip = self._hit_in(
            getattr(self, "_grip_box", None), event.x, event.y
        )

    def _on_motion(self, event: tk.Event) -> None:
        if self._press is None:
            return
        px, py, wx, wy = self._press
        dx, dy = event.x_root - px, event.y_root - py

        if self._on_grip:
            if not self._resizing and abs(dx) + abs(dy) > 2:
                self._resizing = True
                self._rsz = (self._w, self._h)
            if self._resizing:
                ow, oh = self._rsz
                self._w = max(int(MIN_WIDTH * self.s), int(ow + dx))
                self._h = max(int(MIN_HEIGHT * self.s), int(oh + dy))
                self.root.geometry(f"{self._w}x{self._h}+{self._x}+{self._y}")
            return

        if self._resizing:
            return
        if not self._moved and abs(dx) < DRAG_THRESHOLD and abs(dy) < DRAG_THRESHOLD:
            return
        self._moved = True
        self._x, self._y = int(wx + dx), int(wy + dy)
        self.root.geometry(f"+{self._x}+{self._y}")

    def _on_release(self, event: tk.Event) -> None:
        if self._resizing:
            self._resizing = False
            self._press = None
            self._save_rect()
            self.render()
            return

        moved = self._moved
        self._press = None
        self._on_grip = False

        if moved:
            self._save_rect()
            self._schedule(120, self._clear_moved)
            return

        x, y = event.x, event.y
        if self._hit_in(getattr(self, "_plus_box", None), x, y):
            self._add_item()
            return

        row = self._hit_row(x, y)
        if row is None:
            return
        # 点在勾选框上 → 切换完成；点在文字上 → 编辑
        box = row["box"]
        if row["x0"] - 2 <= x <= row["x0"] + box + int(4 * self.s):
            self._toggle(row["id"])
        else:
            self._begin_edit(row)

    def _on_right_click(self, event: tk.Event) -> None:
        row = self._hit_row(event.x, event.y)
        if row is None:
            return
        if self._menu is not None:
            try:
                self._menu.destroy()
            except tk.TclError:
                pass
        self._menu = tk.Menu(self.root, tearoff=0)
        self._menu.add_command(label="编辑", command=lambda: self._begin_edit(row))
        self._menu.add_command(label="标记完成/未完成", command=lambda: self._toggle(row["id"]))
        self._menu.add_separator()
        self._menu.add_command(label="删除", command=lambda: self._delete_item(row["id"]))
        try:
            self._menu.tk_popup(event.x_root, event.y_root)
        except tk.TclError:
            pass

    def _on_wheel(self, event: tk.Event) -> None:
        if self._content_bottom and self._content_bottom > self._view_bottom:
            self._scroll = max(0.0, self._scroll - event.delta / 120.0 * self._row_h())
            self.render()

    def _schedule(self, ms: int, fn) -> None:  # noqa: ANN001
        try:
            self.root.after(ms, fn)
        except (tk.TclError, RuntimeError):
            pass

    def _clear_moved(self) -> None:
        self._moved = False

    # ---------------------------------------------------------------- 数据操作

    def _toggle(self, memo_id: int) -> None:
        self.store.set_done(memo_id, not self.store.is_done(memo_id))
        self.render()

    def _delete_item(self, memo_id: int) -> None:
        self.store.delete_memo(memo_id)
        self.render()

    def _add_item(self) -> None:
        mid = self.store.add_memo("")
        self.render()
        rows = [r for r in self._rows if r["id"] == mid]
        if rows:
            self._begin_edit(rows[0])
        else:
            self.render()

    # ---------------------------------------------------------------- 内嵌编辑

    def _begin_edit(self, row: dict) -> None:
        if self._editor:
            self._finish_edit()
        self._editor = None

        # 用 Canvas 上的 Entry 覆盖原文字位置
        w = max(int(60 * self.s), row["wrap"])
        h = max(int(18 * self.s), row["lh"] + int(4 * self.s))
        entry = tk.Entry(
            self.canvas, bd=0, relief="flat",
            bg=GLASS, fg=INK, insertbackground=INK,
            font=self.f_entry, highlightthickness=1,
            highlightbackground=BORDER, highlightcolor=ACCENT,
        )
        entry.insert(0, self.store.get_text(row["id"]))
        entry.select_range(0, "end")
        entry.icursor("end")
        wid = self.canvas.create_window(
            row["text_x"], row["y"] + int(2 * self.s),
            window=entry, anchor="nw", width=w, height=h,
        )
        self._editor = {"id": row["id"], "entry": entry, "wid": wid}

        entry.bind("<Return>", lambda e: self._finish_edit())
        entry.bind("<Escape>", lambda e: self._finish_edit(commit=False))
        entry.bind("<FocusOut>", lambda e: self._finish_edit())
        self._schedule(10, self._focus_entry)

    def _focus_entry(self) -> None:
        if not self._editor:
            return
        try:
            self._editor["entry"].focus_force()
        except tk.TclError:
            pass

    def _finish_edit(self, commit: bool = True) -> None:
        if not self._editor:
            return
        ed = self._editor
        self._editor = None
        try:
            text = ed["entry"].get().strip()
        except tk.TclError:
            text = ""
        try:
            ed["entry"].destroy()
        except tk.TclError:
            pass
        try:
            self.canvas.delete(ed["wid"])
        except tk.TclError:
            pass

        if commit:
            if text:
                self.store.update_text(ed["id"], text)
            else:
                self.store.delete_memo(ed["id"])
        self.render()

    # ---------------------------------------------------------------- 窗口控制

    def _save_rect(self) -> None:
        self.store.save_window_rect(self._x, self._y, self._w, self._h)

    def hide(self) -> None:
        self._save_rect()
        self._hiding = True
        self.root.withdraw()

    def show(self) -> None:
        self._hiding = False
        self.root.deiconify()
        self._apply_geometry()
        self.render()

    def toggle(self) -> None:
        if self._hiding:
            self.show()
        else:
            self.hide()

    def run(self) -> None:
        self.root.mainloop()

    def destroy(self) -> None:
        try:
            self.root.destroy()
        except tk.TclError:
            pass