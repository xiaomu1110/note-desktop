"""MCP 服务端（stdio）：把备忘录的本地 API 包装成 MCP 工具。

为什么手写 JSON-RPC 而不是用 mcp SDK：
本项目的核心卖点是「零第三方依赖」。MCP 的 stdio 传输本质上就是
一行一个 JSON-RPC 2.0 报文，只需要实现 initialize / tools/list /
tools/call / ping 四个方法，用标准库就够了，不值得为此拉一个 SDK。

启动方式（由 Hermes 拉起）：
    python -m desktop_memo.mcp_server

它本身不碰 Tk，只通过 HTTP 调上面那个本地 API，
所以备忘录没在运行时也能给出清晰的错误提示。
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from typing import Any

from . import __version__
from .config import API_HOST, API_PORT, get_api_token

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "desktop-memo"
SERVER_VERSION = __version__
BASE_URL = f"http://{API_HOST}:{API_PORT}"


# ---------------------------------------------------------------- HTTP 客户端


class ApiClientError(Exception):
    """调用本地 API 失败的统一异常。"""


def _request(method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE_URL + path, data=data, method=method)
    req.add_header("Authorization", f"Bearer {get_api_token()}")
    if data is not None:
        req.add_header("Content-Type", "application/json; charset=utf-8")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            detail = json.loads(raw).get("error", raw)
        except json.JSONDecodeError:
            detail = raw
        raise ApiClientError(f"备忘录接口返回 {exc.code}：{detail}") from exc
    except urllib.error.URLError as exc:
        raise ApiClientError(
            "连接不上桌面备忘录。它可能没有运行——"
            "请先启动（例如双击 run.pyw 或执行 desktop-memo），再重试。"
            f"（{exc.reason}）"
        ) from exc
    except TimeoutError as exc:
        raise ApiClientError("调用桌面备忘录超时。") from exc


# ---------------------------------------------------------------- 工具定义

TOOLS: list[dict] = [
    {
        "name": "memo_status",
        "description": (
            "查询桌面备忘录的运行状态：是否正在运行、卡片是否可见、"
            "是否已开机自启、当前有多少条事项。任何操作前都可以先调这个确认。"
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "memo_start",
        "description": (
            "启动桌面备忘录。如果已经在运行会直接返回当前状态（幂等）。"
            "用于用户说「打开备忘录/把备忘录叫出来」时。"
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "memo_shutdown",
        "description": (
            "关闭（退出）桌面备忘录进程。注意：这只关闭程序，"
            "不会删除任何事项数据；下次启动时卡片和内容会原样回来。"
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "memo_set_autostart",
        "description": (
            "设置或取消「开机自动启动」。开启后每次登录 Windows 都会自动拉起备忘录。"
            "注意这需要备忘录正在运行才能生效。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "enabled": {"type": "boolean", "description": "true 开启自启，false 关闭"}
            },
            "required": ["enabled"],
            "additionalProperties": False,
        },
    },
    {
        "name": "memo_set_visible",
        "description": "显示或隐藏桌面上的卡片（不退出程序，托盘图标仍保留）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "visible": {"type": "boolean", "description": "true 显示，false 隐藏"}
            },
            "required": ["visible"],
            "additionalProperties": False,
        },
    },
    {
        "name": "memo_list_items",
        "description": "列出备忘录里的所有事项，含 id、文本、是否已完成。",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "memo_add_item",
        "description": "往备忘录里新增一条事项。新增后卡片会自动刷新。",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "事项内容，不能为空"}},
            "required": ["text"],
            "additionalProperties": False,
        },
    },
    {
        "name": "memo_update_item",
        "description": "修改某条事项的文本，和/或标记完成/未完成。按 id 定位。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "integer", "description": "事项 id，可从 memo_list_items 获取"},
                "text": {"type": "string", "description": "新的文本，不传则不改"},
                "done": {"type": "boolean", "description": "是否已完成，不传则不改"},
            },
            "required": ["id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "memo_delete_item",
        "description": "删除某条事项。删除不可撤销，调用前建议先 memo_list_items 确认 id。",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "integer", "description": "要删除的事项 id"}},
            "required": ["id"],
            "additionalProperties": False,
        },
    },
]


def _ok(payload: Any) -> dict:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(payload, ensure_ascii=False, indent=2),
            }
        ],
        "isError": False,
    }


def _err(message: str) -> dict:
    return {"content": [{"type": "text", "text": message}], "isError": True}


# ---------------------------------------------------------------- 工具实现


def _ensure_running() -> dict:
    """确认备忘录在跑，返回 /state。不在跑则抛错。"""
    return _request("GET", "/state")


def call_tool(name: str, args: dict) -> dict:
    try:
        if name == "memo_status":
            return _ok(_ensure_running())

        if name == "memo_start":
            state = _try_state()
            if state is not None:
                return _ok({"started": False, "already_running": True, "state": state})
            started = _launch_process()
            if not started:
                return _err("启动桌面备忘录失败，请手动检查。")
            state = _wait_for_state(timeout=20.0)
            if state is None:
                return _err("已请求启动，但 20 秒内没有连上接口，请稍后重试 memo_status。")
            return _ok({"started": True, "state": state})

        if name == "memo_shutdown":
            _ensure_running()
            return _ok(_request("POST", "/shutdown"))

        if name == "memo_set_autostart":
            enabled = args.get("enabled")
            if not isinstance(enabled, bool):
                return _err("enabled 必须是布尔值")
            return _ok(_request("POST", "/autostart", {"enabled": enabled}))

        if name == "memo_set_visible":
            visible = args.get("visible")
            if not isinstance(visible, bool):
                return _err("visible 必须是布尔值")
            return _ok(_request("POST", "/window", {"visible": visible}))

        if name == "memo_list_items":
            return _ok(_request("GET", "/items"))

        if name == "memo_add_item":
            text = args.get("text")
            if not isinstance(text, str) or not text.strip():
                return _err("text 不能为空")
            return _ok(_request("POST", "/items", {"text": text.strip()}))

        if name == "memo_update_item":
            memo_id = args.get("id")
            if not isinstance(memo_id, int):
                return _err("id 必须是整数")
            payload: dict = {}
            if "text" in args:
                if not isinstance(args["text"], str) or not args["text"].strip():
                    return _err("text 不能为空")
                payload["text"] = args["text"].strip()
            if "done" in args:
                if not isinstance(args["done"], bool):
                    return _err("done 必须是布尔值")
                payload["done"] = args["done"]
            if not payload:
                return _err("至少要提供 text 或 done 之一")
            return _ok(_request("PATCH", f"/items/{memo_id}", payload))

        if name == "memo_delete_item":
            memo_id = args.get("id")
            if not isinstance(memo_id, int):
                return _err("id 必须是整数")
            return _ok(_request("DELETE", f"/items/{memo_id}"))

        return _err(f"未知工具：{name}")
    except ApiClientError as exc:
        return _err(str(exc))
    except Exception as exc:  # 兜底
        return _err(f"调用失败：{exc}")


def _try_state() -> dict | None:
    try:
        return _request("GET", "/state")
    except ApiClientError:
        return None


def _wait_for_state(timeout: float) -> dict | None:
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        state = _try_state()
        if state is not None:
            return state
        time.sleep(0.4)
    return None


def _launch_process() -> bool:
    """拉起备忘录主程序（独立进程，不占住 MCP 的 stdio）。"""
    import subprocess
    from pathlib import Path

    # 优先用项目根目录的 run.pyw（双击可用的那个），退回到 -m 方式
    pkg = Path(__file__).resolve()
    project_root = pkg.parents[2]
    launcher = project_root / "run.pyw"
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    if not pythonw.exists():
        pythonw = exe

    if launcher.exists():
        cmd = [str(pythonw), str(launcher)]
        cwd = str(project_root)
    else:
        cmd = [str(pythonw), "-m", "desktop_memo"]
        cwd = str(project_root)

    kwargs: dict = {}
    if sys.platform == "win32":
        # DETACHED_PROCESS：不要控制台窗口，也不要继承 MCP 的管道
        kwargs["creationflags"] = 0x00000008 | 0x00000200
    else:  # pragma: no cover
        kwargs["start_new_session"] = True

    try:
        subprocess.Popen(
            cmd,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            **kwargs,
        )
        return True
    except OSError:
        return False


# ---------------------------------------------------------------- JSON-RPC


def handle_request(msg: dict) -> dict | None:
    """处理一条 JSON-RPC 报文，返回响应（通知则返回 None）。"""
    method = msg.get("method")
    msg_id = msg.get("id")
    is_notification = "id" not in msg

    if method == "initialize":
        result = {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        }
        if is_notification:
            return None
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    if method == "notifications/initialized":
        return None  # 通知，不回复

    if method == "ping":
        if is_notification:
            return None
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}

    if method == "tools/list":
        if is_notification:
            return None
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}}

    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name", "")
        args = params.get("arguments") or {}
        result = call_tool(name, args)
        if is_notification:
            return None
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    if is_notification:
        return None
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def main() -> int:
    """stdio 主循环：一行一条报文，读到 EOF 退出。"""
    stdin = sys.stdin
    stdout = sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            stdout.write(json.dumps({
                "jsonrpc": "2.0", "id": None,
                "error": {"code": -32700, "message": "Parse error"},
            }) + "\n")
        else:
            try:
                resp = handle_request(msg)
            except Exception as exc:  # 别让一条坏请求杀掉整个服务
                resp = {
                    "jsonrpc": "2.0", "id": msg.get("id"),
                    "error": {"code": -32603, "message": f"Internal error: {exc}"},
                }
            if resp is not None:
                stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
        stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())