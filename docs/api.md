# 本地 API 与 MCP

桌面备忘录内置一个**只绑回环地址**的 HTTP API，外面套了一个 MCP 服务端，
让 Hermes Agent（或任何 MCP 客户端）能远程控制它。

```
Hermes Agent  ──MCP(stdio)──▶  desktop_memo.mcp_server  ──HTTP──▶  备忘录主程序
                                                                   （Tk + SQLite）
```

## 安全模型

| 项 | 做法 |
|---|---|
| 监听地址 | `127.0.0.1`，外网无法访问 |
| 端口 | `52741` 固定 |
| 鉴权 | `Authorization: Bearer <token>` |
| token 位置 | `%APPDATA%\desktop-memo\api_token`（可用 `DESKTOP_MEMO_TOKEN` 覆盖） |
| 免鉴权端点 | 只有 `/health`（用于探测服务是否起来） |
| 请求体上限 | 64 KB |

token 在首次启动时自动生成，比较时用定长比较避免时序侧信道。

## HTTP 端点

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 保活，**不需要 token** |
| GET | `/state` | 运行状态：`running` `visible` `autostart` `count` |
| GET | `/items` | 列出所有事项 |
| POST | `/items` | 新增，body `{"text": "..."}` |
| PATCH | `/items/{id}` | 改文本和/或完成状态，body `{"text"?, "done"?}` |
| DELETE | `/items/{id}` | 删除 |
| POST | `/autostart` | 开机自启，body `{"enabled": true}` |
| POST | `/window` | 显示/隐藏卡片，body `{"visible": false}` |
| POST | `/shutdown` | 退出程序（数据保留） |

示例：

```bash
TOKEN=$(cat "$APPDATA/desktop-memo/api_token")
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:52741/state
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
     -d '{"text":"买牛奶"}' http://127.0.0.1:52741/items
```

错误格式统一为 `{"error": "..."}`，状态码 400 / 401 / 404 / 413 / 500。

## MCP 工具

| 工具 | 作用 |
|---|---|
| `memo_status` | 查运行状态 |
| `memo_start` | 启动（幂等，已在运行时直接返回状态） |
| `memo_shutdown` | 退出程序 |
| `memo_set_autostart` | 开机自启开关 |
| `memo_set_visible` | 显示 / 隐藏卡片 |
| `memo_list_items` | 列出事项 |
| `memo_add_item` | 新增事项 |
| `memo_update_item` | 改文本 / 打勾 |
| `memo_delete_item` | 删除事项 |

MCP 服务端用**标准库手写 JSON-RPC**（只实现 `initialize` / `tools/list` /
`tools/call` / `ping`），不为这一点功能引入 `mcp` SDK 依赖。

## 注册到 Hermes

```bash
hermes mcp add desktop-memo --command "D:/Miniconda3/python.exe" \
  --args -m desktop_memo.mcp_server
```

> ⚠️ **坑**：`hermes mcp add` 会把 `--env` 和 `--connect-timeout` 也塞进 `args`，
> 导致子进程收到错误的命令行而连接失败。正确做法是直接编辑
> `~/.hermes/config.yaml` 里的 `mcp_servers.desktop-memo`：

```yaml
mcp_servers:
  desktop-memo:
    command: D:/Miniconda3/python.exe
    args:
      - -m
      - desktop_memo.mcp_server
    env:
      PYTHONPATH: D:\Users\lyh20\Desktop\桌面备忘录\src
    connect_timeout: 30
    enabled: true
```

然后 `hermes mcp test desktop-memo` 验证。

## 线程模型

Tk 不是线程安全的，所以：

- HTTP 服务跑在独立线程
- 需要动 Tk 或 SQLite 时，把任务排进队列，由**主线程**的 `after()` 轮询取出执行
- SQLite 连接打开 `check_same_thread=False`，并发由 `LiveController` 的锁保护

## 关闭 API

如果端口被占导致 API 起不来，程序仍然正常显示卡片，只是会往 stderr 打一行提示。
换成 `-m desktop_memo.mcp_server` 的客户端此时会报「连接不上」。