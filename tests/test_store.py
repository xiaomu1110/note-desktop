"""存储层测试：条目增删改查 + 窗口位置记忆。

全部跑在内存库上，不碰真实数据。
"""

import pytest

from desktop_memo.store import MemoStore


@pytest.fixture()
def store():
    s = MemoStore(":memory:")
    yield s
    s.close()


def test_schema_and_seed(store):
    # 首次打开会塞示例条目，而且只塞一次
    assert store.count() == 3
    assert store.list_memos()[0]["text"] == "今天 10:00 交周报"


def test_seed_is_idempotent(tmp_path):
    path = tmp_path / "m.db"
    with MemoStore(path) as s1:
        n1 = s1.count()
    with MemoStore(path) as s2:
        assert s2.count() == n1


def test_add_and_list_order(store):
    store.delete_memo(int(store.list_memos()[0]["id"]))
    new_id = store.add_memo(" новые дела ")
    rows = store.list_memos()
    assert rows[-1]["id"] == new_id
    assert rows[-1]["text"] == " новые дела "
    assert rows[-1]["pos"] >= max(r["pos"] for r in rows[:-1])


def test_update_text(store):
    mid = store.list_memos()[0]["id"]
    store.update_text(int(mid), "改过了")
    assert store.get_text(int(mid)) == "改过了"


def test_toggle_done(store):
    mid = int(store.list_memos()[0]["id"])
    assert store.is_done(mid) is False
    store.set_done(mid, True)
    assert store.is_done(mid) is True
    store.set_done(mid, False)
    assert store.is_done(mid) is False


def test_delete(store):
    mid = int(store.list_memos()[0]["id"])
    before = store.count()
    store.delete_memo(mid)
    assert store.count() == before - 1
    assert store.get_text(mid) == ""
    assert store.is_done(mid) is False


def test_window_rect_roundtrip(store):
    assert store.get_window_rect() is None
    store.save_window_rect(100, 200, 320, 220)
    assert store.get_window_rect() == (100, 200, 320, 220)


def test_window_rect_rejects_garbage(store):
    store.set_meta("rect", "not,a,rect")
    assert store.get_window_rect() is None
    store.set_meta("rect", "1,2,3")
    assert store.get_window_rect() is None
    store.set_meta("rect", "1,2,0,5")  # 宽为零属于坏数据
    assert store.get_window_rect() is None


def test_rect_persists_across_reopen(tmp_path):
    path = tmp_path / "m.db"
    with MemoStore(path) as s1:
        s1.save_window_rect(11, 22, 333, 444)
    with MemoStore(path) as s2:
        assert s2.get_window_rect() == (11, 22, 333, 444)


def test_data_survives_unclean_shutdown(tmp_path):
    # 每写必 commit：不 close 直接再打开，数据也必须在
    s1 = MemoStore(tmp_path / "m.db")
    s1.add_memo("不能丢")
    del s1
    with MemoStore(tmp_path / "m.db") as s2:
        assert "不能丢" in [r["text"] for r in s2.list_memos()]