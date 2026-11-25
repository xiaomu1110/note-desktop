"""本地 API 测试：鉴权、路由、增删改、参数校验。

用真实 HTTP 请求打真实服务，不 mock http.server。
"""

import json
import os
import threading
import urllib.error
import urllib.request

import pytest

from desktop_memo.api import ApiServer, Controller


class FakeController(Controller):
    def __init__(self):
        self.items = {1: {"id": 1, "text": "第一条", "done": False, "pos": 0}}
        self.autostart = False
        self.visible = True
        self.shut = False

    def get_state(self):
        return {"running": True, "visible": self.visible,
                "autostart": self.autostart, "count": len(self.items)}

    def list_items(self):
        return list(self.items.values())

    def add_item(self, text):
        mid = max(self.items) + 1 if self.items else 1
        item = {"id": mid, "text": text, "done": False, "pos": mid - 1}
        self.items[mid] = item
        return item

    def update_item(self, memo_id, text, done):
        if memo_id not in self.items:
            raise LookupError("不存在")
        item = dict(self.items[memo_id])
        if text is not None:
            item["text"] = text
        if done is not None:
            item["done"] = done
        self.items[memo_id] = item
        return item

    def delete_item(self, memo_id):
        if memo_id not in self.items:
            raise LookupError("不存在")
        del self.items[memo_id]
        return {"deleted": memo_id, "remaining": len(self.items)}

    def set_autostart(self, enabled):
        self.autostart = enabled
        return enabled

    def set_visible(self, visible):
        self.visible = visible
        return visible

    def shutdown(self):
        self.shut = True
        return {"ok": True}


@pytest.fixture()
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("DESKTOP_MEMO_TOKEN", "test-token-123")
    ctrl = FakeController()
    srv = ApiServer(ctrl, port=0)
    from desktop_memo.api import _make_handler, _NoReuseHTTPServer

    handler_httpd = _NoReuseHTTPServer(("127.0.0.1", 0), _make_handler(srv))
    handler_httpd.daemon_threads = True
    port = handler_httpd.server_address[1]
    threading.Thread(target=handler_httpd.serve_forever, daemon=True).start()
    yield ctrl, port, "test-token-123"
    handler_httpd.shutdown()
    handler_httpd.server_close()


def call(port, method, path, body=None, token="test-token-123"):
    url = f"http://127.0.0.1:{port}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token is not None:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode())


def test_health_needs_no_token(api):
    _, port, _ = api
    status, body = call(port, "GET", "/health", token=None)
    assert status == 200
    assert body["ok"] is True
    assert body["app"] == "desktop-memo"


def test_missing_token_is_401(api):
    _, port, _ = api
    status, body = call(port, "GET", "/state", token=None)
    assert status == 401
    assert body["error"] == "unauthorized"


def test_wrong_token_is_401(api):
    _, port, _ = api
    status, _ = call(port, "GET", "/state", token="wrong")
    assert status == 401


def test_malformed_auth_header(api):
    _, port, _ = api
    req = urllib.request.Request(f"http://127.0.0.1:{port}/state")
    req.add_header("Authorization", "Basic abc")
    try:
        urllib.request.urlopen(req, timeout=5)
        raise AssertionError("应当被拒绝")
    except urllib.error.HTTPError as exc:
        assert exc.code == 401


def test_state(api):
    _, port, _ = api
    status, body = call(port, "GET", "/state")
    assert status == 200
    assert body["running"] is True
    assert body["count"] == 1


def test_list_items(api):
    _, port, _ = api
    status, body = call(port, "GET", "/items")
    assert status == 200
    assert len(body["items"]) == 1
    assert body["items"][0]["text"] == "第一条"


def test_add_item(api):
    _, port, _ = api
    status, body = call(port, "POST", "/items", {"text": "新任务"})
    assert status == 201
    assert body["text"] == "新任务"
    assert body["done"] is False


def test_add_item_rejects_empty(api):
    _, port, _ = api
    status, body = call(port, "POST", "/items", {"text": "   "})
    assert status == 400
    assert "text" in body["error"]


def test_patch_text_and_done(api):
    _, port, _ = api
    s1, b1 = call(port, "PATCH", "/items/1", {"text": "改过了"})
    assert s1 == 200 and b1["text"] == "改过了"
    s2, b2 = call(port, "PATCH", "/items/1", {"done": True})
    assert s2 == 200 and b2["done"] is True


def test_patch_requires_a_field(api):
    _, port, _ = api
    status, body = call(port, "PATCH", "/items/1", {})
    assert status == 400


def test_patch_rejects_non_bool_done(api):
    _, port, _ = api
    status, _ = call(port, "PATCH", "/items/1", {"done": "yes"})
    assert status == 400


def test_delete_item(api):
    _, port, _ = api
    status, body = call(port, "DELETE", "/items/1")
    assert status == 200
    assert body["deleted"] == 1
    status2, body2 = call(port, "GET", "/items")
    assert body2["items"] == []


def test_autostart(api):
    ctrl, port, _ = api
    status, body = call(port, "POST", "/autostart", {"enabled": True})
    assert status == 200 and body["enabled"] is True
    assert ctrl.autostart is True


def test_autostart_requires_bool(api):
    _, port, _ = api
    status, _ = call(port, "POST", "/autostart", {"enabled": 1})
    assert status == 400


def test_window_visibility(api):
    ctrl, port, _ = api
    status, body = call(port, "POST", "/window", {"visible": False})
    assert status == 200 and body["visible"] is False
    assert ctrl.visible is False


def test_shutdown(api):
    ctrl, port, _ = api
    status, body = call(port, "POST", "/shutdown")
    assert status == 200
    assert ctrl.shut is True


def test_unknown_route_is_404(api):
    _, port, _ = api
    status, body = call(port, "GET", "/nope")
    assert status == 404


def test_bad_json_is_400(api):
    _, port, _ = api
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/items", data=b"{not json", method="POST"
    )
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer test-token-123")
    try:
        urllib.request.urlopen(req, timeout=5)
        raise AssertionError("应当报错")
    except urllib.error.HTTPError as exc:
        assert exc.code == 400


def test_404_on_non_numeric_id(api):
    _, port, _ = api
    status, _ = call(port, "DELETE", "/items/abc")
    assert status == 404


def test_server_port_in_use_returns_none(api, monkeypatch):
    _, port, _ = api
    from desktop_memo.api import start_server

    monkeypatch.setenv("DESKTOP_MEMO_TOKEN", "t")
    srv = start_server(FakeController(), port=port)
    assert srv is None