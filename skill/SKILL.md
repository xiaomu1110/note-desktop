---
name: note-desktop
description: "Use when the user wants to add, check off, or delete quick to-do items on their desktop sticky-note, or to open/close the note-desktop app and toggle its auto-start. Talks to the note-desktop MCP server (memo_* tools)."
version: 1.0.0
author: xiaomu
license: MIT
platforms: [windows]
metadata:
  hermes:
    tags: [todo, sticky-note, desktop, memo, reminder, mcp, note-desktop]
    related_skills: [meeting-action-items, document-to-action-items]
---

# Desktop memo control (note-desktop)

## When to Use

The user refers to their **desktop sticky note / 桌面备忘录 / 便签**, or asks for
any of these in passing:

- "帮我在备忘录里加一条…" / "记一下…" (add a quick note)
- "把牛奶那条划掉" / "那个做完了" (check something off)
- "删掉那条" (delete an item)
- "打开备忘录" / "把备忘录调出来" (start / show it)
- "关掉备忘录" (quit it)
- "开机自动启动" (toggle auto-start)

Do **not** use this for long-form notes, documents, or knowledge bases — that's
Obsidian / the note-taking skills. This skill is only for the small always-on
desktop card.

## How it works

`note-desktop` is a small Windows app (Tk + SQLite) that draws an always-on
sticky note on the desktop. This skill talks to it through the `note-desktop`
MCP server, which exposes nine `memo_*` tools. The MCP server forwards requests
to a local HTTP API on `127.0.0.1:52741`.

Practical consequences you should remember:

- **The app must be running** for anything except `memo_start`. If a tool returns
  "连接不上桌面备忘录", call `memo_start` first, then retry.
- Changes are saved immediately and show up on the card within a fraction of a second.
- Data lives in `%APPDATA%\desktop-memo\memo.db`. Deleting it loses everything.
  (The directory keeps the historical name `desktop-memo` for compatibility.)

## Tool map

| Tool | Use for |
|---|---|
| `memo_status` | Confirm it's running / visible / auto-start / item count |
| `memo_start` | Launch it (idempotent — safe to call when unsure) |
| `memo_shutdown` | Quit the app (data is preserved) |
| `memo_list_items` | Get ids before update/delete |
| `memo_add_item` | Add one item |
| `memo_update_item` | Edit text and/or toggle done |
| `memo_delete_item` | Delete one item (irreversible) |
| `memo_set_visible` | Hide/show the card without quitting |
| `memo_set_autostart` | Toggle "start with Windows" |

## Workflow

### Adding several items at once

1. `memo_start` — cheap and idempotent; avoids doing work against a dead app.
2. `memo_add_item` per item. Don't batch into one long multi-line string —
   the card renders one item per line.

### Checking something off

1. `memo_list_items` and **match by text** to find the id.
2. `memo_update_item` with `{"id": N, "done": true}`.

### Deleting

Deletion is irreversible. If the user's phrasing is ambiguous ("把那个删了"
without saying which), **list items and confirm the target** before deleting.

### "打开备忘录"

`memo_start` when the app is down; `memo_set_visible` with `visible: true` when it
is running but hidden. `memo_start` then `memo_set_visible` covers both without
needing to branch yourself.

## Rules

- **Never invent ids.** Always get them from `memo_list_items`.
- **Confirm before destructive actions** when the target isn't unambiguous.
- **Don't call `memo_shutdown` casually.** Quitting stops the always-on card;
  hide with `memo_set_visible: false` unless the user said "关掉/退出/关闭程序".
- **Report what actually changed.** Quote the resulting item text back to the user.
- If the user dictates items while the app is down, still try `memo_start` once.
  If it fails, tell them to launch it manually and offer to retry.

## Troubleshooting

| Symptom | Meaning | Fix |
|---|---|---|
| "连接不上桌面备忘录" | app not running, or API port taken | `memo_start`; if the port is taken, another copy may hold it |
| 401 unauthorized | token mismatch (stale `api_token` file) | Delete `%APPDATA%\desktop-memo\api_token` and restart the app |
| "备忘录接口返回 404" | item id gone (deleted elsewhere) | Re-run `memo_list_items` |
| `memo_start` succeeds but status still fails | it's still booting | Wait ~2s and retry `memo_status` |

## Example

User: "帮我记一下，明天上午十点跟王工对图纸"

```
memo_start()
memo_add_item(text="明天上午十点跟王工对图纸")
→ {"id": 7, "text": "明天上午十点跟王工对图纸", "done": false}
```

Reply: 记好了，「明天上午十点跟王工对图纸」已经加到桌面备忘录。

## Technical details (for debugging only)

- MCP server: `python -m desktop_memo.mcp_server` (stdio, zero-dependency JSON-RPC)
- HTTP API: `http://127.0.0.1:52741`, `Authorization: Bearer <token>`
- Token file: `%APPDATA%\desktop-memo\api_token` (or `DESKTOP_MEMO_TOKEN` env)
- Project source: https://github.com/xiaomu1110/note-desktop