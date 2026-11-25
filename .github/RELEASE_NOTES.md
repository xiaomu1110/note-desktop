## note-desktop v1.0.0

常驻桌面、可拖动的极简便签。**零第三方依赖**，只用 Python 标准库。

### 下载

| 文件 | 说明 |
|---|---|
| `note-desktop.exe` | 单文件，双击即用，**不需要安装 Python**（约 13 MB） |
| `note_desktop-1.0.0-py3-none-any.whl` | Python 包，`pip install` 后可用 `note-desktop` 命令 |

> ⚠️ exe 未做代码签名，首次运行 Windows SmartScreen 可能提示「未知发布者」。
> 点「更多信息」→「仍要运行」即可。

### 功能

- 🗒️ **无边框卡片**：没有标题栏、系统边框、关闭按钮，圆角 7px，一整块就是内容
- 🖱️ **按住任意位置拖动**：松手即保存坐标，下次开机原位出现
- 📌 **只活在桌面层**：不占任务栏、不进 Alt+Tab；`Win+D` 之后卡片仍在
- 🧰 **托盘常驻**：关掉窗口不退出，单击托盘图标显示/隐藏
- 🚀 **开机自启**：托盘右键一键开关
- 💾 **自动保存**：每改一次立即写盘，强杀进程也不丢
- 📐 **右下角拖拽缩放**：可自由调整卡片大小
- 🤖 **本地 API + MCP**：让 Agent 远程增删事项、开关程序与自启

### 配套 MCP 与 Agent 技能

Release 附件里的两个压缩包：

- `note-desktop-mcp.zip` —— MCP 配置模板 + 一键安装脚本
- `note-desktop-skill.zip` —— Agent 技能文件（`SKILL.md`）

**快速配置（Hermes Agent）**

```bash
git clone https://github.com/xiaomu1110/note-desktop.git
cd note-desktop
python install_integration.py
hermes mcp test note-desktop
```

> ⚠️ 不要用 `hermes mcp add --env`，该命令会把 `--env` 塞进 `args` 导致连接失败。
> 用上面的脚本，或手动编辑 `%LOCALAPPDATA%\hermes\config.yaml`。

**Claude Desktop** 等客户端用 `mcp/mcp-config.example.json` 模板。

**9 个工具**：`memo_status` `memo_start` `memo_shutdown` `memo_set_autostart`
`memo_set_visible` `memo_list_items` `memo_add_item` `memo_update_item` `memo_delete_item`

装好后可以直接对 Agent 说：

- 「帮我在备忘录里加一条：明天下午三点开会」
- 「把牛奶那条划掉」
- 「打开备忘录」/「关掉备忘录」

### 从源码运行

```bash
git clone https://github.com/xiaomu1110/note-desktop.git
cd note-desktop
python run.pyw
```

需要 Python 3.10+。

### 数据位置

- 事项：`%APPDATA%\desktop-memo\memo.db`
- API token：`%APPDATA%\desktop-memo\api_token`
- 开机自启：注册表 `HKCU\...\Run\DesktopMemo`

卸载只需删掉 exe 和 `%APPDATA%\desktop-memo` 目录，不留残留。

### 已知问题

- 仅支持 Windows
- exe 未签名，可能被杀毒软件误报
- 背景没有做「透出壁纸的云母」：实测 Tk 的透明色键会关闭文字抗锯齿
  （中间灰度 55.2% → 0.9%），二者不可兼得，选择了保住文字清晰度。详见 README。

**完整文档**：[安装指南](https://github.com/xiaomu1110/note-desktop/blob/main/docs/install.md) ·
[API 文档](https://github.com/xiaomu1110/note-desktop/blob/main/docs/api.md)