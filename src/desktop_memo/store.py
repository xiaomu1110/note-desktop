"""存储层：一个 SQLite 文件同时存备忘录条目和窗口位置。

所有写操作立即 commit，进程被强杀也不丢数据。
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memos (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    text       TEXT    NOT NULL DEFAULT '',
    done       INTEGER NOT NULL DEFAULT 0,
    pos        INTEGER NOT NULL DEFAULT 0,
    updated_at REAL    NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS window_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

SEED_ITEMS = ["今天 10:00 交周报", "买牛奶、鸡蛋", "给妈妈打个电话"]


def _now() -> float:
    return time.time()


class MemoStore:
    """备忘录数据访问对象。"""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        # 本地 API 会在独立线程里读写，所以关掉同线程校验；
        # 并发安全由调用方（app.LiveController 的锁）保证。
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._seed_once()

    # ---------- 生命周期 ----------

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "MemoStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---------- 初始化 ----------

    def _seed_once(self) -> None:
        """首次运行塞几条示例，之后不再打扰。"""
        if self.get_meta("seeded") is not None:
            return
        if not self.list_memos():
            for text in SEED_ITEMS:
                self.add_memo(text)
        self.set_meta("seeded", "1")

    # ---------- 元数据 ----------

    def get_meta(self, key: str) -> str | None:
        row = self._conn.execute(
            "SELECT value FROM window_state WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO window_state(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self._conn.commit()

    # ---------- 窗口位置 ----------

    def get_window_rect(self) -> tuple[int, int, int, int] | None:
        """返回 (x, y, w, h)，没有记录时返回 None。"""
        raw = self.get_meta("rect")
        if not raw:
            return None
        try:
            parts = [int(float(p)) for p in raw.split(",")]
        except ValueError:
            return None
        if len(parts) != 4:
            return None
        x, y, w, h = parts
        if w <= 0 or h <= 0:
            return None
        return x, y, w, h

    def save_window_rect(self, x: int, y: int, w: int, h: int) -> None:
        self.set_meta("rect", f"{int(x)},{int(y)},{int(w)},{int(h)}")

    # ---------- 条目 ----------

    def list_memos(self) -> list[sqlite3.Row]:
        cur = self._conn.execute(
            "SELECT id, text, done, pos FROM memos ORDER BY pos, id"
        )
        return cur.fetchall()

    def add_memo(self, text: str = "") -> int:
        cur = self._conn.execute("SELECT COALESCE(MAX(pos), -1) + 1 FROM memos")
        pos = cur.fetchone()[0]
        cur = self._conn.execute(
            "INSERT INTO memos(text, done, pos, updated_at) VALUES(?, 0, ?, ?)",
            (text, pos, _now()),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def update_text(self, memo_id: int, text: str) -> None:
        self._conn.execute(
            "UPDATE memos SET text = ?, updated_at = ? WHERE id = ?",
            (text, _now(), memo_id),
        )
        self._conn.commit()

    def set_done(self, memo_id: int, done: bool) -> None:
        self._conn.execute(
            "UPDATE memos SET done = ?, updated_at = ? WHERE id = ?",
            (1 if done else 0, _now(), memo_id),
        )
        self._conn.commit()

    def delete_memo(self, memo_id: int) -> None:
        self._conn.execute("DELETE FROM memos WHERE id = ?", (memo_id,))
        self._conn.commit()

    def get_text(self, memo_id: int) -> str:
        row = self._conn.execute(
            "SELECT text FROM memos WHERE id = ?", (memo_id,)
        ).fetchone()
        return row["text"] if row else ""

    def is_done(self, memo_id: int) -> bool:
        row = self._conn.execute(
            "SELECT done FROM memos WHERE id = ?", (memo_id,)
        ).fetchone()
        return bool(row["done"]) if row else False

    def count(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) FROM memos").fetchone()[0])
