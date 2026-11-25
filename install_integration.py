"""一键安装 note-desktop 的 MCP 服务器和 Agent 技能。

做三件事：
1. 把 skill/SKILL.md 复制到目标目录（默认 ~/.hermes/skills/productivity/note-desktop/）
2. 把 MCP 服务器写进 MCP 客户端配置
3. 打印验证方法

用法：
    python install_integration.py                      # 装到 Hermes
    python install_integration.py --dry-run            # 只看会改什么
    python install_integration.py --skills-dir PATH    # 自定义技能目录
    python install_integration.py --no-skill           # 只装 MCP
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
SKILL_SRC = ROOT / "skill" / "SKILL.md"
SERVER_MODULE = "desktop_memo.mcp_server"
SERVER_NAME = "note-desktop"


def hermes_home() -> Path:
    """Hermes 的数据目录。

    优先 HERMES_HOME；其次 %LOCALAPPDATA%\\hermes（Windows 默认）；
    再退回 ~/.hermes。
    """
    env = os.environ.get("HERMES_HOME")
    if env:
        return Path(env)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidate = Path(local) / "hermes"
        if candidate.exists():
            return candidate
    return Path.home() / ".hermes"


def default_skills_dir() -> Path:
    """Hermes 的技能目录。"""
    return hermes_home() / "skills" / "productivity" / SERVER_NAME


def detect_hermes() -> bool:
    from shutil import which

    return which("hermes") is not None


def find_python() -> str:
    """优先用带 tkinter 的 python 解释器。"""
    exe = Path(sys.executable)
    return str(exe).replace("\\", "/")


def mcp_entry() -> dict:
    return {
        "command": find_python(),
        "args": ["-m", SERVER_MODULE],
        "env": {"PYTHONPATH": str(SRC)},
    }


# ---------------------------------------------------------------- Hermes


def install_hermes(cfg_path: Path, dry_run: bool) -> bool:
    if not cfg_path.exists():
        print(f"  ! 找不到 Hermes 配置：{cfg_path}")
        return False

    import re

    text = cfg_path.read_text(encoding="utf-8")
    entry = mcp_entry()
    block = (
        f"  {SERVER_NAME}:\n"
        f"    command: {entry['command']}\n"
        "    args:\n"
        "      - -m\n"
        f"      - {SERVER_MODULE}\n"
        "    env:\n"
        f"      PYTHONPATH: {entry['env']['PYTHONPATH']}\n"
        "    connect_timeout: 30\n"
        "    enabled: true\n"
    )

    # 已存在则整块替换，否则追加到 mcp_servers 段
    existing = re.search(
        rf"^(  {re.escape(SERVER_NAME)}:.*?)(?=^  \S|\Z)", text, re.M | re.S
    )
    if existing:
        new_text = text[: existing.start()] + block + text[existing.end():]
        action = "更新"
    elif "mcp_servers:" in text:
        idx = text.index("mcp_servers:")
        eol = text.index("\n", idx) + 1
        new_text = text[:eol] + block + text[eol:]
        action = "新增"
    else:
        new_text = text.rstrip("\n") + "\nmcp_servers:\n" + block
        action = "新增段"

    if dry_run:
        print(f"  [dry-run] 将{action} mcp_servers.{SERVER_NAME}")
        return True

    backup = cfg_path.with_suffix(cfg_path.suffix + ".bak-note-desktop")
    shutil.copy2(cfg_path, backup)
    cfg_path.write_text(new_text, encoding="utf-8")
    print(f"  ✓ 已{action} mcp_servers.{SERVER_NAME}（备份：{backup.name}）")
    return True


# ---------------------------------------------------------------- 通用 JSON


def install_json(cfg_path: Path, dry_run: bool) -> bool:
    data: dict = {}
    if cfg_path.exists():
        try:
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print(f"  ! {cfg_path.name} 不是合法 JSON，跳过")
            return False
    servers = data.setdefault("mcpServers", {})
    servers[SERVER_NAME] = mcp_entry()
    if dry_run:
        print(f"  [dry-run] 将写入 {cfg_path}")
        return True
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"  ✓ 已写入 {cfg_path}")
    return True


def candidate_json_configs() -> list[Path]:
    appdata = Path(os.environ.get("APPDATA", ""))
    home = Path.home()
    return [
        appdata / "Claude" / "claude_desktop_config.json",
        home / ".cursor" / "mcp.json",
        home / ".codeium" / "windsurf" / "mcp_config.json",
    ]


# ---------------------------------------------------------------- 主流程


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="install_integration",
        description="安装 note-desktop 的 MCP 服务器与 Agent 技能",
    )
    ap.add_argument("--skills-dir", type=Path, default=None,
                    help="技能安装目录（默认 Hermes 的 productivity/note-desktop）")
    ap.add_argument("--no-skill", action="store_true", help="只装 MCP，不装技能")
    ap.add_argument("--no-mcp", action="store_true", help="只装技能，不装 MCP")
    ap.add_argument("--hermes-config", type=Path, default=None,
                    help="Hermes 配置文件（默认 ~/.hermes/config.yaml）")
    ap.add_argument("--json-config", type=Path, default=None,
                    help="额外写入某个 MCP 客户端的 JSON 配置")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不改动")
    args = ap.parse_args(argv)

    print(f"note-desktop 集成安装")
    print(f"  源码目录: {SRC}")
    print(f"  Python:   {find_python()}")
    if not SRC.exists():
        print(f"  ✗ 找不到 {SRC}，请把本脚本放在仓库根目录运行")
        return 1

    ok = True

    # ---- 技能
    if not args.no_skill:
        target_dir = args.skills_dir or default_skills_dir()
        if not SKILL_SRC.exists():
            print(f"  ! 找不到 {SKILL_SRC}")
            ok = False
        elif args.dry_run:
            print(f"  [dry-run] 将复制 SKILL.md → {target_dir}")
        else:
            target_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(SKILL_SRC, target_dir / "SKILL.md")
            print(f"  ✓ 技能已安装到 {target_dir}")

    # ---- MCP
    if not args.no_mcp:
        hermes_cfg = args.hermes_config or (hermes_home() / "config.yaml")
        wrote = False
        if detect_hermes() or hermes_cfg.exists():
            wrote = install_hermes(hermes_cfg, args.dry_run) or wrote

        if args.json_config:
            wrote = install_json(args.json_config, args.dry_run) or wrote
        elif not hermes_cfg.exists():
            # 没装 Hermes，给出模板让用户自己合并
            print("  ! 未检测到 Hermes，MCP 配置模板见 mcp/mcp-config.example.json")

        if not wrote and hermes_cfg.exists():
            ok = False

    print()
    if args.dry_run:
        print("dry-run 结束，未改动任何文件。")
        return 0 if ok else 1

    print("验证：")
    print("  1. 启动 note-desktop（exe 或 python run.pyw）")
    print("  2. Hermes:  hermes mcp test note-desktop")
    print("  3. 对 Agent 说：「帮我在备忘录里加一条测试」")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())