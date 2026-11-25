# 安装指南

三种安装方式，按需要选一种。

---

## 一、下载 EXE（最简单，不需要 Python）

1. 打开 [Releases](https://github.com/xiaomu1110/note-desktop/releases)
2. 下载 `note-desktop.exe`（约 13 MB，单文件）
3. 放到一个固定的目录，双击运行

> 首次运行 Windows SmartScreen 可能提示"未知发布者"，因为 exe 未做代码签名。
> 点「更多信息」→「仍要运行」即可。

运行后：

- 桌面右侧出现一张便签卡片，按住任意位置可拖动
- 右下角系统托盘出现一个橙色小图标，右键可开机自启 / 显示隐藏 / 退出
- 数据存在 `%APPDATA%\desktop-memo\memo.db`

---

## 二、从源码运行（需要 Python 3.10+）

```bash
git clone https://github.com/xiaomu1110/note-desktop.git
cd note-desktop
python run.pyw
```

或者装成命令：

```bash
pip install -e .
note-desktop
```

依赖：**零**。Tkinter / sqlite3 / ctypes 都是 Python 标准库。

---

## 三、从 Release 下载 wheel

```bash
pip install note_desktop-1.0.0-py3-none-any.whl
note-desktop
```

---

## 配套：MCP 服务器

让 Hermes Agent / Claude Desktop 等能控制备忘录。

### 前置

备忘录必须**正在运行**（MCP 只是遥控器，本体得开着）。

### 一键安装

仓库里的脚本会自动装好技能和 MCP 配置：

```bash
cd note-desktop
python install_integration.py
```

它会做两件事：

1. 把 `skill/SKILL.md` 复制到 Hermes 的技能目录
2. 把 `note-desktop` 写进 `config.yaml` 的 `mcp_servers`

先看会改什么：

```bash
python install_integration.py --dry-run
```

### 手动安装

**Hermes Agent**：编辑 `%LOCALAPPDATA%\hermes\config.yaml`，加入：

```yaml
mcp_servers:
  note-desktop:
    command: python
    args:
      - -m
      - desktop_memo.mcp_server
    env:
      PYTHONPATH: D:\path\to\note-desktop\src
    connect_timeout: 30
    enabled: true
```

> ⚠️ **不要用 `hermes mcp add --env`**。实测该命令会把 `--env` 和
> `--connect-timeout` 一起塞进 `args`，导致子进程收到错误命令行而连接失败。
> 直接改配置文件才可靠。

**Claude Desktop**：编辑 `%APPDATA%\Claude\claude_desktop_config.json`：

```json
{
  "mcpServers": {
    "note-desktop": {
      "command": "python",
      "args": ["-m", "desktop_memo.mcp_server"],
      "env": { "PYTHONPATH": "D:\\path\\to\\note-desktop\\src" }
    }
  }
}
```

完整的模板见 [mcp/mcp-config.example.json](../mcp/mcp-config.example.json)。

### 验证

```bash
hermes mcp test note-desktop
```

看到 `✓ Connected` 和 `✓ Tools discovered: 9` 就成功了。

### 9 个工具

| 工具 | 作用 |
|---|---|
| `memo_status` | 查运行状态 |
| `memo_start` | 启动（幂等） |
| `memo_shutdown` | 退出程序 |
| `memo_set_autostart` | 开机自启开关 |
| `memo_set_visible` | 显示 / 隐藏卡片 |
| `memo_list_items` | 列出事项 |
| `memo_add_item` | 新增事项 |
| `memo_update_item` | 改文本 / 打勾 |
| `memo_delete_item` | 删除事项 |

---

## 配套：Agent 技能

`skill/SKILL.md` 是一份 Agent 技能说明，教 Agent 什么时候该用这些工具、
怎么找 id、删除前要确认等。安装方式见上面的「一键安装」。

手动安放位置（Hermes）：

```
%LOCALAPPDATA%\hermes\skills\productivity\note-desktop\SKILL.md
```

装好后，可以直接对 Agent 说：

- 「帮我在备忘录里加一条：明天下午三点开会」
- 「把牛奶那条划掉」
- 「打开备忘录」
- 「关掉备忘录」
- 「帮我把开机自启打开」

---

## 本地 API（进阶）

备忘录运行时会开一个**只绑 127.0.0.1** 的 HTTP 服务，端口 `52741`。

```bash
# token 在 %APPDATA%\desktop-memo\api_token
TOKEN=$(cat "$APPDATA/desktop-memo/api_token")

curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:52741/state
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
     -d '{"text":"买牛奶"}' http://127.0.0.1:52741/items
```

完整端点列表见 [docs/api.md](../docs/api.md)。

---

## 数据位置与卸载

| 内容 | 路径 |
|---|---|
| 事项数据库 | `%APPDATA%\desktop-memo\memo.db` |
| API token | `%APPDATA%\desktop-memo\api_token` |
| 开机自启 | 注册表 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run\DesktopMemo` |

**卸载**：删掉 exe，在托盘右键关掉「开机自动启动」，再删掉
`%APPDATA%\desktop-memo` 目录即可。不留任何残留。

---

## 常见问题

**卡片不见了？**
单击托盘里的小图标，或在托盘菜单里选「显示 / 隐藏卡片」。
`Win+D` 之后卡片应该还在；如果不小心被最小化，单击托盘即可找回。

**MCP 报「连接不上桌面备忘录」？**
程序没在运行。先双击 `note-desktop.exe`，再重试工具调用。
Agent 可以自己调 `memo_start` 拉起它。

**HTTP 返回 401 unauthorized？**
token 不匹配（可能是旧版本残留的 token 文件）。
删掉 `%APPDATA%\desktop-memo\api_token`，重启程序会重新生成。

**exe 被杀毒软件拦了？**
PyInstaller 打包的 exe 常被误报。这是已知问题，用源码方式运行可避免。