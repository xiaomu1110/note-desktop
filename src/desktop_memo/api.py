"""本地 HTTP API：让外部程序（MCP / Hermes Agent）控制备忘录。

设计约束：
- 只绑 127.0.0.1，不对外网开放
- 每个请求都要带 `Authorization: Bearer <token>`，token 存在数据目录里
- 标准库 http.server，不引入任何第三方依赖
- 服务跑在独立线程；所有对 Tk / SQLite 的操作都通过回调转交主线程，
  避免跨线程直接碰 Tk 对象

端点：
    GET    /health                 保活与版本
    GET    /state                  运行状态（含是否在运行、自启、可见性）
    GET    /items                  列出条目
    POST   /items                  新增条目        {text}
    PATCH  /items/{id}             更新文本/完成   {text?, done?}
    DELETE /items/{id}             删除条目
    POST   /autostart              开机自启        {enabled}
    POST   /window                 显示/隐藏卡片   {visible}
    POST   /shutdown               退出程序
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import urlparse

from . import __version__
from .config import API_HOST, API_PORT, get_api_token

MAX_BODY = 64 * 1024  # 请求体上限，防止被塞爆


class ApiError(Exception):
    """带 HTTP 状态码的业务错误。"""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


class ApiServer:
    """把 HTTP 请求翻译成对 Controller 的调用。"""

    def __init__(
        self,
        controller: "Controller",
        host: str = API_HOST,
        port: int = API_PORT,
    ) -> None:
        self.controller = controller
        self.host = host
        self.port = port
        self.token = get_api_token()
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    # ---------------------------------------------------------------- 生命周期

    def start(self) -> bool:
        """在后台线程启动服务，返回是否成功。端口被占时返回 False。"""
        handler = _make_handler(self)
        try:
            self._httpd = _NoReuseHTTPServer((self.host, self.port), handler)
        except OSError:
            return False
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, name="memo-api", daemon=True
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        if self._httpd is not None:
            try:
                self._httpd.shutdown()
                self._httpd.server_close()
            except Exception:
                pass
            self._httpd = None

    # ---------------------------------------------------------------- 鉴权

    def authorized(self, header: str | None) -> bool:
        if not header:
            return False
        parts = header.split(None, 1)
        if len(parts) != 2 or parts[0].lower() != "bearer":
            return False
        # 定长比较，避免时序侧信道
        a, b = parts[1].encode(), self.token.encode()
        if len(a) != len(b):
            return False
        diff = 0
        for x, y in zip(a, b):
            diff |= x ^ y
        return diff == 0

    # ---------------------------------------------------------------- 路由

    def dispatch(self, method: str, path: str, body: dict | None) -> tuple[int, Any]:
        """返回 (状态码, 响应体)。业务异常由 handler 统一转换。"""
        c = self.controller

        if path == "/health":
            return 200, {
                "ok": True,
                "app": "desktop-memo",
                "version": __version__,
            }

        if path == "/state":
            return 200, c.get_state()

        if path == "/items" and method == "GET":
            return 200, {"items": c.list_items()}

        if path == "/items" and method == "POST":
            text = str((body or {}).get("text", "")).strip()
            if not text:
                raise ApiError("text 不能为空")
            return 201, c.add_item(text)

        if path == "/autostart" and method == "POST":
            raw = (body or {}).get("enabled")
            if not isinstance(raw, bool):
                raise ApiError("enabled 必须是布尔值")
            return 200, {"enabled": c.set_autostart(raw)}

        if path == "/window" and method == "POST":
            raw = (body or {}).get("visible")
            if not isinstance(raw, bool):
                raise ApiError("visible 必须是布尔值")
            return 200, {"visible": c.set_visible(raw)}

        if path == "/shutdown" and method == "POST":
            return 200, c.shutdown()

        # /items/{id}
        if path.startswith("/items/"):
            raw_id = path[len("/items/"):]
            if not raw_id.isdigit():
                raise ApiError("条目 id 必须是整数", 404)
            memo_id = int(raw_id)
            if method == "PATCH":
                data = body or {}
                if "text" not in data and "done" not in data:
                    raise ApiError("至少要提供 text 或 done")
                # 类型校验放在 API 层，别依赖具体 Controller 实现
                if "text" in data and not isinstance(data["text"], str):
                    raise ApiError("text 必须是字符串")
                if "done" in data and not isinstance(data["done"], bool):
                    raise ApiError("done 必须是布尔值")
                return 200, c.update_item(
                    memo_id,
                    text=data.get("text"),
                    done=data.get("done"),
                )
            if method == "DELETE":
                return 200, c.delete_item(memo_id)

        raise ApiError(f"未知接口：{method} {path}", 404)


class _NoReuseHTTPServer(ThreadingHTTPServer):
    """端口被占时必须失败，而不是因为 SO_REUSEADDR 悄悄共享。

    默认 allow_reuse_address=True 在 Windows 上会让两个进程绑同一端口，
    导致"第二个实例以为启动成功、实际收不到请求"。这里显式关掉。
    """

    allow_reuse_address = False


class Controller:
    """控制器接口。真实实现由 app 层注入（它在主线程里操作 Tk 与 store）。

    所有方法都在 API 线程里被调用，必须自己保证线程安全。
    """

    def get_state(self) -> dict:  # pragma: no cover - 接口声明
        raise NotImplementedError

    def list_items(self) -> list[dict]:  # pragma: no cover
        raise NotImplementedError

    def add_item(self, text: str) -> dict:  # pragma: no cover
        raise NotImplementedError

    def update_item(self, memo_id: int, text: Any, done: Any) -> dict:  # pragma: no cover
        raise NotImplementedError

    def delete_item(self, memo_id: int) -> dict:  # pragma: no cover
        raise NotImplementedError

    def set_autostart(self, enabled: bool) -> bool:  # pragma: no cover
        raise NotImplementedError

    def set_visible(self, visible: bool) -> bool:  # pragma: no cover
        raise NotImplementedError

    def shutdown(self) -> dict:  # pragma: no cover
        raise NotImplementedError


def _make_handler(server: ApiServer) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "DesktopMemoAPI"

        # -------------------------------------------------- 工具
        def _send(self, status: int, payload: Any) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _read_body(self) -> dict | None:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                return None
            if length > MAX_BODY:
                raise ApiError("请求体过大", 413)
            raw = self.rfile.read(length)
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise ApiError("请求体不是合法 JSON")
            if not isinstance(parsed, dict):
                raise ApiError("请求体必须是 JSON 对象")
            return parsed

        def _handle(self, method: str) -> None:
            path = urlparse(self.path).path.rstrip("/") or "/"
            if path == "/health":
                # 保活接口不鉴权，方便外部探测服务是否起来
                self._send(200, {"ok": True, "app": "desktop-memo",
                                 "version": __version__})
                return
            if not server.authorized(self.headers.get("Authorization")):
                self._send(401, {"error": "unauthorized",
                                 "hint": "需要 Authorization: Bearer <token>"})
                return
            try:
                body = self._read_body() if method in ("POST", "PATCH") else None
                status, payload = server.dispatch(method, path, body)
            except ApiError as exc:
                self._send(exc.status, {"error": exc.message})
                return
            except Exception as exc:  # 兜底，别让服务线程崩掉
                self._send(500, {"error": f"内部错误：{exc}"})
                return
            self._send(status, payload)

        def do_GET(self) -> None:  # noqa: N802
            self._handle("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._handle("POST")

        def do_PATCH(self) -> None:  # noqa: N802
            self._handle("PATCH")

        def do_DELETE(self) -> None:  # noqa: N802
            self._handle("DELETE")

        def log_message(self, *_args) -> None:
            """静默访问日志：这个服务是给本机代理用的，不需要刷屏。"""

    return Handler


def start_server(controller: Controller, port: int = API_PORT) -> ApiServer | None:
    """便捷入口：启动失败不抛异常，返回 None。"""
    server = ApiServer(controller, port=port)
    if not server.start():
        return None
    return server