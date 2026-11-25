"""MCP 服务端测试：协议握手、工具列表、参数校验、启动进程逻辑。

不依赖真实运行的备忘录——API 调用用打桩替换，协议部分跑真实报文。
"""

import json
import subprocess
import sys

import pytest

from desktop_memo import mcp_server


def rpc(method, params=None, msg_id=1, notify=False):
    msg = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        msg["params"] = params
    if not notify:
        msg["id"] = msg_id
    return mcp_server.handle_request(msg)


# ---------------------------------------------------------------- 协议


def test_initialize_returns_protocol_and_server_info():
    resp = rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}})
    assert resp["result"]["protocolVersion"] == "2024-11-05"
    assert resp["result"]["serverInfo"]["name"] == "desktop-memo"
    assert "tools" in resp["result"]["capabilities"]


def test_initialized_notification_gets_no_response():
    resp = rpc("notifications/initialized", notify=True)
    assert resp is None


def test_ping():
    assert rpc("ping")["result"] == {}


def test_tools_list_contains_all_tools():
    tools = rpc("tools/list")["result"]["tools"]
    names = {t["name"] for t in tools}
    assert names == {
        "memo_status", "memo_start", "memo_shutdown", "memo_set_autostart",
        "memo_set_visible", "memo_list_items", "memo_add_item",
        "memo_update_item", "memo_delete_item",
    }


def test_every_tool_has_schema():
    for tool in rpc("tools/list")["result"]["tools"]:
        assert tool["description"]
        assert tool["inputSchema"]["type"] == "object"
        # 需要参数的必须声明 required
        if tool["inputSchema"].get("properties"):
            assert "required" in tool["inputSchema"]


def test_unknown_method_returns_error():
    resp = rpc("no/such/method")
    assert resp["error"]["code"] == -32601


def test_unknown_tool_is_error_result():
    resp = rpc("tools/call", {"name": "nope", "arguments": {}})
    assert resp["result"]["isError"] is True


# ---------------------------------------------------------------- 参数校验

@pytest.fixture()
def stubbed(monkeypatch):
    """把 HTTP 层换成内存里的假接口，方便测工具逻辑。"""
    calls = []
    state = {"running": True, "items": [{"id": 1, "text": "已有", "done": False}]}

    def fake_request(method, path, body=None):
        calls.append((method, path, body))
        if path == "/state":
            return state
        if path == "/items" and method == "GET":
            return {"items": state["items"]}
        if path == "/items" and method == "POST":
            item = {"id": 2, "text": body["text"], "done": False}
            state["items"].append(item)
            return item
        if path.startswith("/items/") and method == "PATCH":
            return {"id": 1, **body}
        if path.startswith("/items/") and method == "DELETE":
            return {"deleted": 1}
        if path == "/autostart":
            return {"enabled": body["enabled"]}
        if path == "/window":
            return {"visible": body["visible"]}
        if path == "/shutdown":
            return {"ok": True}
        raise AssertionError(f"未预期的调用 {method} {path}")

    monkeypatch.setattr(mcp_server, "_request", fake_request)
    return calls, state


def test_status(stubbed):
    resp = rpc("tools/call", {"name": "memo_status", "arguments": {}})
    assert resp["result"]["isError"] is False
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert payload["running"] is True


def test_list_items(stubbed):
    resp = rpc("tools/call", {"name": "memo_list_items", "arguments": {}})
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert payload["items"][0]["text"] == "已有"


def test_add_item_normalizes_text(stubbed):
    calls, _ = stubbed
    resp = rpc("tools/call", {"name": "memo_add_item", "arguments": {"text": "  空格  "}})
    assert resp["result"]["isError"] is False
    assert calls[-1] == ("POST", "/items", {"text": "空格"})


def test_add_item_rejects_blank(stubbed):
    resp = rpc("tools/call", {"name": "memo_add_item", "arguments": {"text": "   "}})
    assert resp["result"]["isError"] is True


def test_update_item_requires_a_field(stubbed):
    resp = rpc("tools/call", {"name": "memo_update_item", "arguments": {"id": 1}})
    assert resp["result"]["isError"] is True


def test_update_item_rejects_bad_done(stubbed):
    resp = rpc("tools/call",
               {"name": "memo_update_item", "arguments": {"id": 1, "done": "yes"}})
    assert resp["result"]["isError"] is True


def test_update_item_text_and_done(stubbed):
    calls, _ = stubbed
    resp = rpc("tools/call",
               {"name": "memo_update_item",
                "arguments": {"id": 1, "text": "新", "done": True}})
    assert resp["result"]["isError"] is False
    assert calls[-1][:2] == ("PATCH", "/items/1")
    assert calls[-1][2] == {"text": "新", "done": True}


def test_delete_item(stubbed):
    resp = rpc("tools/call", {"name": "memo_delete_item", "arguments": {"id": 1}})
    assert resp["result"]["isError"] is False


def test_autostart_requires_bool(stubbed):
    resp = rpc("tools/call", {"name": "memo_set_autostart", "arguments": {"enabled": 1}})
    assert resp["result"]["isError"] is True


def test_autostart_ok(stubbed):
    resp = rpc("tools/call", {"name": "memo_set_autostart", "arguments": {"enabled": True}})
    assert json.loads(resp["result"]["content"][0]["text"])["enabled"] is True


def test_set_visible(stubbed):
    resp = rpc("tools/call", {"name": "memo_set_visible", "arguments": {"visible": False}})
    assert json.loads(resp["result"]["content"][0]["text"])["visible"] is False


def test_connection_error_is_friendly(monkeypatch):
    def boom(*_a, **_k):
        raise mcp_server.ApiClientError("连接不上桌面备忘录。它可能没有运行")

    monkeypatch.setattr(mcp_server, "_request", boom)
    resp = rpc("tools/call", {"name": "memo_status", "arguments": {}})
    assert resp["result"]["isError"] is True
    assert "没有运行" in resp["result"]["content"][0]["text"]


def test_start_is_idempotent(monkeypatch, stubbed):
    # 已经在跑：不应该重复拉起进程
    launched = []
    monkeypatch.setattr(mcp_server, "_launch_process", lambda: launched.append(1) or True)
    resp = rpc("tools/call", {"name": "memo_start", "arguments": {}})
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert payload["already_running"] is True
    assert launched == []


def test_start_launches_when_down(monkeypatch):
    calls = {"n": 0}

    def state_then_none(method, path, body=None):
        if path == "/state":
            calls["n"] += 1
            if calls["n"] <= 1:
                raise mcp_server.ApiClientError("没在跑")
            return {"running": True}
        raise AssertionError(path)

    monkeypatch.setattr(mcp_server, "_request", state_then_none)
    monkeypatch.setattr(mcp_server, "_launch_process", lambda: True)
    resp = rpc("tools/call", {"name": "memo_start", "arguments": {}})
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert payload["started"] is True


# ---------------------------------------------------------------- 真实 stdio

def test_stdio_roundtrip():
    """真实子进程 + 管道：模拟 MCP 客户端的一次完整会话。"""
    import os
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    env = {**os.environ}
    env["PYTHONPATH"] = str(root / "src") + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.Popen(
        [sys.executable, "-m", "desktop_memo.mcp_server"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, cwd=str(root), env=env,
    )
    try:
        def send(obj):
            proc.stdin.write(json.dumps(obj) + "\n")
            proc.stdin.flush()
            return json.loads(proc.stdout.readline())

        init = send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                     "params": {"protocolVersion": "2024-11-05", "capabilities": {}}})
        assert init["result"]["serverInfo"]["name"] == "desktop-memo"

        proc.stdin.write(json.dumps({"jsonrpc": "2.0",
                                     "method": "notifications/initialized"}) + "\n")
        proc.stdin.flush()

        tools = send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        assert len(tools["result"]["tools"]) == 9
    finally:
        proc.stdin.close()
        proc.terminate()
        proc.wait(timeout=10)